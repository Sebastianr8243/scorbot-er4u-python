# Authors: Jose Luis Pérez Pérez and Yolanda M. Gimeno Rodríguez
# Date:
# Title: Trajectory generation
# University of La Laguna

import libdef
import conf
import libhex
import log
import logging

################################################################################
# Script that performs complex robot motions by generating point-to-point
# trajectories with simultaneous joint motion.
################################################################################

# Delay before making a read request after a write
WRITE = conf.readData("cadera", "write")
# Delay before starting the next message after a read request
READ = conf.readData("cadera", "read")
# Minimum value of the upper valid range
UP_LIMIT = conf.readData("general", "upLimit")
# Maximum value of the lower valid range
DOWN_LIMIT = conf.readData("general", "downLimit")


# Function that converts a point [x,y,z] into the equivalent encoder values needed
# to reach it. It acts as the manager for trajectory generation and execution.
#
# posObj      -> Vector with the target point values x,y,z
# vel         -> Speed at which the movement is performed
# posRef      -> Position in encoder values of the last reached point
# b_1         -> Sequence byte
# epout       -> Controller output endpoint object
# epin        -> Controller input endpoint object
# buffer      -> Vector storing the most recent read data
# cola_read   -> Queue with the averaged encoder position vector
# cola_orden  -> Queue with the command to process and/or runtime feedback
#
def controlXYZ(posObj, vel, posRef, b_1, epout, epin, buffer, cola_read, cola_orden):
    block = False
    dirRef = False # True means angle increase and False means angle decrease
    media = cola_read.get()
    sentido = []
    ite = []

    try:
        [x,y,z] = [int(posObj[0]),int(posObj[1]),int(posObj[2])]
    except TypeError:
        cola_orden.put(9)  # Error when empty fields are submitted
        cola_read.put(media)
        logging.error(libdef.error_msg(9), exc_info = True)
        return [b_1, posRef]
    except ValueError:
        cola_orden.put(10) # Error when non-integer values are entered
        cola_read.put(media)
        logging.error(libdef.error_msg(10), exc_info = True)
        return [b_1, posRef]

    posObj = libdef.cIn(x,y,z)

    if posObj[0] > 90 or posObj[0] < -90:
        logging.warning(libdef.error_msg(6))
        cola_orden.put(6)
        cola_read.put(media)
        return [b_1, posRef]

    elif posObj[1] > 100 or posObj[1] < 15:
        logging.warning(libdef.error_msg(7))
        cola_orden.put(7)
        cola_read.put(media)
        return [b_1, posRef]

    elif posObj[2] > -30 or posObj[2] < -120:
        logging.warning(libdef.error_msg(8))
        cola_orden.put(8)
        cola_read.put(media)
        return [b_1, posRef]

    for i in range(3):
        print(f'Target angle: {posObj}')
        print(f'Reference position {i}: {posRef[i]}')
        if posObj[i] == -1:
            block = True
            cola_orden.put(4)
            logging.warning(libdef.error_msg(4)) # Uncalculable angle
            break

        posInc = posRef[i]
        obj = round(int(round(libdef.conversorAngEnc(i+1, 1, posObj[i]))))

        if obj > 65535:
            obj = abs(obj) - 65535

        if (obj > DOWN_LIMIT and obj < UP_LIMIT) or obj < 0:
            print("Target out of reach")
            logging.warning(libdef.error_msg(3))
            block = True
            cola_orden.put(3)
            break

        elif (posInc < DOWN_LIMIT and obj < DOWN_LIMIT) or (posInc > UP_LIMIT and obj > UP_LIMIT):
            inc = obj - posInc
            if inc < 0:
                dirRef = False
                if i == 1:
                    sentido.append(2)
                else:
                    sentido.append(1)
                inc = abs(inc)
            else:
                dirRef = True
                if i == 1:
                    sentido.append(1)
                else:
                    sentido.append(2)

        elif posInc > UP_LIMIT and obj < DOWN_LIMIT:
            inc = 65535 - posInc + obj
            dirRef = True
            if i == 1:
                sentido.append(1)
            else:
                sentido.append(2)

        elif posInc < DOWN_LIMIT and obj > UP_LIMIT:
            dirRef = False
            inc = 65535 - obj + posInc
            if i == 1:
                sentido.append(2)
            else:
                sentido.append(1)

        print(f'Initial position: {posInc}')
        print(f'Target position: {obj}')

        ite.append(libdef.numIte(inc, vel))
        if dirRef == True:
            posRef[i] += inc
        else:
            posRef[i] -= inc

        if posRef[i] < 0:
            posRef[i] += 65535
        elif posRef[i] > 65535:
            posRef[i] -= 65535

        print(f'Position increment {i}: {inc}')
        print(f'New reference position {i}: {posRef[i]}')
        print('\n')

    if block != True:
        [b_1,media] = move(b_1, ite, sentido, vel, buffer, media, epout, epin)
    else:
        cola_read.put(media)
        return [b_1, posRef]

    cola_read.put(media)
    return [b_1, posRef]


# Function used to build the compound motion message. It follows the same logic as
# the simple movement functions.
#
# b_1         -> Sequence byte
# ite         -> Number of times the encoder position increment must be applied
# sentido     -> Rotation direction of the joint
# vel         -> Speed at which the movement is performed
# buffer      -> Vector storing the most recent read data
# media       -> Encoder position adjustment vector
# epout       -> Controller output endpoint object
# epin        -> Controller input endpoint object
#
def move(b_1, ite, sentido, vel, buffer, media, epout, epin):
    [b_1, buffer, media] = libdef.openMov(b_1, media , epout, epin, buffer, WRITE, READ)

    cont = 0
    step_in = [media[0], media[1], media[2]]
    signo = [libdef.get_signo(21, buffer), libdef.get_signo(26, buffer), libdef.get_signo(31, buffer)]
    signal_out = ''
    while cont <= max(ite):
        signal_out = ''
        cadena = libhex.mov_comm(1)
        b_1 = libdef.countByte1(b_1)
        cadena = cadena.format(libdef.f_byte(b_1))
        cadena = libdef.fill_msg(cadena, 24)
        for i in range(3):
            if cont <= ite[i]:
                if sentido[i] == 1:
                    [step_in[i], signo[i]] = libdef.suma([step_in[i], signo[i]], cont+1, vel, ite[i])
                elif sentido[i] == 2:
                    [step_in[i], signo[i]] = libdef.resta([step_in[i], signo[i]], cont+1, vel, ite[i])

            signal_out  += libdef.detrans(step_in[i])
            signal_out  += signo[i]

        msg = libdef.get_encoder(buffer, media)
        cadena += signal_out + msg[24:len(msg)]
        libdef.set_msg(cadena, epout, epin, buffer, WRITE, READ)

        media = libdef.get_media(buffer,media)
        cont += 1

    cont = 0
    while abs(step_in[0] - media[0]) > 20 or abs(step_in[1] - media[1]) > 20 or abs(step_in[2] - media[2]) > 20:
        cadena = libhex.mov_comm(1)
        b_1 = libdef.countByte1(b_1)
        cadena = cadena.format(libdef.f_byte(b_1))
        cadena = libdef.fill_msg(cadena, 24)
        msg = libdef.get_encoder(buffer, media)
        cadena += signal_out + msg[24:len(msg)] # varies by joint
        libdef.set_msg(cadena, epout, epin, buffer, WRITE, READ)
        media = libdef.get_media(buffer, media)
        cont += 1

    [b_1, buffer, media] = libdef.closeMov(b_1, media, 20, signal_out, epout, epin, buffer, WRITE, READ)
    return [b_1,media]
