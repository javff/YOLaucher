# -*- coding: utf-8 -*-
"""Equipos entre jugadores reales (cliente YO2 Ver.080912), 1:1 con lo que el cliente lee y escribe.

Evidencia y confianza (C/P/D) en research/equipos/NOTES.md. Resumen del protocolo (todo big-endian, como proto.W):

  cliente -> servidor                          servidor -> cliente
  0x4550 TEAMCREATE  nombre16 (espera +0x84)   0x4551 u32 código (0 ok, 1 nombre ya registrado, 4 nombre inapropiado)
                                               + 0x4566 s32 (pone +0x84=2: fin de la espera)
  0x4552 TEAMDISBAND (sin datos)               0x4553 s32 al jefe; 0x4560 u32 equipo a los miembros
  0x4554 TEAMJOIN    u32 pid invitado          (al invitado) 0x4567 u32, nombre16, u32, nombre16
  0x4568 TEAMJOIN_REPLY u32 sí(1)/no(0), u32, u32 (los dos u32 de la invitación, en el mismo orden)
                                               0x4561 u32 equipo, u32 pid, nombre16 a los miembros
  0x4556 TEAMBANISH  u32 pid                   0x4562 u32 equipo, u32 pid, nombre16
  0x4569 TEAMLEAVE   (sin datos)               0x4570 u32 equipo, u32 pid, nombre16
  0x4558 TEAMLIST    (espera +0x88)            0x4564 s32 nº miembros, u32 equipo, nombre16, bandera16
                                               + 0x4559 n×(u32 pid, nombre16, u8 autoridad) + 0x4565 s32 (+0x88=2)
  (cambio visible)                             0x4571 u32 pid, u32 equipo, nombre16, bandera16 (bloque y él mismo)
  LIST5 0x4223 / 0x3112: u32 equipo, nombre16, bandera16 -> list5_body(s)
  0x4400 tipo 5 (chat de equipo) -> 0x4402 a los miembros conectados -> team_chat_targets(s)

Autoridad (u8 de 0x4559, el cliente la guarda en +0x11e1 si el pid es el suyo): bit 2 = jefe (Disolver activo,
Dejar inactivo), bit 4 = puede invitar (opción «Invitar al Equipo» en la ficha), bit 8 = puede expulsar.  C (bits)

No edita run.py / pvp_hub.py / research/lobby: integración en NOTES.md («Integración»).
"""
import os, sys, struct, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
EMU = os.path.normpath(os.path.join(HERE, "..", ".."))
if EMU not in sys.path:
    sys.path.insert(0, EMU)
import proto

W = proto.W

# ---------------------------------------------------------------- constantes (C/P/D: ver NOTES.md)
MAX_MEMBERS = 100        # C: la ficha solo ofrece «Invitar» si la lista local (+0x11c4) tiene < 100 (0x898e10)
NAME_LEN = 16            # C: 0x4550 manda 16 B; la caja de texto tiene tope 0x10 (+0x3f0, FUN_00a95c10)
CREATE_OK, CREATE_DUP, CREATE_BADNAME = 0, 1, 4   # C: FUN_00a97120 caso 5 (textos 5323 y 5331); 2/3 no pintan nada
AUTH_LEADER_BIT, AUTH_INVITE_BIT, AUTH_BANISH_BIT = 0x02, 0x04, 0x08   # C (FUN_00a9abe0 y 0x898dfc)
AUTH_LEADER = AUTH_LEADER_BIT | AUTH_INVITE_BIT | AUTH_BANISH_BIT     # P: el valor exacto del oficial no se sabe
AUTH_MEMBER = 0x00       # D: ¿los miembros podían invitar (bit 4)? El cliente lo permitiría; aquí no
FLAG16 = bytes(16)       # D: «bandera» del equipo (FUN_00ad7ed0, se baja por teamURL/teamflag/); no la elige el cliente
NAME_CASEFOLD = True     # D: ¿«Dragones» y «dragones» son el mismo nombre registrado? Aquí sí
CREATE_IN_TEAM_CODE = CREATE_DUP   # D: crear estando ya en un equipo (el botón está apagado: no debería llegar)
DISBAND_DENIED = 1       # D: 0x4553 de quien no es jefe (el cliente solo lo apunta en +0x984, no lo pinta)
# P: orden de 0x4567. El registro del cliente (DAT_00ca3c60, «チーム『%s』の%sから…») pinta 『2ª cadena』 como EQUIPO
# y la 1ª como la persona; el duplicado se busca por el 2º u32 (= equipo). Por eso: u32 pid que invita, su nombre,
# u32 equipo, nombre del equipo. El diálogo enseña la 2ª cadena + "\n" + texto 5320. Si en vivo saliera el nombre
# de la persona en el diálogo, poner False (orden equipo primero).
APPY_INVITER_FIRST = True
ANNOUNCE_4571_BLOCK = True    # P: 0x4571 a los del bloque (actualiza su LIST5 en memoria: la ficha del otro)
ANNOUNCE_4571_SELF = True     # P: 0x4571 al propio (pone +0x11e0, tiene equipo o no, si está en su lista de bloque)
JOIN_NOTICE_TO_JOINER = True  # P: 0x4561 también al que entra («X se ha unido al equipo.» en su pestaña Equipo)
INVITE_TTL = 600.0            # s que vale una invitación sin contestar (D: el cliente no tiene caducidad)
PATCH_HUB_LIST5 = True        # install(): HUB.list5_body = list5_body (LIST5 y 0x3112 con el equipo real)
MAX_DATA = 1024               # por mensaje (FUN_00689ea0)

NS = {}
LOCK = threading.RLock()
MEM = {"equipos": {"next_id": 1, "teams": {}}}   # sin load_state (pruebas sin run.py)
PENDING = {}       # (pid invitado, id equipo) -> (pid que invita, hora)


# ---------------------------------------------------------------- utilidades
def install(ns):
    """Recibe los globals de run.py (VIVOS): load_state/save_state, STATE_LOCK, CTX, PP, HUB, my_pid/my_name, log."""
    global NS
    NS = ns if isinstance(ns, dict) else dict(ns)
    hub = NS.get("HUB")
    if PATCH_HUB_LIST5 and hub is not None:
        hub.list5_body = list5_body           # atributo de la instancia: pvp_hub.py no cambia
    _log(f"[equipos] instalado ({len(_teams())} equipos guardados)")


def _log(*a):
    (NS.get("log") or print)(*a)


class _NoLock:
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _state_lock():
    lk = NS.get("STATE_LOCK")
    return lk if lk is not None and hasattr(lk, "__enter__") else _NoLock()


def _load():
    if "load_state" in NS:
        st = NS["load_state"]()
        return st, st.setdefault("equipos", {"next_id": 1, "teams": {}})
    return None, MEM["equipos"]


def _commit(st):
    if st is not None and "save_state" in NS:
        NS["save_state"](st)


def _teams():
    try:
        return _load()[1].get("teams", {})
    except Exception as e:
        _log(f"[equipos] state.json ilegible: {e!r}")
        return {}


def _team_of(pid, teams=None):
    """(id, equipo) del pid según el registro global state.json["equipos"], o (None, None)."""
    for tid, t in (teams if teams is not None else _teams()).items():
        for m in t.get("members", []):
            if m[0] == pid:
                return tid, t
    return None, None


def _member(t, pid):
    for m in t.get("members", []):
        if m[0] == pid:
            return m
    return None


def _hub():
    return NS.get("HUB")


def _who(c):
    """(sesión, pid, nombre) del que manda. Sin HUB/sesión (un solo jugador): my_pid()/my_name() de run.py."""
    s = getattr(c, "sess", None)
    if s is not None and getattr(s, "pid", None) is not None:
        return s, s.pid, s.name or "Duelista"
    pid = NS.get("my_pid")() if callable(NS.get("my_pid")) else NS.get("PLAYER_ID", 1)
    name = NS.get("my_name")() if callable(NS.get("my_name")) else "Duelista"
    return None, pid, name or "Duelista"


def _s(text, size):
    return W().s(str(text or "").encode("latin1", "replace").decode("latin1"), size).bytes()


def _name16(data):
    return bytes(data[:NAME_LEN]).split(bytes(1))[0].decode("latin1", "replace")


def _flag(t):
    try:
        return bytes.fromhex(t.get("flag", ""))[:16].ljust(16, b"\0") if t else FLAG16
    except ValueError:
        return FLAG16


def _online(pid):
    hub = _hub()
    return hub.by_pid(pid) if hub is not None else None


def _send(c, me, s, mid, data):
    """Al que pidió, por c (síncrono, en orden con su respuesta); a los demás, por la cola del HUB."""
    if s is not None and s is me:
        c.send(mid, data)
    elif s is not None:
        _hub()._post(s, mid, data)


def _members_online(t):
    out = []
    for m in (t or {}).get("members", []):
        o = _online(m[0])
        if o is not None:
            out.append(o)
    return out


def _profile_set(s, tid):
    """Puntero por PERFIL (state.json profiles[<hueco>]["team"] = id o None), en la vista de la cuenta de s, como
    lobby._friend_add. La fuente de verdad es el registro global (miembros de varias cuentas); el puntero se
    corrige solo en el siguiente TEAMLIST si quedó viejo (p. ej. expulsado sin conexión)."""
    if s is None or "load_state" not in NS:
        return
    ctx = NS.get("CTX")
    old = getattr(ctx, "sess", None) if ctx is not None else None
    try:
        if ctx is not None:
            ctx.sess = s
        PP = NS.get("PP")
        with _state_lock():
            st = NS["load_state"]()
            slot = s.slot if getattr(s, "slot", None) is not None else 0
            if PP is not None:
                p = PP.profile(st, slot)
            else:
                p = st.setdefault("profiles", {}).setdefault(str(slot), {})
            if p.get("team") != tid:
                p["team"] = tid
                NS["save_state"](st)
    except Exception as e:
        _log(f"[equipos] perfil de {s!r} (equipo {tid}): {e!r}")
    finally:
        if ctx is not None:
            ctx.sess = old


def list5_body(s):
    """LIST5 / 0x3112 sin pid (36 B): u32 id equipo (+0x17c), nombre16 (+0x184), bandera16 (FUN_00ad7ed0).  C"""
    tid, t = _team_of(getattr(s, "pid", None))
    if t is None:
        return struct.pack(">I", 0) + bytes(16) + bytes(16)
    return struct.pack(">I", int(tid)) + _s(t["name"], 16) + _flag(t)


def team_chat_targets(s):
    """Sesiones conectadas del equipo de s, sin s (chat 0x4400 tipo 5 -> 0x4402 canal 5)."""
    tid, t = _team_of(getattr(s, "pid", None))
    return [o for o in _members_online(t) if o is not s]


def _msg_4571(pid, tid, t):
    return struct.pack(">II", pid, int(tid or 0)) + _s(t["name"] if t else "", 16) + _flag(t)


def _announce(c, me, who, tid, t):
    """0x4571 (FUN_006be750): el equipo de `who` cambió. A los de su bloque y a él mismo.  P"""
    if who is None:
        return
    data = _msg_4571(who.pid, tid, t)
    if ANNOUNCE_4571_SELF:
        _send(c, me, who, 0x4571, data)
    if ANNOUNCE_4571_BLOCK and _hub() is not None:
        for o in _hub().others(who):
            _send(c, me, o, 0x4571, data)


# ---------------------------------------------------------------- lista
def _team_list(c, pid):
    """0x4564 BEGIN (FUN_006be3e0, 40 B: s32 nº miembros -> +0x11e0 = nº≠0, u32 id, nombre16, bandera16) +
    0x4559 (FUN_006be190: bucle u32 pid, nombre16, u8 autoridad) + 0x4565 END s32 0 (+0x88=2).  C"""
    tid, t = _team_of(pid)
    s = getattr(c, "sess", None)
    _profile_set(s, int(tid) if tid is not None else None)
    if t is None:
        c.send(0x4564, W().u32(0).u32(0).bytes() + bytes(16) + bytes(16))
        c.send(0x4565, W().u32(0).bytes())
        return
    ms = t["members"]
    c.send(0x4564, W().u32(len(ms)).u32(int(tid)).bytes() + _s(t["name"], 16) + _flag(t))
    cur = b""
    for m in ms:
        e = W().u32(m[0]).bytes() + _s(m[1], 16) + W().u8(m[2] & 0xff).bytes()
        if len(cur) + len(e) > MAX_DATA:
            c.send(0x4559, cur); cur = b""
        cur += e
    if cur:
        c.send(0x4559, cur)
    c.send(0x4565, W().u32(0).bytes())


def h_teamlist(c, msg_id, param, data):
    """0x4558 TEAMLIST (sin datos; la piden la ventana «Lista de Equipo» y My Room al entrar, FUN_00826020)."""
    s, pid, name = _who(c)
    _team_list(c, pid)
    return True


# ---------------------------------------------------------------- crear / disolver
def h_teamcreate(c, msg_id, param, data):
    """0x4550 TEAMCREATE nombre16 -> 0x4551 u32 código + 0x4566 s32 0. Con 0 la ventana vuelve a pedir TEAMLIST."""
    s, pid, pname = _who(c)
    name = _name16(data).strip()
    code = CREATE_OK
    with LOCK, _state_lock():
        st, reg = _load()
        teams = reg.setdefault("teams", {})
        if _team_of(pid, teams)[1] is not None:
            code = CREATE_IN_TEAM_CODE
        elif not name:
            code = CREATE_BADNAME
        else:
            key = name.casefold() if NAME_CASEFOLD else name
            if any((t["name"].casefold() if NAME_CASEFOLD else t["name"]) == key for t in teams.values()):
                code = CREATE_DUP
        if code == CREATE_OK:
            tid = int(reg.get("next_id", 1))
            reg["next_id"] = tid + 1
            teams[str(tid)] = {"id": tid, "name": name, "flag": FLAG16.hex(), "leader": pid,
                               "members": [[pid, pname, AUTH_LEADER]], "created": int(time.time())}
            _commit(st)
    c.send(0x4551, W().u32(code).bytes())
    c.send(0x4566, W().u32(0).bytes())
    _log(f"[equipos] crear {name!r} por {pname} ({pid}): código {code}")
    if code == CREATE_OK:
        t = teams[str(tid)]
        _profile_set(s, tid)
        _announce(c, s, s, tid, t)
    return True


def h_teamdisband(c, msg_id, param, data):
    """0x4552 TEAMDISBAND -> 0x4553 s32 (FUN_006bd220: vacía la lista, +0x11e1=0; la ventana se cierra sola, caso 10)
    + 0x4560 u32 id a cada miembro conectado («Su equipo ha sido disuelto.» en la pestaña Equipo) + 0x4571 equipo 0
    (0x4553/0x4560 NO ponen +0x11e0 = 0)."""
    s, pid, name = _who(c)
    with LOCK, _state_lock():
        st, reg = _load()
        teams = reg.setdefault("teams", {})
        tid, t = _team_of(pid, teams)
        m = _member(t, pid) if t else None
        if t is None or not (m[2] & AUTH_LEADER_BIT):
            ok = False
        else:
            ok = True
            teams.pop(tid, None)
            _commit(st)
    if not ok:
        c.send(0x4553, W().u32(DISBAND_DENIED).bytes())
        _log(f"[equipos] disolver denegado a {name} ({pid}): {'sin equipo' if t is None else 'no es jefe'}")
        return True
    c.send(0x4553, W().u32(0).bytes())
    online = _members_online(t)
    for o in online:
        _send(c, s, o, 0x4560, W().u32(int(tid)).bytes())
    for o in online:
        _profile_set(o, None)
        _announce(c, s, o, 0, None)
    for k in [k for k in PENDING if k[1] == int(tid)]:
        PENDING.pop(k, None)
    _log(f"[equipos] {t['name']!r} ({tid}) disuelto por {name}")
    return True


# ---------------------------------------------------------------- invitar / contestar
def h_teamjoin(c, msg_id, param, data):
    """0x4554 TEAMJOIN u32 pid (opción «Invitar al Equipo» de la ficha) -> al invitado 0x4567 TEAMJOIN_APPY
    (FUN_006bd300: cola +0x11f0, +0x11e8=1; el mapa abre el diálogo «<equipo>\\nTienes una invitación…» solo si
    +0x11e0 == 0). Sin respuesta al que invita (0x4555 TEAMJOINRESULT no tiene manejador en el cliente)."""
    s, pid, name = _who(c)
    target = struct.unpack(">I", data[:4])[0] if len(data) >= 4 else 0
    tid, t = _team_of(pid)
    m = _member(t, pid) if t else None
    o = _online(target)
    why = None
    if t is None:
        why = "no tiene equipo"
    elif not (m[2] & AUTH_INVITE_BIT):
        why = "sin permiso de invitar"
    elif o is None or o is s:
        why = "el invitado no está conectado (o es él mismo / una NPC)"
    elif _team_of(target)[1] is not None:
        why = "el invitado ya tiene equipo"
    elif len(t["members"]) >= MAX_MEMBERS:
        why = "equipo lleno"
    if why:
        _log(f"[equipos] invitación de {name} ({pid}) a {target} ignorada: {why}")
        return True
    PENDING[(target, int(tid))] = (pid, time.time())
    if APPY_INVITER_FIRST:
        body = W().u32(pid).bytes() + _s(name, 16) + W().u32(int(tid)).bytes() + _s(t["name"], 16)
    else:
        body = W().u32(int(tid)).bytes() + _s(t["name"], 16) + W().u32(pid).bytes() + _s(name, 16)
    _hub()._post(o, 0x4567, body)
    _log(f"[equipos] {name} invita a {o.name} ({target}) a {t['name']!r} ({tid})")
    return True


def _pending_for(pid, a, b):
    """Invitación de pid que case con (a, b) en cualquier orden (no se inventa cuál es cuál: ver APPY_INVITER_FIRST)."""
    now = time.time()
    for k in [k for k, v in PENDING.items() if now - v[1] > INVITE_TTL]:
        PENDING.pop(k, None)
    for (inv, tid), (by, _) in list(PENDING.items()):
        if inv == pid and {a, b} == {tid, by}:
            return (inv, tid), by
    return None, None


def h_teamjoinreply(c, msg_id, param, data):
    """0x4568 TEAMJOIN_REPLY (FUN_006cd390): u32 1 = sí / 0 = no, y los dos u32 de la invitación (DAT_012af248 y
    DAT_012af24c = 1er y 2º u32 de 0x4567). Sí -> 0x4571 al que entra (y a su bloque) + 0x4561 a los miembros."""
    if len(data) < 12:
        return True
    yes, a, b = struct.unpack(">III", data[:12])
    s, pid, name = _who(c)
    key, by = _pending_for(pid, a, b)
    if key is None:
        _log(f"[equipos] respuesta {yes} de {name} ({pid}) sin invitación pendiente ({a}, {b})")
        return True
    PENDING.pop(key, None)
    tid = key[1]
    if yes != 1:
        _log(f"[equipos] {name} ({pid}) rechaza la invitación al equipo {tid}")
        return True                     # D: el cliente no tiene aviso para el que invitó (texto 5321 sin uso)
    with LOCK, _state_lock():
        st, reg = _load()
        teams = reg.setdefault("teams", {})
        t = teams.get(str(tid))
        why = None
        if t is None:
            why = "el equipo ya no existe"
        elif _team_of(pid, teams)[1] is not None:
            why = "ya tiene equipo"
        elif len(t["members"]) >= MAX_MEMBERS:
            why = "equipo lleno"
        else:
            t["members"].append([pid, name, AUTH_MEMBER])
            _commit(st)
    if why:
        _log(f"[equipos] {name} ({pid}) no entra en {tid}: {why}")
        return True
    _profile_set(s, tid)
    _announce(c, s, s, tid, t)
    note = W().u32(tid).u32(pid).bytes() + _s(name, 16)
    for o in _members_online(t):
        if o is s and not JOIN_NOTICE_TO_JOINER:
            continue
        _send(c, s, o, 0x4561, note)
    _log(f"[equipos] {name} ({pid}) entra en {t['name']!r} ({tid})")
    return True


# ---------------------------------------------------------------- expulsar / salir
def _remove(c, s, pid, target, need_bit, mid, why_text):
    with LOCK, _state_lock():
        st, reg = _load()
        teams = reg.setdefault("teams", {})
        tid, t = _team_of(pid, teams)
        me = _member(t, pid) if t else None
        tm = _member(t, target) if t else None
        why = None
        if t is None:
            why = "sin equipo"
        elif need_bit and not (me[2] & need_bit):
            why = "sin permiso"
        elif tm is None:
            why = "no es del equipo"
        elif tm[2] & AUTH_LEADER_BIT:
            why = "es el jefe"           # D: el cliente no deja (Dejar apagado al jefe; expulsarse no tiene sentido)
        else:
            t["members"].remove(tm)
            _commit(st)
    if why:
        _log(f"[equipos] {why_text} {target} por {pid} ignorado: {why}")
        return
    who = _online(target)
    note = W().u32(int(tid)).u32(target).bytes() + _s(tm[1], 16)
    for o in _members_online(t) + ([who] if who is not None else []):
        _send(c, s, o, mid, note)
    if who is not None:
        _profile_set(who, None)
        _announce(c, s, who, 0, None)
    _log(f"[equipos] {why_text} {tm[1]} ({target}) de {t['name']!r} ({tid})")


def h_teambanish(c, msg_id, param, data):
    """0x4556 TEAMBANISH u32 pid (botón «Elimina del Equipo», bit 8) -> 0x4562 BANISH_NOTICE (FUN_006bd9f0: u32
    equipo, u32 pid, nombre16; texto 5325) a los miembros y al expulsado + 0x4571 equipo 0 al expulsado."""
    s, pid, name = _who(c)
    target = struct.unpack(">I", data[:4])[0] if len(data) >= 4 else 0
    _remove(c, s, pid, target, AUTH_BANISH_BIT, 0x4562, "expulsar")
    return True


def h_teamleave(c, msg_id, param, data):
    """0x4569 TEAMLEAVE (sin datos; el cliente ya puso +0x11e0 = 0) -> 0x4570 LEAVE_NOTICE (FUN_006bddd0, texto 5326)
    a los que quedan y al que sale + 0x4571 equipo 0."""
    s, pid, name = _who(c)
    _remove(c, s, pid, pid, 0, 0x4570, "salida de")
    return True


HANDLERS = {
    0x4550: h_teamcreate, 0x4552: h_teamdisband, 0x4554: h_teamjoin, 0x4556: h_teambanish,
    0x4558: h_teamlist, 0x4568: h_teamjoinreply, 0x4569: h_teamleave,
}


def handle(c, msg_id, param, data):
    h = HANDLERS.get(msg_id)
    return bool(h and h(c, msg_id, param, data))
