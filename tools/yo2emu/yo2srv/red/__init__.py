"""Subpaquete de red de yo2srv (REESTRUCTURA.md 2.2): protocolo (proto.py, E1a), conexiones (conexion.py), servidor TCP
(servidor.py), puerta 5730 y tabla de puertos (puerta.py), respuestas por defecto (auto.py) y HTTP (http.py), todo E1b.

Importar este paquete no hace nada. run.py llama a `setup(SRV)` (idempotente) antes de importar los nombres de los
módulos; de ahí en adelante cada uno lee del namespace de run.py a través de `srv.X` (REESTRUCTURA.md §2.3).
"""


def setup(srv):
    from . import auto, conexion, http, puerta, servidor
    for m in (conexion, servidor, puerta, auto, http):
        m.setup(srv)
    return srv
