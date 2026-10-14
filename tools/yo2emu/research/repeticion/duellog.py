"""Registro de duelo (repetición) que el servidor manda al acabar un PvP: 0x6010 / 0x6016 / 0x6011… / 0x6012.

Todo sale del descompilado de yo2.exe (ver NOTES.md de esta carpeta, con confianza C/P/D por campo):
  - 0x6010 DUELLOG_HEADER (FUN_006944b0, big-endian): cabecera de la lista + tamaños.
  - 0x6011 DUELLOG_COMMAND (FUN_006947a0): bytes en crudo que el cliente va pegando en un búfer de +0xbcec bytes.
  - 0x6012 DUELLOG_FINISH (FUN_00694840): el cliente escribe duellogs/<título>.log (cabecera de 0x28 B + u32 +0xbcf8
    + los bytes recibidos) y añade una línea a duellogs/list.txt.
  - Al verlo, FUN_00697210 carga el .log (versión >= 0xd6: RLE de parejas (valor, cuenta)) y FUN_00695430 decodifica
    el «blob» (little-endian) que lleva los dos jugadores y la lista de comandos 0x6202 de cada lado.
Las entradas de la grabación son los mismos 0x6202 DUEL_SETCOMMAND que el servidor mandó a cada cliente, marcadas con
el lado (0 = jugador del bloque 0, 1 = bloque 1). La repetición (FUN_0079d0f0) solo usa las de su lado.
Antes del duelo la repetición juega el piedra-papel-tijera con la mano de cada bloque (+0xbc04 / +0xbc08) y empieza
quien diga +0xbbe6 (replay_start; NOTES §7): con dos manos iguales se queda en bucle (07-10).
"""
import struct
import time

MAGIC = 0x080A              # +0xba94: el decodificador exige 0x80a (C)
VERSION = 0xD8              # +0xba96: acepta 201..216; >= 0xd6 = datos en RLE (C). Cuál mandaba el oficial: D
MODE_SINGLE, MODE_MATCH = 3, 4   # +0xba98: la repetición solo arranca con 3 o 4 (-> modo 10/11, escena 0x1e) (C)
BGM_DEFAULT = 0x20000010    # +0xbbfc -> ND+0x684 = música del duelo (FUN_00794cd0 elige 0x20000010..12) (P)
CHUNK = 1024                # datos por 0x6011: el lector se para en el cursor 0x400 (C); el oficial: D
COUNTRY_DEFAULT = b"ESP\0"
MAX_DECODED = 0x9FFFFF      # el cargador aborta por encima (C)

M_HEADER, M_COMMAND, M_FINISH, M_ACK = 0x6010, 0x6011, 0x6012, 0x6013
M_FUSION_INIT, M_FUSION, M_SEARCH_INIT, M_SEARCH = 0x6014, 0x6015, 0x6016, 0x6017


# --------------------------------------------------------------------------------------------- RLE (FUN_00697210)
def rle_encode(data):
    """Parejas (valor, cuenta 1..255): lo que lee FUN_00697210 con versión >= 0xd6."""
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        v = data[i]
        j = i + 1
        while j < n and data[j] == v and j - i < 255:
            j += 1
        out += bytes((v, j - i))
        i = j
    return bytes(out)


def rle_decode(data):
    out = bytearray()
    for k in range(0, len(data) - 1, 2):
        out += bytes([data[k]]) * data[k + 1]
    return bytes(out)


# ------------------------------------------------------------------------------------------------ blob (LE)
def _pad(b, n):
    b = bytes(b or b"")[:n]
    return b + bytes(n - len(b))


def _name16(s):
    """16 B con NUL garantizado (el cliente hace strcpy al bloque del jugador)."""
    return _pad(str(s or "").encode("latin1", "replace")[:15], 16)


def entry_from_6202(side, data):
    """0x6202 (40 B big-endian: u32 tipo, u32 B, 8×u32 P) -> entrada de 44 B del blob (little-endian):
    u32 tipo, u32 B, u8 lado, u8 0, u16 0, 8×u32 P (orden de lectura de FUN_00695430). +1 y +2 no los usa la
    repetición (FUN_0079d0f0 copia desde +4): C; qué llevaban en el oficial: D (para 0x4d el u16 se sumaba a +0xbba8
    en el lector muerto FUN_00697d80)."""
    d = _pad(data, 40)
    a, b, *p = struct.unpack(">10I", d)
    return struct.pack("<IIBBH8I", a, b, side & 0xff, 0, 0, *p)


def player_block(pl, hand=None):
    """172 B por jugador (orden de FUN_00695430; destino en ND entre corchetes):
    u32 pid [+0xbbb4], u32 valor de la lista [+0xbbbc], 16 B nombre [+0xbbc4], u32 cartas del mazo [+0xbbec -> +0x564],
    u32 extra [+0xbbf4 -> +0x568], u32 música [+0xbbfc -> +0x684], 4×u8 [+0xbc04.. jugador 0 / +0xbc08.. jugador 1],
    u32 [+0xbc0c], 3×32 B piezas del avatar por mundo [+0xbc14/+0xbc56/+0xbc98 -> +0x574/+0x585/+0x596, 16 B usados],
    4 B país [+0xbcda -> +0x5b8], 28 B sin leer.
    El 1.er de los 4×u8 es la MANO del piedra-papel-tijera (hand, 0..2 como el u8 de 0x6101): la repetición la lee de
    +0xbc04 / +0xbc08 (FUN_007c76b0, ND+0x678 & 4; C). Los otros 3: D (0)."""
    parts = list(pl.get("parts") or [])[:3] + [b""] * 3
    u8s = _pad(pl.get("u8s", b""), 4)
    if hand is not None:
        u8s = bytes([hand % 3]) + u8s[1:]
    return (struct.pack("<II", pl.get("pid", 0) & 0xffffffff, pl.get("listval", 0) & 0xffffffff)
            + _name16(pl.get("name"))
            + struct.pack("<III", pl.get("main", 0), pl.get("extra", 0), pl.get("bgm", BGM_DEFAULT))
            + u8s + struct.pack("<I", pl.get("u32b", 0))
            + b"".join(_pad(x, 32) for x in parts[:3])
            + _pad(pl.get("country", COUNTRY_DEFAULT), 4) + bytes(28))


# +0xbba3 = tipo de sala (b2 de la puerta, ROOM_KINDS de run.py). Al abrir la repetición el cliente llama
# FUN_00a587a0(+0xbba3) -> mundo de las piezas del avatar: 1,9,10,0xb,0xe,0x10,0x11,0x14 -> 1 (Academia);
# 7,8,0xd,0xf -> 0 (Domino); 0xc (My Room) -> el mundo actual; CUALQUIER OTRO (p. ej. 0) -> aserto «Lobby Rank Setting
# Error» (yo2GameManager.cpp L693 = 0x2b5) y exit(1). C
ROOM_KINDS_OK = {1, 9, 10, 0xB, 0xE, 0x10, 0x11, 0x14, 7, 8, 0xD, 0xF, 0xC}
ROOM_KIND_DEFAULT = 0xC

# Offsets en el blob decodificado (build_blob; los usa reparar_registros.py): +0xbbe6 y la mano de cada bloque.
OFF_GAMES = 88
OFF_HAND = (166, 166 + 172)


def replay_start(hands=None, first=None):
    """Piedra-papel-tijera y quién empieza de la repetición -> (manos (j0, j1), quién empieza, avisos).

    Lo que hace el cliente al verla (FUN_007c76b0 con ND+0x678 & 4, research/repeticion/NOTES.md §7; C):
    - mano propia = +0xbc04, la del otro = +0xbc08 (1.er u8 de cada bloque, intercambiados si mira el jugador 1);
      gana el que (otro − mía) mod 3 == 1 ve; con un EMPATE vuelve a jugar con LAS MISMAS manos: bucle sin fin.
    - quién empieza = +0xbbe6 (partida 1; 0 = el jugador del bloque 0) -> +0x900 -> DAT_012a907c. 0xff = -1 = nadie.
    Van las manos que decidieron en vivo (PvpMatch.jhands, las mismas del 0x6701 del espectador; que el oficial grabara
    esas: P, es lo único que no se queda en bucle) y quién empezó (PvpMatch.first, absoluto). Si falta algo, NUESTRO
    (P): unas manos que no empatan con el que empieza como ganador, o empieza el ganador del piedra-papel-tijera."""
    avisos = []
    f = first if first in (0, 1) else None
    h = None
    if hands is not None and len(hands) == 2 and all(isinstance(x, int) and 0 <= x <= 2 for x in hands):
        h = (hands[0], hands[1])
    why = ("sin manos del piedra-papel-tijera" if h is None
           else f"manos {list(h)} empatadas (la repetición se quedaría en bucle)" if (h[1] - h[0]) % 3 == 0 else None)
    if why:
        w = f if f is not None else 0
        h = (0, 1) if w == 0 else (1, 0)                   # 0 gana a 1 (+0x8f8 = 1): gana w
        avisos.append(f"{why}: van {list(h)} (gana el jugador {w}, P)")
    if f is None:
        f = 0 if (h[1] - h[0]) % 3 == 1 else 1
        avisos.append(f"sin quién empieza: el ganador del piedra-papel-tijera, {f} (P)")
    return h, f, avisos


def build_blob(players, entries, mode=MODE_SINGLE, when=None, room_kind=ROOM_KIND_DEFAULT, games=(0xFF, 0xFF, 0xFF),
               fusion=None, search=None):
    """Blob que decodifica FUN_00695430. entries = [(lado, datos 0x6202 de 40 B)]. games = +0xbbe6..8: QUIÉN EMPIEZA
    cada partida del match (0/1 = jugador del bloque 0/1; se invierten al ver desde el lado 1; otro = nadie). La
    repetición lee +0xbbe5 + DAT_012a9088 (partida, 1 en un duelo suelto) en el estado 7 del piedra-papel-tijera (C;
    antes se ponía aquí el ganador: P equivocada). players[k]["hand"] = mano del piedra-papel-tijera (0..2).
    Siempre sale una repetición que arranca: replay_start rellena lo que falte (manos que no empatan, quién empieza)."""
    when = int(time.time() if when is None else when)
    games = list(games or ()) + [0xFF] * 3
    hands = [pl.get("hand") for pl in players[:2]]
    hands, games[0], _ = replay_start(None if None in hands else hands, games[0])
    b = bytearray()
    b += bytes(32)                                         # 8×u32 que se leen y se tiran
    b += bytes(32)                                         # 32 B saltados
    b += struct.pack("<IBII", when, 0, mode, 0)            # +0xba9c fecha, +0xbaa0, +0xba98 modo, u32 tirado
    if room_kind not in ROOM_KINDS_OK:
        room_kind = ROOM_KIND_DEFAULT
    b += bytes([0, room_kind & 0xff, 0, 0])                # +0xbba2..5 (+0xbba3 = tipo de sala, ver arriba: C)
    b += struct.pack("<I", 0) + bytes(3)                   # +0xbbac, +0xbbb0..2
    b += bytes(g & 0xff for g in games[:3])               # +0xbbe6..8 quién empieza cada partida
    b += bytes(7 + 32)                                     # saltados
    for k, pl in enumerate(players[:2]):
        b += player_block(pl, hands[k])
    b += bytes(1)                                          # saltado
    b += struct.pack("<III", len(entries), 0, 0) + bytes(32)
    for side, data in entries:
        b += entry_from_6202(side, data)
    fa, fb, fm = fusion or ([], [], [])                   # +0xbd00 n, +0xbd04 m, n×u16, n×u16, m×u32
    b += struct.pack("<II", len(fa), len(fm))
    b += b"".join(struct.pack("<H", x) for x in fa) + b"".join(struct.pack("<H", x) for x in fb)
    b += b"".join(struct.pack("<I", x) for x in fm)
    search = list(search or [])                            # +0xd78c n, n×(u16, u16)
    b += struct.pack("<I", len(search)) + b"".join(struct.pack("<HH", x, y) for x, y in search)
    return bytes(b)


# ---------------------------------------------------------------------------------------------- red (BE)
def header_6010(title, n_sent, n_decoded, mode, listvals, names, when, bbac=0, bbb=(0, 0, 0),
                magic=MAGIC, version=VERSION):
    """0x6010 (FUN_006944b0): u16 +0xba94, u16 +0xba96, cstr título +0xbaa2, u32 bytes a recibir +0xbcec,
    u32 tamaño decodificado +0xbcf8, u32 modo +0xba98, u32 +0xbbbc, u32 +0xbbc0, 16 B nombre 0, 16 B nombre 1,
    u32 fecha +0xba9c, 4 B que el cliente NO lee (FUN_0068a930 escribe), u32 +0xbbac, 3×u8 +0xbbb0..2."""
    t = safe_title(title).encode("latin1", "replace")
    return (struct.pack(">HH", magic, version) + t + b"\0"
            + struct.pack(">IIIII", n_sent, n_decoded, mode, listvals[0] & 0xffffffff, listvals[1] & 0xffffffff)
            + _name16(names[0]) + _name16(names[1])
            + struct.pack(">III", int(when), 0, bbac) + bytes(b & 0xff for b in bbb))


def safe_title(title):
    """Nombre de fichero: el cliente hace fopen(«…\\duellogs\\<título>.log», "wb") sin comprobar (fputc a NULL si
    falla): fuera de la lista blanca no pasa nada."""
    ok = "".join(ch for ch in str(title or "") if ch.isalnum() or ch in "-_")
    return (ok or time.strftime("pvp%Y%m%d%H%M%S"))[:31]


def messages(players, entries, title=None, mode=MODE_SINGLE, when=None, room_kind=ROOM_KIND_DEFAULT, games=None,
             version=VERSION, chunk=CHUNK, search_reset=True):
    """Lista ordenada [(id, datos)] para UN cliente: 0x6010, 0x6011 × k, 0x6016 (u32 0), 0x6012. El servidor manda
    la siguiente tras cada 0x6013 (el cliente lo contesta a 0x6010, 0x6011, 0x6014..0x6017; a 0x6012 no)."""
    when = int(time.time() if when is None else when)
    blob = build_blob(players, entries, mode=mode, when=when, room_kind=room_kind,
                      games=games if games is not None else (0xFF, 0xFF, 0xFF))
    # Con versión < 0xd6 el cargador toma todo lo que va tras los 0x28 B, así que el u32 +0xbcf8 que escribe
    # FUN_00694840 hace de 4 primeros bytes del blob (el 1.º de los 8 u32 que se tiran) y por la red va el resto (C).
    # Con RLE (>= 0xd6) +0xbcf8 es el tamaño decodificado.
    if version >= 0xD6:
        body, n_dec = rle_encode(blob), len(blob)
    else:
        body, n_dec = blob[4:], struct.unpack_from("<I", blob, 0)[0]
    head = header_6010(title or time.strftime("pvp%Y%m%d%H%M%S", time.localtime(when)), len(body), n_dec, mode,
                       [p.get("listval", 0) for p in players], [p.get("name") for p in players], when,
                       version=version)
    out = [(M_HEADER, head)]
    for k in range(0, len(body), chunk):
        out.append((M_COMMAND, body[k:k + chunk]))
    if search_reset:
        # +0xd78c = 0 (C): FUN_00694840 escribe 8 B por entrada en un búfer de pila de 503 B (máx. 62 entradas); sin
        # este mensaje el contador es basura (cierre del portátil, access-violation en 0x6949a3). 1.ª columna de
        # list.txt = «00000000». Orden pedido por el coordinador: cabecera -> 0x6011… -> 0x6016 -> 0x6012.
        out.append((M_SEARCH_INIT, struct.pack(">I", 0)))
    out.append((M_FINISH, b""))
    return out


class Recorder:
    """Apunta los 0x6202 que salen hacia cada lado, en orden de envío."""

    def __init__(self):
        self.entries = []

    def note(self, side, msgs):
        for m in msgs:
            mid, data = m if isinstance(m, tuple) else (0x6202, m)
            if mid == 0x6202 and isinstance(data, (bytes, bytearray)):
                self.entries.append((side, bytes(data)))
