# Autores: Jose Luis Pérez Pérez y Yolanda M. Gimeno Rodríguez
# Fecha:
# Título: Generacion del archivo .json
# Universidad de La Laguna

import json
from math import pi
from pathlib import Path


CONFIG_PATH = Path(__file__).with_name("data.json")

#################################################################################
# Script que genera el archivo json con los parametros de funcionamiento estandar
# del programa
#################################################################################

# Funcion para extraer informacion del archivo .json con las variables
#
# a   -> Grupo en el que esta la variable
# b   -> Nombre de la variable
#
def readData(a,b):
    if not CONFIG_PATH.exists():
        setup()
    with CONFIG_PATH.open('r', encoding='utf-8') as f:
        data = json.load(f)
    return data[a][b]

# Libreria con las variables globales
#
def setup():
    try:
        info ={
            #Variables de caracter general
            "general":{
                # Longitud maxima de un mensajes
                "MSG_LEN": 128,
                # Valor maximo del byte de secuencia
                "MAX_COUNT": 256,
                # Error maximo aceptable en los bytes de error
                "MAX_ERROR": 40,
                #
                "ite": 100,
                # Time_out para el endpoint de salida
                "TIME_OUT_W" : 1500,
                # Time_out para el endpoint de entrada
                "TIME_OUT_R" : 1500,
                # Identificacion de que no hubo errores durante la accion
                "DONE" : 0,
                # Identificacion de que se va a cerrar el programa
                "EXIT" : 528,
                # Posiciones de los datos de cada motor dentro del buffer
                #   [cadera, hombro, codo, m1_muñeca, m2_muñeca, pinza]
                "VEC_POS":[19 ,24, 29, 34, 39, 44],
                # Posiciones de los bytes de error de cada motor dentro del buffer
                #   [cadera, hombro, codo, m1_muñeca, m2_muñeca, pinza]
                "VEC_ERROR":[22,27,32,37,42,47],
                # Tiempo de espera para realizar una peticion de lectura tras la escritura
                "WRITE": 0.008,
                # Tiempo de espera para empezar a generar el siguiente mensaje a enviar tras
                # una  peticion de lectura.
                "READ": 0.005,
                # Minimo valor de la franja superior de valores posibles
                "upLimit": 50000,
                # Maximo valor de la franja inferior de valores posibles
                "downLimit": 20000, # " " " " " "
                # Posicion inicial relativa de cadera,hombro,codo en valores de encoders
                # tras el HOME
                "posRef": [0,10350,55401],
                # Posicion inicial relativa de las articulaciones en valores de angulo
                # tras el HOME
                "angRef": [0, 90, -90, 0, 0],
                # Longitudes de los links
                "longitudes":[364,220,220],
                # d de la cinematica inversa
                "link-offset":[364,0,0,0,145.125],
                # a de la cinematica inversa
                "link-length":[16,220,220,0,0],
                # alpha de la cinematica inversa
                "link-twist-angle":[pi/2,0,0,pi/2]
            },
            #Variables relacionadas con la articulacion de la cadera
            "cadera":{
                # Velocidad de la articulacion en el HOME
                "h_vel": 20,
                # Identificador del microinterruptor
                "switch": 1,
                # Tiempo de espera para realizar una peticion de lectura tras la escritura
                "write": 0.008,
                # Tiempo de espera para empezar a generar el siguiente mensaje a enviar tras
                # una  peticion de lectura.
                "read" : 0.012
            },
            #Variables relacionadas con la articulacion del hombro
            "hombro":{
                # Velocidad de la articulacion en el HOME
                "h_vel": 10,
                # Identificador del microinterruptor
                "switch" : 2,
                # Tiempo de espera para realizar una peticion de lectura tras la escritura
                "write": 0.008,
                # Tiempo de espera para empezar a generar el siguiente mensaje a enviar tras
                # una  peticion de lectura.
                "read" : 0.012
            },
            #Variables relacionadas con la articulacion del codo
            "codo":{
                # Velocidad de la articulacion en el HOME
                "h_vel": 20,
                # Identificador del microinterruptor
                "switch" : 4,
                # Tiempo de espera para realizar una peticion de lectura tras la escritura
                "write": 0.008,
                # Tiempo de espera para empezar a generar el siguiente mensaje a enviar tras
                # una  peticion de lectura.
                "read" : 0.012
            },
            #Variables relacionadas con la articulacion de la muñeca
            "wrist":{
                # Velocidad de la articulacion en el HOME
                "h_vel": 10,
                # Identificador del microinterruptor
                "switch_roll" : 16,
                # Identificador del microinterruptor
                "switch_pitch" : 8,
                # Tiempo de espera para realizar una peticion de lectura tras la escritura
                "write": 0.008,
                # Tiempo de espera para empezar a generar el siguiente mensaje a enviar tras
                # una  peticion de lectura.
                "read" : 0.013
            },
            #Variables relacionadas con la articulacion de la pinza
            "pinza":{
                # Velocidad del movimiento. Valor constante
                "vel" : 150,
                # Tiempo de espera para realizar una peticion de lectura tras la escritura
                "write" : 0.008,
                # Tiempo de espera para empezar a generar el siguiente mensaje a enviar tras
                # una  peticion de lectura.
                "read" : 0.019,
                # Numero de iteraciones que dura el movimiento. Valor constante
                "ite_clamp" : 30
            }
        }

        f = json.dumps(info, indent=2)

        if not CONFIG_PATH.exists():
            with CONFIG_PATH.open('w', encoding='utf-8') as outfile:
                outfile.write(f)
    except OSError as exc:
        raise RuntimeError("Could not create the robot configuration") from exc
