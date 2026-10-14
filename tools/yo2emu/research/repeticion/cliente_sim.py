"""Lo que hace yo2.exe con el registro de duelo, reescrito a partir del descompilado (independiente de duellog.py,
para las pruebas). Direcciones de yo2.exe 2008-11; ND = objeto CNetDuel (*(*(0x012711ac)+0xc70)).

  on_6010   FUN_006944b0   cabecera (big-endian) + calloc(+0xbcec + 0x40)            -> contesta 0x6013
  on_6011   FUN_006947a0   pega bytes hasta +0xbcec (si no se canceló: +0x678 & 2)     -> contesta 0x6013
  on_6016   FUN_00694340   +0xd790 = u32, +0xd78c = 0                                   -> contesta 0x6013
  on_6017   FUN_00694390   pares u16,u16 en +0xcf8c                                     -> contesta 0x6013
  on_6012   FUN_00694840   escribe <título>.log y la línea de list.txt; +0x678 |= 1     (no contesta)
  load_log  FUN_00697210   lee el .log (RLE si versión >= 0xd6)
  decode    FUN_00695430   decodifica el blob (little-endian)
  replay_queue FUN_0079d0f0 qué entradas llegan a la cola 0x6202 en la repetición
"""
import struct

LIST_FMT = "%s %s.log %d %d %s %d %s %d 0 0 %d %d %d %d %d 0 0 0 0 0 0 0 0"   # 0xc9af38 (C)


class Rd:
    def __init__(self, b, be=True):
        self.b, self.o, self.e = b, 0, ">" if be else "<"

    def take(self, n):
        if self.o + n > len(self.b):
            raise ValueError(f"DuelLogDecordInfo error [CheckGetBuffer] en {self.o}+{n} > {len(self.b)}")
        v = self.b[self.o:self.o + n]
        self.o += n
        return v

    def u8(self): return self.take(1)[0]
    def u16(self): return struct.unpack(self.e + "H", self.take(2))[0]
    def u32(self): return struct.unpack(self.e + "I", self.take(4))[0]

    def cstr(self):
        k = self.b.index(b"\0", self.o)
        s = self.b[self.o:k]
        self.o = k + 1
        return s


def cstr16(b):
    return b.split(b"\0")[0].decode("latin1")


class Client:
    def __init__(self):
        self.nd = {"d78c": 0x7FFF, "678": 0}          # 0x7fff = «basura» sin 0x6016
        self.acks = 0
        self.files = {}

    def on(self, mid, data):
        getattr(self, "on_%04x" % mid)(data)

    def on_6010(self, d):
        r = Rd(d)
        n = self.nd
        n["ba94"], n["ba96"] = r.u16(), r.u16()
        n["title"] = r.cstr().decode("latin1")
        n["bcec"], n["bcf8"], n["ba98"], n["bbbc"], n["bbc0"] = (r.u32() for _ in range(5))
        n["name0"], n["name1"] = cstr16(r.take(16)), cstr16(r.take(16))
        n["ba9c"] = r.u32()
        r.take(4)                                        # FUN_0068a930 ESCRIBE aquí: +0xbba8 se queda en 0
        n["bba8"] = 0
        n["bbac"] = r.u32()
        n["bbb0"], n["bbb1"], n["bbb2"] = r.u8(), r.u8(), r.u8()
        n["buf"], n["bce8"] = bytearray(), 0
        self.acks += 1

    def on_6011(self, d):
        n = self.nd
        assert len(d) <= 0x400, "el lector se para en el cursor 0x400"
        for x in d:
            if n["bce8"] >= n["bcec"] or n["678"] & 2:
                break
            n["buf"].append(x)
            n["bce8"] += 1
        self.acks += 1

    def on_6016(self, d):
        self.nd["d790"] = struct.unpack(">I", d[:4])[0]
        self.nd["d78c"] = 0
        self.nd["si"] = []
        self.acks += 1

    def on_6017(self, d):
        r = Rd(d)
        while r.o < len(d) and self.nd["d78c"] < self.nd["d790"]:
            self.nd["si"].append((r.u16(), r.u16()))
            self.nd["d78c"] += 1
        self.acks += 1

    def on_6012(self, d):
        n = self.nd
        if n["678"] & 2:
            return
        if n["d78c"] * 8 > 0x1f7:                    # búfer de pila de 0x7d*4+3 B en esp+0xd5 (desde 0xcc: 503 B)
            raise OverflowError(f"FUN_00694840: {n['d78c']} entradas de búsqueda desbordan la pila")
        hx = ""
        for a, b in n.get("si", [])[:n["d78c"]]:
            hx += "".join("%X" % ((v >> s) & 0xF) for v in (a, b) for s in (0, 4, 8, 12))
        hx = hx or "00000000"
        line = LIST_FMT % (hx, n["title"], n["ba98"], n["bbbc"], n["name0"], n["bbc0"], n["name1"], n["ba9c"],
                           n["bba8"], n["bbac"], n["bbb0"], n["bbb1"], n["bbb2"])
        log = (struct.pack("<HHI", n["ba94"], n["ba96"], 0x28) + bytes(32) + struct.pack("<I", n["bcf8"])
               + bytes(n["buf"][:n["bcec"]]) + bytes(max(0, n["bcec"] - len(n["buf"]))))   # calloc: resto a 0
        self.files[n["title"] + ".log"] = log
        self.files.setdefault("list.txt", []).append(line)
        n["678"] |= 1


def load_log(f):
    """FUN_00697210: -> (ba94, ba96, blob decodificado)."""
    ba94, ba96, hsz = struct.unpack_from("<HHI", f, 0)
    if ba96 < 0xD6:
        return ba94, ba96, f[0x28:]
    size = struct.unpack_from("<I", f, 0x28)[0]
    if size > 0x9FFFFF:
        raise ValueError("DuelLogDecordInfo error [DUELLOG_DECORDBUFFER]")
    out = bytearray()
    body = f[0x2C:]
    for k in range(0, len(body) - (len(body) & 1), 2):
        out += bytes([body[k]]) * body[k + 1]
        if len(out) > size:
            raise ValueError("DuelLogDecordInfo error [Outside assumption 001]")
    return ba94, ba96, bytes(out)


def decode(ba94, ba96, blob):
    """FUN_00695430 (orden de lectura del desensamblado 0x69554c-0x6965f0)."""
    if not (ba94 == 0x80A and 200 < ba96 < 0xD9):
        raise ValueError("versión no aceptada (el cliente no decodifica)")
    r = Rd(blob, be=False)
    d = {}
    for _ in range(8):
        r.u32()
    r.take(32)
    d["ba9c"], d["baa0"], d["ba98"] = r.u32(), r.u8(), r.u32()
    r.u32()
    d["bba2_5"] = [r.u8() for _ in range(4)]
    d["bbac"] = r.u32()
    d["bbb0_2"] = [r.u8() for _ in range(3)]
    d["bbe6_8"] = [r.u8() for _ in range(3)]
    r.take(7 + 32)
    d["players"] = []
    for _ in range(2):
        p = {"pid": r.u32(), "listval": r.u32(), "name": cstr16(r.take(16)),
             "main": r.u32(), "extra": r.u32(), "bgm": r.u32(), "u8s": [r.u8() for _ in range(4)], "u32b": r.u32(),
             "parts": [r.take(32) for _ in range(3)], "country": r.take(4)}
        r.take(28)
        d["players"].append(p)
    r.take(1)
    n = r.u32()
    r.u32(); r.u32(); r.take(32)
    d["entries"] = []
    for _ in range(n):
        a, b, side, x1, x2 = r.u32(), r.u32(), r.u8(), r.u8(), r.u16()
        p = [r.u32() for _ in range(8)]
        d["entries"].append({"side": side, "x1": x1, "x2": x2, "cmd": [a, b] + p})
    nf, mf = r.u32(), r.u32()
    d["fusion"] = ([r.u16() for _ in range(nf)], [r.u16() for _ in range(nf)], [r.u32() for _ in range(mf)])
    ns = r.u32()
    d["search"] = [(r.u16(), r.u16()) for _ in range(ns)]
    d["rest"] = len(blob) - r.o
    return d


def room_world(kind, current=0):
    """FUN_00a587a0: tipo de sala -> mundo; otro valor = aserto «Lobby Rank Setting Error» (L693) y exit(1)."""
    if kind in (1, 9, 10, 0xB, 0xE, 0x10, 0x11, 0x14):
        return 1
    if kind in (7, 8, 0xD, 0xF):
        return 0
    if kind == 0xC:
        return current
    raise RuntimeError("Lobby Rank Setting Error. (yo2GameManager.cpp L693)")


def can_play(d):
    """Lanzador de la repetición (FUN_00770f30 / FUN_0064eff0): mundo de +0xbba3 y modo +0xba98 en 3/4."""
    room_world(d["bba2_5"][1])
    return d["ba98"] in (3, 4)


def viewer_side(d, my_pid):
    """+0xbaa1: 1 si mi pid (perfil +0xc) == pid del bloque 1 (+0xbbb8); entonces se intercambian los bloques."""
    return 1 if my_pid == d["players"][1]["pid"] else 0


def viewer_view(d, my_pid):
    """Lo que queda en ND tras FUN_00695430 visto por my_pid (0x6965f0-0x6969ff): si mi pid es el del bloque 1
    (+0xbaa1 = 1) intercambia los dos bloques entero (pid, nombre, mazos, música, los 4 u8 +0xbc04..7 <-> +0xbc08..b,
    piezas, país) y +0xbbe6..8: 0 -> 1, 1 -> 0, otro -> 0xff. Así el que mira es siempre el bloque 0 (C)."""
    v = dict(d)
    if viewer_side(d, my_pid) == 1:
        v["players"] = [d["players"][1], d["players"][0]]
        v["bbe6_8"] = [1 if g == 0 else 0 if g == 1 else 0xFF for g in d["bbe6_8"]]
    return v


def replay_janken(d, my_pid, game=1):
    """Piedra-papel-tijera de la repetición (CJankenScene, FUN_007c76b0 con ND+0x678 & 4; C):
    - estado 2, pasados 0x3c fotogramas: +0x8ec (mi mano) = ND+0xbc04 y +0x8f4 (la del otro) = ND+0xbc08, es decir,
      el 1.er u8 de los «4×u8» de cada bloque; ni 0x6101 ni 0x6102 (no hay servidor).
    - estado 4: +0x8f8 = (otro − mía) mod 3: 1 = gano yo, 2 = gana el otro, 0 = EMPATE -> estado 5 -> vuelve al
      estado 2, que vuelve a leer LAS MISMAS manos: bucle sin fin (lo que vio el owner el 07-10 con +0xbc04 = +0xbc08
      = 0 en todos los registros).
    - estado 7: +0x900 = ND[+0xbbe5 + FUN_00790700()] (= DAT_012a9088, partida del match, 1 en modo 10) = quién
      empieza (0 = el que mira) -> DAT_012a907c (research/lockstep §0.4). 0xff = -1: sin quién empieza.
    Devuelve {"mia", "otra", "gano", "empieza"}; lanza RuntimeError donde el cliente se quedaría atascado."""
    v = viewer_view(d, my_pid)
    mine, opp = v["players"][0]["u8s"][0], v["players"][1]["u8s"][0]
    if mine > 2 or opp > 2:
        raise RuntimeError(f"mano fuera de 0..2 (+0xbc04 = {mine}, +0xbc08 = {opp}): índice de animación malo")
    r = (opp - mine) % 3
    if r == 0:
        raise RuntimeError(f"piedra-papel-tijera en bucle: empate con las mismas manos ({mine}, {opp}) en cada vuelta")
    first = v["bbe6_8"][game - 1]
    if first not in (0, 1):
        raise RuntimeError(f"+0xbbe6 + {game - 1} = 0x{first:02x}: +0x900 = -1, nadie empieza")
    return {"mia": mine, "otra": opp, "gano": r == 1, "empieza": first}


def replay_queue(d, side):
    """FUN_0079d0f0: entradas del lado que se ve, tipo <= 0x70 y (tipo < 10 o (tipo > 0xf y tipo != 0x1b))."""
    out = []
    for e in d["entries"]:
        t = e["cmd"][0]
        if e["side"] != side or t > 0x70:
            continue
        if t < 10 or (t > 0xF and t != 0x1B):
            out.append(e["cmd"])
    return out
