"""Administración del servidor (paso E8 de la reestructura): la consola de control 5799 con token (consola.py), la moderación
del anfitrión (moderacion.py: vetos, expulsar, cuentas, avisos, copias) y su API para el lanzador (gestion.py, 8791 + offset).
El panel web del owner (research/admin/, privado) usa moderacion.py y no es necesario para nada de esto.

Importar este paquete no hace nada. arranque.py llama a `setup(SRV)` (idempotente) antes de arrancar la consola; de ahí en
adelante el módulo lee del namespace de run.py a través de `srv.X` (REESTRUCTURA.md §2.3).
"""


def setup(srv):
    from . import consola, moderacion
    consola.setup(srv)
    moderacion.setup(srv)
    return srv
