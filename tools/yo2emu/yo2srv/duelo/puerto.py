"""El puerto de duelo 5760 (paso E6 de la reestructura, REESTRUCTURA.md §3.4): `duel_reply` (el manejador del puerto, con
la NPC y el PvP), la cola de trabajo de cada conexión de duelo (`duel_task` / `_duel_worker`), la sesión ocgcore de la
conexión (`ocg_session`, `npc_deck_id`, `ocg_finish`) y los datos de los dos duelistas (`cur_world`, `duel_parts`,
`preparation_result`).

Es el código de run.py MOVIDO sin cambiar la lógica. Las dos definiciones de `duel_reply` que había (la base, y el
envoltorio que se ponía encima al cargar research/lockstep) son ahora:
  - `duel_reply_base`: la antigua primera, el servidor de duelo «de esqueleto» (saludo, piedra-papel-tijera, PREPARATION…).
    run.py se la da a lockstep (`LS.duel_reply_base = duel_reply_base`), que la llama para lo que no trata él;
  - `duel_reply`: UNA sola función, la que está en `HANDLERS[DUEL_PORT]`. Con research/lockstep cargado (`srv.LS`) es el
    envoltorio de siempre (espectador de NPC, PvP, rendición, ocgcore, mazo bloqueado, `LS.duel_reply_v2`); sin él (la carga
    falló: no hay `LS` en el namespace) es la rama «sin LS» del principio, que hace exactamente lo que hacía la base sola.
Reglas (REESTRUCTURA.md §2.3): todo lo que puede reasignarse o parchearse en caliente (`LS`, `PVP`, `OCG`, `DFM`, `DF`,
`df_for`, `duel_task`, `preparation_result`, `load_state`, `NPCS`, `log`, las banderas…) se lee como `srv.X` en el momento
de usarlo; lo que puede no existir (`NW`, `DK`, `LS`) con `getattr(srv, "X", None)`, que equivale al `globals().get("X")` de
antes. La carga en caliente de research/cuentas/hot_sin_sesion.py cambia el `__code__` de `ocg_session` y `_duel_worker`,
y la de research/lobby/hot_npc_mirar.py el de `duel_reply`: por eso solo leen `srv.X`, builtins y los módulos que un
run.py de siempre ya tiene (`proto`, `queue`, `threading`, `traceback`).
"""
import queue
import threading
import traceback

import proto

srv = None      # el Server; lo fija setup(). Hasta entonces nada de este módulo se puede llamar.


def setup(servidor):
    """Enlaza el módulo con el Server (idempotente). run.py lo llama (yo2srv.duelo.setup) justo antes de importar los
    nombres de aquí."""
    global srv
    srv = servidor
    return servidor


def cur_world():
    """V09 (P0-8): mundo (0 Domino, 1 Academia) del jugador de ESTE hilo (su sesión lo apunta en cada 0x4170,
    pvp_hub.on_lobby_msg). AV_STATE["world"] es el último 0x4170 de CUALQUIERA: con dos jugadores, el retrato del duelo
    y las piezas de las NPC salían con el mundo del otro. Sin sesión (pruebas, consola): el global de siempre.
    (Aquí, dentro del bloque de reload_duel.py, para que una recarga de solo el duelo también la tenga.)"""
    s = srv.cur_sess()
    w = getattr(s, "world", None) if s is not None else None
    return srv.AV_STATE["world"] if w is None else w

def duel_parts(rival, world=None):
    """16 B de avatar para el duelo: rival None = el jugador (perfil); NPC normal = sus piezas válidas (sin ellas el
    retrato sale como el chico por defecto); famosas (51-94) = ceros, usan su retrato propio. world: 0 Domino,
    1 Academia (por defecto, el actual)."""
    w = srv.cur_world() if world is None else world  # V09: el de la sesión de este hilo
    if rival is None:
        try:
            st = srv.load_state(); a = srv.PP.profile(st, srv.PP.active_slot(st)).get("avatar", {})
            h = a.get("b" if w == 1 else "a") or a.get("a")
            if h:
                return bytes.fromhex(h)[:16].ljust(16, bytes(1))
        except Exception:
            pass
        return bytes.fromhex("0000203040608090b820000000000000")
    if srv.AV is not None and not 51 <= rival <= 94:
        return srv.AV.npc_parts(rival, srv.NPC_SEX.get(rival), w)
    return bytes(16)

def preparation_result():
    """PREPARATION_RESULT 0x600c (FUN_006918d0), 168 B: nombre A16, nombre B16, u32 id A, u32 id B, s32 A, s32 B,
    3×16 B de A, 3×16 B de B, 4 B A (país), 4 B B, 2×u32 A, 2×u32 B. A = el jugador, B = el rival."""
    st = srv.load_state(); me = srv.my_name(st)
    rival = srv.duel_rival()                 # V08: la NPC que retó ESTE jugador (antes DUEL["rival"], de cualquiera)
    rv = next((n[1] for n in srv.NPCS if n[0] == rival), f"Rival {rival}")
    w = proto.W().s(me, 16).s(rv, 16).u32(srv.my_pid(st)).u32(rival).u32(8000).u32(8000).bytes()   # LP
    # 3 bloques de avatar por lado, uno por mundo: el retrato del duelo usa el del mundo actual (FUN_0087xxxx caso 0x40:
    # base + mundo*0x11 -> FUN_0079a370). 0 Domino, 1 Academia, el 3.º sin uso visto.
    w += srv.duel_parts(None, 0) + srv.duel_parts(None, 1) + bytes(16)
    w += srv.duel_parts(rival, 0) + srv.duel_parts(rival, 1) + bytes(16) + b"ESP" + bytes(1) + b"ESP" + bytes(1) + bytes(16)
    return w

def duel_reply_base(c, msg_id, param, data):
    """Servidor de duelo (lo anuncia DUELSERVERNOTICE 0x4306: ip15 + u16 puerto). De momento: saludo como el de cuentas
    y respuestas por defecto (AUTO); todo lo demás se registra para sacar el protocolo de duelo.
    (Antes `duel_reply` a secas; run.py se la da a lockstep como `LS.duel_reply_base`.)"""
    if msg_id in (0x3001, 0x3003):
        return srv.game_reply(c, msg_id, param, data)
    if msg_id == 0x0005:
        return c.send(0x0004, proto.W().u32(0).bytes())
    if msg_id in (0x6600, 0x6601, 0x6603):   # mazo del jugador: cabecera, principal (u32 n + u16/carta), extra
        if srv.PH:
            srv.PH["h_duel_deck"](c, msg_id, data)   # se guarda en el perfil (decks.last_duel)
        return
    # Arranque: el servidor devuelve cada paso (0x6100 GOTOJYANKEN, 0x6102 GOTOSELECTFIRST, 0x6103 SELECTFIRST_RESULT
    # con el u8 elegido, 0x6104 GOTODUELSTART); el cliente espera ese eco para pasar de pantalla (indicadores +0x1e..).
    if msg_id in (0x6100, 0x6102, 0x6103, 0x6104):
        c.send(msg_id, data[:1] if msg_id == 0x6103 else b"")
        if msg_id == 0x6102:   # con JYANKEN_RESULT 0 gana la NPC y elige ella; con 0 el cliente esperaba la 1ª
            c.send(0x6103, bytes([srv.DUEL.get("first", 1)]))   # jugada del rival: se prueba 1 = empiezas tú
        return
    if msg_id == 0x61fe:      # el cliente ha montado el tablero (u8) -> GOTODUELSTART
        return c.send(0x6104)
    if msg_id == 0x6101:      # mano del piedra-papel-tijera -> JYANKEN_RESULT (u8 resultado, u8 mano del rival)
        mine = data[0] if data else 0
        return c.send(0x6101, bytes([0, (mine + 2) % 3]))   # gana siempre el jugador (por confirmar el código)
    if msg_id == 0x600b:      # PREPARATION -> PREPARATION_RESULT (datos de los dos duelistas)
        c.send(0x600c, srv.preparation_result())
        # DUEL_OTHERDECKINFO (u16 principal, u16 extra): el cliente monta el mazo del rival como cartas tapadas con
        # ese tamaño (+0x5f8/+0x5fc -> FUN_0079cbf0). Sin él, el tamaño es basura y se sale de memoria (0x704d98).
        # OTHERDECKINFO: tamaño del mazo de la NPC = el que le impone el servidor (hoy, copia del mazo del jugador)
        _ls = getattr(srv, "LS", None); _cd = _ls.DUEL_CFG.get("_st", {}).get("client_deck") if _ls else None
        _n = len(_ls.DUEL_CFG.get("npc_deck") or _cd or ()) if _ls else 0
        return c.send(0x6005, proto.W().u16(_n or 40).u16(0).bytes())
    if msg_id == 0x6001:      # comprobación de reserva (u32 id jugador) -> BOOKED_CHECK_RESULT s32 > 0 = reservado
        return c.send(0x6007, proto.W().u32(1).bytes())     # con 0: «LB_DUEL_BOOKEDCHECK_WAIT ERROR» y se cierra
    if not srv.auto_reply(c, msg_id):
        srv.log(f"[srv:{c.port}] sin respuesta para 0x{msg_id:04x}")

# Duelo con servidor autoritativo (agente 2, research/lockstep: NOTES.md). El motor del cliente solo ejecuta los
# 0x6202 que le llegan: tras 0x61fe el servidor manda DUEL_START (confirmado en vivo), las cartas reales de los dos
# mazos (tipo 0x40), el robo de 5 y 5 (confirmado en vivo), las fases y la petición de jugada. Lo demás, duel_reply_base.
def duel_reply(c, msg_id, param, data):
    if getattr(srv, "LS", None) is None:      # sin research/lockstep (no cargó): el duelo base de siempre
        return srv.duel_reply_base(c, msg_id, param, data)
    # 07-10 (npc_watch.py; añadido nuestro, aprobado por el owner): el espectador de un duelo de NPC (su 0x6700 y
    # todo lo de su conexión) se queda ahí y no sigue; del jugador de NPC solo se apunta (mano, «¿Te rindes?»,
    # arranque con 0x61fe) y sigue como siempre. Nunca lanza.
    _nw = getattr(srv, "NW", None)
    if _nw is not None and _nw.on_duel_msg(c, msg_id, data):
        return
    # PvP fase 2 (V06): si el pid de 0x6001 tiene una reserva de reto aceptado, TODA esta conexión de duelo la lleva
    # su PvpMatch (pvp_match.py); si no, el flujo de la NPC de siempre, sin cambios.
    _pvp = getattr(srv, "PVP", None)
    if _pvp is not None:
        m = _pvp.route(c, msg_id)
        if m is not None:
            return m.on(c, msg_id, param, data)
    DF = srv.df_for(c)                        # V08: el duelfin de la sesión de c (o el global sin sesión)
    # El cliente vuelve a mandar 0x61fe (con u8 1) en mitad del duelo, p. ej. al tocar las opciones: solo el
    # primero de cada conexión arranca el duelo; si no, se reparten otra vez mazos y manos encima de la partida.
    srv.df_end_mark(c, DF, msg_id)            # B58: qué partida de duelfin es la de esta conexión
    if msg_id == 0x61fe:
        _s = srv.ocg_session(c)
        if _s is not None:                    # u8 = opción «Auto Card Placing» del cliente (1 = manual); también al cambiarla en pleno duelo
            _s.place_manual = bool(data[:1] and data[0])
            if getattr(_s, "d", None) is not None:
                _s.d.place_manual = _s.place_manual
        if getattr(c, "duel_started", False):
            srv.log(f"[duelo] 0x61fe repetido ({data.hex()}): ignorado")
            return
        c.duel_started = True
    s = srv.ocg_session(c)
    if DF is not None and msg_id == 0x6350:   # «Rendirse» (bit 10): primero diálogo SÍ/NO
        r = DF["h_6350"](c, data)
        if r is True and s is not None:
            s.surrender()
            srv._admin_npc("npc_end", c, 1, "surrender")
        if r:
            return
    if DF is not None and msg_id == 0x6423:   # respuesta del diálogo de rendición
        r = DF["h_6423"](c, data)
        if r == "end" and s is not None:
            s.surrender()
            srv._admin_npc("npc_end", c, 1, "surrender")
        elif r == "no" and s is not None and s.d is not None:
            with s.lock:
                s.out.put(s.d._resend())
        if r:
            return
    _dk = getattr(srv, "DK", None)            # mazo bloqueado (YO2_DECK_CHECK=bloquear): el duelo no empieza
    if _dk is not None and getattr(c, "deck_block", None) and _dk.npc_gate(c, msg_id, data):
        return
    if s is not None:                         # duelo con reglas reales (ocg_duel.py)
        if msg_id in (0x6601, 0x6603):
            s.client_deck(msg_id, data)       # y sigue: run.py lo guarda también en el perfil
            if msg_id == 0x6603 and _dk is not None:
                _dk.npc_deck(c, s)            # mazo completo: baúl y reglas de 2008 (avisar / bloquear)
        elif msg_id == 0x600b:
            c.send(0x600c, srv.preparation_result())
            n_main, n_extra = s.npc_sizes()   # OTHERDECKINFO = tamaño del mazo oficial de la NPC
            return c.send(0x6005, proto.W().u16(n_main).u16(n_extra).bytes())
        elif msg_id == 0x61fe:
            first = (getattr(c, "duel_st", None) or {}).get("first", 0)
            srv.duel_task(c, s.start, first)
            return
        elif msg_id in srv.OCG_CLIENT_MSGS:
            srv.duel_task(c, s.on_client, msg_id, data)
            return
    if DF is not None and msg_id == 0x0003:   # el cliente cierra la sesión de duelo tras los resultados
        DF["h_0003"](c, data)
        return
    return srv.LS.duel_reply_v2(c, msg_id, param, data)

# Duelo con reglas reales (ocg_duel.py = duelcore/ocgcore + research/traductor + mazos oficiales de NPC). DUEL_MODE
# «ocg» lo usa; «skeleton» vuelve al esqueleto sin reglas de research/lockstep. Si no carga, esqueleto.
# (DUEL_MODE, OCG_CLIENT_MSGS y la carga de OCG se quedan en run.py.)

def duel_task(c, fn, *args):
    """B49 (lado run.py): antes había un hilo por mensaje del cliente y el RLock de la sesión no es FIFO, así que dos
    mensajes seguidos podían aplicarse al revés. Ahora cada conexión de duelo tiene UNA cola y UN hilo trabajador:
    s.start y s.on_client se ejecutan en el orden de llegada. B02: una excepción dentro va al log como [EXC] con la
    traza (antes, a stderr de una consola oculta y el duelo se quedaba quieto sin rastro)."""
    q = getattr(c, "duel_q", None)
    if q is None:
        q = c.duel_q = queue.Queue()
        threading.Thread(target=srv._duel_worker, args=(c, q), daemon=True, name=f"duelo-{c.port}-{id(c) & 0xffff:04x}").start()
    q.put((fn, args))

def _duel_worker(c, q):
    while True:
        item = q.get()
        if item is None:                         # la conexión se cerró
            return
        fn, args = item
        srv.CTX.sess = getattr(c, "sess", None) or srv.SIN_SESION   # V02: el perfil de la cuenta de c (07-10: nunca el dueño)
        try:
            fn(*args)
        except Exception as e:
            s = getattr(c, "ocg", None)
            tb = traceback.format_exc().replace(chr(10), " | ")
            srv.log(f"[EXC] duelo {getattr(s, 'id', '?')} en {getattr(fn, '__name__', fn)}: {e!r} | {tb}")

def ocg_session(c):
    """Sesión ocg de esta conexión de duelo (se crea con el primer mensaje), o None en modo esqueleto."""
    if srv.OCG is None or srv.DUEL_MODE != "ocg":
        return None
    s = getattr(c, "ocg", None)
    if s is None and srv.cur_sess() is srv.SIN_SESION:
        # 07-10: el 1.er mensaje de la 1.ª conexión de duelo es 0x0005, ANTES de 0x6001 (que engancha la sesión): la
        # sesión ocg se creaba ahí con el rival global y el NPCBIN del dueño (famous_decks). Ahora se crea con el
        # primer mensaje que ya tiene sesión; sin sesión no hay duelo.
        return None
    if s is None:
        s = c.ocg = srv.OCG.OcgSession(c, srv.log, srv.ocg_finish, srv.npc_deck_id(srv.duel_rival(getattr(c, "sess", None))))   # V08
    return s

def npc_deck_id(rival):
    """NPC_RULES_2008 famous_decks: el famoso del nivel 1 juega con la receta de su nivel en curso (NPCBIN b3)."""
    if not srv.npc_rule("famous_decks") or not 1 <= int(rival or 0) <= 98:
        return rival
    try:
        st = srv.load_state(); p = srv.DFM.PS.profile(st, srv.DFM.PS.active_slot(st), create=False) or {}
        nid = srv.NR.deck_npc(rival, lambda i: srv.DFM.PS.npcbin_get(p, i))
        if nid != rival:
            srv.log(f"[npcreglas] NPC {rival}: juega con la receta {10000 + nid}")
        return nid
    except Exception as e:
        srv.log(f"[npcreglas] deck_npc: {e!r}"); return rival

def ocg_finish(c, winner, reason, turns=0):
    df = srv.df_for(c)                           # V08: el fin va al lobby y al perfil de la sesión de c
    if df is None:
        return srv.log("[ocg] fin de duelo sin research/duelfin: el cliente se quedará en el tablero")
    # B50: reason es el número de WIN de ocgcore (1 LP, 2 mazo, 0x10 Exodia…) o un texto; winner None = empate
    if hasattr(srv.DFM, "why_from_ocg"):
        why = srv.DFM.why_from_ocg(reason)
    else:
        why = srv.DFM.WHY_DECKOUT if "deck" in str(reason).lower() else srv.DFM.WHY_LP
    df["finish"](c, winner, why, turns=turns)
    srv._admin_npc("npc_end", c, winner, why, turns)   # panel: Actividad ("lp", "deckout", "exodia", "abort"…)
