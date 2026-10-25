"""Duelos contra NPC vistos desde fuera: pose «en duelo» y «Mirar» (espectador). AÑADIDO NUESTRO, aprobado por el owner
el 07-10 (no es 1:1: no hay evidencia de qué hacía Konami con un duelo de NPC; research/lobby/NOTES.md «Mundo 2008» C
y §«Espectador de duelos contra NPC»).

- Pose: cuando el duelo contra la NPC EMPIEZA de verdad (primer 0x61fe de la conexión de duelo, con la sesión ocg ya
  creada), los demás del bloque reciben 0x3112 op 0x67 + nombre de la NPC (LV/V/D/título 0: una NPC no tiene récord)
  si pvp_hub.NPC_DUEL_STATE. Así no sale si el reto se rechaza (dp_npc_gate, −612) ni si el duelo no llega a empezar.
  Se quita (op 1 por pvp_hub.set_state(s, 0)) en cuanto el duelo acaba por lo que sea: 0x4d al jugador (victoria,
  derrota, rendición, empate, duelcore caído: todos pasan por duelfin finish) o la conexión de duelo del jugador se cae
  / se sustituye / su sesión ocg acaba sin 0x4d en END_GRACE s. Al volver al mapa (0x4202/0x4210/0x5204) pvp_hub
  también la quita si siguiera puesta.
- Espectador: 0x4320 con el pid de quien tiene un duelo de NPC en marcha -> research/lobby h_watch -> 0x4321 = 0 +
  servidor de duelo y se apunta (IP -> duelo, WATCH_REQ_TTL s). El 0x6700 de esa IP engancha la conexión: 0x6006
  (jugador 0 = el humano, jugador 1 = la NPC), 0x6701 (manos y quién empieza) y, a cada 0x6702, la siguiente tanda de
  los 0x6202 que ha recibido el jugador DESDE EL ARRANQUE (lo ya pasado también). Igual que el espectador de PvP
  (pvp_match _watch_*: mismas constantes WATCH_BATCH, WATCH_REQ_TTL, WATCH_SEND_PREP, COUNTRY). Sin pid ni sesión: c.pvp
  = un _Proxy para que run.py lo trate como espectador (on_conn_closed no lo cuenta como abandono de duelo y la reserva
  de duelo, research/seguridad_duelo, rechaza/apunta un 0x6001 suyo como «espectador», igual que en PvP).
- Grabación: la conexión de duelo del jugador lleva un c.send propio (atributo de la instancia, no de la clase Conn)
  que llama al Conn.send de siempre y DESPUÉS apunta el 0x6202 (y la mano de la NPC del 0x6101). Nunca lanza: un fallo
  aquí solo va al log. El diálogo «¿Te rindes?» (DFM.surrender_confirm_msgs) no se apunta: es solo del jugador.
- Aislamiento: el espectador no tiene sesión ni toca perfil, DP, premios, NPCBIN ni el tiempo del jugador; lo suyo sale
  por su propia cola (Conn.post) y nunca entra en el flujo de la NPC de run.py (on_duel_msg lo para antes).

- Archivo de repeticiones del servidor (07-10, OK del owner; NUESTRO: research/repeticion/NOTES.md §8, rep_archivo.py):
  con pvp_match.REPLAY_ARCHIVE, al acabar el duelo (0x4d al jugador o corte) los 0x6202 grabados van a
  <datos>/repeticiones/AAAA-MM/npc_<fecha>_<n>.log (formato exacto del .log del cliente: el humano es el jugador 0 y la
  NPC el 1; solo el lado 0) + una línea en indice.jsonl. Ganador, motivo y turnos salen del fin del motor
  (OcgSession.finish, envuelto en _arm), de la rendición (0x6423 SÍ) o del corte (desconexión). Manos del
  piedra-papel-tijera y quién empieza: las del duelo (c.npc_hand0/1 y OcgSession.d.first), no se suponen. En un hilo
  aparte; un fallo solo va al log.

Flags: YO2_NPC_WATCH=0 apaga el «Mirar» de NPC y la pose (la pose depende además de pvp_hub.NPC_DUEL_STATE,
YO2_NPC_DUEL_STATE=0); la grabación sigue si REPLAY_ARCHIVE está encendido (YO2_REPLAY_ARCHIVE=0 la apaga). Autoprueba
sin red: python npc_watch.py --test
"""
import os
import struct
import threading
import time
import traceback

NPC_WATCH = os.environ.get("YO2_NPC_WATCH", "1") not in ("", "0")
END_GRACE = 30.0          # s con la sesión ocg acabada (over) sin 0x4d antes de darlo por cortado (ocg_duel END_DRAIN = 20)
KEEP_DONE = 120.0         # s que se guarda un duelo acabado con espectadores (los que aún piden su última tanda)
KEEP_DONE_IDLE = 10.0     # ídem sin espectadores
TICK = 1.0                # vigilante (conexión del jugador caída, duelo sin 0x4d, peticiones caducadas)
MAX_WATCHERS = 16         # espectadores por duelo
T_FINISH = 0x4d           # 0x6202 tipo 0x4d: el cliente sale del tablero (research/duelfin)


def _is_finish(d):
    return len(d) >= 8 and (struct.unpack(">I", d[:4])[0] & 0xff) == T_FINISH


def _end_from_entries(entries):
    """(ganador, motivo) del último 0x4d grabado (P0: 1 gana el jugador, 2 pierde, 3 empate; research/duelfin) o
    (None, "abort") si no hay: solo para duelos armados antes de cargar el archivo en caliente (sin OcgSession.finish
    envuelto)."""
    for e in reversed(entries):
        if _is_finish(e) and len(e) >= 12:
            p0 = struct.unpack(">I", e[8:12])[0]
            if p0 in (1, 2):
                return p0 - 1, None
            if p0 == 3:
                return None, "draw"
    return None, "abort"


class _Duel:
    """Un duelo contra una NPC que se puede mirar (uno por pid; el último manda)."""

    def __init__(self, c, sess, rival, name, info, prep, hands, first):
        self.c, self.sess, self.pid = c, sess, sess.pid
        self.rival, self.name = rival, name
        self.info, self.prep = info, prep          # 0x6006 (147 B) y 0x600c opcional (WATCH_SEND_PREP)
        self.hands, self.first = hands, first      # piedra-papel-tijera que decidió (humano, NPC) y quién empieza
        self.entries = []                          # 0x6202 del jugador desde el arranque
        self.watchers = []                         # {"c", "i", "want", "jseen", "gone"}
        self.lock = threading.RLock()
        self.t0 = time.time()
        self.end_t = None                          # fin visto (0x4d) o cortado
        self.over_t = None                         # sesión ocg «over» sin 0x4d todavía
        self.ask = False                           # diálogo «¿Te rindes?» abierto
        self.done = False
        self.fin = None                            # (ganador, motivo de ocgcore, turnos) de OcgSession.finish
        self.why = None                            # "surrender" si el jugador dijo SÍ a «¿Te rindes?»
        self.arch = None                           # datos para el archivo de repeticiones (_arch_info, en _arm)
        self.archived = False

    def __repr__(self):
        return f"<duelo NPC {self.name!r} de pid {self.pid}{' acabado' if self.done else ''}>"


class _Proxy:
    """Lo que queda en c.pvp de una conexión de espectador de NPC (run.py on_conn_closed / reserva de duelo)."""

    def __init__(self, nw, duel):
        self.nw, self.duel = nw, duel
        self.name = f"npc-{duel.pid}"

    @property
    def done(self):
        return self.duel.done

    def on(self, c, msg_id, param, data):
        self.nw._watch_msg(self.duel, c, msg_id, bytes(data))

    def on_close(self, c):
        self.nw._watch_close(self.duel, c)


class NpcWatch:
    def __init__(self, env):
        self.env = env                             # globals() VIVO de run.py
        self.lock = threading.RLock()
        self.duels = {}                            # pid -> _Duel
        self.reqs = {}                             # IP -> (_Duel, hora del 0x4320)
        self.ticker = None
        self.autotick = True                       # False (autoprueba): sin hilo vigilante, tick() a mano
        self.stats = {"duelos": 0, "espectadores": 0, "cortados": 0}

    # ------------------------------------------------------------------ utilidades
    def log(self, *a):
        (self.env.get("log") or print)(*a)

    def _pm(self, k, default):
        m = self.env.get("pvp_match")
        return getattr(m, k, default) if m is not None else default

    def _post(self, c, mid, data=b""):
        """Al espectador, por su cola (Conn.post: no duerme en el hilo que llama, que puede ser el del jugador)."""
        OCG = self.env.get("OCG")
        pause = getattr(OCG, "SEND_PAUSE", 0.05)
        p = getattr(c, "post", None)
        if p is not None:
            p(mid, bytes(data), pause=pause)
        else:
            c.send(mid, bytes(data), pause=pause)

    def _confirm(self):
        DFM = self.env.get("DFM")
        try:
            return {bytes(d) for m, d in DFM.surrender_confirm_msgs()} if DFM is not None else set()
        except Exception:                          # noqa: BLE001
            return set()

    # ------------------------------------------------------------------ entrada (run.py duel_reply, ANTES de todo)
    def on_duel_msg(self, c, msg_id, data):
        """True = mensaje de un espectador de NPC (ya tratado: run.py no sigue). Del jugador solo apunta y devuelve
        False. Nunca lanza."""
        try:
            d = getattr(c, "npc_watch", None)
            if d is not None:
                self._watch_msg(d, c, msg_id, bytes(data))
                return True
            if msg_id == 0x6700:
                return self._attach(c)
            if not (NPC_WATCH or self._arch_on()) or getattr(c, "pvp", None) is not None \
                    or getattr(c, "sess", None) is None or getattr(c, "ocg", None) is None:
                return False
            self._observe(c, msg_id, bytes(data))
        except Exception as e:                     # noqa: BLE001  (nunca tumba el duelo del jugador)
            self.log(f"[npcmirar] 0x{msg_id:04x}: {e!r} | " + traceback.format_exc().replace(chr(10), " | "))
            return getattr(c, "npc_watch", None) is not None
        return False

    def _observe(self, c, msg_id, data):
        if "send" not in c.__dict__:
            self._install_tap(c)
        if msg_id == 0x6101 and data:
            c.npc_hand0 = data[0] % 3
        elif msg_id == 0x6350:
            d = getattr(c, "npc_duel", None)
            DFM = self.env.get("DFM")
            if d is not None and DFM is not None and DFM.is_surrender(data):
                d.ask = True
        elif msg_id == 0x6423:
            d = getattr(c, "npc_duel", None)
            if d is not None:
                DFM = self.env.get("DFM")
                if d.ask and len(data) >= 4 and struct.unpack(">I", data[:4])[0] == getattr(DFM, "YES", 1):
                    d.why = "surrender"                # archivo: el duelo acaba por rendición
                d.ask = False
        elif msg_id == 0x61fe and not getattr(c, "duel_started", False) and getattr(c, "npc_duel", None) is None:
            self._arm(c)

    def _install_tap(self, c):
        nw = self

        def send(mid, data=b"", *a, **k):          # el Conn.send de siempre y luego se apunta
            r = type(c).send(c, mid, data, *a, **k)
            try:
                nw._tap(c, mid, data)
            except Exception as e:                 # noqa: BLE001
                nw.log(f"[npcmirar] grabación 0x{mid:04x}: {e!r}")
            return r
        c.send = send

    def _tap(self, c, mid, data):
        if mid == 0x6101 and len(data) >= 2:       # JYANKEN_RESULT: u8, u8 mano de la NPC
            c.npc_hand1 = data[1] % 3
            return
        if mid != 0x6202:
            return
        d = getattr(c, "npc_duel", None)
        if d is None or d.c is not c or d.end_t is not None:
            return
        data = bytes(data)
        with d.lock:
            if d.ask and data in self._confirm():
                return                             # «¿Te rindes?» solo para el jugador
            d.entries.append(data)
            end = _is_finish(data)
            if end:
                d.end_t, d.done = time.time(), True
        self._pump(d)
        if end:
            self.log(f"[npcmirar] {d!r}: fin ({len(d.entries)} comandos, {len(d.watchers)} espectadores)")
            self._unpose(d)
            self._archive(d)

    # ------------------------------------------------------------------ arranque del duelo (0x61fe)
    def _arm(self, c):
        s = c.sess
        if getattr(s, "pid", None) is None:
            return
        env = self.env
        rival = env["duel_rival"](s) if callable(env.get("duel_rival")) else 0
        name = next((n[1] for n in (env.get("NPCS") or []) if n[0] == rival), f"Rival {rival}")
        info = self._watch_info(c, s, rival, name)
        prep = None
        if self._pm("WATCH_SEND_PREP", False) and callable(env.get("preparation_result")):
            prep = env["preparation_result"]()     # hilo del jugador (CTX.sess = s): su vista
        h0, h1 = getattr(c, "npc_hand0", None), getattr(c, "npc_hand1", None)
        hands = (h0, h1) if h0 is not None and h1 is not None else (0, 1)
        first = int((getattr(c, "duel_st", None) or {}).get("first", 0) or 0) & 1
        d = _Duel(c, s, rival, name, info, prep, hands, first)
        with self.lock:
            old = self.duels.get(s.pid)
            self.duels[s.pid] = d
            self.stats["duelos"] += 1
        if old is not None and not old.done:
            self._cut(old, "el mismo jugador empieza otro duelo")
        c.npc_duel = d
        try:                                       # archivo de repeticiones: datos del hilo del jugador y fin del motor
            d.arch = self._arch_info(c, s, rival, name)
            o = getattr(c, "ocg", None)
            if o is not None and callable(getattr(o, "finish", None)) and not getattr(o.finish, "_npc_arch", False):
                def fin(c_, winner, reason, turns=0, _o=o.finish, _d=d):
                    _d.fin = (winner, reason, turns)
                    return _o(c_, winner, reason, turns=turns)
                fin._npc_arch = True
                o.finish = fin
        except Exception as e:                     # noqa: BLE001
            self.log(f"[npcmirar] archivo: datos de {d!r}: {e!r}")
        if NPC_WATCH:                              # la pose va con el «Mirar» (como antes de REPLAY_ARCHIVE)
            self._pose(d)
        self._ticker()
        self.log(f"[npcmirar] {s!r} empieza contra {name} ({rival}): manos {hands}, empieza {first}")

    def _watch_info(self, c, s, rival, name):
        """0x6006 (147 B, formato de pvp_match.watch_info): jugador 0 = el humano (su mazo, nombre y piezas), 1 = la
        NPC (su mazo oficial, nombre y piezas como en 0x600c); modo 3 (duelo suelto)."""
        o = getattr(c, "ocg", None)
        pl = getattr(o, "player", None) or {}
        npc = getattr(o, "npc", None) or {}
        dp = self.env.get("duel_parts")
        country = self._pm("COUNTRY", b"ESP\0")
        out = b""
        for who, deck, nm in ((None, pl, s.name or "Duelista"), (rival, npc, name)):
            main, extra = len(deck.get("main") or ()) or (40 if who is not None else 0), len(deck.get("extra") or ())
            parts = (dp(who, 0) + dp(who, 1)) if dp is not None else bytes(32)
            out += struct.pack(">HHB", main, extra, 0) + str(nm).encode("latin1", "replace")[:15].ljust(16, b"\0")
            out += parts[:32].ljust(32, b"\0") + bytes(16) + country
        return out + bytes([3])

    # ------------------------------------------------------------------ pose «en duelo»
    def _pose(self, d):
        hm, hub, s = self.env.get("pvp_hub"), self.env.get("HUB"), d.sess
        if hm is None or hub is None or not getattr(hm, "NPC_DUEL_STATE", False):
            return
        if getattr(s, "block", None) is None or 0x67 <= (getattr(s, "state", 0) or 0) <= 0x6a:
            return
        try:
            hub.set_state(s, getattr(hm, "DUEL_STATE", 0x67), d.name)
            s.npc_pose = True
        except Exception as e:                     # noqa: BLE001
            self.log(f"[npcmirar] pose de {s!r}: {e!r}")

    def _unpose(self, d):
        hub, s = self.env.get("HUB"), d.sess
        if not getattr(s, "npc_pose", False):
            return
        s.npc_pose = False
        if hub is None or not 0x67 <= (getattr(s, "state", 0) or 0) <= 0x6a:
            return
        try:
            hub.set_state(s, 0)
        except Exception as e:                     # noqa: BLE001
            self.log(f"[npcmirar] quitar la pose de {s!r}: {e!r}")

    # ------------------------------------------------------------------ 0x4320 y 0x6700
    def watch_request(self, peer, pid):
        """research/lobby h_watch: el próximo 0x6700 desde peer mira el duelo de NPC de pid. None si no hay."""
        if not NPC_WATCH:
            return None
        with self.lock:
            d = self.duels.get(pid)
            if d is None or d.done or getattr(d.c, "closed", False):
                return None
            self.reqs[peer] = (d, time.time())
        self._ticker()
        return d

    def forget(self, peer):
        """h_watch dio paso a un PvP desde esa IP: su petición de NPC (si había) ya no vale."""
        with self.lock:
            self.reqs.pop(peer, None)

    def _attach(self, c):
        peer = getattr(c, "peer", None)
        with self.lock:
            req = self.reqs.pop(peer, None)
        if req is None:
            return False                           # no es nuestro: sigue pvp_match (espectador de PvP)
        d, t = req
        ttl = self._pm("WATCH_REQ_TTL", 60.0)
        if time.time() - t > ttl or len(d.watchers) >= MAX_WATCHERS or \
                (d.done and time.time() - (d.end_t or 0) > KEEP_DONE_IDLE):
            self.log(f"[npcmirar] 0x6700 de {peer}: {d!r} ya no se puede mirar")
            return False
        w = {"c": c, "i": 0, "want": False, "jseen": False, "gone": False}
        c.npc_watch, c.pvp, c.pvp_i, c.pvp_w = d, _Proxy(self, d), None, True
        with d.lock:
            d.watchers.append(w)
        self.stats["espectadores"] += 1
        self.log(f"[npcmirar] espectador {peer} mira {d!r} ({len(d.watchers)}; {len(d.entries)} comandos ya pasados)")
        if d.prep:
            self._post(c, 0x600c, d.prep)
        self._post(c, 0x6006, d.info)
        self._pump(d)
        return True

    def _watch_msg(self, d, c, mid, data):
        w = next((x for x in d.watchers if x["c"] is c), None)
        if mid == 0x0005:
            return self._post(c, 0x0004, struct.pack(">I", 0))
        if w is None:
            return
        if mid == 0x6702:                          # «se me ha vaciado la cola»: la siguiente tanda
            w["want"] = True
            return self._pump(d)
        if mid == 0x0003:                          # se despide tras los resultados
            w["gone"] = True
            return
        if mid in (0x6700, 0x6703):                # 0x6703: sale del piedra-papel-tijera (nadie lo contesta)
            return
        self.log(f"[npcmirar] espectador de {d!r}: 0x{mid:04x} ignorado (no puede actuar)")

    def _watch_close(self, d, c):
        with d.lock:
            d.watchers = [w for w in d.watchers if w["c"] is not c]
        self.log(f"[npcmirar] espectador {getattr(c, 'peer', '?')} sale de {d!r} ({len(d.watchers)} quedan)")

    def _pump(self, d):
        """0x6701 en cuanto está enganchado (las manos ya se saben: el duelo ha empezado) y, a cada 0x6702, hasta
        WATCH_BATCH 0x6202 más (como pvp_match._watch_pump: ningún 0x6202 antes de su primer 0x6702), SIN las
        peticiones de jugada ni las ventanas de decisión del jugador (pvp_match.watch_oculta, 25-10)."""
        if not d.watchers:
            return
        batch_n = self._pm("WATCH_BATCH", 64)
        sin, oculta = self._pm("WATCH_SIN_DECISIONES", False), self._pm("watch_oculta", None)   # 25-10 (rx/ESP)
        with d.lock:
            for w in list(d.watchers):
                c = w["c"]
                if w["gone"] or getattr(c, "closed", False):
                    d.watchers.remove(w)
                    continue
                if not w["jseen"]:
                    w["jseen"] = True
                    self._post(c, 0x6701, bytes([d.hands[0] & 0xff, d.hands[1] & 0xff, d.first & 0xff]))
                    continue
                if not w["want"]:
                    continue
                batch, i = [], w["i"]
                while i < len(d.entries) and len(batch) < batch_n:
                    x = d.entries[i]
                    i += 1
                    if not (sin and oculta is not None and oculta(x)):   # sin peticiones ni ventanas de decisión
                        batch.append(x)
                w["i"] = i
                if batch:
                    w["want"] = False
                    for x in batch:
                        self._post(c, 0x6202, x)

    # ------------------------------------------------------------------ cortes y limpieza
    def _cut(self, d, why):
        """El duelo se acaba sin 0x4d al jugador (conexión caída, sustituida, sesión ocg acabada sin fin): los
        espectadores reciben el fin de una derrota por desconexión vista desde el jugador (como el que recibiría él)
        y se quita la pose."""
        DFM = self.env.get("DFM")
        msgs = []
        if DFM is not None:
            try:
                msgs = [bytes(x) for m, x in DFM.end_duel_msgs(1, DFM.WHY_DISCONNECT) if m == 0x6202]
            except Exception as e:                 # noqa: BLE001
                self.log(f"[npcmirar] fin para los espectadores: {e!r}")
        if not msgs:
            msgs = [struct.pack(">IIII", T_FINISH, 0, 2, 0)]
        with d.lock:
            if d.done:
                return
            d.entries += msgs
            d.done, d.end_t = True, time.time()
        self.stats["cortados"] += 1
        self.log(f"[npcmirar] {d!r} cortado: {why} ({len(d.watchers)} espectadores reciben el fin)")
        self._pump(d)
        self._unpose(d)
        self._archive(d, "disconnect")

    # ------------------------------------------------------------------ archivo de repeticiones (REPLAY_ARCHIVE)
    def _arch_on(self):
        return bool(self._pm("REPLAY_ARCHIVE", False))

    def _arch_info(self, c, s, rival, name):
        """En _arm (hilo del jugador: duel_parts(None, …) lee SU perfil): bloques de jugador del registro (humano = 0,
        NPC = 1, como 0x600c), tipo de sala, jugadores y npc para el índice."""
        env = self.env
        o = getattr(c, "ocg", None)
        pl = getattr(o, "player", None) or {}
        npc = getattr(o, "npc", None) or {}
        dp = env.get("duel_parts")
        country = self._pm("COUNTRY", b"ESP\0")

        def parts(who):
            return [dp(who, 0), dp(who, 1), bytes(16)] if dp is not None else []
        human = {"pid": s.pid, "listval": s.pid, "name": s.name or "Duelista", "parts": parts(None),
                 "main": len(pl.get("main") or ()), "extra": len(pl.get("extra") or ()), "country": country}
        npcb = {"pid": int(rival or 0), "listval": int(rival or 0), "name": str(name), "parts": parts(rival),
                "main": len(npc.get("main") or ()) or 40, "extra": len(npc.get("extra") or ()), "country": country}
        try:
            kind = env["ROOM_KINDS"][s.block[0] - env["ROOM_PORT_BASE"]]
        except Exception:                          # noqa: BLE001
            kind = 12
        return {"human": human, "npc": npcb, "room_kind": kind,
                "jugadores": [{"nombre": s.name or "Duelista", "pid": s.pid, "acct": getattr(s, "acct", None)},
                              {"nombre": str(name), "pid": None, "acct": None}],
                "npc_idx": {"id": int(rival or 0), "nombre": str(name)}}

    def _archive(self, d, cut=None):
        """REPLAY_ARCHIVE: copia del servidor de este duelo contra NPC (una vez por duelo; nada si no hay 0x6202). El
        .log y el índice, en un hilo aparte (rep_archivo.archivar); un fallo solo va al log."""
        try:
            if getattr(d, "archived", False) or not self._arch_on():
                return
            d.archived = True
            pm = self.env.get("pvp_match")
            RA = pm._RA() if pm is not None and hasattr(pm, "_RA") else None
            root = RA.raiz(self.env.get("STATE_PATH")) if RA is not None else None
            if root is None:
                return
            with d.lock:
                entries = list(d.entries)
            c = d.c
            o = getattr(c, "ocg", None)
            fin = getattr(d, "fin", None)
            DFM = self.env.get("DFM")
            if getattr(d, "why", None) == "surrender":
                winner, why = 1, "surrender"
            elif fin is not None:
                winner = fin[0] if fin[0] in (0, 1) else None
                why = DFM.why_from_ocg(fin[1]) if DFM is not None and hasattr(DFM, "why_from_ocg") else str(fin[1])
            elif cut:
                winner, why = 1, cut
            else:
                winner, why = _end_from_entries(entries)
            turns = (fin[2] if fin is not None else 0) or getattr(o, "turn", 0) or 0
            first = getattr(getattr(o, "d", None), "first", None)
            first = first if first in (0, 1) else d.first
            h0, h1 = getattr(c, "npc_hand0", None), getattr(c, "npc_hand1", None)
            hands = (h0, h1) if h0 is not None and h1 is not None else None
            info = getattr(d, "arch", None) or {
                "human": {"pid": d.pid, "listval": d.pid, "name": getattr(d.sess, "name", "") or "Duelista"},
                "npc": {"pid": int(d.rival or 0), "listval": int(d.rival or 0), "name": d.name, "main": 40},
                "room_kind": 12,
                "jugadores": [{"nombre": getattr(d.sess, "name", "") or "Duelista", "pid": d.pid,
                               "acct": getattr(d.sess, "acct", None)}, {"nombre": d.name, "pid": None, "acct": None}],
                "npc_idx": {"id": int(d.rival or 0), "nombre": d.name}}
            when = int(time.time())

            def build():
                msgs, h, f, avisos = RA.mensajes_npc(info["human"], info["npc"], entries, when, first, hands,
                                                     info["room_kind"])
                return msgs, len(entries), (f"piedra-papel-tijera {list(h)}, empieza {f}"
                                            + (f" ({'; '.join(avisos)})" if avisos else ""))
            duelo = f"npc-ocg-{getattr(o, 'id', '?')}" if o is not None else f"npc-{d.pid}"
            RA.archivar(root, "npc", when, build, info["jugadores"], info["npc_idx"], winner, why, turns, self.log,
                        duelo=duelo)
        except Exception as e:                     # noqa: BLE001  (nunca tumba el fin del duelo)
            self.log(f"[npcmirar] archivo de repeticiones de {d!r}: {e!r} | "
                     + traceback.format_exc().replace(chr(10), " | "))

    def _ticker(self):
        if not getattr(self, "autotick", True):
            return
        with self.lock:
            if self.ticker is not None and self.ticker.is_alive():
                return
            self.ticker = threading.Thread(target=self._loop, daemon=True, name="npc-mirar")
            self.ticker.start()

    def _loop(self):
        while True:
            time.sleep(TICK)
            try:
                if not self.tick():
                    with self.lock:
                        if not self.duels and not self.reqs:
                            self.ticker = None
                            return
            except Exception as e:                 # noqa: BLE001
                self.log(f"[npcmirar] vigilante: {e!r} | " + traceback.format_exc().replace(chr(10), " | "))

    def tick(self, now=None):
        """Una vuelta del vigilante. Devuelve True si queda algo que vigilar."""
        now = time.time() if now is None else now
        ttl = self._pm("WATCH_REQ_TTL", 60.0)
        with self.lock:
            self.reqs = {k: v for k, v in self.reqs.items() if now - v[1] <= ttl}
            duels = list(self.duels.values())
        for d in duels:
            if not d.done:
                c, why = d.c, None
                stale = getattr(c, "stale", None)
                o = getattr(c, "ocg", None)
                if getattr(c, "closed", False):
                    why = "se ha cerrado la conexión de duelo del jugador"
                elif callable(stale) and stale():
                    why = "el jugador ya usa otra conexión de duelo"
                elif o is not None and getattr(o, "over", False):
                    d.over_t = d.over_t or now
                    if now - d.over_t > END_GRACE:
                        why = f"el duelo acabó sin 0x4d en {END_GRACE:.0f} s"
                if why:
                    self._cut(d, why)
                continue
            with d.lock:
                d.watchers = [w for w in d.watchers if not w["gone"] and not getattr(w["c"], "closed", False)]
                idle = not d.watchers
            if now - (d.end_t or now) > (KEEP_DONE_IDLE if idle else KEEP_DONE):
                with self.lock:
                    if self.duels.get(d.pid) is d:
                        self.duels.pop(d.pid, None)
        with self.lock:
            return bool(self.duels or self.reqs)

    def snapshot(self):
        with self.lock:
            return {"duelos": {p: {"npc": d.name, "comandos": len(d.entries), "espectadores": len(d.watchers),
                                   "acabado": d.done} for p, d in self.duels.items()},
                    "peticiones": len(self.reqs), **self.stats}


# ================================================================================ autoprueba (sin red)
def selftest():
    errors = []

    def check(name, cond):
        if not cond:
            errors.append(name)

    class FakeConn:
        def __init__(self, peer):
            self.peer, self.out, self.closed, self.sess, self.ocg = peer, [], False, None, None
            self.port = 5760

        def send(self, mid, data=b"", pause=0.25):
            self.out.append((mid, bytes(data)))

        def stale(self):
            return self.closed

    class FakeSess:
        def __init__(self, pid, name):
            self.pid, self.name, self.block, self.state = pid, name, ("b", 5), 0

    class FakeHub:
        def __init__(self):
            self.calls = []

        def set_state(self, s, state, rival_name="", b=0, u=(0, 0, 0)):
            s.state = state
            self.calls.append((s.pid, state, rival_name))

    class FakeHm:
        NPC_DUEL_STATE, DUEL_STATE = True, 0x67

    class FakeDFM:
        WHY_DISCONNECT = "disconnect"

        @staticmethod
        def surrender_confirm_msgs():
            return [(0x6202, b"ASK1____"), (0x6202, b"ASK2____"), (0x6202, b"ASK3____")]

        @staticmethod
        def is_surrender(data):
            return data == b"SURR"

        @staticmethod
        def end_duel_msgs(winner, why):
            return [(0x6202, struct.pack(">II", 0x05, 0)), (0x6613, bytes(36)), (0x6202, struct.pack(">IIII", T_FINISH, 0, 2, 0))]

    class FakeOcg:
        player = {"main": list(range(40)), "extra": [1, 2]}
        npc = {"main": list(range(41)), "extra": []}
        over = False

    hub = FakeHub()
    env = {"HUB": hub, "pvp_hub": FakeHm, "DFM": FakeDFM, "NPCS": [(84, "Aster Phoenix")], "log": lambda *a: None,
           "duel_rival": lambda s: 84, "duel_parts": lambda who, w: bytes(16)}
    nw = NpcWatch(env)
    nw.autotick = False
    A = FakeSess(1004, "Player")
    c = FakeConn("127.0.0.2"); c.sess, c.ocg = A, FakeOcg()
    for mid, data in ((0x6600, b""), (0x6101, b"\0")):
        check("jugador: on_duel_msg devuelve False", nw.on_duel_msg(c, mid, data) is False)
    c.send(0x6101, b"\0\1")                           # respuesta del servidor: mano de la NPC 1
    check("tap: el c.send propio sigue mandando", c.out[-1] == (0x6101, b"\0\1"))
    check("sin arrancar: no se puede mirar", nw.watch_request("127.0.0.3", 1004) is None and not hub.calls)
    c.duel_st = {"first": 0}
    nw.on_duel_msg(c, 0x61fe, b"\1")
    check("0x61fe: pose 0x67 con el nombre de la NPC", hub.calls == [(1004, 0x67, "Aster Phoenix")] and A.npc_pose)
    d = nw.duels.get(1004)
    check("0x61fe: duelo apuntado con manos (0, 1) y empieza 0", d is not None and d.hands == (0, 1) and d.first == 0)
    check("0x6006 de 147 B (40/2 del humano, 41 de la NPC)", d and len(d.info) == 147 and d.info[:4] == b"\0\x28\0\x02"
          and d.info[73:75] == b"\0\x29" and d.info[78:83] == b"Aster")
    for i in range(5):
        c.send(0x6202, struct.pack(">II", 0x10, i))
    nw.on_duel_msg(c, 0x6350, b"SURR")
    for _, x in FakeDFM.surrender_confirm_msgs():
        c.send(0x6202, x)
    nw.on_duel_msg(c, 0x6423, b"\0\0\0\0")
    check("rendición: el diálogo no se graba", len(d.entries) == 5)
    # espectador
    check("0x4320: hay duelo que mirar", nw.watch_request("127.0.0.3", 1004) is d)
    w = FakeConn("127.0.0.3")
    check("0x6700 de otra IP: no es nuestro", nw.on_duel_msg(FakeConn("127.0.0.9"), 0x6700, b"") is False)
    check("0x6700: enganchado", nw.on_duel_msg(w, 0x6700, b"") is True and w.pvp is not None and w.npc_watch is d)
    check("espectador: 0x6006 y 0x6701 (0, 1, 0)", [m for m, _ in w.out] == [0x6006, 0x6701] and w.out[1][1] == b"\0\1\0")
    nw.on_duel_msg(w, 0x6702, b"")
    check("0x6702: lo ya pasado (5)", [x for m, x in w.out if m == 0x6202] == d.entries[:5])
    n = len(w.out)
    c.send(0x6202, struct.pack(">II", 0x10, 99))
    check("sin 0x6702 no se manda más", len(w.out) == n)
    nw.on_duel_msg(w, 0x6702, b"")
    check("0x6702: lo nuevo", w.out[-1] == (0x6202, struct.pack(">II", 0x10, 99)))
    check("espectador: 0x6001 / jugadas no llegan al duelo", nw.on_duel_msg(w, 0x6350, b"SURR") is True and not d.ask)
    nw.on_duel_msg(w, 0x0005, b"")
    check("espectador: 0x0005 -> 0x0004", w.out[-1][0] == 0x0004)
    # fin normal
    nw.on_duel_msg(w, 0x6702, b"")
    c.send(0x6202, struct.pack(">IIII", T_FINISH, 0, 1, 0))
    check("0x4d: acabado, pose quitada (op 1) y el espectador lo recibe", d.done and hub.calls[-1] == (1004, 0, "")
          and not A.npc_pose and _is_finish(w.out[-1][1]))
    check("acabado: ya no se puede mirar", nw.watch_request("127.0.0.3", 1004) is None)
    w.pvp.on_close(w)
    check("el espectador sale", not d.watchers)
    # corte: el jugador se cae
    B = FakeSess(1008, "Otro")
    c2 = FakeConn("127.0.0.4"); c2.sess, c2.ocg = B, FakeOcg()
    nw.on_duel_msg(c2, 0x6600, b"")
    nw.on_duel_msg(c2, 0x61fe, b"\1")
    d2 = nw.duels[1008]
    nw.watch_request("127.0.0.5", 1008)
    w2 = FakeConn("127.0.0.5")
    nw.on_duel_msg(w2, 0x6700, b"")
    nw.on_duel_msg(w2, 0x6702, b"")
    c2.closed = True
    nw.tick()
    check("corte: el espectador recibe el fin (0x4d) y se quita la pose", d2.done and _is_finish(w2.out[-1][1])
          and hub.calls[-1] == (1008, 0, ""))
    nw.tick(now=time.time() + KEEP_DONE + 1)
    check("limpieza: los duelos acabados se olvidan", not nw.duels)
    # sin pose (NPC_DUEL_STATE apagado) y sin NPC_WATCH
    FakeHm.NPC_DUEL_STATE = False
    C = FakeSess(1012, "Tercero")
    c3 = FakeConn("127.0.0.6"); c3.sess, c3.ocg = C, FakeOcg()
    nw.on_duel_msg(c3, 0x6600, b""); nw.on_duel_msg(c3, 0x61fe, b"\1")
    check("NPC_DUEL_STATE apagado: sin pose", hub.calls[-1][0] != 1012 and not getattr(C, "npc_pose", False))
    global NPC_WATCH
    old, NPC_WATCH = NPC_WATCH, False
    D = FakeSess(1016, "Cuarto")
    c4 = FakeConn("127.0.0.7"); c4.sess, c4.ocg = D, FakeOcg()
    nw.on_duel_msg(c4, 0x6600, b""); nw.on_duel_msg(c4, 0x61fe, b"\1")
    check("NPC_WATCH apagado: ni grabación ni Mirar", 1016 not in nw.duels and "send" not in c4.__dict__
          and nw.watch_request("x", 1012) is None)
    NPC_WATCH = old
    for e in errors:
        print("MAL", e)
    print(f"prueba npc_watch (pose y espectador de NPC, sin red): {len(errors)} fallos")
    return not errors


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        sys.exit(0 if selftest() else 1)
    print(__doc__)
