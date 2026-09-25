##############################################
# Library with standard controller communication message structures.
# A specific message is selected numerically in each helper module.
##############################################

### Messages for the first stage of the initial synchronization
###
def get_msg1(x):
	switcher = {
		1: '5A',
		2: '4FFF54',
		3: '73FF',
		4: '42FF',
	}
	return switcher.get(x, "Invalid request")

### Messages for the second stage of the initial synchronization
###
def get_msg2(select):
	switcher = {
		1: '000800',
		2: 'B0AD01',
		3: 'B0710B',
		4: 'E02E00',
		5: '000000',
		6: 'E80300',
		7: '000000',
		8: '010000',
		9: '460000',
		10: '00F000',
		11: '000800',
		12: 'C0D401',
		13: '804F12',
		14: 'E02E00',
		15: '000000',
		16: '007D00',
		17: '000000',
		18: '010000',
		19: '460000',
		20: '00F000',
		21: '000800',
		22: 'C0D401',
		23: '804F12',
		24: 'E02E00',
		25: '000000',
		26: 'E80300',
		27: '000000',
		28: '010000',
		29: '460000',
		30: '00F000',
		31: '000800',
		32: 'C0D401',
		33: '804F12',
		34: 'E02E00',
		35: '000000',
		36: 'E80300',
		37: '000000',
		38: '010000',
		39: '460000',
		40: '00F000',
		41: '000800',
		42: 'C0D401',
		43: '804F12',
		44: 'E02E00',
		45: '000000',
		46: 'E80300',
		47: '000000',
		48: '010000',
		49: '460000',
		50: '001800',
		51: '140000',
		52: 'A08601',
		53: '00350C',
		54: '102700',
		55: 'F40100',
		56: '007D00',
		57: '000000',
		58: '010000',
		59: '2C0100',
		60: '00F000',
		61: '000800',
		62: '983A00',
		63: '102700',
		64: 'E80300',
		65: '000000',
		66: 'E80300',
		67: '000000',
		68: '010000',
		69: '40420F',
		70: '00F000',
		71: '000800',
		72: '983A00',
		73: '102700',
		74: 'E80300',
		75: '000000',
		76: 'E80300',
		77: '000000',
		78: '010000',
		79: '40420F',
		80: '{0}000000{1}',
		81: '{0}0000005300080010',
		82: '{0}0000005301000000F0',
		83: '{0}00000053{1}{2}00',
		84: '{0}000C000D',
		}
	return switcher.get(select, "Invalid request")

### Messages for the third stage of the initial synchronization
###
def motorson(x):
	switcher = {
		1: '0D',
		2: '47',
		3: '0D',
		4: '4FFF53',
		5: '42FF01',
		6: '7320',
		7: '4220',
	}
	return switcher.get(x, "Invalid request")

### Messages used to disconnect the motors
###
def motorsoff(x):
	switcher = {
		1: '{0}0000000D',
		2: '{0}00000047',
		3: '{0}00000073FF',
		4: '{0}00000042FF',
		5: '{0}0000004FFF53',
		6: '{0}00000073FF',
		7: '{0}0000007320',
		8: '{0}0000004220',
		9: '{0}00000064',
		10: '{0}00000064',
		11: '{0}00000064',
		12: '{0}00000061',
		13: '{0}00000061',
		14: '{0}00000061',
		15: '{0}00000064',
		16: '{0}00000064',
		17: '{0}00000064',
		18: '{0}00000061',
		19: '{0}00000061',
		20: '{0}00000061',
		21: '{0}00000064',
		22: '{0}00000064',
		23: '{0}00000064',
		24: '{0}00000061',
		25: '{0}00000061',
		26: '{0}00000061',
	}
	return switcher.get(x, "Invalid request")

### Messages used to disconnect the controller from the program and close the
### synchronization
###
def get_scorbotoff(x):
	switcher = {
		1: '47',
		2: '4FFF53',
		3: '7320',
		4: '4220',
		5: '64',
		6: '64',
		7: '64',
		8: '61',
		9: '61',
		10: '61',
		11: '61',
	}
	return switcher.get(x, "Invalid request")

### Messages used for gripper movements
###
def clamp(cont):
	switcher = {
		1: '{0}0000004F2053',
		2: '{0}0000004C2000',
		3: '{0}000000422001',
		4: '{0}0000007320',
		5: '{0}0000004220',
		}
	return switcher.get(cont, "Invalid request")

### Messages used in different parts of the code and shared across modules
###
def mov_comm(cont):
	switcher = {
		1: '{0}0000000D',
		2: '{0}00000047',
		3: '{0}0000004F3F530000000000',
		4: '{0}0000007320000000000000',
		5: '{0}0000004220000000000000',
		6: '{0}000000',
		}
	return switcher.get(cont, "Invalid request")
