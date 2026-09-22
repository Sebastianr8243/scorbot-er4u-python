# Autores: Jose Luis Pérez Pérez y Yolanda M. Gimeno Rodríguez
# Fecha:
# Título: HOME
# Universidad de La Laguna

import libdef
import conf
import libhex
import time
import log
import logging

################################################################################
# Script para realizar el HOME del robot. La posicion fisica inicial de las
# articulaciones debe ser el correcto. La version del HOME no contempla la
# realizacion del HOME desde cualquier posicion.
################################################################################


# Posiciones de los datos de cada motor dentro del buffer
#   [cadera, hombro, codo, m1_muñeca, m2_muñeca, pinza]
VEC_POS   = conf.readData("general","VEC_POS")
# Posiciones de los bytes de error de cada motor dentro del buffer
#   [cadera, hombro, codo, m1_muñeca, m2_muñeca, pinza]
VEC_ERROR = conf.readData("general", "VEC_ERROR")
# Valor maximo del byte de secuencia
MAX_COUNT = conf.readData("general","MAX_COUNT")
# Error maximo aceptable en los bytes de error
MAX_ERROR = conf.readData("general", "MAX_ERROR")
# Valor asociado al microinterruptor activo de la cadera
SW_HIP = conf.readData("cadera", "switch")
# Valor asociado al microinterruptor activo del hombro
SW_SHOULDER = conf.readData("hombro", "switch")
# Valor asociado al microinterruptor activo del codo
SW_ELBOW = conf.readData("codo", "switch")
# Valor asociado al microinterruptor activo del movimiento de pitch
SW_PITCH = conf.readData("wrist", "switch_pitch")
# Valor asociado al microinterruptor activo del movimiento de roll
SW_ROLL = conf.readData("wrist", "switch_roll")
# Tiempo de espera para realizar una peticion de lectura tras la escritura
WRITE = conf.readData("general", "WRITE")
# Tiempo de espera para empezar a generar el siguiente mensaje a enviar tras
# una  peticion de lectura.
READ = conf.readData("general", "READ")

# Provisional upper bound on each switch search. Verify against the actual arm.
HOME_SEARCH_TIMEOUT_S = 30.0


def search_must_stop(deadline, cancel_event, result_queue):
    if cancel_event is not None and cancel_event.is_set():
        result_queue.put(2)
        logging.error("Homing cancelled")
        return True
    if time.monotonic() >= deadline:
        result_queue.put(2)
        logging.error("Homing switch search timed out")
        return True
    return False


#to do: el error no salta en el momento correcto

# Secuencia de realizacion del home. Los movimientos se realizan una a continuacion
# del otro siguiendo siempre el mismo orden. En caso de que una articulacion este
# inicialmente en su posicion de HOME, se pasa a la siguiente articulacion.
#
# Secuencia de movimientos:
#           [hombro -> codo -> pitch -> roll -> cadera]
#
# b_1         -> Byte de secuencia
# epout       -> Objeto de endpoint de salida de la controladora
# epin        -> Objeto de enpoint de entrada de la controladora
# buffer      -> Vector que almacena los datos de la ultima lectura
# cola_read   -> Cola con el vector media de la posición de encoders
# cola_orden  -> Cola con la orden a procesar y/o feedback del funcionamiento
#
def homing(b_1, epout, epin, buffer, cola_read, cola_orden, cancel_event=None):
    status = 0
    block = False
    sw = libdef.get_switch(SW_SHOULDER, buffer[5])
    media = cola_read.get()
    signal_out = ''
    logging.info(libdef.info_text(5))
    #Comprueba hombro en su sitio
    if sw == True:
        sw = True
    else:
        #muevo el hombro
        write = conf.readData("hombro","write")
        vel = conf.readData("hombro","h_vel")
        read = conf.readData("hombro","read")
        cadena = libhex.mov_comm(2)
        b_1 = libdef.countByte1(b_1)
        cadena = cadena.format(libdef.f_byte(b_1))
        cadena = libdef.fill_msg(cadena, 24)
        media = libdef.get_media(buffer,media)
        cadena += libdef.get_encoder(buffer, media)
        libdef.set_msg(cadena, epout, epin, buffer, write, read)

        media = libdef.get_media(buffer,media)
        step_in = media[1]
        signo = libdef.get_signo(26, buffer)
        dato_in = [step_in, signo]
        cont_vel = 0
        deadline = time.monotonic() + HOME_SEARCH_TIMEOUT_S
        while(sw == False):
            if search_must_stop(deadline, cancel_event, cola_orden):
                block = True
                break
            [b_1, cadena, signal_out, dato_in] = libdef.builder(b_1, dato_in, cont_vel-1, 100, 6, vel, media, buffer)
            libdef.set_msg(cadena, epout, epin, buffer, write, read)
            media = libdef.get_media(buffer,media)

            sw = libdef.get_switch(SW_SHOULDER, buffer[5])

            value_err = libdef.getError(buffer, VEC_ERROR[1])
            if value_err >= MAX_ERROR:
                block = True
                print("Joint limit reached")
                cola_orden.put(1)
                logging.warning(libdef.error_msg(1))
                break

            if cont_vel < 12:
                cont_vel += 1

        if block == True:
            cola_read.put(media)
            status = 1
            return [b_1, status]

        #Realiza la frenada controlada
        cont_vel = 88
        for i in range(12):
            [b_1, cadena, signal_out, dato_in] = libdef.builder(b_1, dato_in, cont_vel-1, 100, 6, vel, media, buffer)
            libdef.set_msg(cadena, epout, epin, buffer, write, read)
            media = libdef.get_media(buffer,media)

        cont = 0
        while abs(dato_in[0] - media[1]) > 20:
            cadena = libhex.mov_comm(1)
            b_1 = libdef.countByte1(b_1)
            cadena = cadena. format(libdef.f_byte(b_1))
            cadena = libdef. fill_msg(cadena, 24)
            msg = libdef.get_encoder(buffer, media)
            cadena += libdef.getStruct(6, signal_out, msg)
            libdef.set_msg(cadena, epout, epin, buffer, write, read)
            media = libdef.get_media(buffer, media)
            cont += 1
            if cont == 100:
                print("ERROR: Joint did not respond")
                cola_orden.put(2)
                logging.warning(libdef.error_msg(2))
                block = True
                break


        [b_1, buffer, media] = libdef.closeMov(b_1, media, 6, signal_out, epout, epin, buffer, write, read)

    logging.info(libdef.info_text(18))

    if block == True:
        cola_read.put(media)
        status = 1
        return [b_1, status]

    #transicion entre articulaciones
    for i in range(20):
        cadena = libhex.mov_comm(1)
        b_1 = libdef.countByte1(b_1)
        cadena = cadena.format(libdef.f_byte(b_1))
        cadena = libdef.fill_msg(cadena, 24)
        msg = libdef.get_encoder(buffer, media)
        cadena += msg
        libdef.set_msg(cadena, epout, epin, buffer, WRITE, READ)
        media = libdef.get_media(buffer, media)

    #Compruebo codo
    sw = libdef.get_switch(SW_ELBOW, buffer[5])
    if sw == True:
        sw = True
    else:
        #muevo el codo
        write = conf.readData("codo","write")
        vel = conf.readData("codo","h_vel")
        read = conf.readData("codo","read")
        cadena = libhex.mov_comm(2)
        b_1 = libdef.countByte1(b_1)
        cadena = cadena.format(libdef.f_byte(b_1))
        cadena = libdef.fill_msg(cadena, 24)
        media = libdef.get_media(buffer,media)
        cadena += libdef.get_encoder(buffer, media)
        libdef.set_msg(cadena, epout, epin, buffer, write, read)

        media = libdef.get_media(buffer,media)
        step_in = media[2]
        signo = libdef.get_signo(31, buffer)
        dato_in = [step_in, signo]
        cont_vel = 0
        deadline = time.monotonic() + HOME_SEARCH_TIMEOUT_S
        while(sw == False):
            if search_must_stop(deadline, cancel_event, cola_orden):
                block = True
                break
            [b_1, cadena, signal_out, dato_in] = libdef.builder(b_1, dato_in, cont_vel-1, 100, 8, vel, media, buffer)
            libdef.set_msg(cadena, epout, epin, buffer, write, read)
            media = libdef.get_media(buffer,media)

            sw = libdef.get_switch(SW_ELBOW, buffer[5])

            value_err = libdef.getError(buffer, VEC_ERROR[2])
            if value_err >= MAX_ERROR:
                print("Joint limit reached")
                cola_orden.put(1)
                logging.warning(libdef.error_msg(1))
                block = True
                break

            if cont_vel < 12:
                cont_vel += 1

        if block == True:
            cola_read.put(media)
            status = 1
            return [b_1, status]

        #Realiza la frenada controlada
        cont_vel = 88
        for i in range(12):
            [b_1, cadena, signal_out, dato_in] = libdef.builder(b_1, dato_in, cont_vel-1, 100, 8, vel, media, buffer)
            libdef.set_msg(cadena, epout, epin, buffer, write, read)
            media = libdef.get_media(buffer,media)

        cont = 0
        while abs(dato_in[0] - media[2]) > 20 and block != True:
            cadena = libhex.mov_comm(1)
            b_1 = libdef.countByte1(b_1)
            cadena = cadena. format(libdef.f_byte(b_1))
            cadena = libdef. fill_msg(cadena, 24)
            msg = libdef.get_encoder(buffer, media)
            cadena += libdef.getStruct(8, signal_out, msg)
            libdef.set_msg(cadena, epout, epin, buffer, write, read)
            media = libdef.get_media(buffer, media)
            cont += 1
            if cont == 100:
                block = True
                cola_orden.put(2)
                logging.warning(libdef.error_msg(2))
                print("ERROR: Joint did not respond")
                break

        [b_1, buffer, media] = libdef.closeMov(b_1, media, 8, signal_out, epout, epin, buffer, write, read)

    logging.info(libdef.info_text(19))

    if block == True:
        cola_read.put(media)
        status = 1
        return [b_1, status]

    #transicion entre articulaciones
    for i in range(20):
        cadena = libhex.mov_comm(1)
        b_1 = libdef.countByte1(b_1)
        cadena = cadena.format(libdef.f_byte(b_1))
        cadena = libdef.fill_msg(cadena, 24)
        msg = libdef.get_encoder(buffer, media)
        cadena += msg
        libdef.set_msg(cadena, epout, epin, buffer, WRITE, READ)
        media = libdef.get_media(buffer, media)


    #Combruebo pitch
    sw = libdef.get_switch(SW_PITCH, buffer[5])
    if sw == True:
        sw = True
    else:
        #muevo pitch
        write = conf.readData("wrist","write")
        vel = conf.readData("wrist","h_vel")
        read = conf.readData("wrist","read")
        cadena = libhex.mov_comm(2)
        b_1 = libdef.countByte1(b_1)
        cadena = cadena.format(libdef.f_byte(b_1))
        cadena = libdef.fill_msg(cadena, 24)
        media = libdef.get_media(buffer,media)
        cadena += libdef.get_encoder(buffer, media)
        libdef.set_msg(cadena, epout, epin, buffer, write, read)

        media = libdef.get_media(buffer,media)
        step_in_1 = media[3]
        step_in_2 = media[4]
        signo_1 = libdef.get_signo(36, buffer)
        signo_2 = libdef.get_signo(41, buffer)
        dato_in_1 = [step_in_1, signo_1]
        dato_in_2 = [step_in_2, signo_2]
        cont_vel = 0
        deadline = time.monotonic() + HOME_SEARCH_TIMEOUT_S
        while(sw == False):
            if search_must_stop(deadline, cancel_event, cola_orden):
                block = True
                break
            cadena = libhex.mov_comm(1)
            b_1 = libdef.countByte1(b_1)
            cadena = cadena.format(libdef.f_byte(b_1))
            cadena = libdef.fill_msg(cadena, 24)
            dato_in_1 = libdef.suma(dato_in_1, cont_vel,vel,100)
            dato_in_2 = libdef.resta(dato_in_2, cont_vel,vel,100)
            signal_out = libdef.detrans(dato_in_1[0])
            signal_out += dato_in_1[1]
            signal_out += libdef.detrans(dato_in_2[0])
            signal_out += dato_in_2[1]
            msg = libdef.get_encoder(buffer,media)
            cadena += libdef.getStruct(10, signal_out, msg)
            libdef.set_msg(cadena, epout, epin, buffer, write, read)
            media = libdef.get_media(buffer,media)

            sw = libdef.get_switch(SW_PITCH, buffer[5])

            value_err1 = libdef.getError(buffer, VEC_ERROR[3])
            value_err2 = libdef.getError(buffer, VEC_ERROR[4])

            if value_err1 >= MAX_ERROR or value_err2 >= MAX_ERROR:
                print("Joint limit reached")
                block = True
                cola_orden.put(1)
                logging.warning(libdef.error_msg(1))
                break

            if cont_vel < 12:
                cont_vel += 1

        if block == True:
            cola_read.put(media)
            status = 1
            return [b_1, status]

        #Ajuste de offset para alinear con la vertical
        cont_vel = 87
        for i in range(60):
            cadena = libhex.mov_comm(1)
            b_1 = libdef.countByte1(b_1)
            cadena = cadena.format(libdef.f_byte(b_1))
            cadena = libdef.fill_msg(cadena, 24)
            dato_in_1 = libdef.suma(dato_in_1, cont_vel,vel,100)
            dato_in_2 = libdef.resta(dato_in_2, cont_vel,vel,100)
            signal_out = libdef.detrans(dato_in_1[0])
            signal_out += dato_in_1[1]
            signal_out += libdef.detrans(dato_in_2[0])
            signal_out += dato_in_2[1]
            msg = libdef.get_encoder(buffer,media)
            cadena += libdef.getStruct(10, signal_out, msg)
            libdef.set_msg(cadena, epout, epin, buffer, write, read)
            media = libdef.get_media(buffer,media)

        cont_vel += 1

        #Realiza la frenada controlada
        cont_vel = 88
        for i in range(12):
            cadena = libhex.mov_comm(1)
            b_1 = libdef.countByte1(b_1)
            cadena = cadena.format(libdef.f_byte(b_1))
            cadena = libdef.fill_msg(cadena, 24)
            dato_in_1 = libdef.suma(dato_in_1, cont_vel,vel,100)
            dato_in_2 = libdef.resta(dato_in_2, cont_vel,vel,100)
            signal_out = libdef.detrans(dato_in_1[0])
            signal_out += dato_in_1[1]
            signal_out += libdef.detrans(dato_in_2[0])
            signal_out += dato_in_2[1]
            msg = libdef.get_encoder(buffer,media)
            cadena += libdef.getStruct(10, signal_out, msg)
            libdef.set_msg(cadena, epout, epin, buffer, write, read)
            media = libdef.get_media(buffer,media)

        cont = 0
        while abs(dato_in_1[0] - media[3]) > 20 or abs(dato_in_2[0] - media[4]) > 20:
            cadena = libhex.mov_comm(1)
            b_1 = libdef.countByte1(b_1)
            cadena = cadena. format(libdef.f_byte(b_1))
            cadena = libdef. fill_msg(cadena, 24)
            msg = libdef.get_encoder(buffer, media)
            cadena += libdef.getStruct(10, signal_out, msg)
            libdef.set_msg(cadena, epout, epin, buffer, write, read)
            media = libdef.get_media(buffer, media)
            cont += 1
            if cont == 100:
                block = True
                cola_orden.put(2)
                logging.warning(libdef.error_msg(2))
                break

        [b_1, buffer, media] = libdef.closeMov(b_1, media, 10, signal_out, epout, epin, buffer, write, read)

    logging.info(libdef.info_text(20))

    if block == True:
        cola_read.put(media)
        status = 1
        return [b_1, status]

    #transicion entre articulaciones
    for _ in range(20):
        cadena = libhex.mov_comm(1)
        b_1 = libdef.countByte1(b_1)
        cadena = cadena.format(libdef.f_byte(b_1))
        cadena = libdef.fill_msg(cadena, 24)
        msg = libdef.get_encoder(buffer, media)
        cadena += msg
        libdef.set_msg(cadena, epout, epin, buffer, WRITE, READ)
        media = libdef.get_media(buffer, media)

    #Combruebo roll
    sw = libdef.get_switch(SW_ROLL, buffer[5])
    if sw == True:
        sw = True
    else:
        #muevo roll
        write = conf.readData("wrist","write")
        vel = conf.readData("wrist","h_vel")
        read = conf.readData("wrist","read")
        cadena = libhex.mov_comm(2)
        b_1 = libdef.countByte1(b_1)
        cadena = cadena.format(libdef.f_byte(b_1))
        cadena = libdef.fill_msg(cadena, 24)
        media = libdef.get_media(buffer,media)
        cadena += libdef.get_encoder(buffer, media)
        libdef.set_msg(cadena, epout, epin, buffer, write, read)

        media = libdef.get_media(buffer,media)
        step_in_1 = media[3]
        step_in_2 = media[4]
        signo_1 = libdef.get_signo(36, buffer)
        signo_2 = libdef.get_signo(41, buffer)
        dato_in_1 = [step_in_1, signo_1]
        dato_in_2 = [step_in_2, signo_2]
        cont_vel = 0
        deadline = time.monotonic() + HOME_SEARCH_TIMEOUT_S
        while(sw == False):
            if search_must_stop(deadline, cancel_event, cola_orden):
                block = True
                break
            cadena = libhex.mov_comm(1)
            b_1 = libdef.countByte1(b_1)
            cadena = cadena.format(libdef.f_byte(b_1))
            cadena = libdef.fill_msg(cadena, 24)
            dato_in_1 = libdef.resta(dato_in_1, cont_vel,vel,100)
            dato_in_2 = libdef.resta(dato_in_2, cont_vel,vel,100)
            signal_out = libdef.detrans(dato_in_1[0])
            signal_out += dato_in_1[1]
            signal_out += libdef.detrans(dato_in_2[0])
            signal_out += dato_in_2[1]
            msg = libdef.get_encoder(buffer,media)
            cadena += libdef.getStruct(10, signal_out, msg)
            libdef.set_msg(cadena, epout, epin, buffer, write, read)
            media = libdef.get_media(buffer,media)

            sw = libdef.get_switch(SW_ROLL, buffer[5])

            value_err1 = libdef.getError(buffer, VEC_ERROR[3])
            value_err2 = libdef.getError(buffer, VEC_ERROR[4])

            if value_err1 >= MAX_ERROR or value_err2 >= MAX_ERROR:
                print("Joint limit reached")
                block = True
                cola_orden.put(1)
                logging.warning(libdef.error_msg(1))
                break

            if cont_vel < 12:
                cont_vel += 1

        if block == True:
            cola_read.put(media)
            status = 1
            return [b_1, status]

        #Ajuste de offset para alinear con la horizontal
        cont_vel = 87
        for i in range(60):
            cadena = libhex.mov_comm(1)
            b_1 = libdef.countByte1(b_1)
            cadena = cadena.format(libdef.f_byte(b_1))
            cadena = libdef.fill_msg(cadena, 24)
            dato_in_1 = libdef.resta(dato_in_1, cont_vel,vel,100)
            dato_in_2 = libdef.resta(dato_in_2, cont_vel,vel,100)
            signal_out = libdef.detrans(dato_in_1[0])
            signal_out += dato_in_1[1]
            signal_out += libdef.detrans(dato_in_2[0])
            signal_out += dato_in_2[1]
            msg = libdef.get_encoder(buffer,media)
            cadena += libdef.getStruct(10, signal_out, msg)
            libdef.set_msg(cadena, epout, epin, buffer, write, read)
            media = libdef.get_media(buffer,media)

        cont_vel += 1
        #Realiza la frenada controlada
        for i in range(12):
            cadena = libhex.mov_comm(1)
            b_1 = libdef.countByte1(b_1)
            cadena = cadena.format(libdef.f_byte(b_1))
            cadena = libdef.fill_msg(cadena, 24)
            dato_in_1 = libdef.resta(dato_in_1, cont_vel,vel,100)
            dato_in_2 = libdef.resta(dato_in_2, cont_vel,vel,100)
            signal_out = libdef.detrans(dato_in_1[0])
            signal_out += dato_in_1[1]
            signal_out += libdef.detrans(dato_in_2[0])
            signal_out += dato_in_2[1]
            msg = libdef.get_encoder(buffer,media)
            cadena += libdef.getStruct(10, signal_out, msg)
            libdef.set_msg(cadena, epout, epin, buffer, write, read)
            media = libdef.get_media(buffer,media)

        cont = 0
        while abs(dato_in_1[0] - media[3]) > 20 or abs(dato_in_2[0] - media[4]) > 20:
            cadena = libhex.mov_comm(1)
            b_1 = libdef.countByte1(b_1)
            cadena = cadena. format(libdef.f_byte(b_1))
            cadena = libdef. fill_msg(cadena, 24)
            msg = libdef.get_encoder(buffer, media)
            cadena += libdef.getStruct(10, signal_out, msg)
            libdef.set_msg(cadena, epout, epin, buffer, write, read)
            media = libdef.get_media(buffer, media)
            cont += 1
            if cont == 100:
                block = True
                cola_orden.put(2)
                logging.warning(libdef.error_msg(2))
                print("ERROR: Joint did not respond")
                break

        [b_1, buffer, media] = libdef.closeMov(b_1, media, 10, signal_out, epout, epin, buffer, write, read)

    logging.info(libdef.info_text(21))

    if block == True:
        cola_read.put(media)
        status = 1
        return [b_1, status]

    #transicion entre articulaciones
    for i in range(20):
        cadena = libhex.mov_comm(1)
        b_1 = libdef.countByte1(b_1)
        cadena = cadena.format(libdef.f_byte(b_1))
        cadena = libdef.fill_msg(cadena, 24)
        msg = libdef.get_encoder(buffer, media)
        cadena += msg
        libdef.set_msg(cadena, epout, epin, buffer, WRITE, READ)
        media = libdef.get_media(buffer, media)

    #Combruebo cadera
    sw = libdef.get_switch(SW_HIP, buffer[5])
    if sw == True:
        sw = True
    else:
        #muevo cadera
        write = conf.readData("cadera","write")
        vel = conf.readData("cadera","h_vel")
        read = conf.readData("cadera","read")
        cadena = libhex.mov_comm(2)
        b_1 = libdef.countByte1(b_1)
        cadena = cadena.format(libdef.f_byte(b_1))
        cadena = libdef.fill_msg(cadena, 24)
        media = libdef.get_media(buffer,media)
        cadena += libdef.get_encoder(buffer, media)
        libdef.set_msg(cadena, epout, epin, buffer, write, read)

        media = libdef.get_media(buffer,media)
        step_in = media[0]
        signo = libdef.get_signo(21, buffer)
        dato_in = [step_in, signo]
        cont_vel = 0
        deadline = time.monotonic() + HOME_SEARCH_TIMEOUT_S
        while(sw == False):
            if search_must_stop(deadline, cancel_event, cola_orden):
                block = True
                break
            [b_1, cadena, signal_out, dato_in] = libdef.builder(b_1, dato_in, cont_vel-1, 100, 4, vel, media, buffer)
            libdef.set_msg(cadena, epout, epin, buffer, write, read)
            media = libdef.get_media(buffer,media)

            sw = libdef.get_switch(SW_HIP, buffer[5])

            value_err = libdef.getError(buffer, VEC_ERROR[0])
            if value_err >= MAX_ERROR:
                print("Joint limit reached")
                block = True
                cola_orden.put(1)
                logging.warning(libdef.error_msg(1))
                break

            if cont_vel < 12:
                cont_vel += 1

        if block == True:
            cola_read.put(media)
            status = 1
            return [b_1, status]

        #Realiza la frenada controlada
        cont_vel = 88
        for i in range(12):
            [b_1, cadena, signal_out, dato_in] = libdef.builder(b_1, dato_in, cont_vel-1, 100, 4, vel, media, buffer)
            libdef.set_msg(cadena, epout, epin, buffer, write, read)
            media = libdef.get_media(buffer,media)

        cont = 0
        while abs(dato_in[0] - media[0]) > 20:
            cadena = libhex.mov_comm(1)
            b_1 = libdef.countByte1(b_1)
            cadena = cadena. format(libdef.f_byte(b_1))
            cadena = libdef. fill_msg(cadena, 24)
            msg = libdef.get_encoder(buffer, media)
            cadena += libdef.getStruct(4, signal_out, msg)
            libdef.set_msg(cadena, epout, epin, buffer, write, read)
            media = libdef.get_media(buffer, media)
            cont += 1
            if cont == 100:
                block = True
                cola_orden.put(2)
                logging.warning(libdef.error_msg(2))
                break

        [b_1, buffer, media] = libdef.closeMov(b_1, media, 4, signal_out, epout, epin, buffer, write, read)

    logging.info(libdef.info_text(22))

    if block:
        status = 1
    cola_read.put(media)
    return [b_1, status]
