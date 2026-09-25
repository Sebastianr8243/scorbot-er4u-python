# Authors: Jose Luis Pérez Pérez and Yolanda M. Gimeno Rodríguez
# Date: 08/07/2020
# Title: Main script for Open Scorbot.
# University of La Laguna

import usb.core
import usb.util
import libsync
import libcomm
import libdef
import log
import logging
import threading
import queue
import time
import conf
import sys
from PyQt5 import QtWidgets, uic, QtGui

# By default the program starts in online mode
online = True

# Queue to store the synchronization byte
cola_sync = queue.Queue()
# Queue to indicate pending orders to be executed
cola_orden = queue.Queue()
# Queue with the averaged encoder read vector
cola_read = queue.Queue()

buffer = 0

# Launch the graphical interface and the connection
def main():
	set_conection()
	app = QtWidgets.QApplication(sys.argv)
	main = MainWindow()
	main.show()
	sys.exit(app.exec_())

def set_conection():
	global online
	#########################################################################
	# Look up the arm using the device identifiers
	try:
		dev = usb.core.find(idVendor = 0x09f1, idProduct = 0x0007)
	except usb.core.USBError:
		print("error dev")
		online = False

	# If it is not connected, exit the program
	if not dev or online == False:
		print("Device not found")
		logging.warning(libdef.error_msg(12)) # Device not found
		online = False
	else:
		print("Device found")
		logging.debug(libdef.info_text(15)) # Device found
		# Save the interface 0 configuration of device 0
		i = dev[0].interfaces()[0].bInterfaceNumber

		# Reset to take control
		try:
			dev.reset()
		except usb.core.USBError:
			print("entity not found")
			logging.warning(libdef.error_msg(13)) # Entity not found
			online = False
			return -1

		# Detach the arm from the kernel
		if dev.is_kernel_driver_active(i):
		    dev.detach_kernel_driver(i)

		# Read the configuration data
		cfg = dev.get_active_configuration()
		# Read the endpoint data from the configuration
		intf = cfg[(0,0)]

		# Search for the INPUT ENDPOINT in the configuration
		epin = usb.util.find_descriptor(
		    intf,
		    custom_match = \
		    lambda e: \
		        usb.util.endpoint_direction(e.bEndpointAddress) == \
		        usb.util.ENDPOINT_IN)

		# Search for the OUTPUT ENDPOINT in the configuration
		epout = usb.util.find_descriptor(
				    intf,
				    custom_match = \
				        lambda e: \
				            usb.util.endpoint_direction(e.bEndpointAddress) == \
				            usb.util.ENDPOINT_OUT)

		# Create the buffer to store replies using the size allowed by the input EP
		global buffer

		buffer= usb.util.create_buffer(epin.wMaxPacketSize)

		# Load the JSON file containing the joint-specific configuration and other
		# runtime details used by the program
		conf.setup()

		# Send the initial messages and initialize the sequence byte and the average
		# position vector for the encoders
		print('Connecting...')
		logging.info(libdef.info_text(16))
		ans = libsync.msg_start(epout,epin,buffer)
		print('Connected')
		logging.info(libdef.info_text(17))

		# Extract the sequence byte value
		b_1 = ans[0]

		# Extract the average value vector
		media = ans[1]

		# Put the sequence byte into the sync queue and the average value into read queue
		cola_sync.put(b_1)
		cola_read.put(media)

		# Main synchronization thread
		h1	= threading.Thread(target = libsync.syncro, args = [cola_sync, cola_read, epout, epin, buffer])
		# Command execution thread
		h2	= threading.Thread(target = libcomm.execute, args = [cola_sync, cola_orden, cola_read, epout, epin, buffer])

		# Start the threads
# Class defining the graphical window of the program.
# Built with PyQt5
class MainWindow(QtWidgets.QMainWindow):
	def __init__(self, *args, **kwargs):
		super(MainWindow, self).__init__(*args,**kwargs)
		from pathlib import Path
		uic.loadUi(str(Path(__file__).with_name('open_SCB.ui')), self)
		self.hip_left.clicked.connect(lambda:libdef.write_data(self, 4, cola_orden, buffer))
		self.hip_right.clicked.connect(lambda:libdef.write_data(self, 5, cola_orden, buffer))
		self.shoulder_up.clicked.connect(lambda:libdef.write_data(self, 6, cola_orden, buffer))
		self.shoulder_down.clicked.connect(lambda:libdef.write_data(self, 7, cola_orden, buffer))
		self.elbow_up.clicked.connect(lambda:libdef.write_data(self, 8, cola_orden, buffer))
		self.elbow_down.clicked.connect(lambda:libdef.write_data(self, 9, cola_orden, buffer))
		self.roll_left.clicked.connect(lambda:libdef.write_data(self,13, cola_orden, buffer))
		self.pitch_up.clicked.connect(lambda:libdef.write_data(self,10, cola_orden, buffer))
		self.roll_right.clicked.connect(lambda:libdef.write_data(self,12, cola_orden, buffer))
		self.pitch_down.clicked.connect(lambda:libdef.write_data(self,11, cola_orden, buffer))
		self.open_clamp.clicked.connect(lambda:libdef.write_data(self,14, cola_orden, buffer))
		self.close_clamp.clicked.connect(lambda:libdef.write_data(self,15, cola_orden, buffer))
		self.pushbutton_home.clicked.connect(lambda:libdef.write_data(self,18, cola_orden, buffer))
		self.pushButton_go.clicked.connect(lambda:libdef.write_data(self,19, cola_orden, buffer))
		self.pushbutton_exit.clicked.connect(lambda:exit(self))
		self.pushbutton_online.clicked.connect(lambda:libdef.stateMotors(self, 17, cola_orden, buffer))
		self.pushbutton_offline.clicked.connect(lambda:libdef.stateMotors(self, 16, cola_orden, buffer))
		check_conection(self)

# Verify that the connection was successful. If not, disable the interface actions
# except for the exit button.
def check_conection(self):
	if online == False:
		self.pushbutton_online.setEnabled(False)
		self.pushbutton_offline.setEnabled(False)
		self.pushbutton_online.setStyleSheet("background-color: rgb(240,240,240)")
		# Warning message for the error.
		libdef.send_textlabel(self, 1, 12)
		libdef.stateButtons(self,False)

# Close the graphical window
def exit(self):
	QtWidgets.QApplication.quit()
	libdef.write_data(self,'exit', cola_orden, buffer)
