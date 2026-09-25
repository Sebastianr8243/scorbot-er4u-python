# Authors: Jose Luis Pérez Pérez and Yolanda M. Gimeno Rodríguez
# Date:
# Title: Generation of the .json file
# University of La Laguna

import json
from math import pi
from pathlib import Path


CONFIG_PATH = Path(__file__).with_name("data.json")

#################################################################################
# Script that generates the standard runtime configuration JSON file for the app.
#################################################################################

# Function to extract information from the .json file with configuration variables.
#
# a   -> Group that contains the variable
# b   -> Variable name
#
def readData(a,b):
    if not CONFIG_PATH.exists():
        setup()
    with CONFIG_PATH.open('r', encoding='utf-8') as f:
        data = json.load(f)
    return data[a][b]

# Libreria con las variables globales
#
def setup():
    try:
        info ={
            # General-purpose configuration variables
            "general":{
                # Maximum length of a message
                "MSG_LEN": 128,
                # Maximum valid sequence byte value
                "MAX_COUNT": 256,
                # Maximum acceptable error in the error bytes
                "MAX_ERROR": 40,
                #
                "ite": 100,
                # Timeout for the output endpoint
                "TIME_OUT_W" : 1500,
                # Timeout for the input endpoint
                "TIME_OUT_R" : 1500,
                # Identifier for no errors during the action
                "DONE" : 0,
                # Identifier for the program shutdown request
                "EXIT" : 528,
                # Positions of each motor's data bytes within the buffer
                #   [base, shoulder, elbow, wrist motor 1, wrist motor 2, gripper]
                "VEC_POS":[19 ,24, 29, 34, 39, 44],
                # Positions of each motor's error bytes within the buffer
                #   [base, shoulder, elbow, wrist motor 1, wrist motor 2, gripper]
                "VEC_ERROR":[22,27,32,37,42,47],
                # Delay before making a read request after a write
                "WRITE": 0.008,
                # Delay before starting the next message after a read request
                "READ": 0.005,
                # Minimum value of the upper valid range
                "upLimit": 50000,
                # Maximum value of the lower valid range
                "downLimit": 20000, # " " " " " "
                # Relative initial base/shoulder/elbow position in encoder values after HOME
                "posRef": [0,10350,55401],
                # Relative initial joint positions in angle values after HOME
                "angRef": [0, 90, -90, 0, 0],
                # Link lengths
                "longitudes":[364,220,220],
                # d term of inverse kinematics
                "link-offset":[364,0,0,0,145.125],
                # a term of inverse kinematics
                "link-length":[16,220,220,0,0],
                # alpha term of inverse kinematics
                "link-twist-angle":[pi/2,0,0,pi/2]
            },
            # Variables related to the base joint
            "cadera":{
                # Joint speed during HOME
                "h_vel": 20,
                # Limit switch identifier
                "switch": 1,
                # Delay before making a read request after a write
                "write": 0.008,
                # Delay before starting the next message after a read request
                "read" : 0.012
            },
            # Variables related to the shoulder joint
            "hombro":{
                # Joint speed during HOME
                "h_vel": 10,
                # Limit switch identifier
                "switch" : 2,
                # Delay before making a read request after a write
                "write": 0.008,
                # Delay before starting the next message after a read request
                "read" : 0.012
            },
            # Variables related to the elbow joint
            "codo":{
                # Joint speed during HOME
                "h_vel": 20,
                # Limit switch identifier
                "switch" : 4,
                # Delay before making a read request after a write
                "write": 0.008,
                # Delay before starting the next message after a read request
                "read" : 0.012
            },
            # Variables related to the wrist joint
            "wrist":{
                # Joint speed during HOME
                "h_vel": 10,
                # Roll limit switch identifier
                "switch_roll" : 16,
                # Pitch limit switch identifier
                "switch_pitch" : 8,
                # Delay before making a read request after a write
                "write": 0.008,
                # Delay before starting the next message after a read request
                "read" : 0.013
            },
            # Variables related to the gripper joint
            "pinza":{
                # Motion speed. Constant value
                "vel" : 150,
                # Delay before making a read request after a write
                "write" : 0.008,
                # Delay before starting the next message after a read request
                "read" : 0.019,
                # Number of iterations the movement lasts. Constant value
                "ite_clamp" : 30
            }
        }

        f = json.dumps(info, indent=2)

        if not CONFIG_PATH.exists():
            with CONFIG_PATH.open('w', encoding='utf-8') as outfile:
                outfile.write(f)
    except OSError as exc:
        raise RuntimeError("Could not create the robot configuration") from exc
