"""Duelo con reglas reales para run.py: duelcore (ocgcore en Node) + traductor (research/traductor) + mazos de NPC.

Flujo (puerto 5760, ver research/lockstep/NOTES.md y research/traductor/NOTES.md §11):
  0x6601/0x6603  mazo del jugador (ids YO2)            -> se guarda en la sesión
  0x600b         PREPARATION                           -> 0x600c (lo manda run.py) + 0x6005 con el tamaño del mazo NPC
  0x61fe         tablero montado                       -> YO2Duel.start() + duelcore.new(); bombeo de eventos
  0x6350/0x6351/0x002e/0x6423 jugadas del cliente      -> YO2Duel.on_client() -> respuesta a duelcore; bombeo
  fin («end» de duelcore)                              -> finish() de research/duelfin (0x4140 + cola de fin + 0x4800)
La NPC (jugador 1) la juega bot.ts dentro de duelcore (o el cerebro de research/ia cuando esté).
"""
import json
import os
import queue
import random
import struct
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.join(HERE, "duelcore"), os.path.join(HERE, "research", "traductor"), os.path.join(HERE, "research", "ia")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from duelcore import DuelCore, DuelCoreError   # noqa: E402
import translator as TR                # noqa: E402
from cardmap import CardMap            # noqa: E402
try:                                   # cerebro de NPC (research/ia/BRAIN.md); sin él juega bot.ts
    from brain import NpcBrain, load_npc_profile, CardDB   # noqa: E402
except Exception:                      # noqa: BLE001
    NpcBrain = None

SUBSTITUTE = 15025844                  # Mystical Elf: existe en YO2 y en cards.cdb (cartas sin pareja)
NPC_DECKS_PATH = os.path.join(HERE, "research", "ia", "npc_decks.json")
NPC_DECKS_EXTRA_PATH = os.path.join(HERE, "research", "ia", "npc_decks_extra.json")
# 13-10: mazos PROPIOS (deducidos, NO oficiales) para las 26 NPC sin receta en el cliente (research/ia/npc_mazos_extra.py).
# APAGADO por defecto = como el original (esas NPC juegan con una copia del mazo del jugador). YO2_NPC_DECKS_EXTRA=1 lo
# enciende; solo se usa para una NPC sin mazo oficial en npc_decks.json.
NPC_DECKS_EXTRA = (os.environ.get("YO2_NPC_DECKS_EXTRA") or "0") not in ("", "0")
SEND_PAUSE = float(os.environ.get("YO2_DUEL_PAUSE") or 0.05)   # entre mensajes seguidos (el cliente encola 0x6202;
                                       # 512 huecos). YO2_DUEL_PAUSE solo para la instancia de prueba (fake_client pvp)
NPC_THINK = 0.8                        # s antes de cada jugada visible de la NPC (que se vean animaciones y bocadillos)
MAX_NPC_DECISIONS = 3000               # tope anti-bucle por duelo
USE_BRAIN = True
# B03: «error» de duelcore con el duelo aún vivo (p. ej. una respuesta que la librería no sabe codificar): se deja
# decidir a bot.ts UNA vez; si el siguiente paso vuelve a dar error, se cierra el duelo (sin bucles de reintento)
CORE_ERROR_AUTO = 1
END_DRAIN = 20.0                       # B29: s como mucho esperando a que el Sender vacíe la cola antes del fin
# Regla de ocgcore de los duelos NUEVOS (NPC y PvP), research/traductor/NOTES.md §27. "MR5" = la de siempre aquí
# (2020); "MR1" = la de la época de YO2 (2008: el primero roba en el turno 1, una sola magia de campo boca arriba,
# prioridad de ignición, sin zonas extra/péndulo). YO2_DUEL_RULE=MR1 la cambia al arrancar; en caliente (tras
# hot_regla_duelo.py): python admin.py "OCG.DUEL_RULE = 'MR1'". Un duelo ya empezado sigue con la suya.
DUEL_RULES = ("MR1", "MR2", "MR3", "MR4", "MR5")   # GOAT/SPEED/RUSH no: el cliente de 2008 no los sabe pintar
DUEL_RULE = os.environ.get("YO2_DUEL_RULE") or "MR1"   # 08-10: MR1 (reglas de 2008) por OK del owner


def duel_rule():
    """Regla para un duelo nuevo: DUEL_RULE si es una de DUEL_RULES; si no (un valor mal puesto a mano), "MR5"."""
    r = str(DUEL_RULE or "").strip().upper()
    return r if r in DUEL_RULES else "MR5"

_CORE = None
_CORE_LOCK = threading.Lock()
_CM = None


def cardmap():
    global _CM
    if _CM is None:
        _CM = CardMap()
    return _CM


if HERE not in sys.path:                            # yo2srv (rutas y log) vive junto a este fichero
    sys.path.append(HERE)
from yo2srv import rutas as _rutas                  # noqa: E402
from yo2srv import log as _ylog                     # noqa: E402
# <YO2_LOGDIR>/ocg o logs/ocg (modo paquete: <logdir>/ocg). P1: con tope (yo2srv/log.py `limites`): una traza que pasa de
# YO2_LOG_MAX_MB deja de crecer y solo se guardan las YO2_LOG_KEEP_OCG más recientes. En desarrollo, sin tope (como antes).
TRACE_DIR = _rutas.trazas_ocg(HERE)
TRACE_START = time.strftime("%Y%m%d-%H%M%S")      # los id de duelo vuelven a 1 en cada arranque
TRACE_MAX, _, TRACE_KEEP = _ylog.limites()


class TracedCore(DuelCore):
    """2026-10-06: traza de lo que hace el MOTOR (eventos de ocgcore + respuestas), un .jsonl por duelo en logs/ocg/.
    El log de run.py solo tiene lo que pinta el cliente: sin esto no se puede saber qué carta hay de verdad en cada
    sitio cuando cliente y motor no cuadran. Un fallo al escribir nunca rompe el duelo."""

    def _trace(self, did, kind, data):
        try:
            os.makedirs(TRACE_DIR, exist_ok=True)
            path = os.path.join(TRACE_DIR, f"{TRACE_START}-duelo{did}.jsonl")
            if TRACE_MAX and os.path.exists(path) and os.path.getsize(path) >= TRACE_MAX:
                return                                     # P1: traza llena (YO2_LOG_MAX_MB); el duelo sigue igual
            nueva = TRACE_KEEP and kind == "new" and not os.path.exists(path)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": time.strftime("%H:%M:%S"), kind: data}, ensure_ascii=False, default=str) + "\n")
            if nueva:                                      # P1: una traza nueva -> se quitan las más viejas
                _ylog.podar(TRACE_DIR, _ylog.TRAZA_RE, TRACE_KEEP, por_fecha=True)
        except Exception:                                  # noqa: BLE001
            pass

    def new(self, did, *a, **k):
        self._trace(did, "new", {"args": a, **k})
        return super().new(did, *a, **k)

    def wait(self, did, *a, **k):
        evs = super().wait(did, *a, **k)
        for e in evs:
            self._trace(did, "ev", e)
        return evs

    def respond(self, did, resp, *a, **k):
        self._trace(did, "respond", {"resp": resp, **k})
        return super().respond(did, resp, *a, **k)

    def auto(self, did, *a, **k):
        self._trace(did, "auto", {})
        return super().auto(did, *a, **k)

    def alive(self):
        return self.proc.poll() is None

    def next_event(self, duel_id, timeout=30.0):
        """B25: como DuelCore.next_event, pero si node muere no se queda los 30 s esperando: mira proc.poll() cada
        medio segundo y lanza DuelCoreError en cuanto el proceso ha terminado y la cola del duelo está vacía.
        (Solo vale para los TracedCore creados con este fichero: el node vivo de antes de una recarga sigue con la
        espera de 30 s de siempre, que también acaba en DuelCoreError.)"""
        q = self._queues[self._key(duel_id)]
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            left = 0.5 if deadline is None else min(0.5, deadline - time.monotonic())
            try:
                return q.get(timeout=max(0.01, left))
            except queue.Empty:
                pass
            if not self.alive() and q.empty():
                raise DuelCoreError(f"duelcore terminó (código {self.proc.poll()}): "
                                    + " | ".join(list(self.stderr_tail)[-3:]))
            if deadline is not None and time.monotonic() >= deadline:
                raise DuelCoreError(f"sin eventos del duelo {duel_id} en {timeout}s")


def core_alive(dc=None):
    """¿Sigue vivo el proceso node de dc (por defecto, el compartido)? Sin arrancar nada."""
    dc = _CORE if dc is None else dc
    try:
        return dc is not None and dc.proc.poll() is None
    except Exception:                               # noqa: BLE001
        return False


def core(log=None):
    """Un único proceso duelcore para todos los duelos (arranca en ~3 s). B25: si node ha muerto (proc.poll()), el
    siguiente que lo pida arranca uno nuevo; los duelos que iban en el muerto se cierran solos (OcgSession._core)."""
    global _CORE
    with _CORE_LOCK:
        if _CORE is not None and not core_alive(_CORE):
            old, _CORE = _CORE, None
            try:
                rc = old.proc.poll()
                tail = " | ".join(list(old.stderr_tail)[-3:])
            except Exception:                       # noqa: BLE001
                rc, tail = "?", ""
            (log or print)(f"[ERROR] [ocg] duelcore había muerto (código {rc}; {tail}): arranco uno nuevo")
            try:
                old.close(timeout=1)
            except Exception:                       # noqa: BLE001
                pass
        if _CORE is None:
            _CORE = TracedCore()
        return _CORE


def warm_up(log=print):
    """Arranca duelcore y el mapa de cartas en segundo plano para que el primer duelo no espere."""
    def run():
        try:
            core(log); cardmap()
            if NpcBrain is not None:
                CardDB.shared()
            log("[ocg] duelcore y CardMap listos")
        except Exception as e:                      # noqa: BLE001
            log(f"[ocg] no arranca duelcore: {e!r}")
    threading.Thread(target=run, daemon=True).start()


def npc_deck(npc_id):
    """Mazo oficial de la NPC (ids YO2) o None."""
    try:
        d = json.load(open(NPC_DECKS_PATH, encoding="utf-8")).get(str(npc_id))
    except OSError:
        return None
    if not d or not d.get("main"):
        return npc_deck_extra(npc_id) if NPC_DECKS_EXTRA else None
    return {"main": list(d["main"])[:80], "extra": list(d.get("extra") or [])[:18]}


def npc_deck_extra(npc_id):
    """Mazo propio (npc_decks_extra.json: passcodes -> ids YO2) de una NPC que no tiene receta en el cliente, o None."""
    try:
        e = json.load(open(NPC_DECKS_EXTRA_PATH, encoding="utf-8")).get(str(npc_id))
    except (OSError, ValueError):
        return None
    if not e or not e.get("main"):
        return None
    cm = cardmap()
    out = {"main": [], "extra": []}
    for k, tope in (("main", 80), ("extra", 18)):
        for code in list(e.get(k) or [])[:tope]:
            y = cm.code_to_yo2(code)
            if y:
                out[k].append(y)
    return out if out["main"] else None


def to_codes(yo2_ids):
    cm = cardmap()
    return [cm.yo2_to_code(y) or SUBSTITUTE for y in yo2_ids]


def _is_end(m):
    """¿Es un 0x6202 de fin de duelo (tipo 0x4d, o comando del motor 0x05 DUEL_END)?"""
    mid, data = m if isinstance(m, tuple) else (0x6202, m)
    if mid != 0x6202 or len(data) < 8:
        return False
    t, b = struct.unpack(">II", data[:8])
    return (t & 0xff) == 0x4d or ((t & 0xff) == 0 and (b & 0xfff) == 0x05)


class Sender:
    """Cola de envío con pausa corta, en su propio hilo (no bloquea la lectura del socket)."""

    def __init__(self, c, log=None):
        self.c, self.q, self.log = c, queue.Queue(), log
        self.dead = False                             # el hilo salió (conexión cerrada): nada más se va a mandar
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            mid, data = self.q.get()
            if mid is None:                           # PvP (fase 2): (None, función) = ejecutarla EN ORDEN con la cola
                try:                                  # (p. ej. el 0x4800 del lobby después del 0x4d del duelo)
                    data()
                except Exception as e:                # noqa: BLE001
                    if self.log is not None:
                        self.log(f"[ocg] Sender: función en cola: {e!r}")
                continue
            try:
                self.c.send(mid, data, pause=SEND_PAUSE)
            except Exception:                         # noqa: BLE001  (conexión cerrada)
                self.dead = True
                self._release()
                return

    def _release(self):
        """Hilo muerto: despierta a quien espere en drain() (las funciones de cola que sean «marcas» de drain)."""
        while True:
            try:
                mid, data = self.q.get_nowait()
            except queue.Empty:
                return
            if mid is None and getattr(data, "_drain_mark", False):
                data()

    def put(self, msgs):
        for m in msgs:
            self.q.put(m if isinstance(m, tuple) else (0x6202, m))

    def drain(self, timeout=END_DRAIN):
        """B29: espera a que todo lo encolado HASTA AHORA se haya mandado (marca al final de la cola). True si se
        vació; False si el hilo ya no manda (conexión cerrada) o se acaba el tiempo."""
        if self.dead:
            return False
        ev = threading.Event()
        mark = lambda: ev.set()                       # noqa: E731
        mark._drain_mark = True
        self.q.put((None, mark))
        if self.dead:                                 # murió justo entre la comprobación y el put
            return False
        return ev.wait(timeout) and not self.dead     # muerto = lo de antes de la marca no se mandó entero


class OcgSession:
    _next_id = int(time.time()) % 1000000000   # 07-10: único aunque se recargue el módulo (duelcore guarda los ids viejos)
    ended = False                      # (valores por defecto también para sesiones hechas con __new__ en las pruebas)
    dc = None
    core_errors = 0
    turn = 0

    def __init__(self, c, log, finish, npc_id):
        self.c, self.log, self.finish, self.npc_id = c, log, finish, npc_id
        self.id = OcgSession._next_id; OcgSession._next_id += 1
        self.out = Sender(c, log)
        self.lock = threading.RLock()
        self.player = {"main": [], "extra": []}
        self.npc = None
        self.d = None
        self.over = False
        self.ended = False             # B25: el fin ya se mandó (o lo manda otro: rendición); nunca dos veces
        self.brain = None
        self.npc_decisions = 0
        self.dc = None                 # B25: el proceso duelcore de ESTE duelo (si muere, el duelo se cierra)
        self.core_errors = 0           # B03: «error» de duelcore seguidos
        self.turn = 0

    def _core(self):
        """B25: duelcore de este duelo. Si su node ha muerto, DuelCoreError: no se pasa al node nuevo (allí el duelo
        no existe); el duelo se cierra en _fatal y el SIGUIENTE duelo arranca otro node con core()."""
        if self.dc is None:
            self.dc = core(self.log)
        if not core_alive(self.dc):
            raise DuelCoreError(f"duelcore terminó (código {self.dc.proc.poll()})")
        return self.dc

    # ---- mazos
    def client_deck(self, msg_id, data):
        if len(data) < 4:
            return
        n = struct.unpack(">I", data[:4])[0]
        ids = list(struct.unpack(">%dH" % n, data[4:4 + 2 * n]))
        if msg_id == 0x6601:
            self.player["main"] = ids
        elif msg_id == 0x6603:
            self.player["extra"] = ids

    def npc_sizes(self):
        """(principal, extra) del mazo de la NPC para 0x6005; su mazo oficial o, si no hay, una copia del jugador."""
        self.npc = npc_deck(self.npc_id) or {"main": list(self.player["main"]), "extra": list(self.player["extra"])}
        return len(self.npc["main"]), len(self.npc["extra"])

    # ---- arranque
    def start(self, first):
        with self.lock:
            try:
                self._start(first)
            except Exception as e:                    # noqa: BLE001  B25: node no arranca o muere al empezar
                self._fatal(e)

    def _start(self, first):
        if self.npc is None:
            self.npc_sizes()
        decks = [{"main": to_codes(self.player["main"]), "extra": to_codes(self.player["extra"])},
                 {"main": to_codes(self.npc["main"]), "extra": to_codes(self.npc["extra"])}]
        self.d = TR.YO2Duel(decks, first=first, cardmap=cardmap(), log=self.log)
        # con query, el traductor corrige posición y ATK/DEF (0x36/0x37) ANTES de cada petición de jugada, antes
        # de cada ataque y al cerrar cada cadena; sin ella solo se hacía DESPUÉS del 0x1a (ver sync_stats abajo)
        self.d.query = lambda: self._core().query(self.id)
        self.out.put(self.d.start())
        seed = random.randrange(1 << 30)
        dc = self._core()
        rule = duel_rule()                            # §27: "MR5" salvo YO2_DUEL_RULE / OCG.DUEL_RULE
        # NOTES §30: con chain_none_prompt el traductor necesita las ventanas de cadena VACÍAS (duelcore las pasa solas
        # con auto_chain); las de la NPC se pasan en _pump sin preguntar al cerebro
        auto_chain = not self.d.cfg.get("chain_none_prompt")
        if USE_BRAIN and NpcBrain is not None:
            self.brain = NpcBrain(load_npc_profile(self.npc_id), player=1, seed=seed)
            dc.new(self.id, decks, first=first, seed=seed, shuffle=True, brain="python", rule=rule,
                   auto_chain=auto_chain)
        else:
            dc.new(self.id, decks, first=first, seed=seed, shuffle=True, rule=rule, auto_chain=auto_chain)
        self.log(f"[ocg] duelo {self.id}: NPC {self.npc_id}, empieza {first}, regla {rule}, mazos "
                 f"{len(decks[0]['main'])}+{len(decks[0]['extra'])} / {len(decks[1]['main'])}+{len(decks[1]['extra'])}")
        self.pump()

    # ---- fin
    def _end(self, winner, reason, turns=0):
        """B29: el fin de duelfin (0x05 + 0x6613 + 0x4d, que manda directo por c.send) sale DESPUÉS de todo lo que el
        traductor ya ha encolado en el Sender (la última animación de LP incluida). Antes se intercalaban: el WIN/LOSE
        se veía antes del último cambio de LP."""
        self.ended = True
        self.over = True
        if not self.out.drain():
            self.log(f"[ocg] duelo {self.id}: la cola de envío no se ha vaciado (conexión cerrada o más de "
                     f"{END_DRAIN:.0f} s): el fin sale igual")
        self.finish(self.c, winner, reason, turns=turns)

    def _fatal(self, e):
        """B03/B25/B02: node muerto, duelcore sin respuesta, errores seguidos o excepción en el bombeo. Se registra y el
        duelo se cierra «sin resultado» (empate con motivo «error» -> duelfin WHY_ABORT: 0x05 resultado 3 + 0x6613 +
        0x4d, sin LP a 0), igual que el cierre por error que ya mandaba duelcore (B50). Sin reintentos."""
        tb = traceback.format_exc().replace(chr(10), " | ")
        dead = self.dc is not None and not core_alive(self.dc)
        self.log(f"[ERROR] [ocg] duelo {self.id}: {'duelcore ha muerto' if dead else 'fallo del duelo'} ({e!r}): "
                 f"{'ya estaba acabado' if self.ended or self.over else 'se cierra sin resultado'} | {tb}")
        if self.ended or self.over:      # over sin ended = lo paró run.py (_abandon_duel: conexión vieja): sin fin
            return
        self.over = True
        if self.d is not None:
            self.d.query = None
            self.d.finished = True
        if self.dc is not None and not dead:
            try:
                self.dc.end(self.id)                  # que duelcore suelte el duelo (su «end» ya no lo lee nadie)
            except Exception:                         # noqa: BLE001
                pass
        try:
            self._end(None, "error", self.turn)
        except Exception as e2:                       # noqa: BLE001
            self.log(f"[ERROR] [ocg] duelo {self.id}: el fin por error también falla: {e2!r} | "
                     + traceback.format_exc().replace(chr(10), " | "))

    # ---- bucle de eventos de duelcore hasta que haga falta el jugador
    def pump(self):
        with self.lock:
            try:
                self._pump()
            except Exception as e:                    # noqa: BLE001  B03/B25: antes [EXC] en run.py y duelo parado
                self._fatal(e)

    def _pump(self):
        while not self.over:
            evs = self._core().wait(self.id)
            last = evs[-1]
            if last.get("ev") != "error":
                self.core_errors = 0
            if isinstance(last.get("turn"), int):
                self.turn = last["turn"]
            if self.brain is not None:
                for e in evs:
                    if e.get("ev") == "msg":
                        self.brain.observe(e)
            if (last.get("ev") == "select" and last.get("player") == 1 and last.get("type") == "SELECT_CHAIN"
                    and not last.get("selects") and not last.get("forced") and not last.get("retry")):
                # §30 (auto_chain=False): ventana vacía de la NPC: no encadena, sin cerebro ni MAX_NPC_DECISIONS
                self.out.put(self.d.feed_many(evs[:-1]))
                self._core().respond(self.id, {"type": "SELECT_CHAIN", "index": None}, player=1)
                continue
            if last.get("ev") == "select" and last.get("player") == 1:   # decide la NPC (cerebro)
                self.out.put(self.d.feed_many(evs[:-1]))     # el traductor no ve los select de la NPC
                self.npc_decisions += 1
                if self.npc_decisions > MAX_NPC_DECISIONS:
                    self.log("[ocg] la NPC entra en bucle: se acaba el duelo (gana el jugador)")
                    self._core().end(self.id)
                    self._end(0, "loop", self.turn)
                    return
                if last.get("type") in ("SELECT_IDLECMD", "SELECT_BATTLECMD"):
                    time.sleep(NPC_THINK)                    # hilo propio de la sesión: no frena la red
                if last.get("retry"):
                    self._core().auto(self.id)                # respuesta rechazada: decide bot.ts
                else:
                    r = self.brain.choose(last)
                    if r is None:
                        self._core().auto(self.id)
                    else:
                        self._core().respond(self.id, r, player=1)
                continue
            # §33 (12-10): SELECT_SUM del jugador (ritual) ya no lo decide el bot: duelcore parchea el lector de
            # ocgcore-wasm 0.1.2 (parche_ocgcore.mts) y el traductor lo pregunta con las ventanas del oficial
            if last.get("ev") == "end":
                # el fin (DUEL_END 0x05 + 0x4d) lo manda finish() de research/duelfin con lo del lobby en orden;
                # el traductor también lo emite al ver WIN: se quita para no mandarlo dos veces
                self.d.query = None                   # duelcore ya cerró el duelo: no se puede consultar
                self.out.put([m for m in self.d.feed_many(evs[:-1]) if not _is_end(m)])
                self.d.finished = True
                self.over = True
                winner = last.get("winner")
                self.log(f"[ocg] fin: ganador {winner}, motivo {last.get('reason')}, turnos {last.get('turns')}")
                if last.get("reason") == "error":
                    self.log(f"[ERROR] [ocg] duelo {self.id} cerrado por error de duelcore: {last.get('error')}")
                # B50: empate de ocgcore (winner None) o cierre por error = EMPATE, no victoria del jugador como
                # antes (`1 if winner == 1 else 0`). El motivo va tal cual (número de ocgcore): lo traduce
                # ocg_finish de run.py con DFM.why_from_ocg. B29: _end espera a que se vacíe la cola antes del fin
                self._end(winner if winner in (0, 1) else None, last.get("reason") or 0,
                          turns=last.get("turns") or 0)
                return
            self.out.put(self.d.feed_many(evs))
            if last.get("ev") == "select" and last.get("player", 0) == 0 and hasattr(self.d, "sync_stats"):
                try:                                  # ATK/DEF de ocgcore (equipos, efectos) antes de que decida
                    self.out.put(self.d.sync_stats(self._core().query(self.id)))
                except Exception as e:                # noqa: BLE001
                    self.log(f"[ocg] sync_stats: {e!r}")
            if last.get("ev") == "error":
                # B03: antes log + return y el duelo se quedaba parado. Si duelcore ya cerró el duelo (núcleo roto),
                # detrás del «error» viene su «end» (reason «error») y sale en el wait siguiente; si el duelo sigue
                # esperando (respuesta que la librería no supo codificar), decide bot.ts una vez. Otro error seguido
                # = se cierra el duelo (_fatal), sin más reintentos.
                self.core_errors += 1
                self.log(f"[ocg] error de duelcore (duelo {self.id}, {self.core_errors}.º seguido): {last.get('error')}")
                if self.core_errors > CORE_ERROR_AUTO:
                    raise DuelCoreError(f"{self.core_errors} errores seguidos de duelcore: {last.get('error')}")
                self._core().auto(self.id)
                continue
            if self.d.needs_auto():
                self._cmd(self.d.auto_response())
                continue
            return

    def _cmd(self, cmd):
        if cmd.get("cmd") == "auto":
            self._core().auto(self.id)
        else:
            self._core().respond(self.id, cmd["response"])

    # ---- jugadas del cliente
    def on_client(self, msg_id, data):
        with self.lock:
            if self.d is None or self.over:
                return
            try:
                cmd, back = self.d.on_client(msg_id, data)
            except Exception as e:                    # noqa: BLE001
                # B02: el traductor falla con una jugada; duelcore no ha avanzado: se registra y se vuelve a pedir la
                # jugada pendiente (antes: [EXC] y el cliente esperando para siempre)
                self.log(f"[EXC] [ocg] duelo {self.id}: traductor con 0x{msg_id:04x}: {e!r} | "
                         + traceback.format_exc().replace(chr(10), " | "))
                try:
                    self.out.put(self.d._resend())
                except Exception as e2:               # noqa: BLE001
                    self.log(f"[ocg] duelo {self.id}: no se puede volver a pedir la jugada: {e2!r}")
                return
            self.out.put(back)
            if cmd:
                try:
                    self._cmd(cmd)
                except Exception as e:                # noqa: BLE001  B25: node muerto al responder
                    self._fatal(e)
                    return
                self.pump()

    def surrender(self):
        with self.lock:
            self.over = True
            self.ended = True                         # el fin lo manda run.py (duelfin h_6350/h_6423)
            try:
                if self.dc is not None and core_alive(self.dc):
                    self.dc.end(self.id)
            except Exception:                         # noqa: BLE001
                pass


# ================================================================================ PvP (fase 2: V06, V07) ======
# Dos humanos en el MISMO duelo de duelcore (bot=(False, False), sin cerebro): research/audit/social_pvp.md P0-6/P0-7.
# Protocolo de duelcore: jugador 0 = A (s.pids[0] de PvpMatch), 1 = B, en índices ABSOLUTOS. Cada cliente se cree el
# jugador 0 (DAT_012a9078 = 0, FUN_00794d90; research/lockstep), así que hay DOS traductores:
#   views[0] = YO2Duel([A, B], first)      -> recibe los eventos tal cual;
#   views[1] = YO2Duel([B, A], 1 - first)  -> recibe los eventos ESPEJADOS (mirror(): 0 <-> 1).
# El espejo cambia exactamente lo que duelcore cambia cuando arranca con first=1 (duelcore.mts plain()/messageJson():
# player, controller, triggering_controller a cualquier profundidad, los arrays de 2 «players»/«results» y la máscara
# absoluta de FIELD_DISABLED), más lo que duelcore añade al select/end (turn_player, lp, winner). La máscara de
# SELECT_PLACE/SELECT_DISFIELD es relativa al que elige: no se toca (igual que en duelcore).
# Un select del jugador p solo se traduce en views[p] (el otro solo ve los mensajes de antes: igual que el traductor
# de la NPC, que nunca ve los select de la NPC). La respuesta vuelve a duelcore con player=p y, si p = 1, con el
# «player» de places deshecho (es lo único de una respuesta que lleva jugador: _respond/_free_places).
# translator.py NO cambia: ya tiene npc_phrases (apagado) y hide_npc tapa al jugador 1 de cada vista (= el rival).
PVP_PLAYER_KEYS = ("player", "controller", "triggering_controller", "turn_player", "winner")
PVP_PAIR_KEYS = ("players", "results", "lp")
PVP_SUM_MAX = 40          # intentos seguidos de bot.ts con un SELECT_SUM antes de cortar el duelo (ver pump)


def ritual_sum_ok():
    """§33: ¿el jugador decide sus SELECT_SUM? (parche de duelcore encendido y flag ritual_sum del traductor)."""
    if os.environ.get("YO2_PARCHE_SUM", "1") in ("", "0"):
        return False
    try:
        return bool(TR.DEFAULT_CFG.get("ritual_sum"))
    except Exception:                                 # noqa: BLE001
        return False


def mirror(v, key=""):
    """Evento de duelcore visto desde el otro lado (copia; no toca el original)."""
    if isinstance(v, list):
        arr = [mirror(x) for x in v]
        if len(arr) == 2 and key in PVP_PAIR_KEYS:
            arr.reverse()
        return arr
    if isinstance(v, dict):
        o = {}
        for k, x in v.items():
            if k in PVP_PLAYER_KEYS and not isinstance(x, bool) and x in (0, 1):
                o[k] = 1 - x
            else:
                o[k] = mirror(x, k)
        if o.get("type") == "FIELD_DISABLED" and isinstance(o.get("field_mask"), int):
            m = o["field_mask"]
            o["field_mask"] = (((m & 0xffff) << 16) | (m >> 16)) & 0xffffffff
        return o
    return v


def mirror_state(st):
    """query de duelcore desde el otro lado: duelcore con first=1 solo invierte «players» (duelcore.mts cmdQuery)."""
    if not isinstance(st, dict):
        return st
    o = dict(st)
    if isinstance(o.get("players"), list) and len(o["players"]) == 2:
        o["players"] = [o["players"][1], o["players"][0]]
    return o


def unmirror_response(p, resp):
    """Respuesta del traductor de views[p] -> índices absolutos (solo places[].player lleva jugador)."""
    if p != 1 or not isinstance(resp, dict) or not isinstance(resp.get("places"), list):
        return resp
    r = dict(resp)
    r["places"] = [dict(pl, player=1 - pl["player"]) if pl.get("player") in (0, 1) else pl for pl in resp["places"]]
    return r


class PvpSession:
    """Duelo de duelcore entre dos clientes. NO es thread-safe por sí sola: pvp_match.PvpMatch la usa desde UN hilo
    trabajador por partida (los dos sockets le encolan sus mensajes). conns = [conexión de A, de B];
    decks = [{"main": [ids YO2], "extra": [...]}, {...}] tal como los mandó cada cliente en 0x6601/0x6603.
    finish(ganador absoluto 0/1/None, motivo, turnos) lo llama pump() al acabar (lo implementa PvpMatch)."""
    _next = int(time.time()) % 1000000000      # 07-10: «pvp1» repetido tras una recarga = duelcore cerraba el duelo nuevo («ended»)
    # Límite de tiempo (research/traductor/TIEMPO.md «Implementado»). None = sin límite: así siguen los duelos creados
    # sin reloj (límite apagado, sala con «Límite OFF») y los que ya estaban en marcha antes de una recarga en caliente
    # (hot_tiempo.py cambia estos métodos en la clase viva: con clk None hacen exactamente lo de antes).
    clk = None

    def __init__(self, conns, decks, log, finish, out=None, clock=None):
        self.id = f"pvp{PvpSession._next}"; PvpSession._next += 1
        self.conns, self.decks, self.log, self.finish = list(conns), decks, log, finish
        # clock = {"turn": s de tu turno, "resp": s para contestar en el turno del rival, "margin": s de gracia,
        # "notify": fn(item) que encola item en el hilo de la partida (PvpMatch.q)} o None (sin límite)
        self.clk = _clock_state(clock) if clock else None
        # out = las colas que ya usa PvpMatch para el arranque: UNA por cliente (dos colas al mismo socket desordenan)
        self.out = list(out) if out is not None else [Sender(conns[0]), Sender(conns[1])]
        self.views = [None, None]
        self.decider = None          # jugador (absoluto) con la decisión pendiente, o None
        self.over = False
        self.started = False
        self.ended = False           # B25: finish ya llamado (o lo hace PvpMatch tras end()): nunca dos veces
        self.dc = None               # B25: el duelcore de ESTE duelo
        self.core_errors = 0         # B03
        self.turn = 0

    def _core(self):
        """B25: como OcgSession._core (node muerto -> DuelCoreError; el siguiente duelo arranca otro)."""
        if self.dc is None:
            self.dc = core(self.log)
        if not core_alive(self.dc):
            raise DuelCoreError(f"duelcore terminó (código {self.dc.proc.poll()})")
        return self.dc

    def _fatal(self, e):
        """B03/B25/B02: se registra y el duelo acaba para los dos «sin resultado» (finish(None, "error") -> PvpMatch
        _on_end -> duelfin WHY_ABORT), por las mismas colas que el resto del duelo (el fin sale detrás de todo)."""
        tb = traceback.format_exc().replace(chr(10), " | ")
        dead = self.dc is not None and not core_alive(self.dc)
        self.log(f"[ERROR] [pvp] {self.id}: {'duelcore ha muerto' if dead else 'fallo del duelo'} ({e!r}): "
                 f"{'ya estaba acabado' if self.ended else 'se cierra sin resultado'} | {tb}")
        if self.ended:
            return
        self.ended = True
        self.over = True
        self.decider = None
        for v in self.views:
            if v is not None:
                v.query = None
                v.finished = True
        if self.dc is not None and not dead:
            try:
                self.dc.end(self.id)
            except Exception:                         # noqa: BLE001
                pass
        try:
            self.finish(None, "error", self.turn)
        except Exception as e2:                       # noqa: BLE001
            self.log(f"[ERROR] [pvp] {self.id}: el fin por error también falla: {e2!r} | "
                     + traceback.format_exc().replace(chr(10), " | "))

    def ev_for(self, i, evs):
        return evs if i == 0 else [mirror(e) for e in evs]

    def start(self, first):
        if self.started:
            return
        self.started = True
        try:
            self._start(first)
        except Exception as e:                        # noqa: BLE001  B25
            self._fatal(e)

    def _start(self, first):
        codes = [{"main": to_codes(d.get("main", [])), "extra": to_codes(d.get("extra", []))} for d in self.decks]
        cfg = {"npc_phrases": False}                      # las frases son de la NPC (y necesitan 0x43a0 = 1)
        if self.clk:                                      # límite de tiempo: los 0x6461 los manda la sesión (_clk_show)
            cfg["turn_timer"] = 0
        self.views[0] = TR.YO2Duel([codes[0], codes[1]], first=first, cardmap=cardmap(), cfg=cfg, log=self.log)
        self.views[1] = TR.YO2Duel([codes[1], codes[0]], first=1 - first, cardmap=cardmap(), cfg=cfg, log=self.log)
        self.views[0].query = lambda: self._core().query(self.id)
        self.views[1].query = lambda: mirror_state(self._core().query(self.id))
        for i in (0, 1):
            self.out[i].put(self.views[i].start())
        seed = random.randrange(1 << 30)
        rule = duel_rule()                                # §27: "MR5" salvo YO2_DUEL_RULE / OCG.DUEL_RULE
        # NOTES §30: con chain_none_prompt cada jugador ve sus ventanas de cadena VACÍAS (aviso 204/205, como el oficial)
        self._core().new(self.id, codes, first=first, seed=seed, shuffle=True, bot=(False, False), rule=rule,
                         auto_chain=not self.views[0].cfg.get("chain_none_prompt"))
        self.log(f"[pvp] duelo {self.id}: empieza {first}, regla {rule}, mazos {len(codes[0]['main'])}+{len(codes[0]['extra'])}"
                 f" / {len(codes[1]['main'])}+{len(codes[1]['extra'])}")
        self.pump()

    def pump(self):
        try:
            self._pump()
        except Exception as e:                        # noqa: BLE001  B03/B25: antes [EXC] en PvpMatch y duelo parado
            self._fatal(e)

    def _pump(self):
        while not self.over:
            evs = self._core().wait(self.id)
            last = evs[-1]
            kind = last.get("ev")
            if self.clk:                                  # límite de tiempo: cada turno repone los presupuestos
                for e in evs:
                    if e.get("ev") == "msg" and e.get("type") == "NEW_TURN" and e.get("player") in (0, 1):
                        self._clk_turn(e["player"])
            if kind != "error":
                self.core_errors = 0
            if isinstance(last.get("turn"), int):
                self.turn = last["turn"]
            if kind == "end":
                for i in (0, 1):
                    v = self.views[i]
                    v.query = None                    # duelcore ya cerró el duelo
                    self.out[i].put([m for m in v.feed_many(self.ev_for(i, evs[:-1])) if not _is_end(m)])
                    v.finished = True
                self.over = True
                self.ended = True
                self.decider = None
                w = last.get("winner")
                self.log(f"[pvp] fin {self.id}: ganador {w}, motivo {last.get('reason')}, turnos {last.get('turns')}")
                self.last_lp = last.get("lp")             # LP finales [jugador 0, jugador 1] (orden del cliente): lp_total del perfil
                self.finish(w if w in (0, 1) else None, last.get("reason") or 0, last.get("turns") or 0)
                return
            if kind == "select":
                p = last.get("player", 0)
                p = p if p in (0, 1) else 0
                o = 1 - p
                if last.get("type") == "SELECT_SUM" and not ritual_sum_ok():
                    # sin el parche de duelcore (YO2_PARCHE_SUM=0) o con YO2_RITUAL_SUM=0: como antes, decide bot.ts
                    for i in (0, 1):
                        self.out[i].put(self.views[i].feed_many(self.ev_for(i, evs[:-1])))
                    # Prueba 2 (fake_client pvp, mazo de la NPC 51 con rituales): bot.ts tampoco acierta y duelcore
                    # vuelve a pedir lo mismo (retry) sin fin: el hilo de la partida se quedaba girando y los dos
                    # clientes congelados. Tope: tras PVP_SUM_MAX intentos seguidos se corta el duelo como empate.
                    self.sum_autos = getattr(self, "sum_autos", 0) + 1
                    if self.sum_autos == 1 or last.get("retry") is False:
                        self.log(f"[pvp] {self.id}: SELECT_SUM del jugador {p}: decide bot.ts")
                    if self.sum_autos > PVP_SUM_MAX:
                        self.log(f"[pvp] {self.id}: SELECT_SUM sin respuesta válida tras {PVP_SUM_MAX} intentos: "
                                 f"se corta el duelo (empate)")
                        self.end()
                        self.finish(None, "sum", last.get("turn") or 0)
                        return
                    self._core().auto(self.id)
                    continue
                self.sum_autos = 0
                self.out[o].put(self.views[o].feed_many(self.ev_for(o, evs[:-1])))
                v = self.views[p]
                self.out[p].put(v.feed_many(self.ev_for(p, evs)))
                if hasattr(v, "sync_stats"):
                    try:                                  # ATK/DEF de ocgcore antes de que decida (como OcgSession)
                        self.out[p].put(v.sync_stats(v.query()))
                    except Exception as e:                # noqa: BLE001
                        self.log(f"[pvp] sync_stats: {e!r}")
                if v.needs_auto():
                    self._cmd(p, v.auto_response())
                    continue
                self.decider = p
                if self.clk:                              # límite de tiempo: corre el reloj de p
                    self._clk_begin(p)
                return
            for i in (0, 1):                              # error u otro: que los dos vean lo que haya
                self.out[i].put(self.views[i].feed_many(self.ev_for(i, evs)))
            if kind == "error":
                # B03: como OcgSession: si duelcore cerró el duelo, su «end» llega en el wait siguiente; si sigue
                # esperando, bot.ts decide una vez por quien tuviera la decisión; otro error seguido = _fatal
                self.core_errors += 1
                self.log(f"[pvp] error de duelcore en {self.id} ({self.core_errors}.º seguido): {last.get('error')}")
                if self.core_errors > CORE_ERROR_AUTO:
                    raise DuelCoreError(f"{self.core_errors} errores seguidos de duelcore: {last.get('error')}")
                self.decider = None
                self._core().auto(self.id)
                continue
            return

    def _cmd(self, p, cmd):
        if self.clk:                                      # límite de tiempo: la decisión de p termina, se cobra
            self._clk_stop()
        self.decider = None
        if cmd is None or cmd.get("cmd") == "auto":
            self._core().auto(self.id)
        else:
            self._core().respond(self.id, unmirror_response(p, cmd["response"]), player=p)

    def on_client(self, i, msg_id, data):
        """Jugada del cliente i (absoluto). Solo llega a duelcore si la decisión pendiente es suya."""
        if self.over or self.views[i] is None:
            return
        try:
            cmd, back = self.views[i].on_client(msg_id, data)
        except Exception as e:                        # noqa: BLE001  B02: como OcgSession.on_client
            self.log(f"[EXC] [pvp] {self.id}: traductor de {i} con 0x{msg_id:04x}: {e!r} | "
                     + traceback.format_exc().replace(chr(10), " | "))
            try:
                self.out[i].put(self.views[i]._resend())
            except Exception as e2:                   # noqa: BLE001
                self.log(f"[pvp] {self.id}: no se puede volver a pedir la jugada a {i}: {e2!r}")
            return
        self.out[i].put(back)
        if cmd:
            if self.decider != i:
                self.log(f"[pvp] {self.id}: respuesta de {i} sin ser su decisión (decide {self.decider}): ignorada")
                return
            try:
                self._cmd(i, cmd)
            except Exception as e:                    # noqa: BLE001  B25
                return self._fatal(e)
            self.pump()

    def resend(self, i):
        v = self.views[i]
        if v is not None and not self.over:
            self.out[i].put(v._resend())

    def auto(self, i):
        """Prueba (YO2_PVP_TEST): que bot.ts decida por i su decisión pendiente. False si no es suya."""
        if self.over or self.decider != i or self.views[i] is None:
            return False
        v = self.views[i]
        v.waiting = None; v.state = None; v.viewing = False
        try:
            self._cmd(i, {"cmd": "auto"})
        except Exception as e:                        # noqa: BLE001  B25
            self._fatal(e)
            return True
        self.pump()
        return True

    def end(self):
        """Corta el duelo en duelcore (rendición/desconexión: el fin lo manda PvpMatch)."""
        if self.over:
            return
        self.over = True
        self.ended = True                             # el fin lo manda PvpMatch (o el SELECT_SUM de pump)
        self.decider = None
        if self.clk:
            self.clk["run"] = None
        for v in self.views:
            if v is not None:
                v.query = None
        if self.started and self.dc is not None and core_alive(self.dc):
            try:
                self.dc.end(self.id)
            except Exception:                         # noqa: BLE001
                pass

    # ------------------------------------------------------------------ límite de tiempo (TIEMPO.md «Implementado»)
    # Modelo (C del cliente, FUN_00692bf0/FUN_00692cc0): UN solo reloj activo, el del último 0x6461; el del jugador
    # del turno se queda quieto con su valor guardado mientras corre el de otro. El servidor lleva la cuenta de verdad:
    # - «left»: presupuesto de TU turno (turn_timer = 180, 60 en GUNSLINGER); vuelve a lleno en tu NEW_TURN.
    # - «rleft»: presupuesto para contestar en el turno del RIVAL (tiempo_respuesta = 30, I); vuelve a lleno cada vez
    #   que empieza un turno del rival.
    # Solo corre mientras ese jugador tiene una decisión pendiente (decider) y desde que la petición SALE hacia su
    # cliente (marca en su cola de envío: el tiempo de duelcore, del rival y de la cola no cuenta). Al agotarse (+ el
    # margen), PvpMatch._timeout da la derrota.
    def _clk_turn(self, p):
        """NEW_TURN de p: su turno y la respuesta del rival vuelven a estar llenos."""
        k = self.clk
        k["tp"] = p
        k["left"][p] = k["turn"]
        k["rleft"][1 - p] = k["resp"]

    def _clk_pool(self, p):
        return "rleft" if self.clk["tp"] not in (None, p) else "left"

    def clock_left(self, p):
        """Segundos que le quedan a p en el presupuesto que gastaría ahora (descontado lo que lleva corriendo)."""
        k = self.clk
        if not k:
            return None
        left = k[self._clk_pool(p)][p]
        r = k["run"]
        if r is not None and r["p"] == p and r["t0"] is not None:
            left -= time.monotonic() - r["t0"]
        return max(0.0, left)

    def _clk_begin(self, p):
        """Nueva decisión de p (ya encolada su petición): 0x6461 a los dos y marca de salida en la cola de p."""
        k = self.clk
        pool = self._clk_pool(p)
        k["run"] = None
        if (k["turn"] if pool == "left" else k["resp"]) <= 0:      # 0 = ese presupuesto sin límite
            return
        k["gen"] += 1
        gen = k["gen"]
        k["run"] = {"p": p, "gen": gen, "t0": None, "pool": pool}
        self._clk_show(p)
        notify = k.get("notify")
        if notify is None:                                         # sin hilo que vigile (pruebas): corre ya
            k["run"]["t0"] = time.monotonic()
            return
        mark = lambda g=gen: notify(("clock", g, time.monotonic()))   # noqa: E731  (hilo del Sender)
        self.out[p].put([(None, mark)])

    def _clk_show(self, p):
        """0x6461 (u8 lado en SU vista, s32 s, u8 pausado, s32 −1) para que cada cliente pinte el reloj que corre: el
        de p. Si p contesta en el turno del rival, antes se congela (pausado) el del jugador del turno con lo que le
        queda: FUN_00692cc0 enseña el guardado del jugador del turno, quieto, mientras otro tiene el activo."""
        k, o = self.clk, 1 - p
        tw = TR.timeup_warning
        sec = lambda x: max(0, int(round(x)))                      # noqa: E731
        mine = sec(self.clock_left(p))
        if k["tp"] in (None, p):
            self.out[p].put([(0x6461, tw(0, mine, 0))])
            self.out[o].put([(0x6461, tw(1, mine, 0))])
        else:
            tl = sec(k["left"][o])
            self.out[p].put([(0x6461, tw(1, tl, 1)), (0x6461, tw(0, mine, 0))])
            self.out[o].put([(0x6461, tw(0, tl, 1)), (0x6461, tw(1, mine, 0))])

    def _clk_stop(self):
        """La decisión en curso termina (respuesta, auto): se cobra lo gastado desde que salió la petición."""
        k = self.clk
        r, k["run"] = k["run"], None
        if r is None or r["t0"] is None:
            return
        pool = k[r["pool"]]
        pool[r["p"]] = max(0.0, pool[r["p"]] - (time.monotonic() - r["t0"]))

    def clock_check(self, gen, t0=None):
        """Hilo de la partida (PvpMatch). Con t0: la petición de la decisión gen acaba de salir hacia el cliente y el
        reloj cuenta desde t0. Devuelve ("expired", p) si a p se le ha acabado el tiempo (con el margen), ("wait", s)
        para volver a mirar dentro de s, o None (esa decisión ya terminó, duelo acabado o sin límite)."""
        k = self.clk
        if not k or self.over:
            return None
        r = k["run"]
        if r is None or r["gen"] != gen:
            return None
        if t0 is not None and r["t0"] is None:
            r["t0"] = t0
        if r["t0"] is None:
            return None
        rest = k[r["pool"]][r["p"]] + k["margin"] - (time.monotonic() - r["t0"])
        return ("expired", r["p"]) if rest <= 0 else ("wait", rest)


def _clock_state(c):
    """Estado del reloj de una PvpSession a partir de su configuración (ver PvpSession.__init__)."""
    turn, resp = float(c.get("turn") or 0), float(c.get("resp") or 0)
    return {"turn": turn, "resp": resp, "margin": max(0.0, float(c.get("margin") or 0)), "notify": c.get("notify"),
            "tp": None, "left": [turn, turn], "rleft": [resp, resp], "run": None, "gen": 0}
