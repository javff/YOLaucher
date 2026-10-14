"""Propuesta del agente 4: avatar, vestuario y tienda de avatar para run.py (NO se importa desde run.py todavía).

Todo sale de yo2.exe Ver.080912 (Ghidra, copia ghidra_a4) y de merge2008/data/common/yo2pbinmap.dat.
Notas completas y grado de confianza: NOTES.md (misma carpeta).

Resumen de lo que aporta:
  - npc_parts(nid, sex, world)   sustituye a npc_parts de run.py: piezas que PASAN la validación del cliente
                                 (FUN_00a58cc0); si una sola pieza falla, el cliente pinta el avatar por defecto
                                 de chico, que es lo que pasaba con casi todas las NPC normales.
  - validate_parts(...)          réplica de FUN_00a58cc0 (para probar sin cliente).
  - avatar_shop_items(...)       artículos de tienda de tipo 2 (ropa/pelo/disco...), param = índice PBIN.
  - purchase_avatar(...)         respuesta a la compra de un artículo de tipo 2 (0x5212 + 0x5211; el integrador
                                 añade 0x4140). Sin 0x5205 desde la ola 1 (B69: el cliente ya pone el bit él solo).
  - npc_portrait_audit()         B65: comprueba las fichas de retrato de las NPC famosas (data/Avatar/npc/*.csv).
  - AvatarStore                  guarda en state.json las piezas (0x4170) y los bits PBIN comprados.
  - payloads: 0x4171, 0x4181, 0x3012 (con avatares), 0x4216 (BLOCKPLAYERLIST2), 0x3114 (PBIN), 0x5205, 0x43a0.

Integración en run.py (lo hace el integrador; aquí no se toca run.py):
  1. game_reply 0x3010: player_entry(...) -> player_entry_with_avatars(slot, name, *store.avatars(slot)).
  2. game_reply 0x3114: c.send(0x3114, store.pbin_payload()) en vez de bytes(128).
  3. lobby_reply 0x4170: world, pieces = parse_regavatarinfo(data); store.set_avatar("0", world, pieces);
     c.world = world; c.send(0x4171, regavatar_result()).   (el último 0x4170 que llega = mundo actual)
  4. lobby_reply 0x4214: quitar de EMPTY_LISTS y contestar 0x4215 (u32 0) + 0x4216 block_player2_payload(...) + 0x4217.
  5. 0x4230 (NPC): para las normales, npc_parts(nid, sexo, getattr(c, "world", 0)) en el momento de mandar.
  6. tienda: items = shop_catalog() + avatar_shop_items(len(shop_catalog()) + 1); en 0x5210, si el tipo es 2,
     usar purchase_avatar(...) (mandar los mensajes que devuelve, en orden).
  7. (opcional) 0x4288: el 2º bloque de 16 B de AUTODUELSTART = npc_parts(rival, ...) -> autoduelstart_payload().

Prueba: python tools/yo2emu/research/avatar/proposal.py   (ast + tamaños contra protocol.json + validación de NPC)
"""
import json
import os
import random
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EMU = os.path.normpath(os.path.join(HERE, "..", ".."))
if EMU not in sys.path:
    sys.path.insert(0, EMU)
import proto  # noqa: E402

ORIG = os.path.normpath(os.path.join(EMU, "..", "..", "_original"))
try:                                   # YO2_JUEGO_DATOS (yo2srv/rutas.py); sin ella, _original/extracted/merge2008/data
    from yo2srv import rutas as _rutas  # noqa: E402
except ImportError:                    # servidor viejo sin yo2srv/rutas.py (cargas en caliente sobre commits antiguos): la ruta de siempre
    _rutas = None
_DATA = _rutas.juego() if _rutas else os.path.join(ORIG, "extracted", "merge2008", "data")
PBINMAP = os.path.join(_DATA, "common", "yo2pbinmap.dat")

DOMINO, ACADEMIA = 0, 1          # «mundo» (DAT_012bb6b0+0x448; u32 de 0x4170/0x4181): 0 Domino, 1 Academia
MALE, FEMALE = 0, 1              # byte 0 de las piezas

# Los 16 bytes (CAvatarData +4..+0x13). Índice = «hueco» (columna 4 de yo2pbinmap.dat).
P_SEX, P_HAIR_FRONT, P_FACE_ACC, P_NOSE, P_EYES, P_MOUTH, P_CLOTHES, P_HAIR_BACK, P_DISK, P_GLASSES = range(10)
P_SKIN, P_HAIR_COLOR, P_EYE_COLOR = 13, 14, 15     # 10-12: sin uso (el cliente no los mira)


# ---------------------------------------------------------------- diccionario PBIN (yo2pbinmap.dat)
def load_pbinmap(path=PBINMAP):
    """{pbin: (valor, inicial, clase, hueco, id_texto, mundo)}. clase 1 = pieza de chico, 2 = de chica,
    10 = título; mundo 1 = común, 2 = Domino, 3 = Academia. Lo carga el cliente en FUN_00ad53d0."""
    out = {}
    try:
        raw = open(path, "rb").read().decode("cp932", errors="replace")
    except OSError:
        return out
    for line in raw.replace("\r", "").split("\n"):
        f = line.split(",")
        if len(f) < 8:
            continue
        try:
            idx, val, dflt, kind, slot, text = (int(x) for x in f[:6])
            world = int(f[7])
        except ValueError:
            continue
        out[idx] = (val, dflt, kind, slot, text, world)
    return out


PBIN = load_pbinmap()


def pbin_index(sex, value):
    """Índice PBIN de una pieza: chico = valor, chica = 0x100 + valor (FUN_00a58cc0, iVar5 = 0x100)."""
    return value + (0x100 if sex == FEMALE else 0)


def _world_code(world):
    return 2 if world == DOMINO else 3


# ---------------------------------------------------------------- validación (réplica de FUN_00a58cc0)
def validate_parts(p, world=DOMINO, check_world=True):
    """True si el cliente acepta las piezas tal cual. Si devuelve False, el cliente las cambia ENTERAS por
    default_parts(MALE, world) (¡chico aunque fuera chica!). Lo llama el muñeco del mapa (FUN_009ccfc0,
    check_world=1), el busto (FUN_009e6e30) y el editor."""
    p = bytearray(p[:16].ljust(16, bytes(1)))
    if p[0] > 1:
        return False
    sex = p[0]
    for i in range(16):
        v = p[i]
        if i in (0, 13, 14, 15):
            if v > 7:
                return False
        elif 1 <= i <= 9:
            if i == 9 and v == 0:
                v = 0x20                      # el cliente corrige 0 -> 0x20 (sin gafas)
            rec = PBIN.get(pbin_index(sex, v))
            slot = rec[3] if rec else 0
            rworld = rec[5] if rec else 0
            if slot != i and not (i in (2, 9) and v == 0x20):
                return False
            if check_world and rworld != 1 and rworld != _world_code(world):
                return False
    return True


def default_parts(sex=MALE, world=DOMINO):
    """CAvatarData por defecto (FUN_009bced0(n), n = sexo + 2*mundo)."""
    clothes, disk = (0x70, 0xb0) if world == DOMINO else (0x80, 0xb8)
    return bytes([sex, 0x00, 0x20, 0x30, 0x40, 0x60, clothes, 0x90, disk, 0x20, 0, 0, 0, 0, 0, 0])


def as_client_sees(p, world=DOMINO):
    """Lo que acabará dibujando el cliente."""
    return bytes(p) if validate_parts(p, world) else default_parts(MALE, world)


# ---------------------------------------------------------------- NPC normales
# Listas «seguras»: todas pasan la validación, tienen busto (wm/ww_data.csv) y muñeco (chibi).
# Fuera: pelo de chico 6 (rapado: sin busto), ropas especiales que cambian el pelo por capucha
# (chico 0x78 túnica/0x7c Marik; chica 0x75 túnica/0x77 Marik) y la ropa regalo de los famosos.
NPC_POOL = {
    MALE: {P_HAIR_FRONT: [0, 1, 2, 3, 4, 5, 7, 8, 10, 11], P_HAIR_BACK: [0x90, 0x91, 0x92, 0x93],
           P_FACE_ACC: [0x20, 0x20, 0x20, 0x21], P_EYES: [0x40, 0x41, 0x42, 0x43, 0x44, 0x45],
           P_MOUTH: [0x60, 0x61, 0x62], P_GLASSES: [0x20] * 6 + [0x50, 0x51, 0x52, 0x53],
           P_CLOTHES: {DOMINO: [0x70, 0x71, 0x72, 0x73, 0x74, 0x75, 0x76, 0x77],
                       ACADEMIA: [0x80, 0x81, 0x82, 0x83, 0x84, 0x85, 0x86]}},
    FEMALE: {P_HAIR_FRONT: [0, 1, 2, 3, 4, 8, 5], P_HAIR_BACK: [0x90, 0x91, 0x92],
             P_FACE_ACC: [0x20, 0x20, 0x21], P_EYES: [0x40, 0x41, 0x42, 0x43],
             P_MOUTH: [0x60, 0x61], P_GLASSES: [0x20] * 6 + [0x50, 0x51, 0x52, 0x53, 0x54, 0x55],
             P_CLOTHES: {DOMINO: [0x70, 0x71, 0x72, 0x73, 0x74],
                         ACADEMIA: [0x80, 0x81, 0x82, 0x83, 0x84, 0x85, 0x86]}},
}
NPC_DISKS = {DOMINO: [0xb0, 0xb0, 0xb0, 0xb1, 0xb2, 0xb3, 0xb7, 0xc3, 0xc4],
             ACADEMIA: [0xb8, 0xb8, 0xb8, 0xb9, 0xba, 0xbb, 0xbf, 0xd3, 0xd4]}


def npc_sex(nid, sex):
    """'M'/'F' de npc_ids.json; si falta o es '?', reparto fijo por id."""
    if sex in ("F", FEMALE):
        return FEMALE
    if sex in ("M", MALE):
        return MALE
    return FEMALE if (nid * 2654435761 >> 7) & 1 else MALE


def npc_parts(nid, sex=None, world=DOMINO):
    """16 piezas válidas y variadas para una NPC normal (ids 1-50 y 95-98; las 51-94 ignoran las piezas).
    La cara/pelo/colores son siempre los mismos para cada id; la ropa y el disco dependen del mundo, porque
    el cliente rechaza (y cambia por el avatar por defecto) la ropa o el disco de otro mundo."""
    s = npc_sex(nid, sex)
    pool = NPC_POOL[s]
    r = random.Random(0x5EED0000 + nid)          # mismo aspecto en cada arranque
    pick = lambda key: r.choice(pool[key])       # noqa: E731
    p = bytearray(16)
    p[P_SEX] = s
    p[P_HAIR_FRONT] = pick(P_HAIR_FRONT)
    p[P_FACE_ACC] = pick(P_FACE_ACC)
    p[P_NOSE] = r.choice((0x30, 0x31))
    p[P_EYES] = pick(P_EYES)
    p[P_MOUTH] = pick(P_MOUTH)
    p[P_HAIR_BACK] = pick(P_HAIR_BACK)
    p[P_GLASSES] = pick(P_GLASSES)
    k_clothes, k_disk = r.randrange(1 << 16), r.randrange(1 << 16)   # índice estable entre mundos
    w = ACADEMIA if world == ACADEMIA else DOMINO
    p[P_CLOTHES] = pool[P_CLOTHES][w][k_clothes % len(pool[P_CLOTHES][w])]
    p[P_DISK] = NPC_DISKS[w][k_disk % len(NPC_DISKS[w])]
    p[P_SKIN] = r.choice((0, 0, 1, 2))           # 3 tonos (editor: 3 opciones)
    p[P_HAIR_COLOR] = r.randrange(7)             # 7 colores (c01-c07)
    p[P_EYE_COLOR] = r.randrange(7)
    out = bytes(p)
    if not validate_parts(out, w):               # no debería pasar nunca; por si cambia el diccionario
        out = default_parts(s, w)
    return out


# ---------------------------------------------------------------- B65: retratos de NPC famosas (data/Avatar/npc/)
# Resultado de la investigación (ola 1, 2026-10-06): NO es un fallo y el servidor no tiene nada que mandar.
#  - FUN_009efd80(n) carga el retrato de medio cuerpo de una NPC «con ficha»: n = 0..0x12 elige uno de 19 .csv
#    (d01-d10, a01-a09; el mapa n -> fichero está en el switch del propio cliente). n lo decide el cliente; el
#    servidor solo manda el id de la NPC, y las piezas de 0x4230 de las NPC 51-94 no se usan.
#  - Cada .csv: 4 líneas de cabecera (la 4.ª, menos 1, debe ir de 0 a 8: si no, «NPC DATA ERROR») y 15 piezas.
#    Una pieza «****» es un hueco A PROPÓSITO: el cliente escribe el aviso de depuración «NPC[%d]データ[****]»
#    (cadena en 0xd1195c, solo al log) y, si la pieza es 9-14, apaga la animación que va con ella (+0x1d64..0x1d68:
#    parpadeo/boca). No hay ventana de error ni pieza por defecto.
#  - Lo visto en los logs (NPC 78 pieza 0 = a06.csv; NPC 72 piezas 2, 5 y 8 = a05.csv; también a07 piezas 0 y 3)
#    coincide uno a uno con los «****» de esos .csv, y todos los .png que nombran los 19 .csv existen en merge2008.
#    (d05.csv usa a propósito los .png de npc_d04: misma cabecera 8005/207/17/1.)
# npc_portrait_audit() lo vuelve a comprobar en la autoprueba por si alguien cambia la carpeta de datos.
NPC_PORTRAIT_DIR = os.path.join(_DATA, "Avatar", "npc")
if not os.path.isdir(NPC_PORTRAIT_DIR):          # en disco la carpeta es «avatar» (Windows no distingue)
    NPC_PORTRAIT_DIR = os.path.join(_DATA, "avatar", "npc")
NPC_PORTRAIT_CSVS = ["d%02d.csv" % i for i in range(1, 11)] + ["a%02d.csv" % i for i in range(1, 10)]


def npc_portrait_audit(folder=NPC_PORTRAIT_DIR):
    """{csv: {"head": [4 enteros] | None, "empty": [piezas «****»], "missing": [(pieza, png que falta)],
    "error": texto | None}}. Lee como el cliente (FUN_009efd80): se saltan las líneas vacías y las que empiezan
    por «//». Un .png que falte sí sería un fallo (pieza sin dibujar); «****» no."""
    out = {}
    try:
        names = {f.lower() for f in os.listdir(folder)}
    except OSError as e:
        return {"(carpeta)": {"head": None, "empty": [], "missing": [], "error": repr(e)}}
    for csv in NPC_PORTRAIT_CSVS:
        rep = {"head": None, "empty": [], "missing": [], "error": None}
        out[csv] = rep
        if csv not in names:
            rep["error"] = "no existe (el cliente diría «NpcFile Read Error»)"
            continue
        try:
            raw = open(os.path.join(folder, csv), "rb").read().decode("cp932", errors="replace")
        except OSError as e:
            rep["error"] = repr(e)
            continue
        lines = [ln.strip() for ln in raw.replace("\r", "").split("\n")]
        lines = [ln for ln in lines if ln and not ln.startswith("//")]
        try:
            rep["head"] = [int(x) for x in lines[:4]]
        except ValueError:
            rep["error"] = "cabecera no numérica: %r" % (lines[:4],)
            continue
        if len(rep["head"]) < 4 or not 0 <= rep["head"][3] - 1 <= 8:
            rep["error"] = "cabecera inválida (NPC DATA ERROR): %r" % (rep["head"],)
        for i, name in enumerate(lines[4:]):
            if name == "****":
                rep["empty"].append(i)
            elif os.path.basename(name).lower() not in names:
                rep["missing"].append((i, name))
    return out


# ---------------------------------------------------------------- mensajes de avatar
def parse_regavatarinfo(data):
    """0x4170 REGAVATARINFO (FUN_006cc060): u32 mundo, 16 B piezas, u32 (siempre 0). Lo manda el cliente al entrar
    en un bloque (estado 0xd/0xf de FUN_006d1840): en My Room primero el del OTRO mundo y luego el del actual;
    en las demás salas solo el actual. -> (mundo, piezas)."""
    world = struct.unpack(">I", data[:4])[0] if len(data) >= 4 else 0
    return world, bytes(data[4:20]).ljust(16, bytes(1))


def regavatar_result(code=0):
    """0x4171 (FUN_006ba340): s32 que el cliente guarda en +0x80c y no comprueba; solo desbloquea la espera."""
    return proto.W().u32(code).bytes()


def update_avatar_payload(pid, world, pieces):
    """0x4181 UPDATE_PLAYERAVATARINFO (FUN_006b9930): u32 id jugador, u32 mundo, 16 B. Cambia el avatar de un
    jugador de la lista del bloque (+0x134 + mundo*0x14) y marca refresco (+0x9b8). Para avisar a los demás."""
    return proto.W().u32(pid).u32(world).bytes() + bytes(pieces[:16]).ljust(16, bytes(1))


def player_entry_with_avatars(slot, name, domino=None, academia=None):
    """Entrada de 0x3012 PLAYERLIST (FUN_0068f520), 76 B; los dos últimos bloques de 16 B son los avatares de
    Domino (+0x134) y Academia (+0x148). Mismo orden que player_entry de run.py."""
    w = proto.W().u8(slot).s(name, 16).u8(0).u32(0).u32(0).u32(0).u16(0).u32(0)
    w.u16(0).u16(0).u16(0).u16(0)
    return w.bytes() + bytes(domino or default_parts(MALE, DOMINO))[:16] + \
        bytes(academia or default_parts(MALE, ACADEMIA))[:16]


def block_player2_entry(pid, pieces, kind=0, unk1=0, unk2=0, unk3=0, extra=None):
    """Registro de 0x4216 BLOCKPLAYERLIST2_DATA (FUN_006b8d10 + FUN_006b8de0): u32 id, u8 tipo (+0x66),
    16 B piezas (+0x68 -> avatar del mundo ACTUAL del jugador), u16, u16, u8. Si tipo es 'g'..'j' (0x67-0x6a)
    siguen 16 B, u8, u16, u16, u16 (extra, 23 B). 26 B con tipo 0."""
    w = proto.W().u32(pid).u8(kind).bytes() + bytes(pieces[:16]).ljust(16, bytes(1))
    w += proto.W().u16(unk1).u16(unk2).u8(unk3).bytes()
    if 0x67 <= kind <= 0x6a:
        w += (extra or bytes(23))[:23].ljust(23, bytes(1))
    return w


def block_player2_payload(entries):
    """entries = [(pid, piezas)] -> datos de UN mensaje 0x4216 (máx. 1024 B = 39 registros de 26 B)."""
    return b"".join(block_player2_entry(pid, pc) for pid, pc in entries)


def autoduelstart_payload(rival, name, rival_pieces=None):
    """0x43a0 AUTODUELSTART (FUN_006baab0): u32 id, nombre16, u8, u16, u16, 16 B AVATAR del rival (CAvatarData
    +0xb28), u8. run.py manda hoy ceros en el avatar."""
    return proto.W().u32(rival).s(name, 16).u8(0).u16(0).u16(0).bytes() + \
        bytes(rival_pieces or bytes(16))[:16].ljust(16, bytes(1)) + proto.W().u8(0).bytes()


# ---------------------------------------------------------------- PBIN (lo que tiene el jugador)
def default_pbin_bits():
    """Bits que todo jugador tiene de serie: piezas con columna «inicial» = 1 (clases 1 y 2)."""
    return sorted(i for i, (v, d, k, s, t, w) in PBIN.items() if d == 1 and k in (1, 2))


def pbin_bytes(bits):
    """128 B: bit i = byte[i >> 3] & (1 << (i & 7))  (FUN_00ad6a90 / FUN_00ad6900, LSB primero)."""
    b = bytearray(128)
    for i in bits:
        if 0 <= i < 1024:
            b[i >> 3] |= 1 << (i & 7)
    return bytes(b)


def itemget_result(pbin_bits=(), cards=()):
    """0x5205 ITEMGET_RESULT (FUN_006c2b30): u16 n + n×u16 bits PBIN que se ACTIVAN (FUN_00ad6900(i,1) y aviso de
    objeto conseguido), u16 m + m×u16 cartas (suma 1 a la colección)."""
    w = proto.W().u16(len(pbin_bits))
    for i in pbin_bits:
        w.u16(i)
    w.u16(len(cards))
    for c in cards:
        w.u16(c)
    return w.bytes()


# ---------------------------------------------------------------- tienda de avatar
SHOP_AVATAR_TYPE = 2   # CShopAvatarWindow (FUN_00afac40) solo enseña artículos de tipo 2; 3-5 no los usa nadie
# Precios inventados (el cliente no trae precios; los ponía el servidor).
AVATAR_PRICES = {P_HAIR_FRONT: 300, P_FACE_ACC: 200, P_NOSE: 100, P_EYES: 200, P_MOUTH: 100,
                 P_CLOTHES: 1000, P_HAIR_BACK: 300, P_DISK: 1500, P_GLASSES: 500}
PRESENT_TEXT_IDS = {3318, 3319, 3327, 3530, 3532, 3328, 3329, 3531, 3533, 3535, 3384, 3326, 3534}


def avatar_shop_items(first_iid, include_defaults=False, include_presents=True):
    """Artículos de 17 B (formato de FUN_006c1c70): u32 id artículo, u8 tipo=2, u32 param = ÍNDICE PBIN,
    u32 precio MP, 4×u8 (sin uso conocido, 0). Se mandan todas; la ventana filtra sola por sexo del jugador y por
    mundo (común o el actual) y marca «ya lo tienes» si el bit PBIN está puesto."""
    items, iid = [], first_iid
    for idx in sorted(PBIN):
        val, dflt, kind, slot, text, world = PBIN[idx]
        if kind not in (1, 2) or not 1 <= slot <= 9:
            continue
        if dflt and not include_defaults:
            continue
        if text in PRESENT_TEXT_IDS and not include_presents:
            continue
        if (slot in (P_FACE_ACC, P_GLASSES)) and val == 0x20:
            continue
        cost = AVATAR_PRICES.get(slot, 500) * (3 if text in PRESENT_TEXT_IDS else 1)
        items.append(proto.W().u32(iid).u8(SHOP_AVATAR_TYPE).u32(idx).u32(cost).u8(0).u8(0).u8(0).u8(0).bytes())
        iid += 1
    return items


def purchase_avatar(iid, item, num, mp, owned, notify=False):
    """Compra de un artículo de tipo 2. item = 17 B del catálogo; mp = saldo; owned = bits PBIN que ya tiene.
    -> (nuevo_saldo, bit_comprado | None, [(id_mensaje, datos)]). Los mensajes, en orden:
       0x5212 PURCHASE_SHOP_ITEM_RESULT: s32 0 + entrada de 13 B (u32 id artículo, u8 tipo 2, u32 bit PBIN, u8 1, 3×u8)
       0x5211 SHOPRESULTEND s32 0
       (el integrador añade 0x4140 PLAYERDATAUPDATE con el saldo nuevo, como en las cartas)

    B69 (ola 1): ya NO se manda 0x5205. Lo que decía la versión anterior («el cliente no activa el bit con 0x5212»)
    era cierto solo a medias: 0x5212 no lo pone, pero tras el 0x5211 la tienda pasa a CCardPackCheckScene
    (escena 0x19) y sus estados 0x30-0x34 recorren las entradas de tipo 2 de la lista de comprados, enseñan el muñeco
    y llaman a FUN_00ad6900(param, 1) (0x856369), que pone el bit de PBIN en local. Un 0x5205 de más mete el mismo
    bit en la cola de «¡HAS GANADO UN NUEVO OBJETO!» (DAT_012bb6b0+0x46c/0x470, estados 0x1f-0x24): doble aviso, o
    un aviso tardío en la siguiente CCardPackCheckScene porque llega detrás de 0x4140. El servidor sigue guardando el
    bit (run.py: PP.pbin_set) y lo devuelve en el próximo 0x3114 GETPBIN, así que no se pierde nada.
    notify=True recupera el 0x5205 (u16 1, u16 bit, u16 0; SIN cartas) por si la prueba T25 enseña que la prenda no
    sale como «ya la tienes» al volver a abrir la tienda. Nunca con cartas: ver hueco 6 de audit/tienda_coleccion.md."""
    kind = item[4] if len(item) > 4 else -1
    if len(item) < 13:
        idx = cost = -1
    else:
        idx = struct.unpack(">I", item[5:9])[0]
        cost = struct.unpack(">I", item[9:13])[0]      # se compra 1 aunque pidan más: es un bit
    fail = [(0x5212, proto.W().u32(1).bytes()), (0x5211, proto.W().u32(1).bytes())]
    if kind != SHOP_AVATAR_TYPE or idx not in PBIN or idx in owned or cost < 0 or mp < cost:
        return mp, None, fail
    entry = proto.W().u32(0).u32(iid).u8(kind).u32(idx).u8(1).u8(0).u8(0).u8(0).bytes()
    msgs = [(0x5212, entry), (0x5211, proto.W().u32(0).bytes())]
    if notify:
        msgs.append((0x5205, itemget_result([idx])))
    return mp - cost, idx, msgs


# ---------------------------------------------------------------- persistencia (state.json)
class AvatarStore:
    """Usa load_state/save_state de run.py. Claves nuevas en state.json:
         "avatars": {"<hueco jugador>": {"0": "<32 hex Domino>", "1": "<32 hex Academia>"}}
         "pbin": [bits conseguidos/comprados además de los de serie]"""

    def __init__(self, load_state, save_state):
        self.load, self.save = load_state, save_state

    def avatars(self, slot="0"):
        a = self.load().get("avatars", {}).get(str(slot), {})
        get = lambda w: bytes.fromhex(a[str(w)]) if str(w) in a else default_parts(MALE, w)  # noqa: E731
        return get(DOMINO), get(ACADEMIA)

    def set_avatar(self, slot, world, pieces):
        st = self.load()
        st.setdefault("avatars", {}).setdefault(str(slot), {})[str(world)] = bytes(pieces[:16]).hex()
        self.save(st)

    def owned_bits(self):
        return set(default_pbin_bits()) | set(self.load().get("pbin", []))

    def add_bit(self, i):
        st = self.load()
        st["pbin"] = sorted(set(st.get("pbin", [])) | {i})
        self.save(st)

    def pbin_payload(self):
        return pbin_bytes(self.owned_bits())


# ---------------------------------------------------------------- pruebas
def _fmt_size(ops):
    n = 0
    for f in ops:
        if isinstance(f, dict):
            n += _fmt_size(f["ops"])
        elif f in ("u8",):
            n += 1
        elif f == "u16":
            n += 2
        elif f in ("u32", "s32"):
            n += 4
        elif f.startswith("bytes") and f[5:].isdigit():
            n += int(f[5:])
    return n


def _selftest():
    import ast
    ast.parse(open(__file__, encoding="utf-8").read())
    proto_path = os.path.join(ORIG, "ghidra", "protocol.json")
    P = json.load(open(proto_path, encoding="utf-8"))
    R, S = P["recv"], P["send"]
    ok = True

    def check(name, got, want):
        nonlocal ok
        flag = "ok" if got == want else "MAL"
        ok &= got == want
        print("  %-48s %4d B  (protocol.json: %d)  %s" % (name, got, want, flag))

    def rec(msg):     # tamaño de un registro de LOOP (lo que va detrás de 'LOOP')
        r = R[msg]["reads"]
        return _fmt_size(r[r.index("LOOP") + 1:])

    def head(msg):
        r = R[msg]["reads"]
        return _fmt_size(r[:r.index("LOOP")]) if "LOOP" in r else _fmt_size(r)

    print("Tamaños:")
    assert len(PBIN) > 700, "no se encontró yo2pbinmap.dat (merge2008)"
    it = avatar_shop_items(100)
    check("0x5209 artículo de tienda (tipo 2)", len(it[0]), rec("0x5209"))
    mp, bit, msgs = purchase_avatar(100, it[0], 1, 10000, set())
    check("0x5212 resultado (s32 + 1 entrada)", len(msgs[0][1]), head("0x5212") + rec("0x5212"))
    check("0x5211", len(msgs[1][1]), _fmt_size(R["0x5211"]["reads"]))
    check("0x5205 con 1 bit y 1 carta", len(itemget_result([1], [1])), _fmt_size(R["0x5205"]["reads"]))
    check("0x4171", len(regavatar_result()), _fmt_size(R["0x4171"]["reads"]))
    check("0x4181", len(update_avatar_payload(1, 0, default_parts())), _fmt_size(R["0x4181"]["reads"]))
    check("0x3012 entrada", len(player_entry_with_avatars(0, "Player1")), rec("0x3012"))
    check("0x3114 PBIN", len(pbin_bytes(default_pbin_bits())), _fmt_size(R["0x3114"]["reads"]))
    full = block_player2_entry(1, default_parts(), kind=0x67)
    check("0x4216 registro tipo 'g' (con extra)", len(full), rec("0x4216"))
    print("  0x4216 registro tipo 0 (sin extra, condicional en FUN_006b8de0): %d B" % len(block_player2_entry(1, default_parts())))
    check("0x43a0 AUTODUELSTART", len(autoduelstart_payload(5, "x", npc_parts(5, "M"))), _fmt_size(R["0x43a0"]["reads"]))
    check("0x4170 (lo que manda el cliente)", 24, _fmt_size(S["0x4170"]["writes"]))
    w, pc = parse_regavatarinfo(bytes.fromhex("00000001" "0000203040608090b820000000000000" "00000000"))
    assert (w, pc) == (1, bytes.fromhex("0000203040608090b820000000000000"))

    print("B69 (compra de prenda sin 0x5205):")
    cost0 = struct.unpack(">I", it[0][9:13])[0]
    ids = [m for m, _ in msgs]
    assert ids == [0x5212, 0x5211], ids
    assert bit == struct.unpack(">I", it[0][5:9])[0] and mp == 10000 - cost0
    assert msgs[0][1][:4] == bytes(4) and msgs[1][1] == bytes(4), "resultado 0 en 0x5212 y 0x5211"
    _, bit2, msgs2 = purchase_avatar(100, it[0], 1, 10000, set(), notify=True)
    assert [m for m, _ in msgs2] == [0x5212, 0x5211, 0x5205] and msgs2[2][1] == itemget_result([bit2])
    assert msgs2[2][1][-2:] == bytes(2), "el 0x5205 de reserva no lleva cartas"
    for args in ((it[0], 1, 10000, {bit}), (it[0], 1, cost0 - 1, set()), (it[0][:8], 1, 10000, set()),
                 (b"\x00" * 4 + b"\x01" + it[0][5:], 1, 10000, set())):
        mp_f, bit_f, msgs_f = purchase_avatar(100, *args)
        assert bit_f is None and mp_f == args[2] and [m for m, _ in msgs_f] == [0x5212, 0x5211], args
        assert all(d == proto.W().u32(1).bytes() for _, d in msgs_f)
    print("  compra buena: 0x5212 + 0x5211 (sin 0x5205); notify=True: + 0x5205 sin cartas; "
          "ya la tiene / sin saldo / artículo corto / tipo 1: resultado 1, saldo intacto: ok")

    print("B65 (retratos de NPC famosas, %s):" % os.path.relpath(NPC_PORTRAIT_DIR, ORIG))
    aud = npc_portrait_audit()
    errs = {k: v["error"] for k, v in aud.items() if v["error"]}
    miss = {k: v["missing"] for k, v in aud.items() if v["missing"]}
    n_ok, n_miss = len(aud) - len(errs), sum(len(v) for v in miss.values())
    flag = n_ok == len(NPC_PORTRAIT_CSVS) and n_miss == 0
    ok &= flag
    print("  .csv leídos sin error: %d de %d; .png que faltan: %d  %s"
          % (n_ok, len(NPC_PORTRAIT_CSVS), n_miss, "ok" if flag else "MAL"))
    for k, v in sorted(miss.items()):
        print("    FALTA %s: %r" % (k, v))
    for k, v in sorted(errs.items()):
        print("    ERROR %s: %s" % (k, v))
    seen_in_logs = {"a06.csv": [0], "a05.csv": [2, 5, 8], "a07.csv": [0, 3]}   # NPC 78, NPC 72 y otra (logs 06-10)
    for k, pieces in seen_in_logs.items():
        got = aud.get(k, {}).get("empty", [])
        flag = all(x in got for x in pieces)
        ok &= flag
        print("  %s: huecos «****» %s; los del log %s -> %s" % (k, got, pieces, "ok" if flag else "MAL"))
    print("  huecos «****» en total: %d (normales: el cliente solo los anota en el log)"
          % sum(len(v["empty"]) for v in aud.values()))

    print("Validación (réplica de FUN_00a58cc0):")
    player_aca = bytes.fromhex("0000203040608090b820000000000000")
    player_dom = bytes.fromhex("0000203040607090b020000000000000")
    assert validate_parts(player_aca, ACADEMIA) and validate_parts(player_dom, DOMINO)
    assert not validate_parts(player_aca, DOMINO), "ropa/disco de Academia en Domino debe fallar"
    for s in (MALE, FEMALE):
        for w_ in (DOMINO, ACADEMIA):
            assert validate_parts(default_parts(s, w_), w_)
    print("  piezas de Player1 y por defecto: ok")
    try:
        sys.path.insert(0, EMU)
        info = {d["id"]: d for d in json.load(open(os.path.join(EMU, "npc_ids.json"), encoding="utf-8"))}
    except OSError:
        info = {}
    normals = [i for i in range(1, 99) if not 51 <= i <= 94]

    def old_npc_parts(nid, sex):   # la de run.py hoy, para comparar
        r = (nid * 2654435761) & 0xffffffff
        pick = lambda k, n: (r >> (k * 3)) % n  # noqa: E731
        clothes = (0x80 if pick(0, 2) else 0x70) + pick(1, 4)
        return bytes([1 if sex == "F" else 0, pick(2, 6), 0x20, 0x30, 0x40 + pick(3, 4), 0x60 + pick(4, 4), clothes,
                      0x90 + pick(5, 4), 0xb8, 0x20, 0, 0, 0, pick(6, 3), pick(7, 6), pick(8, 4)])
    for w_ in (DOMINO, ACADEMIA):
        new = [npc_parts(i, info.get(i, {}).get("sex"), w_) for i in normals]
        bad_new = sum(not validate_parts(p, w_) for p in new)
        bad_old = sum(not validate_parts(old_npc_parts(i, info.get(i, {}).get("sex")), w_) for i in normals)
        print("  mundo %-8s NPC normales: %d; inválidas -> avatar por defecto: nueva %d, run.py actual %d; "
              "distintas %d" % (("Domino", "Academia")[w_], len(normals), bad_new, bad_old, len(set(new))))
        ok &= bad_new == 0
    print("  bits PBIN de serie: %d; artículos de tienda de avatar: %d" % (len(default_pbin_bits()), len(it)))
    print("TODO OK" if ok else "HAY FALLOS")
    return ok


if __name__ == "__main__":
    sys.exit(0 if _selftest() else 1)
