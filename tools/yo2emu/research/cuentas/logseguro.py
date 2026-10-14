"""Qué NO va en claro al log del servidor (07-10). Lo usan run.py (log() y la línea «[srv:…] <- 0x…» de _handle),
hot_logseguro.py (en caliente), sanear_logs.py (logs ya escritos) y las pruebas.

Se tapa:
  - 0x3003 (usuario + MD5 con la contraseña, Blowfish ECB; research/cuentas/NOTES.md): ni hex ni repr, solo
    «len=32 … cuenta <6 hex del id canónico>» (con el blob se saca el usuario y se puede atacar la contraseña).
  - 0x4288 REQCREATEROOM y 0x4300 REQMATCH: los 16 B de contraseña de sala (bytes 4-20); el resto se deja.
  - 0x81xx (DUELPASS: 0x8101 = 16 B de código): todo el contenido.
  - Gancho del cliente (net_hook.js -> «[cli] send» + volcado hex): el volcado de un envío con alguno de esos
    mensajes (los bytes del cable solo llevan el XOR de proto.KEY: se leen igual).
  - Registro interno del cliente ([game:…]): los argumentos de «ID[%s]», «PW[%s]», las líneas de contraseña guardada
    (パスワード…[%s]) y «password:%s» de la lista de salas.
  - «[persistencia] DPAUTH '<código entero>'» (formato viejo; hoy ya sale con 4 caracteres + «…» y se deja).
Nunca lanza: ante cualquier error devuelve una versión tapada (no la original).
"""
import hashlib
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
XOR_KEY = bytes.fromhex("5aa55a55")          # proto.KEY
HDR = 24
PW_RANGE = {0x4288: (4, 20), 0x4300: (4, 20)}  # research/lobby/proposal.py h_createroom / h_reqmatch
OCULTO = "[contenido oculto]"


def sensible(msg_id):
    return msg_id == 0x3003 or msg_id in PW_RANGE or (msg_id >> 8) == 0x81


# ---------------------------------------------------------------- id de cuenta (6 hex)
def _cifrado():
    m = sys.modules.get("yo2_cifrado")
    if m is None:
        try:
            import importlib.util
            sp = importlib.util.spec_from_file_location("yo2_cifrado", os.path.join(HERE, "cifrado.py"))
            m = importlib.util.module_from_spec(sp)
            sp.loader.exec_module(m)
            sys.modules["yo2_cifrado"] = m
        except Exception:                            # noqa: BLE001
            return None
    return m


def acct6(data):
    """6 primeros hex del id canónico de cuenta (cifrado.account_id); sin cifrado.py, del sha256 de los bytes."""
    data = bytes(data[:32])
    try:
        m = _cifrado()
        if m is not None:
            return m.account_id(data)[:6]
    except Exception:                                # noqa: BLE001
        pass
    return hashlib.sha256(data).hexdigest()[:6]


# ---------------------------------------------------------------- línea «[srv:p] <- 0x…» de run.py
def data_txt(msg_id, data):
    """Texto del contenido de un mensaje sensible (sin repr)."""
    data = bytes(data)
    if msg_id == 0x3003:
        return f"cuenta {acct6(data)}" if len(data) >= 32 else OCULTO
    if msg_id in PW_RANGE:
        a, b = PW_RANGE[msg_id]
        if len(data) <= a:
            return data.hex(" ")
        return " ".join(x for x in (data[:a].hex(" "), "[contraseña oculta]", data[b:].hex(" ")) if x)
    return "[código oculto]"


def rx_line(port, msg_id, param, data, ok):
    """La línea de log de un mensaje recibido. Igual que antes salvo los sensibles (sin hex ni repr)."""
    md5 = "ok" if ok else "MAL"
    if not sensible(msg_id):
        return f"[srv:{port}] <- 0x{msg_id:04x} #{param} len={len(data)} md5={md5} {data.hex(' ')} | {data!r}"[:400]
    try:
        txt = data_txt(msg_id, data)
    except Exception:                                # noqa: BLE001
        txt = OCULTO
    return f"[srv:{port}] <- 0x{msg_id:04x} #{param} len={len(data)} md5={md5} {txt}"


RX_LINE = re.compile(r"^\[srv:(\d+)\] <- 0x([0-9a-f]{4}) #(\d+) len=(\d+) md5=(\w+) ?(.*)$", re.S)
HEXRUN = re.compile(r"^((?:[0-9a-f]{2} )*[0-9a-f]{2})")


def _mask_srv(line):
    m = RX_LINE.match(line)
    if not m or not sensible(int(m.group(2), 16)):
        return line
    port, mid, param, n, md5, rest = m.groups()
    mid = int(mid, 16)
    if rest.startswith(("cuenta ", "[")) or "[contraseña oculta]" in rest:
        return line                                  # ya tapada
    h = HEXRUN.match(rest)
    try:
        data = bytes.fromhex(h.group(1).replace(" ", "")) if h else b""
        txt = data_txt(mid, data) if len(data) == int(n) else OCULTO
    except Exception:                                # noqa: BLE001
        txt = OCULTO
    return f"[srv:{port}] <- 0x{mid:04x} #{param} len={n} md5={md5} {txt}"


# ---------------------------------------------------------------- volcados hex del gancho del cliente
DUMP_LINE = re.compile(r"^[0-9a-fA-F]{8}  ((?:[0-9a-fA-F]{2} ){0,15}[0-9a-fA-F]{2})")


def is_dump_line(line):
    return len(line) > 11 and line[8:10] == "  " and DUMP_LINE.match(line) is not None


def dump_bytes(lines):
    out = bytearray()
    for ln in lines:
        m = DUMP_LINE.match(ln)
        if not m:
            return None
        out += bytes.fromhex(m.group(1).replace(" ", ""))
    return bytes(out)


def wire_ids(b):
    """Ids de mensaje que hay en unos bytes del cable (XOR de proto): los de una lectura seguida desde el
    principio y, por si el volcado no empieza en un mensaje, cualquier cabecera plausible de un id sensible."""
    ids = []
    i = 0
    while i + 4 <= len(b):
        mid = (b[i] ^ 0x5a) << 8 | (b[i + 1] ^ 0xa5)
        n = (b[i + 2] ^ 0x5a) << 8 | (b[i + 3] ^ 0x55)
        ids.append(mid)
        if n > 1024:
            break
        i += HDR + n
    for i in range(0, max(0, len(b) - 3)):
        mid = (b[i] ^ 0x5a) << 8 | (b[i + 1] ^ 0xa5)
        n = (b[i + 2] ^ 0x5a) << 8 | (b[i + 3] ^ 0x55)
        if sensible(mid) and n <= 1024:
            ids.append(mid)
    return ids


def mask_dump(lines):
    """Líneas de un volcado -> las mismas, o UNA línea «[volcado oculto …]» si lleva un mensaje sensible."""
    b = dump_bytes(lines)
    if b is None:
        return lines
    hid = sorted({m for m in wire_ids(b) if sensible(m)})
    if not hid:
        return lines
    return [f"{lines[0][:8]}  [volcado oculto: {', '.join(f'0x{m:04x}' for m in hid)}; {len(b)} B]"]


# ---------------------------------------------------------------- registro interno del cliente
GAME_SENS = re.compile(r"(?<![\w:])(?:ID|PW)\[%s\]|パスワード|password:%s")
BRACKETS = re.compile(r"\[[^\]]*\]")
ROOM_PW = re.compile(r"password:.*$", re.S)


def _mask_game(line):
    if "  ||  " not in line:
        return line
    left, right = line.split("  ||  ", 1)
    if "%s" not in left or not GAME_SENS.search(left):
        return line
    if "password:%s" in left:
        right = ROOM_PW.sub("password:***", right)
    else:
        right = BRACKETS.sub("[***]", right)
        if "[" not in right:                         # sin corchetes reconocibles: fuera todo
            right = "***"
    return left + "  ||  " + right


# El código entero (formato viejo); el de hoy (persistencia/duelpass.short) ya lleva solo 4 caracteres + «…».
DPAUTH = re.compile(r"^(\[persistencia\] DPAUTH )(?:'[^']{5,}'|\"[^\"]{5,}\")")


def mask_line(text):
    """Una línea (o un bloque de varias, como el volcado de log(extra)) apta para el log. Nunca lanza."""
    try:
        if not text:
            return text
        c = text[0]
        if c == "[":
            if text.startswith("[srv:"):
                return _mask_srv(text) if " <- 0x" in text[:20] else text
            if text.startswith("[game:"):
                return _mask_game(text)
            if text.startswith("[persistencia] DPAUTH"):
                return DPAUTH.sub(r"\1[código oculto]", text)
            return text
        if c in "0123456789abcdefABCDEF" and is_dump_line(text):
            return "\n".join(mask_dump(text.split("\n")))
        return text
    except Exception:                                # noqa: BLE001
        return text[:12] + " [línea oculta: error al tapar]"
