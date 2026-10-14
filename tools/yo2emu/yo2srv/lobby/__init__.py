"""Lobby (paso E7 de la reestructura): `lobby_reply` y sus tablas (lobby.py). El resto del lobby (presencia, salas, retos,
chat, equipos, intercambio, torneos) sigue donde estaba y viene en la fase 3.

Importar este paquete no hace nada. run.py llama a `setup(SRV)` (idempotente) justo antes de importar los nombres de
lobby.py; de ahí en adelante el módulo lee del namespace de run.py a través de `srv.X` (REESTRUCTURA.md §2.3).
"""


def setup(srv):
    from . import lobby
    lobby.setup(srv)
    return srv
