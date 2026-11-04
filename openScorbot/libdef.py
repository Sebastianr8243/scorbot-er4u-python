# Authors: Jose Luis Pérez Pérez and Yolanda M. Gimeno Rodríguez
# Date:
# Title: Function library
# University of La Laguna

import usb.core
import usb.util
import time
import conf
import libhex
import queue
import log
import logging
import builtins
from math import *
from numpy import *

# TODO:
# Error messages to add:
# Function check -> Line 422
# Function get_signo -> Line 477


#############################################################
# Script containing all functions shared by the two threads
#############################################################


# Program shutdown identifier
EXIT		= 528
# Delay before starting the next message after a read request
SLEEP = conf.readData("general","READ")
# Identifier used when no errors occurred during the action
DONE = conf.readData("general", "DONE")
# Minimum value of the upper valid range
LIM_SUP = conf.readData("general", "upLimit")
# Maximum value of the lower valid range
LIM_INF = conf.readData("general", "downLimit")
# Positions of each motor's data bytes within the buffer
# [base, shoulder, elbow, wrist motor 1, wrist motor 2, gripper]
VEC_POS = conf.readData("general", "VEC_POS")
# Per-message values, read once here: re-parsing data.json for every USB message cost about 0.3 ms each.
MAX_COUNT = conf.readData("general", "MAX_COUNT")
MSG_LEN = conf.readData("general", "MSG_LEN")
TIME_OUT_W = conf.readData("general", "TIME_OUT_W")
# Reference angle, the starting point after home.
angRef = conf.readData("general", "angRef")
posRef = []
# Setpoint region (bytes, the get_encoder layout) the last movement ended on: closeMov's last
# message, stopMov's hold, or the measured snapshot motors_on takes. libcomm.execute clears it
# before every order and hands it on after the order (docs/protocol/LEDGER.md PL98).
ultimo_hold = None
# Joint names in VEC_POS order, for the messages of CountJumpError.
JOINT_NAMES = ("base", "shoulder", "elbow", "wrist_motor_1", "wrist_motor_2", "gripper")


# A reply whose 24-bit count disagrees with its low word: the top byte changed without the low word
# wrapping, so the count moved by a multiple of 65536 in one reply (a corrupt reply). Raised by the
# echo and hold builders so no setpoint is built from it (docs/requirements/count-frame.md CT16).
class CountJumpError(ValueError):
	pass


# The count guard: the buffer it watches, the last count of each joint it trusted, and the joints the
# last reply failed. Every reply passes through `check`, which judges it against the trusted counts
# (never against the reply before, so one corrupt reply cannot make the next good one look wrong).
_guard = {"buffer": None, "trusted": None, "suspect": {}}


# Forget the trusted counts; the next reply is trusted as it is. For a new connection
# (libsync.msg_start) and after the vendor home, whose `48` re-zeroes the counters.
def reset_count_guard():
	_guard.update(buffer=None, trusted=None, suspect={})


# The six controller counts (raw24 - 0x7FFFFF) of the reply in buffer.
def reply_counts(buffer):
	return [(buffer[pos + 2] << 16 | buffer[pos + 1] << 8 | buffer[pos]) - 0x7FFFFF for pos in VEC_POS]


# Judges the reply just read into buffer. A joint's count may move by any amount its low word moves
# (the shortest way, under 32768 counts per reply); a change the low word does not show is suspect and
# the trusted count is kept. A buffer the guard has not seen starts it afresh.
def watch_counts(buffer):
	counts = reply_counts(buffer)
	if _guard["buffer"] is not buffer or _guard["trusted"] is None:
		_guard.update(buffer=buffer, trusted=counts, suspect={})
		return
	trusted, suspect = _guard["trusted"], {}
	for i, (now, was) in enumerate(zip(counts, trusted)):
		if now - was == (now - was + 32768) % 65536 - 32768:
			trusted[i] = now
		else:
			suspect[JOINT_NAMES[i]] = (was, now, buffer[VEC_POS[i] + 2])
	_guard["suspect"] = suspect


# Raises CountJumpError, naming the motors, when the last reply in buffer failed the guard.
def refuse_jumped_counts(buffer):
	if _guard["buffer"] is buffer and _guard["suspect"]:
		raise CountJumpError("; ".join(
			f"{name}: the reply's count {now} (top byte {top:#04x}) jumped from {was} without its low word "
			"moving that far: a corrupt reply; no setpoint is built from it"
			for name, (was, now, top) in _guard["suspect"].items()))


# Fills the message with zeros so it reaches the specified length.
#
# msg -> Message to be padded
# len_msg -> Required final length of the message
#
## Example:
# msg = 'FFFFFF'
# len_msg = 8
# return -> 'FFFFFF00'
#
def fill_msg(msg, len_msg):
	x = len_msg - len(msg)
	for i in range(0,x):
		msg += '0'
	return msg

# Formats the sequence byte so it always has length 2.
#
# b_1 is an integer. If its value is between 0 and 16, the hexadecimal equivalent is
# a single character, so a leading 0 is added and the relevant result value is kept.
# Otherwise, the conversion is performed and the relevant data is kept.
#
# b_1   -> Sequence byte
#
## Example:
#
# b_1 = 5
# str(hex(b_1))= 'x/5'
# relevant value -> 5
# return '05'
#
def f_byte(b_1):
	x = b_1
	if b_1 < 16:
		msg = str(hex(x))
		msg = "0" + msg[2]
		return msg
	else:
		msg = str(hex(x))
		msg = msg[2] + msg[3]
		return msg

# Increments the sequence byte value by 1 on each iteration.
#
# If the counter reaches the maximum value defined in conf.py, the counter resets to 1.
#
# b_1  -> Sequence byte
#
def countByte1(b_1):
	b_1 += 1
	if b_1 < MAX_COUNT:
		return b_1
	else:
		b_1 = 1
		return b_1

# Averages the encoder values between the stored average and the most recent reading.
# If the difference between the reading and the average is greater than or equal to the
# configured threshold, the average becomes the reading value.
#
# buffer -> Vector storing the most recent read data
# media  -> Encoder position adjustment vector
#
def get_media(buffer, media):
	vec_pos = VEC_POS
	for i in range(len(vec_pos)):
		dato = transform([buffer[vec_pos[i]], buffer[vec_pos[i]+1]])
		if abs(dato - media[i]) >= 1000:
			media[i] = dato
		else:
			dato_media = (media[i] + dato)/2
			# NumPy's round (imported above) returns a float, but encoder packet
			# fields must remain integers for hexadecimal formatting.
			media[i] = builtins.round(dato_media)

	return media


# The setpoint region that echoes the smoothed positions: each joint's `media` low word with the sign
# byte of the last reply, written as the controller count (countField).
# A reply the count guard failed raises CountJumpError instead (watch_counts).
#
# The data order by joint is:
#
# 				base-shoulder-elbow-wrist1-wrist2-gripper
#
# buffer -> Vector storing the most recent read data
# media  -> Encoder position adjustment vector
#
def get_encoder(buffer, media):
	refuse_jumped_counts(buffer)
	msg = ''
	vec_pos = VEC_POS
	for i in range(len(vec_pos)):
		msg += slotHex(buffer[vec_pos[i]+2], media[i])
	return msg


# The setpoint region that holds the counts of the last reply, for the stop and the enable snapshot.
# The controller's count is raw24 - 0x7FFFFF and it reads each setpoint as a signed 32-bit count
# (docs/protocol/VENDOR_DLL_PROTOCOL.md sections 3 and 6), so -1 is held as ffffffff and +1 as
# 01000000, as countField writes the echo. The old echo of the low word and sign word held every count
# one low. A reply the count guard failed raises CountJumpError instead (watch_counts).
# Unverified on the arm.
#
# buffer -> Vector storing the most recent read data
#
def get_hold(buffer):
	refuse_jumped_counts(buffer)
	msg = ''
	for pos in VEC_POS:
		msg += slotHex(buffer[pos+2], transform([buffer[pos], buffer[pos+1]]))
	return msg


# One joint's setpoint slot: the controller count raw24 - 0x7FFFFF, with raw24 = top byte * 65536 +
# low word, as a signed 32-bit count. The top byte is what the legacy code calls the sign byte (128
# is count low + 1, 127 is low - 65535, the others are counts past +/-65535). Echoing the low word
# and sign word sent every count one low (docs/requirements/count-frame.md). Unverified on the arm.
#
# top -> The reply's byte after the low word, 0..255
# low -> Low word; a jog target may run past 0..65535 (suma/resta), which is the same count
#
def slotHex(top, low):
	return (top * 65536 + low - 0x7FFFFF).to_bytes(4, 'little', signed=True).hex()


# slotHex for the legacy pair of a low word and its sign word ('0000' for 128, 'ffff' for 127) that
# media, get_signo and suma/resta keep.
#
def countField(low, signo):
	return slotHex(128 if signo == '0000' else 127, low)


# Message library shown in the graphical interface after successful actions.
#
# cont   -> Message identifier to look up
#
def info_text(cont):
	switcher = {
		1 : 'Motion complete. Ready for the next command',
		2 : 'Closing robot connections',
		3 : 'Motors off. Motion disabled',
		4 : 'Motors on',
		5 : 'Homing in progress',
		6 : 'Base motion in progress',
		7 : 'Shoulder motion in progress',
		8 : 'Elbow motion in progress',
		9 : 'Wrist pitch motion in progress',
		10 : 'Wrist roll motion in progress',
		11 : 'Opening gripper',
		12 : 'Closing gripper',
		13 : 'Homing complete',
		14 : 'Motion in progress',
		15 : 'Device found',
		16 : 'Establishing connection',
		17 : 'Connection established',
		18 : 'Shoulder homed',
		19 : 'Elbow homed',
		20 : 'Wrist pitch homed',
		21 : 'Wrist roll homed',
		22 : 'Base homed'
		}
	return switcher.get(cont,"Invalid request")


# Message library shown in the graphical interface when an error occurs.
#
# cont   -> Message identifier to look up
#
def error_msg(cont):
	switcher = {
		1: 'ERROR 101: Motion did not complete correctly',
		2: 'ERROR 102: Motor did not respond',
		3: 'ERROR 103: Target is unreachable',
		4: 'ERROR 104: Could not calculate angle',
		5: 'WARNING: Home the robot first',
		6: 'WARNING: Base angle is outside the workspace',
		7: 'WARNING: Shoulder angle is outside the workspace',
		8: 'WARNING: Elbow angle is outside the workspace',
		9: 'ERROR 105: XYZ fields are empty',
		10: 'ERROR 106: Values must be numeric',
		11: 'ERROR 107: Restart the motors',
		12: 'ERROR 108: Device was not detected. Close the program',
		13: 'ERROR 109: Entity not found',
		14: 'INFO: Motion stopped on request'
		}
	return switcher.get(cont, "Invalid request")


# Detects a limit switch. Looks up the value associated with the argument art within
# the argument lectura_sw.
# Active switch code by joint:
#   Base   : 1
#   Shoulder: 2
#   Elbow  : 4
#   Pitch  : 8
#   Roll   : 16
#
# art          -> Identifier of the switch of interest
# lectura_sw   -> Byte 6 in the buffer containing limit-switch information
#
def get_switch(art, lectura_sw):
	state = False
	if art == 1:
		if lectura_sw % 2 != 0:
			state = True
		else:
			state = False
	elif art != 1 and lectura_sw != 0:
		vect_sw = []
		if lectura_sw % 2 != 0:
			lectura_sw -= 1
		if lectura_sw >= 16:
			lectura_sw -= 16
			vect_sw.append(16)
		if lectura_sw >= 8:
			lectura_sw -= 8
			vect_sw.append(8)
		if lectura_sw >= 4:
			lectura_sw -= 4
			vect_sw.append(4)
		if lectura_sw == 2:
			vect_sw.append(2)

		for i in range(len(vect_sw)):
			if vect_sw[i] == art:
				state = True
	return state


# Start sequence for a joint movement.
#
# b_1       -> Sequence byte
# media     -> Encoder position adjustment vector
# epout     -> Controller output endpoint object
# epin      -> Controller input endpoint object
# buffer    -> Vector storing the most recent read data
# write     -> Delay before making a read request after a write
# read      -> Delay before starting the next message after a read request
#
def openMov(b_1, media, epout, epin, buffer, write, read):
	cadena = libhex.mov_comm(2)
	b_1 = countByte1(b_1)
	cadena = cadena.format(f_byte(b_1))
	cadena = fill_msg(cadena, 24)
	media = get_media(buffer,media)
	cadena += get_encoder(buffer, media)
	set_msg(cadena, epout, epin, buffer, write, read)
	media = get_media(buffer,media)

	return [b_1, buffer, media]

# End sequence for a joint movement.
#
# b_1        -> Sequence byte
# media      -> Encoder position adjustment vector
# orden      -> Movement order; it distinguishes direction of movement
# signal_out -> String with motor information
# epout      -> Controller output endpoint object
# epin       -> Controller input endpoint object
# buffer     -> Vector storing the most recent read data
# write      -> Delay before making a read request after a write
# read       -> Delay before starting the next message after a read request
#
def closeMov(b_1, media, orden, signal_out, epout, epin, buffer, write, read):
	cadena = libhex.mov_comm(3)
	b_1 = countByte1(b_1)
	cadena = cadena.format(f_byte(b_1))
	msg = get_encoder(buffer,media)
	cadena += getStruct(orden, signal_out, msg)
	set_msg(cadena, epout, epin, buffer, write, read)

	media = get_media(buffer,media)
	cadena = libhex.mov_comm(4)
	b_1 = countByte1(b_1)
	cadena = cadena.format(f_byte(b_1))
	msg = get_encoder(buffer,media)
	cadena += getStruct(orden, signal_out, msg)
	set_msg(cadena, epout, epin, buffer, write, read)

	media = get_media(buffer,media)
	cadena = libhex.mov_comm(5)
	b_1 = countByte1(b_1)
	cadena = cadena.format(f_byte(b_1))
	msg = get_encoder(buffer,media)
	hold = getStruct(orden, signal_out, msg)
	cadena += hold
	set_msg(cadena, epout, epin, buffer, write, read)
	# The idle messages keep this last setpoint (the jog target), as USBC.dll's idle 0D keeps its own.
	global ultimo_hold
	ultimo_hold = bytes.fromhex(hold)

	media = get_media(buffer,media)

	return [b_1, buffer, media]

# Ends a joint movement early, on request. Not an emergency stop.
#
# Sends the message that opens every movement (mov_comm(2), which the vendor
# DLL names "Clear communication buffer") and then the three end-of-movement
# commands (mov_comm(3..5)). The vendor's own arm stop is this sequence
# (docs/protocol/VENDOR_DLL_PROTOCOL.md section 5); it is from disassembly and
# unverified on the controller.
#
# Unlike closeMov, no message carries the jog target. The vendor's stop first
# copies the counts of the last reply over its setpoints, once (lab build:
# Stop('A') -> 0x100217c9 -> 0x100212d1 copies the measured array, which
# 0x10006c17 fills from every reply as raw24 - 0x7FFFFF, 0x1000b130; no
# averaging), and every message then carries those setpoints. So all four
# messages here carry the counts of the last reply (get_hold), not the
# smoothed `media` (get_media lags a moving joint by a few counts), and the
# idle messages keep that hold. The returned media is the last reply's low
# words, the legacy form the jogs and the smoothing read.
#
# Arguments as for openMov; `media` is replaced, not read.
def stopMov(b_1, media, epout, epin, buffer, write, read):
	media = [transform([buffer[pos], buffer[pos+1]]) for pos in VEC_POS]
	hold = get_hold(buffer)
	return _stop_with_hold(b_1, media, hold, epout, epin, buffer, write, read)


# CT20: freeze the guard's trusted counts before the stop replies can update them. Never read
# setpoints from the suspect reply or clear the guard. Same stop bytes and sleeps; arm unverified.
def stop_at_trusted(b_1, media, epout, epin, buffer, write, read):
	if _guard["buffer"] is not buffer or _guard["trusted"] is None:
		raise CountJumpError("No trusted counts for this buffer; cannot build a stop")
	counts = list(_guard["trusted"])
	media = [(count + 0x7FFFFF) & 0xFFFF for count in counts]
	hold = ''.join(setpointField(count) for count in counts)
	return _stop_with_hold(b_1, media, hold, epout, epin, buffer, write, read)


def _stop_with_hold(b_1, media, hold, epout, epin, buffer, write, read):
	for i in range(2, 6):
		cadena = libhex.mov_comm(i)
		b_1 = countByte1(b_1)
		cadena = cadena.format(f_byte(b_1))
		cadena = fill_msg(cadena, 24)
		cadena += hold
		set_msg(cadena, epout, epin, buffer, write, read)
	# The idle messages keep the counts at the stop, as the vendor's do (LEDGER.md PL98).
	global ultimo_hold
	ultimo_hold = bytes.fromhex(hold)

	return [b_1, buffer, media]

# Builds one joint's setpoint field from a controller count (scorbot.state's signed_encoder_counts):
# the count as a signed 32-bit slot, over the 24-bit range the controller reports
# (docs/requirements/count-frame.md). Until 2026-11-04 it wrote the old decode's low word and sign
# word, one count low.
#
# valor -> Controller count, -0x7FFFFF..0x800000
def setpointField(valor):
	if valor < -0x7FFFFF or valor > 0x800000:
		raise ValueError("Setpoint out of range: {}".format(valor))
	return valor.to_bytes(4, 'little', signed=True).hex()

# Selects the standard structure for sending positions to the controller according to
# the received command.
#
# orden      -> Movement order; it distinguishes direction of movement
# signal_out -> String with motor information
# msg        -> Message string without the completed structure
#
def getStruct(orden, signal_out, msg):
	if orden == 4 or orden == 5:
		section = signal_out + msg[8:len(msg)]
	elif orden == 6 or orden == 7:
		section = msg[0:8] + signal_out + msg[16:len(msg)]
	elif orden == 8 or orden == 9:
		section = msg[0:16] + signal_out + msg[24:len(msg)]
	elif orden == 10 or orden == 11 or orden == 12 or orden == 13:
		section = msg[0:24] + signal_out + msg[40:len(msg)]
	elif orden == 14 or orden == 15:
		section = msg[0:40] + signal_out + msg[48:len(msg)]
	elif orden == 20:
		# From base on: three fields (base, shoulder, elbow) or five (a wrist stream adds both
		# wrist motors, where the wrist jog puts them); the rest stays the measured position.
		section = signal_out + msg[len(signal_out):len(msg)]

	return section

# Increments or decrements the encoder values in the write message according to the
# order. This function is used in the base, shoulder, and elbow motions in libcomm.
def builder(b_1, dato_in, i, ite, orden, vel, media, buffer, step=None):
	cadena = libhex.mov_comm(1)
	b_1 = countByte1(b_1)
	cadena = cadena.format(f_byte(b_1))
	cadena = fill_msg(cadena, 24)
	if orden == 5 or orden == 6 or orden == 9 or orden == 14:
		dato_in = suma(dato_in, i+1, vel, ite, step=step)
	else:
		dato_in = resta(dato_in, i+1, vel, ite, step=step)

	signal_out = countField(dato_in[0], dato_in[1])
	msg = get_encoder(buffer, media)
	cadena += getStruct(orden, signal_out, msg)

	return [b_1, cadena, signal_out, dato_in]

# Checks the error bytes in the buffer.
#
# buffer  -> Vector storing the most recent read data
# pos     -> Position of the byte to inspect
#
def getError(buffer, pos):
	x = transform([buffer[pos],buffer[pos+1]])
	if x >= 65500:
		x = abs(65535 - x)
	return x


# Function for inspecting write/read messages between the controller and the program.
#
# msg   -> Message to export
# setup -> Indicator of the source and destination of the message
#
def filter(msg, setup):
	result = []
	if(setup == "escritura"):
		result.append("Write")
		for i in range(64):
			str_hex = msg[2*i] + msg[2*i+1]
			result.append(int(str_hex, 16))
	else:
		result.append("Read")
		for i in range(len(msg)):
			result.append(msg[i])

	print(result)


# Applies the final message format before sending it.
#
# cadena    -> Pre-format message string
# epout     -> Controller output endpoint object
# epin      -> Controller input endpoint object
# buffer    -> Vector storing the most recent read data
# write      -> Delay before making a read request after a write
# read       -> Delay before starting the next message after a read request
#
def set_msg(cadena, epout, epin, buffer, write,read):
	cadena = fill_msg(cadena, MSG_LEN)
	#filter(cadena, 'write')
	x = bytes.fromhex(cadena)
	check(x, epout, epin, buffer, write,read)
	#filter(buffer, 'read') # Communicates with the controller by sending and receiving messages.
## NOTE: The sleeps are essential to allow the controller time to generate the
## correct response.
# If an error occurs, a warning message is triggered.

# x         -> Message to send in the correct format
# epout     -> Controller output endpoint object
# epin      -> Controller input endpoint object
# buffer    -> Vector storing the most recent read data
# write     -> Delay before making a read request after a write
# read      -> Delay before starting the next message after a read request
#
def check(x,epout,epin,buffer, write,read):
	try:
		epout.write(x, TIME_OUT_W)
		time.sleep(write)
	except Exception as exc:
		raise RuntimeError(f"USB write failed: {exc}") from exc
	try:
		size = epin.read(buffer, conf.readData('general','TIME_OUT_R'))
		if size != len(buffer):
			raise ValueError(f"expected {len(buffer)} bytes, got {size}")
		time.sleep(read)
	except Exception as exc:
		raise RuntimeError(f"USB read failed: {exc}") from exc
	watch_counts(buffer)


# Converts a pair of int values into a single equivalent int.
## NOTE: The encoder data is stored in reverse order and in pairs in the buffer, so it
## must be reversed before combining it and converting it to a single integer.
#
# vect_int -> Vector containing two integer data values
#
## Example
# We want the number 1 as the result, so the vector should contain:
# vect_int = [1, 0]
# str_hex = 0001
# return 1
###
def transform(vect_int):
	str_hex = format(vect_int[1], '02x')
	str_hex += format(vect_int[0], '02x')
	return int(str_hex, 16)


# Converts an integer into its 4-digit hexadecimal equivalent.
# If the hex value does not have the required length, zeros are appended on the right.
#
# dato -> Integer to convert to hexadecimal
#
def detrans(dato):
	str = format(dato, '04x')
	str_hex = str[2] + str[3] + str[0] + str[1]
	return str_hex


# Converts the sign value associated with each encoder to its hexadecimal equivalent.
## NOTE: The int and hex values of this transformation do not share a direct mathematical
## relationship because the sign occupies 2 bytes in the read value and 4 bytes in the
## write value.
#
# pos      -> Buffer position where the requested data is stored
# buffer   -> Vector storing the most recent read data
#
# signo == '0000' -> Indicates the encoder is at the minimum value or reached the
# maximum while adding
# signo == 'FFFF' -> Indicates the encoder is at the maximum value or reached the
# minimum while subtracting
#
def get_signo(pos, buffer):
	if buffer[pos] == 128:
		return '0000'
	elif buffer[pos] == 127:
		return 'ffff'
	else:
		raise ValueError(f'Signo fuera de rango: {buffer[pos]}')


# Performs the addition operation according to the provided arguments. Returns the
# result of the addition and its associated sign in a single vector.
#
# dato_in  -> Contains the value to increase in dato_in[0] and the associated sign in
#             dato_in[1]
# cont     -> Number of times the addition has been performed
# vel      -> Speed at which the summed value should increase
# ite      -> Total number of iterations to be performed
#
## NOTE: The pair is a low word and its sign word, the count countField writes. Only a crossing of
## zero moves it to the other sign word (docs/requirements/count-frame.md).
#
def suma(dato_in,cont,vel,ite,step=None):
	dato_in[0] += incremento(cont,vel,ite) if step is None else step
	# Crossing zero ('ffff' low 65535 is count 0, '0000' low 0 is count 1) moves the low word by
	# 65536, so the count goes on by exactly the step (countField). It was 65535, which skipped a count.
	# On the '0000' side the low word runs past 65535 (count 65537 and up): wrapping it there jumped
	# the target back 65536 counts.
	if dato_in[0] > 65535 and dato_in[1] == 'ffff':
		dato_in[0] -= 65536
		dato_in[1] = '0000'
	return dato_in


# Performs the subtraction operation according to the provided arguments. Returns the
# result of the subtraction and its associated sign in a single vector.
#
# dato_in  -> Contains the value to decrease in dato_in[0] and the associated sign in
#             dato_in[1]
# cont     -> Number of times the subtraction has been performed
# vel      -> Speed at which the subtracted value should decrease
# ite      -> Total number of iterations to be performed
#
## NOTE: As in suma, only a crossing of zero changes the sign word.
###
def resta(dato_in,cont,vel,ite,step=None):
	dato_in[0] -= incremento(cont,vel,ite) if step is None else step
	# Crossing zero moves the low word by 65536, as in suma; on the 'ffff' side it runs below 0.
	if dato_in[0] < 0 and dato_in[1] == '0000':
		dato_in[0] = 65536 + dato_in[0]
		dato_in[1] = 'ffff'
	return dato_in


# Performs the increments/decrements for the math operations according to the speed and
# remaining number of iterations.
#
# If the operation has been performed fewer than twelve times, a cumulative increase of
# 1/12 is applied on each iteration.
#
# If the operation has been performed 12 times and is still below the maximum iterations - 12,
# increments equal to the configured speed are applied.
#
# If only 12 iterations remain, the increment is reduced cumulatively to 1/12 of the speed.
#
# cont -> Number of times the subtraction has been performed
# vel  -> Speed at which the subtracted value should decrease
# ite  -> Total number of iterations to be performed
#
def incremento(cont,vel,ite):
	if cont < 12:
		inc = builtins.round((vel/12)*cont)
		if inc > vel:
			inc = vel
	elif(cont >= (ite-12)):
		inc = builtins.round((vel/12)*(ite-cont))
		if inc < 0:
			inc = 0
	else:
		inc = vel
	return inc

# Inverse kinematics calculation for the 3-DOF Scorbot arm.
#
# x   -> Position along the x axis
# y   -> Position along the y axis
# z   -> Position along the z axis
#
def cIn(x,y,z):
	l = conf.readData("general", "longitudes")
	d = conf.readData("general", "link-offset")
	a = conf.readData("general", "link-length")
	alpha = conf.readData("general", "link-twist-angle")

	distO = sqrt(pow(x,2) + pow(y,2) + pow(z-l[0],2))
	q1 = atan2(y, x)

	m1 = matrizT(d[0], q1, a[0], alpha[0])
	m2 = matrizT(d[1], 0, a[1], alpha[1])
	coff = m1*m2*[[0],[0],[0],[1]]

	arg_q3 = (pow(x-coff[1,1],2)+pow(y-coff[2,1],2)+pow(z-l[0],2)-pow(l[1],2)-pow(l[2],2))/(2*l[1]*l[2])

	if abs(arg_q3) > 1:
		return [-1,-1,-1]

	q3 = -acos(arg_q3)

	phi = atan2((z- l[0]),(sqrt(pow(x - coff[1,1],2)+pow(y-coff[2,1],2))))
	beta = atan2((l[2]* sin(q3)),(l[1]+l[2]*cos(q3)))
	q2 = phi - beta

	sol = array([q1,q2,q3]) * 180/pi
	return sol

# Transformation matrix for inverse kinematics.
#
# d     -> Distance along z between centroids
# tita  -> Angle between z axes
# a     -> Distance along x between centroids
# alpha -> Angle between x axes
#
def matrizT(d, tita, a, alfa):
	T = array([[cos(tita), -cos(alfa)*sin(tita), sin(alfa)*sin(tita), a*cos(tita)],
			   [sin(tita), cos(alfa)*cos(tita), -sin(alfa)*cos(tita), a*sin(tita)],
			   [0, sin(alfa), cos(alfa), d],
			   [0, 0, 0, 1]])
	return T

# Converts an angle to encoder values and vice versa according to the joint.
# Joint codes:
#  Base  : 1
#  Shoulder: 2
#  Elbow : 3
#  Pitch : 4
#  Roll  : 5
#
# Conversion codes:
#  0 -> Encoder to angle
#  1 -> Angle to encoder
#
# arti  -> Joint identifier
# con   -> Conversion identifier
# value -> Value to transform
#
# The controller's collision flag: IN byte 59, one bit per motor (0x01 base, 0x02 shoulder,
# 0x04 elbow, 0x18 the two wrist motors, 0x20 gripper; ULPGC thesis p. 57). A jog stops only on
# the bits of the motors it moves: the gripper's move ends against a stop, so its bit may stay set.
COLLISION_BYTE = 59

# USBC.dll's impact check (docs/protocol/LEDGER.md PL98): each reply's error word, read as a
# signed 16-bit value, faults with error 201 when its absolute value is strictly above the
# axis's ImpactDetect (ER4AxN.ini; scorbot.state.IMPACT_DETECT_COUNTS, which a test keeps equal).
# [base, shoulder, elbow, wrist motor 1, wrist motor 2, gripper]
IMPACT_DETECT = [70, 70, 70, 70, 70, 300]

# The error word at `pos` as a signed 16-bit value (the DLL sign-extends it, MOVSX at 0x1003db4d).
# getError, which legacy homing still uses, reads 0xFFBA as 65466.
def getErrorWord(buffer, pos):
	x = transform([buffer[pos],buffer[pos+1]])
	return x - 0x10000 if x >= 0x8000 else x

# Whether a signed error word is an impact for motor `motor` (index as IMPACT_DETECT).
def impacto(valor, motor):
	return abs(valor) > IMPACT_DETECT[motor]


def conversorAngEnc(arti, conv, value):
	if arti == 1:
		if conv == 0:
			x = (20*value)/2837
		elif conv == 1:
			x = (value*2837)/20
			if x < 0:
				x = 65535 - abs(x)
	elif arti == 2:
		if  conv == 0:
			x = (20*value)/2300
		elif conv == 1:
			x = (2300*value)/20
			if x < 0:
				x = 65535 - abs(x)
	elif arti == 3:
		if conv == 0:
			x = (20*value)/2252
		elif conv == 1:
			x = (2252*value)/20
			if x < 0:
				x = 65535 - abs(x)
	elif arti == 4:
		if conv == 0:
			x = (90*value)/2511
		elif conv == 1:
			x = (2511*value)/90
			if x < 0:
				x = 65535 - abs(x)
	elif arti == 5:
		if conv == 0:
			x = (20*value)/558
		elif conv == 1:
			x = (558*value)/20
			if x < 0:
				x = 65535 - abs(x)

	return round(x,2)

# Calculates the number of iterations based on the speed and the distance to travel.
#
# enc  -> Distance to travel
# vel  -> Motion speed
#
def numIte(enc, vel):
	ite = 0
	enc = abs(enc)
	for i in range(12):
		if enc >= vel/12:
			enc -= vel/12
			ite += 1
		elif enc < 12 and enc >=0:
			ite += 1
			break
		else:
			break

	while enc >= vel/12:
		enc -= vel
		ite += 1

	for i in range(12):
		if enc >= vel/12:
			enc -= vel/12
			ite += 1
		elif enc < 12 and enc >=0:
			ite += 1
			break
		else:
			break


	return ite


###############################################################################
# Graphical interface functions
###############################################################################

# Reads the encoder values and displays them in the graphical window.
#
# self   -> Pointer to the graphical interface
# buffer -> Vector storing the most recent read data
#
def encoder(self, buffer):
	e1 = str(transform([buffer[19],buffer[20]]))
	e2 = str(transform([buffer[24],buffer[25]]))
	e3 = str(transform([buffer[29],buffer[30]]))
	e4 = str(transform([buffer[34],buffer[35]]))
	e5 = str(transform([buffer[39],buffer[40]]))
	e6 = str(transform([buffer[44],buffer[45]]))
	self.label_5.setText(e1)
	self.label_5.repaint()
	self.label_6.setText(e2)
	self.label_6.repaint()
	self.label_10.setText(e3)
	self.label_10.repaint()
	self.label_12.setText(e4)
	self.label_12.repaint()
	self.label_14.setText(e5)
	self.label_14.repaint()
	self.label_16.setText(e6)
	self.label_16.repaint()


# Enables or disables the buttons depending on whether the motors are ON or OFF.
#
# self   -> Pointer to the graphical interface
# state  -> Enables or disables GUI buttons
#
def stateButtons(self, state):
	self.hip_left.setEnabled(state)
	self.hip_right.setEnabled(state)
	self.shoulder_up.setEnabled(state)
	self.shoulder_down.setEnabled(state)
	self.elbow_up.setEnabled(state)
	self.elbow_down.setEnabled(state)
	self.pitch_up.setEnabled(state)
	self.pitch_down.setEnabled(state)
	self.roll_left.setEnabled(state)
	self.roll_right.setEnabled(state)
	self.open_clamp.setEnabled(state)
	self.close_clamp.setEnabled(state)
	self.pushbutton_home.setEnabled(state)
	self.pushButton_go.setEnabled(state)

# Writes the robot's functional state to the GUI.
#
# self   -> Pointer to the graphical interface
# type   -> Indicates whether this is an error or end-of-execution message
# msg    -> Message to display in the graphical interface
#
def send_textlabel(self, type, msg):
	if type == 0:
		self.mainLabel.setText(info_text(msg))
		self.mainLabel.repaint()
	else:
		self.mainLabel.setText(error_msg(msg))
		self.mainLabel.repaint()


# Sends the power-on or power-off motor command to the controller.
#
# self       -> Pointer to the graphical interface
# orden      -> Command to execute
# cola_orden -> Queue with the command to process and/or feedback about runtime status
# buffer     -> Vector storing the most recent read data
def stateMotors(self, orden, cola_orden, buffer):
	if orden == 16:
		self.pushbutton_online.setStyleSheet("background-color: rgb(240,240,240)")
		self.pushbutton_offline.setStyleSheet("background-color: red")
		stateButtons(self, False)
	elif orden == 17:
		self.pushbutton_online.setStyleSheet("background-color: rgb(0, 255, 0)")
		self.pushbutton_offline.setStyleSheet("background-color: rgb(240,240,240)")
		stateButtons(self, True)

	write_data(self, orden, cola_orden, buffer)

# Collects user commands and forwards them to thread 2 of the program.
#
# self       -> Pointer to the graphical interface
# orden      -> Command to execute
# cola_orden -> Queue with the command to process and/or feedback about runtime status
# buffer     -> Vector storing the most recent read data
#
def write_data(self,orden, cola_orden, buffer):
	print(orden)
	select = []
	# Action for the 'exit' instruction
	if orden == 'exit':
		print("Closing connections")
		logging.info(info_text(2))
		send_textlabel(self, 0, 2)
		select.append(EXIT)
		select.append(int(self.spinBox.value()))
		select.append(int(self.spinBox_2.value()))
		cola_orden.put(select)
		online = False
		time.sleep(SLEEP)
	else:
		select.append(int(orden))
		if select[0] != 19:
			select.append(int(self.spinBox.value()))
			select.append(int(self.spinBox_2.value()))
	    # Action to disable the motors
		if select[0] == 16:
			cola_orden.put(select)
			time.sleep(SLEEP)
			cola_orden.get()
			print("Motors off")
			logging.info(info_text(3))
			send_textlabel(self,0,3)
	    # Action to enable the motors
		elif select[0] == 17:
			cola_orden.put(select)
			time.sleep(SLEEP)
			cola_orden.get()
			print("Motors on")
			logging.info(info_text(4))
			send_textlabel(self,0,4)

		# Action to perform homing
		elif select[0] == 18:
			print("Homing...")
			logging.info(info_text(5))
			send_textlabel(self,0,5)

		# Action to move to an XYZ point.
		elif select[0] == 19:
			posObj = []
			posObj.append(self.lineEdit.text())
			posObj.append(self.lineEdit_2.text())
			posObj.append(self.lineEdit_3.text())
			print(posObj)
			select.append(posObj)
			select.append(int(self.spinBox.value()))
			print(select)

        # Moves the base
		elif select[0] == 4 or select[0] == 5:
			print("Moving base...")
			logging.info(info_text(6))
			send_textlabel(self,0,6)

        # Moves the shoulder
		elif select[0] == 6 or select[0] == 7:
			print("Moving shoulder...")
			logging.info(info_text(7))
			send_textlabel(self, 0, 7)

        # Moves the elbow
		elif select[0] == 8 or select[0] == 9:
			print("Moving elbow...")
			logging.info(info_text(8))
			send_textlabel(self, 0, 8)

        # Moves the wrist pitch joint
		elif select[0] == 10 or select[0] == 11:
			print("Moving wrist pitch...")
			logging.info(info_text(9))
			send_textlabel(self, 0 , 9)

        # Moves the wrist roll joint
		elif select[0] == 12 or select[0] == 13:
			print("Moving wrist roll...")
			logging.info(info_text(10))
			send_textlabel(self, 0, 10)

        # Opens the gripper
		elif select[0] == 14:
			print("Opening gripper...")
			logging.info(info_text(11))
			send_textlabel(self,0,11)

		# Closes the gripper
		elif select[0] == 15:
			print("Closing gripper...")
			logging.info(info_text(12))
			send_textlabel(self,0,12)

		control_error(self, select, cola_orden, buffer)


# Error handling during execution and completion of the received command.
# It uses the order queue to send the command to the command thread. It then reads
# result to check whether the operation was completed successfully (DONE), or otherwise
# which type of error occurred.
#
# self       -> Pointer to the graphical interface
# select     -> Vector containing all parameters required to execute the order
# cola_orden -> Queue with the command to process and/or feedback about runtime status
# buffer     -> Vector storing the most recent read data
#
def control_error(self, select, cola_orden, buffer):
	if select[0] != 16 and select[0] != 17: # Ignore motor enable/disable actions.
		cola_orden.put(select)
		time.sleep(SLEEP)
		result = cola_orden.get()
		if result == DONE and select[0] != 18 and select[0] != 19:
			print("Motion complete")
			print("")
			logging.info(info_text(1)) # Motion finished
			send_textlabel(self,0,1)
		elif result == DONE and select[0] == 18:
			send_textlabel(self, 0 ,13)
			global posRef
			for i in range(len(VEC_POS)):
				posRef.append(transform([buffer[VEC_POS[i]], buffer[VEC_POS[i]+1]]))
			print("Updated joints after homing")
			print(posRef)
			joint(self, buffer)
		elif result == DONE and select[0] == 19:
			send_textlabel(self,0,1)
			logging.info(info_text(1)) # Motion finished
			print("Updated joints after motion")
			joint(self, buffer)

		else:
			# Recoge el error que se ha dado y muestra por GIU el mensaje correspondiente-
			cola_orden.get()
			if result == 1 or result == 2:
				send_textlabel(self,1,11)
			elif result == 3:
				send_textlabel(self,1,3)
			elif result == 4:
				send_textlabel(self,1,4)
			elif result == 5:
				send_textlabel(self,1,5)
			elif result == 6:
				send_textlabel(self,1,6)
			elif result == 7:
				send_textlabel(self,1,7)
			elif result == 8:
				send_textlabel(self,1,8)
			elif result == 9:
				send_textlabel(self,1,9)
			elif result == 10:
				send_textlabel(self,1,10)
			else:
				self.mainLabel.setText('')

		self.mainLabel.repaint()
	# Actualiza el valor de los encoders en la ventana gráfica
	encoder(self, buffer)


# Calculates the joint angles and displays them in the interface.
#
# self       -> Pointer to the graphical interface
# buffer     -> Vector storing the most recent read data
#
def joint(self, buffer):
	global posRef
	global angRef

	print(f'posRef: {posRef}')
	print(f'angRef: {angRef}')

	vector = []
	for i in range(len(VEC_POS)-1):
		vector.append(transform([buffer[VEC_POS[i]], buffer[VEC_POS[i]+1]]))
	print(f'vector: {vector}')
	r = []
	# Se calcula el incremento en funcion de las diferentes zonas de las posiciones
	# de referencia y las posiciones objetivo
	for i in range(len(vector)):
		if posRef[i] >= 0 and posRef[i] <= LIM_INF:
			if vector[i] >= 0 and vector[i] <= LIM_INF:
				inc = vector[i] - posRef[i]
			elif vector[i] <= 65535 and vector[i] >= LIM_SUP:
				inc = -(65535 - vector[i] + posRef[i])
		elif posRef[i] <= 65535 and posRef[i] >= LIM_SUP:
			if vector[i] <= 65535 and vector[i] >= LIM_SUP:
				inc = vector[i] - posRef[i]
			elif vector[i] >= 0 and vector[i] <= LIM_INF:
				inc = 65535 - posRef[i] + vector[i]

		posRef[i] = vector[i]
		# Si el incremento es negativo debemos sumar el resultado al angulo de
		# referencia y viceversa.
		# A excepción de la articulación hombro (i = 1) que lleva logica inversa
		if inc < 0:
			if i == 1:
				ang = angRef[i] - conversorAngEnc(i+1, 0, abs(inc))
			else:
				ang = angRef[i] + conversorAngEnc(i+1, 0, abs(inc))
		else:
			if i == 1:
				ang = angRef[i] + conversorAngEnc(i+1, 0, inc)
			else:
				ang = angRef[i] - conversorAngEnc(i+1, 0, inc)

		# En caso de que el angulo resultado sea sobrepase los +- 360º se ajusta
		# para que el resultado sea entre este rango.
		if ang >= 360:
			ang -= 360
		elif ang <= -360:
			ang += 360

		angRef[i] = ang
		r.append(str(round(ang,2)))

	# Muestra por interfaz los angulos resultantes de los joints.
	self.joint_base.setText(r[0])
	self.joint_base.repaint()
	self.joint_shoulder.setText(r[1])
	self.joint_shoulder.repaint()
	self.joint_elbow.setText(r[2])
	self.joint_elbow.repaint()
	self.joint_pitch.setText(r[3])
	self.joint_pitch.repaint()
	self.joint_roll.setText(r[4])
	self.joint_roll.repaint()
