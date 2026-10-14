"""Intercambio de cartas entre dos jugadores reales (sobre 0x7000) — emulador de YO2 (cliente Ver.080912).

Todo sale del cliente (descompilado + desensamblado); detalle, direcciones y confianza en NOTES.md. Resumen:

  pedir      A: 0x7000/0x7102 REQTRADE u32 pidB      -> A 0x7103 s32 (0 = enviada; -493 = «No se pudo enviar»)
                                                       -> B 0x7104 TRADEOFFER u32 pidA + nombre16
  contestar  B: 0x7000/0x7105 s32 (1 sí / 0 no)      -> B 0x7105 s32 0 (ack) ; A 0x7105 s32 (0 = aceptada, ≠0 = rechazada)
  escena     X: 0x7000/0x7132 TRADEREADY             -> X 0x7125 + 7 x 0x7126 (baúl 0xcb8 B) + 0x7127
                                                          + 0x7129 + 7 x 0x7130 (límite 0xcb8 B) + 0x7131
             X: 0x7000/0x7101 TRADESTART             -> X 0x7133
  cartas     X: 0x7113 ADDCARD u16 id / 0x7114 DELETECARD u16 índice -> X 0x7113/0x7114 s32 0
                                                       -> Y 0x7116 MOVEOTHERCARD u16 (id | índice), u8 (0 añade | 1 quita)
             Y: 0x7122 ARRIVETRADECARD (ack)         -> X 0x7122
  listo      X: 0x7117 SETTRADESTATUS u8 (siempre 0) -> X 0x7117 u8 1 ; Y 0x7134 u8 1 ; los dos -> 0x7118 TRADEALLREADY
  confirmar  X: 0x7119 u8 (1 sí / 2 no / 0 reinicio), 10 x s32 mis cartas, 10 x s32 las suyas (-1 = hueco)
             los dos 1 -> 0x7120 u8 3 + 0x7121 TRADEEND u8 1 + baúl nuevo (0x7125..0x7127) a cada uno
             alguno 2  -> 0x7120 u8 2 a los dos (vuelven a editar)
  salir      X: 0x7112 TRADEUSEREXIT (sin respuesta) -> Y 0x7112 u32 (el pid que Y tiene en +0x1bb8)
             X: 0x7128 TRADECLEAR (sin respuesta; al cerrar el flujo en el lobby)

Uso (run.py): TP = load_research("intercambio"); TP.install(globals()); LP.HANDLERS.update(TP.HANDLERS);
y en on_conn_closed: TP.on_close(c). Ver NOTES.md §Integración.
"""
import os
import struct
import sys
import threading
from collections import Counter, deque

HERE = os.path.dirname(os.path.abspath(__file__))
EMU = os.path.normpath(os.path.join(HERE, "..", ".."))
if EMU not in sys.path:
    sys.path.insert(0, EMU)
import proto  # noqa: E402

W = proto.W

# ---------------------------------------------------------------- constantes (C = confirmado en el cliente)
SUB_TRADESTART, SUB_REQTRADE, SUB_OFFERREPLY, SUB_CANCELREQ = 0x7101, 0x7102, 0x7105, 0x7108
SUB_USEREXIT, SUB_ADDCARD, SUB_DELETECARD, SUB_SETSTATUS = 0x7112, 0x7113, 0x7114, 0x7117
SUB_LASTCONFIRM, SUB_ARRIVE, SUB_CLEAR, SUB_READY = 0x7119, 0x7122, 0x7128, 0x7132

M_REQTRADERESULT, M_TRADEOFFER, M_OFFERREPLY = 0x7103, 0x7104, 0x7105
M_CANCELNOTICE, M_USEREXIT, M_ADDCARD, M_DELETECARD = 0x7109, 0x7112, 0x7113, 0x7114
M_MOVEOTHER, M_SETSTATUS, M_ALLREADY = 0x7116, 0x7117, 0x7118
M_LASTCONFIRMRESULT, M_TRADEEND, M_ARRIVE = 0x7120, 0x7121, 0x7122
M_TRUNKSTART, M_TRUNK, M_TRUNKEND = 0x7125, 0x7126, 0x7127
M_LIMITSTART, M_LIMIT, M_LIMITEND = 0x7129, 0x7130, 0x7131
M_STARTRESULT, M_CHANGESTATUS = 0x7133, 0x7134

REQ_FAIL = -493               # C: FUN_006d1840 caso 0x2a, único valor de error de 0x7103
ADDCARD_FATAL = -1010         # C: escena 0x8fac30 estados 9/10/0xc -> +0x162=1, estado 0x10 (cierra)
MAX_CARD = 0xcb8              # C: baúl/límite = 0xcb8 B, byte i = carta id i+1
CHUNKS = (0x200,) * 6 + (0xb8,)   # C: dispatcher 0x7126/0x7130: trozo n en +n*0x200, el 6 = 0xb8 B. Nunca 8.
MAX_OFFER = 10                # C: panel +0x414 < 10 y LASTCONFIRM lleva 10 + 10 s32
CONFIRM_YES, CONFIRM_NO = 1, 2    # C: botón 0xfa0 -> 1, 0xfa1/cerrar -> 2 (0x9024d7 / 0x90222e, 0x902787)
RESULT_DONE, RESULT_BACK = 3, 2   # C: 0x7120 u8: 3 -> estado 0xd, 2 -> estado 9, 1 -> estado 0xb (no se usa: D)

# ---------------------------------------------------------------- banderas (1:1: lo que el cliente no dice, APAGADO)
LIMIT_BYTES = None            # D: qué mandaba el oficial en 0x7130 (copias NO intercambiables por carta). None = ceros
ADDCARD_REJECT_1010 = False   # D: rechazar con -1010 un ADDCARD que el baúl no cubre (si no, se cancela al final)
RESET_READY_ON_CHANGE = False # D: ¿un cambio de cartas quitaba el «listo» de los dos? (0x7134 0 + estado)
CLEAR_STATUS_AFTER_NO = False # D: tras 0x7120 = 2, ¿mandaba 0x7134 0 para apagar el «listo» del compañero?
DECLINE_VALUE = 1             # D: valor ≠0 de 0x7105 al que pidió cuando B dice que no (el cliente solo mira ≠0)
SEND_PAUSE = 0.03             # s entre mensajes (el cliente procesa uno por fotograma)

NS = {}
LOCK = threading.RLock()
TRADES = {}                   # pid -> Trade (las dos puntas apuntan al mismo objeto)


def install(ns):
    """Globals VIVOS de run.py: HUB, CTX, load_state/save_state, STATE_LOCK, PP, log."""
    global NS
    NS = ns if isinstance(ns, dict) else dict(ns)


def _log(*a):
    (NS.get("log") or print)(*a)


def _sess(c):
    return getattr(c, "sess", None)


def _name16(text):
    raw = str(text or "Duelista").encode("latin1", "replace")[:15]
    return raw + bytes(16 - len(raw))


def _s32(v):
    return struct.pack(">i", int(v))


# ---------------------------------------------------------------- estado de un intercambio
class Trade:
    """a = el que pide, b = el que recibe la oferta. phase: offer -> open -> done."""

    def __init__(self, a, ca, b):
        self.a, self.b = a, b
        self.conn = {a.pid: ca, b.pid: None}
        self.phase = "offer"
        # Lo que cada cliente tiene en CNetLobby+0x1bb8 (C: solo 0x7104 lo escribe -> el que pide se queda en 0).
        # 0x7112 solo cierra la escena si su u32 == ese valor (FUN_006bf910).
        self.known = {a.pid: 0, b.pid: a.pid}
        self.offer = {a.pid: [], b.pid: []}
        self.ready = {a.pid: False, b.pid: False}
        self.confirm = {a.pid: None, b.pid: None}
        self.started = {a.pid: False, b.pid: False}
        self.queue = {a.pid: deque(), b.pid: deque()}     # 0x7116 pendientes para ese receptor
        self.inflight = {a.pid: None, b.pid: None}        # pid del que movió la carta que ese receptor aún no confirmó

    def other(self, pid):
        return self.b if pid == self.a.pid else self.a

    def sess(self, pid):
        return self.a if pid == self.a.pid else self.b


def _trade_of(s):
    if s is None or getattr(s, "pid", None) is None:
        return None
    with LOCK:
        return TRADES.get(s.pid)


def _drop(t):
    with LOCK:
        for p in (t.a.pid, t.b.pid):
            if TRADES.get(p) is t:
                TRADES.pop(p, None)


# ---------------------------------------------------------------- envío
def _to(t, pid, mid, data=b"", c=None):
    """A la punta pid: por c si es su conexión actual (respuesta en el mismo hilo), si no por la cola del HUB."""
    s = t.sess(pid) if t is not None else None
    cc = t.conn.get(pid) if t is not None else None
    if c is not None and (cc is c or (s is not None and _sess(c) is s)):
        c.send(mid, data, SEND_PAUSE)
        return
    hub = NS.get("HUB")
    if hub is not None and s is not None and getattr(s, "lobby", None) is not None:
        hub._post(s, mid, data, SEND_PAUSE)
    elif cc is not None and not getattr(cc, "closed", False):
        (cc.post if hasattr(cc, "post") else cc.send)(mid, data, SEND_PAUSE)
    else:
        _log(f"[intercambio] 0x{mid:04x} para pid {pid}: sin conexión")


# ---------------------------------------------------------------- perfil (baúl)
class _NoLock:
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _state_lock():
    lk = NS.get("STATE_LOCK")
    return lk if lk is not None and hasattr(lk, "__enter__") else _NoLock()


def _with_profile(s, fn, save=False):
    """fn(perfil) en la vista de state.json de la cuenta de s (CTX.sess = s, como _friend_add del lobby)."""
    ctx, PP = NS.get("CTX"), NS.get("PP")
    old = getattr(ctx, "sess", None) if ctx is not None else None
    try:
        if ctx is not None:
            ctx.sess = s
        st = NS["load_state"]()
        slot = s.slot if getattr(s, "slot", None) is not None else PP.active_slot(st)
        p = PP.profile(st, slot)
        r = fn(p)
        if save:
            NS["save_state"](st)
        return r
    finally:
        if ctx is not None:
            ctx.sess = old


def _trunk_bytes(s):
    PP = NS.get("PP")
    try:
        return _with_profile(s, PP.trunk_bytes)
    except Exception as e:
        _log(f"[intercambio] baúl de {s!r}: {e!r} (se manda vacío)")
        return bytes(MAX_CARD)


def _limit_bytes(s):
    raw = LIMIT_BYTES(s) if callable(LIMIT_BYTES) else LIMIT_BYTES
    raw = bytes(raw or b"")[:MAX_CARD]
    return raw + bytes(MAX_CARD - len(raw))


def _table(start, mid, end, raw):
    out, pos = [(start, b"")], 0
    for n in CHUNKS:
        out.append((mid, raw[pos:pos + n])); pos += n
    out.append((end, b""))
    return out


def _send_trunk(t, pid, c=None):
    for mid, d in _table(M_TRUNKSTART, M_TRUNK, M_TRUNKEND, _trunk_bytes(t.sess(pid))):
        _to(t, pid, mid, d, c)


def _send_limit(t, pid, c=None):
    for mid, d in _table(M_LIMITSTART, M_LIMIT, M_LIMITEND, _limit_bytes(t.sess(pid))):
        _to(t, pid, mid, d, c)


def _available(s, cid):
    """Copias de cid que s puede ofrecer = baúl - límite (regla del cliente, FUN_00970xxx modo 1)."""
    try:
        n = _with_profile(s, lambda p: int(p.get("trunk", {}).get(str(int(cid)), 0)))
    except Exception:
        return 0
    return n - _limit_bytes(s)[cid - 1] if 1 <= cid <= MAX_CARD else 0


def _execute(t):
    """Pasa las cartas entre los dos perfiles. True si se hizo. Todo bajo STATE_LOCK (leer-comprobar-escribir)."""
    PP = NS.get("PP")
    a, b = t.a, t.b
    give_a, give_b = list(t.offer[a.pid]), list(t.offer[b.pid])
    with _state_lock():
        try:
            for s, give in ((a, give_a), (b, give_b)):
                need = Counter(give)
                lim = _limit_bytes(s)
                have = _with_profile(s, lambda p: dict(p.get("trunk", {})))
                for cid, n in need.items():
                    if not 1 <= cid <= MAX_CARD or int(have.get(str(cid), 0)) - lim[cid - 1] < n:
                        _log(f"[intercambio] {s!r} no tiene {n} x {cid}: se cancela")
                        return False

            def apply(give, take):
                def f(p):
                    PP.trunk_remove(p, give)
                    PP.trunk_add(p, take, src="intercambio")
                return f
            _with_profile(a, apply(give_a, give_b), save=True)
            _with_profile(b, apply(give_b, give_a), save=True)
        except Exception as e:
            _log(f"[intercambio] error al pasar las cartas: {e!r}")
            return False
    _log(f"[intercambio] hecho: {a!r} da {give_a}, {b!r} da {give_b}")
    try:                                               # panel (research/admin/eventos.py, 07-10): «Actividad»
        _ev = sys.modules.get("yo2_eventos")
        if _ev is not None:
            _ev.intercambio(a, b, len(give_a), len(give_b))
    except Exception:                                  # noqa: BLE001
        pass
    return True


# ---------------------------------------------------------------- cola de 0x7116 (control de flujo con 0x7122)
def _queue_move(t, mover_pid, data):
    rcv = t.other(mover_pid).pid
    t.queue[rcv].append((mover_pid, data))
    _pump(t, rcv)


def _pump(t, rcv, c=None):
    """Manda al receptor el siguiente 0x7116 si ya está en la escena (TRADESTART) y confirmó el anterior (0x7122).
    El cliente guarda un solo movimiento pendiente (+0x1bd2) y lo confirma con ARRIVETRADECARD: P."""
    with LOCK:
        if not t.started[rcv] or t.inflight[rcv] is not None or not t.queue[rcv]:
            return
        mover, data = t.queue[rcv].popleft()
        t.inflight[rcv] = mover
    _to(t, rcv, M_MOVEOTHER, data, c)


# ---------------------------------------------------------------- subcomandos
def sub_reqtrade(c, s, body):
    """0x7102 REQTRADE u32 pid (FUN_006cdf40; tarea caso 0x29/0x2a). 0x7103 s32: -493 = error, otro = enviada."""
    pid = struct.unpack(">I", body[:4])[0] if len(body) >= 4 else 0
    hub = NS.get("HUB")
    o = hub.by_pid(pid) if hub is not None else None
    why = None
    if s is None or getattr(s, "pid", None) is None:
        why = "sin sesión"
    elif o is None or getattr(o, "pid", None) is None or getattr(o, "lobby", None) is None:
        why = f"pid {pid} no conectado"
    elif o is s:
        why = "consigo mismo"
    elif _trade_of(s) is not None or _trade_of(o) is not None:
        why = "uno de los dos ya está en un intercambio u oferta"
    elif 0x67 <= int(getattr(o, "state", 0) or 0) <= 0x6a:
        why = "el otro está en duelo"
    if why:
        _log(f"[intercambio] REQTRADE -> {pid}: rechazo -493 ({why})")
        c.send(M_REQTRADERESULT, _s32(REQ_FAIL), SEND_PAUSE)
        return
    t = Trade(s, c, o)
    with LOCK:
        TRADES[s.pid] = TRADES[o.pid] = t
    c.send(M_REQTRADERESULT, _s32(0), SEND_PAUSE)
    _to(t, o.pid, M_TRADEOFFER, W().u32(s.pid).bytes() + _name16(s.name))
    _log(f"[intercambio] {s!r} pide intercambio a {o!r}")


def sub_offerreply(c, s, body):
    """0x7105 TRADEOFFERREPLY s32 (1 = sí, 0 = no; lobby 0x876970 botones 0xfa0/0xfa1). El que contesta espera
    0x7105 s32 0 (tarea caso 0x2c); el que pidió lee 0x7105 como resultado: 0 = aceptada (lobby estado 0x26)."""
    ok = struct.unpack(">i", body[:4])[0] if len(body) >= 4 else 0
    t = _trade_of(s)
    if t is None or t.phase != "offer" or t.b is not s:
        # El que pidió ya no está (o no hay oferta): con «no» -618 el cliente no dice nada (C); con «sí» sale el
        # error genérico 0x4a (C). Valor -618: P.
        c.send(M_OFFERREPLY, _s32(-618), SEND_PAUSE)
        return
    t.conn[s.pid] = c
    c.send(M_OFFERREPLY, _s32(0), SEND_PAUSE)
    if ok:
        t.phase = "open"
        _to(t, t.a.pid, M_OFFERREPLY, _s32(0))
        _log(f"[intercambio] {s!r} acepta a {t.a!r}")
    else:
        _drop(t)
        _to(t, t.a.pid, M_OFFERREPLY, _s32(DECLINE_VALUE))
        _log(f"[intercambio] {s!r} rechaza a {t.a!r}")


def sub_cancelreq(c, s, body):
    """0x7108 CANCELREQTRADE: su emisor (FUN_006d0350) no tiene llamadas en el exe (C). Por si acaso: 0x7109 al que
    cancela (+0x54) y al destino (+0x54, y +0x1bda = oferta retirada: FUN_006bf650)."""
    t = _trade_of(s)
    c.send(M_CANCELNOTICE, b"", SEND_PAUSE)
    if t is not None and t.phase == "offer" and t.a is s:
        _drop(t)
        _to(t, t.b.pid, M_CANCELNOTICE)


def sub_ready(c, s, body):
    """0x7132 TRADEREADY (escena 0x8fac30 estado 1; no espera +0x4e). La escena espera el baúl (+0x5f, estado 3) y
    luego el límite (+0x62, estado 4) y entonces manda TRADESTART."""
    t = _trade_of(s)
    if t is None or t.phase != "open":
        _log(f"[intercambio] TRADEREADY de {s!r} sin intercambio abierto: no se contesta")
        return
    t.conn[s.pid] = c
    _send_trunk(t, s.pid, c)
    _send_limit(t, s.pid, c)


def sub_start(c, s, body):
    """0x7101 TRADESTART -> 0x7133 TRADESTARTRESULT vacío (+0x4f; estado 2 -> 9)."""
    c.send(M_STARTRESULT, b"", SEND_PAUSE)
    t = _trade_of(s)
    if t is not None and t.phase == "open":
        t.started[s.pid] = True
        _pump(t, s.pid, c)


def _unready(t, c):
    for p in (t.a.pid, t.b.pid):
        if t.ready[p]:
            t.ready[p] = False
            _to(t, t.other(p).pid, M_CHANGESTATUS, b"\x00", c)


def sub_addcard(c, s, body):
    """0x7113 ADDCARD u16 id de carta (0x902f6a: arrastrar al panel propio; el cliente ya la pinta). -> 0x7113
    s32 0; al otro 0x7116 (id, 0) -> FUN_00993090 la añade a SU lista de «cartas del otro»."""
    cid = struct.unpack(">H", body[:2])[0] if len(body) >= 2 else 0
    t = _trade_of(s)
    if t is None or t.phase != "open":
        c.send(M_ADDCARD, _s32(0), SEND_PAUSE)
        return
    mine = t.offer[s.pid]
    bad = not (1 <= cid <= MAX_CARD) or len(mine) >= MAX_OFFER or mine.count(cid) >= _available(s, cid)
    if bad and ADDCARD_REJECT_1010:
        c.send(M_ADDCARD, _s32(ADDCARD_FATAL), SEND_PAUSE)
        _abort(t, s, c, "ADDCARD imposible")
        return
    if bad:
        _log(f"[intercambio] ADDCARD {cid} de {s!r} no cubierto por su baúl (se aceptará y se cancelará al final)")
    if len(mine) >= MAX_OFFER:
        c.send(M_ADDCARD, _s32(0), SEND_PAUSE)
        return
    mine.append(cid)
    c.send(M_ADDCARD, _s32(0), SEND_PAUSE)
    if RESET_READY_ON_CHANGE:
        _unready(t, c)
    _queue_move(t, s.pid, W().u16(cid).u8(0).bytes())


def sub_deletecard(c, s, body):
    """0x7114 DELETECARD u16 ÍNDICE en la lista propia (0x90304c: FUN_00993680 quita por posición). -> 0x7114 s32 0;
    al otro 0x7116 (índice, 1) -> FUN_00993550 quita por posición."""
    idx = struct.unpack(">H", body[:2])[0] if len(body) >= 2 else 0xffff
    t = _trade_of(s)
    c.send(M_DELETECARD, _s32(0), SEND_PAUSE)
    if t is None or t.phase != "open" or idx >= len(t.offer[s.pid]):
        return
    t.offer[s.pid].pop(idx)
    if RESET_READY_ON_CHANGE:
        _unready(t, c)
    _queue_move(t, s.pid, W().u16(idx).u8(1).bytes())


def sub_arrive(c, s, body):
    """0x7122 ARRIVETRADECARD (sin campos; la escena lo manda tras aplicar un 0x7116). Se reenvía al que movió la
    carta (0x7122 pone +0x60 -> la escena limpia +0x56/+0x57) y sale el siguiente 0x7116."""
    t = _trade_of(s)
    if t is None:
        return
    with LOCK:
        mover = t.inflight[s.pid]
        t.inflight[s.pid] = None
    if mover is not None:
        _to(t, mover, M_ARRIVE, b"", c)
    _pump(t, s.pid, c)


def sub_setstatus(c, s, body):
    """0x7117 SETTRADESTATUS u8 (único emisor 0x99278a, siempre 0 = botón «Listo»). La escena (estado 10) lee 0x7117
    u8: 0 -> vuelve a 9 (editar), 1 -> 0xb (listo, espera), otro -> +0x15c (= 0xb). Al otro 0x7134 u8 (+0x1bd5;
    FUN_009943d0 con 0/1). Los dos listos -> 0x7118 TRADEALLREADY a los dos (+0x5b; estado 0xb -> diálogo final)."""
    t = _trade_of(s)
    if t is None or t.phase != "open":
        c.send(M_SETSTATUS, b"\x00", SEND_PAUSE)
        return
    t.ready[s.pid] = True
    t.confirm = {t.a.pid: None, t.b.pid: None}
    c.send(M_SETSTATUS, b"\x01", SEND_PAUSE)
    o = t.other(s.pid)
    _to(t, o.pid, M_CHANGESTATUS, b"\x01", c)
    if all(t.ready.values()):
        _to(t, s.pid, M_ALLREADY, b"", c)
        _to(t, o.pid, M_ALLREADY, b"", c)


def sub_lastconfirm(c, s, body):
    """0x7119 TRADELASTCONFIRM u8 + 10 s32 (mis cartas) + 10 s32 (las del otro), -1 = hueco (FUN_006ce290 y
    FUN_00994540). u8: 1 sí, 2 no, 0 = lo manda la escena SIEMPRE tras recibir 0x7120 (no se contesta: P)."""
    if len(body) < 1:
        return
    choice = body[0]
    vals = list(struct.unpack(">20i", body[1:81])) if len(body) >= 81 else []
    t = _trade_of(s)
    if t is None or t.phase != "open" or choice == 0:
        return
    if vals:
        mine = [v for v in vals[:10] if v != -1]
        theirs = [v for v in vals[10:] if v != -1]
        o = t.other(s.pid)
        if choice == CONFIRM_YES and (mine != t.offer[s.pid] or theirs != t.offer[o.pid]):
            _log(f"[intercambio] LASTCONFIRM de {s!r} no casa con el servidor: cliente {mine}/{theirs}, "
                 f"servidor {t.offer[s.pid]}/{t.offer[o.pid]} (manda el servidor)")
    t.confirm[s.pid] = choice if choice in (CONFIRM_YES, CONFIRM_NO) else CONFIRM_NO
    a, b = t.a.pid, t.b.pid
    if CONFIRM_NO in t.confirm.values():
        t.ready = {a: False, b: False}
        t.confirm = {a: None, b: None}
        for p in (a, b):
            _to(t, p, M_LASTCONFIRMRESULT, bytes([RESULT_BACK]), c)
            if CLEAR_STATUS_AFTER_NO:
                _to(t, p, M_CHANGESTATUS, b"\x00", c)
        return
    if None in t.confirm.values():
        return                                   # espera al otro (sigue en el estado 0xc)
    done = _execute(t)
    t.phase = "done"
    for p in (a, b):
        _to(t, p, M_LASTCONFIRMRESULT, bytes([RESULT_DONE]), c)
        _to(t, p, M_TRADEEND, b"\x01" if done else b"\x00", c)
        if done:                                 # estado 0xf espera +0x5f (baúl nuevo) tras «Intercambio Completado»
            _send_trunk(t, p, c)


def _abort(t, s, c, why):
    """s sale: al otro 0x7112 con el pid que él tiene en +0x1bb8 (si no casa, su escena no se entera)."""
    _drop(t)
    if t.phase == "done":
        return
    o = t.other(s.pid)
    _to(t, o.pid, M_USEREXIT, W().u32(t.known[o.pid]).bytes(), c)
    _log(f"[intercambio] {s!r} sale ({why}): aviso a {o!r}")


def sub_userexit(c, s, body):
    """0x7112 TRADEUSEREXIT (0x8f7019, salvo en estados 0xd-0x10; no espera nada)."""
    t = _trade_of(s)
    if t is not None:
        if t.phase == "offer" and t.b is s:
            _drop(t)
            _to(t, t.a.pid, M_OFFERREPLY, _s32(DECLINE_VALUE), c)
        else:
            _abort(t, s, c, "TRADEUSEREXIT")


def sub_clear(c, s, body):
    """0x7128 TRADECLEAR (lobby estado 0x30 -> 0x31, FUN_00879570; no espera nada). Limpia lo que quede."""
    t = _trade_of(s)
    if t is not None:
        _abort(t, s, c, "TRADECLEAR")


SUBS = {
    SUB_REQTRADE: sub_reqtrade, SUB_OFFERREPLY: sub_offerreply, SUB_CANCELREQ: sub_cancelreq,
    SUB_READY: sub_ready, SUB_TRADESTART: sub_start, SUB_ADDCARD: sub_addcard, SUB_DELETECARD: sub_deletecard,
    SUB_ARRIVE: sub_arrive, SUB_SETSTATUS: sub_setstatus, SUB_LASTCONFIRM: sub_lastconfirm,
    SUB_USEREXIT: sub_userexit, SUB_CLEAR: sub_clear,
}


def h_trade(c, msg_id, param, data):
    """0x7000: u16 subcomando + campos. Los subcomandos sin respuesta conocida no se contestan."""
    sub = struct.unpack(">H", data[:2])[0] if len(data) >= 2 else 0
    fn = SUBS.get(sub)
    if fn is None:
        _log(f"[intercambio] subcomando 0x{sub:04x} desconocido: sin respuesta")
        return True
    with LOCK:
        fn(c, _sess(c), bytes(data[2:]))
    return True


def on_close(c):
    """Conexión cerrada: si era la de una punta de un intercambio u oferta, el otro se entera."""
    s = _sess(c)
    t = _trade_of(s)
    if t is None:
        return
    if t.conn.get(s.pid) is not c and getattr(s, "lobby", None) is not c:
        return
    with LOCK:
        if t.phase == "offer" and t.b is s:
            _drop(t)
            _to(t, t.a.pid, M_OFFERREPLY, _s32(DECLINE_VALUE))
        else:
            _abort(t, s, None, "conexión cerrada")


HANDLERS = {0x7000: h_trade}


def handle(c, msg_id, param, data):
    h = HANDLERS.get(msg_id)
    return bool(h and h(c, msg_id, param, data))
