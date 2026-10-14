"""Personajes de la cuenta (paso E3 de la reestructura, REESTRUCTURA.md §3.4): lista, creación y borrado de personajes
(0x3010/0x3020/0x3030), baúl (0x3100), PBIN/NPCBIN (0x3114), la entrada de PLAYERLIST (`player_entry`), el archivo de
perfiles borrados (`archive_profile`) y «quién soy» (`my_slot`/`my_pid`/`my_name`/`sync_player_id`).

Es el código de `game_reply` y de las funciones de run.py que lo rodean, MOVIDO sin cambiar la lógica. `game_reply`
(el despachador del puerto 5731, con el login 0x3001/0x3003 que es de la fase 3) vive también aquí desde E8 y llama a las
funciones `*_0x30xx` en las mismas ramas y en el mismo orden que antes (la rama de PH, que va delante, no cambia).
run.py (arranque.py) conserva los MISMOS nombres con

    from yo2srv.cuentas import personajes as _personajes
    _personajes.setup(SRV)
    from yo2srv.cuentas.personajes import (player_entry, PROFILES_ARCHIVE_MAX, archive_profile, my_slot, my_pid, my_name,
                                           sync_player_id)

Reglas (REESTRUCTURA.md §2.3): `PLAYER_ID` se REASIGNA en caliente (`sync_player_id`; lo lee research/lobby y equipos), así
que vive en el namespace y aquí se escribe como `srv.PLAYER_ID`. Todo lo demás de run.py (`load_state`/`save_state`,
`HUB`, `PH`, `log`, `cur_sess`, `SIN_SESION`, `STATE_LOCK`, `my_pid`…) se lee como `srv.X` en el momento de usarlo.
"""
import time

import proto

srv = None   # el Server; lo fija setup(). Hasta entonces nada de este módulo se puede llamar.


def setup(servidor):
    """Enlaza el módulo con el Server (idempotente). run.py lo llama justo antes de importar los nombres."""
    global srv
    srv = servidor
    return servidor


def player_entry(slot, name):
    """Entrada de 0x3012 PLAYERLIST (orden de FUN_0068f520); campos aún sin identificar a 0."""
    w = proto.W().u8(slot).s(name, 16).u8(0).u32(0).u32(0).u32(0).u16(0).u32(0)
    w.u16(0).u16(0).u16(0).u16(0).s("", 16).s("", 16)
    return w.bytes()

PROFILES_ARCHIVE_MAX = 10   # perfiles borrados que se guardan en state["profiles_borrados"] (los más recientes)

def archive_profile(st, slot, why):
    """Saca el perfil del hueco de state["profiles"] y lo guarda en state["profiles_borrados"] (recuperable a mano)."""
    p = st.get("profiles", {}).pop(str(slot), None)
    if p is None:
        return None
    arch = st.setdefault("profiles_borrados", [])
    arch.append({"slot": str(slot), "motivo": why, "fecha": time.strftime("%Y-%m-%d %H:%M:%S"), "perfil": p})
    del arch[:-srv.PROFILES_ARCHIVE_MAX]
    srv.log(f"[perfil] hueco {slot} ({p.get('name') or '-'}): perfil archivado en profiles_borrados ({why})")
    return p


# ---- Las ramas de game_reply (5731) que son de personajes. `c` = la conexión, `data` = el cuerpo del mensaje.

def playerlist_0x3010(c):
    """0x3010 -> YG_MSG_PLAYERLIST (0x3012)."""
    st = srv.load_state()
    w = proto.W().u32(0)
    for slot, name in sorted(st["players"].items()):
        w.b += srv.player_entry(int(slot), name)
    c.send(0x3012, w.bytes())

def regplayer_0x3020(c, data):
    """0x3020 crear jugador: hueco + nombre -> YG_MSG_REGPLAYERRESULT (0x3022)."""
    name = data[1:17].split(bytes(1))[0].decode("latin1") if len(data) > 1 else ""
    with srv.STATE_LOCK:
        st = srv.load_state(); slot = str(data[0]) if data else "0"
        old = st.get("profiles", {}).get(slot)
        if old is not None and old.get("name") and old.get("name") != name:
            srv.archive_profile(st, slot, "crear")   # perfil viejo de otro personaje en ese hueco (antes de B75)
        st.setdefault("players", {})[slot] = name; srv.save_state(st)
    c.send(0x3022, proto.W().u32(0).bytes())

def delplayer_0x3030(c, data):
    """0x3030 borrar jugador -> YG_MSG_DELPLAYERRESULT (0x3032)."""
    # B75: borrar el personaje borra también su perfil (baúl, saldo, PBIN/NPCBIN...); si no, el siguiente
    # personaje creado en ese hueco lo heredaba. El perfil no se pierde: va a state["profiles_borrados"].
    if not data:
        srv.log(f"[srv:{c.port}] 0x3030 sin hueco: no se borra nada")
    else:
        with srv.STATE_LOCK:
            st = srv.load_state(); slot = str(data[0])
            st.setdefault("players", {}).pop(slot, None)
            srv.archive_profile(st, slot, "borrar")
            srv.save_state(st)
    c.send(0x3032, proto.W().u32(0).bytes())

def trunk_0x3100(c):
    """0x3100 baúl: 3 copias de cada carta (3256 bytes en 7 trozos)."""
    c.send(0x3101)
    for i in range(7):
        c.send(0x3102, bytes([3]) * (512 if i < 6 else 0xb8))
    c.send(0x3103)

def pbin_0x3114(c):
    """0x3114 -> YG_MSG_GETPBIN (128 bytes) + YG_MSG_NPCBIN (512)."""
    c.send(0x3114, bytes(128))
    c.send(0x4234, bytes(512))


# ---- «Quién soy»: el personaje activo de la sesión del hilo.
# B12: el personaje activo NO es siempre el hueco 0. PSELECTRESULT (persistencia.h_pselect) da pid = hueco + 1 y el
# cliente busca ese pid en el bloque (FUN_009be6b0): block_player, avatar_info y preparation_result tienen que usar
# el mismo. my_slot/my_pid/my_name se calculan en cada llamada desde state.json (hueco activo = el último 0x3100 /
# 0x4100). PLAYER_ID (en el namespace de run.py) se mantiene por compatibilidad y se pone al día tras cada PSELECT
# (sync_player_id).

def my_slot(st=None):
    try:
        st = st if st is not None else srv.load_state()
        return int(st.get("active_slot", 0)) & 3
    except Exception:
        return 0

def my_pid(st=None):
    # V02/P1-15: pid = 1000 + 4·índice de cuenta + hueco (pvp_hub.PID_BASE), el mismo que manda PSELECTRESULT (h_pselect
    # con pid_of). El dueño es la cuenta 0: su pid pasa de 1 a 1000 (los pids 1-98 el lobby los toma por NPC).
    s = srv.cur_sess()
    if s is srv.SIN_SESION:
        return 0                                 # 07-10: sin sesión no hay pid (antes: el del dueño, 1000)
    if s is not None and s.pid is not None and st is None:
        return s.pid
    return srv.HUB.pid_for(s, srv.my_slot(st))

def my_name(st=None):
    try:
        st = st if st is not None else srv.load_state()
        slot = str(srv.my_slot(st))
        return (st.get("players", {}).get(slot) or st.get("profiles", {}).get(slot, {}).get("name")
                or "Duelista")
    except Exception:
        return "Duelista"

def sync_player_id():
    srv.PLAYER_ID = srv.my_pid()


# ---- game_reply: el despachador del puerto 5731 (E8: salió de run.py, donde se había quedado en E3 con el login).

def game_reply(c, msg_id, param, data):
    """Servidor del lobby (5731). Nombres de los mensajes: los registros del propio cliente."""
    # 07-10: sin load_state() aquí arriba: 0x3001/0x3003/0x0005 llegan antes de tener sesión (con SIN_SESION lanzaría)
    if msg_id == 0x0005:                       # latido: el cliente corta si en 120 s no recibe nada (FUN_0068d080).
        if c.port == srv.GAME_PORT:            # 0x3113 no hace nada en el manejador de cuentas (FUN_0068eca0)
            c.send(0x3113)
        return
    if msg_id == 0x3001:                       # hola -> acuse
        c.send(0x3002)
    elif msg_id == 0x3003:                     # autenticación (usuario/clave cifrados) -> YG_MSG_AUTHRESULT
        # V02: los 32 B son iguales siempre para el mismo usuario y clave -> clave de la cuenta (sesión). Llega en
        # cuentas, lobby y cada sala; desde aquí, load_state() de este hilo ve el perfil de ESA cuenta.
        if srv.HUB.login(c, data) is None:
            # 07-10: rechazado (−240 cuenta/clave, −246 veto, −200 tope de sesiones/cuentas): ya llevó su 0x3004 y se
            # cierra. NUNCA «ok» sin sesión (CARGA.md §4.6: así entraban como el dueño). Un hub de antes que no
            # rechazaba solo: se rechaza aquí.
            if not getattr(c, "closed", False):
                srv.HUB.reject(c, getattr(srv.pvp_hub, "AUTH_SERVER_FULL", -200), "")
            return
        srv.CTX.sess = c.sess
        # 07-10: + bits de sanción del panel (0x20 chat -> 4581, 0x40 intercambio -> 4580; research/admin/secciones.py)
        c.send(0x3004, proto.W().u32(0).u32(1 | (int(getattr(c, "admin_flags", 0) or 0) & 0x60)).s("ESP", 4).bytes())
    elif srv.PH and msg_id in srv.PH_GAME:     # PLAYERLIST, baúl y PBIN/NPCBIN desde el perfil guardado
        srv.PH[srv.PH_GAME[msg_id]](c, data)
        if msg_id == 0x3100:                   # h_gettrunk fija el hueco activo (B12)
            srv.sync_player_id()
    elif msg_id == 0x3010:                     # -> YG_MSG_PLAYERLIST
        srv.playerlist_0x3010(c)
    elif msg_id == 0x3110:                     # GetDuelPassPurchase -> 0x3111 s32 pases sin abrir (cuenta+0x1210)
        # 07-10 B70: con persistencia lo contesta PH["h_dp_saldo"] (PH_GAME) con el saldo de la cuenta; sin ella no
        # hay saldo: 0 (el cliente abre la ventana de código). Antes: 1000 fijo = sobres infinitos.
        c.send(0x3111, proto.W().u32(0).bytes())
    elif msg_id == 0x3020:                     # crear jugador: hueco + nombre -> YG_MSG_REGPLAYERRESULT
        srv.regplayer_0x3020(c, data)
    elif msg_id == 0x3030:                     # borrar jugador -> YG_MSG_DELPLAYERRESULT
        srv.delplayer_0x3030(c, data)
    elif msg_id == 0x3100:                     # baúl: 3 copias de cada carta (3256 bytes en 7 trozos)
        srv.trunk_0x3100(c)
    elif msg_id == 0x3114:                     # YG_MSG_GETPBIN (128 bytes) + YG_MSG_NPCBIN (512)
        srv.pbin_0x3114(c)
    elif msg_id not in (0x0003, 0x0005) and not srv.auto_reply(c, msg_id):
        srv.log(f"[srv:{c.port}] ** mensaje sin respuesta todavía: 0x{msg_id:04x}")
