"""Duelo contra NPC con las reglas oficiales de 2008 (YO2 Ver.080912). Notas: research/npcs_web/NOTES.md,
sección «Duelo contra NPC: reglas aplicadas».

Todo va tras banderas de run.py (NPC_RULES_2008, todas apagadas por defecto). Este módulo no toca ficheros: se
enchufa en duelfin (install) y run.py le pregunta (chain_fields, npc_list, deck_npc, h_selectpack).

    python research/npcreglas/proposal.py      # autoprueba

Banderas (claves de NPC_RULES_2008):
  dp           cada duelo contra NPC cuesta 1 DP (también rendirse/desconectarse).          C (regla) / C (cliente)
  no_record    el duelo NO cambia LV, puntuación ni el récord del jugador.                   C (regla)
  card         1 carta al final: 0x4800 con 1 sobre -> 0x4802 -> 0x4803 con 1 carta.          C (regla) / P (vía)
  friendship   amistad: cadena de 3 niveles de los famosos en NPCINFOLIST (+0x34 umbral,
               +0x40 previa), estrellas, premios intermedio/completo, 0x4237 y títulos.      C (cliente) / P (cifras)
  famous_decks si el mapa solo enseña el nivel 1, la NPC juega con la receta de su nivel.    P
  npc_window   0x43a0 último u8 = 1: ventana de resultados de NPC (CWindowResultNpc, amistad
               y V/D/E contra ella). El cliente lo trata como match: se cierra tras 1 partida. C (cliente) / D (oficial)
"""
import copy, json, os, random, struct, threading

HERE = os.path.dirname(os.path.abspath(__file__))
EMU = os.path.normpath(os.path.join(HERE, "..", ".."))
NPC_DECKS = os.path.join(EMU, "research", "ia", "npc_decks.json")

FLAG_KEYS = ("dp", "no_record", "card", "friendship", "famous_decks", "npc_window")
DEFAULT_FLAGS = {k: False for k in FLAG_KEYS}

# ---------------------------------------------------------------- coste y carta
DP_COST = 1                     # OF-CPU / guía 2007-09-07: «cuesta 1 DP».  C. El cliente exige DPP >= 1 en el mapa
                                # (+0x9c == 3) y >= 3 en el resto (FUN_00863ec0: 3 - 2*(+0x9c == 3)); nunca resta.  C
CARD_PACKS = tuple(range(1, 11))  # sobres que se ofrecen tras el duelo (D: el oficial no se sabe; = 0x4901 de lobby)
CARD_DUE_MAX = 5                # sobres de NPC sin abrir que se recuerdan por perfil

# ---------------------------------------------------------------- amistad
# b3 de NPCBIN = progreso con esa NPC (C). El cliente lo pone a 1 en la 1.ª charla (ITEMGET tipo 4) y el servidor
# suma por duelo. Nivel completo cuando b3 >= umbral (+0x34): la NPC se va y sale el siguiente nivel en el mismo
# punto (FUN_009ff2d0); estrellas = profundidad en la cadena + 1 si completa; medidor b3*5/umbral+1 (FUN_009d7bf0). C
# Cifras de premios (Yugipedia, patrón YO2): por nivel, carta a las 5 victorias y receta a las 8 (Jaden y Alexis 9);
# originales (DuelCity): carta (+ pieza) a las 5 y receta a las 9.  C (fuentes) / P (que sean victorias del nivel)
WIN_PTS, LOSS_PTS, DRAW_PTS = 1, 0, 0   # YO2 también sube al perder/empatar, cuánto NO se sabe (D): 0 por defecto
MID_WINS = 5
COMP_WINS_FAMOUS = 8
COMP_WINS_ORIGINAL = 9
COMP_WINS = {60: 9, 61: 9, 62: 9, 66: 9, 67: 9, 68: 9}   # Jaden y Alexis
COUNT_ALL_DUELS = set()         # dancer y raMon dan el 1.er premio a los 5 DUELOS: ids sin cruzar (D)
FAMOUS_BASES = tuple(range(51, 87, 3))   # 51 Yugi … 84 Aster (los 12 del mapa de 2008)
FAN_WINS = 200                  # «X FAN» (título 071…) a las 200 victorias contra el personaje.  C (YP)
DEFEAT_LOSSES = 20              # título burlón (056…) al perder 20 veces.  C (YP)
PRESENT = 1                     # 0x4237: 1 -> diálogo 8 (ganaste) / 10 (no) -> ITEMGET(3,0).  C (lectura) / P

# Carta del premio intermedio de cada nivel (YP: 5 / 13 / 21 victorias = niveles 1 / 2 / 3). Ids YO2 de
# cards_yo2.csv; con varias impresiones se usa la de id más bajo (D: cuál era la «arte alt.»).
MID_CARDS = {
    51: [365], 52: [716], 53: [358],              # Yugi: Swift Gaia / Buster Blader / Valkyrion
    54: [807], 55: [2055], 56: [1304],            # Kaiba: Vorse Raider (arte alt.) / King Dragun / Vampire Lord
    57: [1729], 58: [2409], 59: [1802],           # Téa: Guardian Angel Joan / Voltanis / Sanctuary in the Sky
    60: [2155], 61: [2215], 62: [2366],           # Jaden: Dark Catapulter / Shining Flare Wingman / Neos
    63: [2232], 64: [1908], 65: [1927],           # Chazz: VWXYZ Catapult Cannon / Armed Dragon LV7 / Ojama King
    66: [2230], 67: [2233], 68: [2663],           # Alexis: Etoile Cyber / Cyber Blader / Advanced Ritual Art
    69: [1082], 70: [428], 71: [610],             # Joey: Red-Eyes B. Dragon / Jinzo / Graceful Dice
    72: [2733], 73: [2739], 74: [2738],           # Jesse: Crystal Beacon / Rainbow Ruins / Sapphire Pegasus
    78: [2160], 79: [2505], 80: [2432],           # Kaiser: Cyber End / Cyberdark Dragon / Chimeratech Overdragon
    81: [1016], 82: [1360], 83: [1627],           # Bakura: Mask of Darkness / Puppet Master / Dark Master - Zorc
    84: [2368], 85: [2373], 86: [2448],           # Aster: Phoenix Enforcer / Diamond Dude / Dogma
    # Marik (75-77): sin página en Yugipedia -> solo el bit (D)
    17: [2397], 43: [2117], 26: [1623],           # originales con id seguro: Robert, Abigail, NiaNia (DuelCity)
}

_CTX = threading.local()


def flags(cfg=None):
    f = dict(DEFAULT_FLAGS)
    f.update({k: bool(v) for k, v in (cfg or {}).items() if k in DEFAULT_FLAGS})
    return f


def chain_base(nid):
    nid = int(nid)
    for b in FAMOUS_BASES:
        if b <= nid < b + 3:
            return b
    return None


def comp_wins(nid):
    nid = int(nid)
    if nid in COMP_WINS:
        return COMP_WINS[nid]
    return COMP_WINS_FAMOUS if chain_base(nid) is not None else COMP_WINS_ORIGINAL


def threshold(nid):
    """Umbral +0x34 de NPCINFOLIST = b3 con el nivel completo (b3 empieza en 1 con la 1.ª charla)."""
    return 1 + comp_wins(nid)


def mid_b3(nid):
    return 1 + MID_WINS


def chain_fields(nid, sent_ids):
    """(umbral, previa) del registro de NPCINFOLIST. La previa solo si va en la misma lista (si no, el cliente lee
    un puntero nulo: research/persistencia chain_fields)."""
    nid = int(nid)
    base = chain_base(nid)
    prev = nid - 1 if base is not None and nid > base and (nid - 1) in sent_ids else 0
    return threshold(nid), prev


def npc_list(npcs):
    """Añade los niveles 2 y 3 de cada famoso detrás del nivel 1, en el MISMO punto. npcs = [(id, nombre, punto,
    piezas)] de run.py."""
    out = list(npcs)
    have = {n[0] for n in out}
    for n in npcs:
        if n[0] in FAMOUS_BASES:
            for lvl in (1, 2):
                if n[0] + lvl not in have:
                    out.append((n[0] + lvl, n[1], n[2], n[3]))
    return out


def new_b3(old, result, nid):
    pts = WIN_PTS if result == "win" else LOSS_PTS if result == "lose" else DRAW_PTS
    if int(nid) in COUNT_ALL_DUELS:
        pts = max(pts, 1)
    return min(max(int(old), 1) + pts, 255)


def totals(PS, p, nid):
    """(victorias, derrotas) contra el personaje: suma de sus 3 niveles (como CWindowResultNpc, FUN_008c3c30)."""
    base = chain_base(nid)
    ids = [base, base + 1, base + 2] if base is not None else [int(nid)]
    es = [PS.npcbin_get(p, i) for i in ids]
    return sum(e[0] for e in es), sum(e[1] for e in es)


def rewards(PS, p, nid, old_b3, b3, result):
    """Bits y cartas que tocan al pasar b3 de old_b3 a b3 (más títulos por totales). Solo lo que aún no tiene."""
    nid = int(nid)
    pb = PS.npc_prize_bits().get(nid, {})
    bits, cards = [], []
    if old_b3 < mid_b3(nid) <= b3:
        if "mid_got" in pb:
            bits.append(pb["mid_got"])
        cards += MID_CARDS.get(nid, [])
    base = chain_base(nid)
    name = PS.FAMOUS_BASE.get(base) if base is not None else None
    if old_b3 < threshold(nid) <= b3:
        if "comp_got" in pb:
            bits.append(pb["comp_got"])            # receta 100NN (sin las cartas: OF-CPU)
        if name and nid == base + 2:              # nivel 3 completo: título de amistad + accesorio del personaje
            if name in PS.TITLE_BITS:
                bits.append(PS.TITLE_BITS[name][0])
            boy, girl = PS.PRESENT_CLOTHES.get(name, (None, None))
            b = girl if PS.player_sex(p) == "F" else boy
            if b is not None:
                bits.append(b)
    if name and name in PS.TITLE_BITS:
        w, l_ = totals(PS, p, nid)
        if result == "win" and w >= FAN_WINS:
            bits.append(PS.TITLE_BITS[name][2])
        if result == "lose" and l_ >= DEFEAT_LOSSES:
            bits.append(PS.TITLE_BITS[name][1])
    out = []
    for b in bits:
        if b not in out and not PS.pbin_test(p, b):
            out.append(b)
    return out, cards


def apply_friendship(PS, p, nid, result, old_entry):
    """b3 con la regla de arriba y premio como regalo de la NPC (0x4237 + ITEMGET 3). Devuelve el paquete o None."""
    e = PS.npcbin_get(p, nid)
    old = int(old_entry[3])
    e[3] = new_b3(old, result, nid)
    PS.npcbin_set(p, nid, e)
    bits, cards = rewards(PS, p, nid, max(old, 1), e[3], result)
    pkg = PS.pending_add(p, bits, cards, PS.VIA_NPC, "NPC %d b3 %d->%d" % (nid, old, e[3])) if bits or cards else None
    d = p.setdefault("last_npc_end", {})
    d["present"] = PRESENT if pkg else 0
    return pkg


def deck_npc(nid, npcbin_get):
    """Id cuyo mazo-receta juega la NPC: el nivel en curso del famoso (si el mapa enseña el nivel 1). Sin receta en
    npc_decks.json -> el id tal cual."""
    nid = int(nid)
    base = chain_base(nid)
    if base is None or nid != base:
        return nid
    lvl = 0
    while lvl < 2 and int(npcbin_get(base + lvl)[3]) >= threshold(base + lvl):
        lvl += 1
    try:
        d = json.load(open(NPC_DECKS, encoding="utf-8")).get(str(base + lvl)) or {}
    except OSError:
        d = {}
    return base + lvl if d.get("main") else nid


# ---------------------------------------------------------------- enchufe en duelfin
def install(DFM, get_cfg, log=print):
    """Envuelve DFM.npc_duel_end y DFM.lobby_after_duel_msgs (los busca finish/h_disconnect en tiempo de llamada)
    y neutraliza, solo dentro del fin de duelo de NPC y con su bandera, las reglas propuestas de persistencia
    (npc_rewards con umbral 4; títulos por métricas cuando el récord no cambia). Idempotente."""
    PS = DFM.PS
    if getattr(DFM, "_npcreglas", None):
        DFM._npcreglas["get_cfg"] = get_cfg
        return DFM._npcreglas
    st = {"get_cfg": get_cfg, "orig_end": DFM.npc_duel_end, "orig_lob": DFM.lobby_after_duel_msgs,
          "orig_rewards": PS.npc_rewards, "orig_titles": PS.check_titles, "orig_rights": PS.rights_due}

    def cfg():
        try:
            return flags(st["get_cfg"]())
        except Exception:                          # noqa: BLE001
            return dict(DEFAULT_FLAGS)

    def npc_rewards(p, nid, old_b3, new_b3_):
        if getattr(_CTX, "on", None) and _CTX.on.get("friendship"):
            return [], []
        return st["orig_rewards"](p, nid, old_b3, new_b3_)

    def check_titles(p, surrender=None, turns=0):
        if getattr(_CTX, "on", None) and _CTX.on.get("no_record"):
            return []
        return st["orig_titles"](p, surrender=surrender, turns=turns)

    def rights_due(p):
        if getattr(_CTX, "on", None) and _CTX.on.get("no_record"):
            return []
        return st["orig_rights"](p)

    def npc_duel_end(p, npc, result, turns=0, lp_left=0):
        f = cfg()
        if not (f["dp"] or f["no_record"] or f["friendship"]):
            return st["orig_end"](p, npc, result, turns=turns, lp_left=lp_left)
        npc = int(npc or 0)
        valid = 0 < npc < PS.NPCBIN_MAX
        rec_before = copy.deepcopy(p.get("rec", {}))
        e_before = PS.npcbin_get(p, npc) if valid else [0, 0, 0, 0]
        _CTX.on = f
        try:
            st["orig_end"](p, npc, result, turns=turns, lp_left=lp_left)
        finally:
            _CTX.on = None
        if f["no_record"]:
            p["rec"] = rec_before                  # ni LV, ni puntuación, ni victorias/derrotas, ni estadísticas
        if f["friendship"] and valid:
            pkg = apply_friendship(PS, p, npc, result, e_before)
            if pkg:
                log(f"[npcreglas] NPC {npc}: regalo {pkg['bits']} cartas {pkg['cards']}")
        if f["dp"]:                                # coste único con el PvP (persistencia §13; YO2_DP_COSTE=0 apaga)
            cost = getattr(PS, "DUEL_DP_COST", DP_COST)
            if hasattr(PS, "dp_charge"):
                PS.dp_charge(p, cost)
            else:
                r = p.setdefault("rec", {})
                r["dpp"] = max(0, int(r.get("dpp", PS.START_DPP)) - cost)
        return PS.player_data_update(p, npc=npc if valid else None)

    def lobby_after_duel_msgs(p, npc, result, *, turns=0, lp_left=0, present=None, packs=()):
        f = cfg()
        if f["card"] and not packs:
            pack = random.choice(CARD_PACKS)
            due = p.setdefault("npc_card_due", [])
            due.append(pack)
            del due[:-CARD_DUE_MAX]
            packs = (pack,)
        out = st["orig_lob"](p, npc, result, turns=turns, lp_left=lp_left, present=present, packs=packs)
        if f["friendship"] and present is None:
            pr = PS.take_present(p)
            if pr:
                at = next((i for i, (m, _) in enumerate(out) if m == DFM.M_CARDPACKLIST), len(out))
                out.insert(at, (DFM.M_NPCPRESENT, DFM.npc_present_type(pr)))
        return out

    DFM.npc_duel_end = npc_duel_end
    DFM.lobby_after_duel_msgs = lobby_after_duel_msgs
    PS.npc_rewards, PS.check_titles, PS.rights_due = npc_rewards, check_titles, rights_due
    DFM._npcreglas = st
    return st


def h_selectpack(c, data, orig, take_due, cards_of, send_cards):
    """0x4802 tras un duelo de NPC (bandera card): si el perfil tiene un sobre de NPC pendiente, 0x4803 con UNA
    carta de ese sobre. take_due(pack) -> True si había uno (y lo quita, guardando). Si no, orig()."""
    pack = struct.unpack(">H", data[:2])[0] if len(data) >= 2 else 0
    if not take_due(pack):
        return orig()
    send_cards(c, cards_of(pack)[:1])
    return True


def take_due_in(p, pack):
    due = p.get("npc_card_due") or []
    if not due:
        return False
    if pack in due:
        due.remove(pack)
    else:
        due.pop(0)
    return True


# ---------------------------------------------------------------- autoprueba
def selftest():
    import importlib.util
    ok = True

    def check(name, got, want):
        nonlocal ok
        good = got == want
        ok &= good
        print(("OK  " if good else "MAL ") + "%s: %s (esperado %s)" % (name, got, want))

    spec = importlib.util.spec_from_file_location("duelfin_npcreglas_test",
                                                  os.path.join(EMU, "research", "duelfin", "proposal.py"))
    DFM = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(DFM)
    PS = DFM.PS
    cfgd = dict(DEFAULT_FLAGS)
    install(DFM, lambda: cfgd, log=lambda *_: None)
    install(DFM, lambda: cfgd, log=lambda *_: None)          # idempotente

    # 1) banderas apagadas = lo de antes
    p0 = PS.blank_profile("t"); p1 = PS.blank_profile("t")
    a = DFM.lobby_after_duel_msgs(p0, 51, DFM.RESULT_WIN)
    b = st_orig_lob(DFM)(p1, 51, DFM.RESULT_WIN)
    check("apagado: mismos mensajes", a == b, True)
    check("apagado: récord sube como antes", p0["rec"]["wins"], p1["rec"]["wins"])

    # 2) DP y récord
    cfgd.update(dp=True, no_record=True)
    p = PS.blank_profile("t"); rec0 = dict(p["rec"])
    pdu = DFM.lobby_after_duel_msgs(p, 51, DFM.RESULT_LOSE)[0][1]
    check("dp: -1", p["rec"]["dpp"], rec0["dpp"] - 1)
    check("no_record: victorias/derrotas/duelos iguales", [p["rec"][k] for k in ("wins", "losses", "duels", "score")],
          [rec0[k] for k in ("wins", "losses", "duels", "score")])
    check("no_record: NPCBIN sí cuenta la derrota (b1)", PS.npcbin_get(p, 51)[1], 1)
    check("PDU lleva el DPP nuevo", PS.decode(PS.PDU_SPEC, pdu)["dpp"], rec0["dpp"] - 1)
    p["rec"]["dpp"] = 0
    DFM.npc_duel_end(p, 51, "win")
    check("dp: no baja de 0", p["rec"]["dpp"], 0)

    # 3) amistad: Yugi nivel 1 -> carta a las 5, receta a las 8, sale el nivel 2
    cfgd.update(dp=False, no_record=False, friendship=True)
    p = PS.blank_profile("t")
    PS.npcbin_set(p, 51, [0, 0, 0, 1])                       # 1.ª charla (ITEMGET 4)
    pb = PS.npc_prize_bits()
    got = []
    for i in range(8):
        msgs = DFM.lobby_after_duel_msgs(p, 51, DFM.RESULT_WIN)
        got.append([m for m, _ in msgs])
        if i == 4:
            check("5.ª victoria: bit intermedio + Swift Gaia", (PS.pbin_test(p, pb[51]["mid_got"]),
                                                                 p["trunk"].get("365")), (True, 1))
            check("5.ª victoria: 0x4237 antes de 0x4800", got[-1], [0x4140, 0x4237, 0x4800])
    check("victorias 1-4 sin regalo (ni el de umbral 4 de persistencia)", got[:4], [[0x4140, 0x4800]] * 4)
    check("8.ª victoria: receta 1 (comp_got)", PS.pbin_test(p, pb[51]["comp_got"]), True)
    check("b3 = 1 + 8 = umbral", PS.npcbin_get(p, 51)[3], threshold(51))
    recs = [(n, 204, *chain_fields(n, {51, 52, 53})) for n in (51, 52, 53)]
    nb = {i: PS.npcbin_get(p, i) for i in (51, 52, 53)}
    check("mapa: se va el 51 y sale el 52", PS.visible(recs, nb), [52])
    check("estrellas del 52 (1 + medidor)", PS.stars(recs, nb, 52)[0], 1)
    check("famous_decks: nivel 2 -> receta 10052", deck_npc(51, lambda i: PS.npcbin_get(p, i)), 52)
    check("derrota con amistad: b3 igual (LOSS_PTS 0)", (DFM.npc_duel_end(p, 52, "lose") and
                                                       PS.npcbin_get(p, 52)[3]), 1)
    # nivel 3 completo: título de amistad + accesorio
    PS.npcbin_set(p, 53, [0, 0, 0, threshold(53) - 1])
    DFM.lobby_after_duel_msgs(p, 53, DFM.RESULT_WIN)
    check("nivel 3: título 160 y ropa 121", (PS.pbin_test(p, 160), PS.pbin_test(p, 121)), (True, True))
    check("nivel 3: 3 estrellas", PS.stars(recs, {**nb, 53: PS.npcbin_get(p, 53)}, 53)[0], 3)

    # 4) carta por duelo
    cfgd.update(friendship=False, card=True)
    p = PS.blank_profile("t")
    msgs = DFM.lobby_after_duel_msgs(p, 17, DFM.RESULT_LOSE)
    pk = struct.unpack(">H", msgs[-1][1])[0]
    check("0x4800 con 1 sobre", (msgs[-1][0], len(msgs[-1][1]), pk in CARD_PACKS), (0x4800, 2, True))
    sent = []
    r = h_selectpack(None, struct.pack(">H", pk), lambda: "orig", lambda k: take_due_in(p, k),
                     lambda k: [11, 12, 13, 14, 15], lambda c, cs: sent.append(cs))
    check("0x4802 -> 1 carta", (r, sent), (True, [[11]]))
    r = h_selectpack(None, struct.pack(">H", pk), lambda: "orig", lambda k: take_due_in(p, k),
                     lambda k: [11], lambda c, cs: sent.append(cs))
    check("sin sobre de NPC pendiente -> manejador de siempre", r, "orig")

    # 5) lista de NPC
    base = [(51, "Yami Yugi", 204, bytes(16)), (17, "Robert", 37, b"x" * 16)]
    lst = npc_list(base)
    check("npc_list: niveles 2 y 3 en el mismo punto", [(n[0], n[2]) for n in lst],
          [(51, 204), (17, 37), (52, 204), (53, 204)])
    ids = {n[0] for n in lst}
    check("chain_fields 53 / 51 / 17", [chain_fields(53, ids), chain_fields(51, ids), chain_fields(17, ids)],
          [(9, 52), (9, 0), (10, 0)])
    check("chain_fields sin la previa en la lista -> 0", chain_fields(52, {51 + 1}), (9, 0))
    check("Jaden y Alexis: 9 victorias", (threshold(60), threshold(68)), (10, 10))
    print("TODO OK" if ok else "HAY FALLOS")
    return ok


def st_orig_lob(DFM):
    return DFM._npcreglas["orig_lob"]


if __name__ == "__main__":
    import sys
    sys.exit(0 if selftest() else 1)
