"""Cerebro de NPC para el emulador de YO2: IA de reglas «al estilo Konami» sobre duelcore (ocgcore).

Idea (ver WEB.md §1 y BRAIN.md): reglas simples y codiciosas, la dificultad sale sobre todo del mazo, y
cada NPC tiene un perfil (`NpcProfile`) que ajusta unas pocas perillas: agresividad, si coloca boca abajo,
si guarda las trampas para el momento bueno, si gasta la destrucción enseguida, umbral para atacar,
cuántos tributos acepta y una «torpeza» (errores aleatorios) que baja con el nivel.

Uso (con duelcore en modo cerebro, `brain`):

    from brain import NpcBrain, PERFILES
    b = NpcBrain(PERFILES["dificil"], player=1, seed=1234)
    dc.new(id, decks, brain="python")              # el jugador 1 lo decide Python
    for ev in dc.wait(id):
        if ev["ev"] == "msg":
            b.observe(ev)                          # solo usa información pública
    sel = evs[-1]
    resp = b.choose(sel)                           # dict listo para dc.respond(); None = que decida bot.ts (auto)

El cerebro no copia código de WindBot. Algunas ideas generales (atacar con el más débil que gane, no
encadenar a lo propio, «usar las cartas cuando tienen objetivo») son las de cualquier CPU de reglas.
"""
from __future__ import annotations

import collections
import json
import os
import random
import re
import sqlite3
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]                      # .../DuelCards
try:                                        # rutas de datos: yo2srv/rutas.py (YO2_VENDOR; sin ella, REPO/_vendor)
    import sys as _sys
    if str(HERE.parents[1]) not in _sys.path:
        _sys.path.append(str(HERE.parents[1]))  # tools/yo2emu
    from yo2srv import rutas as _rutas
except ImportError:                         # servidor viejo sin yo2srv/rutas.py (cargas en caliente sobre commits antiguos): la ruta de siempre
    _rutas = None
CDB = (Path(_rutas.vendor()) if _rutas else REPO / "_vendor") / "cards.cdb"

# --- constantes de ocgcore (constant.lua) ---------------------------------------------
TYPE_MONSTER, TYPE_SPELL, TYPE_TRAP = 0x1, 0x2, 0x4
TYPE_NORMAL, TYPE_EFFECT, TYPE_FUSION, TYPE_RITUAL = 0x10, 0x20, 0x40, 0x80
TYPE_TOKEN, TYPE_QUICKPLAY, TYPE_CONTINUOUS, TYPE_EQUIP = 0x4000, 0x10000, 0x20000, 0x40000
TYPE_FIELD, TYPE_COUNTER, TYPE_FLIP = 0x80000, 0x100000, 0x200000
TYPE_EXTRA = 0x40 | 0x2000 | 0x800000 | 0x4000000

LOC_DECK, LOC_HAND, LOC_MZONE, LOC_SZONE, LOC_GRAVE, LOC_REMOVED, LOC_EXTRA = 1, 2, 4, 8, 16, 32, 64
POS_FU_ATK, POS_FD_ATK, POS_FU_DEF, POS_FD_DEF = 1, 2, 4, 8
PH_DRAW, PH_STANDBY, PH_M1, PH_BATTLE_START, PH_BATTLE_STEP = 1, 2, 4, 8, 16
PH_DAMAGE, PH_DAMAGE_CAL, PH_BATTLE, PH_M2, PH_END = 32, 64, 128, 256, 512

# HINTMSG_* (qué pide el SELECT_CARD siguiente)
H_RELEASE, H_DISCARD, H_DESTROY, H_REMOVE, H_TOGRAVE, H_RTOHAND, H_ATOHAND, H_TODECK = 500, 501, 502, 503, 504, 505, 506, 507
H_SUMMON, H_SPSUMMON, H_SET, H_EQUIP, H_CONTROL, H_ATTACKTARGET, H_EFFECT, H_TARGET = 508, 509, 510, 518, 520, 549, 550, 551
# 12-10: la NPC usa rituales (duelcore parchea SELECT_SUM). YO2_NPC_RITUALES=0 = como antes (nunca); sin el parche
# (YO2_PARCHE_SUM=0) tampoco: el mensaje llegaría roto
NPC_RITUALES = all(os.environ.get(k, "1") not in ("", "0") for k in ("YO2_NPC_RITUALES", "YO2_PARCHE_SUM"))
HINTS_MALO = {H_RELEASE, H_DISCARD, H_DESTROY, H_REMOVE, H_RTOHAND, H_TODECK, H_CONTROL, H_ATTACKTARGET, 530}
HINTS_BUENO = {H_ATOHAND, H_SUMMON, H_SPSUMMON, H_SET, H_EQUIP, 514, 522, 527, 529}

# Opcodes de ANNOUNCE_CARD (constant.lua de EDOPro, 64 bits)
OP = {name: (0x40000000 + i) << 32 for name, i in (
    ("ADD", 0x0), ("SUB", 0x1), ("MUL", 0x2), ("DIV", 0x3), ("AND", 0x4), ("OR", 0x5), ("NEG", 0x6), ("NOT", 0x7),
    ("BAND", 0x8), ("BOR", 0x9), ("BNOT", 0x10), ("BXOR", 0x11), ("LSHIFT", 0x12), ("RSHIFT", 0x13),
    ("ALLOW_ALIASES", 0x14), ("ALLOW_TOKENS", 0x15), ("ISCODE", 0x100), ("ISSETCARD", 0x101), ("ISTYPE", 0x102),
    ("ISRACE", 0x103), ("ISATTRIBUTE", 0x104), ("GETCODE", 0x105), ("GETSETCARD", 0x106), ("GETTYPE", 0x107),
    ("GETRACE", 0x108), ("GETATTRIBUTE", 0x109))}
OP_BY_VALUE = {v: k for k, v in OP.items()}


# ======================================================================================
# Cartas (cards.cdb)
# ======================================================================================

# Etiquetas por texto (inglés de cards.cdb). Orden: lo más concreto primero.
_TAGS = [
    ("nuke_opp", r"destroy all (?:face-up |attack position )*monsters your opponent controls|destroy all (?:of )?your opponent'?s? (?:face-up )?monsters|destroy all monsters your opponent"),
    ("nuke_all", r"destroy all monsters on the field|destroy all monsters\b(?! your opponent)|destroy as many monsters"),
    ("storm_opp", r"destroy all spells? (?:and|&) traps? (?:cards )?your opponent controls|destroy all spell and trap cards your opponent"),
    ("storm_all", r"destroy all spells? (?:and|&) traps?(?: cards)?(?: on the field)?\.|return all spell and trap cards on the field"),
    ("control", r"take control|switch(?:es)? control"),
    ("negate", r"negate (?:the|that|its) (?:activation|summon)|negate the summon|negate the activation"),
    ("on_attack", r"when (?:an|a|your) (?:opponent'?s? )?monster declares an? (?:direct )?attack|when an opponent'?s monster declares|declares an attack: |attacking monster"),
    ("on_summon", r"when (?:your opponent|a monster|a monster\(s\)|your opponent'?s? monster)[^.]{0,40}summon|when a monster\(s\) is summoned|would be summoned"),
    ("draw", r"\bdraw (?:\d|a|one|two|three|1|2|3) ?(?:cards?)?|then draw"),
    ("burn", r"inflict \d+ (?:points of )?damage to your opponent|inflict \d+ damage|inflict damage to your opponent"),
    ("heal", r"gain \d+ (?:life points|lp)|increase your life points|you gain lp|gain lp"),
    ("revive", r"special summon[^.]{0,60}(?:graveyard|\bgy\b)|in (?:your|either) (?:graveyard|gy)[^.]{0,30}special summon"),
    ("search", r"from your deck to your hand|add 1 [^.]{0,60}from your deck"),
    ("stall", r"cannot (?:declare an )?attack|no monsters can attack|cannot attack"),
    ("boost", r"gains? \d+ atk|increase (?:the|its) atk|atk (?:increases|is increased)|gains? atk|\+\d+ atk"),
    ("weaken", r"loses? \d+ atk|decrease (?:the|its) atk|atk becomes halved|original atk becomes|atk becomes 0"),
    ("bounce", r"return that target to the hand|return (?:it|that monster|1 monster) to (?:the|its owner'?s?) hand"),
    ("destroy_monster", r"destroy (?:that|the|1|one|it|this)[^.]{0,60}monster|monster[^.]{0,60}; destroy (?:that|it)|destroy that face-up monster"),
    ("destroy_st", r"(?:spell/trap|spell or trap|spell and trap|spell card)[^.]{0,60}; destroy|destroy (?:that|the|1)[^.]{0,30}(?:spell/trap|spell or trap)"),
    ("destroy_any", r"target 1 card on the field; destroy|destroy 1 card"),
    ("fusion", r"fusion summon|fusion material"),
    ("ritual", r"ritual summon"),
    ("pay_half", r"pay half your (?:lp|life points)"),
]
_TAGS_RE = [(t, re.compile(p)) for t, p in _TAGS]

# 10-10: cartas que dan ventaja a quien las lleva, no a «el que tenga monstruos»: un equipo/boost sin monstruo propio
# acaba en el campo del rival. Los equipos con estas etiquetas tienen otro fin (robar, destruir...) y no cuentan.
_TAGS_NO_BOOST = frozenset({"control", "weaken", "destroy_monster", "destroy_any", "bounce", "stall", "negate",
                            "nuke_opp", "nuke_all", "revive"})
# Efectos que se deshacen solos en la End Phase («banish all monsters that were Special Summoned by this effect»)
_RX_TEMPORAL = re.compile(r"end phase[^.]{0,40}(?:banish|destroy|return|send|shuffle)[^.]{0,60}special summoned by this")

HARMFUL_TAGS = {"nuke_opp", "nuke_all", "storm_opp", "storm_all", "control", "destroy_monster", "destroy_st",
                "destroy_any", "bounce", "burn", "weaken", "stall", "negate", "draw", "revive", "boost"}


@dataclass(frozen=True)
class Card:
    code: int
    name: str
    type: int
    atk: int
    deff: int
    level: int
    race: int
    attribute: int
    alias: int
    setcode: int
    desc: str
    tags: frozenset

    @property
    def is_monster(self):
        return bool(self.type & TYPE_MONSTER)

    @property
    def is_spell(self):
        return bool(self.type & TYPE_SPELL)

    @property
    def is_trap(self):
        return bool(self.type & TYPE_TRAP)

    @property
    def is_flip(self):
        return bool(self.type & TYPE_FLIP) or self.desc.lower().startswith("flip:")

    @property
    def is_extra(self):
        return bool(self.type & TYPE_EXTRA)

    def tributes(self) -> int:
        if not self.is_monster or self.is_extra:
            return 0
        return 0 if self.level <= 4 else (1 if self.level <= 6 else 2)


class CardDB:
    """cards.cdb en memoria (14.7k cartas, ~0,3 s). Una sola instancia compartida: CardDB.shared()."""
    _shared: "CardDB | None" = None

    def __init__(self, path: Path = CDB):
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        rows = con.execute("select d.id, t.name, d.type, d.atk, d.def, d.level, d.race, d.attribute, d.alias, d.setcode, t.desc "
                           "from datas d join texts t on t.id = d.id").fetchall()
        con.close()
        self.cards: dict[int, Card] = {}
        self.by_name: dict[str, int] = {}
        for code, name, typ, atk, df, lvl, race, attr, alias, setcode, desc in rows:
            low = (desc or "").lower()
            tags = frozenset(t for t, rx in _TAGS_RE if rx.search(low))
            c = Card(code, name or "", typ, max(atk, 0), max(df, 0), lvl & 0xff, race, attr, alias, setcode, desc or "", tags)
            self.cards[code] = c
            if not alias:
                self.by_name.setdefault((name or "").lower(), code)

    @classmethod
    def shared(cls) -> "CardDB":
        if cls._shared is None:
            cls._shared = CardDB()
        return cls._shared

    def get(self, code) -> Card | None:
        try:
            return self.cards.get(int(code))
        except (TypeError, ValueError):
            return None

    def code(self, name: str) -> int:
        return self.by_name[name.lower()]


# ======================================================================================
# Perfiles
# ======================================================================================

@dataclass
class NpcProfile:
    nombre: str = "normal"
    agresividad: float = 0.6          # 0..1: ataca boca abajo, acepta empates, ataca aunque haya trampas
    coloca_boca_abajo: bool = True    # monstruos débiles/flip boca abajo en defensa (si no, todo en ATK)
    guarda_trampas: bool = True       # trampas solo en el momento bueno (si no, en cuanto se pueda)
    destruccion_inmediata: bool = True  # Raigeki/Fissure/MST nada más tener objetivo (si no, solo si hace falta)
    umbral_ataque: int = 0            # ataca si ATK propio - valor del objetivo > umbral (negativo = suicida)
    max_tributos: int = 2             # tributos que acepta para invocar (0, 1 o 2)
    torpeza: float = 0.1              # probabilidad de una decisión aleatoria/mala en cada jugada importante
    defensa_boca_abajo: int = 1200    # DEF que supone para un monstruo boca abajo del rival
    curar_por_debajo: int = 6500      # usa curaciones con LP por debajo de esto
    acciones_por_turno: int = 14      # tope de decisiones de fase principal por turno (anti-bucles)

    @staticmethod
    def por_nivel(nivel: int, **cambios) -> "NpcProfile":
        """Nivel 1..9 (como las estrellas de Power of Chaos): torpeza y umbral bajan con el nivel."""
        n = max(1, min(9, int(nivel)))
        f = (n - 1) / 8                                       # 0 (nivel 1) .. 1 (nivel 9)
        p = NpcProfile(
            nombre=f"nivel{n}",
            agresividad=round(0.85 - 0.4 * f, 2),
            coloca_boca_abajo=n >= 3,
            guarda_trampas=n >= 4,
            destruccion_inmediata=n <= 6,
            umbral_ataque=int(round(-800 * (1 - f) ** 2 / 50) * 50),
            max_tributos=1 if n <= 2 else 2,
            torpeza=round(0.4 * (1 - f) ** 1.5, 3),
            defensa_boca_abajo=int(800 + 700 * f) // 50 * 50,
            curar_por_debajo=6500,
            acciones_por_turno=8 + n,
        )
        return replace(p, **cambios)

    @staticmethod
    def from_dict(d: dict) -> "NpcProfile":
        ok = {f.name for f in fields(NpcProfile)}
        return NpcProfile(**{k: v for k, v in d.items() if k in ok})


PERFILES = {
    "facil": NpcProfile("facil", agresividad=0.85, coloca_boca_abajo=False, guarda_trampas=False, destruccion_inmediata=True,
                        umbral_ataque=-1000, max_tributos=1, torpeza=0.5, defensa_boca_abajo=800, acciones_por_turno=9),
    "normal": NpcProfile("normal", agresividad=0.65, coloca_boca_abajo=True, guarda_trampas=True, destruccion_inmediata=True,
                         umbral_ataque=0, max_tributos=2, torpeza=0.12, defensa_boca_abajo=1200, acciones_por_turno=13),
    "dificil": NpcProfile("dificil", agresividad=0.45, coloca_boca_abajo=True, guarda_trampas=True, destruccion_inmediata=False,
                          umbral_ataque=0, max_tributos=2, torpeza=0.0, defensa_boca_abajo=1500, acciones_por_turno=17),
}


# ======================================================================================
# Tablero (a partir de select.view de duelcore)
# ======================================================================================

def face_up(pos) -> bool:
    return bool((pos or 0) & (POS_FU_ATK | POS_FU_DEF))


def in_attack(pos) -> bool:
    return bool((pos or 0) & (POS_FU_ATK | POS_FD_ATK))


class Board:
    def __init__(self, sel: dict, me: int, db: CardDB, prof: NpcProfile):
        v = sel.get("view") or {}
        ps = v.get("players") or [{}, {}]
        self.me, self.op = me, 1 - me
        self.db = db
        self.lp = sel.get("lp") or [8000, 8000]
        self.turn_player = sel.get("turn_player")
        self.phase = sel.get("phase", 0)
        self.my_turn = self.turn_player == me
        mine, opp = ps[me] or {}, ps[1 - me] or {}
        self.hand = [c for c in mine.get("hand", []) if c]
        self.op_hand_count = opp.get("hand_count", 0)
        self.mz = [mine.get("mzone", []), opp.get("mzone", [])]   # 0 = mío, 1 = rival
        self.sz = [mine.get("szone", []), opp.get("szone", [])]
        self.grave = [mine.get("grave", []), opp.get("grave", [])]
        self.removed = [mine.get("removed", []), opp.get("removed", [])]   # desterradas (las propias, con código)
        self.my_m = [(i, c) for i, c in enumerate(self.mz[0]) if c]
        self.op_m = [(i, c) for i, c in enumerate(self.mz[1]) if c]
        self.my_st = [(i, c) for i, c in enumerate(self.sz[0][:5]) if c]
        self.op_st = [(i, c) for i, c in enumerate(self.sz[1]) if c and i < 6]   # 0-4 M/T, 5 = campo
        self.my_free_m = sum(1 for c in self.mz[0][:5] if not c) if self.mz[0] else 5
        self.my_free_s = sum(1 for c in self.sz[0][:5] if not c) if self.sz[0] else 5
        self.fd_guess = prof.defensa_boca_abajo

    # --- valores ---
    def mon_atk(self, c) -> int:
        if not c:
            return 0
        if not face_up(c.get("position")) and not c.get("code"):
            return self.fd_guess
        a = c.get("attack")
        if a is None:
            ci = self.db.get(c.get("code"))
            return ci.atk if ci else 0
        return max(0, a)

    def mon_def(self, c) -> int:
        if not c:
            return 0
        if not face_up(c.get("position")) and not c.get("code"):
            return self.fd_guess
        d = c.get("defense")
        if d is None:
            ci = self.db.get(c.get("code"))
            return ci.deff if ci else 0
        return max(0, d)

    def mon_value(self, c) -> int:
        """Lo que «vale» un monstruo en el campo: su ATK (o DEF si es mejor muro), mínimo 300.
        Uno prestado (Change of Heart: está en mi campo pero su dueño es el rival) no vale nada para mí."""
        if self.borrowed(c):
            return 0
        return max(300, self.mon_atk(c), int(self.mon_def(c) * 0.8))

    def borrowed(self, c) -> bool:
        return bool(c) and c.get("owner") is not None and c.get("owner") != self.me and any(x is c for x in self.mz[0])

    def side(self, controller) -> int:
        return 0 if controller == self.me else 1

    def mon_at(self, controller, seq):
        z = self.mz[self.side(controller)]
        return z[seq] if 0 <= seq < len(z) else None

    @property
    def op_threat(self) -> int:
        """ATK más alto del rival (los boca arriba en DEF cuentan: pueden pasar a ATK)."""
        return max((self.mon_atk(c) if face_up(c.get("position")) else self.fd_guess for _, c in self.op_m), default=0)

    @property
    def my_best(self) -> int:
        return max((self.mon_atk(c) for _, c in self.my_m), default=0)

    def side_value(self, s) -> int:
        ms = self.my_m if s == 0 else self.op_m
        return sum(self.mon_value(c) for _, c in ms)

    def grave_best(self, both=True) -> int:
        best = 0
        for s in ((0, 1) if both else (0,)):
            for code in self.grave[s]:
                ci = self.db.get(code)
                if ci and ci.is_monster and not ci.is_extra:
                    best = max(best, ci.atk)
                elif ci and ci.is_monster:
                    best = max(best, ci.atk - 500)    # las de fusión no siempre se pueden revivir
        return best


# ======================================================================================
# Cerebro
# ======================================================================================

def R_IDLE(action, index=None):
    return {"type": "SELECT_IDLECMD", "action": action, "index": index}


def R_BATTLE(action, index=None):
    return {"type": "SELECT_BATTLECMD", "action": action, "index": index}


class NpcBrain:
    """Decide por un jugador (índice del protocolo de duelcore). Un objeto por duelo y jugador."""

    def __init__(self, profile: NpcProfile | str = "normal", player: int = 1, seed=None, db: CardDB | None = None):
        self.p = PERFILES[profile] if isinstance(profile, str) else profile
        self.me = player
        self.db = db or CardDB.shared()
        self.rng = random.Random(seed)
        self.stats = collections.Counter()
        self.turn = None
        self._new_turn()
        self.hint = 0
        self.chain: list[tuple[int, int, int]] = []   # (controller, code, location) de la cadena en curso
        self.attack = None                            # ataque declarado en curso: (card, target) del protocolo
        self.opp_summon = None                        # (code, controller, sequence) recién invocado por el rival
        self.last_activated = 0                       # última carta que activé (para elegir objetivos)
        self.seen_opp = collections.Counter()         # códigos públicos del rival (para ANNOUNCE_*)
        self.attack_plan = None                       # (índice en attacks, seq objetivo o None)

    # ----------------------------------------------------------------------------------
    def _new_turn(self):
        self.idle_n = 0
        self.attacks_n = 0
        self.bp_done = False
        self.used = collections.Counter()

    def observe(self, ev: dict):
        """Mensajes públicos del duelo (ev["ev"] == "msg"). No mira robos ni cartas colocadas del rival."""
        if ev.get("ev") != "msg":
            return
        t = ev.get("type")
        if t == "NEW_TURN":
            self._new_turn()
            self.attack = None
        elif t == "NEW_PHASE":
            self.attack = None
            self.opp_summon = None
        elif t == "HINT":
            if ev.get("hint_type") == 3 and ev.get("player") == self.me:
                self.hint = ev.get("hint", 0)
        elif t == "ATTACK":
            self.attack = (ev.get("card"), ev.get("target"))
        elif t in ("BATTLE", "ATTACK_DISABLED", "DAMAGE_STEP_END"):
            self.attack = None
        elif t in ("SUMMONING", "SPSUMMONING", "FLIPSUMMONING"):
            if ev.get("controller") != self.me:
                self.opp_summon = (ev.get("code"), ev.get("controller"), ev.get("sequence"))
                self.seen_opp[ev.get("code")] += 1
        elif t == "CHAINING":
            self.chain.append((ev.get("controller"), ev.get("code"), ev.get("location")))
            if ev.get("controller") != self.me:
                self.seen_opp[ev.get("code")] += 1
        elif t in ("CHAIN_END",):
            self.chain = []

    # ----------------------------------------------------------------------------------
    def choose(self, sel: dict) -> dict | None:
        """Respuesta para un evento select de duelcore. None = no sé (que conteste bot.ts con `auto`)."""
        if sel.get("turn") != self.turn:
            self.turn = sel.get("turn")
            self._new_turn()
        t = sel.get("type")
        b = Board(sel, self.me, self.db, self.p)
        self.stats[t] += 1
        fn = getattr(self, "_" + (t or "").lower(), None)
        if fn is None:
            self.stats["sin_regla"] += 1
            return None
        return fn(sel, b)

    def _blocked(self, code) -> bool:
        """Cartas que la NPC no usa nunca. Rituales: solo con YO2_NPC_RITUALES=0 (hasta el 12-10 siempre, porque
        ocgcore-wasm 0.1.2 leía mal SELECT_SUM; duelcore lo parchea ya y las NPC de Konami sí los usaban)."""
        if NPC_RITUALES:
            return False
        ci = self.db.get(code)
        return bool(ci and ci.is_spell and (ci.type & TYPE_RITUAL or "ritual" in ci.tags))

    def _torpe(self, k=1.0) -> bool:
        return self.p.torpeza > 0 and self.rng.random() < self.p.torpeza * k

    # --- 10-10: jugadas que no se hacen ni por torpeza -------------------------------------
    def _es_boost(self, ci: Card) -> bool:
        """Equipo/boost puro: solo sirve poniéndolo en un monstruo propio."""
        return not ci.is_monster and not ci.tags & _TAGS_NO_BOOST and ("boost" in ci.tags or bool(ci.type & TYPE_EQUIP))

    def _objetivo_propio(self, b: Board) -> bool:
        return any(face_up(m.get("position")) and not b.borrowed(m) for _, m in b.my_m)

    def _temporal_compensa(self, b: Board) -> bool:
        """Return from the Different Dimension y similares: paga la mitad de LP y lo invocado se va en la End Phase.
        Solo en mi turno (M1 / inicio de batalla), con >= 2 desterrados utilizables y si el daño (campo vacío) o las
        bajas (monstruos del rival que ganan en combate) superan lo que se paga."""
        if not b.my_turn or b.phase not in (PH_M1, PH_BATTLE_START):
            return False
        mons = [ci for ci in (self.db.get(c) for c in b.removed[0] if c) if ci and ci.is_monster]
        n = min(len(mons), b.my_free_m)
        if n < 2:
            return False
        atks = sorted((ci.atk for ci in mons), reverse=True)[:n]
        lp, cost = b.lp[self.me], (b.lp[self.me] + 1) // 2
        if not b.op_m:
            gain = sum(atks)
            return gain >= b.lp[b.op] or (gain > cost and lp - cost > 1000)
        gain = 0
        left = list(atks)
        for _, t in sorted(b.op_m, key=lambda x: -b.mon_value(x[1])):
            if not face_up(t.get("position")):
                continue
            need = b.mon_atk(t) if in_attack(t.get("position")) else b.mon_def(t)
            win = [a for a in left if a > need]
            if win:
                left.remove(min(win))
                gain += b.mon_value(t)
        return gain > cost and lp - cost > 1000

    def _jugada_mala(self, ci: Card, b: Board) -> bool:
        """True si activar `ci` ahora regala algo al rival o sale a pérdida, sea cual sea la torpeza de la NPC:
        - equipo/boost sin monstruo propio boca arriba (Metalmorph se lo pone al del rival);
        - cartas que pagan la mitad de LP con menos de 2000 LP;
        - cartas temporales (Return from the Different Dimension) fuera de su sitio: ver _temporal_compensa."""
        if ci.is_monster:
            return False
        if self._es_boost(ci) and not self._objetivo_propio(b):
            return True
        if "negate" in ci.tags:
            return False                       # los anuladores con coste de LP tienen su propia regla en _score_chain
        if "pay_half" in ci.tags and b.lp[self.me] < 2000:
            return True
        if _RX_TEMPORAL.search(ci.desc.lower()):
            return not self._temporal_compensa(b)
        return False

    def _act_mala(self, c: dict, b: Board) -> bool:
        ci = self.db.get(c.get("code"))
        return bool(ci and self._jugada_mala(ci, b))

    def _cap(self, key, n=1) -> bool:
        """True si todavía se puede hacer `key` este turno (y lo cuenta)."""
        if self.used[key] >= n:
            return False
        self.used[key] += 1
        return True

    # ======================= fase principal =======================
    def _select_idlecmd(self, sel, b: Board):
        self.idle_n += 1
        self.attack_plan = None
        self.opp_summon = None
        tired = self.idle_n > self.p.acciones_por_turno
        acts = sel.get("activates", [])
        if not tired:
            # Torpeza: una jugada al azar de las «normales» (invocar, colocar, activar desde la mano)
            if self._torpe(0.5):
                opts = [("SELECT_SUMMON", i) for i in range(len(sel.get("summons", [])))]
                opts += [("SELECT_MONSTER_SET", i) for i in range(len(sel.get("monster_sets", [])))]
                opts += [("SELECT_SPELL_SET", i) for i in range(len(sel.get("spell_sets", [])))]
                opts += [("SELECT_ACTIVATE", i) for i, c in enumerate(acts) if c.get("location") == LOC_HAND
                         and not self._blocked(c["code"]) and not self._act_mala(c, b) and self.used[("act", c["code"], c["location"], c["sequence"], c.get("description", 0))] < 1]
                if opts:
                    a, i = self.rng.choice(opts)
                    self.stats["torpeza"] += 1
                    if a == "SELECT_ACTIVATE":
                        c = acts[i]
                        self.used[("act", c["code"], c["location"], c["sequence"], c.get("description", 0))] += 1
                        self.last_activated = c["code"]
                    return R_IDLE(a, i)

            scored = [(self._score_activation(c, b), i) for i, c in enumerate(acts)]
            scored = [(s, i) for s, i in scored if s > 0]
            scored.sort(reverse=True)
            # 1) Robo, destrucción, control... antes de invocar
            if scored and scored[0][0] >= 100:
                return self._activate(acts, scored[0][1])
            # 2) Invocaciones especiales (Cyber Dragon, fusiones desde el campo...)
            for i, c in enumerate(sel.get("special_summons", [])):
                if self._cap(("sp", c["code"], c["location"]), 2):
                    return R_IDLE("SELECT_SPECIAL_SUMMON", i)
            # 3) Invocación normal o colocar
            r = self._summon_choice(sel, b)
            if r:
                return r
            # 4) Equipos, campos, efectos de monstruo...
            if scored:
                return self._activate(acts, scored[0][1])
            # 5) Cambios de posición
            r = self._pos_change(sel, b)
            if r:
                return r
            # 6) Colocar trampas (y rápidas si las guarda)
            r = self._spell_set(sel, b)
            if r:
                return r
        # 7) Batalla o fin de turno
        if sel.get("to_bp") and not self.bp_done and b.phase == PH_M1 and any(self._could_attack(c, b) for _, c in b.my_m):
            return R_IDLE("TO_BP")
        if sel.get("to_ep"):
            return R_IDLE("TO_EP")
        if sel.get("to_bp"):
            return R_IDLE("TO_BP")
        # Nada más posible (raro): la primera acción que haya
        for a, k in (("SELECT_SUMMON", "summons"), ("SELECT_MONSTER_SET", "monster_sets"), ("SELECT_ACTIVATE", "activates"),
                     ("SELECT_SPELL_SET", "spell_sets"), ("SELECT_POS_CHANGE", "pos_changes"), ("SELECT_SPECIAL_SUMMON", "special_summons")):
            if sel.get(k):
                return R_IDLE(a, 0)
        return None

    def _activate(self, acts, i):
        c = acts[i]
        self.used[("act", c["code"], c["location"], c["sequence"], c.get("description", 0))] += 1
        self.last_activated = c["code"]
        self.stats["activa"] += 1
        return R_IDLE("SELECT_ACTIVATE", i)

    def _could_attack(self, c, b: Board) -> bool:
        """¿Vale la pena entrar en batalla por este monstruo? Solo si está boca arriba en ATK."""
        return in_attack(c.get("position")) and face_up(c.get("position"))

    def _score_activation(self, c, b: Board) -> int:
        """Puntuación de activar algo en la fase principal: >=100 antes de invocar, 1..99 después, 0 = no."""
        key = ("act", c["code"], c["location"], c["sequence"], c.get("description", 0))
        if self.used[key] >= (2 if c["location"] == LOC_HAND else 1):
            return 0
        ci = self.db.get(c["code"])
        if ci is None:
            return 10
        return self._card_use_score(ci, c, b, chain=False)

    def _card_use_score(self, ci: Card, c, b: Board, chain: bool) -> int:
        """Lo útil que es usar `ci` ahora. Compartido por fase principal y ventanas de cadena."""
        tags = ci.tags
        p = self.p
        loc = c.get("location", 0)
        is_monster_effect = ci.is_monster and loc in (LOC_MZONE, LOC_HAND, LOC_GRAVE, LOC_REMOVED, LOC_EXTRA)
        op_n, my_n = len(b.op_m), len(b.my_m)
        need_kill = op_n and (b.op_threat >= b.my_best or op_n >= 2)
        if is_monster_effect:
            return 45                                   # efectos de monstruo: Konami los usa siempre
        if self._jugada_mala(ci, b):
            return 0                                    # regala ventaja al rival o paga LP a cambio de nada
        if _RX_TEMPORAL.search(ci.desc.lower()):
            return 0 if chain else 90                   # superó _temporal_compensa: mi turno, ≥2 desterrados, daño/bajas > coste
        if ci.is_spell and (ci.type & TYPE_RITUAL or "ritual" in tags):
            # 12-10: con el parche de SELECT_SUM de duelcore la NPC ya puede (ocgcore solo la ofrece si hay monstruo
            # de ritual y sacrificios). Después de la invocación normal, como un equipo o un campo (< 100)
            return 0 if (chain or not NPC_RITUALES) else 95
        if "draw" in tags and ci.is_spell and not tags & {"nuke_opp", "nuke_all"}:
            return 200 if len(b.hand) <= 6 else 0
        if "nuke_opp" in tags:
            return 190 if op_n and (p.destruccion_inmediata or need_kill) else 0
        if "nuke_all" in tags and "on_summon" not in tags:
            gain = b.side_value(1) - b.side_value(0)
            return 175 if op_n and gain > (0 if p.destruccion_inmediata else 800) else 0
        if "storm_opp" in tags:
            return 160 if b.op_st else 0
        if "storm_all" in tags:
            mine = len(b.my_st) - (1 if loc == LOC_SZONE else 0)
            return 150 if len(b.op_st) > mine else 0
        if "control" in tags:
            if "switch" in ci.desc.lower():
                return 155 if op_n and my_n and b.op_threat > b.my_best else 0
            return 160 if op_n and b.my_free_m else 0
        if "revive" in tags:
            gb = b.grave_best()
            return 140 if b.my_free_m and gb >= max(1500, b.my_best) else (20 if b.my_free_m and gb >= 1000 else 0)
        if tags & {"destroy_monster", "bounce"} and not tags & {"on_attack", "on_summon"} and not ci.is_monster:
            if ci.type & TYPE_EQUIP:
                return 50 if op_n else 0
            return 150 if op_n and (p.destruccion_inmediata or need_kill) else 0
        if "destroy_any" in tags:
            return 140 if (op_n or b.op_st) and (p.destruccion_inmediata or need_kill or len(b.op_st) >= 2) else 0
        if "destroy_st" in tags and not tags & {"on_attack", "on_summon"}:
            if not b.op_st:
                return 0
            if p.destruccion_inmediata or len(b.op_st) >= 2 or (chain and not b.my_turn and b.phase == PH_END):
                return 140
            return 0
        if "stall" in tags and not tags & {"on_attack"}:
            if op_n and b.op_threat > b.my_best:
                return 130
            return 15 if not p.guarda_trampas else 0
        if "burn" in tags and not tags & {"on_attack"}:
            return 120
        if "heal" in tags:
            return 110 if b.lp[self.me] < p.curar_por_debajo else (25 if len(b.hand) >= 6 else 0)
        if "search" in tags:
            return 105
        if "fusion" in tags and ci.is_spell:
            return 95
        if tags & {"boost", "weaken"} or ci.type & TYPE_EQUIP:
            if "weaken" in tags and not "boost" in tags:
                return 60 if op_n else 0
            return 60 if any(face_up(m.get("position")) and not b.borrowed(m) for _, m in b.my_m) else 0
        if tags & {"on_attack", "on_summon", "negate"}:
            return 0                                     # esas se usan en la cadena del rival
        if ci.type & TYPE_FIELD:
            return 70
        if ci.is_trap:
            return 0 if p.guarda_trampas else 30
        return 30                                        # mágica sin etiqueta: Konami la usa igual

    # --- invocar ---
    def _summon_choice(self, sel, b: Board):
        best = None
        weakest = sorted(b.mon_value(c) for _, c in b.my_m)
        op_threat = b.op_threat
        for act, key in (("SELECT_SUMMON", "summons"), ("SELECT_MONSTER_SET", "monster_sets")):
            for i, c in enumerate(sel.get(key, [])):
                ci = self.db.get(c["code"])
                if ci is None:
                    continue
                trib = ci.tributes() if c.get("location") == LOC_HAND else 0
                if trib > self.p.max_tributos:
                    continue
                trib_val = sum(weakest[:trib]) if trib <= len(weakest) else 0
                atk, df = ci.atk, ci.deff
                if trib and atk <= trib_val + 300:
                    continue
                if act == "SELECT_SUMMON":
                    score = atk - trib_val
                    # ¿puede matar algo este turno? (M1 con batalla por delante)
                    kills = [b.mon_value(t) for _, t in b.op_m if face_up(t.get("position")) and
                             ((in_attack(t.get("position")) and atk > b.mon_atk(t)) or (not in_attack(t.get("position")) and atk > b.mon_def(t)))]
                    can_battle = b.phase == PH_M1 and sel.get("to_bp")
                    if not b.op_m or atk > op_threat:
                        score += 500          # domina el campo
                    elif can_battle and kills:
                        score += max(kills) // 2 - 200   # cambia golpe por golpe: se lleva uno aunque luego caiga
                    elif atk < op_threat and self.p.coloca_boca_abajo:
                        score -= 1200         # en ATK delante de algo más fuerte: se lo comen
                    if ci.is_flip:
                        score -= 1000
                else:
                    if not self.p.coloca_boca_abajo:
                        score = df * 0.3 - trib_val - 1500
                    else:
                        # muro: cuenta la DEF; un buen atacante colocado boca abajo se desperdicia
                        score = df - trib_val - 0.5 * max(0, atk - df) - 200
                        if df > op_threat:
                            score += 400
                        if ci.is_flip:
                            score += 800
                        if not b.op_m and atk >= 1000:
                            score -= 700
                if best is None or score > best[0]:
                    best = (score, act, i)
        if best is None:
            return None
        if self._torpe(0.3):
            self.stats["torpeza"] += 1
            return None                   # «se le olvida» invocar este turno (sigue con lo demás)
        return R_IDLE(best[1], best[2])

    def _pos_change(self, sel, b: Board):
        before_battle = b.phase == PH_M1 and sel.get("to_bp") and not self.bp_done
        for i, c in enumerate(sel.get("pos_changes", [])):
            m = b.mon_at(self.me, c.get("sequence", -1)) if c.get("location") == LOC_MZONE else None
            if not m:
                continue
            pos = m.get("position", 0)
            ci = self.db.get(c["code"])
            atk = b.mon_atk(m)
            df = b.mon_def(m)
            if not self._cap(("pos", c["code"], c["sequence"])):
                continue
            if before_battle and not in_attack(pos):
                # a ATK (o voltear) si así puede atacar con ventaja
                if (not b.op_m and atk >= 500) or atk > b.op_threat or (ci and ci.is_flip and not face_up(pos)):
                    return R_IDLE("SELECT_POS_CHANGE", i)
            elif not before_battle and in_attack(pos) and face_up(pos):
                if b.op_m and atk < b.op_threat and df >= atk - 200:
                    return R_IDLE("SELECT_POS_CHANGE", i)
        return None

    def _spell_set(self, sel, b: Board):
        for i, c in enumerate(sel.get("spell_sets", [])):
            ci = self.db.get(c["code"])
            if ci is None:
                continue
            if ci.is_trap or (ci.type & TYPE_QUICKPLAY and self.p.guarda_trampas):
                if b.my_free_s > (1 if ci.type & TYPE_QUICKPLAY else 0):
                    return R_IDLE("SELECT_SPELL_SET", i)
            elif b.phase == PH_M2 and len(b.hand) > 6 and b.my_free_s > 1:
                return R_IDLE("SELECT_SPELL_SET", i)          # mejor colocar que descartar
        return None

    # ======================= batalla =======================
    def _select_battlecmd(self, sel, b: Board):
        self.bp_done = True
        self.attacks_n += 1
        attacks = sel.get("attacks", [])
        leave = R_BATTLE("TO_M2") if sel.get("to_m2") else R_BATTLE("TO_EP")
        if not attacks or self.attacks_n > 10:
            return leave
        if self._torpe(0.6):
            # Torpeza: ataca con el primero, como bot.ts (aunque pierda)
            self.stats["torpeza"] += 1
            self.attack_plan = (0, None)
            return R_BATTLE("SELECT_BATTLE", 0)
        plan = self._plan_attack(attacks, b)
        if plan is None:
            return leave
        self.attack_plan = plan
        return R_BATTLE("SELECT_BATTLE", plan[0])

    def _plan_attack(self, attacks, b: Board):
        """(índice del atacante, secuencia del objetivo o None = directo). Mejor objetivo con el atacante más
        débil que lo gane; si nadie gana nada, ataque directo si se puede."""
        att = []
        for i, a in enumerate(attacks):
            m = b.mon_at(self.me, a.get("sequence", -1))
            att.append((b.mon_atk(m) if m else (self.db.get(a["code"]).atk if self.db.get(a["code"]) else 0), i, a))
        thr, agg = self.p.umbral_ataque, self.p.agresividad
        if not b.op_m:
            direct = [x for x in att if x[2].get("can_direct")]
            if direct:
                return (max(direct)[1], None)
            return (max(att)[1], None)
        # objetivos por valor (los que más interesa quitar primero)
        targets = sorted(b.op_m, key=lambda t: -b.mon_value(t[1]))
        best = None
        for seq, t in targets:
            pos = t.get("position", 0)
            fd = not face_up(pos)
            for A, i, a in sorted(att):
                if fd:
                    need = self.p.defensa_boca_abajo if not t.get("code") else b.mon_def(t)
                    margin = A - need
                    ok = margin > thr + (1 - agg) * 400
                    gain = 900 if ok else 0
                elif in_attack(pos):
                    margin = A - b.mon_atk(t)
                    ok = margin > thr or (margin == 0 and agg >= 0.6 and A <= b.mon_atk(t))
                    gain = b.mon_atk(t) + max(margin, 0)
                else:
                    margin = A - b.mon_def(t)
                    ok = margin > thr
                    gain = b.mon_value(t)
                if ok:
                    cand = (gain, -A, i, seq)
                    if best is None or cand > best:
                        best = cand
                    break                         # el atacante más débil que lo gana
        if best:
            return (best[2], best[3])
        direct = [x for x in att if x[2].get("can_direct")]
        if direct:
            return (max(direct)[1], None)
        return None

    # ======================= cadenas =======================
    def _select_chain(self, sel, b: Board):
        selects = sel.get("selects", [])
        if not selects:
            return {"type": "SELECT_CHAIN", "index": None}
        if sel.get("forced"):
            return {"type": "SELECT_CHAIN", "index": 0}
        scored = []
        for i, c in enumerate(selects):
            key = ("chain", c.get("code"), c.get("location"), c.get("sequence"), c.get("description", 0))
            if self.used[key] >= 1:
                continue
            s = self._score_chain(c, b)
            if s > 0:
                scored.append((s, i, key))
        flip = self._torpe(0.5)
        if flip:
            self.stats["torpeza"] += 1
            if scored:
                return {"type": "SELECT_CHAIN", "index": None}      # se le pasa el momento
            ok = [i for i, c in enumerate(selects) if not self._blocked(c.get("code")) and not self._act_mala(c, b)]
            if not ok:
                return {"type": "SELECT_CHAIN", "index": None}
            i = self.rng.choice(ok)
            self.used[("chain", selects[i].get("code"), selects[i].get("location"), selects[i].get("sequence"), selects[i].get("description", 0))] += 1
            self.last_activated = selects[i].get("code", 0)
            return {"type": "SELECT_CHAIN", "index": i}             # la usa a destiempo
        if not scored:
            return {"type": "SELECT_CHAIN", "index": None}
        s, i, key = max(scored)
        self.used[key] += 1
        self.last_activated = selects[i].get("code", 0)
        self.stats["encadena"] += 1
        return {"type": "SELECT_CHAIN", "index": i}

    def _attack_threat(self, b: Board) -> tuple[bool, int]:
        """¿Hay un ataque del rival en curso que me haga daño? (amenaza, ATK del atacante)."""
        if not self.attack:
            return False, 0
        card, target = self.attack
        if not card or card.get("controller") == self.me:
            return False, 0
        a = b.mon_at(card.get("controller"), card.get("sequence", -1))
        A = b.mon_atk(a) if a else 0
        if not target:
            return A > 0, A
        m = b.mon_at(target.get("controller"), target.get("sequence", -1))
        if not m:
            return True, A
        if in_attack(m.get("position")):
            return A >= b.mon_atk(m), A
        return A > b.mon_def(m) or not face_up(m.get("position")), A

    def _score_chain(self, c, b: Board) -> int:
        ci = self.db.get(c.get("code"))
        if ci is None:
            return 0
        tags = ci.tags
        p = self.p
        # No responder a mi propia cadena (salvo efectos de monstruo obligatorios, que salen como forced)
        if self.chain and self.chain[-1][0] == self.me:
            return 0
        if self._jugada_mala(ci, b):
            return 0
        threat, A = self._attack_threat(b)
        opp_link = self.chain[-1] if self.chain and self.chain[-1][0] != self.me else None
        loc = c.get("location", 0)

        if "negate" in tags:
            if "pay_half" in tags and b.lp[self.me] < 2000:
                return 0
            if opp_link:
                oc = self.db.get(opp_link[1])
                if oc and oc.tags & HARMFUL_TAGS and not oc.is_monster:
                    big = oc.tags & {"nuke_opp", "nuke_all", "storm_all", "storm_opp", "control"}
                    if "pay_half" in tags and not big and b.lp[self.me] < 6000:
                        return 0
                    return 150
                return 0 if p.guarda_trampas else 40
            if self.opp_summon:
                sc = self.db.get(self.opp_summon[0])
                need = 2400 if "pay_half" in tags else 1900          # pagar la mitad de LP solo por algo gordo
                if "pay_half" in tags and b.lp[self.me] < 3000:
                    return 0
                if sc and (sc.atk >= max(need, b.my_best + 1) or not p.guarda_trampas):
                    return 120
            return 0
        if "on_attack" in tags:
            if self.attack and self.attack[0] and self.attack[0].get("controller") != self.me:
                if threat or not p.guarda_trampas:
                    n_atk = sum(1 for _, m in b.op_m if in_attack(m.get("position")) and face_up(m.get("position")))
                    return 140 + 10 * n_atk
            return 0
        if "on_summon" in tags:
            if self.opp_summon:
                sc = self.db.get(self.opp_summon[0])
                strong = sc and (sc.atk >= max(1500, b.my_best) or sc.atk >= 2000)
                if "nuke_all" in tags:
                    return 130 if b.side_value(1) >= b.side_value(0) + (0 if not p.guarda_trampas else 500) else 0
                if strong or not p.guarda_trampas:
                    return 130
            return 0
        if ci.is_monster:
            return 50
        # El rival va a destruir/devolver mis M/T (Heavy Storm, MST...): lo que esté colocado, mejor usarlo ya
        if opp_link and loc == LOC_SZONE:
            oc = self.db.get(opp_link[1])
            if oc and oc.tags & {"storm_all", "storm_opp", "destroy_st", "destroy_any"}:
                if "destroy_st" in tags and b.op_st:
                    return 140
                if not tags & {"on_attack", "on_summon", "negate"}:
                    base = self._card_use_score(ci, c, b, chain=True)
                    if base > 0:
                        return max(base, 80)
        if "revive" in tags and ci.is_trap:
            gb = b.grave_best()
            good = b.my_free_m and gb >= max(1500, b.my_best)
            when = (not p.guarda_trampas) or (not b.my_turn and b.phase == PH_END) or threat or (b.my_turn and b.phase in (PH_M1, PH_STANDBY))
            return 110 if good and when else 0
        if "weaken" in tags and threat:
            return 100
        if tags & {"stall"} and threat:
            return 100
        if threat and (ci.type & TYPE_QUICKPLAY) and ("token" in ci.desc.lower()):
            return 90                                       # Scapegoat cuando le atacan
        base = self._card_use_score(ci, c, b, chain=True)
        if base <= 0:
            # trampa sin etiqueta clara (Waboku, Negate Attack...): el momento bueno es cuando le atacan
            if threat and ci.is_trap and not tags & {"boost"}:
                return 60
            return 0
        if p.guarda_trampas and ci.is_trap and base < 100:
            # trampas genéricas: al final del turno del rival o cuando le atacan
            return base if (threat or (not b.my_turn and b.phase == PH_END)) else 0
        if b.my_turn and ci.type & TYPE_QUICKPLAY and loc == LOC_HAND and base < 100:
            return 0
        return base

    # ======================= sí/no, opciones =======================
    def _select_effectyn(self, sel, b: Board):
        yes = self._cap(("yn", sel.get("code"), sel.get("description", 0)), 6)
        return {"type": "SELECT_EFFECTYN", "yes": yes}

    def _select_yesno(self, sel, b: Board):
        yes = self._cap(("yesno", sel.get("description", 0)), 6)
        return {"type": "SELECT_YESNO", "yes": yes}

    def _select_option(self, sel, b: Board):
        texts = [(t or "").lower() for t in sel.get("options_text", [])]
        opts = sel.get("options", [])
        n = len(opts) or len(texts) or 1
        prefer = ("destroy", "draw", "inflict", "special summon", "add", "gain")
        for kw in prefer:
            for i, t in enumerate(texts):
                if kw in t and i < n:
                    return {"type": "SELECT_OPTION", "index": i}
        return {"type": "SELECT_OPTION", "index": 0}

    # ======================= cartas =======================
    def _card_value(self, s: dict, b: Board) -> int:
        code, loc = s.get("code", 0), s.get("location", 0)
        if loc == LOC_MZONE:
            m = b.mon_at(s.get("controller"), s.get("sequence", -1))
            if m:
                return b.mon_value(m)
        ci = self.db.get(code)
        if ci is None:
            return 1000 if loc != LOC_MZONE else b.fd_guess
        if ci.is_monster:
            v = max(ci.atk, int(ci.deff * 0.8), 300) + (150 if ci.type & TYPE_EFFECT else 0)
            if loc == LOC_HAND and ci.tributes() > len(b.my_m):
                v -= 600                                    # en la mano y sin tributos: estorba
            return v
        t = ci.tags
        if t & {"nuke_opp", "nuke_all", "storm_opp", "control"}:
            return 2800
        if t & {"draw", "revive", "destroy_monster", "destroy_any", "negate"}:
            return 2200
        if ci.is_trap:
            return 1500
        return 1100

    def _intent_bad(self, selects, b: Board) -> bool:
        """True si lo que se elige sale perjudicado (destruir, desterrar, tributar, descartar...)."""
        h = self.hint
        if h in HINTS_MALO:
            return True
        if h in HINTS_BUENO:
            return False
        if h == H_TOGRAVE:
            return any(s.get("location") in (LOC_MZONE, LOC_SZONE, LOC_HAND) for s in selects)
        ci = self.db.get(self.last_activated)
        if ci and (ci.tags & {"boost", "revive", "search"} or self._es_boost(ci)) and not ci.tags & {"destroy_monster", "control", "weaken"}:
            return False
        if ci and ci.tags & {"nuke_opp", "nuke_all", "destroy_monster", "destroy_st", "destroy_any", "control", "weaken", "bounce", "stall"}:
            return True
        # por defecto: si hay cartas del rival, que le perjudique a él
        return any(s.get("controller") != self.me for s in selects)

    def _rank(self, selects, b: Board, bad: bool):
        out = []
        for i, s in enumerate(selects):
            v = self._card_value(s, b)
            mine = s.get("controller") == self.me
            score = (-v if mine else v) if bad else (v if mine else -v)
            out.append((score, i))
        out.sort(reverse=True)
        return [i for _, i in out]

    def _select_card(self, sel, b: Board):
        selects = sel.get("selects", [])
        if not selects:
            return {"type": "SELECT_CARD", "indicies": None}
        lo, hi = sel.get("min", 1), sel.get("max", 1)
        n = max(1, lo)
        n = min(n, hi, len(selects))
        if self.hint == H_ATTACKTARGET and self.attack_plan and self.attack_plan[1] is not None:
            for i, s in enumerate(selects):
                if s.get("controller") != self.me and s.get("sequence") == self.attack_plan[1] and s.get("location") == LOC_MZONE:
                    order = [i] + [j for j in self._rank(selects, b, True) if j != i]
                    return {"type": "SELECT_CARD", "indicies": order[:n]}
        if self.hint == H_ATTACKTARGET:
            # sin plan (torpeza o el rival puso algo): el objetivo que mejor gane, o el más débil
            order = sorted(range(len(selects)), key=lambda i: self._card_value(selects[i], b))
            return {"type": "SELECT_CARD", "indicies": order[:n]}
        order = self._rank(selects, b, self._intent_bad(selects, b))
        return {"type": "SELECT_CARD", "indicies": order[:n]}

    def _select_tribute(self, sel, b: Board):
        selects = sel.get("selects", [])
        lo = sel.get("min", 1)
        order = self._rank(selects, b, bad=True)          # mis más débiles primero (y del rival, el mejor)
        out, total = [], 0
        for i in order:
            if total >= lo:
                break
            out.append(i)
            total += selects[i].get("release_param", 1) or 1
        return {"type": "SELECT_TRIBUTE", "indicies": out}

    def _select_unselect_card(self, sel, b: Board):
        sc = sel.get("select_cards", [])
        if sel.get("can_finish") or not sc:
            if sel.get("can_finish") or not sel.get("unselect_cards"):
                return {"type": "SELECT_UNSELECT_CARD", "index": None}
            return {"type": "SELECT_UNSELECT_CARD", "index": len(sc)}
        order = self._rank(sc, b, self._intent_bad(sc, b))
        return {"type": "SELECT_UNSELECT_CARD", "index": order[0]}

    def _select_sum(self, sel, b: Board):
        must = sel.get("selects_must", [])
        selects = sel.get("selects", [])
        target = sel.get("amount", 0)
        vals = [[v for v in (s.get("amount", 0) & 0xffff, s.get("amount", 0) >> 16) if v > 0] for s in selects]
        base = 0
        for s in must:
            base += s.get("amount", 0) & 0xffff
        # preferir gastar las cartas que menos valen
        order = sorted(range(len(selects)), key=lambda i: self._card_value(selects[i], b) if selects[i].get("controller") == self.me else -1)
        at_least = bool(sel.get("select_max"))
        path: list[int] = []

        def dfs(k, total):
            if total == target and (path or must):
                return True
            if at_least and total >= target and path:
                # sin carta sobrante (ocgcore: check_with_sum_greater_limit): quitando la que menos vale ya no llega
                low = min([vals[i][0] for i in path] + [s.get("amount", 0) & 0xffff for s in must])
                return total - low < target
            if k >= len(order) or (total > target and not at_least):
                return False
            i = order[k]
            for v in vals[i]:
                path.append(i)
                if dfs(k + 1, total + v):
                    return True
                path.pop()
            return dfs(k + 1, total)

        if not dfs(0, base):
            return None                                     # sin solución exacta: que decida bot.ts
        return {"type": "SELECT_SUM", "indicies": list(range(len(must))) + [i + len(must) for i in path]}

    def _select_counter(self, sel, b: Board):
        left = sel.get("count", 0)
        out = []
        for c in sel.get("cards", []):
            take = min(c.get("count", 0), left)
            left -= take
            out.append(take)
        return {"type": "SELECT_COUNTER", "counters": out}

    def _places(self, mask: int, count: int, rival_first: bool):
        out = []
        sides = (1, 0) if rival_first else (0, 1)
        for side in sides:
            for base, loc in ((0, LOC_MZONE), (8, LOC_SZONE)):
                for seq in (2, 1, 3, 0, 4, 5, 6, 7):      # centro primero, como un jugador
                    if len(out) >= count:
                        return out
                    if not mask & (1 << (side * 16 + base + seq)):
                        out.append({"player": self.me if side == 0 else 1 - self.me, "location": loc, "sequence": seq})
        return out

    def _select_place(self, sel, b: Board):
        return {"type": "SELECT_PLACE", "places": self._places(sel.get("field_mask", 0), sel.get("count", 1), False)}

    def _select_disfield(self, sel, b: Board):
        return {"type": "SELECT_DISFIELD", "places": self._places(sel.get("field_mask", 0), sel.get("count", 1), True)}

    def _select_position(self, sel, b: Board):
        mask = sel.get("positions", POS_FU_ATK)
        ci = self.db.get(sel.get("code"))
        atk, df = (ci.atk, ci.deff) if ci else (0, 0)
        want_atk = not b.op_m or atk >= b.op_threat or atk >= 2000 or (atk >= df and atk > b.op_threat - 300)
        if self._torpe(0.3):
            want_atk = not want_atk
        if want_atk:
            order = (POS_FU_ATK, POS_FD_ATK, POS_FU_DEF, POS_FD_DEF)
        elif ci and ci.is_flip and self.p.coloca_boca_abajo:
            order = (POS_FD_DEF, POS_FU_DEF, POS_FU_ATK, POS_FD_ATK)
        else:
            order = (POS_FU_DEF, POS_FD_DEF, POS_FU_ATK, POS_FD_ATK)
        pos = next((x for x in order if mask & x), POS_FU_ATK)
        return {"type": "SELECT_POSITION", "position": pos}

    def _sort_card(self, sel, b):
        return {"type": "SORT_CARD", "order": None}

    _sort_chain = _sort_card

    def _rock_paper_scissors(self, sel, b):
        return {"type": "ROCK_PAPER_SCISSORS", "value": self.rng.randint(1, 3)}

    def _seen_cards(self):
        return [self.db.get(c) for c, _ in self.seen_opp.most_common() if self.db.get(c)]

    def _announce_race(self, sel, b):
        avail, count = int(sel.get("available", 0)), sel.get("count", 1)
        cnt = collections.Counter(c.race for c in self._seen_cards() if c.is_monster)
        bits = [1 << k for k in range(32) if avail & (1 << k)]
        bits.sort(key=lambda x: -cnt.get(x, 0))
        return {"type": "ANNOUNCE_RACE", "races": bits[:count]}

    def _announce_attrib(self, sel, b):
        avail, count = int(sel.get("available", 0)), sel.get("count", 1)
        cnt = collections.Counter(c.attribute for c in self._seen_cards() if c.is_monster)
        bits = [1 << k for k in range(16) if avail & (1 << k)]
        bits.sort(key=lambda x: -cnt.get(x, 0))
        return {"type": "ANNOUNCE_ATTRIB", "attributes": bits[:count]}

    def _announce_number(self, sel, b):
        opts = [int(x) for x in sel.get("options", [])]
        if not opts:
            return {"type": "ANNOUNCE_NUMBER", "value": 0}
        if max(opts) <= 12:                                   # parece un nivel: el más común del rival
            lv = collections.Counter(c.level for c in self._seen_cards() if c.is_monster)
            for lvl, _ in lv.most_common():
                if lvl in opts:
                    return {"type": "ANNOUNCE_NUMBER", "value": opts.index(lvl)}
        if min(opts) >= 1000:                                 # pagar LP (Wall of Revealing Light...): lo justo
            need = b.op_threat
            ok = [o for o in opts if o >= need and o < b.lp[self.me] - 1000]
            pick = min(ok) if ok else min(opts)
            return {"type": "ANNOUNCE_NUMBER", "value": opts.index(pick)}
        return {"type": "ANNOUNCE_NUMBER", "value": 0}

    def _announce_card(self, sel, b):
        ops = [int(x) for x in sel.get("opcodes", [])]
        cands = [c for c in self._seen_cards()] + [c for c in self.db.cards.values()]
        for c in cands:
            if declarable(c, ops):
                return {"type": "ANNOUNCE_CARD", "card": c.code}
        return None


def declarable(c: Card, ops: list[int]) -> bool:
    """Evalúa los opcodes de ANNOUNCE_CARD (pila, como el core de EDOPro) sobre una carta."""
    if c.type & TYPE_TOKEN or c.alias:
        allow_alias = OP["ALLOW_ALIASES"] in ops
        allow_tok = OP["ALLOW_TOKENS"] in ops
        if (c.type & TYPE_TOKEN and not allow_tok) or (c.alias and not allow_alias):
            return False
    st: list[int] = []

    def pop():
        return st.pop() if st else 0

    for op in ops:
        name = OP_BY_VALUE.get(op)
        if name is None:
            st.append(op)
            continue
        if name in ("ADD", "SUB", "MUL", "DIV", "AND", "OR", "BAND", "BOR", "BXOR", "LSHIFT", "RSHIFT"):
            y, x = pop(), pop()
            st.append({"ADD": x + y, "SUB": x - y, "MUL": x * y, "DIV": (x // y) if y else 0, "AND": int(bool(x and y)),
                       "OR": int(bool(x or y)), "BAND": x & y, "BOR": x | y, "BXOR": x ^ y, "LSHIFT": x << y, "RSHIFT": x >> y}[name])
        elif name == "NEG":
            st.append(-pop())
        elif name == "NOT":
            st.append(int(not pop()))
        elif name == "BNOT":
            st.append(~pop())
        elif name == "ISCODE":
            v = pop()
            st.append(int(c.code == v or c.alias == v))
        elif name == "ISSETCARD":
            v = pop()
            sc = c.setcode
            hit = False
            while sc:
                part = sc & 0xffff
                if part and (part & 0xfff) == (v & 0xfff) and (part & v) == v:
                    hit = True
                sc >>= 16
            st.append(int(hit))
        elif name == "ISTYPE":
            st.append(int(bool(c.type & pop())))
        elif name == "ISRACE":
            st.append(int(bool(c.race & pop())))
        elif name == "ISATTRIBUTE":
            st.append(int(bool(c.attribute & pop())))
        elif name == "GETCODE":
            st.append(c.code)
        elif name == "GETTYPE":
            st.append(c.type)
        elif name == "GETRACE":
            st.append(c.race)
        elif name == "GETATTRIBUTE":
            st.append(c.attribute)
        elif name == "GETSETCARD":
            st.append(c.setcode)
        # ALLOW_ALIASES / ALLOW_TOKENS no tocan la pila
    return len(st) == 1 and st[0] != 0


# ======================================================================================
# Perfiles por NPC (npc_decks.json) -> npc_profiles.json
# ======================================================================================

# Personajes famosos (51-95): 3 recetas seguidas por personaje = posición en su cadena (LOCAL.md §0.4, run.py FAMOUS)
FAMOUS_FIRST = [51, 54, 57, 60, 63, 66, 69, 72, 75, 78, 81, 84, 87, 90, 93]
# Sesgo de carácter (por el personaje / el nombre de la NPC; no hay datos de Konami: es una elección nuestra)
CARACTER = {
    "Seto Kaiba": dict(agresividad=+0.2), "Joey Wheeler": dict(agresividad=+0.2, torpeza=+0.05),
    "Jaden Yuki": dict(agresividad=+0.15), "Hell Kaiser": dict(agresividad=+0.25), "Chazz Princeton": dict(agresividad=+0.1),
    "Yami Marik": dict(agresividad=+0.1), "Yami Bakura": dict(guarda_trampas=True), "Tea Gardner": dict(agresividad=-0.15),
    "Duelista timido": dict(agresividad=-0.3), "Chico ardiente": dict(agresividad=+0.25), "Estratega": dict(agresividad=-0.25),
    "Maton": dict(agresividad=+0.25), "Pandillera": dict(agresividad=+0.2), "Chica nerviosa": dict(torpeza=+0.05),
    "Perezoso": dict(torpeza=+0.08), "Mentor": dict(agresividad=+0.3), "Introvertido": dict(agresividad=-0.2),
}
STAPLES = {"Raigeki", "Dark Hole", "Mirror Force", "Pot of Greed", "Graceful Charity", "Change of Heart", "Monster Reborn",
           "Heavy Storm", "Harpie's Feather Duster", "Snatch Steal", "Torrential Tribute", "Ring of Destruction",
           "Call of the Haunted", "Premature Burial", "Mystical Space Typhoon", "Brain Control", "Sakuretsu Armor",
           "Bottomless Trap Hole", "Solemn Judgment", "Lightning Vortex", "Smashing Ground", "Fissure", "Limiter Removal",
           "Cyber Dragon", "Jinzo", "Breaker the Magical Warrior", "Sangan", "Exodia the Forbidden One", "Black Luster Soldier - Envoy of the Beginning"}


def deck_power(passcodes: list[int], db: CardDB) -> float:
    """Fuerza aproximada de un mazo: ATK medio de sus monstruos (sin fusión) + cartas «de las buenas»."""
    mons = [db.get(c) for c in passcodes if db.get(c) and db.get(c).is_monster and not db.get(c).is_extra]
    avg = sum(m.atk for m in mons) / len(mons) if mons else 0
    staples = sum(1 for c in passcodes if db.get(c) and db.get(c).name in STAPLES)
    return avg / 100 + staples * 1.5


def npc_profiles(decks_path: Path = HERE / "npc_decks.json", db: CardDB | None = None) -> dict:
    """Perfil por defecto de cada NPC:
    - famosos (51-95): nivel 4/6/8 según su receta 1.ª/2.ª/3.ª de la cadena;
    - estándar (1-50) y Ghouls (96-98): nivel 1..5 según la fuerza del mazo (cuantiles) — no hay datos de su cadena;
    - sin receta: nivel 2 (mazo genérico).
    Después, un sesgo de carácter (CARACTER) por nombre."""
    db = db or CardDB.shared()
    decks = json.loads(Path(decks_path).read_text(encoding="utf-8"))
    std = []
    for k, v in decks.items():
        n = int(k)
        if (n <= 50 or n >= 96) and v.get("main_passcodes"):
            std.append((deck_power(v["main_passcodes"], db), n))
    std.sort()
    std_level = {n: 1 + min(4, i * 5 // max(1, len(std))) for i, (_, n) in enumerate(std)}
    out = {}
    for k, v in sorted(decks.items(), key=lambda kv: int(kv[0])):
        n = int(k)
        name = v.get("nombre", f"NPC {n}")
        if 51 <= n <= 95:
            pos = (n - 51) % 3
            nivel, motivo = (4, 6, 8)[pos], f"famoso, receta {pos + 1}/3 de su cadena"
        elif v.get("main_passcodes"):
            nivel, motivo = std_level.get(n, 2), "estándar, por la fuerza del mazo"
        else:
            nivel, motivo = 2, "sin receta en el cliente (mazo genérico)"
        prof = NpcProfile.por_nivel(nivel)
        for attr, delta in CARACTER.get(name, {}).items():
            cur = getattr(prof, attr)
            if isinstance(cur, bool) or isinstance(delta, bool):
                setattr(prof, attr, delta)
            elif isinstance(cur, float):
                setattr(prof, attr, round(min(1.0, max(0.0, cur + delta)), 3))
        prof.nombre = f"npc{n}"
        out[str(n)] = {"npc": n, "nombre": name, "nivel": nivel, "motivo": motivo,
                       "tiene_mazo": bool(v.get("main_passcodes")),
                       "fuerza_mazo": round(deck_power(v.get("main_passcodes") or [], db), 1),
                       "perfil": asdict(prof)}
    return out


def load_npc_profile(npc: int, path: Path = HERE / "npc_profiles.json") -> NpcProfile:
    if not Path(path).is_file():        # sin la tabla (árbol público: sale de npc_decks.json, que es del cliente): genérico
        return NpcProfile.por_nivel(2)
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    e = d.get(str(npc))
    return NpcProfile.from_dict(e["perfil"]) if e else NpcProfile.por_nivel(2)


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if len(sys.argv) > 1 and sys.argv[1] == "perfiles":
        profs = npc_profiles()
        dest = HERE / "npc_profiles.json"
        dest.write_text(json.dumps(profs, ensure_ascii=False, indent=1), encoding="utf-8")
        for k, e in profs.items():
            p = e["perfil"]
            print(f"{k:>3} {e['nombre'][:18]:18} nivel {e['nivel']} fuerza {e['fuerza_mazo']:5} torpeza {p['torpeza']:.2f} "
                  f"agres. {p['agresividad']:.2f} umbral {p['umbral_ataque']:5}  ({e['motivo']})")
        print(f"-> {dest}")
    else:
        db = CardDB.shared()
        print(f"{len(db.cards)} cartas")
        for n in ("Raigeki", "Mirror Force", "Trap Hole", "Fissure", "Dian Keto the Cure Master", "Monster Reborn",
                  "Axe of Despair", "Heavy Storm", "Mystical Space Typhoon", "Swords of Revealing Light", "Call of the Haunted",
                  "Solemn Judgment", "Torrential Tribute", "Sakuretsu Armor", "Snatch Steal", "Lightning Vortex", "Hinotama"):
            c = db.get(db.code(n))
            print(f"{n:28} {sorted(c.tags)}")
