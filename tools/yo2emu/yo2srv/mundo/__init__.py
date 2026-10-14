"""Mundo, mapas y avatares (paso E5 de la reestructura): bloques (bloques.py), NPC (npc.py), mundo de 2008 (mapas.py)
y avatar propio (avatar.py).

Importar este paquete no hace nada. run.py llama a `setup(SRV)` (idempotente) justo antes de importar los nombres de los
cuatro módulos; de ahí en adelante cada módulo lee del namespace de run.py a través de `srv.X` (REESTRUCTURA.md §2.3).
"""


def setup(srv):
    from . import avatar, bloques, mapas, npc
    avatar.setup(srv)
    bloques.setup(srv)
    mapas.setup(srv)
    npc.setup(srv)
    return srv
