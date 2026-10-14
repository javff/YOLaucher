"""La puerta 5730 y la tabla de puertos (paso E1b de la reestructura, REESTRUCTURA.md §2.2 y §3.4).

Salido de run.py SIN cambiar la lógica:
  - `puertos(offset)`: GATE_PORT 5730, GAME_PORT 5731, LOBBY_PORT 5732, ROOM_PORT_BASE 5740 (sala de tipo ROOM_KINDS[i]
    -> puerto 5740+i: así el servidor sabe en qué sala entró el cliente), HTTP_PORT 8730 y ADMIN_PORT 5799, todos más
    YO2_PORT_OFFSET. Es una FUNCIÓN y no constantes del módulo para que importar este módulo no fije el desplazamiento
    (una prueba que ejecute run.py dos veces con otro YO2_PORT_OFFSET en el mismo proceso lo vería cacheado).
  - 10-10, salas por idioma (`ROOM_PER_LANG`, YO2_ROOM_PER_LANG, apagada por defecto): `room_port`, `room_of_port`,
    `room_ports` y `room_block_key` (research/lobby/NOTES.md, «Salas por idioma 1:1»).
  - `ROOM_KINDS` y `GATE_PAUSE`: constantes.
  - `gate_reply`: 0x2005 -> 0x2002, 0x2003 × N, 0x2004.

Dentro de `gate_reply` todo lo que se puede reasignar (puertos, IP pública, `HUB`) y `room_lang` (de master 526d16ac:
con ROOM_LANG_MERGE el idioma de la clave del bloque es 0 para todos, y la lista de la puerta cuenta jugadores por ese
mismo bloque) se lee como `srv.X` en cada llamada; `room_lang`, `ROOM_LANG_MERGE` y `block_key` viven desde E8 en
yo2srv/mundo/bloques.py. run.py (arranque.py) importa de aquí los MISMOS nombres.
"""
import os

from . import proto

srv = None      # el Server; lo fija setup(). Hasta entonces nada de este módulo se puede llamar.


def setup(servidor):
    """Enlaza el módulo con el Server (idempotente). run.py lo llama (yo2srv.red.setup) antes de importar `gate_reply`."""
    global srv
    srv = servidor
    return servidor


def puertos(offset=0):
    """(GATE_PORT, GAME_PORT, LOBBY_PORT, ROOM_PORT_BASE, HTTP_PORT, ADMIN_PORT) con el desplazamiento de puertos."""
    return (5730 + offset, 5731 + offset, 5732 + offset,
            5740 + offset,   # sala de tipo ROOM_KINDS[i] -> puerto 5740+i: así el servidor sabe en qué sala entró el cliente
            8730 + offset, 5799 + offset)


GATE_PAUSE = 0.03   # la lista de la puerta va seguida (ver gate_reply)
ROOM_KINDS = (13, 7, 15, 8, 14, 1, 9, 16, 17, 10, 20)   # b2: puerta azul 13,7,15,8; roja 14,1,9,16,17,10,20


# ---- Salas por idioma 1:1 (10-10; research/lobby/NOTES.md «Salas por idioma 1:1»). Lo que hace el cliente (C):
# CMyRoom2MapTask (FUN_0082d9f0 / FUN_0082d750) guarda el idioma elegido (b1 = «rgn»), el tipo (b2 = «rank») y el nº de
# sala (b3 = «area») en CNetLobby +0x9ec/+0x9ed/+0x9ee (FUN_006d3ca0) y FUN_006abe10 busca en la lista de la puerta la
# entrada de tipo 3 con ESOS tres bytes («接続先ロビーサーバはコレですね！！・・・rgn=%d,rank=%d,area=%d») y conecta a SU IP y
# SU puerto (+0x2e). Ningún mensaje lleva +0x9ec al servidor (solo lo leen esa búsqueda y 0x5000 EVENTINFO, que lo
# compara con su «mundo»): el servidor sabe la sala solo por dónde se conectó el cliente. El 0x4100 lleva el idioma
# del CLIENTE. Por eso, con ROOM_PER_LANG, cada (idioma, tipo) tiene su puerto:
#   - mundial (b1 = 6): los puertos de siempre, ROOM_PORT_BASE + i (5740-5750), donde se juntan todos los idiomas;
#   - idioma L != 6: ROOM_LANG_PORT_BASE + franja(L) * len(ROOM_KINDS) + i, franja = L (0-5) o 6 (coreano, 7):
#     7 × 11 = 77 puertos, 5800-5876 (+ YO2_PORT_OFFSET). En 5740-5750 no caben 88 salas (11 puertos).
# La clave del bloque sigue siendo (puerto «canónico» ROOM_PORT_BASE + i, idioma): así pvp_hub, pvp_match, npc_watch,
# el panel y research/lobby, que sacan el tipo con ROOM_KINDS[clave[0] - ROOM_PORT_BASE], no cambian; el idioma de la
# clave es el de la SALA (b1), no el del cliente, y ROOM_LANG_MERGE deja de contar. Apagada = como antes (un puerto por
# tipo y la clave con room_lang(idioma del cliente)). Hay que reiniciar: los puertos se abren al arrancar.
ROOM_PER_LANG = os.environ.get("YO2_ROOM_PER_LANG", "0") not in ("", "0")
WORLD_LANG = 6        # b1 de la sala mundial (FUN_00a54b80: texto 2017) — C
ROOM_LANGS = tuple(range(8))   # b1 0-7 (FUN_00a54b80; > 7 = «unknown») — C


def puerto_idiomas(offset=0):
    """ROOM_LANG_PORT_BASE: primer puerto de las salas de idioma (5800 + YO2_PORT_OFFSET). Ver el bloque de arriba."""
    return 5800 + offset


def _per_lang():
    return bool(srv.ns.get("ROOM_PER_LANG"))


def _franja(lang):
    return lang if lang < WORLD_LANG else lang - 1


def room_ports():
    """Puertos de sala que se escuchan, en orden: los ROOM_PORT_BASE + i de siempre y, con ROOM_PER_LANG, los 77 de
    idioma. Lo usan tabla_puertos y comprobar_puertos (red/servidor.py)."""
    n = len(srv.ROOM_KINDS)
    ps = [srv.ROOM_PORT_BASE + i for i in range(n)]
    if _per_lang():
        b = srv.ROOM_LANG_PORT_BASE
        ps += [b + f * n + i for f in range(len(ROOM_LANGS) - 1) for i in range(n)]
    return ps


def room_port(lang, kind):
    """Puerto que se anuncia en 0x2003 para la sala (b1 = lang, b2 = kind). Sin ROOM_PER_LANG, o para la mundial, el de
    siempre; si el de idioma no está en HANDLERS (bandera encendida en caliente sin reiniciar) también el de siempre."""
    i = srv.ROOM_KINDS.index(kind)
    if _per_lang() and lang != WORLD_LANG and lang in ROOM_LANGS:
        p = srv.ROOM_LANG_PORT_BASE + _franja(lang) * len(srv.ROOM_KINDS) + i
        h = srv.ns.get("HANDLERS")
        if not h or p in h:
            return p
    return srv.ROOM_PORT_BASE + i


def room_of_port(port):
    """(idioma de la sala o None, índice en ROOM_KINDS) del puerto de una sala de las puertas, o None si no es de sala.
    Idioma None = sin ROOM_PER_LANG (el puerto no dice el idioma; manda el del cliente, con room_lang)."""
    n = len(srv.ROOM_KINDS)
    if port is None:
        return None
    if srv.ROOM_PORT_BASE <= port < srv.ROOM_PORT_BASE + n:
        return (WORLD_LANG if _per_lang() else None), port - srv.ROOM_PORT_BASE
    if _per_lang():
        b = srv.ROOM_LANG_PORT_BASE
        if b <= port < b + (len(ROOM_LANGS) - 1) * n:
            f, i = divmod(port - b, n)
            return (f if f < WORLD_LANG else f + 1), i
    return None


def room_block_key(port, client_lang=0):
    """Clave del bloque del HUB para una conexión al puerto `port`: (ROOM_PORT_BASE + i, idioma). Idioma = el de la sala
    con ROOM_PER_LANG; si no, room_lang(idioma del cliente) como siempre. None si el puerto no es de sala."""
    r = room_of_port(port)
    if r is None:
        return None
    lang, i = r
    return srv.ROOM_PORT_BASE + i, (srv.room_lang(client_lang) if lang is None else lang)


def gate_reply(c, msg_id, param, data):
    """Puerta de entrada (5730): versión OK + lista de servidores (cuentas y lobby, área 0) + fin de lista."""
    if msg_id == 0x2005:
        c.send(0x2002, proto.W().u32(0).bytes(), pause=srv.GATE_PAUSE)
        # Lobbies (tipo 3), campos b1/b2/b3 de la entrada:
        #  - My Room: b1=0, b2=12, b3=área (FUN_006d3a50).
        #  - Salas de las puertas (FUN_0082e170): b1 = idioma elegido (0 japonés, 1 inglés, 2 alemán, 3 italiano, 4 francés, 5 español, 6 mundial, 7 coreano: FUN_00a54b80),
        #    b2 = tipo de sala (ROOM_KINDS, según la puerta), b3 = nº de sala 0-7. Anunciamos la sala 0 de cada una.
        # Máx. 1024 B por mensaje (21 entradas); el cliente acumula las de varios 0x2003. Entre 0x2002 y 0x2004
        # la lista está a medias (+0x548=1) y si el juego busca un lobby entonces lee un puntero nulo y se cierra:
        # la lista va seguida, casi sin pausa (GATE_PAUSE).
        entries = [proto.server_entry(1, 1, "DuelCards", srv.PUBLIC_IP, srv.GAME_PORT),
                   proto.server_entry(2, 3, "MyRoom", srv.PUBLIC_IP, srv.LOBBY_PORT, b1=0, b2=12, b3=0)]
        # «extra» (+0x30) = jugadores en esa sala: FUN_0082e170 lo pinta como «%s (%4d/1000)» en la lista de la
        # puerta (antes 0 fijo -> «0/1000»). El cliente pide esta lista al cruzar cada puerta: va al día solo.
        # Con ROOM_PER_LANG cada (idioma, tipo) va a su puerto (room_port) y cuenta los de SU bloque (room_block_key).
        for lang in ROOM_LANGS:
            for kind in srv.ROOM_KINDS:
                port = room_port(lang, kind)
                with srv.HUB.lock:
                    n = len(srv.HUB.blocks.get(room_block_key(port, lang), {}))
                entries.append(proto.server_entry(10 + len(entries), 3, f"Sala {lang}-{kind}", srv.PUBLIC_IP,
                                                  port, extra=n, b1=lang, b2=kind, b3=0))
        for i in range(0, len(entries), 21):
            c.send(0x2003, b"".join(entries[i:i + 21]), pause=srv.GATE_PAUSE)
        c.send(0x2004)
