"""SOLO para la paridad: grabar.py pone esta carpeta en PYTHONPATH de la instancia de prueba (comun.Instancia con
semilla). Python carga sitecustomize al arrancar, antes que run.py: fija la semilla del `random` global (semilla del
duelo de ocgcore y del cerebro en ocg_duel, sobres, piedra-papel-tijera...) para que los duelos de la paridad sean
reproducibles y se puedan comparar en vez de enmascararlos. No toca ningún módulo del servidor; sin la variable
YO2_PARIDAD_SEMILLA no hace nada."""
import os
import random

_s = os.environ.get("YO2_PARIDAD_SEMILLA")
if _s:
    random.seed(int(_s))
