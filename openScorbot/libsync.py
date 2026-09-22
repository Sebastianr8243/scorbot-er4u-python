# Autores: Jose Luis Pérez Pérez y Yolanda M. Gimeno Rodríguez
# Fecha:
# Título: Script de linea de sincronismo
# Universidad de La Laguna

import time
import libcomm
import libhex
import libdef
import conf
import log
import logging

###############################################################################
# Script que realiza la conexion inicial entre la aplicación y la controladora
# y mantiene la sincronización entre ambos durante el uso de la aplicacion.
###############################################################################

# Tiempo de espera para realizar una peticion de lectura tras la escritura
WRITE = conf.readData("general","WRITE")

# Tiempo de espera para empezar a generar el siguiente mensaje a enviar tras
# una  peticion de lectura.
READ = conf.readData("general","READ")

# Bound each initial controller acknowledgment wait.
HANDSHAKE_WAIT_TIMEOUT_S = 30.0

# Define hilo principal de sincronizacion entre la controladora y el programa.
# Envía mensajes de estado de reposo a la controladora.
# Se mantiene una comunicacion en tiempo real con el estado de los encoder
# de cada motor. Se actualiza el valor del byte de secuencia y del vector
# media
#
# cola_sync -> Cola con el byte de secuencia
# cola_read -> Cola con el vector media de la posición de encoders
# epout     -> Objeto de endpoint de salida de la controladora
# epin      -> Objeto de enpoint de entrada de la controladora
# buffer    -> Vector que almacena los datos de la ultima lectura
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

# Funcion que establece la conexion inicial con la controladora. Se divide en tres grupos
# de mensajes. Se inicializa el vector media.
# La funcion devuelve el valor del byte de secuencia y el vector media.
#
# epout     -> Objeto de endpoint de salida de la controladora
# epin      -> Objeto de enpoint de entrada de la controladora
# buffer    -> Vector que almacena los datos de la ultima lectura
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

# Primer paquete del proceso de conexión. Es el mas pequeño de los tres
# y se envia nada mas realizar la conexion USB.
# Devuelve el valor del byte de secuencia.
#
# b_1       -> Byte de secuencia
# epout     -> Objeto de endpoint de salida de la controladora
# epin      -> Objeto de enpoint de entrada de la controladora
# buffer    -> Vector que almacena los datos de la ultima lectura
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

# Segundo paquete del proceos de conexion.
# Devuelve el valor del byte de secuencia en decimal
#
# b_1       -> Byte de secuencia
# epout     -> Objeto de endpoint de salida de la controladora
# epin      -> Objeto de enpoint de entrada de la controladora
# buffer    -> Vector que almacena los datos de la ultima lectura
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

# Tercer paquete del proceseo de conexion. Se realiza la conexion de los motores.
# Devuelve el valor del byte de secuencia en decimal
#
# b_1       -> Byte de secuencia
# epout     -> Objeto de endpoint de salida de la controladora
# epin      -> Objeto de enpoint de entrada de la controladora
# buffer    -> Vector que almacena los datos de la ultima lectura
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

# Funcion que se emplea despues de enviar cada uno de los tres paquetes de mensajes
# anteriores. Su fin es enviar un mensaje sin ordenes que mantenga la secuencia hasta
# que la controladora confirma haber recibido al completo el grupo de mensajes.
#
# b_1       -> Byte de secuencia
# epout     -> Objeto de endpoint de salida de la controladora
# epin      -> Objeto de enpoint de entrada de la controladora
# buffer    -> Vector que almacena los datos de la ultima lectura
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

# Los dos contadores a continuacion se emplean en el send_pkt2, para simplificar
# el código de generación de mensaje.

# Contador dependiente del valor devuelto por el countByte7. Este duplica su valor
# cuando b_7 = 0
#
# b_6 -> Valor del byte numero 6 del mensaje
# b_7 -> Valor del byte numero 7 del mensaje
#
def countByte6(b_7, b_6):
	if b_7 == 0:
		b_6 += b_6
		return b_6
	else:
		return b_6

# Contador de 0 a 10, sin el 8.
#
# b_7 -> Valor del byte numero 7 del mensaje
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
