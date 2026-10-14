"""Compatibilidad con los nombres de hoy (REESTRUCTURA.md §2.2 y §2.3).

1. ALIAS: nombre nuevo (minúsculas, el que usará el código reestructurado: `srv.hub`) -> nombre de hoy en el namespace
   de run.py (`HUB`). `Server.__getattr__/__setattr__` lo consultan: `srv.hub` es `ns["HUB"]` y `srv.HUB` también (lo que
   no está en la tabla se busca con su nombre tal cual). La tabla solo crece; ningún nombre de hoy se renombra en el
   namespace (la consola 5799, el panel y los hot_*.py los siguen usando: paridad/contrato_ns.txt).

2. alias_modulo(): el cargador de alias de módulos. Cuando un fichero se mueva al paquete (fase 3), el viejo pasa a ser
   `import sys, importlib; sys.modules[__name__] = importlib.import_module("yo2srv....")` y este helper lo hace con un
   solo nombre. OJO (plan §Fase 3): vale para `import x`, NO para `spec_from_file_location(...).exec_module(...)`,
   que devolvería el módulo vacío; cada carga por ruta se cambia en el mismo paso.

3. cargar(): UN solo objeto de módulo por área de research (paso U1). Cada research/<area>/proposal.py vive en
   sys.modules con el nombre `research_<area>`; lo cargan por igual el arranque del servidor (`srv.LS`, `srv.PP`), el
   traductor (los nombres de lockstep que importa) y duelfin (`DFM.LS`, `DFM.PS`), así que ya no hay copias con su propio
   estado de módulo. Fuera del servidor (pruebas y herramientas que cargan translator.py o duelfin/proposal.py por ruta)
   el primero que pide el área la carga y los demás reciben ese mismo objeto.
"""
import importlib
import importlib.util
import os
import sys
import threading

EMU = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))   # tools/yo2emu
_LOCK_CARGA = threading.RLock()

ALIAS = {
    # servicios y objetos compartidos
    "ctx": "CTX",
    "state_lock": "STATE_LOCK",
    "store": "STORE",
    "hub": "HUB",
    "pvp": "PVP",
    "conns": "CONNS",
    "peer_conns": "PEER_CONNS",
    "duel": "DUEL",
    "lobby_room": "LOBBY_ROOM",
    "av_state": "AV_STATE",
    "npcs": "NPCS",
    "handlers": "HANDLERS",
    "player_id": "PLAYER_ID",
    "deadline": "DEADLINE",
    "auto": "AUTO",
    "ph": "PH",
    "ph_game": "PH_GAME",
    "ph_lobby": "PH_LOBBY",
    "df": "DF",
    # módulos de research (nombre corto de hoy)
    "lobby": "LP",
    "equipos": "EQ",
    "intercambio": "TP",
    "torneos": "TO",
    "mundo": "MW",
    "avatar": "AV",
    "npcreglas": "NR",
    "perfiles": "PP",
    "duelpass": "DPC",
    "duelfin": "DFM",
    "lockstep": "LS",
    "ocg": "OCG",
    "panel": "ADMIN_WEB",
}


def nombre_ns(nombre):
    """Nombre con el que vive `nombre` en el namespace de run.py."""
    return ALIAS.get(nombre, nombre)


def alias_modulo(viejo, nuevo):
    """Hace que `import <viejo>` devuelva EL MISMO objeto de módulo que `<nuevo>`. Devuelve el módulo."""
    m = importlib.import_module(nuevo)
    sys.modules[viejo] = m
    return m


def nombre_research(area):
    """Nombre en sys.modules del proposal.py de research/<area> (el mismo que le da arranque.load_research)."""
    return "research_" + area


def cargar(area, fresco=False, base=None):
    """El módulo de research/<area>/proposal.py con su nombre canónico `research_<area>`.

    Sin `fresco`: el que ya esté en sys.modules y, si no hay ninguno, se carga UNA vez y queda ahí (todos los que lo piden
    comparten el estado de módulo: DUEL_CFG de lockstep, RAW_STATE y las banderas de persistencia…). Con `fresco=True` se
    ejecuta de nuevo y sustituye al anterior (es lo que hace arranque.load_research al arrancar el servidor y lo que hará
    una recarga en caliente); los que lo pidan DESPUÉS reciben el nuevo. `base` = tools/yo2emu (por defecto, el de este
    paquete). Si el fichero falla al ejecutarse no queda nada a medias en sys.modules."""
    nombre = nombre_research(area)
    with _LOCK_CARGA:
        if not fresco:
            m = sys.modules.get(nombre)
            if m is not None:
                return m
        ruta = os.path.join(base or EMU, "research", area, "proposal.py")
        spec = importlib.util.spec_from_file_location(nombre, ruta)
        mod = importlib.util.module_from_spec(spec)
        previo = sys.modules.get(nombre)
        sys.modules[nombre] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            if previo is None:
                sys.modules.pop(nombre, None)
            else:
                sys.modules[nombre] = previo
            raise
        return mod
