"""Reserva de duelo (paso E8 de la reestructura): seguridad del 0x6001 BOOKED_CHECK, salida de run.py SIN cambiar la lógica.

Seguridad del duelo (07-10, research/seguridad_duelo: PROPUESTA.md opción A + OFICIAL.md §3; NOTES.md). El 0x6001
BOOKED_CHECK solo trae el pid, que es PÚBLICO: hasta hoy cualquiera que llegara al puerto de duelo (sin login) se
sentaba en el duelo de otro (poc_secuestro.py). Ahora Conn.send crea una RESERVA al mandar 0x4306 por la conexión
de una sesión con personaje {sesión, pid, IP de esa conexión, conexión, duelo, hora}, y el 0x6001 solo vale con una
reserva de ese pid sin usar, de < 90 s y desde la MISMA IP (la 127.0.0.X que pone el túnel por invitado). Un intento
desde otra IP no la gasta; muere si se cierra esa conexión de lobby/sala, si la sesión cambia o si el PvP se anula.
Modo DUEL_RESERVA (YO2_DUEL_RESERVA; en caliente desde la consola: duel_reserva("on") / duel_reserva()):
  "vigilar" (por defecto) = como antes, pero apunta «[reserva] rechazaría …» con el motivo;
  "on" = rechaza: 0x6007 s32 0 (el cliente enseña «LB_DUEL_BOOKEDCHECK_WAIT ERROR» y cierra) y se cierra la
         conexión SIN tocar la sesión ni el duelo de la víctima;  "off" = sin comprobar (las reservas se siguen creando).
Una IP con 5 rechazos sospechosos en 5 min: aviso en el log y en Actividad del panel (tipo «seguridad»); nunca veto.

Qué hay aquí: `DUEL_RESERVA_MODOS`, `_load_tickets`, `_duel_reserva_mode`, `duel_reserva` (consola), `_duel_ticket_issue`
(lo llama `Conn.send` con 0x4306), `_duel_ticket_alive` y `duel_reserva_6001` (lo llama `_handle` en el primer 0x6001 de una
conexión de duelo sin sesión), más `iniciar()`, que fija en el namespace `DUEL_RESERVA` (modo) y `DT` (las reservas:
research/seguridad_duelo/tickets.py). Reglas (REESTRUCTURA.md §2.3): `DUEL_RESERVA` se REASIGNA desde la consola, así que
vive en el namespace (`srv.DUEL_RESERVA`); todo lo parcheable (`HUB`, `PVP`, `DT`, `pvp_hub`, `_admin_ev`, `log`…) se lee
como `srv.X` en cada uso y lo que puede no existir (`DT`, `PVP`) con `getattr(srv, "X", None)`.
"""
import os
import sys
import threading
import time

import proto

srv = None      # el Server; lo fija setup(). Hasta entonces nada de este módulo se puede llamar.

DUEL_RESERVA_MODOS = ("off", "vigilar", "on")
DUEL_RESERVA = (os.environ.get("YO2_DUEL_RESERVA") or "vigilar").strip().lower()   # el valor de arranque (lo ligan las cargas en caliente); el vivo está en srv.DUEL_RESERVA


def setup(servidor):
    """Enlaza el módulo con el Server (idempotente). arranque.py lo llama antes de usarlo."""
    global srv
    srv = servidor
    return servidor


def _load_tickets():
    import importlib.util as _ilu
    _sp = _ilu.spec_from_file_location("yo2_tickets", os.path.join(srv.HERE, "research", "seguridad_duelo", "tickets.py"))
    _m = _ilu.module_from_spec(_sp); _sp.loader.exec_module(_m); sys.modules["yo2_tickets"] = _m
    return _m

def iniciar():
    """DUEL_RESERVA (el modo: YO2_DUEL_RESERVA o «vigilar») y DT (las reservas) en el namespace. Un modo que no vale se
    deja en «vigilar» con un aviso. Sin tickets.py, DT = None (con DUEL_RESERVA=on se rechaza todo)."""
    srv.DUEL_RESERVA_MODOS = DUEL_RESERVA_MODOS
    srv.DUEL_RESERVA = getattr(srv, "DUEL_RESERVA", None) or (os.environ.get("YO2_DUEL_RESERVA") or "vigilar").strip().lower()
    try:
        srv.DT = getattr(srv, "DT", None) or srv._load_tickets().DuelTickets()
    except Exception as _e:
        srv.log(f"[ERROR] [reserva] research/seguridad_duelo/tickets.py no cargado: {_e!r} (con DUEL_RESERVA=on se rechaza todo)")
        srv.DT = None
    if srv.DUEL_RESERVA not in DUEL_RESERVA_MODOS:
        srv.log(f"[AVISO] [reserva] YO2_DUEL_RESERVA={srv.DUEL_RESERVA!r} no vale (off/vigilar/on): se usa «vigilar»")
        srv.DUEL_RESERVA = "vigilar"

def _duel_reserva_mode():
    v = str(getattr(srv, "DUEL_RESERVA", None) or "").strip().lower()
    return v if v in DUEL_RESERVA_MODOS else "vigilar"

def duel_reserva(modo=None):
    """Consola: duel_reserva() apunta en el log el modo, las reservas vivas y los rechazos por IP; duel_reserva("on" |
    "vigilar" | "off") cambia el modo en caliente (el siguiente 0x6001 ya lo usa). Devuelve el modo."""
    if modo is not None:
        m = str(modo).strip().lower()
        if m not in DUEL_RESERVA_MODOS:
            raise ValueError(f"modo {modo!r}: tiene que ser uno de {DUEL_RESERVA_MODOS}")
        old = srv._duel_reserva_mode()
        srv.DUEL_RESERVA = m
        srv.log(f"[reserva] modo {old} -> {m}")
        if old != m:
            srv._admin_ev("emit", "seguridad", f"Reserva de duelo: modo {old} → {m}", det="consola")
    dt = getattr(srv, "DT", None)
    srv.log(f"[reserva] modo {srv._duel_reserva_mode()}; " + (str(dt.snapshot()) if dt is not None else "tickets.py NO cargado"))
    return srv._duel_reserva_mode()

def _duel_ticket_issue(c):
    """Conn.send con 0x4306 DUELSERVERNOTICE: reserva para el 0x6001 de ESTA sesión desde la IP de c. Nunca lanza."""
    try:
        dt = getattr(srv, "DT", None)
        if dt is None:
            return None
        s = getattr(c, "sess", None)
        if s is None or s is srv.SIN_SESION or getattr(s, "pid", None) is None:
            srv.log(f"[reserva] 0x4306 por una conexión sin sesión o sin personaje ({getattr(c, 'peer', '?')}): sin reserva")
            return None
        pvp = getattr(srv, "PVP", None)
        m = pvp.by_pid(s.pid) if pvp is not None else None
        if m is not None and not getattr(m, "done", False):
            kind, ref = str(getattr(m, "name", "pvp")), m        # el PvP concreto: si se anula, la reserva muere
        else:
            d = getattr(s, "duel", None)
            kind, ref = ("npc" if isinstance(d, dict) and d.get("lobby") is c else "duelo"), None
        t = dt.issue(s, getattr(c, "peer", None), kind, ref, c)
        if t is not None:
            srv.log(f"[reserva] 0x4306 a pid={s.pid} ({t.peer}, puerto {c.port}): reserva {kind} de {int(dt.ttl)} s")
        return t
    except Exception as e:
        srv.log(f"[reserva] no se pudo crear la reserva: {e!r}")
        return None

def _duel_ticket_alive(t):
    """None si la reserva t sigue valiendo; si no, el motivo (lo llama DT.check fuera de su candado)."""
    c = t.conn
    if c is not None and getattr(c, "closed", False):
        return "se cerró la conexión de lobby/sala que recibió el 0x4306"
    if srv.HUB.by_pid(t.pid) is not t.sess:
        return "la sesión de ese pid ya no es la que pidió el duelo"
    if t.ref is not None:
        pvp = getattr(srv, "PVP", None)
        if getattr(t.ref, "done", False) or (pvp is not None and pvp.by_pid(t.pid) is not t.ref):
            return f"el duelo {t.kind} se ha anulado"
    return None

def duel_reserva_6001(c, pid):
    """_handle, primer 0x6001 de una conexión de duelo sin sesión, ANTES de HUB.attach. True = seguir como siempre
    (HUB.attach por el pid); False = rechazado: 0x6007 s32 0 y cierre de c (REJECT_CLOSE_S, como un 0x3003 malo); la
    sesión y el duelo de la víctima no se tocan. En «on» un error interno también rechaza (mejor un duelo fallido
    que uno secuestrado)."""
    mode = srv._duel_reserva_mode()
    if mode == "off":
        return True
    on = mode == "on"
    peer = getattr(c, "peer", "?")
    dt = getattr(srv, "DT", None)
    r = None
    try:
        if dt is None:
            raise RuntimeError("tickets.py no cargado")
        r = dt.check(pid, peer, alive=srv._duel_ticket_alive, spectator=getattr(c, "pvp", None) is not None)
    except Exception as e:
        srv.log(f"[ERROR] [reserva] 0x6001 pid={pid} desde {peer}: {e!r}")
    if r is not None and r.ok:
        srv.log(f"[reserva] 0x6001 pid={pid} desde {peer}: reserva {r.ticket.kind} válida "
                f"({int(time.time() - r.ticket.t)} s tras el 0x4306)")
        return True
    why, cat = (r.why, r.cat) if r is not None else ("error interno al comprobar la reserva", "error")
    srv.log(f"[reserva] {'RECHAZADO' if on else 'rechazaría'} 0x6001 pid={pid} desde {peer}: {why}"
            + (f" ({r.fails} rechazos sospechosos de esa IP en {int(dt.insist_s // 60)} min)" if r is not None and r.fails else "")
            + ("" if on else f" -> se deja pasar (modo {mode})"))
    try:
        victim = srv.HUB.by_pid(pid)
        if r is not None and r.alert:
            srv.log(f"[AVISO] [reserva] la IP {peer} insiste: {r.fails} intentos sospechosos de entrar en duelos ajenos en "
                    f"{int(dt.insist_s // 60)} min (solo aviso: no se veta)")
            srv._admin_ev("emit", "seguridad", f"La IP {peer} insiste en entrar en duelos ajenos ({r.fails} intentos en "
                          f"{int(dt.insist_s // 60)} min)", det=f"modo {mode}; el último, al de pid {pid}: {why}")
        elif on and cat == "reconexion":          # OFICIAL §3.4: aparte, para no confundirlo con un ataque
            srv._admin_ev("emit", "seguridad", "Reconexión al duelo rechazada: ", victim or f"pid {pid}",
                          f" desde {peer}", det=why)
        elif on:
            srv._admin_ev("emit", "seguridad", "Intento de entrar en el duelo de ", victim or f"pid {pid}",
                          f" desde {peer}", det=why)
    except Exception as e:
        srv.log(f"[reserva] aviso: {e!r}")
    if not on:
        return True
    c.booked_fail = why
    try:
        c.send(0x6007, proto.W().u32(0).bytes())  # ≤ 0: «LB_DUEL_BOOKEDCHECK_WAIT ERROR» y cierra (FUN_00691800)
    except Exception as e:
        srv.log(f"[reserva] 0x6007 0: {e!r}")
    c.closed = True
    _t = threading.Timer(getattr(srv.pvp_hub, "REJECT_CLOSE_S", 1.5), srv.pvp_hub._close_sock, (c,))
    _t.daemon = True
    _t.start()
    return False
