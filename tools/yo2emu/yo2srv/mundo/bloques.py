"""Bloques del mapa (paso E5 de la reestructura, REESTRUCTURA.md §3.4): a qué bloque del HUB pertenece una conexión
(`block_key`), quién más está en él, y las entradas de la lista de jugadores del bloque (0x4211-0x4213 y 0x4212).

Salido de run.py SIN cambiar la lógica: solo cambia de dónde sale cada nombre. Todo lo que alguien puede reasignar o
parchear en caliente (`HUB`, `PP`, `CTX`, `load_state`, `my_pid`, `my_name`, `log`, las propias funciones de este
módulo que un hot_*.py sustituya en el namespace…) se lee SIEMPRE como `srv.X` en el momento de usarlo (§2.3), es decir,
de `srv.ns` = `globals()` de run.py. run.py importa de aquí los MISMOS nombres, así que la consola 5799, el panel,
pvp_hub y research/lobby (que reciben `ACAD_KINDS`, `block_player`… del namespace) los siguen encontrando donde siempre.

`room_lang` y `ROOM_LANG_MERGE` (idioma de la clave del bloque, master 526d16ac) viven aquí desde E8 (estaban en run.py);
los usa también la puerta (`gate_reply`, paso E1b) y `block_key` los llama como `srv.room_lang`.
"""
import os

from yo2srv.red import proto

srv = None      # el Server; lo fija setup(). Hasta entonces nada de este módulo se puede llamar.


def setup(servidor):
    """Enlaza el módulo con el Server (idempotente). run.py lo llama justo antes de importar los nombres de aquí."""
    global srv
    srv = servidor
    return servidor


# 08-10: el idioma de 0x4100 es el del CLIENTE (francés = 3, español = 5), no el de la sala elegida en la puerta (todas
# las salas de un tipo van al mismo puerto, así que el servidor no sabe cuál eligió). Con la clave (puerto, idioma del
# cliente), un portátil en francés y un PC en español en «la misma» sala no se veían. Con ROOM_LANG_MERGE (por defecto;
# YO2_ROOM_LANG_MERGE=0 vuelve a separar) todos los de un tipo de sala comparten bloque (idioma 0 en la clave).
# 10-10: el oficial separaba por SALA (C: el cliente conecta al IP:puerto de la entrada b1/b2/b3 que eligió y no manda
# b1 en ningún mensaje). Con ROOM_PER_LANG (yo2srv/red/puerta.py) el puerto dice el idioma de la sala y la clave lleva
# ese idioma: ROOM_LANG_MERGE ya no se usa (solo cuenta con ROOM_PER_LANG apagada).
ROOM_LANG_MERGE = os.environ.get("YO2_ROOM_LANG_MERGE", "1") not in ("", "0")

def room_lang(lang):
    """Idioma de la clave del bloque: 0 para todos con ROOM_LANG_MERGE; si no, el del cliente."""
    return 0 if srv.ROOM_LANG_MERGE else lang

ACAD_KINDS = {14, 1, 9, 16, 17, 10, 20}   # puerta roja: Academia, TRIAL, EVOLUTION, ANIVERSARIO, ÚLTIMO COMBATE, Principiantes
ACAD_AREA, DOMINO_AREA = 5, 0             # área de entrada de cada mundo (0x4203; ver el bloque 0x4202)

def block_key(c):
    """Bloque compartido del HUB: (puerto de la sala, idioma). My Room (LOBBY_PORT, tipo 12) es de cada uno: None.
    El puerto de la clave es siempre ROOM_PORT_BASE + i (el tipo); el idioma, el de la sala con ROOM_PER_LANG (el
    puerto lo dice) o room_lang(idioma del cliente) sin ella: srv.room_block_key (yo2srv/red/puerta.py)."""
    if c.port == srv.LOBBY_PORT:
        return None
    rbk = srv.ns.get("room_block_key")
    if rbk is None:     # este módulo cargado en caliente sobre un run.py sin red/puerta.py nuevo: como antes
        if not (srv.ROOM_PORT_BASE <= c.port < srv.ROOM_PORT_BASE + len(srv.ROOM_KINDS)):
            return None
        return c.port, srv.room_lang(getattr(c, "lang", 0))
    return rbk(c.port, getattr(c, "lang", 0))

def hub_area(name, default, *a):
    """Método de áreas de HUB (pvp_hub.AREA_BLOCKS) o «default» si HUB es de antes (reload_lobby sin reiniciar)."""
    f = getattr(srv.HUB, name, None)
    return f(*a) if f is not None else default

def block_others(c):
    """Los otros jugadores del bloque de c (solo si c es la conexión de lobby de su sesión y está en ese bloque)."""
    s = getattr(c, "sess", None)
    key = srv.block_key(c)
    if s is None or key is None or s.block != key or s.lobby is not c:
        return []
    return srv.HUB.others(s)

def send_block_list(c):
    """0x4211 + 0x4212 (yo primero y luego los demás del bloque, 36 B cada uno, <= 28 por mensaje) + 0x4213. El
    valor de 0x4211 se queda en 1 como siempre (FUN_006b88d0 lo guarda en +0x7f4 y vacía la lista)."""
    hub = srv.HUB
    entries = [srv.block_player(srv.my_pid())]
    view = {}                                    # lo que esta LIST1 dice de cada uno, para corregirlo si cambia mientras sale
    for o in srv.block_others(c):
        view[o.pid] = hub.view_of(o) if hasattr(hub, "view_of") else None
        entries.append(hub.list1_entry(o))
    c.send(0x4211, proto.W().u32(1).bytes())
    for i in range(0, len(entries), 28):
        c.send(0x4212, b"".join(entries[i:i + 28]))
    c.send(0x4213)
    # 24-10 (research/lobby/NOTES.md §9c): la LIST1 se arma ANTES de mandarla (leer el perfil de cada uno del disco, bajo el
    # candado del estado, y 0,25 s de pausa por mensaje) y mientras tanto los avisos de los demás (0x3112, por su cola) siguen
    # saliendo. El cliente vacía su lista con 0x4211 y escribe lo que trae 0x4212: si el aviso de «ya está libre» llega antes
    # que una LIST1 hecha cuando aún estaba «en duelo», el estado viejo gana y no hay «Retar» hasta salir y volver a entrar.
    # Lo que cambió desde que se miró a cada uno se vuelve a mandar detrás.
    s, key = getattr(c, "sess", None), srv.block_key(c)
    if (hasattr(hub, "reconcile") and s is not None and key is not None and s.block == key and s.lobby is c
            and None not in view.values()):                  # (las mismas condiciones que block_others)
        for mid, data in hub.reconcile(s, view):
            c.send(mid, data, pause=0.02)

def block_player(pid, name=None, sess=None):
    """Entrada de 0x4212 BLOCKPLAYERLISTDATA (FUN_006b8b70), 36 B: u32 id, nombre16, u8, u16 x4, u8, cadena4, u8,
    u8 (= índice del bloque, +0x131; debe coincidir con el de ENTERBLOCKRESULT o el mapa no crea el avatar).
    Prueba 2 (LV 0 en la lista y en la ficha): FUN_006b8c30 copia el u8 tras el nombre a reg+0xa4 = LV, los u16 a
    +0xb2 victorias, +0xba derrotas, +0x112 título (FUN_0068fc50) y +0x116 (sin lecturas) — mismos campos que
    PSELECTRESULT (research/persistencia NOTES §PSELECT). Iban a 0: «Lv 0 / Duelista principiante». Ahora salen del
    perfil de ESA sesión (sess; sin ella, la del hilo)."""
    name = name or srv.my_name()
    lv = wins = losses = title = 0
    country = srv.DEFAULT_COUNTRY
    old = getattr(srv.CTX, "sess", None)
    try:
        if sess is not None:
            srv.CTX.sess = sess
        st = srv.load_state()
        slot = sess.slot if sess is not None and sess.slot is not None else srv.my_slot(st)
        if srv.ns.get("PP") is not None:
            v = srv.PP.rec_values(srv.PP.profile(st, slot, create=False) or {})
            lv, wins, losses, title = (int(v.get(k, 0) or 0) for k in ("lv", "wins", "losses", "title"))
            country = str((srv.PP.profile(st, slot, create=False) or {}).get("country") or country)
    except Exception as e:
        srv.log(f"[lista] datos de {pid} para LIST1: {e!r}")
    finally:
        srv.CTX.sess = old
    return (proto.W().u32(pid).s(name, 16).u8(min(max(lv, 0), 255)).u16(wins & 0xffff).u16(losses & 0xffff)
            .u16(title & 0xffff).u16(0).u8(0).s(country[:3], 4).u8(0)
            .u8(srv.hub_area("ablock_of", 0, sess if sess is not None else old)).bytes())   # bloque (AREA_BLOCKS)

def block_type(c):
    """(b1, b2) del bloque según el puerto: My Room = (0, 12); sala = (idioma, tipo de sala). Idioma = el de la sala con
    ROOM_PER_LANG (el b1 que eligió el cliente en la puerta: lo dice el puerto); sin ella, el del cliente (0x4100)."""
    if c.port == srv.LOBBY_PORT:
        return 0, 12
    rop = srv.ns.get("room_of_port")   # None = cargado en caliente sin red/puerta.py nuevo (como antes)
    r = rop(c.port) if rop is not None else None
    if r is None:
        return getattr(c, "lang", 0), srv.ROOM_KINDS[c.port - srv.ROOM_PORT_BASE]
    lang, i = r
    return (getattr(c, "lang", 0) if lang is None else lang), srv.ROOM_KINDS[i]
