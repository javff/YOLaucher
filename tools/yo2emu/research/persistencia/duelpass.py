"""Códigos DUELPASS de un solo uso (0x8101 REQDPAUTH -> 0x8102 DPAUTHRESULT) y cifras de inicio de un personaje nuevo.

Lo usan run.py (al arrancar) o research/admin/hot_duelpass_juego.py (en caliente) para el juego, y el panel
(research/admin/sec_duelpass.py) para crear, listar y anular códigos y para editar las cifras de inicio. Va en
sys.modules["yo2_duelpass"]: el panel y el juego comparten el mismo módulo (y su cerrojo).

Cómo era 1:1 (detalle y pruebas en NOTES.md §«Códigos DUELPASS»):
- El jugador pulsa «Puntos de DUELPASS» con el saldo de 0x3111 < 1 y sale la ventana «Por favor ingresa tu Código
  de DUELPASS» (texto 4055). El botón (FUN_00a86470 -> FUN_006ce5c0) manda 0x8101 = el texto tecleado en 16 B
  (FUN_0068aaf0(buf, texto, 0x10)). C
- 0x8102 (FUN_006c01b0) = s32 resultado, u16 carta, u8 (sin uso), u8 DPP, u16 mazo. La ventana (0xa85758) solo mira
  si el s32 es 0: distinto de 0 -> «Código de DUELPASS incorrecto» (4056, en 0xa85af6). El texto 4057 («Este código
  ya ha sido utilizado») está en yo2text pero este exe no lo usa. Con 0, ventana de puntos (4052). C
- Con resultado 0 el cliente, en local: carta +1 al baúl (si id <= 0xcb7); mazo = un pack de packinfo (FUN_006c3610):
  sus cartas ×1 y el bit «GET済み» = bit del pack + 1 (pack 8999: especial, aquí no se usa); DPP += u8; MP del
  HUECO 0 += 10 si el u8 es 15, 20 o 30; y los derechos BeginnersDeck 0x302/0x304/0x306 que falten (se canjean en el
  siguiente inicio con 0x5400). Con error suma 1 a +0x544 y a partir de 11 fallos en la sesión ni siquiera manda
  0x8101 (pone -2000 él solo). C
- Qué valor exacto mandaba el oficial en cada error: D (el cliente no los distingue). Aquí: 1 no existe, 2 ya
  canjeado, 3 anulado. Que el código no distinga mayúsculas: P (inofensivo; los generados son solo A-Z y 2-9).

Sin admin_duelpass.json todo código es «incorrecto» (antes, 0x8102 iba por AUTO a ceros = «éxito» que no daba nada
pero ponía en local los derechos de principiante sin que el servidor lo supiera).
YO2_DPAUTH=0 al arrancar deja 0x8101 como antes (AUTO).

Cifras de inicio (admin_regalos.json junto a state.json, opcional): {"mp", "dpp", "baul"} para los perfiles que se
crean a partir de ese momento. Sin fichero = exactamente como hoy (START_MP, START_DPP, YO2_TRUNK_START).
"""
import datetime
import json
import os
import re
import secrets
import shutil
import struct
import threading

LOCK = threading.RLock()
CODES_FILE = "admin_duelpass.json"
START_FILE = "admin_regalos.json"
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # sin 0/O ni 1/I para que no se confundan al teclearlos
CODE_LEN = 12                                       # cabe en los 16 B de 0x8101 (el cliente no manda más)
CODE_RX = re.compile(r"^[A-Z0-9]{4,16}$")
DPP_MAX = 255                                       # u8 en 0x8102
CARD_MAX = 0xcb7                                    # FUN_006c01b0: carta > 0xcb7 -> no da nada
MP_BONUS_DPP = (15, 20, 30)                         # FUN_006c01b0: con ese DPP, +10 MP al hueco 0
RES_OK, RES_NO, RES_USED, RES_VOID = 0, 1, 2, 3
RES_TXT = {RES_OK: "OK", RES_NO: "no existe", RES_USED: "ya canjeado", RES_VOID: "anulado"}
BAUL_MODES = ("all3", "deck", "empty")
START_MAX = 1000000
DPAUTH_ON = os.environ.get("YO2_DPAUTH", "1") != "0"


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def short(code):
    """Lo único que va a logs y auditoría: los 4 primeros caracteres."""
    return (str(code or "")[:4] + "…") if code else "?"


_TEXTO = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_ ")


def _blowfish():
    """research/cuentas/cifrado.py (Blowfish ECB con la clave del exe), cargado una vez."""
    import importlib.util, sys
    m = sys.modules.get("yo2_cifrado")
    if m is None:
        sp = importlib.util.spec_from_file_location(
            "yo2_cifrado", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cuentas", "cifrado.py"))
        m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m); sys.modules["yo2_cifrado"] = m
    return m._bf()


def norm(code):
    """Texto de 0x8101 o del panel -> código guardado (mayúsculas, sin espacios alrededor).
    El cliente manda los 16 B de 0x8101 CIFRADOS con Blowfish ECB y la misma clave que el login 0x3003 (visto en vivo
    07-10: llegaban bytes no imprimibles y al descifrarlos sale el código tecleado; C por el log). Si los 16 B ya son
    texto (fake_client, pruebas viejas) se usan tal cual."""
    if isinstance(code, (bytes, bytearray)):
        raw = bytes(code[:16])
        txt = raw.split(b"\0")[0]
        if len(raw) == 16 and (not txt or not set(txt.decode("latin1")) <= _TEXTO):
            try:
                dec = _blowfish().decrypt_ecb(raw).split(b"\0")[0]
                if dec and set(dec.decode("latin1")) <= _TEXTO:
                    txt = dec
            except Exception:                        # noqa: BLE001  (sin cifrado.py: se queda como venía)
                pass
        code = txt.decode("latin1", "replace")
    return str(code or "").strip().upper()


def _write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


# ---------------------------------------------------------------- mazos que el cliente sabe recibir por 0x8102
def packinfo_decks(PP):
    """{id: {"nombre", "bit"}} de los mazos de packinfo_s.txt (tipo 1, con bit de derecho en la 7.ª línea y <id>.ydc).
    El cliente busca el mazo en su lista de packinfo (FUN_006c3610) y pone bit + 1 (8001 <-> 0x302 visto en el log)."""
    out = {}
    try:
        t = open(os.path.join(PP.DATA2008, "cardpack", "packinfo_s.txt"), "rb").read().decode("cp1252").replace("\r", "")
    except OSError:
        return out
    for b in t.strip().split("\n\n"):
        ln = b.split("\n")
        if len(ln) >= 7 and ln[0].strip().isdigit() and ln[1].strip() == "1" and ln[6].strip().isdigit():
            pid = int(ln[0])
            if pid != 8999 and PP.pack_file(pid, ".ydc"):
                out[pid] = {"nombre": ln[3].strip(), "bit": int(ln[6])}
    return out


# ---------------------------------------------------------------- almacén de códigos
def codes_path(state_dir):
    return os.path.join(state_dir, CODES_FILE)


def load(state_dir):
    with LOCK:
        try:
            with open(codes_path(state_dir), encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return {}
        c = d.get("codigos") if isinstance(d, dict) else None
        return {str(k): v for k, v in c.items() if isinstance(v, dict)} if isinstance(c, dict) else {}


def save(state_dir, codes):
    with LOCK:
        p = codes_path(state_dir)
        _write(p, json.dumps({"_nota": "Códigos DUELPASS de un solo uso (panel: Economía › Códigos DUELPASS). "
                                       "estado: libre / canjeado / anulado.", "codigos": codes},
                             ensure_ascii=False, indent=1))


def new_code(taken):
    while True:
        c = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
        if c not in taken:
            return c


def create(state_dir, n, dpp=0, carta=0, mazo=0, nota=""):
    """n códigos nuevos con lo mismo. Devuelve la lista. La validación de cifras la hace quien llama (panel)."""
    with LOCK:
        codes = load(state_dir)
        p = codes_path(state_dir)
        if os.path.exists(p):
            shutil.copyfile(p, p + ".bak-admin")
        out = []
        for _ in range(int(n)):
            c = new_code(codes)
            codes[c] = {"estado": "libre", "creado": now(), "dpp": int(dpp), "carta": int(carta), "mazo": int(mazo),
                        "nota": str(nota or "")[:80]}
            out.append(c)
        save(state_dir, codes)
        return out


def void(state_dir, code):
    """Anula un código libre. Devuelve el estado que tenía (None si no existe)."""
    with LOCK:
        codes = load(state_dir)
        e = codes.get(norm(code))
        if e is None:
            return None
        was = e.get("estado", "libre")
        if was == "libre":
            p = codes_path(state_dir)
            shutil.copyfile(p, p + ".bak-admin")
            e["estado"] = "anulado"; e["anulado"] = now()
            save(state_dir, codes)
        return was


def take(state_dir, code, who):
    """(resultado, entrada). Con 0 el código queda ya canjeado (por who) en el fichero."""
    with LOCK:
        codes = load(state_dir)
        e = codes.get(norm(code)) if CODE_RX.match(norm(code)) else None
        if e is None:
            return RES_NO, None
        st = e.get("estado", "libre")
        if st == "canjeado":
            return RES_USED, e
        if st != "libre":
            return RES_VOID, e
        e["estado"] = "canjeado"; e["canje"] = dict(who, fecha=now())
        save(state_dir, codes)
        return RES_OK, dict(e)


def untake(state_dir, code):
    """Deshace take() si luego falló dar las cosas (vuelve a libre)."""
    with LOCK:
        codes = load(state_dir)
        e = codes.get(norm(code))
        if e is not None and e.get("estado") == "canjeado":
            e["estado"] = "libre"; e.pop("canje", None)
            save(state_dir, codes)


# ---------------------------------------------------------------- lo que hace el cliente con 0x8102 (FUN_006c01b0)
def reply(res, e=None):
    """Datos de 0x8102: s32, u16 carta, u8 0, u8 DPP, u16 mazo (todo 0 si hay error, como hace el cliente al leerlo)."""
    if res != RES_OK or not e:
        return struct.pack(">iHBBH", res, 0, 0, 0, 0)
    return struct.pack(">iHBBH", 0, int(e.get("carta", 0)) & 0xffff, 0, int(e.get("dpp", 0)) & 0xff,
                       int(e.get("mazo", 0)) & 0xffff)


def apply(PP, p, st, e):
    """Replica en el perfil lo que el cliente hace en local con 0x8102 = 0 (para no desincronizarse)."""
    card, dpp, deck = int(e.get("carta", 0)), int(e.get("dpp", 0)) & 0xff, int(e.get("mazo", 0))
    if card and PP.card_ok(card) and card <= CARD_MAX:
        PP.trunk_add(p, [card], src="0x8102 DPAUTH")
    decks = packinfo_decks(PP) if deck else {}
    if deck in decks:
        PP.trunk_add(p, PP.structure_deck_cards(deck), src="0x8102 DPAUTH mazo %d" % deck)
        PP.pbin_set(p, decks[deck]["bit"] + 1)
    PP.rec_inc(p.setdefault("rec", {}), "dpp", dpp)
    if dpp in MP_BONUS_DPP:                          # el cliente escribe cuenta+0x12f8 = registro del hueco 0
        p0 = (st.get("profiles", {}).get("0") if st is not None else None) or p
        PP.rec_inc(p0.setdefault("rec", {}), "mp", 10)
    for b in PP.PB_BEGINNER_RIGHTS:
        if not PP.pbin_test(p, b) and not PP.pbin_test(p, b + 1):
            PP.pbin_set(p, b)


def make_handler(PP, load_state, save_state, lock, state_dir, log):
    """h_dpauth(c, data) para PH["h_dpauth"] de run.py (0x8101 del lobby)."""
    lk = lock if lock is not None else LOCK

    def h_dpauth(c, data):                          # 0x8101: 16 B de texto -> 0x8102
        code = norm(data)
        e = None
        with lk:
            st = load_state()
            slot = PP.active_slot(st)
            p = PP.profile(st, slot)
            who = {"cuenta": str(st.get("_acct") or st.get("owner_acct") or "dueño"), "hueco": slot,
                   "personaje": str(p.get("name") or "")[:16]}
            res, e = take(state_dir, code, who)
            if res == RES_OK:
                try:
                    apply(PP, p, st, e)
                    save_state(st)
                except Exception:
                    untake(state_dir, code)
                    raise
        log(f"[duelpass] 0x8101 código {short(code)} de {who['personaje'] or '?'} (hueco {slot}) -> "
            f"{RES_TXT.get(res, res)}" + (f": DPP +{e.get('dpp', 0)}, carta {e.get('carta', 0)}, mazo {e.get('mazo', 0)}"
                                          if res == RES_OK else ""))
        c.send(0x8102, reply(res, e))

    h_dpauth._duelpass = True
    return h_dpauth


def install_game(ns, state_dir=None):
    """Engancha 0x8101 en un run.py (globals vivos o al arrancar). Devuelve True si quedó puesto."""
    PP, PH, PHL = ns.get("PP"), ns.get("PH"), ns.get("PH_LOBBY")
    if PP is None or PH is None or PHL is None:
        return False
    sd = state_dir or os.path.dirname(os.path.abspath(ns["STATE_PATH"]))
    PH["h_dpauth"] = make_handler(PP, ns["load_state"], ns["save_state"], ns.get("STATE_LOCK"), sd, ns["log"])
    PHL[0x8101] = "h_dpauth"
    return True


# ---------------------------------------------------------------- cifras de inicio (Regalos de inicio)
_START_CACHE = {}


def start_path(state_dir):
    return os.path.join(state_dir, START_FILE)


def start_values(state_dir):
    """{"mp", "dpp", "baul"} del fichero (solo las claves válidas; {} sin fichero). Cacheado por mtime."""
    p = start_path(state_dir)
    try:
        sig = os.stat(p).st_mtime_ns
    except OSError:
        return {}
    hit = _START_CACHE.get(p)
    if hit and hit[0] == sig:
        return dict(hit[1])
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    out = {}
    if isinstance(d, dict):
        for k in ("mp", "dpp"):
            if type(d.get(k)) is int and 0 <= d[k] <= START_MAX:
                out[k] = d[k]
        if d.get("baul") in BAUL_MODES:
            out["baul"] = d["baul"]
    _START_CACHE[p] = (sig, out)
    return dict(out)


def install_start(PP, state_dir):
    """Envuelve PP.blank_profile y PP.seed_trunk (idempotente): si admin_regalos.json existe, sus cifras valen para
    los perfiles que se creen; si no, las de siempre."""
    bp = getattr(PP.blank_profile, "_dp_orig", PP.blank_profile)
    sd = getattr(PP.seed_trunk, "_dp_orig", PP.seed_trunk)

    def blank_profile(name="", _orig=bp):
        p = _orig(name)
        sv = start_values(state_dir)
        r = p.setdefault("rec", {})
        for k in ("mp", "dpp"):
            if k in sv:
                r[k] = sv[k]
        return p

    def seed_trunk(p, mode=None, _orig=sd):
        if mode is None:
            mode = start_values(state_dir).get("baul")
        return _orig(p, mode)

    blank_profile._dp_orig = bp
    seed_trunk._dp_orig = sd
    PP.blank_profile = blank_profile
    PP.seed_trunk = seed_trunk
    PP._DUELPASS_START_DIR = state_dir
    return True


def start_installed(PP):
    return PP is not None and hasattr(PP.blank_profile, "_dp_orig") and hasattr(PP.seed_trunk, "_dp_orig")
