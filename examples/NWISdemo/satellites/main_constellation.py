# -*- coding: utf-8 -*-
"""
    *This application demonstrates a constellation of satellites for imgaing floods propagated from Two-Line Elements (TLEs)*

    The application contains one :obj:`Constellation` (:obj:`Entity`) object class, one :obj:`PositionPublisher` (:obj:`WallclockTimeIntervalPublisher`), and two :obj:`Observer` object classes to monitor for :obj:`FloodDetected` and :obj:`FloodReported` events, respectively. The application also contains several methods outside of these classes, which contain standardized calculations sourced from Ch. 5 of *Space Mission Analysis and Design* by Wertz and Larson.

"""

from datetime import datetime, timezone, timedelta
import logging
import numpy as np
import pandas as pd
import copy
from dotenv import dotenv_values

# import time

from nost_tools.application_utils import ConnectionConfig, ShutDownObserver
from nost_tools.entity import Entity
from nost_tools.observer import Observer
from nost_tools.managed_application import ManagedApplication
from nost_tools.publisher import WallclockTimeIntervalPublisher
from skyfield.api import load, wgs84, EarthSatellite

# import schema here
from constellation_config_files.schemas import (
    FloodStarted,
    FloodImaged,
    FloodDownlinked,
    SatelliteStatus,
    GroundLocation,
)
from constellation_config_files.config import (
    PREFIX,
    NAME,
    SCALE,
    TLES,
    FIELD_OF_REGARD,
    names
)

logging.basicConfig(level=logging.INFO)


def compute_min_elevation(altitude, field_of_regard):
    """
    Computes the minimum elevation angle required for a satellite to observe a point from current location.

    Args:
        altitude (float): Altitude (meters) above surface of the observation.
        field_of_regard (float): Angular width (degrees) of observation.

    Returns:
        float : min_elevation
            The minimum elevation angle (degrees) for observation.
    """
    earth_equatorial_radius = 6378137.000000000
    earth_polar_radius = 6356752.314245179
    earth_mean_radius = (2 * earth_equatorial_radius + earth_polar_radius) / 3

    # eta is the angular radius of the region viewable by the satellite
    sin_eta = np.sin(np.radians(field_of_regard / 2))
    # rho is the angular radius of the earth viewed by the satellite
    sin_rho = earth_mean_radius / (earth_mean_radius + altitude)
    # epsilon is the min satellite elevation for obs (grazing angle)
    cos_epsilon = sin_eta / sin_rho
    if cos_epsilon > 1:
        return 0.0
    return np.degrees(np.arccos(cos_epsilon))


def compute_sensor_radius(altitude, min_elevation):
    """
    Computes the sensor radius for a satellite at current altitude given minimum elevation constraints.

    Args:
        altitude (float): Altitude (meters) above surface of the observation.
        min_elevation (float): Minimum angle (degrees) with horizon for visibility.

    Returns:
        float : sensor_radius
            The radius (meters) of the nadir pointing sensors circular view of observation.
    """
    earth_equatorial_radius = 6378137.0
    earth_polar_radius = 6356752.314245179
    earth_mean_radius = (2 * earth_equatorial_radius + earth_polar_radius) / 3
    # rho is the angular radius of the earth viewed by the satellite
    sin_rho = earth_mean_radius / (earth_mean_radius + altitude)
    # eta is the nadir angle between the sub-satellite direction and the target location on the surface
    eta = np.degrees(np.arcsin(np.cos(np.radians(min_elevation)) * sin_rho))
    # calculate swath width half angle from trigonometry
    sw_HalfAngle = 90 - eta - min_elevation
    if sw_HalfAngle < 0.0:
        return 0.0
    return earth_mean_radius * np.radians(sw_HalfAngle)


def get_elevation_angle(t, sat, loc):
    """
    Returns the elevation angle (degrees) of satellite with respect to the topocentric horizon

    Args:
        t (:obj:`Time`): Time object of skyfield.timelib module
        sat (:obj:`EarthSatellite`): Skyview EarthSatellite object from skyfield.sgp4lib module
        loc (:obj:`GeographicPosition`): Geographic location on surface specified by latitude-longitude from skyfield.toposlib module

    Returns:
        float : alt.degrees
            Elevation angle (degrees) of satellite with respect to the topocentric horizon
    """
    difference = sat - loc
    topocentric = difference.at(t)
    # NOTE: Topos uses term altitude for what we are referring to as elevation
    alt, az, distance = topocentric.altaz()
    return alt.degrees


def check_in_view(t, satellite, topos, min_elevation):
    """
    Checks if the elevation angle of the satellite with respect to the ground location is greater than the minimum elevation angle constraint

    Args:
        t (:obj:`Time`): Time object of skyfield.timelib module
        satellite (:obj:`EarthSatellite`): Skyview EarthSatellite object from skyfield.sgp4lib module
        topos (:obj:`GeographicPosition`): Geographic location on surface specified by latitude-longitude from skyfield.toposlib module
        min_elevation (float): Minimum elevation angle (degrees) for ground to be in view of satellite, as calculated by compute_min_elevation

    Returns:
        bool : isInView
            True/False indicating visibility of ground location to satellite
    """
    isInView = False
    elevationFromFlood = get_elevation_angle(t, satellite, topos)
    if elevationFromFlood >= min_elevation:
        isInView = True
    return isInView


def check_in_range(t, satellite, grounds):
    """
    Checks if the satellite is in range of any of the operational ground stations

    Args:
        t (:obj:`Time`): Time object of skyfield.timelib module
        satellite (:obj:`EarthSatellite`): Skyview EarthSatellite object from skyfield.sgp4lib module
        grounds (:obj:`DataFrame`): Dataframe of ground station locations, minimum elevation angles for communication, and operational status (T/F)

    Returns:
        bool, int :
            isInRange
                True/False indicating visibility of satellite to any operational ground station
            groundId
                groundId of the ground station currently in comm range (NOTE: If in range of two ground stations simultaneously, will return first groundId)
    """
    isInRange = False
    groundId = None
    for k, ground in grounds.iterrows():
        if ground.operational:
            groundLatLon = wgs84.latlon(ground.latitude, ground.longitude)
            satelliteElevation = get_elevation_angle(t, satellite, groundLatLon)
            if satelliteElevation >= ground.elevAngle:
                isInRange = True
                groundId = k
                break
    return isInRange, groundId


# define an entity to manage satellite updates
class Constellation(Entity):
    """
    *This object class inherits properties from the Entity object class in the NOS-T tools library*

    Args:
        cName (str): A string containing the name for the constellation application
        app (:obj:`ManagedApplication`): An application containing a test-run namespace, a name and description for the app, client credentials, and simulation timing instructions
        id (:obj:`list`): List of unique *int* ids for each satellite in the constellation
        names (:obj:`list`): List of unique *str* for each satellite in the constellation (must be same length as **id**)
        ES (:obj:`list`): Optional list of :obj:`EarthSatellite` objects to be included in the constellation (NOTE: at least one of **ES** or **tles** MUST be specified, or an exception will be thrown)
        tles (:obj:`list`): Optional list of Two-Line Element *str* to be converted into :obj:`EarthSatellite` objects and included in the simulation

    Attributes:
        floods (:obj:`list`): List of floods with unique floodId (*int*), ignition (:obj:`datetime`), and latitude-longitude location (:obj:`GeographicPosition`) - *NOTE:* initialized as [ ]
        grounds (:obj:`DataFrame`): Dataframe containing information about ground stations with unique groundId (*int*), latitude-longitude location (:obj:`GeographicPosition`), min_elevation (*float*) angle constraints, and operational status (*bool*) - *NOTE:* initialized as **None**
        satellites (:obj:`list`): List of :obj:`EarthSatellite` objects included in the constellation - *NOTE:* must be same length as **id**
        detect (:obj:`list`): List of detected floods with unique floodId (*int*), detected :obj:`datetime`, and name (*str*) of detecting satellite - *NOTE:* initialized as [ ]
        report (:obj:`list`): List of reported floods with unique floodId (*int*), reported :obj:`datetime`, name (*str*) of reporting satellite, and groundId (*int*) of ground station reported to - *NOTE:* initialized as [ ]
        positions (:obj:`list`): List of current latitude-longitude-altitude locations (:obj:`GeographicPosition`) of each satellite in the constellation - *NOTE:* must be same length as **id**
        next_positions (:obj:`list`): List of next latitude-longitude-altitude locations (:obj:`GeographicPosition`) of each satellite in the constellation - *NOTE:* must be same length as **id**
        min_elevations_flood (:obj:`list`): List of *floats* indicating current elevation angle (degrees) constraint for visibility by each satellite - *NOTE:* must be same length as **id**, updates every time step

    """

    ts = load.timescale()
    PROPERTY_FLOOD_IMAGED = "imageTaken"
    PROPERTY_FLOOD_DOWNLINKED = "imageDownlinked"
    PROPERTY_POSITION = "position"

    def __init__(self, cName, app, id, names, ES=None, tles=None):
        super().__init__(cName)
        self.app = app
        self.id = id
        self.names = names
        self.floods = []
        self.grounds = None
        self.satellites = []
        if ES is not None:
            for satellite in ES:
                self.satellites.append(satellite)
        if tles is not None:
            for i, tle in enumerate(tles):
                self.satellites.append(
                    EarthSatellite(tle[0], tle[1], self.names[i], self.ts)
                )
        self.image = []
        self.downlink = []
        self.positions = self.next_positions = [None for satellite in self.satellites]
        self.min_elevations_flood = [
            compute_min_elevation(
                wgs84.subpoint(satellite.at(satellite.epoch)).elevation.m,
                FIELD_OF_REGARD,
            )
            for i, satellite in enumerate(self.satellites)
        ]
        # # Two print lines below can be used as sanity check that SMAD Ch. 5 equations implemented properly
        # print("\nInitial elevation angles:\n")
        # print(self.min_elevations_flood)

    def initialize(self, init_time):
        """
        Activates the :obj:`Constellation` at a specified initial scenario time

        Args:
            init_time (:obj:`datetime`): Initial scenario time for simulating propagation of satellites
        """
        super().initialize(init_time)
        self.grounds = pd.DataFrame(
            {
                "groundId": pd.Series([], dtype="int"),
                "latitude": pd.Series([], dtype="float"),
                "longitude": pd.Series([], dtype="float"),
                "elevAngle": pd.Series([], dtype="float"),
                "operational": pd.Series([], dtype="bool"),
            }
        )
        self.positions = self.next_positions = [
            wgs84.subpoint(satellite.at(self.ts.from_datetime(init_time)))
            for satellite in self.satellites
        ]

    def tick(self, time_step):
        """
        Computes the next :obj:`Constellation` state after the specified scenario duration and the next simulation scenario time

        Args:
            time_step (:obj:`timedelta`): Duration between current and next simulation scenario time
        """
        # tik = time.time()
        super().tick(time_step)
        self.next_positions = [
            wgs84.subpoint(
                satellite.at(self.ts.from_datetime(self.get_time() + time_step))
            )
            for satellite in self.satellites
        ]
        for i, satellite in enumerate(self.satellites):
            then = self.ts.from_datetime(self.get_time() + time_step)
            self.min_elevations_flood[i] = compute_min_elevation(
                float(self.next_positions[i].elevation.m), FIELD_OF_REGARD
            )
            for j, flood in enumerate(self.floods):
                if self.image[j][self.names[i]] is None:
                    topos = wgs84.latlon(flood["latitude"], flood["longitude"])
                    isInView = check_in_view(
                        then, satellite, topos, self.min_elevations_flood[i]
                    )
                    if isInView:
                        self.image[j][self.names[i]] = (
                            self.get_time() + time_step
                        )  # TODO could use event times
                        if self.image[j]["firstImager"] is None:
                            self.image[j]["firstImage"] = True
                            self.image[j]["firstImager"] = self.names[i]
                if (self.image[j][self.names[i]] is not None) and (
                    self.downlink[j][self.names[i]] is None
                ):
                    isInRange, groundId = check_in_range(then, satellite, self.grounds)
                    if isInRange:
                        self.downlink[j][self.names[i]] = self.get_time() + time_step
                        if self.downlink[j]["firstDownlinker"] is None:
                            self.downlink[j]["firstDownlink"] = True
                            self.downlink[j]["firstDownlinker"] = self.names[i]
                            self.downlink[j]["firstDownlinkedTo"] = groundId
        # tok = time.time() - tik
        # print(f"The tick took {tok} seconds to filter \n")

    def tock(self):
        """
        Commits the next :obj:`Constellation` state and advances simulation scenario time

        """
        # tik = time.time()
        self.positions = self.next_positions
        for i, newly_imaged_flood in enumerate(self.image):
            if newly_imaged_flood["firstImage"]:
                imager = newly_imaged_flood["firstImager"]
                self.notify_observers(
                    self.PROPERTY_FLOOD_IMAGED,
                    None,
                    {
                        "floodId": newly_imaged_flood["floodId"],
                        "imaged": newly_imaged_flood[imager],
                        "imagedBy": imager,
                    },
                )
                self.image[i]["firstImage"] = False
        for i, newly_downlinked_flood in enumerate(self.downlink):
            if newly_downlinked_flood["firstDownlink"]:
                downlinker = newly_downlinked_flood["firstDownlinker"]
                self.notify_observers(
                    self.PROPERTY_FLOOD_DOWNLINKED,
                    None,
                    {
                        "floodId": newly_downlinked_flood["floodId"],
                        "downlinked": newly_downlinked_flood[downlinker],
                        "downlinkedBy": downlinker,
                        "downlinkedTo": newly_downlinked_flood["firstDownlinkedTo"],
                    },
                )
            self.downlink[i]["firstDownlink"] = False
        # tok = time.time() - tik
        # print(f"The tock took {tok} seconds \n")
        super().tock()

    # on flood
    def on_flood(self, client, userdata, message):
        """
        Callback function appends a dictionary of information for a new flood to floods :obj:`list` when message detected on the *PREFIX/NWISdemo/floodWarning* topic

        Args:
            client (:obj:`MQTT Client`): Client that connects application to the event broker using the MQTT protocol. Includes user credentials, tls certificates, and host server-port information.
            userdata: User defined data of any type (not currently used)
            message (:obj:`message`): Contains *topic* the client subscribed to and *payload* message content as attributes

        """
        started = FloodStarted.parse_raw(message.payload)
        self.floods.append(
            {
                "floodId": started.floodId,
                "start": started.startTime,
                "latitude": started.latitude,
                "longitude": started.longitude,
            },
        )
        satelliteDictionary = dict.fromkeys(
            self.names
        )  # Creates dictionary where keys are satellite names and values are defaulted to NoneType
        satelliteDictionary[
            "floodId"
        ] = (
            started.floodId
        )  # Adds floodId to dictionary, which will coordinate with position of dictionary in list of dictionaries
        imageDictionary = copy.deepcopy(satelliteDictionary)
        imageDictionary["firstImage"] = False
        imageDictionary["firstImager"] = None
        self.image.append(imageDictionary)
        downlinkDictionary = copy.deepcopy(satelliteDictionary)
        downlinkDictionary["firstDownlink"] = False
        downlinkDictionary["firstDownlinker"] = None
        downlinkDictionary["firstDownlinkedTo"] = None
        self.downlink.append(downlinkDictionary)

    def on_ground(self, client, userdata, message):
        """
        Callback function appends a dictionary of information for a new ground station to grounds :obj:`list` when message detected on the *PREFIX/ground/location* topic. Ground station information is published at beginning of simulation, and the :obj:`list` is converted to a :obj:`DataFrame` when the Constellation is initialized.

        Args:
            client (:obj:`MQTT Client`): Client that connects application to the event broker using the MQTT protocol. Includes user credentials, tls certificates, and host server-port information.
            userdata: User defined data of any type (not currently used)
            message (:obj:`message`): Contains *topic* the client subscribed to and *payload* message content as attributes

        """
        location = GroundLocation.parse_raw(message.payload)
        if location.groundId in self.grounds.groundId:
            self.grounds[
                self.grounds.groundId == location.groundId
            ].latitude = location.latitude
            self.grounds[
                self.grounds.groundId == location.groundId
            ].longitude = location.longitude
            self.grounds[
                self.grounds.groundId == location.groundId
            ].elevAngle = location.elevAngle
            self.grounds[
                self.grounds.groundId == location.groundId
            ].operational = location.operational
            print(f"Station {location.groundId} updated at time {self.get_time()}.")
        else:
            self.grounds = self.grounds.append(
                {
                    "groundId": location.groundId,
                    "latitude": location.latitude,
                    "longitude": location.longitude,
                    "elevAngle": location.elevAngle,
                    "operational": location.operational,
                },
                ignore_index=True,
            )
            print(f"Station {location.groundId} registered at time {self.get_time()}.")


# define a publisher to report satellite status
class PositionPublisher(WallclockTimeIntervalPublisher):
    """
    *This object class inherits properties from the WallclockTimeIntervalPublisher object class from the publisher template in the NOS-T tools library*

    The user can optionally specify the wallclock :obj:`timedelta` between message publications and the scenario :obj:`datetime` when the first of these messages should be published.

    Args:
        app (:obj:`ManagedApplication`): An application containing a test-run namespace, a name and description for the app, client credentials, and simulation timing instructions
        constellation (:obj:`Constellation`): Constellation :obj:`Entity` object class
        time_status_step (:obj:`timedelta`): Optional duration between time status 'heartbeat' messages
        time_status_init (:obj:`datetime`): Optional scenario :obj:`datetime` for publishing the first time status 'heartbeat' message

    """

    def __init__(
        self, app, constellation, time_status_step=None, time_status_init=None
    ):
        super().__init__(app, time_status_step, time_status_init)
        self.constellation = constellation
        self.isInRange = [
            False for i, satellite in enumerate(self.constellation.satellites)
        ]

    def publish_message(self):
        """
        *Abstract publish_message method inherited from the WallclockTimeIntervalPublisher object class from the publisher template in the NOS-T tools library*

        This method sends a message to the *PREFIX/constellation/location* topic for each satellite in the constellation (:obj:`Constellation`), including:

        Args:
            id (:obj:`list`): list of unique *int* ids for each satellite in the constellation
            names (:obj:`list`): list of unique *str* for each satellite in the constellation - *NOTE:* must be same length as **id**
            positions (:obj:`list`): list of current latitude-longitude-altitude locations (:obj:`GeographicPosition`) of each satellite in the constellation - *NOTE:* must be same length as **id**
            radius (:obj:`list`): list of the radius (meters) of the nadir pointing sensors circular view of observation for each satellite in the constellation - *NOTE:* must be same length as **id**
            commRange (:obj:`list`): list of *bool* indicating each satellites visibility to *any* ground station - *NOTE:* must be same length as **id**
            time (:obj:`datetime`): current scenario :obj:`datetime`

        """
        for i, satellite in enumerate(self.constellation.satellites):
            next_time = constellation.ts.from_datetime(
                constellation.get_time() + SCALE * self.time_status_step
            )
            satSpaceTime = satellite.at(next_time)
            subpoint = wgs84.subpoint(satSpaceTime)
            sensorRadius = compute_sensor_radius(
                subpoint.elevation.m, constellation.min_elevations_flood[i]
            )
            self.isInRange[i], groundId = check_in_range(
                next_time, satellite, constellation.grounds
            )
            self.app.send_message(
                "location",
                SatelliteStatus(
                    id=i,
                    name=satellite.name,
                    latitude=subpoint.latitude.degrees,
                    longitude=subpoint.longitude.degrees,
                    altitude=subpoint.elevation.m,
                    radius=sensorRadius,
                    commRange=self.isInRange[i],
                    time=constellation.get_time(),
                ).json(),
            )


# define an observer to send flood imaging events
class FloodImagedObserver(Observer):
    """
    *This object class inherits properties from the Observer object class from the observer template in the NOS-T tools library*

    Args:
        app (:obj:`ManagedApplication`): An application containing a test-run namespace, a name and description for the app, client credentials, and simulation timing instructions

    """

    def __init__(self, app):
        self.app = app

    def on_change(self, source, property_name, old_value, new_value):
        """
        *Standard on_change callback function format inherited from Observer object class in NOS-T tools library*

        In this instance, the callback function checks for notification of the "detected" property and publishes :obj:`FloodDetected` message to *PREFIX/constellation/detected* topic:

        """
        if property_name == Constellation.PROPERTY_FLOOD_IMAGED:
            self.app.send_message(
                "imageTaken",
                FloodImaged(
                    floodId=new_value["floodId"],
                    imaged=str(datetime.now()),#new_value["imaged"],
                    imagedBy=new_value["imagedBy"],
                ).json(),
            )


# define an observer to send flood reporting events
class FloodDownlinkedObserver(Observer):
    """
    *This object class inherits properties from the Observer object class from the observer template in the NOS-T tools library*

    Args:
        app (:obj:`ManagedApplication`): An application containing a test-run namespace, a name and description for the app, client credentials, and simulation timing instructions

    """

    def __init__(self, app):
        self.app = app

    def on_change(self, source, property_name, old_value, new_value):
        """
        *Standard on_change callback function format inherited from Observer object class in NOS-T tools library*

        In this instance, the callback function checks for notification of the "reported" property and publishes :obj:`FloodReported` message to *PREFIX/constellation/reported* topic:

        """
        if property_name == Constellation.PROPERTY_FLOOD_DOWNLINKED:
            self.app.send_message(
                "imageDownlinked",
                FloodDownlinked(
                    floodId=new_value["floodId"],
                    downlinked=str(datetime.now()),#new_value["downlinked"],
                    downlinkedBy=new_value["downlinkedBy"],
                    downlinkedTo=new_value["downlinkedTo"],
                ).json(),
            )


# name guard used to ensure script only executes if it is run as the __main__
if __name__ == "__main__":
    # Note that these are loaded from a .env file in current working directory
    credentials = dotenv_values(".env")
    HOST, PORT = credentials["SMCE_HOST"], int(credentials["SMCE_PORT"])
    USERNAME, PASSWORD = credentials["SMCE_USERNAME"], credentials["SMCE_PASSWORD"]
    # set the client credentials
    config = ConnectionConfig(USERNAME, PASSWORD, HOST, PORT, True)

    # create the managed application
    app = ManagedApplication(NAME)

    ES=[]

    # initialize the Constellation object class (in this example from EarthSatellite type)
    constellation = Constellation("constellation", app, [0,1,2,3,4,5,6,7,8,9], names, ES, TLES)

    # add observer classes to the Constellation object class
    constellation.add_observer(FloodImagedObserver(app))
    constellation.add_observer(FloodDownlinkedObserver(app))

    # add the Constellation entity to the application's simulator
    app.simulator.add_entity(constellation)

    # add a shutdown observer to shut down after a single test case
    app.simulator.add_observer(ShutDownObserver(app))

    # add a position publisher to update satellite state every 5 seconds of wallclock time
    app.simulator.add_observer(
        PositionPublisher(app, constellation, timedelta(seconds=5))
    )

    # start up the application on PREFIX, publish time status every 10 seconds of wallclock time
    app.start_up(
        PREFIX,
        config,
        True,
        time_status_step=timedelta(seconds=1) * SCALE,
        time_status_init=datetime(2020, 1, 1, 7, 20, tzinfo=timezone.utc),
        time_step=timedelta(seconds=1) * SCALE,
    )

    # add message callbacks
    app.add_message_callback("streamGauge", "floodWarning", constellation.on_flood)
    app.add_message_callback("ground", "location", constellation.on_ground)