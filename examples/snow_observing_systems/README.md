# Snow Observing Systems Visualization

## Overview
This project visualizes the position and sensor radius of the CAPELLA-14 (ACADIA-4) satellite in real-time using Flask for the backend and CesiumJS for the frontend.

## Project Structure

. ├── env.js ├── index.html ├── README.md ├── requirements.txt └── server.py

- **env.js**: Contains environment variables such as the API token.
- **index.html**: The frontend of the application, which uses CesiumJS to visualize the satellite's position.
- **README.md**: This file, providing an overview and documentation of the project.
- **requirements.txt**: Lists the Python dependencies required to run the project.
- **server.py**: The backend of the application, which uses Flask to serve endpoints and Skyfield to compute satellite positions.

## Installation
1. Clone the repository:
    ```sh
    git clone <repository-url>
    cd <repository-directory>
    ```

2. Install the required Python packages:
    ```sh
    pip install -r requirements.txt
    ```

## Running the Project
1. Start the Flask server:
    ```sh
    python server.py
    ```

2. Open [`index.html`](command:_github.copilot.openRelativePath?%5B%7B%22scheme%22%3A%22file%22%2C%22authority%22%3A%22%22%2C%22path%22%3A%22%2Fmnt%2Fc%2FUsers%2Femgonz38%2FOneDrive%20-%20Arizona%20State%20University%2Fubuntu_files%2Fwork%2Fcode%2Fgit_repos%2Fforks%2Fnost-tools%2Fexamples%2Fsnow_observing_systems%2Findex.html%22%2C%22query%22%3A%22%22%2C%22fragment%22%3A%22%22%7D%5D "/mnt/c/Users/emgonz38/OneDrive - Arizona State University/ubuntu_files/work/code/git_repos/forks/nost-tools/examples/snow_observing_systems/index.html") in a web browser to view the visualization.

## Endpoints
- **/**: Serves the main page.
- **/get_position**: Returns the current position and sensor radius of the satellite in JSON format.
- **/env.js**: Serves the environment variables.

## Example Response from [`/get_position`](command:_github.copilot.openSymbolFromReferences?%5B%22%2Fget_position%22%2C%5B%7B%22uri%22%3A%7B%22%24mid%22%3A1%2C%22fsPath%22%3A%22%2Fmnt%2Fc%2FUsers%2Femgonz38%2FOneDrive%20-%20Arizona%20State%20University%2Fubuntu_files%2Fwork%2Fcode%2Fgit_repos%2Fforks%2Fnost-tools%2Fexamples%2Fsnow_observing_systems%2Fserver.py%22%2C%22external%22%3A%22file%3A%2F%2F%2Fmnt%2Fc%2FUsers%2Femgonz38%2FOneDrive%2520-%2520Arizona%2520State%2520University%2Fubuntu_files%2Fwork%2Fcode%2Fgit_repos%2Fforks%2Fnost-tools%2Fexamples%2Fsnow_observing_systems%2Fserver.py%22%2C%22path%22%3A%22%2Fmnt%2Fc%2FUsers%2Femgonz38%2FOneDrive%20-%20Arizona%20State%20University%2Fubuntu_files%2Fwork%2Fcode%2Fgit_repos%2Fforks%2Fnost-tools%2Fexamples%2Fsnow_observing_systems%2Fserver.py%22%2C%22scheme%22%3A%22file%22%7D%2C%22pos%22%3A%7B%22line%22%3A48%2C%22character%22%3A12%7D%7D%5D%5D "Go to definition")
```json
{
    "name": "CAPELLA-14 (ACADIA-4)",
    "latitude": 37.7749,
    "longitude": -122.4194,
    "altitude": 500.0,
    "sensor_radius": 1000.0
}