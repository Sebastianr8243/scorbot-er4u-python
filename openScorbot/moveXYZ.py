# Autores: Jose Luis Pérez Pérez y Yolanda M. Gimeno Rodríguez
# Fecha:
# Título: Generacion de trayectorias
# Universidad de La Laguna

import libdef
import conf
import libhex
import log
import logging

################################################################################
# Script que realiza movimientos complejos del robot generando trayectorias
# punto a punto con movimiento simultáneo de los ejes
################################################################################

# Tiempo de espera para realizar una peticion de lectura tras la escritura
WRITE = conf.readData("cadera", "write")
# Tiempo de espera para empezar a generar el siguiente mensaje a enviar tras
# una  peticion de lectura.
READ = conf.readData("cadera", "read")
# Minimo valor de la franja superior de valores posibles
UP_LIMIT = conf.readData("general", "upLimit")
# Maximo valor de la franja inferior de valores posibles
DOWN_LIMIT = conf.readData("general", "downLimit")


# Funcion con la que se pasa de un punto [x,y,z] a los valores de encoder equivalentes
# para alcanzar dicho punto. Funciona como manager de la generacion y ejecucion de
# trayectorias
#
# posObj      -> Vector con los valores x,y,z del punto objetivo
# vel         -> Velocidad a la que realiza el movimiento
# posRef      -> Posicion en valores de encoder de la ultima posicion alcanzada
# b_1         -> Byte de secuencia
# epout       -> Objeto de endpoint de salida de la controladora
# epin        -> Objeto de enpoint de entrada de la controladora
# buffer      -> Vector que almacena los datos de la ultima lectura
# cola_read   -> Cola con el vector media de la posición de encoders
# cola_orden  -> Cola con la orden a procesar y/o feedback del funcionamiento
#
def controlXYZ(posObj, vel, posRef, b_1, epout, epin, buffer, cola_read, cola_orden):
    block = False
    dirRef = False #True, es incremento de ang y False es drecemento de angulo
    media = cola_read.get()
    sentido = []
    ite = []

    try:
        [x,y,z] = [int(posObj[0]),int(posObj[1]),int(posObj[2])]
    except TypeError:
        cola_orden.put(9)  #Error al introducir casillas vacias
        cola_read.put(media)
        logging.error(libdef.error_msg(9), exc_info = True)
        return [b_1, posRef]
    except ValueError:
        cola_orden.put(10) #Error al introducir valores no enteros
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
            logging.warning(libdef.error_msg(4)) #Angulo incalculable
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


# Funcion para construir el mensaje de movimiento compuesto. Sigue la misma logica
# que las funciones de movimiento simple
#
# b_1         -> Byte de secuencia
# ite         -> Numero de veces que debe realizarse el incremento de posiciones de encoder
# sentido     -> Direccion de giro de la articulacion
# vel         -> Velocidad a la que realiza el movimiento
# buffer      -> Vector que almacena los datos de la ultima lectura
# media       -> Vector de ajuste de las posiciones de los encoders
# epout       -> Objeto de endpoint de salida de la controladora
# epin        -> Objeto de enpoint de entrada de la controladora
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
        cadena += signal_out + msg[24:len(msg)] #cambia segun articulacion
        libdef.set_msg(cadena, epout, epin, buffer, WRITE, READ)
        media = libdef.get_media(buffer, media)
        cont += 1

    [b_1, buffer, media] = libdef.closeMov(b_1, media, 20, signal_out, epout, epin, buffer, WRITE, READ)
    return [b_1,media]
