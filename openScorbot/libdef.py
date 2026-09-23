# Autores: Jose Luis Pérez Pérez y Yolanda M. Gimeno Rodríguez
# Fecha:
# Título: Libreria de funciones
# Universidad de La Laguna

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

#to do:
#Mensajes de error a añadir:
# Funcion check -> Linea 422
# Funcion get_signo -> Linea 477


#############################################################
# Script donde estan todas las funciones que son compartidas
# por los dos hilos
#############################################################


# Identificacion de que se va a cerrar el programa
EXIT		= 528
# Tiempo de espera para empezar a generar el siguiente mensaje a enviar tras
# una  peticion de lectura.
SLEEP = conf.readData("general","READ")
# Identificacion de que no hubo errores durante la accion
DONE = conf.readData("general", "DONE")
# Minimo valor de la franja superior de valores posibles
LIM_SUP = conf.readData("general", "upLimit")
# Maximo valor de la franja inferior de valores posibles
LIM_INF = conf.readData("general", "downLimit")
# Posiciones de los datos de cada motor dentro del buffer
# [cadera, hombro, codo, m1_muñeca, m2_muñeca, pinza]
VEC_POS = conf.readData("general", "VEC_POS")
# Angulo de referencia, el punto inicial tras el home.
angRef = conf.readData("general", "angRef")
posRef = []


#Rellena los mensajes con ceros para que tengan la longitud especificada
#
# msg -> Es el mensaje que debe ser rellenado
# len_msg -> Es la longitud que msg debe alcanzar rellenandose con ceros
#
##Ejemplo:
# msg = 'FFFFFF'
# len_msg = 8
# return -> 'FFFFFF00'
#
def fill_msg(msg, len_msg):
	x = len_msg - len(msg)
	for i in range(0,x):
		msg += '0'
	return msg

# Da el formato apropiado al byte de secuencia para que siempre tenga longitud 2
#
# b_1 es un entero. Si su valor esta entre 0 y 16, su equivalente hex sera de un char
# por lo que se le añade un 0 a la izquierda y se guarda el dato de interes del resultado
# de la conversion. En caso contrario, se realiza la conversion y se guardan los datos de
# interes
#
# b_1   -> Byte de secuencia
#
##Ejemplo:
#
# b_1 = 5
# str(hex(b_1))= 'x/5'
# dato de interes -> 5
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

# Incrementa el valor del byte de secuencia en 1 en cada iteracion.
#
# Si el contador llega al maximo indicado en la variable almacenada en el conf.py,
# el valor del contador se reinicia a 1
#
# b_1  -> Byte de secuencia
#
def countByte1(b_1):
	b_1 += 1
	if b_1 < conf.readData("general","MAX_COUNT"):
		return b_1
	else:
		b_1 = 1
		return b_1

# Realiza una media de los valores de los encoders entre el valor de la media
# almacenada y la ultima lectura realizada
# Si la diferencia entre la lectura y la media es mayor o igual al parametro,
# la media pasa a valer lo que la lectura
#
# buffer -> Vector que almacena los datos de la ultima lectura
# media  -> Vector de ajuste de las posiciones de los encoders
#
def get_media(buffer, media):
	vec_pos = conf.readData("general","VEC_POS")
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


# Extrae especificamente del buffer el valor de la lectura de los encoders
# poniendo ordenadamente la informacion convertida a hexadecimal y añadiendo
# el signo asociado a cada encoder.
#
# El orden de los datos segun la articulacion es:
#
#  				cadera-hombro-codo-muñeca1-muñeca2-pinza
#
# Y la estructura de los datos de cada arituclacion es:
#
#							posicion-signo
#
# buffer -> Vector que almacena los datos de la ultima lectura
# media  -> Vector de ajuste de las posiciones de los encoders
#
def get_encoder(buffer, media):
	msg = ''
	vec_pos = conf.readData("general","VEC_POS")
	for i in range(len(vec_pos)):
		dato = detrans(media[i])
		msg += dato
		dato = get_signo((vec_pos[i]+2),buffer)
		msg += dato
	return msg


# Libreria de mensajes mostrados en la interfaz grafica al finalizar acciones
# sin ningun error
#
# cont   -> Identificador del mensaje buscado
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


# Libreria de mensajes mostrados en la interfaz grafica al producirse un error
# durante la realizacion de un accion.
#
# cont   -> Identificador del mensaje buscado
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
		13: 'ERROR 109: Entity not found'
		}
	return switcher.get(cont, "Invalid request")


# Deteccion del microinterruptor. Busca el valor asociado al argumento art dentro
# del argumento lectura_sw.
# Codigo del microinterruptor activo segun la articulacion:
#   Cadera: 1
#   Hombro: 2
#   Codo  : 4
#   Pitch : 8
#   Roll  : 16
#
# art          -> Identificacion del microinterruptor de interes
# lectura_sw   -> Byte numero 6 del buffer con la informacion de los microinterruptores
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


# Secuencia de inicio de movimiento de una articulacion
#
# b_1       -> Byte de secuencia
# media     -> Vector de ajuste de las posiciones de los encoders
# epout     -> Objeto de endpoint de salida de la controladora
# epin      -> Objeto de enpoint de entrada de la controladora
# buffer    -> Vector que almacena los datos de la ultima lectura
# write     -> Tiempo de espera para realizar una peticion de lectura tras la escritura
# read      -> Tiempo de espera para empezar a generar el siguiente mensaje a enviar tras
#              una  peticion de lectura.
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

# Secuencia de finalizacion de movimiento de una articulacion
#
# b_1        -> Byte de secuencia
# media      -> Vector de ajuste de las posiciones de los encoders
# orden      -> Introduce la orden a seguir. Diferencia el sentido del movimiento
# signal_out -> String con la informacion de los motores
# epout      -> Objeto de endpoint de salida de la controladora
# epin       -> Objeto de enpoint de entrada de la controladora
# buffer     -> Vector que almacena los datos de la ultima lectura
# write      -> Tiempo de espera para realizar una peticion de lectura tras la escritura
# read       -> Tiempo de espera para empezar a generar el siguiente mensaje a enviar tras
#              una  peticion de lectura.
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
	cadena += getStruct(orden, signal_out, msg)
	set_msg(cadena, epout, epin, buffer, write, read)

	media = get_media(buffer,media)

	return [b_1, buffer, media]

# Selección de la estructura estandar para el envio de posiciones a la controladora
# segun la orden recibida
#
# orden      -> Introduce la orden a seguir. Diferencia el sentido del movimiento
# signal_out -> String con la informacion de los motores
# msg        -> String del mensaje a enviar sin la estructura completa
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
		section = signal_out + msg[24:len(msg)]

	return section

# Decrementa o incrementa, en funcion de la orden, el valor de los encoders
# en el mensaje de escritura. Se hace uso de esta función en los movimientos
# de cadera, hombro y codo en el script libcomm.
def builder(b_1, dato_in, i, ite, orden, vel, media, buffer):
	cadena = libhex.mov_comm(1)
	b_1 = countByte1(b_1)
	cadena = cadena.format(f_byte(b_1))
	cadena = fill_msg(cadena, 24)
	if orden == 5 or orden == 6 or orden == 9 or orden == 14:
		dato_in = suma(dato_in, i+1, vel, ite)
	else:
		dato_in = resta(dato_in, i+1, vel, ite)

	signal_out = detrans(dato_in[0])
	signal_out += dato_in[1]
	msg = get_encoder(buffer, media)
	cadena += getStruct(orden, signal_out, msg)

	return [b_1, cadena, signal_out, dato_in]

# Comprueba bytes de error del buffer
#
# buffer  -> Vector que almacena los datos de la ultima lectura
# pos     -> Indica que byte hay que estudiar
#
def getError(buffer, pos):
	x = transform([buffer[pos],buffer[pos+1]])
	if x >= 65500:
		x = abs(65535 - x)
	return x


# Funcion para estudiar los mensajes de escritura y lectura entre controladora
# y programa
#
# msg   -> Mensaje a exportar
# setup -> Indicador del origen y destino del mensaje
#
def filter(msg, setup):
	result = []
	if(setup == "escritura"):
		result.append("Escritura")
		for i in range(64):
			str_hex = msg[2*i] + msg[2*i+1]
			result.append(int(str_hex, 16))
	else:
		result.append("Lectura")
		for i in range(len(msg)):
			result.append(msg[i])

	print(result)


#Da el formato final apropiado al mensaje antes de ser enviado
#
# cadena    -> Mensaje previo formato
# epout     -> Objeto de endpoint de salida de la controladora
# epin      -> Objeto de enpoint de entrada de la controladora
# buffer    -> Vector que almacena los datos de la ultima lectura
# write      -> Tiempo de espera para realizar una peticion de lectura tras la escritura
# read       -> Tiempo de espera para empezar a generar el siguiente mensaje a enviar tras
#              una  peticion de lectura.
#
def set_msg(cadena, epout, epin, buffer, write,read):
	cadena = fill_msg(cadena, conf.readData('general','MSG_LEN'))
	#filter(cadena, 'escritura')
	x = bytes.fromhex(cadena)
	check(x, epout, epin, buffer, write,read)
	#filter(buffer, 'lectura') # Se comunica con la controladora enviando y recibiendo mensajes.
## NOTA: Los sleeps son vitales  para dar tiempo a la controladora a generar
## la respuesta correcta
# En caso de error, salta un mensaje avisando de ello

# x         -> Mensaje a enviar en el formato correcto
# epout     -> Objeto de endpoint de salida de la controladora
# epin      -> Objeto de enpoint de entrada de la controladora
# buffer    -> Vector que almacena los datos de la ultima lectura
# write     -> Tiempo de espera para realizar una peticion de lectura tras la escritura
# read      -> Tiempo de espera para empezar a generar el siguiente mensaje a enviar tras
#              una  peticion de lectura.
#
def check(x,epout,epin,buffer, write,read):
	try:
		epout.write(x, conf.readData('general','TIME_OUT_W'))
		time.sleep(write)
	except Exception as exc:
		raise RuntimeError(f"USB write failed: {exc}") from exc
	try:
		epin.read(buffer, conf.readData('general','TIME_OUT_R'))
		time.sleep(read)
	except Exception as exc:
		raise RuntimeError(f"USB read failed: {exc}") from exc


# Realiza la transformacion de un par de datos int a un solo int equivalente
## NOTA: Los datos de los encoders en el buffer están al revez y por pares, por
## lo que hay que darles la vuelta antes de juntarlo y transformarlo en un solo entero
#
# vect_int -> vector de dos posiciones de datos en entero
#
##Ejemplo
#Queremos el numero 1 como resultado, por lo que el vector debe contener:
# vect_int = [1, 0]
# str_hex = 0001
# return 1
###
def transform(vect_int):
	str_hex = format(vect_int[1], '02x')
	str_hex += format(vect_int[0], '02x')
	return int(str_hex, 16)


# Transforma un entero en su hexadecimal equivalente de longitud 4
# Si el hexadecimal equivalente no tiene la longitud requerida, se le añaden
# 0 a la derecha
#
# dato -> int a transformar en hexadecimal
#
def detrans(dato):
	str = format(dato, '04x')
	str_hex = str[2] + str[3] + str[0] + str[1]
	return str_hex


# Transforma el valor del signo asociado a cada encoder a su equivalente hexadecimal
## NOTA: El valor int y hex de esta transformacion no guarda relacion matematica
## ya que en la lectura el signo ocupa 2 bytes y en la escritura ocupa 4
#
# pos    -> Posicion del buffer donde esta el dato buscado
# buffer    -> Vector que almacena los datos de la ultima lectura
#
# signo == '0000' -> Indica que el encoder esta en los valores minimos
# o que ha llegado al maximo sumando
# signo == 'FFFF' -> Indica que el encoder esta en los valores maximos
# o que ha llegado al minimo restando
#
def get_signo(pos, buffer):
	if buffer[pos] == 128:
		return '0000'
	elif buffer[pos] == 127:
		return 'ffff'
	else:
		raise ValueError(f'Signo fuera de rango: {buffer[pos]}')


# Funcion que realiza la operacion suma segun indiquen sus argumentos. Se devuelve
# el resultado de la suma y su signo asociado en un mismo vector
#
# dato_in  -> Contine en dato_in[0] el valor que debe ser aumentado y en dato_in[1]
#             el signo asociado a dicho valor
# cont     -> Numero de veces que se ha realizado la suma
# vel      -> Velocidad a la que se debe aumentar el valor sumado
# ite      -> Numero de iteraciones que se van a realizar en total
#
## NOTA: En caso que el resultado de la suma supere el maximo de 65535, se considera
## que el encoder ha llegado al maximo de su resolucion y el valor de la suma debe ser
## la diferencia entre el resultado de la suma anterior y el maximo. Ademas,
## el signo debe cambiarse
#
def suma(dato_in,cont,vel,ite):
	dato_in[0] += incremento(cont,vel,ite)
	if dato_in[0] > 65535:
		dato_in[0] -= 65535
		dato_in[1] = '0000'
	return dato_in


# Funcion que realiza la operacion resta segun indiquen sus argumentos. Se devuelve
# el resultado de la resta y su signo asociado en un mismo vector
#
# dato_in  -> Contine en dato_in[0] el valor que debe ser decrementado y en dato_in[1]
#             el signo asociado a dicho valor
# cont     -> Numero de veces que se ha realizado la resta
# vel      -> Velocidad a la que se debe decrementar el valor restado
# ite      -> Numero de iteraciones que se van a realizar en total
#
##NOTA: En caso que el resultado de la resta sea inferior al minimo de 0, se considera
## que el encoder ha llegado a su minima resolucion y el valor de la resta debe ser la
## diferencia entre el maximo y el resultado de la resta anterior. Ademas,
## el signo debe cambiarse.
###
def resta(dato_in,cont,vel,ite):
	dato_in[0] -= incremento(cont,vel,ite)
	if dato_in[0] < 0:
		dato_in[0] = 65535 + dato_in[0]
		dato_in[1] = 'ffff'
	return dato_in


# Realiza los incrementos/decrementos de las operaciones matematicas en funcion
# de la velocidad y del numero de iteraciones que queden por realizarse.
#
# Si la operacion se ha realizado menos de doce veces, realizamos un incremento
# acumulativo de 1/12 en cada iteacion
#
# Si la operacion se ha realizado 12 veces y menos del maximo de iteraciones - 12,
# se realizaran incrementos iguales a la velocidad establecida.
#
# Si la operacion se va a realizar solo 12 veces mas, realizara una disminucion acumulativa
# del incremento de 1/12 parte de la velocidad.
#
# cont -> Numero de veces que se ha realizado la resta
# vel  -> Velocidad a la que se debe decrementar el valor restado
# ite  -> Numero de iteraciones que se van a realizar en total
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

# Calculo de la cinematica inversa del Scorbot para 3 GDL
#
# x   -> Posicion en el eje x
# y   -> Posicion en el eje y
# z   -> Posicion en el eje z
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

# Matriz de transformacion para la cinematica inversa
#
# d     -> Distancia en z entre centroides
# tita  -> Angulo entre ejes z
# a     -> Distancia en x entre centroides
# alpha -> Angulo entre ejes x
#
def matrizT(d, tita, a, alfa):
	T = array([[cos(tita), -cos(alfa)*sin(tita), sin(alfa)*sin(tita), a*cos(tita)],
			   [sin(tita), cos(alfa)*cos(tita), -sin(alfa)*cos(tita), a*sin(tita)],
			   [0, sin(alfa), cos(alfa), d],
			   [0, 0, 0, 1]])
	return T

# Transformacion de un angulo a valores de encoder y viceversa segun la articulacion
# Codigo de articulacion:
#  Cadera: 1
#  Hombro: 2
#  Codo  : 3
#  Pitch : 4
#  Roll  : 5
#
# Codigo de conversion:
#  0 -> De Encoder a Angulo
#  1 -> De Angulo a Encoder
#
# arti  -> Identificador de la articulacion
# con   -> Identificador de la conversion
# value -> Valor a transformar
#
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
			x = (20*value)/676
		elif conv == 1:
			x = (676*value)/20
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

# Calculo del numero de iteraciones en funcion de la velocidad y de la 'distancia'
# que debe recorrerse.
#
# enc  -> 'Distancia a recorrer'
# vel  -> Velocidad del movimiento
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
# Funciones de la interfaz grafica
###############################################################################

# Lee el valor de los encoders y los muestra en la ventana gráfica
#
# self   -> Puntero a la interfaz gráfica
# buffer -> Vector que almacena los datos de la ultima lectura
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


# Deshabilita y habilita los botones según estén los motores ON u OFF
#
# self   -> Puntero a la interfaz gráfica
# state  -> Habilita o deshabilita los botones de la GUI
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

#Escribe en la GIU el estado funcional del robot
#
# self   -> Puntero a la interfaz gráfica
# type   -> Indica si es un mensaje de error o un mensaje de final de ejecucion
# msg    -> Mensaje a mostrar en la interfaz grafica
#
def send_textlabel(self, type, msg):
	if type == 0:
		self.mainLabel.setText(info_text(msg))
		self.mainLabel.repaint()
	else:
		self.mainLabel.setText(error_msg(msg))
		self.mainLabel.repaint()


# Envia el mensaje a la controladora de apagado o encendido de motores.
#
# self       -> Puntero a la interfaz gráfica
# orden      -> Orden a ejecutar
# cola_orden -> Cola con la orden a procesar y/o feedback del funcionamiento
# buffer     -> Vector que almacena los datos de la ultima lectura
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

# Recoge las ordenes del usuario y las transmite al hilo 2 del programa.
#
# self       -> Puntero a la interfaz gráfica
# orden      -> Orden a ejecutar
# cola_orden -> Cola con la orden a procesar y/o feedback del funcionamiento
# buffer     -> Vector que almacena los datos de la ultima lectura
#
def write_data(self,orden, cola_orden, buffer):
	print(orden)
	select = []
	#Accion de la instruccion 'exit'
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
	    #Accion para desactivar los motores
		if select[0] == 16:
			cola_orden.put(select)
			time.sleep(SLEEP)
			cola_orden.get()
			print("Motors off")
			logging.info(info_text(3))
			send_textlabel(self,0,3)
	    #Accion para activar los motores
		elif select[0] == 17:
			cola_orden.put(select)
			time.sleep(SLEEP)
			cola_orden.get()
			print("Motors on")
			logging.info(info_text(4))
			send_textlabel(self,0,4)

		# Accion para realizar el home
		elif select[0] == 18:
			print("Homing...")
			logging.info(info_text(5))
			send_textlabel(self,0,5)

		#Acción para realizar movimiento hacia un punto XYZ.
		elif select[0] == 19:
			posObj = []
			posObj.append(self.lineEdit.text())
			posObj.append(self.lineEdit_2.text())
			posObj.append(self.lineEdit_3.text())
			print(posObj)
			select.append(posObj)
			select.append(int(self.spinBox.value()))
			print(select)

        #Acciona la cadera
		elif select[0] == 4 or select[0] == 5:
			print("Moving base...")
			logging.info(info_text(6))
			send_textlabel(self,0,6)

        #Acciona el hombro
		elif select[0] == 6 or select[0] == 7:
			print("Moving shoulder...")
			logging.info(info_text(7))
			send_textlabel(self, 0, 7)

        #Acciona el codo
		elif select[0] == 8 or select[0] == 9:
			print("Moving elbow...")
			logging.info(info_text(8))
			send_textlabel(self, 0, 8)

        #Acciona el pivote de la muñeca
		elif select[0] == 10 or select[0] == 11:
			print("Moving wrist pitch...")
			logging.info(info_text(9))
			send_textlabel(self, 0 , 9)

        #Acciona el giro de la muñeca
		elif select[0] == 12 or select[0] == 13:
			print("Moving wrist roll...")
			logging.info(info_text(10))
			send_textlabel(self, 0, 10)

        #Acciona la apertura de la pinza
		elif select[0] == 14:
			print("Opening gripper...")
			logging.info(info_text(11))
			send_textlabel(self,0,11)

		#Acciona el cierra de la pinza
		elif select[0] == 15:
			print("Closing gripper...")
			logging.info(info_text(12))
			send_textlabel(self,0,12)

		control_error(self, select, cola_orden, buffer)


# Control de errores durante la ejecucion y final de la orden recibida
# Hace uso de la cola orden para enviar al hilo de comando la orden que tiene que
# llevar a cabo. Posteriormente recibe en result si la operacion fue realizada
# conformemente (DONE), y en caso contrario también recibe que tipo de error sucedió.
#
# self       -> Puntero a la interfaz gráfica
# select     -> Vector con todos los parametros para ejecutar la orden
# cola_orden -> Cola con la orden a procesar y/o feedback del funcionamiento
# buffer     -> Vector que almacena los datos de la ultima lectura
#
def control_error(self, select, cola_orden, buffer):
	if select[0] != 16 and select[0] != 17: #Descarta acciones de habilitar o deshabilitar motores.
		cola_orden.put(select)
		time.sleep(SLEEP)
		result = cola_orden.get()
		if result == DONE and select[0] != 18 and select[0] != 19:
			print("Motion complete")
			print("")
			logging.info(info_text(1)) #Movimiento finalizado
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
			logging.info(info_text(1)) #Movimiento finalizado
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


# Calcula los ángulos de las articulaciones y los muestra por la interfaz.
#
# self       -> Puntero a la interfaz gráfica
# buffer     -> Vector que almacena los datos de la ultima lectura
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
