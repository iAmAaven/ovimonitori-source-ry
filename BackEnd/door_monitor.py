#!/usr/bin/env python3

import json  # JSON library for reading/writing JSON files
from gpiozero import Button  # GPIO library for handling GPIO pins
from signal import pause  # Signal library for pausing the script (keeps it running by waiting for events)
from datetime import date, datetime  # datetime library for handling date and time
from pathlib import Path  # Pathlib library for handling file paths
import firebase_admin  # Firebase Admin SDK for interacting with Firebase
from firebase_admin import credentials, firestore  # Firestore library for interacting with Firestore database
from apscheduler.schedulers.background import BackgroundScheduler  # Scheduler for running tasks in the background



# This script monitors the SOURCE club room door (A0-35) using a GPIO button (magnetic switch on the door)
# and logs door status, as well as the opening and closing times, to a Firestore Database.
# It also maintains JSON files to keep track of the current door status, latest open/close timestamps, and daily door data.

# The switch has to be connected to GPIO pin 21 (BCM mode) and GND.
# If 21 is not available, change the pin number in the code (at the bottom where the main program starts).



# Path to the project folder
PROJECT_FOLDER = "/home/source/DoorMonitor/"

# Path to the JSON file storing the current door status and timestamps
CURRENT_STATUS_FILE = PROJECT_FOLDER + "current_status.json"
# Path to the JSON file storing the daily door data
DAY_DATA_FILE = PROJECT_FOLDER + "day_data.json"
# Path to the Firebase credentials file
CREDENTIALS_FILE = PROJECT_FOLDER + "credentials.json"


# ---------- FUNCTIONS ----------

# Returns the current timestamp in seconds (used for logging open/close times)
def get_timestamp():
    return int(datetime.now().timestamp())

# Returns today's date as a string in 'yyyy-mm-dd' format
def get_today_date():
    return date.today().strftime('%Y-%m-%d')

# Reads the daily data from the JSON file and returns it as a dictionary
def get_day_data():
    with open(DAY_DATA_FILE, 'r') as file:
        return json.load(file)

# Reads the current status data from the JSON file and returns it as a dictionary
def get_status_data():
    with open(CURRENT_STATUS_FILE, 'r') as file:
        return json.load(file)



# Pushes a new entry of the previous opening and closing timestamps as a dictionary to the day_data.json
def current_openings_to_json():
    status_data = get_status_data()
    new_entry = { "opened": status_data["lastOpened"], "closed": status_data["lastClosed"]}
    day_data = get_day_data()
    today = get_today_date()
    
    if today in day_data:
        # If today's date exists, increments the number of openings and append the new entry
        day_data[today]["numOfOpenings"] += 1
        day_data[today]["openings"].append(new_entry)
        with open(DAY_DATA_FILE, 'w') as file:
            json.dump(day_data, file)
    else:
        # If it's a new day, overwrites the file with the new day's data
        with open(DAY_DATA_FILE, 'w') as file:
            json.dump({
                today: {
                    "numOfOpenings": 1,
                    "openings": [new_entry]
                }
            }, file)



# Sends the current status and previous timestamps to Firebase
def status_to_firebase():
    try:
        status_data = get_status_data()  # Gets the current status data from the JSON file

        # Updates the door status and last opened/closed timestamps in the Firestore database
        db.collection("door_data").document("current_status").set({
            "isOpen": status_data["isOpen"],
            "lastOpened": status_data["lastOpened"],
            "lastClosed": status_data["lastClosed"]
        })

    except Exception as e:
        print(f"[ERROR] Exception while updating status in Firebase: {e}")



# Sends the full day's data to Firebase
def send_full_data_to_db():
    try:
        day_data = get_day_data()
        date_id = next(iter(day_data))  # Gets the date key (should be only one)
        numOfOpens = day_data[date_id]["numOfOpenings"]
        opens = day_data[date_id]["openings"]
        print("DATA SAVED TO DATABASE UNDER ", date_id)
        db.collection("door_data").document(date_id).set({
            "num_of_openings": numOfOpens,
            "openings": opens
        })
    except Exception as e:
        print(f"[ERROR] Failed to log data: {e}")



# Updates the current status and handles daily data and Firebase updates
def update_status(status):
    status_data = get_status_data()
    if(status_data["isOpen"] == status):
        # If the status hasn't changed, do nothing
        print("Status unchanged, no update needed.")
        return
    
    status_data["isOpen"] = status

    if(status == 0):
        # Door closed: updates lastClosed timestamp
        status_data["lastClosed"] = get_timestamp()
        print("Door closed")
    else:
        # Door opened: updates lastOpened timestamp
        status_data["lastOpened"] = get_timestamp()
        print("Door opened")
    
    # Saves the updated status to the JSON file
    with open(CURRENT_STATUS_FILE, 'w') as file:
        json.dump(status_data, file)

    if(status == 0 and status_data["lastOpened"] != 0):
        # If the door is being closed and it was previously opened,
        # checks if the date has changed before updating the day_data.json.
        # This ensures data is sent to Firebase only once per day.
        day_data = get_day_data()
        today = get_today_date()
        date_id = next(iter(day_data))
        if today != date_id:
            send_full_data_to_db()  # Sends previous day's data to Firebase

        current_openings_to_json()  # Logs the new opening/closing event
    
    # Always updates the current status to Firebase
    status_to_firebase()


# This function is called when a new day starts
# Reason for this function is that if the door is closed for multiple days in a row,
#   the program pushes logs to the database even if empty. This is to ensure that the
#   web page of the door's statistics gets appropriate data.
def new_day_is_here():
    print("New day detected!")
    try:
        day_data = get_day_data()
        date_id = next(iter(day_data))  # Gets the date key (should be only one)

        if(db.collection("door_data").document(date_id).get().exists):
            print("Data already detected from ", date_id, ". Data not sent to Firebase.")
            return

        send_full_data_to_db()  # Sends the previous day's data to Firebase

        with open(DAY_DATA_FILE, 'w') as file:
            # Resets the day_data.json for the new day
            json.dump({
                get_today_date(): {
                    "numOfOpenings": 0,
                    "openings": []
                }
            }, file)
            print("day_data JSON reset for the new day.")
    except Exception as e:
        print(f"[ERROR] Failed to archive previous day's data: {e}")



# INITIALIZATIONS

# ---------- Firebase initialization ----------
if not firebase_admin._apps:
    cred = credentials.Certificate(CREDENTIALS_FILE)
    firebase_admin.initialize_app(cred)
db = firestore.client()

# ---------- current_status.json initialization ----------
if not Path(CURRENT_STATUS_FILE).exists() or Path(CURRENT_STATUS_FILE).stat().st_size == 0:
    # If the status file doesn't exist or is empty, creates it with default values
    with open(CURRENT_STATUS_FILE, 'w') as file:
        json.dump({
            "isOpen": 0,
            "lastOpened": 0,
            "lastClosed": 0
        }, file)
        print("JSON created.")

# ---------- day_data.json initialization ----------
if not Path(DAY_DATA_FILE).exists() or Path(DAY_DATA_FILE).stat().st_size == 0:
    # If the day data file doesn't exist or is empty, creates it with today's date and empty data
    with open(DAY_DATA_FILE, 'w') as file:
        json.dump({
            f"{get_today_date()}": {
                "numOfOpenings": 0,
                "openings": []
            }
        }, file)
        print("day_data JSON created.")



# Scheduler to run the new_day_is_here function at 00:01.
# The scheduler waits one minute after midnight to make sure that the get_today_date function returns the correct date
scheduler = BackgroundScheduler(timezone="Europe/Helsinki")
scheduler.add_job(new_day_is_here, 'cron', hour=0, minute=1)
scheduler.start()



# GPIO Button setup
door_button = Button(21, bounce_time=1.0)  # Set up the GPIO button on pin 21 with a bounce time of 1.0 seconds

# Button event handlers
door_button.when_released = lambda: update_status(1)  # When the button is released, the door is opened
door_button.when_pressed = lambda: update_status(0)   # When the button is pressed, the door is closed

pause()  # Keeps the script running and waiting for button events