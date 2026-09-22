# Autores: Jose Luis Pérez Pérez y Yolanda M. Gimeno Rodríguez
# Fecha:
# Título: Script con el hilo de ejecución de ordenes.
# Universidad de La Laguna

import queue
import libsync
import libhex
import libdef
import time
import conf
import setHome
import moveXYZ
import log
import logging

#################################################################################
# Script encargado de la creación de los mensajes de movimientos. Se separa
# por articulaciones ya que cada una tiene una estructura de mensajes diferentes.
#################################################################################

# Identificacion de que se va a cerrar el programa
EXIT 	  = conf.readData("general","EXIT")
# Posiciones de los datos de cada motor dentro del buffer
#   [cadera, hombro, codo, m1_muñeca, m2_muñeca, pinza]
VEC_POS   = conf.readData("general","VEC_POS")
# Posiciones de los bytes de error de cada motor dentro del buffer
#   [cadera, hombro, codo, m1_muñeca, m2_muñeca, pinza]
VEC_ERROR = conf.readData("general", "VEC_ERROR")
# Valor maximo del byte de secuencia
MAX_COUNT = conf.readData("general","MAX_COUNT")
# Identificacion de que no hubo errores durante la accion
DONE 	  = conf.readData("general","DONE")
# Tiempo de espera para realizar una peticion de lectura tras la escritura
WRITE	  = conf.readData("general","WRITE")
# Tiempo de espera para empezar a generar el siguiente mensaje a enviar tras
# una  peticion de lectura.
READ	  = conf.readData("general","READ")
# Error maximo aceptable en los bytes de error
MAX_ERROR = conf.readData("general", "MAX_ERROR")

#------------------------------ARGUMENTOS USADOS-------------------------------#
# b_1		-> Byte de secuencia
# epout		-> Objeto de endpoint de salida de la controladora
# epin		-> Objeto de enpoint de entrada de la controladora
# buffer	-> Vector que almacena los datos de la ultima lectura
# orden		-> Introduce la orden a seguir. Diferencia el sentido del movimiento
# cola_read -> Cola con el vector media de la posición de encoders
# cola_orden-> Cola con la orden a procesar y/o feedback del funcionamiento
# cola_sync -> Cola que almacena el byte de secuencia entre hilos
# vel		-> Velocidad del movimiento, recogida de la interfaz de usuario
# ang		-> Angulo que debe incrementarse/decrementarse en la articulacion
#------------------------------------------------------------------------------#


# Movimiento de la cadera, separado en tres partes: inicio del movimiento,
# incremento de los valores de encoders (sentido según orden) y fin del
# movimiento. El movimiento depende de la velocidad, las iteraciones y la orden.
def move_hips(b_1, epout, epin, buffer, orden, cola_read, cola_orden, vel,ang):
	write = conf.readData("cadera","write")
	read = conf.readData("cadera","read")
	media = cola_read.get()
	[b_1, buffer, media] = libdef.openMov(b_1, media , epout, epin, buffer, write, read)

	ang = libdef.conversorAngEnc(1, 1, ang)
	ite = libdef.numIte(ang, vel)
	step_in = media[0]
	signo = libdef.get_signo(21, buffer)
	dato_in = [step_in, signo]
	signal_out= ''

	for i in range(ite):
		[b_1, cadena, signal_out, dato_in] = libdef.builder(b_1, dato_in, i, ite, orden, vel, media, buffer)
		libdef.set_msg(cadena, epout, epin, buffer, write, read)
		value_err = libdef.getError(buffer, VEC_ERROR[0])
		if value_err >= MAX_ERROR:
			print("Joint limit reached")
			cola_orden.put(1)
			logging.warning(libdef.error_msg(1))
			break

		media = libdef.get_media(buffer,media)

	#Control de error
	cont = 0
	while abs(dato_in[0] - media[0]) > 20 and value_err < MAX_ERROR:
		cadena = libhex.mov_comm(1)
		b_1 = libdef.countByte1(b_1)
		cadena = cadena.format(libdef.f_byte(b_1))
		cadena = libdef.fill_msg(cadena, 24)
		msg = libdef.get_encoder(buffer, media)
		cadena += libdef.getStruct(orden, signal_out, msg)
		libdef.set_msg(cadena, epout, epin, buffer, write, read)
		media = libdef.get_media(buffer, media)
		if cont == 100:
			print("Motion feedback loop failed")
			cola_orden.put(2)
			logging.warning(libdef.error_msg(2))
			break
		cont += 1

	[b_1, buffer, media] = libdef.closeMov(b_1, media, orden, signal_out, epout, epin, buffer, write, read)

	cola_read.put(media)
	return b_1

# Movimiento del hombro, separado en tres partes: inicio del movimiento,
# incremento de los valores de encoders (sentido según orden) y fin del
# movimiento. El movimiento depende de la velocidad, las iteraciones y la orden.
def move_shoulder(b_1, epout, epin, buffer, orden, cola_read, cola_orden, vel,ang):
	write = conf.readData("hombro","write")
	read = conf.readData("hombro","read")
	media = cola_read.get()
	[b_1, buffer, media] = libdef.openMov(b_1, media , epout, epin, buffer, write, read)

	ang = libdef.conversorAngEnc(2, 1, ang)
	ite = libdef.numIte(ang, vel)
	step_in = media[1]
	signo = libdef.get_signo(26, buffer)
	dato_in = [step_in, signo]
	signal_out= ''
	for i in range(ite):
		[b_1, cadena, signal_out, dato_in] = libdef.builder(b_1, dato_in, i, ite, orden, vel, media, buffer)
		libdef.set_msg(cadena, epout, epin, buffer, write, read)
		value_err = libdef.getError(buffer, VEC_ERROR[1])
		if value_err >= MAX_ERROR:
			print("Joint limit reached")
			cola_orden.put(1) #Introduce codigo de error en la ejecucion de la orden
			logging.warning(libdef.error_msg(1))
			break

		media = libdef.get_media(buffer,media)

	cont = 0
	while abs(dato_in[0] - media[1]) > 20 and value_err < MAX_ERROR:
		cadena = libhex.mov_comm(1)
		b_1 = libdef.countByte1(b_1)
		cadena = cadena. format(libdef.f_byte(b_1))
		cadena = libdef. fill_msg(cadena, 24)
		msg = libdef.get_encoder(buffer, media)
		cadena += libdef.getStruct(orden, signal_out, msg)
		libdef.set_msg(cadena, epout, epin, buffer, write, read)
		media = libdef.get_media(buffer, media)
		if cont == 100:
			print("ERROR: Joint did not respond")
			cola_orden.put(2) #Introduce codigo de error en la ejecucion de la orden
			logging.warning(libdef.error_msg(2))
			break
		cont += 1

	[b_1, buffer, media] = libdef.closeMov(b_1, media, orden, signal_out, epout, epin, buffer, write, read)
	cola_read.put(media)

	return b_1


# Movimiento del codo, separado en tres partes: inicio del movimiento,
# incremento de los valores de encoders (sentido según orden) y fin del
# movimiento. El movimiento depende de la velocidad, las iteraciones y la orden.
def move_elbow(b_1, epout, epin, buffer, orden, cola_read, cola_orden, vel,ang):
	write = conf.readData("codo","write")
	read = conf.readData("codo","read")
	media = cola_read.get()
	[b_1, buffer, media] = libdef.openMov(b_1, media , epout, epin, buffer, write, read)

	ang = libdef.conversorAngEnc(3, 1, ang)
	ite = libdef.numIte(ang, vel)
	step_in = media[2]
	signo = libdef.get_signo(31, buffer)
	dato_in = [step_in, signo]
	signal_out= ''
	for i in range(ite):
		[b_1, cadena, signal_out, dato_in] = libdef.builder(b_1, dato_in, i, ite, orden, vel, media, buffer)
		libdef.set_msg(cadena, epout, epin, buffer, write, read)
		value_err = libdef.getError(buffer, VEC_ERROR[2])
		if value_err >= MAX_ERROR:
			print("Joint limit reached")
			cola_orden.put(1) #Introduce codigo de error en la ejecucion de la orden
			logging.warning(libdef.error_msg(1))
			break

		media = libdef.get_media(buffer,media)

	cont = 0
	while abs(dato_in[0] - media[2]) > 20 and value_err < MAX_ERROR:
		cadena = libhex.mov_comm(1)
		b_1 = libdef.countByte1(b_1)
		cadena = cadena. format(libdef.f_byte(b_1))
		cadena = libdef. fill_msg(cadena, 24)
		msg = libdef.get_encoder(buffer, media)
		cadena += libdef.getStruct(orden, signal_out, msg)
		libdef.set_msg(cadena, epout, epin, buffer, write, read)
		media = libdef.get_media(buffer, media)
		if cont == 100:
			print("ERROR: Joint did not respond")
			cola_orden.put(2) #Introduce codigo de error en la ejecucion de la orden
			logging.warning(libdef.error_msg(2))
			break
		cont += 1

	[b_1, buffer, media] = libdef.closeMov(b_1, media, orden, signal_out, epout, epin, buffer, write, read)
	cola_read.put(media)

	return b_1

# Movimiento del codo, separado en tres partes: inicio del movimiento,
# incremento de los valores de encoders (sentido según orden) y fin del
# movimiento. Se deben de mover dos motores simultaneamente por lo que
# las operaciones son dobles. Depende de la velocidad, las iteraciones y la orden.
def move_wrist(b_1, epout, epin, buffer, orden, cola_read, cola_orden, vel,ang):
	write = conf.readData("wrist","write")
	read = conf.readData("wrist","read")
	media = cola_read.get()
	[b_1, buffer, media] = libdef.openMov(b_1, media , epout, epin, buffer, write, read)

	if orden == 10 or orden == 11:
		ang = libdef.conversorAngEnc(4, 1, ang)
		ite = libdef.numIte(ang, vel)
	else:
		ang = libdef.conversorAngEnc(5, 1, ang)
		ite = libdef.numIte(ang, vel)

	step_in_1 = media[3]
	step_in_2 = media[4]
	signo_1 = libdef.get_signo(36, buffer)
	signo_2 = libdef.get_signo(41, buffer)
	dato_in_1 = [step_in_1, signo_1]
	dato_in_2 = [step_in_2, signo_2]
	signal_out= ''
	for i in range(ite):
		siguiente_1 = libdef.transform([buffer[VEC_POS[3]], buffer[VEC_POS[3]+1]])
		siguiente_2 = libdef.transform([buffer[VEC_POS[4]], buffer[VEC_POS[4]+1]])
		cadena = libhex.mov_comm(1)
		b_1 = libdef.countByte1(b_1)
		cadena = cadena.format(libdef.f_byte(b_1))
		cadena = libdef.fill_msg(cadena, 24)
		if orden == 10:
			dato_in_1 = libdef.resta(dato_in_1, i+1,vel,ite)
			dato_in_2 = libdef.suma(dato_in_2, i+1,vel,ite)
		elif orden == 11:
			dato_in_1 = libdef.suma(dato_in_1, i+1,vel,ite)
			dato_in_2 = libdef.resta(dato_in_2, i+1,vel,ite)
		elif orden == 12:
			dato_in_1 = libdef.suma(dato_in_1, i+1,vel,ite)
			dato_in_2 = libdef.suma(dato_in_2, i+1,vel,ite)
		elif orden == 13:
			dato_in_1 = libdef.resta(dato_in_1, i+1,vel,ite)
			dato_in_2 = libdef.resta(dato_in_2, i+1,vel,ite)

		signal_out = libdef.detrans(dato_in_1[0])
		signal_out += dato_in_1[1]
		signal_out += libdef.detrans(dato_in_2[0])
		signal_out += dato_in_2[1]
		msg = libdef.get_encoder(buffer,media)
		cadena += libdef.getStruct(orden, signal_out, msg)
		libdef.set_msg(cadena, epout, epin, buffer, write, read)

		value_err1 = libdef.getError(buffer, VEC_ERROR[3])
		value_err2 = libdef.getError(buffer, VEC_ERROR[4])

		if value_err1 >= MAX_ERROR or value_err2 >= MAX_ERROR:
			print("Joint limit reached")
			cola_orden.put(1) #Introduce codigo de error en la ejecucion de la orden
			logging.warning(libdef.error_msg(1))
			break

		media = libdef.get_media(buffer,media)

	cont = 0
	while (abs(dato_in_1[0] - media[3]) > 20 or abs(dato_in_2[0] - media[4]) > 20) and value_err1 < MAX_ERROR and value_err2 < MAX_ERROR: #cambia media segun articulacion
		cadena = libhex.mov_comm(1)
		b_1 = libdef.countByte1(b_1)
		cadena = cadena. format(libdef.f_byte(b_1))
		cadena = libdef. fill_msg(cadena, 24)
		msg = libdef.get_encoder(buffer, media)
		cadena += libdef.getStruct(orden, signal_out, msg)
		libdef.set_msg(cadena, epout, epin, buffer, write, read)
		media = libdef.get_media(buffer, media)
		if cont == 100:
			print("ERROR: Joint did not respond")
			cola_orden.put(2) #Introduce codigo de error en la ejecucion de la orden
			logging.warning(libdef.error_msg(2))
			break
		cont += 1

	[b_1, buffer, media] = libdef.closeMov(b_1, media, orden, signal_out, epout, epin, buffer, write, read)
	cola_read.put(media)

	return b_1

# Movimiento de apertura y cierre de la pinza. Consta de una secuencia diferente
# a los anteriores movimientos.
# Tiene velocidad constante, y en funcion de la orden hará un incremento o decremento
# de los valores de enconder del mensaje de escritura.
def clamp(b_1, epout, epin, buffer, orden, cola_read):
	write = conf.readData("pinza","write")
	read = conf.readData("pinza","read")
	vel = conf.readData("pinza","vel")
	media = cola_read.get()
	for i in range(1,4):
		cadena = libhex.clamp(i)
		b_1 = libdef.countByte1(b_1)
		cadena = cadena.format(libdef.f_byte(b_1))
		cadena = libdef.fill_msg(cadena, 24)
		media = libdef.get_media(buffer,media)
		cadena += libdef.get_encoder(buffer, media)
		libdef.set_msg(cadena, epout, epin, buffer, write, read)

	media = libdef.get_media(buffer,media)
	step_in = media[5]
	signo = libdef.get_signo(46, buffer)
	dato_in = [step_in, signo]
	signal_out= ''
	ite = conf.readData("pinza","ite_clamp")
	for i in range(ite):
		[b_1, cadena, signal_out, dato_in] = libdef.builder(b_1, dato_in, i, ite, orden, vel, media, buffer)
		libdef.set_msg(cadena, epout, epin, buffer, write, read)

	for _ in range(15):
		cadena = libhex.mov_comm(1)
		b_1 = libdef.countByte1(b_1)
		cadena = cadena.format(libdef.f_byte(b_1))
		cadena = libdef.fill_msg(cadena, 24)
		media = libdef.get_media(buffer, media)
		msg = libdef.get_encoder(buffer, media)
		cadena += libdef.getStruct(orden, signal_out, msg)
		libdef.set_msg(cadena, epout, epin, buffer, write, read)
		media = libdef.get_media(buffer, media)

	cont = 0
	while abs(dato_in[0] - media[5]) == 0:
		cadena = libhex.mov_comm(1)
		b_1 = libdef.countByte1(b_1)
		cadena = cadena.format(libdef.f_byte(b_1))
		cadena = libdef.fill_msg(cadena, 24)
		media = libdef.get_media(buffer, media)
		msg = libdef.get_encoder(buffer, media)
		cadena += libdef.getStruct(orden, signal_out, msg)
		libdef.set_msg(cadena, epout, epin, buffer, write, read)
		media = libdef.get_media(buffer, media)
		if cont == 100:
			print("ERROR: Joint did not respond")
			cola_orden.put(1) #Introduce codigo de error en la ejecucion de la orden
			logging.warning(libdef.error_msg(1))
			break
		cont += 1

	media = libdef.get_media(buffer, media)
	cadena = libhex.clamp(4)
	b_1 = libdef.countByte1(b_1)
	cadena = cadena.format(libdef.f_byte(b_1))
	cadena = libdef.fill_msg(cadena, 24)
	msg = libdef.get_encoder(buffer,media)
	cadena += libdef.getStruct(orden, signal_out, msg)
	libdef.set_msg(cadena, epout, epin, buffer, write, read)

	media = libdef.get_media(buffer, media)
	cadena = libhex.clamp(5)
	b_1 = libdef.countByte1(b_1)
	cadena = cadena.format(libdef.f_byte(b_1))
	cadena = libdef.fill_msg(cadena, 24)
	msg = libdef.get_encoder(buffer,media)
	cadena += libdef.getStruct(orden, signal_out, msg)
	libdef.set_msg(cadena, epout, epin, buffer, write, read)

	media = libdef.get_media(buffer,media)
	cola_read.put(media)
	return b_1

# Desactiva el control sobre los motores
def motors_off(b_1, epout, epin, buffer,cola_read):
	media = libdef.get_media(buffer,cola_read.get())
	for i in range (1,27):
		cadena =libhex.motorsoff(i)
		b_1 = libdef.countByte1(b_1)
		cadena = cadena.format(libdef.f_byte(b_1))
		cadena = libdef.fill_msg(cadena, 24)
		media = libdef.get_media(buffer,media)
		cadena += libdef.get_encoder(buffer,media)
		libdef.set_msg(cadena, epout, epin, buffer, WRITE, READ)

	cola_read.put(media)
	return b_1

# Activa el control sobre los motores
def motors_on(b_1, epout, epin, buffer,cola_read):
	media = libdef.get_media(buffer,cola_read.get())
	for i in range(1,8):
		cadena = libhex.mov_comm(6)
		b_1 = libdef.countByte1(b_1)
		cadena = cadena.format(libdef.f_byte(b_1))
		cadena = libdef.fill_msg(cadena, 8)
		cadena += libhex.motorson(i)
		cadena = libdef.fill_msg(cadena, 24)
		media = libdef.get_media(buffer,media)
		cadena += libdef.get_encoder(buffer,media)
		libdef.set_msg(cadena, epout, epin, buffer, WRITE, READ)

	cola_read.put(media)
	return b_1


# Inicia el cierre de las conexiones entre controladora y host
def scorbotoff(b_1,epout,epin,buffer,cola_read):
	media = libdef.get_media(buffer,cola_read.get())
	for i in range(1,12):
		cadena = libhex.mov_comm(6)
		b_1 = libdef.countByte1(b_1)
		cadena = cadena.format(libdef.f_byte(b_1))
		cadena += libhex.get_scorbotoff(i)
		cadena = libdef.fill_msg(cadena, 24)
		media = libdef.get_media(buffer,media)
		cadena += libdef.get_encoder(buffer,media)
		libdef.set_msg(cadena, epout, epin, buffer, WRITE, READ)

	b_1 = libsync.send_wait(b_1,epout,epin,buffer)
	cadena = libhex.mov_comm(6)
	b_1 = libdef.countByte1(b_1)
	cadena = cadena.format(libdef.f_byte(b_1))
	cadena = libdef.fill_msg(cadena, 8)
	cadena += libhex.get_msg1(4)
	cadena = libdef.fill_msg(cadena, 24)
	media = libdef.get_media(buffer,media)
	cadena += libdef.get_encoder(buffer,media)
	libdef.set_msg(cadena, epout, epin, buffer, WRITE, READ)
	cadena = libhex.mov_comm(6)
	b_1 = libdef.countByte1(b_1)
	cadena = cadena.format(libdef.f_byte(b_1))
	cadena = libdef.fill_msg(cadena, 8)
	cadena += libhex.get_msg1(2)
	cadena = libdef.fill_msg(cadena, 24)
	media = libdef.get_media(buffer,media)
	cadena += libdef.get_encoder(buffer,media)
	libdef.set_msg(cadena, epout, epin, buffer, WRITE, READ)
	media = libdef.get_media(buffer,media)
	cola_read.put(media)

#################################################################################
# Segundo hilo de programa.
# Gestiona las ordenes enviadas por el usuario desde la interfaz gráfica y
# las redirige a las acciones que debe realizar el robot.
#################################################################################
def execute(cola_sync, cola_orden, cola_read, epout, epin, buffer,
		cola_result=None, cancel_event=None):
	# The GUI historically used one queue in both directions. SDK clients pass a
	# separate result queue so they cannot consume their own command.
	if cola_result is None:
		cola_result = cola_orden
	#Inicializamos el home en false, esto cambiara una vez se realice el home
	home = False
	# Se corresponde con el angulo inicial respecto a la vertical
	posRef = conf.readData("general", "posRef")
	while 1:
		select = cola_orden.get()
		orden = select[0]
		if orden != 19:
			vel = select[1]
			ite = select[2]
		cola_sync.put(MAX_COUNT)
		while orden != None:
			b_1 = cola_sync.get()
			if b_1 == MAX_COUNT:
				cola_sync.put(MAX_COUNT)
			else:
				if orden != 19 and orden != 16 and orden != 17:
					home = False
					posRef = conf.readData("general", "posRef")
				if orden == 4 or orden == 5:
					b_1 = move_hips(b_1, epout, epin, buffer, orden, cola_read, cola_result, vel,ite)
				elif orden == 6 or orden == 7:
					b_1 = move_shoulder(b_1, epout, epin, buffer, orden, cola_read, cola_result, vel,ite)
				elif orden == 8 or orden == 9:
					b_1 = move_elbow(b_1, epout, epin, buffer, orden, cola_read, cola_result, vel,ite)
				elif orden == 10 or orden == 11 or orden == 12 or orden == 13:
					b_1 = move_wrist(b_1, epout, epin, buffer, orden, cola_read, cola_result, vel,ite)
				elif orden == 14 or orden == 15:
					b_1 = clamp(b_1, epout, epin, buffer, orden, cola_read)
				elif orden == 16:
					b_1 = motors_off(b_1, epout, epin, buffer, cola_read)
				elif orden == 17:
					b_1 = motors_on(b_1, epout, epin, buffer, cola_read)
				elif orden == 18:
					[b_1, status] = setHome.homing(b_1, epout, epin, buffer,
						cola_read, cola_result, cancel_event)
					if status == 0:
						home = True
					else:
						try:
							b_1 = motors_off(b_1, epout, epin, buffer, cola_read)
						except Exception:
							logging.exception("Could not disable motors after homing failure")
							cola_result.put(2)
						cola_result.put(1)
				elif orden == 19:
					if home == True:
						[b_1, posRef] = moveXYZ.controlXYZ(select[1], select[2], posRef, b_1, epout, epin, buffer, cola_read, cola_result)
					else:
						cola_result.put(5)
						print("Robot has not been homed")
						logging.warning(libdef.error_msg(5))
				elif orden == EXIT:
					b_1= motors_off(b_1,epout,epin,buffer,cola_read)
					scorbotoff(b_1,epout,epin,buffer,cola_read)
					cola_sync.put(EXIT)
					cola_result.put(DONE)
					break
				orden = None
				cola_sync.put(b_1)
				cola_result.put(DONE)
				time.sleep(READ)

		if orden == EXIT:
			break
