"""Mundo y progreso 2008 (agente «mundo», 2026-10-06): NPC originales por id (A) y títulos (B).

Notas y fuentes: research/npcs_web/NOTES.md §12 (A) y research/lobby/NOTES.md «Mundo 2008» (B-D).
Confianza: C = cliente + fuente de la época · P = probable · D = dudoso.

A) Cruce id del cliente <-> NPC original de la web (C): `yo2pbinmap.dat` da a cada NPCID_0NN su receta «コンプ景品
   (レシピ：…)» = cardpack/100NN.ydc; el nombre japonés de esa receta es el del deck que DuelCity lista para cada NPC, y
   la carta del premio de 5 victorias (DuelCity) está en ese .ydc en los 30 casos. Dos con receta renombrada:
   frogMaster = 21 («FLOGGER EVOLUTION»; Des Frog + diálogos «Croak», C) y AoJ = 32 («じっくりバーン»; Dimension
   Wall + amiga de Alexis, P). Zona = la de la captura DuelCity del 1-2-2008 (la última que hay; las originales
   rotaban casi cada semana, así que para el cliente 080912 la zona es P). «Retirada» en esa captura = no se manda.

Todo va tras MUNDO_2008 (dict de run.py, todo False = comportamiento de antes):
  spots       A: NPCINFOLIST con las originales de DC08 (nombre real, su zona) en vez de los ids 1-32 de relleno.
  prizes      A: carta de las 5 victorias de cada original (DC), dancer/raMon cuentan duelos, Miss Scout 8.
  challenge   A: 挑戦権 de Kaiba (592) / Alexis (593) al ganar 5 veces a una de sus originales (Marik: como antes).
  pvp_titles  B: títulos por logros (TITLE_RULES de persistencia, umbrales D) también al acabar un duelo PvP.
  hair_titles B: «EL KAISER AFRO» (152) con la Afro Lv4 y «EL REY DEL MECHÓN» (153) con la リーゼント Lv3. P
  title_owned B: 0x4174 con un título que el perfil no tiene -> 0x4175 = 1 (el cliente solo ofrece los suyos). P
Uso desde run.py: MW = load_research("mundo"); MW.install(globals()) — idempotente; vuelve a leer las banderas en
cada llamada (cambiar MUNDO_2008[...] y llamar MW.install(globals()); NPCS = load_npcs() para «spots»).

GM (2026-10-07, notas en research/mundo/NOTES.md «GM y títulos especiales»). Bandera propia GM_ACCOUNTS (abajo,
encendida por defecto desde el 07-10; YO2_GM_ACCOUNTS=0 o pestaña Banderas del panel la apagan). Lista de cuentas GM: gm_cuentas.json junto a state.json
(la edita el panel). Lo único que el cliente hace con un GM (C): si su título equipado es GAME MASTER (id 2), antepone
«@1» a cada línea de chat que manda (FUN_00a77c70) y todos la ven en el estilo de texto 1 (azul, negrita, 18 px). Con
GM_ACCOUNTS encendida:
  - 0x4174 con KONAMI (bit 225) o GAME MASTER (bit 226) desde una cuenta que no es GM -> 0x4175 = 1 (no se equipa). D
  - una cuenta GM recibe GAME MASTER (bit 226) en la próxima entrada a un mapa («¡Tienes un nuevo Título!»). D
  - 0x4400 de una cuenta que no es GM: se quitan los códigos «@0»-«@9» (el cliente normal escribe «@» como «@@»,
    así que solo un cliente trucado o un título GM regalado antes podrían colorear). D
"""
import json
import os
import shutil
import struct
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
FLAG_KEYS = ("spots", "prizes", "challenge", "pvp_titles", "hair_titles", "title_owned")

# Áreas del exe (research/npcs_web/NOTES.md §10): 0 Clock Square, 1 Game Shop, 2 Pier, 3 Kaiba Corp. · 4 Duel
# Academy, 5 Store, 6 Duel Space, 7 Slifer Red. Puntos normales 1-64: 8 por área (tabla 0xd0f7a0), base por área:
AREA_BASE = {4: 1, 5: 9, 6: 17, 7: 25, 0: 33, 1: 41, 2: 49, 3: 57}
ZONE = {0: "Clock Square", 1: "Game Shop", 2: "Pier", 3: "Kaiba Corp.", 4: "Duel Academy", 5: "Store",
        6: "Duel Space", 7: "Slifer Red", None: "retirada"}

# nid: (nombre web, receta JP del cliente (100NN), área en DC08 o None = retirada, carta 5 vic. (id YO2), conf. id)
ORIGINALS = {
    3: ("bankara", "ファイブ・エンペラー", 2, 1915, "C"),            # Mobius the Frost Monarch
    1: ("BeeBee", "戦わずに勝つ", 3, 1810, "C"),                   # Wall of Revealing Light
    21: ("frogMaster", "FLOGGER EVOLUTION", 2, 2183, "C"),         # Des Frog (web: カエルの合唱)
    7: ("gouk", "魔法のない世界", 1, 1457, "C"),                    # Spell Canceller
    4: ("Joker", "天使か！悪魔か！？儀式デッキ", None, 1796, "C"),    # Archlord Zerato
    24: ("kanna", "ダイヤモンド・ドロー", 1, 2373, "C"),             # Destiny Hero - Diamond Dude
    6: ("KC Dragon", "ホルス-ドラゴン", None, 1901, "C"),           # Horus the Black Flame Dragon LV8
    5: ("kyoko", "暗黒界の脅威", 3, 2250, "C"),                     # Brron, Mad King of Dark World
    2: ("missbomb", "死に至る黒蛇病", 0, 1411, "C"),                # Dark Snake Syndrome
    9: ("Miss Scout", "黒蠍のお仕事", 2, 1320, "C"),                # Don Zaloog
    8: ("Miss Shadow", "破滅の厄災", 0, 2074, "C"),                 # Deck Devastation Virus
    14: ("Reia", "冥界の宴", 1, 2177, "C"),                        # Goblin Elite Attack Force
    17: ("Robert", "伝説の機体", 0, 2397, "C"),                    # Victory Viper XX03
    16: ("Sakura", "コイントスで運試し", 3, 1977, "C"),             # Sasuke Samurai #4
    20: ("ZuruZuru", "機械兵団", None, 736, "C"),                  # Widespread Ruin
    43: ("Abigail", "自然と共に…", 4, 2117, "C"),                  # Elemental Mistress Doriado
    35: ("A-lien", "宇宙からの侵略者", 5, 2483, "C"),               # Alien Mother
    32: ("AoJ", "じっくりバーン", 6, 2207, "P"),                    # Dimension Wall (web: アタッカー歓迎)
    40: ("Brow", "鉄壁デッキ", 4, 1184, "C"),                      # Stone Statue of the Aztecs
    27: ("dancer", "アルティメット忍者", 6, 2093, "C"),             # Ultimate Insect LV7
    38: ("Kain", "混沌スーパーキャノン！", 5, 1858, "C"),           # Spell Economics
    33: ("Kyuros", "お帰りあそばせ！", None, 1744, "C"),           # Compulsory Evacuation Device
    31: ("lalala", "墓守アヌビス", 6, 1880, "C"),                  # The End of Anubis
    29: ("Mika", "天空の裁き", None, 1771, "C"),                   # The Agent of Judgment - Saturn
    26: ("NiaNia", "チェス！デーモン", 4, 1623, "C"),               # Skull Archfiend of Lightning
    34: ("Queen Ryo", "三邪神の恐怖", 7, 1192, "C"),               # Brain Control
    28: ("Rain", "呪いのウィジャ", None, 1030, "C"),               # UFO Turtle
    44: ("raMon", "ドロー・ドロー・エクゾディア", 5, 1845, "C"),     # Emissary of the Afterlife
    49: ("sabasaba", "ノーガード戦法", 7, 1477, "C"),              # Wave-Motion Cannon
    30: ("Zuno", "恐怖のドロー", 7, 1946, "C"),                    # Heavy Slump
}
# Puntos ya usados por NORMAL_SPOTS_2008 (Abigail 2, NiaNia 4 en el área 4): se respetan; el resto, en orden.
FIXED_POINTS = {43: 2, 26: 4}
POINT_ORDER = (1, 3, 5, 7, 2, 4, 6, 8)
SEX = {8: "F", 29: "F"}            # Miss Shadow, Mika: npc_ids.json decía «?» (nombre de chica; P)
COUNT_ALL_DUELS = {27, 44}         # dancer y raMon: 1.er premio a los 5 DUELOS («plays», DC).  C
COMP_WINS = {9: 8}                 # Miss Scout: receta a las 8 (DC).  C
# 挑戦権 (bit PBIN tipo 7) -> originales que lo dan con 5 victorias (YP Kaiba/Alexis; DC «5 wins»).  C
CHALLENGE_BY = {592: (7, 6, 9), 593: (32, 33, 31)}
CHALLENGE_WINS = 5
HAIR_TITLES = {152: (13, 265), 153: (9,)}   # título -> bits de pieza (アフロLv4 chico/chica; リーゼントLv3 chico)  P
FREE_TITLE_BITS = {224}            # «Duelista principiante» (称号001): siempre elegible

# ---------------------------------------------------------------- GM (títulos del personal)
# 07-10: activado por defecto con el OK del owner (YO2_GM_ACCOUNTS=0 la apaga)
GM_ACCOUNTS = os.environ.get("YO2_GM_ACCOUNTS", "1") not in ("", "0")
GM_TITLE_BITS = {225: "KONAMI", 226: "GAME MASTER"}            # bit PBIN -> título (id 1 y 2 en la tabla 0xc98d50)  C
GM_GIVE_BIT = 226                                              # el que se da solo a las cuentas GM
GM_FILE = "gm_cuentas.json"
_GM_LOCK = threading.Lock()

_ST = {}


def gm_path(ns=None):
    ns = ns if ns is not None else (_ST.get("ns") or {})
    sp = ns.get("STATE_PATH") or os.path.join(HERE, "..", "..", "state.json")
    return os.path.join(os.path.dirname(os.path.abspath(sp)), GM_FILE)


def load_gm(ns=None):
    """{acct: {"nota": str, "desde": "AAAA-MM-DD HH:MM"}}; fichero ausente o roto = nadie es GM."""
    with _GM_LOCK:
        try:
            with open(gm_path(ns), encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}


def save_gm(d, ns=None):
    """Escribe la lista (UTF-8 sin BOM); antes deja la anterior en gm_cuentas.json.bak-gm."""
    with _GM_LOCK:
        p = gm_path(ns)
        if os.path.exists(p):
            shutil.copyfile(p, p + ".bak-gm")
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(d, ensure_ascii=False, indent=1))
        os.replace(tmp, p)


def is_gm(acct, ns=None):
    return bool(acct) and str(acct) in load_gm(ns)


def gm_on():
    return bool(GM_ACCOUNTS)


def _acct_of(sess=None):
    """Cuenta del mensaje en curso: la sesión de la conexión o CTX.sess de run.py. Sin sesión (modo de un solo
    jugador de antes) = la del dueño."""
    ns = _ST.get("ns") or {}
    s = sess
    if s is None and callable(ns.get("cur_sess")):
        s = ns["cur_sess"]()
    acct = getattr(s, "acct", None)
    return acct or ns.get("OWNER_ACCT")


def strip_color_codes(text):
    """Quita «@0»-«@9» (cambio de estilo de texto del cliente, FUN_00604410) y deja «@@» (una @ escrita a mano).
    Bytes tal cual: el cliente español es cp1252 (sin bytes de cabecera de doble byte)."""
    b, out, i = bytes(text), bytearray(), 0
    while i < len(b):
        if b[i] == 0x40 and i + 1 < len(b):
            n = b[i + 1]
            if n == 0x40:
                out += b"@@"; i += 2; continue
            if 0x30 <= n <= 0x39:
                i += 2; continue
        out.append(b[i]); i += 1
    return bytes(out)


def flags(cfg):
    cfg = cfg or {}
    return {k: bool(cfg.get(k)) for k in FLAG_KEYS}


def _cfg():
    ns = _ST.get("ns") or {}
    return flags(ns.get("MUNDO_2008"))


# ---------------------------------------------------------------- A: lista de NPC
def orig_entries(info=None, parts_of=None):
    """[(id, nombre, punto, piezas, sexo)] de las originales que DC08 da en una zona (retiradas fuera)."""
    info = info or {}
    used, out = {}, []
    for nid in sorted(ORIGINALS, key=lambda n: (ORIGINALS[n][2] is None, ORIGINALS[n][2] or 0, n)):
        name, _r, area, _c, _conf = ORIGINALS[nid]
        if area is None:
            continue
        taken = used.setdefault(area, set(FIXED_POINTS[n] for n in FIXED_POINTS
                                          if ORIGINALS.get(n, (0, 0, -1))[2] == area))
        k = FIXED_POINTS.get(nid) if nid in FIXED_POINTS else next(k for k in POINT_ORDER if k not in taken)
        taken.add(k)
        sex = SEX.get(nid) or (info.get(nid) or {}).get("sex")
        parts = parts_of(nid, sex) if parts_of else bytes(16)
        out.append((nid, name[:15], AREA_BASE[area] + k - 1, parts, sex))
    return out


def npc_list(famous, info, parts_of):
    """NPCINFOLIST con «spots»: los famosos tal cual (ya colocados por run.py) + las originales de DC08."""
    return list(famous) + [e[:4] for e in orig_entries(info, parts_of)]


# ---------------------------------------------------------------- A: premios (en npcreglas)
def _sync_prizes(on, NR=None):
    NR = NR if NR is not None else (_ST.get("ns") or {}).get("NR")      # NR: la recarga en caliente pasa el módulo nuevo
    if NR is None:
        return
    keep = _ST.setdefault("nr_orig", {"MID": dict(NR.MID_CARDS), "CNT": set(NR.COUNT_ALL_DUELS),
                                      "COMP": dict(NR.COMP_WINS)})
    NR.MID_CARDS.clear(); NR.MID_CARDS.update(keep["MID"])
    NR.COUNT_ALL_DUELS.clear(); NR.COUNT_ALL_DUELS.update(keep["CNT"])
    NR.COMP_WINS.clear(); NR.COMP_WINS.update(keep["COMP"])
    if on:
        for nid, (_n, _r, _a, card, _c) in ORIGINALS.items():
            NR.MID_CARDS.setdefault(nid, [card])
        NR.COUNT_ALL_DUELS.update(COUNT_ALL_DUELS)
        NR.COMP_WINS.update(COMP_WINS)


# ---------------------------------------------------------------- A/B: lo que se da al entrar en un mapa
def challenge_due(PS, p):
    """挑戦権 ganados con las originales (sin el bit y con >= 5 victorias, NPCBIN b0, a una de las tres)."""
    out = []
    for bit, nids in sorted(CHALLENGE_BY.items()):
        if not PS.pbin_test(p, bit) and any(int(PS.npcbin_get(p, n)[0]) >= CHALLENGE_WINS for n in nids):
            out.append(bit)
    return out


def hair_titles_due(PS, p):
    return [t for t, pieces in sorted(HAIR_TITLES.items())
            if not PS.pbin_test(p, t) and any(PS.pbin_test(p, b) for b in pieces)]


def _wrap_pp(PP=None):
    """PP.challenge_rights_due la usa on_itemget(1, 0) en cada entrada a un mapa: ahí van también los títulos de
    pelo. Con las banderas apagadas, la original tal cual. PP: solo la recarga en caliente (yo2srv/recarga.py)."""
    PP = PP if PP is not None else (_ST.get("ns") or {}).get("PP")
    if PP is None or getattr(PP, "_mundo", None):
        return
    orig_due, orig_title = PP.challenge_rights_due, PP.on_title

    def challenge_rights_due(p):
        f = _cfg()
        base = orig_due(p)
        if f["challenge"]:
            base = [b for b in base if b not in CHALLENGE_BY]          # Kaiba/Alexis: solo ganándoselo
            base += [b for b in challenge_due(PP, p) if b not in base]
        if f["hair_titles"]:
            base += [b for b in hair_titles_due(PP, p) if b not in base]
        if gm_on() and is_gm(_acct_of()) and not PP.pbin_test(p, GM_GIVE_BIT) and GM_GIVE_BIT not in base:
            base.append(GM_GIVE_BIT)                                     # GM: GAME MASTER al entrar en un mapa
        return base

    def on_title(p, title):
        bit = int(title) & 0xffff
        if gm_on() and bit in GM_TITLE_BITS and not is_gm(_acct_of()):
            return struct.pack(">I", 1)                                  # título del personal: solo cuentas GM
        if _cfg()["title_owned"] and bit not in FREE_TITLE_BITS and not PP.pbin_test(p, bit):
            return struct.pack(">I", 1)
        return orig_title(p, title)

    PP.challenge_rights_due, PP.on_title = challenge_rights_due, on_title
    PP._mundo = {"due": orig_due, "title": orig_title}


# ---------------------------------------------------------------- B: títulos tras un PvP
def _wrap_pvp(pm=None):
    ns = _ST.get("ns") or {}
    pm = pm if pm is not None else ns.get("pvp_match")                  # pm: la recarga en caliente pasa el módulo nuevo
    if pm is None or getattr(pm, "_mundo", None):
        return
    orig = pm.pvp_record

    def pvp_record(PS, p, res, DFM, turns=0):
        r = orig(PS, p, res, DFM, turns)
        if _cfg()["pvp_titles"]:
            try:
                bits = PS.check_titles(p, turns=turns)
                if bits:                                   # se entregan en el próximo ITEMGET(1,0) (volver al mapa)
                    PS.pending_add(p, bits, [], PS.VIA_ANY, "títulos PvP")
                    (ns.get("log") or print)(f"[mundo] títulos por PvP pendientes: {bits}")
            except Exception as e:                          # noqa: BLE001
                (ns.get("log") or print)(f"[mundo] títulos PvP: {e!r}")
        return r

    pm.pvp_record = pvp_record
    pm._mundo = {"pvp_record": orig}


# ---------------------------------------------------------------- GM: chat
def _wrap_chat(LP=None):
    """LP.HANDLERS[0x4400] (lobby.h_chat): con GM_ACCOUNTS, el texto de una cuenta que no es GM pierde los «@n».
    Texto en data[14:] (u8 0, u8 tipo, u32, u32 destino, u32, cadena). Con la bandera apagada, el de siempre.
    LP: solo la recarga en caliente de LP (cablea el módulo nuevo antes de hacerlo visible)."""
    LP = LP if LP is not None else (_ST.get("ns") or {}).get("LP")
    hs = getattr(LP, "HANDLERS", None)
    if not isinstance(hs, dict) or 0x4400 not in hs or getattr(hs[0x4400], "_gm", False):
        return
    orig = hs[0x4400]

    def h_chat(c, msg_id, param, data, _orig=orig):
        if gm_on() and len(data) > 14 and not is_gm(_acct_of(getattr(c, "sess", None))):
            clean = strip_color_codes(data[14:])
            if clean != bytes(data[14:]):
                (_ST.get("ns", {}).get("log") or print)("[gm] chat de una cuenta no GM: códigos de color quitados")
                data = bytes(data[:14]) + clean
        return _orig(c, msg_id, param, data)

    h_chat._gm = True
    h_chat._orig = orig
    hs[0x4400] = h_chat


def install(ns):
    """Idempotente. ns = globals() de run.py (MUNDO_2008, NR, PP, pvp_match, LP, log)."""
    _ST["ns"] = ns
    _sync_prizes(_cfg()["prizes"])
    _wrap_pp()
    _wrap_pvp()
    _wrap_chat()
    return _cfg()


# ---------------------------------------------------------------- autoprueba
def selftest():
    import json
    import types
    bad = []

    def check(name, got, want):
        ok = got == want
        print(("OK   " if ok else "FALLO"), name, "" if ok else f"-> {got!r} (esperado {want!r})")
        if not ok:
            bad.append(name)

    emu = os.path.normpath(os.path.join(HERE, "..", ".."))
    decks = json.load(open(os.path.join(emu, "research", "ia", "npc_decks.json"), encoding="utf-8"))
    for nid, (name, receta, area, card, _c) in sorted(ORIGINALS.items()):
        d = decks[str(nid)]
        check(f"{name} = {nid}: receta del cliente", d["premios_pbin"]["receta"], receta)
        check(f"{name} = {nid}: carta de 5 vic. en su receta", card in d["main"] + d["side"] + d["extra"], True)
    e = orig_entries()
    check("24 originales en DC08 (6 retiradas fuera)", len(e), 24)
    check("sin dos en el mismo punto", len({x[2] for x in e}), len(e))
    check("cada una en su área", all(AREA_BASE[ORIGINALS[x[0]][2]] <= x[2] < AREA_BASE[ORIGINALS[x[0]][2]] + 8
                                     for x in e), True)
    check("Abigail y NiaNia en sus puntos de siempre", {x[0]: x[2] for x in e if x[0] in (43, 26)}, {43: 2, 26: 4})
    check("Robert en Clock Square (área 0)", 33 <= {x[0]: x[2] for x in e}[17] <= 40, True)

    # premios en un npcreglas falso
    NR = types.SimpleNamespace(MID_CARDS={17: [2397], 51: [365]}, COUNT_ALL_DUELS=set(), COMP_WINS={60: 9})
    ns = {"MUNDO_2008": {}, "NR": NR}
    install(ns)
    check("prizes apagado: npcreglas igual", (NR.MID_CARDS, NR.COUNT_ALL_DUELS, NR.COMP_WINS),
          ({17: [2397], 51: [365]}, set(), {60: 9}))
    ns["MUNDO_2008"]["prizes"] = True; install(ns)
    check("prizes: carta de gouk (7)", NR.MID_CARDS.get(7), [1457])
    check("prizes: dancer/raMon cuentan duelos", NR.COUNT_ALL_DUELS, {27, 44})
    check("prizes: Miss Scout 8", NR.COMP_WINS.get(9), 8)
    ns["MUNDO_2008"]["prizes"] = False; install(ns)
    check("prizes vuelto a apagar", (len(NR.MID_CARDS), NR.COUNT_ALL_DUELS, NR.COMP_WINS), (2, set(), {60: 9}))

    # 挑戦権 y títulos de pelo con un PP falso
    class PPF:
        def __init__(self):
            self.bits = set(); self.nb = {}
        def pbin_test(self, p, b): return b in self.bits
        def npcbin_get(self, p, n): return self.nb.get(n, [0, 0, 0, 0])
        def challenge_rights_due(self, p): return [b for b in (592, 593, 594) if b not in self.bits]
        def on_title(self, p, t): return struct.pack(">I", 0)
    pp = PPF()
    _ST.clear(); ns = {"MUNDO_2008": {}, "PP": pp}; install(ns)
    check("challenge apagado: modo free de siempre", pp.challenge_rights_due({}), [592, 593, 594])
    ns["MUNDO_2008"]["challenge"] = True
    check("challenge: sin victorias solo Marik", pp.challenge_rights_due({}), [594])
    pp.nb[6] = [5, 0, 0, 6]
    check("challenge: 5 vic. a KC Dragon -> Kaiba", pp.challenge_rights_due({}), [594, 592])
    pp.nb[31] = [4, 0, 0, 5]
    check("challenge: 4 a lalala no basta", 593 in pp.challenge_rights_due({}), False)
    ns["MUNDO_2008"]["hair_titles"] = True; pp.bits.add(13)
    check("hair_titles: Afro Lv4 -> 152", 152 in pp.challenge_rights_due({}), True)
    check("title_owned apagado: cualquiera", pp.on_title({}, 230), struct.pack(">I", 0))
    ns["MUNDO_2008"]["title_owned"] = True
    check("title_owned: sin el bit -> 1", pp.on_title({}, 230), struct.pack(">I", 1))
    check("title_owned: 224 siempre", pp.on_title({}, 224), struct.pack(">I", 0))
    pp.bits.add(230)
    check("title_owned: con el bit -> 0", pp.on_title({}, 230), struct.pack(">I", 0))

    # títulos PvP con un pvp_match falso
    calls = []
    pm = types.SimpleNamespace(pvp_record=lambda PS, p, res, DFM, turns=0: calls.append("rec"))
    PS = types.SimpleNamespace(check_titles=lambda p, turns=0: [250], VIA_ANY="any",
                               pending_add=lambda p, b, c, via, why: calls.append(("add", b)))
    _ST.clear(); ns = {"MUNDO_2008": {}, "pvp_match": pm, "log": lambda *a: None}; install(ns)
    pm.pvp_record(PS, {}, 0, None)
    check("pvp_titles apagado: solo el récord", calls, ["rec"])
    ns["MUNDO_2008"]["pvp_titles"] = True; calls.clear()
    pm.pvp_record(PS, {}, 0, None)
    check("pvp_titles: récord + títulos pendientes", calls, ["rec", ("add", [250])])
    _ST.clear()
    bad += selftest_gm()
    print("TODO OK" if not bad else f"{len(bad)} FALLO(S): {bad}")
    return not bad


def selftest_gm():
    """GM_ACCOUNTS con un PP, un LP y un state.json de mentira (carpeta temporal). Devuelve la lista de fallos."""
    import tempfile
    import types
    global GM_ACCOUNTS
    bad = []

    def check(name, got, want):
        ok = got == want
        print(("OK   " if ok else "FALLO"), "gm:", name, "" if ok else f"-> {got!r} (esperado {want!r})")
        if not ok:
            bad.append("gm: " + name)

    class PPF:
        def __init__(self):
            self.bits = set()
        def pbin_test(self, p, b): return b in self.bits
        def challenge_rights_due(self, p): return []
        def on_title(self, p, t): return struct.pack(">I", 0)

    seen = []
    LP = types.SimpleNamespace(HANDLERS={0x4400: lambda c, m, prm, data: seen.append(bytes(data[14:])) or True})
    cur = {"s": None}
    old_flag = GM_ACCOUNTS
    with tempfile.TemporaryDirectory() as tmp:
        pp = PPF()
        ns = {"MUNDO_2008": {}, "PP": pp, "LP": LP, "STATE_PATH": os.path.join(tmp, "state.json"),
              "OWNER_ACCT": "dueno", "cur_sess": lambda: cur["s"], "log": lambda *a: None}
        _ST.clear(); install(ns); install(ns)
        check("chat envuelto una sola vez", getattr(LP.HANDLERS[0x4400], "_orig", None) is not None
              and not getattr(LP.HANDLERS[0x4400]._orig, "_gm", False), True)
        jugador, gm = types.SimpleNamespace(acct="jugador"), types.SimpleNamespace(acct="elgm")
        head = bytes([0, 0]) + bytes(12)
        GM_ACCOUNTS = False
        cur["s"] = jugador
        check("apagada: GAME MASTER se equipa como siempre", pp.on_title({}, 226), struct.pack(">I", 0))
        LP.HANDLERS[0x4400](types.SimpleNamespace(sess=jugador), 0x4400, 0, head + b"@1hola\0")
        check("apagada: el chat pasa tal cual", seen[-1], b"@1hola\0")
        check("apagada: no se regala nada", pp.challenge_rights_due({}), [])
        GM_ACCOUNTS = True
        check("sin fichero nadie es GM", is_gm("elgm"), False)
        save_gm({"elgm": {"nota": "prueba", "desde": "2026-10-07 12:00"}})
        save_gm(load_gm())
        check("fichero UTF-8 sin BOM + copia .bak-gm", (open(gm_path(), "rb").read(3) != b"\xef\xbb\xbf",
                                                         os.path.exists(gm_path() + ".bak-gm")), (True, True))
        check("jugador: KONAMI rechazado", pp.on_title({}, 225), struct.pack(">I", 1))
        check("jugador: GAME MASTER rechazado", pp.on_title({}, 226), struct.pack(">I", 1))
        check("jugador: JUMP FESTA no es del personal", pp.on_title({}, 227), struct.pack(">I", 0))
        check("jugador: no recibe GAME MASTER", pp.challenge_rights_due({}), [])
        LP.HANDLERS[0x4400](types.SimpleNamespace(sess=jugador), 0x4400, 0, head + b"@1hola @@ x@9\0")
        check("jugador: chat sin «@n», «@@» intacto", seen[-1], b"hola @@ x\0")
        cur["s"] = gm
        check("GM: GAME MASTER se equipa", pp.on_title({}, 226), struct.pack(">I", 0))
        check("GM: KONAMI se equipa", pp.on_title({}, 225), struct.pack(">I", 0))
        check("GM: recibe GAME MASTER al entrar en un mapa", pp.challenge_rights_due({}), [226])
        pp.bits.add(226)
        check("GM: con el bit ya no se repite", pp.challenge_rights_due({}), [])
        LP.HANDLERS[0x4400](types.SimpleNamespace(sess=gm), 0x4400, 0, head + b"@1hola\0")
        check("GM: su «@1» llega a los demás", seen[-1], b"@1hola\0")
        cur["s"] = None
        check("sin sesión = dueño (no GM): rechazado", pp.on_title({}, 226), struct.pack(">I", 1))
        save_gm({"dueno": {}})
        check("sin sesión = dueño (GM): vale", pp.on_title({}, 226), struct.pack(">I", 0))
    GM_ACCOUNTS = old_flag
    _ST.clear()
    return bad


if __name__ == "__main__":
    import sys
    sys.exit(0 if selftest() else 1)
