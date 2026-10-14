"""Respuestas por defecto (paso E1b de la reestructura, REESTRUCTURA.md §2.2 y §3.4): `auto_replies.json` +
`research/lobby/auto_overrides.json` y `auto_reply`.

`auto_replies.json` lo saca gen_auto.py del propio cliente (ghidra/protocol_dump.py): para cada petición sin respuesta
propia, la respuesta que la desbloquea, con los datos a 0 y las listas vacías. `auto_overrides.json` (agente 3, con sus
evidencias en research/lobby/NOTES.md) corrige emparejamientos que gen_auto hacía mal y resultados que no pueden ir a
cero («hex» = datos exactos).

Importar este módulo NO abre ficheros (regla del paquete): `cargar()` lee los dos JSON y run.py lo llama donde antes
estaban las dos lecturas (así una re-ejecución de ese bloque de run.py, como hacía reload_lobby.py, vuelve a leerlos).
`auto_reply` lee `AUTO` del namespace en cada llamada (`srv.AUTO`): lo que alguien reasigne ahí manda.
"""
import json
import os

from .. import rutas

srv = None      # el Server; lo fija setup(). Hasta entonces nada de este módulo se puede llamar.


def setup(servidor):
    """Enlaza el módulo con el Server (idempotente). run.py lo llama (yo2srv.red.setup) antes de usar `auto_reply`."""
    global srv
    srv = servidor
    return servidor


def cargar(emu=None):
    """(AUTO, AUTO_OV): las respuestas por defecto ya con las correcciones de los `0x…` del segundo fichero encima.
    Un fichero que falta (OSError) deja {} como antes; uno que no es JSON lanza, como antes."""
    emu = emu or rutas.EMU
    try:
        with open(os.path.join(emu, "auto_replies.json"), encoding="utf-8") as f:
            AUTO = json.load(f)
    except OSError:
        AUTO = {}
    # Correcciones del agente 3 (research/lobby/auto_overrides.json, evidencias en su NOTES.md): emparejamientos que
    # gen_auto hacía mal y resultados que no pueden ir a cero («hex» = datos exactos).
    try:
        with open(os.path.join(emu, "research", "lobby", "auto_overrides.json"), encoding="utf-8") as f:
            AUTO_OV = json.load(f)
        AUTO.update({k: v for k, v in AUTO_OV.items() if k.startswith("0x")})
    except OSError:
        AUTO_OV = {}
    return AUTO, AUTO_OV


_FMT_SIZE = {"u8": 1, "u16": 2, "u32": 4, "s32": 4, "cstr": 1}


def auto_reply(c, msg_id):
    """Manda la respuesta por defecto de AUTO; devuelve False si no hay ninguna conocida."""
    reps = srv.AUTO.get("0x%04x" % msg_id)
    if not reps:
        return False
    for r in reps:
        n = sum(_FMT_SIZE.get(f, 0) or int(f[5:] if f[5:].isdigit() else 0) for f in r["fmt"])
        body = bytes.fromhex(r["hex"]) if r.get("hex") else bytes(n)
        srv.log(f"[srv:{c.port}] auto 0x{msg_id:04x} -> {r['id']} {r['name']} ({body.hex() if r.get('hex') else f'{n} B a cero'})")
        c.send(int(r["id"], 16), body)
    return True
