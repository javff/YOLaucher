"""Comprobación del mazo del duelo (paso E8 de la reestructura): la carga de research/seguridad_duelo/mazo.py y la orden de
consola `deck_check()`, salidas de run.py SIN cambiar la lógica.

Comprobación del mazo del duelo (08-10, research/seguridad_duelo/mazo.py y NOTES.md «Comprobación del mazo»). El
cliente manda su mazo (0x6601/0x6603) y hasta hoy el servidor se lo creía. DK lo mira contra el baúl y las reglas
del editor de 2008 (C) al llegar completo, antes del 0x61fe: apunta p["decks"]["historial"] y, si no cuadra, una
línea «[mazo]» y el evento «mazo_invalido» del panel. YO2_DECK_CHECK = off | avisar (por defecto) | bloquear
(NUESTRO, apagado: el duelo no empieza, 0x649b código 1 en el piedra-papel-tijera). En caliente: deck_check("…").

`DK` (el objeto `DeckCheck`, que recibe el namespace de run.py) vive en el namespace; `iniciar()` lo crea (o conserva el
que hubiera) y `duel_reply` lo lee con `getattr(srv, "DK", None)`.
"""
import os
import sys

srv = None      # el Server; lo fija setup(). Hasta entonces nada de este módulo se puede llamar.


def setup(servidor):
    """Enlaza el módulo con el Server (idempotente). arranque.py lo llama antes de usarlo."""
    global srv
    srv = servidor
    return servidor


def _load_mazo():
    import importlib.util as _ilu
    _sp = _ilu.spec_from_file_location("yo2_mazo", os.path.join(srv.HERE, "research", "seguridad_duelo", "mazo.py"))
    _m = _ilu.module_from_spec(_sp); _sp.loader.exec_module(_m); sys.modules["yo2_mazo"] = _m
    return _m

def iniciar():
    """DK en el namespace: el DeckCheck de mazo.py (None si no carga: sin comprobación del mazo)."""
    try:
        srv.DK = getattr(srv, "DK", None) or srv._load_mazo().DeckCheck(srv.ns)
    except Exception as _e:
        srv.log(f"[ERROR] [mazo] research/seguridad_duelo/mazo.py no cargado: {_e!r} (sin comprobación del mazo)")
        srv.DK = None

def deck_check(modo=None):
    """Consola: deck_check() apunta el modo y la cuenta de mazos vistos; deck_check("off" | "avisar" | "bloquear")
    lo cambia en caliente (el siguiente mazo ya lo usa). Devuelve el modo."""
    dk = getattr(srv, "DK", None)
    if dk is None:
        srv.log("[mazo] mazo.py NO cargado"); return None
    if modo is not None:
        old = dk.mode
        dk.set_mode(modo)
        srv.log(f"[mazo] modo {old} -> {dk.mode}")
    srv.log(f"[mazo] {dk.snapshot()}")
    return dk.mode
