"""Tienda y cartera (paso E4 de la reestructura): catálogo y compra (tienda.py), saldo y 0x4140/0x4101 (cartera.py); E8:
la elección de la carta del sobre tras un duelo (sobres.py).

Importar este paquete no hace nada. run.py llama a `setup(SRV)` (idempotente) justo antes de importar los nombres de
los dos módulos; de ahí en adelante cada módulo lee del namespace de run.py a través de `srv.X` (REESTRUCTURA.md §2.3).
"""


def setup(srv):
    from . import cartera, sobres, tienda
    tienda.setup(srv)
    cartera.setup(srv)
    sobres.setup(srv)
    return srv
