"""El lobby (paso E7 de la reestructura, REESTRUCTURA.md §3.4): `lobby_reply`, el manejador de los puertos 5732 (lobby) y
5740-5750 (salas), y las tablas que lo acompañan: `EMPTY_LISTS` (listas del mapa que se contestan vacías) y `PH_GAME`/`PH_LOBBY`
(id de mensaje -> nombre del manejador de persistencia `PH[...]`, que usan `game_reply` y `lobby_reply`).

Es el código de run.py MOVIDO sin cambiar la lógica ni el orden: login (0x3001/0x3003 -> game_reply) -> tope de DP del reto a
una NPC (0x4288) -> presencia en el HUB (0x4100 + `HUB.on_lobby_msg`) -> `LP.handle` (salas, amigos, chat, correo, equipos,
sobres; research/lobby, fase 3) -> `AV_STATE` (0x4170) -> PH (`PH_LOBBY`) -> ramas propias (0x0003/0x0005, 0x4100, 0x4200/02,
0x4170, 0x4558, 0x5320, 0x4210, 0x4230, 0x4288, listas 2-5, 0x4172, 0x5204, tienda 0x5208/0x5210, 0x4902, 0x5400,
0x5300/0x5310) -> `auto_reply`. NO se mueven aquí (fase 3): pvp_hub.py, research/lobby/proposal.py (LP), equipos,
intercambio y torneos. `dp_npc_gate` y `dp_balance` se quedan en run.py (los parchea hot_dp_coste.py).

run.py conserva los MISMOS nombres con

    from yo2srv.lobby import setup as _setup_lobby; _setup_lobby(SRV)
    from yo2srv.lobby.lobby import EMPTY_LISTS, PH_GAME, PH_LOBBY, lobby_reply

Reglas (§2.3): todo lo que alguien pueda reasignar o parchear en caliente se lee como `srv.X` en el momento de usarlo, no
como global del módulo: `HUB`, `LP`, `PH`, `NPCS`, las funciones de run.py (`game_reply`, `dp_npc_gate`, `duel_begin`,
`shop_buy`, `auto_reply`…) y los mismos `EMPTY_LISTS`/`PH_LOBBY`. Los dos dicts SON los mismos objetos que ve el
namespace: DPC (`install_game`), hot_duelpass_*.py y sec_duelpass.py los modifican por referencia (`PH_LOBBY[0x8101] = …`).
Ojo con los alias de compat.ALIAS: `srv.lobby` es LP; aquí no hace falta.
"""
import struct
import traceback

from yo2srv.red import proto

srv = None   # el Server; lo fija setup(). Hasta entonces nada de este módulo se puede llamar.


def setup(servidor):
    """Enlaza el módulo con el Server (idempotente). run.py lo llama justo antes de importar los nombres de aquí."""
    global srv
    srv = servidor
    return servidor


# Petición -> (START, END) de las listas que el mapa pide al entrar (ids del manejador FUN_006ac020)
# 0x4610 RCVMAIL (buzón) -> RCVMAILLISTSTART (u32 0) + END; 0x4280 REQDUELROOMLIST -> DUELROOMLISTSTART (u32) + DUELROOMLISTEND
EMPTY_LISTS = {0x4214: (0x4215, 0x4217), 0x4218: (0x4219, 0x421b), 0x421c: (0x421d, 0x421f),
               0x4220: (0x4222, 0x4224), 0x4221: (0x4222, 0x4224),
               0x4280: (0x4282, 0x4286),
               0x4610: (0x4611, 0x4613)}

# Mensaje -> nombre del manejador de persistencia (PH[...]) que lo atiende. Se rellenan con los MISMOS dicts para siempre:
# DPC.install_game añade PH_LOBBY[0x8101] y hot_duelpass_saldo.py toca PH_GAME[0x3110] / PH_LOBBY[0x4902].
PH_GAME = {0x3010: "h_playerlist", 0x3100: "h_gettrunk", 0x3114: "h_getpbin"}
PH_LOBBY = {0x4100: "h_pselect", 0x4902: "h_pack_open", 0x5400: "h_sd_get", 0x5204: "h_itemget",
            0x4174: "h_title", 0x4170: "h_avatar"}


def registrar_ph(ph):
    """Tras cargar la persistencia (run.py, bloque de PP/PH): 0x3110 -> 0x3111 = pases DUELPASS de la cuenta (07-10 B70).
    Sin persistencia no hay saldo y game_reply contesta 0. Idempotente; mismo dict PH_GAME."""
    if ph and "h_dp_saldo" in ph:
        PH_GAME[0x3110] = "h_dp_saldo"
    return PH_GAME


def lobby_reply(c, msg_id, param, data):
    """Servidor del lobby (5732). Empieza como el de cuentas (0x3001 REQCCODE -> 0x3002, 0x3003 -> 0x3004 AUTHRESULT);
    formatos sacados de los lectores del cliente (FUN_006ac020 y los que llama)."""
    if msg_id in (0x3001, 0x3003):
        return srv.game_reply(c, msg_id, param, data)
    if msg_id == 0x4288 and srv.dp_npc_gate(c, data):   # reto a NPC sin DP: 0x428a −612 (persistencia NOTES §13)
        return
    # PvP fase 1 (V02/V04): sesión y presencia ANTES de contestar (LP y PH contestan y salen). Esto nunca contesta a c
    # (las respuestas propias siguen igual que con un jugador); solo apunta y avisa a los OTROS del bloque.
    try:
        if msg_id == 0x4100 and getattr(c, "sess", None) is not None:
            c.lang = data[0] if data else 0
            slot = data[1] & 3 if len(data) > 1 else srv.my_slot()
            srv.HUB.select(c, slot, c.lang, (srv.load_state().get("players") or {}).get(str(slot)))
            srv._admin_ev("on_select", c.sess)       # panel: «X entra en el juego» (solo la primera vez)
        srv.HUB.on_lobby_msg(c, msg_id, data)
    except Exception as e:
        srv.log(f"[hub] 0x{msg_id:04x}: {e!r} | " + traceback.format_exc().replace(chr(10), " | "))
    if srv.LP is not None and srv.LP.handle(c, msg_id, param, data):   # salas, amigos, chat, correo, equipos, sobres (agente 3)
        return
    if msg_id == 0x4170 and len(data) >= 4:   # REGAVATARINFO: el último que llega dice el mundo actual
        srv.AV_STATE["world"] = struct.unpack(">I", data[:4])[0]
    if srv.PH and msg_id in srv.PH_LOBBY:   # PSELECTRESULT, DUELPASS, decks, objetos, título y avatar desde el perfil
        r = srv.PH[srv.PH_LOBBY[msg_id]](c, data)
        if msg_id == 0x4100:        # h_pselect fija el hueco activo: PLAYER_ID = hueco + 1 (B12)
            srv.sync_player_id()
        if msg_id == 0x4170:        # My Room: el aspecto propio a su registro del bloque (MYROOM_SELF_AVATAR)
            srv.myroom_self_avatar(c, data)
        return r
    if msg_id == 0x0003:      # B58: el cliente cierra la sesión (FUN_0068cf60: no espera respuesta). Sin ruido en el log
        srv.on_conn_closed(c, "0x0003")
        return
    if msg_id == 0x0005:      # latido cada 60 s; si en 120 s no llega nada del servidor, el cliente corta (FUN_0068d080).
        c.send(0x0004, proto.W().u32(0).bytes())   # 0x0004 lo lee el lobby sin hacer nada (FUN_006bfc80)
    elif msg_id == 0x4100:    # PSELECT (1er byte = idioma: 2 alemán, 5 español) -> PSELECTRESULT: u32 0 + datos (FUN_006b7ef0, 129 B)
        c.lang = data[0] if data else 0
        if len(data) > 1:     # u8 hueco del personaje elegido (B12)
            with srv.STATE_LOCK:
                st = srv.load_state(); st["active_slot"] = data[1] & 3; srv.save_state(st)
            srv.sync_player_id()
        # El 1er campo es el id del jugador: la habitación (CAvatarNetSupport) se monta con él; 0 = ninguno
        c.send(0x4101, srv.pselect_result())
    elif msg_id == 0x4200:    # REQBLOCKLIST -> BLOCKLIST: u16 máximo + entradas (u16 id, u8, u8, u8) (FUN_006b8580)
        b1, b2 = srv.block_type(c)
        bl = srv.hub_area("blocklist", None, srv.block_key(c), b2)   # pvp_hub.AREA_BLOCKS: un bloque por área (si no, None)
        c.send(0x4201, bl if bl is not None else proto.W().u16(8).u16(0).u8(b1).u8(b2).u8(0).bytes())
    elif msg_id == 0x4202:    # ENTERBLOCK -> ENTERBLOCKRESULT: u32 0 + 3 u8 (FUN_006b8840)
        # 3 bytes: índice del bloque dentro de BLOCKLIST (si se sale del vector: «invalid vector<T> subscript» y
        # abort, FUN_00a8dea0), ÁREA inicial (+0x9f0 -> FUN_009964e0 -> GetInitPos(área, 8) FUN_00a4fe30, que solo
        # admite 0-9: con el tipo de sala 13 de la puerta azul salía «illegal mapid GetInitPos(13,8)») y 0.
        # Principiantes (tipo 1) funcionaba porque su tipo cabe en 0-9: se conserva; los demás entran por el área 0.
        b1, b2 = srv.block_type(c)
        area = b2 if c.port == srv.LOBBY_PORT or 0 <= b2 <= 9 else 0   # el lobby (12) ya funcionaba: igual que antes
        # ENTRADA en la sala (data[1] == 0): el área depende del mundo de la sala. La tabla de conexiones 0xd2d5d4 de
        # yo2.exe tiene dos grupos de mapas sin camino entre ellos: Domino {0,1,2,3,9} y Academia {4,5,6,7}; el 5 es
        # la entrada de la Academia (con entrada «desde 11», como el 1 en Domino). Puerta roja (ACAD_KINDS) -> 5,
        # azul -> 0. En un cambio de área (data[1] == 1) el destino lo decide el cliente: se deja como estaba. Antes
        # era el parche en caliente acad_patch_live.py (queda de referencia).
        if c.port != srv.LOBBY_PORT and len(data) >= 2 and data[1] == 0:
            area = srv.ACAD_AREA if b2 in srv.ACAD_KINDS else srv.DOMINO_AREA
        # 1.er u8 = bloque (+0x9ef, FUN_006b8840): con pvp_hub.AREA_BLOCKS, el del área; si no, 0 como siempre
        blk = srv.hub_area("area_block", 0, getattr(c, "sess", None), srv.block_key(c), data, area)
        c.send(0x4203, proto.W().u32(0).u8(blk).u8(area).u8(0).bytes())
        # Jugadores del bloque (nosotros incluidos): sin esto, al llegar 0x5340 la habitación no encuentra
        # al jugador, crea el avatar sin él y el cliente se cierra (FUN_009be6b0)
        # P0-4: entra en el bloque del HUB (a los demás les llega 0x3112 op 1) y la lista lleva a todos.
        srv.HUB.enter(*((getattr(c, "sess", None), srv.block_key(c)) + ((blk,) if hasattr(srv.HUB, "area_block") else ())))
        srv.send_block_list(c)
    elif msg_id == 0x4170:    # REGAVATARINFO (u32 0=Domino/1=Academia, piezas del avatar) -> 0x4171 u32 0
        c.send(0x4171, proto.W().u32(0).bytes())
        srv.myroom_self_avatar(c, data)
    elif msg_id == 0x4558:    # TEAMLIST -> TEAMLISTRESULT_BEGIN (0x4564) + _END (0x4565), sin equipos
        c.send(0x4564, proto.W().u32(0).bytes())
        c.send(0x4565, proto.W().u32(0).bytes())
    elif msg_id == 0x5320:    # REQAVATARINFO -> 0x5340 (FUN_006b9a10): u32 0, u32 id, nombre16, u16 x, u16 y, u8 dirección 0-7
        pid = struct.unpack(">I", data[:4])[0] if len(data) >= 4 else 0
        c.send(0x5340, srv.avatar_info(pid or srv.my_pid()))
    elif msg_id == 0x4210:    # REQBLOCKPLAYERLIST (u32 -1) -> START (u32 nº) + DATA (FUN_006b8b70) + END
        srv.send_block_list(c)
        # La lista llega con la posición a 0 y el mapa solo crea los avatares con posición != (0,0)
        # (FUN_00a024e0 el propio, FUN_00a02ad0 los demás): detrás va la última 0x5300 de cada jugador.
        if getattr(c, "pos", None):
            c.send(0x5300, c.pos, pause=0.02)
        for o in srv.block_others(c):                 # P0-4: y la de los demás (FUN_006c2130 ya los encuentra)
            if o.pos:
                c.send(0x5300, o.pos, pause=0.02)
    elif msg_id == 0x4230:    # REQ NPCINFOLIST -> START (s32 0) + DATA (FUN_006c30f0) + END
        c.send(0x4231, proto.W().u32(0).bytes())
        if srv.NPCS:
            for i in range(0, len(srv.NPCS), 16):                 # 61 B por NPC; máx. 1024 B por mensaje
                c.send(0x4232, b"".join(srv.npc_entry(*srv.npc_list_parts(n)) for n in srv.NPCS[i:i + 16]))
        c.send(0x4233)
    elif msg_id == 0x4288:    # REQCREATEROOM (duelo; 28 B: 4×u8, 16 B, 4×u8, u32 id del rival: la NPC pulsada)
        rival = struct.unpack(">I", data[24:28])[0] if len(data) >= 28 else 0
        # V08 (P0-8): rival, conexión de lobby y duelfin de ESTA sesión (antes DUEL/LOBBY_ROOM/DF globales: con dos
        # jugadores, el fin de uno llegaba al lobby del otro). duel_begin sigue apuntando los globales (consola).
        npc_u8 = 1 if srv.npc_rule("npc_window") and 1 <= rival <= 98 else 0   # 1 = ventana de NPC (modo match)
        srv.duel_begin(c, rival, npc_u8)             # incluye h_autoduelstart(rival, npc_u8): 0 = partida suelta
        c.send(0x428a, proto.W().u32(0).bytes())                     # CREATEROOMRESULT ok
        name = next((n[1] for n in srv.NPCS if n[0] == rival), f"Rival {rival}")
        # AUTODUELSTART (u32 id, nombre16, u8, u16, u16, 16 B, u8) y DUELSERVERNOTICE (ip15, u16 puerto)
        c.send(0x43a0, proto.W().u32(rival).s(name, 16).u8(0).u16(0).u16(0).bytes() + srv.duel_parts(rival) + bytes([npc_u8]))
        c.send(0x4306, proto.W().s(srv.PUBLIC_IP, 15).u16(srv.DUEL_PORT).bytes())
    elif msg_id in srv.EMPTY_LISTS:  # REQBLOCKPLAYERLIST2-5: listas vacías (START con u32 0 + END)
        start, end = srv.EMPTY_LISTS[msg_id]
        c.send(start, proto.W().u32(0).bytes())
        # P0-4: LIST2..5 (0x4216/0x421a/0x421e/0x4223 = START + 1) con los OTROS del bloque: posición y piezas
        # (FUN_006b8d10), comentarios (FUN_006b8fc0/FUN_006b9100) y equipo (FUN_006b9250). Solo = vacías, como antes.
        for chunk in srv.HUB.list_chunks(c, msg_id):
            c.send(start + 1, chunk)
        c.send(end)
    elif msg_id == 0x4172:    # REGPLAYERCOMMENT -> 0x4173 u32 0 (FUN_006ba380)
        c.send(0x4173, proto.W().u32(0).bytes())
    elif msg_id == 0x5204:    # ITEMGET -> ITEMGET_RESULT 0x5205: u16 n + n×u16, u16 m + m×u16 (FUN_006c2b30), vacío
        c.send(0x5205, proto.W().u16(0).u16(0).bytes())
    elif msg_id == 0x5208:    # REQSHOPITEM (tienda) -> 0x5213 inicio (vacía la lista, FUN_006c1da0) + 0x5209 artículos
        # (FUN_006c1c70) + 0x5214 fin (FUN_006c1e40). Sin 0x5214 la tienda se queda colgada.
        c.send(0x5213)
        items = srv.shop_catalog()
        for i in range(0, len(items), 60):          # 17 B por artículo; máx. 1024 B por mensaje
            c.send(0x5209, b"".join(items[i:i + 60]))
        c.send(0x5214)
    elif msg_id == 0x5210:    # REQSHOPRESULT (comprar) = CARRITO: n × (u32 id de artículo, u8 cantidad)
        srv.shop_buy(c, data)
    elif msg_id == 0x4902:    # PURCHASE_PACK_OPEN (sin datos) -> 0x4903 (FUN_006bfcc0): u32 resultado, u32 (+0x540),
        # cartas (u16 id, u8). Con persistencia lo atiende PH["h_pack_open"] (pases de la cuenta, B70). Sin ella no
        # hay pases: s32 1 = sin sobre (FUN_006bfcc0 no suma nada). Antes daba un sobre gratis cada vez.
        c.send(0x4903, proto.W().u32(1).u32(0).bytes())
    elif msg_id == 0x5400:    # SD_GET_REQUEST (canjear deck de estructura): u16 id deck, u8, u8
        # -> SD_GET_RESULT 0x5401 (FUN_006c26d0): u32 resultado + u16 por carta del deck (<id>.ydc)
        deck = struct.unpack(">H", data[:2])[0]
        w = proto.W().u32(0)
        for _, card in srv.shop_purchase(1, deck, 1):
            w.u16(card)
        c.send(0x5401, w.bytes())
    elif msg_id in (0x5300, 0x5310):
        # Posición (0x5300: u32 id, u16 x, u16 y, u8, u8) y acción (0x5310: u32 id, u8, u8) del jugador. El servidor
        # las reenvía a todo el bloque, a él también: en el mapa el avatar propio se crea/actualiza con ellas
        # (lectores FUN_006c2130 / FUN_006c2450, mismo formato). Sin el eco, el avatar no existe y al pulsar un
        # botón el juego lee un CChibiAvatarElm nulo y se cierra (FUN_009c44a0 devuelve el avatar propio, nulo).
        if msg_id == 0x5300:
            c.pos = data
        c.send(msg_id, data, pause=0.02)
    elif msg_id != 0x0005 and not srv.auto_reply(c, msg_id):
        srv.log(f"[srv:{c.port}] sin respuesta para 0x{msg_id:04x}")
