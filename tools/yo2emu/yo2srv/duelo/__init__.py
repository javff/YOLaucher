"""Duelo (paso E6 de la reestructura): el puerto 5760 y la cola de cada conexión (puerto.py) y el estado de duelo por
sesión y el ciclo de vida de las conexiones de duelo (sesion_duelo.py). E8: la reserva del 0x6001 (reserva.py) y la
comprobación del mazo (mazo.py). El motor (ocg, traductor, lockstep, duelfin, pvp) vendrá en la fase 3.

Importar este paquete no hace nada. run.py llama a `setup(SRV)` (idempotente) justo antes de importar los nombres de los
dos módulos; de ahí en adelante cada módulo lee del namespace de run.py a través de `srv.X` (REESTRUCTURA.md §2.3).
"""


def setup(srv):
    from . import mazo, puerto, reserva, sesion_duelo
    sesion_duelo.setup(srv)
    puerto.setup(srv)
    reserva.setup(srv)
    mazo.setup(srv)
    return srv
