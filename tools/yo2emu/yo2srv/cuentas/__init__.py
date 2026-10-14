"""Cuentas y personajes (paso E3 de la reestructura): la sesión del hilo y la vista por cuenta (sesion.py) y los
personajes de la cuenta, 0x3010/0x3020/0x3030/0x3100/0x3114 (personajes.py). El login y el cifrado vendrán en la fase 3.

Importar este paquete no hace nada. run.py llama a `setup(SRV)` (idempotente) justo antes de importar los nombres de los
dos módulos; de ahí en adelante cada módulo lee del namespace de run.py a través de `srv.X` (REESTRUCTURA.md §2.3).
"""


def setup(srv):
    from . import personajes, sesion
    sesion.setup(srv)
    personajes.setup(srv)
    return srv
