# Autores: Jose Luis Pérez Pérez y Yolanda M. Gimeno Rodríguez
# Fecha: 08/07/2020
# Título: Script Main del Open Scorbot.
# Universidad de La Laguna

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

# Por defecto el programa entrará en estado online
online = True

# Cola para almacenar el byte de sincronizacion
cola_sync = queue.Queue()
# Cola para indicar ordenes pendientes de realizarse
cola_orden = queue.Queue()
# Cola con el vector de la media de lecturas
cola_read = queue.Queue()

buffer = 0

# Lanza la interfaz gráfica y la conexion
def main():
	set_conection()
	app = QtWidgets.QApplication(sys.argv)
	main = MainWindow()
	main.show()
	sys.exit(app.exec_())

def set_conection():
	global online
	#########################################################################
	# Buscamos el brazo con los identificadores del dispositivo
	try:
		dev = usb.core.find(idVendor = 0x09f1, idProduct = 0x0007)
	except usb.core.USBError:
		print("error dev")
		online = False

	# Si no esta conectado, salimos del programa
	if not dev or online == False:
		print("Device not found")
		logging.warning(libdef.error_msg(12)) #Dispositivo no encontrado
		online = False
	else:
		print("Device found")
		logging.debug(libdef.info_text(15)) #Dispositivo encontrado
		# Guardamos los datos de la interfaz 0 del dispositivo 0
		i = dev[0].interfaces()[0].bInterfaceNumber

		# Reseteamos para tomar el control
		try:
			dev.reset()
		except usb.core.USBError:
			print("entity not found")
			logging.warning(libdef.error_msg(13)) #Entity not found
			online = False
			return -1

		# Desacoplamos el brazo del kernel
		if dev.is_kernel_driver_active(i):
		    dev.detach_kernel_driver(i)

		# Cogemos los datos de la configuracion
		cfg = dev.get_active_configuration()
		# Cogemos los datos de los endpoints de la configuracion
		intf = cfg[(0,0)]

		# Buscamos el ENDPOINT de entrada en la configuracion
		epin = usb.util.find_descriptor(
		    intf,
		    custom_match = \
		    lambda e: \
		        usb.util.endpoint_direction(e.bEndpointAddress) == \
		        usb.util.ENDPOINT_IN)

		# Buscamos el ENDPOINT de salida en la configuracion
		epout = usb.util.find_descriptor(
				    intf,
				    custom_match = \
				    lambda e: \
				        usb.util.endpoint_direction(e.bEndpointAddress) == \
				        usb.util.ENDPOINT_OUT)

		# Creamos el buffer para almacenar las respuestas con el tamaño que permite
		# el EP de entrada
		global buffer

		buffer= usb.util.create_buffer(epin.wMaxPacketSize)

		# Cargamos el archivo json que contiene la configuracio propia para cada
		# articulacion y demas aspectos que intervienen en el programa
		conf.setup()

		# Lanzamos los primeros mensajes e iniciamos el valor del byte de secuencia
		# y el vector que continue el valor media de la posicion de cada encoder
		print('Connecting...')
		logging.info(libdef.info_text(16))
		ans = libsync.msg_start(epout,epin,buffer)
		print('Connected')
		logging.info(libdef.info_text(17))

		# Separamos el valor del byte de secuencia
		b_1 = ans[0]

		# Separamos el vector con los valores de la media
		media = ans[1]

		# Introducimos el byte de secuencia b_1 en la cola de syncro
		# y la media en la cola read
		cola_sync.put(b_1)
		cola_read.put(media)

		# Hilo principal de sincronizacion
		h1	= threading.Thread(target = libsync.syncro, args = [cola_sync, cola_read, epout, epin, buffer])
		# Hilo de ejecucion de ordenes
		h2	= threading.Thread(target = libcomm.execute, args = [cola_sync, cola_orden, cola_read, epout, epin, buffer])

		# Iniciacion de los hilos
		h1.start()
		h2.start()

# Clase que define la ventana gráfica del programa.
# Realizado en PyQt5
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

# Revisa que la conexion ha sido realizada con exito. En caso contrario,
# desactiva las diferentes funciones de la interfaz excepto el botón de exit.
def check_conection(self):
	if online == False:
		self.pushbutton_online.setEnabled(False)
		self.pushbutton_offline.setEnabled(False)
		self.pushbutton_online.setStyleSheet("background-color: rgb(240,240,240)")
		# Menasaje de aviso del error.
		libdef.send_textlabel(self, 1, 12)
		libdef.stateButtons(self,False)

# Cierra la ventana gráfica
def exit(self):
	QtWidgets.QApplication.quit()
	libdef.write_data(self,'exit', cola_orden, buffer)
