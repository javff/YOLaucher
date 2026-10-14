"""Comprobación del mazo del duelo (08-10, encargo del owner «podemos hacer 1 y 2»; NOTES.md «Comprobación del mazo»).

El cliente monta el mazo él solo y lo manda al empezar cada duelo (0x6600 cabecera, 0x6601 principal, 0x6603 extra;
el side nunca viaja). Hasta hoy el servidor se lo creía: con un .ydc editado o un cliente trucado se podía duelar con
cartas que no tienes. Aquí se mira ese mazo contra el baúl del personaje y las reglas del editor de mazos de 2008.

Bandera YO2_DECK_CHECK = off | avisar | bloquear (por defecto «avisar»; con YO2_PVP_TEST y sin la variable, «off»:
los clientes falsos de las pruebas llevan mazos de NPC con el baúl vacío). En caliente: deck_check("bloquear") en
la consola de run.py.
  - off: como antes (ni historial, ni aviso).
  - avisar: historial en el perfil, línea «[mazo]» en el log y evento «mazo_invalido» en Actividad; el duelo sigue.
  - bloquear: lo mismo y el duelo no empieza (salida limpia: 0x649b código 1 en el piedra-papel-tijera; ver NOTES).
    NUESTRO (D: no se sabe qué hacía el servidor oficial), NO es el valor por defecto.

Reglas (código = «regla» de cada problema; mismos códigos que research/admin/eventos.py MAZO_REGLA):
  baul   copias usadas (principal + extra) de un id > copias de ese id en p["trunk"] del personaje.  C (el editor no
         deja pasar de lo que hay en el baúl: FUN_00907660/FUN_00907820, «copias en principal+side+extra < baúl»)
  existe el id no está en research/ia/cards_yo2.csv (las 3243 cartas del cliente de 2008).  C
  sitio  fusión (tipo de card_prop 2/3 = csv kind 5/7) en el principal, o una que no lo es en el extra.  C (FUN_008df280
         solo deja soltar en el extra los tipos 2/3 y nunca en el principal; el autoarmado FUN_00813xxx igual)
  tamano principal 40-80, extra <= 30.  C (FUN_00904e50 antes de DUEL y el guardado del editor: 0x27 < n < 0x51,
         side < 0x10, extra < 0x1f; texto 6051 «Tu Deck debe tener entre 40 y 80 Cartas»). OJO: NO es 40-60 / 15 (la
         regla del papel de 2008): el cliente deja 80 y 30.
  lista  copias de una carta de la lista de prohibidas/limitadas (columna limit 0/1/2 de cards_yo2.csv = lo que manda
         0x5600 LIMIT_LIST) por encima de su límite. Se cuenta por id KONAMI (las versiones de una misma carta suman):
         C (FUN_0073c190 -> FUN_0073c290 busca por id Konami). Solo con la lista en vigor (LP.LIMIT_LIST encendida;
         si no, el cliente no la tiene y vale 3). limit 65535 (dioses y fichas: «no va en mazos», no se manda en
         0x5600) cuenta como máximo 0: P (el cliente les daría 3; no hay forma legítima de tenerlas en el baúl).
  copias más de 3 copias (id Konami) de una carta que no está en la lista.  C (FUN_0073c290 devuelve 3 por defecto)
  Sin modelar (D): FUN_0073c190 da 3 a todo si el personaje tiene el bit 0x800 de +0x1214 (¿cuenta especial?).

Historial (lo lee el panel, research/admin/secciones.py _ficha_mazos): p["decks"]["historial"] = como mucho HIST_MAX
entradas {hash, main, extra, primera, ultima (epoch), duelos, valido, problemas}, la más reciente al final,
deduplicado por la huella de principal + extra ordenados. «last_duel» no cambia (lo sigue escribiendo persistencia).
"""
import collections
import csv
import hashlib
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
EMU = os.path.normpath(os.path.join(HERE, "..", ".."))
CSV_PATH = os.path.join(EMU, "research", "ia", "cards_yo2.csv")

MODES = ("off", "avisar", "bloquear")
MAIN_MIN, MAIN_MAX = 40, 80      # C: FUN_00904e50 / editor (0x27 < n < 0x51), texto 6051
EXTRA_MAX = 30                   # C: FUN_00904e50 / editor (extra < 0x1f); FUN_00907820 (< 0x1e para añadir)
COPIES_MAX = 3                   # C: FUN_0073c290 (por defecto 3)
NO_DECK = 65535                  # limit de cards_yo2.csv para dioses y fichas: no van en mazos (P: máximo 0)
HIST_MAX = 30
SVR_CUT = 1                      # 0x649b código 1: «%s se ha desconectado» (4519) y el Janken vuelve al mapa (C)
JANKEN_MSGS = (0x6101, 0x6102, 0x6103, 0x6104, 0x61fe)   # lo que se ignora tras cortar un duelo bloqueado


def initial_mode(env=None):
    """Modo al arrancar: YO2_DECK_CHECK (off/avisar/bloquear; 0 = off, 1 = avisar); sin ella, «avisar», salvo en la
    instancia de prueba del PvP (YO2_PVP_TEST), que lleva clientes falsos sin baúl: «off»."""
    env = os.environ if env is None else env
    v = (env.get("YO2_DECK_CHECK") or "").strip().lower()
    if not v:
        return "off" if env.get("YO2_PVP_TEST", "") not in ("", "0") else "avisar"
    v = {"0": "off", "1": "avisar", "on": "avisar"}.get(v, v)
    return v if v in MODES else "avisar"


# ---------------------------------------------------------------- cartas de 2008
_CARDS = {}
_CARDS_LOCK = threading.Lock()


def cards():
    """{id YO2: {"k": id Konami, "n": nombre, "kind": csv kind, "lim": límite}} de cards_yo2.csv (una vez)."""
    if not _CARDS:
        with _CARDS_LOCK:
            if not _CARDS:
                out = {}
                with open(CSV_PATH, encoding="utf-8") as f:
                    for r in csv.DictReader(f):
                        try:
                            cid = int(r["yo2_id"])
                        except (TypeError, ValueError):
                            continue
                        out[cid] = {"k": int(r.get("konami_id") or 0) or None,
                                    "n": (r.get("name_es") or r.get("name_en") or "").strip(),
                                    "kind": int(r.get("kind") or 0), "lim": int(r.get("limit") or COPIES_MAX)}
                _CARDS.update(out)
    return _CARDS


def is_fusion(kind):
    """Tipo de card_prop (FUN_0073c010 = (dword+4 >> 1) & 0x1f) 2 o 3 = fusión / fusión de efecto (csv kind 5/7). C"""
    return ((int(kind) >> 1) & 0x1F) in (2, 3)


def deck_hash(main, extra):
    """Huella del mazo: principal y extra ORDENADOS (el orden en que llegan no importa)."""
    s = ",".join(map(str, sorted(main))) + "|" + ",".join(map(str, sorted(extra)))
    return hashlib.sha1(s.encode()).hexdigest()[:16]


def check(main, extra, trunk, limit_list=True, db=None):
    """Problemas del mazo: [{regla, id, nombre, usa, tiene, max, texto?, zona?}] (vacía = vale). trunk = p["trunk"]
    ({str(id): copias}). limit_list = la lista de prohibidas/limitadas está en vigor (LP.LIMIT_LIST)."""
    db = cards() if db is None else db
    main, extra = [int(x) for x in main], [int(x) for x in extra]
    out = []
    nom = lambda cid: (db.get(cid) or {}).get("n") or ""   # noqa: E731
    # d) tamaños
    if not MAIN_MIN <= len(main) <= MAIN_MAX:
        out.append({"regla": "tamano", "id": None, "nombre": "", "usa": len(main), "tiene": None,
                    "max": MAIN_MAX, "min": MAIN_MIN, "zona": "principal",
                    "texto": f"principal de {len(main)} cartas ("
                             + (f"mínimo {MAIN_MIN})" if len(main) < MAIN_MIN else f"máximo {MAIN_MAX})")})
    if len(extra) > EXTRA_MAX:
        out.append({"regla": "tamano", "id": None, "nombre": "", "usa": len(extra), "tiene": None, "max": EXTRA_MAX,
                    "zona": "extra", "texto": f"extra de {len(extra)} cartas (máximo {EXTRA_MAX})"})
    use = collections.Counter(main + extra)
    # b) existe en el juego de 2008
    for cid in sorted(use):
        if cid not in db:
            out.append({"regla": "existe", "id": cid, "nombre": "", "usa": use[cid], "tiene": None, "max": 0})
    # c) sitio
    for zona, ids, want in (("principal", main, False), ("extra", extra, True)):
        bad = collections.Counter(c for c in ids if c in db and is_fusion(db[c]["kind"]) != want)
        for cid in sorted(bad):
            out.append({"regla": "sitio", "id": cid, "nombre": nom(cid), "usa": bad[cid], "tiene": None, "max": 0,
                        "zona": zona, "texto": ("fusión en el Deck principal" if zona == "principal"
                                                else "no es una fusión y está en el Deck extra")})
    # a) baúl
    trunk = trunk or {}
    for cid in sorted(use):
        try:
            have = int(trunk.get(str(cid), 0) or 0)
        except (TypeError, ValueError):
            have = 0
        if use[cid] > have:
            out.append({"regla": "baul", "id": cid, "nombre": nom(cid), "usa": use[cid], "tiene": have, "max": have})
    # e) lista y copias, por id Konami
    byk = collections.defaultdict(list)
    for cid in use:
        if cid in db and db[cid]["k"]:
            byk[db[cid]["k"]].append(cid)
    for k in sorted(byk):
        ids = sorted(byk[k])
        n = sum(use[c] for c in ids)
        lim = db[ids[0]]["lim"]
        if lim == NO_DECK:
            if n > 0:
                out.append({"regla": "lista", "id": ids[0], "nombre": nom(ids[0]), "usa": n, "tiene": None, "max": 0,
                            "texto": "no va en mazos (dios o ficha)"})
        elif limit_list and lim in (0, 1, 2):
            if n > lim:
                out.append({"regla": "lista", "id": ids[0], "nombre": nom(ids[0]), "usa": n, "tiene": None,
                            "max": lim})
        elif n > COPIES_MAX:
            out.append({"regla": "copias", "id": ids[0], "nombre": nom(ids[0]), "usa": n, "tiene": None,
                        "max": COPIES_MAX})
    return out


def history_add(p, main, extra, problems, t=None):
    """Apunta el mazo en p["decks"]["historial"] (dedup por huella; el más reciente al final; HIST_MAX). Devuelve la
    entrada. Modifica p (guardar fuera)."""
    t = round(time.time() if t is None else t)
    hist = p.setdefault("decks", {}).setdefault("historial", [])
    if not isinstance(hist, list):
        hist = p["decks"]["historial"] = []
    h = deck_hash(main, extra)
    old = next((e for e in hist if isinstance(e, dict) and e.get("hash") == h), None)
    if old is not None:
        hist.remove(old)
    e = old or {"hash": h, "main": list(main), "extra": list(extra), "primera": t, "duelos": 0}
    e.update(main=list(main), extra=list(extra), ultima=t, duelos=int(e.get("duelos") or 0) + 1,
             valido=not problems, problemas=[{k: v for k, v in pr.items()} for pr in problems])
    hist.append(e)
    del hist[:-HIST_MAX]
    return e


def _short(problems, n=4):
    def one(p):
        who = f"#{p['id']} {p.get('nombre') or ''}".strip() if p.get("id") else (p.get("zona") or "")
        if p.get("texto"):
            return f"{p['regla']} {who}: {p['texto']}"
        return f"{p['regla']} {who}: usa {p.get('usa')}" + (f", tiene {p['tiene']}" if p.get("tiene") is not None
                                                             else f", máx. {p.get('max')}")
    s = "; ".join(one(p) for p in problems[:n])
    return s + (f"; y {len(problems) - n} más" if len(problems) > n else "")


class DeckCheck:
    """Lo usa run.py (DK). env = globals() VIVO de run.py (load_state, save_state, STATE_LOCK, PP, CTX, LP, log...)."""

    def __init__(self, env):
        self.env = env
        self.mode = initial_mode()
        self.stats = collections.Counter()

    def log(self, *a):
        (self.env.get("log") or print)(*a)

    def set_mode(self, m):
        m = {"0": "off", "1": "avisar", "on": "avisar"}.get(str(m).strip().lower(), str(m).strip().lower())
        if m not in MODES:
            raise ValueError(f"YO2_DECK_CHECK: {m!r} no vale (off/avisar/bloquear)")
        self.mode = m
        return m

    def _limit_list_on(self):
        lp = self.env.get("LP")
        return bool(getattr(lp, "LIMIT_LIST", True)) if lp is not None else True

    def review(self, s, main, extra, partida=None, mode=None):
        """Mazo completo del personaje de la sesión s (pvp_hub.Session). Apunta el historial en SU perfil (cuenta + hueco
        de la sesión, no el «activo» del hilo), y si no cuadra: línea en el log y evento. Devuelve None (modo off o
        sin sesión) o {"valido", "problemas", "bloquear", "modo"}. Nunca lanza."""
        mode = mode or self.mode
        if mode == "off" or s is None or not getattr(s, "acct", None):
            return None
        try:
            PP, ctx = self.env.get("PP"), self.env.get("CTX")
            lk = self.env.get("STATE_LOCK") or threading.RLock()
            old = getattr(ctx, "sess", None) if ctx is not None else None
            if ctx is not None:
                ctx.sess = s                             # load_state/save_state = la cuenta de s
            try:
                with lk:
                    st = self.env["load_state"]()
                    slot = s.slot if getattr(s, "slot", None) is not None else PP.active_slot(st)
                    p = PP.profile(st, int(slot) & 3, create=False)
                    if p is None:
                        self.log(f"[mazo] {s!r}: sin perfil en el hueco {slot}: no se comprueba")
                        return None
                    probs = check(main, extra, p.get("trunk") or {}, self._limit_list_on())
                    history_add(p, main, extra, probs)
                    self.env["save_state"](st)
            finally:
                if ctx is not None:
                    ctx.sess = old
        except Exception as e:                           # noqa: BLE001  (la comprobación nunca tumba el duelo)
            self.log(f"[EXC] [mazo] {s!r}: {e!r}")
            return None
        block = bool(probs) and mode == "bloquear"
        self.stats["mazos"] += 1
        if probs:
            self.stats["invalidos"] += 1
            self.stats["bloqueados"] += int(block)
            self.log(f"[mazo] {getattr(s, 'name', '') or '?'} (cuenta {str(s.acct)[:6]}, hueco {getattr(s, 'slot', '?')})"
                     f"{f' {partida}' if partida else ''}: {len(probs)} problema{'' if len(probs) == 1 else 's'} "
                     f"({'BLOQUEADO' if block else mode}): {_short(probs)}")
            self._event(s, probs, mode, partida)
        return {"valido": not probs, "problemas": probs, "bloquear": block, "modo": mode}

    @staticmethod
    def _event(s, probs, mode, partida):
        """Actividad del panel (research/admin/eventos.py mazo_invalido). Sin el módulo (pruebas), nada."""
        try:
            m = sys.modules.get("yo2_eventos")
            f = getattr(m, "mazo_invalido", None) if m is not None else None
            if callable(f):
                f(s, probs, mode, partida=partida)
        except Exception:                                # noqa: BLE001
            pass

    # ------------------------------------------------------------ NPC (run.py duel_reply, envoltorio del duelo)
    def npc_deck(self, c, s):
        """0x6603 de un duelo contra NPC: s = OcgSession (s.player = el mazo). Marca c.deck_block si toca bloquear."""
        r = self.review(getattr(c, "sess", None), s.player["main"], s.player["extra"], partida=f"npc:{s.id}")
        if r and r["bloquear"]:
            c.deck_block = {"t": time.time(), "told": False}
        return r

    def npc_gate(self, c, msg_id, data):
        """Duelo contra NPC con el mazo bloqueado. True = mensaje atendido aquí (no sigue). En el 0x6100 (el cliente ya
        está en la escena del piedra-papel-tijera, tras 0x600c) se contesta su eco y 0x649b código 1: el Janken
        (FUN_007c76b0) enseña el aviso, vuelve al mapa y manda 0x0003 (C, research/duelfin NOTES §5). El duelo no
        cuenta: la partida de duelfin se suelta (como 0x0003) y la sesión ocg queda «acabada» (un cierre del socket
        no es derrota)."""
        b = getattr(c, "deck_block", None)
        if not b:
            return False
        if msg_id == 0x6100 and not b["told"]:
            b["told"] = True
            LS = self.env.get("LS")
            svr = LS.duelsvrinfomsg(SVR_CUT, 0) if LS is not None and hasattr(LS, "duelsvrinfomsg") else bytes([SVR_CUT, 0])
            c.send(0x6100, b"")
            c.send(0x649b, svr)
            self._npc_void(c)
            self.log(f"[mazo] duelo contra NPC de {getattr(getattr(c, 'sess', None), 'name', '?')} NO empieza: "
                     f"0x649b código {SVR_CUT} (el cliente vuelve al mapa)")
            return True
        if msg_id == 0x6100 or msg_id in JANKEN_MSGS:
            return True
        return False

    def _npc_void(self, c):
        try:
            s = getattr(c, "ocg", None)
            if s is not None:
                s.over = True                            # on_conn_closed: «el duelo ya acabó», sin derrota
            df_for = self.env.get("df_for")
            df = df_for(c) if callable(df_for) else None
            if df is not None:
                df["sess"]["end"] = None                 # como h_0003: la partida de duelfin no cuenta
        except Exception as e:                           # noqa: BLE001
            self.log(f"[EXC] [mazo] anular el duelo de NPC: {e!r}")

    def snapshot(self):
        return {"modo": self.mode, **dict(self.stats)}
