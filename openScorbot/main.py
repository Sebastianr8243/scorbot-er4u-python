# Authors: Jose Luis Pérez Pérez and Yolanda M. Gimeno Rodríguez
# Date:
# Title: Main script for Open Scorbot.
# University of La Laguna

import gui

###############################################################################
# The program is initialized here. This script creates the object used to handle
# the device and starts the two threads that divide the runtime behavior.
###############################################################################


# Start the connection and the graphical interface
if __name__ == "__main__":
    gui.main()
