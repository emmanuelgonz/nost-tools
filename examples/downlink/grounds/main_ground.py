# -*- coding: utf-8 -*-
"""
    *This application demonstrates a network of ground stations given geospatial locations, minimum elevation angle constraints, and operational status*

    The application contains one class, the :obj:`Environment` class, which waits for a message from the manager that indicates the beginning of the simulation execution. The application publishes all of the ground station information once, at the beginning of the simulation.

"""

import logging
from datetime import datetime, timezone, timedelta
from dotenv import dotenv_values

from nost_tools.application_utils import ConnectionConfig, ShutDownObserver
from nost_tools.simulator import Simulator, Mode
from nost_tools.observer import Observer, Observable
from nost_tools.managed_application import ManagedApplication

from ground_config_files.schemas import (
    SatelliteReady,
    SatelliteStatus,
    GroundLocation,
    LinkStart,
    LinkCharge
)
from ground_config_files.config import (
    PREFIX,
    NAME,
    SCALE,
    GROUND,
)

logging.basicConfig(level=logging.INFO)

# define an observer to manage ground updates
class GroundNetwork(Observable,Observer):
    """
    *The GroundNetwork object class inherits properties from the Observer object class in the NOS-T tools library*

    Attributes:
        app (:obj:`ManagedApplication`): An application containing a test-run namespace, a name and description for the app, client credentials, and simulation timing instructions
        grounds (:obj:`DataFrame`): DataFrame of ground station information including groundId (*int*), latitude-longitude location (:obj:`GeographicPosition`), min_elevation (*float*) angle constraints, and operational status (*bool*)
    """

    PROPERTY_IN_RANGE = "linkOn"
    PROPERTY_OUT_OF_RANGE = "linkCharge"

    def __init__(self, app, grounds):
        super().__init__()
        self.app = app
        self.grounds = grounds
        self.satelliteIds = []
        self.satelliteNames = []
        self.ssrCapacity = []
        self.cumulativeCosts = 0.00

    def on_change(self, source, property_name, old_value, new_value):
        """
        *Standard on_change callback function format inherited from Observer object class*

        In this instance, the callback function checks when the **PROPERTY_MODE** switches to **EXECUTING** to send a :obj:`GroundLocation` message to the *PREFIX/ground/location* topic:

            .. literalinclude:: /../../firesat/grounds/main_ground.py
                :lines: 51-62

        """
        if property_name == Simulator.PROPERTY_MODE and new_value == Mode.EXECUTING:
            for index, ground in self.grounds.iterrows():
                self.app.send_message(
                    "location",
                    GroundLocation(
                        groundId=ground.groundId,
                        latitude=ground.latitude,
                        longitude=ground.longitude,
                        elevAngle=ground.elevAngle,
                        operational=ground.operational,
                        downlinkRate=ground.downlinkRate,
                        costPerSecond=ground.costPerSecond
                    ).json()
                )

    def on_ready(self, client, userdata, message):
        ready = SatelliteReady.parse_raw(message.payload)
        self.satelliteIds.append(ready.id)
        self.satelliteNames.append(ready.name)
        self.ssrCapacity.append(ready.ssr_capacity)

    def all_ready(self, client, userdata, message):
        self.groundTimes = {j:[] for j in self.satelliteNames}
        self.satView = {k:{"on":False,"linkCount":0} for k in self.satelliteNames}

    def on_commRange(self, client, userdata, message):
        satInView = SatelliteStatus.parse_raw(message.payload)
        if self.satView[satInView.name]["on"]:
            if not satInView.commRange:
                self.groundTimes[satInView.name][self.satView[satInView.name]["linkCount"]]["end"] = satInView.time
                self.groundTimes[satInView.name][self.satView[satInView.name]["linkCount"]]["duration"] = (self.groundTimes[satInView.name][self.satView[satInView.name]["linkCount"]]["end"]
                     - self.groundTimes[satInView.name][self.satView[satInView.name]["linkCount"]]["start"]).total_seconds()
                self.groundTimes[satInView.name][self.satView[satInView.name]["linkCount"]]["dataOffload"] = self.groundTimes[satInView.name][self.satView[satInView.name]["linkCount"]]["duration"]*self.grounds["downlinkRate"][self.groundTimes[satInView.name][self.satView[satInView.name]["linkCount"]]["groundId"]]
                self.groundTimes[satInView.name][self.satView[satInView.name]["linkCount"]]["downlinkCost"] = self.groundTimes[satInView.name][self.satView[satInView.name]["linkCount"]]["duration"]*self.grounds["costPerSecond"][self.groundTimes[satInView.name][self.satView[satInView.name]["linkCount"]]["groundId"]]
                self.cumulativeCosts = self.cumulativeCosts + self.groundTimes[satInView.name][self.satView[satInView.name]["linkCount"]]["downlinkCost"]
                self.notify_observers(
                        self.PROPERTY_OUT_OF_RANGE,
                        None,
                        {
                            "groundId":self.groundTimes[satInView.name][self.satView[satInView.name]["linkCount"]]["groundId"],
                            "satId":satInView.id,
                            "satName":satInView.name,
                            "linkId":self.groundTimes[satInView.name][self.satView[satInView.name]["linkCount"]]["linkId"],
                            "end":self.groundTimes[satInView.name][self.satView[satInView.name]["linkCount"]]["end"],
                            "duration":self.groundTimes[satInView.name][self.satView[satInView.name]["linkCount"]]["duration"],
                            "dataOffload":self.groundTimes[satInView.name][self.satView[satInView.name]["linkCount"]]["dataOffload"],
                            "downlinkCost":self.groundTimes[satInView.name][self.satView[satInView.name]["linkCount"]]["downlinkCost"],
                            "cumulativeCosts":self.cumulativeCosts
                        },
                    )
                self.satView[satInView.name]["on"] = False
                self.satView[satInView.name]["linkCount"] = self.satView[satInView.name]["linkCount"]+1

        elif satInView.commRange:
            self.groundTimes[satInView.name].append(
                {
                    "groundId":satInView.groundId,
                    "satId":satInView.id,
                    "satName":satInView.name,
                    "linkId":self.satView[satInView.name]["linkCount"],
                    "start":satInView.time,
                    "end":None,
                    "duration":None,
                    "initialData": satInView.capacity_used*self.ssrCapacity[satInView.id],
                    "dataOffload":None,
                    "downlinkCost":None
                },
            )
            self.satView[satInView.name]["on"] = True
            self.notify_observers(
                    self.PROPERTY_IN_RANGE,
                    None,
                    {
                        "groundId":self.groundTimes[satInView.name][self.satView[satInView.name]["linkCount"]]["groundId"],
                        "satId":satInView.id,
                        "satName":satInView.name,
                        "linkId":self.groundTimes[satInView.name][self.satView[satInView.name]["linkCount"]]["linkId"],
                        "start":satInView.time,
                        "data":self.groundTimes[satInView.name][self.satView[satInView.name]["linkCount"]]["initialData"]
                    },
                )

class LinkStartObserver(Observer):
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

        In this instance, the callback function checks for notification of the "reported" property and publishes :obj:`FireReported` message to *PREFIX/constellation/reported* topic:

        """
        if property_name == GroundNetwork.PROPERTY_IN_RANGE:
            self.app.send_message(
                    "linkStart",
                    LinkStart(
                        groundId = new_value["groundId"],
                        satId = new_value["satId"],
                        satName = new_value["satName"],
                        linkId = new_value["linkId"],
                        start = new_value["start"],
                        data = new_value["data"]
                    ).json(),
                )

class LinkEndObserver(Observer):
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

        In this instance, the callback function checks for notification of the "reported" property and publishes :obj:`FireReported` message to *PREFIX/constellation/reported* topic:

        """
        if property_name == GroundNetwork.PROPERTY_OUT_OF_RANGE:
            self.app.send_message(
                    "linkCharge",
                    LinkCharge(
                        groundId = new_value["groundId"],
                        satId = new_value["satId"],
                        satName = new_value["satName"],
                        linkId = new_value["linkId"],
                        end = new_value["end"],
                        duration = new_value["duration"],
                        dataOffload = new_value["dataOffload"],
                        downlinkCost = new_value["downlinkCost"],
                        cumulativeCosts = new_value["cumulativeCosts"]
                    ).json()
                )

# def on_ready(client, userdata, message):
#     """
#     *Callback function appends a new satellite name in prep for all_ready method*

#     .. literalinclude:: /../../firesat/grounds/main_ground.py
#         :lines: 66-67

#     """
#     for index, observer in enumerate(app.simulator._observers):
#         if isinstance(observer, GroundNetwork):
#             app.simulator._observers[index].on_ready(client, userdata, message)

# def all_ready(client, userdata, message):
#     """
#     *Callback function creates two new dictionaries with keys corresponding to satellite names*

#     .. literalinclude:: /../../firesat/grounds/main_ground.py
#         :lines: 70-71

#     """
#     for index, observer in enumerate(app.simulator._observers):
#         if isinstance(observer, GroundNetwork):
#             app.simulator._observers[index].all_ready(client, userdata, message)

# def on_commRange(client, userdata, message):
#     """
#     *Callback function checks for transitions of commRange boolean*

#     .. literalinclude:: /../../firesat/grounds/main_ground.py
#         :lines: 74-89

#     """
#     for index, observer in enumerate(app.simulator._observers):
#         if isinstance(observer, GroundNetwork):
#             app.simulator._observers[index].on_commRange(client, userdata, message)


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

    # initialize the GroundNetwork Observable
    groundNetwork = GroundNetwork(app,GROUND)

    # add observers for in-range and out-of-range switches to the groundNetwork object class
    groundNetwork.add_observer(LinkStartObserver(app))
    groundNetwork.add_observer(LinkEndObserver(app))

    # add the groundNetwork observer to monitor simulation for switch to EXECUTING mode
    app.simulator.add_observer(groundNetwork)

    # add a shutdown observer to shut down after a single test case
    app.simulator.add_observer(ShutDownObserver(app))

    # start up the application on PREFIX, publish time status every 10 seconds of wallclock time
    app.start_up(
        PREFIX,
        config,
        True,
        time_status_step=timedelta(seconds=10) * SCALE,
        time_status_init=datetime(2020, 10, 24, 7, 20, tzinfo=timezone.utc),
        time_step=timedelta(seconds=1) * SCALE,
    )

    # add message callbacks for constellation messages
    app.add_message_callback("constellation", "ready", groundNetwork.on_ready)
    app.add_message_callback("constellation","allReady", groundNetwork.all_ready)
    app.add_message_callback("constellation", "location", groundNetwork.on_commRange)