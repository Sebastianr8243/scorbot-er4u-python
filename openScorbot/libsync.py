# Authors: Jose Luis Pérez Pérez and Yolanda M. Gimeno Rodríguez
# Date:
# Title: Synchronization line script
# University of La Laguna

import time
import libcomm
import libhex
import libdef
import conf
import log
import logging

###############################################################################
# Script that performs the initial connection between the application and the
# controller and maintains synchronization during program use.
###############################################################################

# Delay before making a read request after a write
WRITE = conf.readData("general","WRITE")

# Delay before starting the next message after a read request
READ = conf.readData("general","READ")

# Bound each initial controller acknowledgment wait.
HANDSHAKE_WAIT_TIMEOUT_S = 30.0

# Main synchronization thread between the controller and the program.
# It sends idle-state messages to the controller.
# It keeps a real-time communication channel with each encoder status, updating
# the sequence byte and the average vector.
#
# cola_sync -> Queue with the sequence byte
# cola_read -> Queue with the averaged encoder position vector
# epout     -> Controller output endpoint object
# epin      -> Controller input endpoint object
# buffer    -> Vector storing the most recent read data
#
def syncro(cola_sync, cola_read, epout, epin, buffer):
	while 1:
		b_1 = cola_sync.get()
		if b_1 == conf.readData("general","EXIT"):
			break
		if b_1 == 256:
			time.sleep(READ)
		else:
			cadena = libhex.mov_comm(1)
			b_1 = libdef.countByte1(b_1)
			cadena = cadena.format(libdef.f_byte(b_1))
			cadena = libdef.fill_msg(cadena, 24)
			media = libdef.get_media(buffer,cola_read.get())
			cadena += libdef.get_encoder(buffer,media)
			libdef.set_msg(cadena, epout, epin, buffer, WRITE, READ)
			media = libdef.get_media(buffer,media)
			cola_read.put(media)
			cola_sync.put(b_1)

# Function that establishes the initial connection with the controller. It is split
# into three message groups. It initializes the average vector.
# The function returns the sequence byte value and the average vector.
#
# epout     -> Controller output endpoint object
# epin      -> Controller input endpoint object
# buffer    -> Vector storing the most recent read data
#
def msg_start(epout,epin,buffer):
	media = []
	vec_pos = conf.readData("general","VEC_POS")
	for i in range(len(vec_pos)):
		vect_int = []
		for j in range(2):
			vect_int.append(buffer[vec_pos[i] + j])
		dato_int = libdef.transform(vect_int)
		media.append(dato_int)

	b_1 = 0
	b_1 = send_pkt1(b_1,epout,epin,buffer)
	b_1 = send_wait(b_1,epout,epin,buffer)
	b_1 = send_pkt2(b_1,epout,epin,buffer)
	b_1 = send_wait(b_1,epout,epin,buffer)
	b_1 = send_pkt3(b_1,epout,epin,buffer,media)

	return [b_1, media]

# First packet of the connection process. It is the smallest of the three and is
# sent immediately after the USB connection is established.
# Returns the sequence byte value.
#
# b_1       -> Sequence byte
# epout     -> Controller output endpoint object
# epin      -> Controller input endpoint object
# buffer    -> Vector storing the most recent read data
#
def send_pkt1(b_1,epout,epin,buffer):
	while b_1 < 4:
		cadena = libhex.mov_comm(6)
		b_1 = libdef.countByte1(b_1)
		cadena = cadena.format(libdef.f_byte(b_1))
		cadena = libdef.fill_msg(cadena, 8)
		msg = libhex.get_msg1(b_1)
		cadena += msg
		libdef.set_msg(cadena, epout, epin, buffer, WRITE, READ)

	return b_1

# Second packet of the connection process.
# Returns the sequence byte value in decimal.
#
# b_1       -> Sequence byte
# epout     -> Controller output endpoint object
# epin      -> Controller input endpoint object
# buffer    -> Vector storing the most recent read data
#
def send_pkt2(b_1,epout,epin,buffer):
	for i in range(1,9):
		cadena = libhex.get_msg2(80)
		if(i < 3):
			b_1 = libdef.countByte1(b_1)
			cadena = cadena.format(libdef.f_byte(b_1), '72')
			libdef.set_msg(cadena, epout, epin, buffer, WRITE, READ)

		if(i >= 3 and i < 6):
			b_1 = libdef.countByte1(b_1)
			cadena = cadena.format(libdef.f_byte(b_1), '64')
			libdef.set_msg(cadena, epout, epin, buffer, WRITE, READ)

		if(i >= 6):
			b_1 = libdef.countByte1(b_1)
			cadena = cadena.format(libdef.f_byte(b_1), '61')
			libdef.set_msg(cadena, epout, epin, buffer, WRITE, READ)

	cadena = libhex.get_msg2(81)
	b_1 = libdef.countByte1(b_1)
	cadena = cadena.format(libdef.f_byte(b_1))
	libdef.set_msg(cadena, epout, epin, buffer, WRITE, READ)
	cadena = libhex.get_msg2(82)
	b_1 = libdef.countByte1(b_1)
	cadena = cadena.format(libdef.f_byte(b_1))
	libdef.set_msg(cadena, epout, epin, buffer, WRITE, READ)
	b_7 = 0
	b_6 = 1
	for i in range(1,80):
		cadena = libhex.get_msg2(83)
		msg = libhex.get_msg2(i)
		b_1 = libdef.countByte1(b_1)
		b_7 = countByte7(b_7)
		b_6 = countByte6(b_7, b_6)
		cadena = cadena.format(libdef.f_byte(b_1), libdef.f_byte(b_6), libdef.f_byte(b_7))
		cadena += msg
		libdef.set_msg(cadena, epout, epin, buffer, WRITE, READ)

	cadena = libhex.get_msg2(84)
	b_1 = libdef.countByte1(b_1)
	cadena = cadena.format(libdef.f_byte(b_1))
	libdef.set_msg(cadena, epout, epin, buffer, WRITE, READ)
	return b_1

# Third packet of the connection process. It enables the motors.
# Returns the sequence byte value in decimal.
#
# b_1       -> Sequence byte
# epout     -> Controller output endpoint object
# epin      -> Controller input endpoint object
# buffer    -> Vector storing the most recent read data
#
def send_pkt3(b_1,epout,epin,buffer, media):
	for i in range(40):
		cadena = libhex.mov_comm(1)
		b_1 = libdef.countByte1(b_1)
		cadena = cadena.format(libdef.f_byte(b_1))
		media = libdef.get_media(buffer,media)
		libdef.set_msg(cadena, epout, epin, buffer, WRITE, READ)

	cadena = libhex.mov_comm(1)
	b_1 = libdef.countByte1(b_1)
	cadena = cadena.format(libdef.f_byte(b_1))
	cadena = libdef.fill_msg(cadena,24)
	media = libdef.get_media(buffer,media)
	cadena += libdef.get_encoder(buffer,media)
	libdef.set_msg(cadena, epout, epin, buffer, WRITE, READ)

	for i in range(1,8):
		cadena = libhex.mov_comm(6)
		b_1 = libdef.countByte1(b_1)
		cadena = cadena.format(libdef.f_byte(b_1))
		cadena = libdef.fill_msg(cadena, 8)
		cadena += libhex.motorson(i)
		cadena = libdef.fill_msg(cadena,24)
		media = libdef.get_media(buffer,media)
		cadena += libdef.get_encoder(buffer,media)
		libdef.set_msg(cadena, epout, epin, buffer, WRITE, READ)

	return b_1

# Function used after sending each of the three preceding message groups. Its goal is
# to send a command-free message that keeps the sequence running until the controller
# confirms it received the full group of messages.
#
# b_1       -> Sequence byte
# epout     -> Controller output endpoint object
# epin      -> Controller input endpoint object
# buffer    -> Vector storing the most recent read data
#
def send_wait(b_1,epout,epin,buffer):
	deadline = time.monotonic() + HANDSHAKE_WAIT_TIMEOUT_S
	while buffer[1] != 13:
		if time.monotonic() >= deadline:
			raise TimeoutError("ER-4U controller handshake acknowledgment timed out")
		cadena = libhex.mov_comm(1)
		b_1 = libdef.countByte1(b_1)
		cadena = cadena.format(libdef.f_byte(b_1))
		libdef.set_msg(cadena, epout, epin, buffer, WRITE, READ)

	cadena = libhex.mov_comm(1)
	b_1 = libdef.countByte1(b_1)
	cadena = cadena.format(libdef.f_byte(b_1))
	libdef.set_msg(cadena, epout, epin, buffer, WRITE, READ)
	return b_1

# The two counters below are used in send_pkt2 to simplify message generation.

# Counter dependent on the value returned by countByte7. It doubles its value when
# b_7 = 0.
#
# b_6 -> Value of byte 6 in the message
# b_7 -> Value of byte 7 in the message
#
def countByte6(b_7, b_6):
	if b_7 == 0:
		b_6 += b_6
		return b_6
	else:
		return b_6

# Counter from 0 to 10, skipping 8.
#
# b_7 -> Value of byte 7 in the message
#
def countByte7(b_7):
	b_7 += 1
	if b_7 <= 10:
		#Para saltar el 8
		if(b_7 == 8):
			b_7 += 1
		return b_7
	else:
		b_7 = 0
		return b_7
