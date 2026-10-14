"""Cliente falso de YO2 en Python (PvP fase 1): habla el protocolo con proto.pack/proto.Reader, numera sus mensajes
como el cliente real y COMPRUEBA la numeración que le llega del servidor (FUN_0068bac0 exige contador + 1 por
conexión; en la puerta 5730, la cuenta sigue entre conexiones del mismo cliente).

Uso (todo contra una instancia de PRUEBA, nunca contra la viva del owner):

  python fake_client.py prueba [--offset 100]
      copia state.json a logs/prueba/state-prueba.json, arranca run.py con YO2_NO_CLIENT=1, YO2_PORT_OFFSET,
      YO2_STATE y YO2_LOGDIR=logs/prueba, pasa «dos» y «replay» y para SOLO esa instancia.
  python fake_client.py pvp [--offset 100]
      instancia de prueba con YO2_PVP_TEST=1: reto rechazado, reto aceptado + duelo entero con «auto» (0x7ffe) y
      rendición, con dos clientes falsos desde 127.0.0.2 y 127.0.0.3.
  python fake_client.py pvpmatch [--offset 100]
      match al mejor de 3 (pvp_match.PVP_MATCH, sala dstyle 4) con YO2_PVP_TEST=1: 2-0, 2-1 y desconexión entre
      partidas; las partidas se acaban con 0x7ffd (prueba: «pierdo esta partida»).
  python fake_client.py pvptiempo [--offset 100]
      límite de tiempo en PvP (research/traductor/TIEMPO.md «Implementado») con YO2_TURN_TIMER=6,
      YO2_TIEMPO_RESPUESTA=3, YO2_TIEMPO_MARGEN=0.5 (si no vienen ya en el entorno): turno agotado, duelo rápido,
      sala OFF y NPC sin límite.
  python fake_client.py descarte [--offset 100]
      descarte por límite de mano contra la NPC (research/traductor/NOTES.md §20): mazo de Pot of Greed, pasa turnos,
      pulsa cartas con el panel CUDiscardDialog abierto y comprueba la cuenta, el cementerio y que no pasa de turno.
  python fake_client.py dos [--offset 100]          dos jugadores (A = cuenta del owner, B = cuenta nueva)
  python fake_client.py replay [--offset 100] [--log logs/<x>.log]
      repite el login de un log vivo (puerta -> cuentas -> lobby -> sala -> mapa, hasta el primer reto 0x4288) y
      compara, mensaje a mensaje, los ids que contesta el servidor con los del log.
"""
import argparse
import glob
import hashlib
import os
import queue
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import proto  # noqa: E402

VERSION = b"Ver.080912.00"
OWNER_ACCT = os.environ.get("YO2_OWNER_ACCT", "0000000000000000")   # como run.py

# Paridad de bytes (REESTRUCTURA.md §3.3, paridad/): con YO2_TAP=<carpeta>, cada Link apunta en
# <carpeta>/tap-<pid>.jsonl lo que recibe (rx) y lo que manda (tx): cliente, puerto sin offset, nº de conexión de ese
# cliente a ese puerto, índice, id, param y datos en hex. Sin la variable (lo normal) no se hace nada.
_TAP = {"lock": threading.Lock(), "f": None, "dir": None, "con": {}}


def _tap_con(link):
    """Nº de conexión (0, 1, ...) de este cliente a este puerto, o None si el tap está apagado."""
    d = os.environ.get("YO2_TAP")
    if not d:
        return None
    key = (link.cli.name, link.port - link.cli.off)
    with _TAP["lock"]:
        n = _TAP["con"].get(key, -1) + 1
        _TAP["con"][key] = n
    return n


def _tap(link, way, idx, mid, param, data, ok=True):
    d = os.environ.get("YO2_TAP")
    if not d or link.tap_con is None:
        return
    import json
    rec = {"cli": link.cli.name, "puerto": link.port - link.cli.off, "con": link.tap_con, "dir": way, "i": idx,
           "id": mid, "param": param, "hex": bytes(data).hex(), "t": round(time.time(), 4)}
    if not ok:
        rec["md5mal"] = True
    with _TAP["lock"]:
        if _TAP["f"] is None or _TAP["dir"] != d:
            os.makedirs(d, exist_ok=True)
            _TAP["f"] = open(os.path.join(d, f"tap-{os.getpid()}.jsonl"), "a", encoding="utf-8", newline="\n")
            _TAP["dir"] = d
        _TAP["f"].write(json.dumps(rec) + "\n")
        _TAP["f"].flush()


class Link:
    """Una conexión TCP al servidor. seq_box = [contador] compartido (puerta) o propio."""

    def __init__(self, cli, port, seq_box=None):
        self.cli, self.port = cli, port
        self.sock = socket.socket()
        if cli.src_ip:
            self.sock.bind((cli.src_ip, 0))
        self.sock.connect((cli.host, port))
        self.sock.settimeout(0.2)
        self.out = 0                               # numeración de lo que mandamos (el servidor solo mira el #1)
        self.seq_box = seq_box if seq_box is not None else [0]
        self.msgs = []                             # (id, param, datos) recibidos
        self.cv = threading.Condition()
        self.rd = proto.Reader()
        self.closed = False
        # carga.py: cli.on_msg(link, mid, param, datos) se llama con cada mensaje (tras comprobar la numeración); si
        # devuelve True, el mensaje no se guarda en msgs (difusiones de otros: sin esto la lista crece sin fin).
        # Sin on_msg (todos los modos de siempre), nada cambia.
        self.on_msg = getattr(cli, "on_msg", None)
        self.tap_con = _tap_con(self)              # paridad: None = tap apagado (YO2_TAP sin poner)
        self.tap_rx = 0
        threading.Thread(target=self._rx, daemon=True).start()

    def _rx(self):
        while not self.closed:
            try:
                d = self.sock.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            if not d:
                break
            for mid, param, data, ok in self.rd.feed(d):
                want = self.seq_box[0] + 1
                if param != want or not ok:
                    self.cli.errors.append(f"{self.cli.name} puerto {self.port}: 0x{mid:04x} llega con #{param}, "
                                           f"esperaba #{want}{'' if ok else ' (MD5 MAL)'}")
                self.seq_box[0] = param
                if self.tap_con is not None:
                    _tap(self, "rx", self.tap_rx, mid, param, data, ok)
                    self.tap_rx += 1
                if self.on_msg is not None and self.on_msg(self, mid, param, data):
                    continue
                with self.cv:
                    self.msgs.append((mid, param, data))
                    self.cv.notify_all()
        self.closed = True

    def send(self, mid, data=b""):
        self.out += 1
        if self.tap_con is not None:
            _tap(self, "tx", self.out, mid, self.out, data)
        self.sock.sendall(proto.pack(mid, bytes(data), self.out))

    def wait(self, mid, timeout=5.0, start=0, pred=None):
        """Primer mensaje mid recibido desde el índice start (y que cumpla pred). None si no llega."""
        end = time.time() + timeout
        with self.cv:
            while True:
                for i in range(start, len(self.msgs)):
                    m = self.msgs[i]
                    if m[0] == mid and (pred is None or pred(m[2])):
                        return m[2]
                left = end - time.time()
                if left <= 0:
                    return None
                self.cv.wait(left)

    def all(self, mid, start=0):
        with self.cv:
            return [m[2] for m in self.msgs[start:] if m[0] == mid]

    def close(self):
        self.closed = True
        try:
            self.sock.close()
        except OSError:
            pass


class FakeClient:
    def __init__(self, name, key, host="127.0.0.1", offset=0, src_ip=None):
        assert len(key) == 32
        self.name, self.key, self.host, self.off, self.src_ip = name, key, host, offset, src_ip
        self.gate_seq = [0]                        # la puerta sigue la cuenta entre conexiones
        self.gate_out = 0
        self.errors = []
        self.pid = None
        self.room = None
        self.wait_s = 5.0                          # espera de gate/account/enter_room (carga.py la sube con carga alta)

    def P(self, base):
        return base + self.off

    def gate(self):
        g = Link(self, self.P(5730), self.gate_seq)
        g.out = self.gate_out
        g.send(0x2005, VERSION)
        ok = g.wait(0x2004, timeout=self.wait_s) is not None
        g.send(0x0003)
        self.gate_out = g.out
        time.sleep(0.1)
        g.close()
        return ok

    def account(self, slot=0, create_name=None):
        a = Link(self, self.P(5731))
        a.send(0x3001); a.wait(0x3002, timeout=self.wait_s)
        a.send(0x3003, self.key); a.wait(0x3004, timeout=self.wait_s)
        n = len(a.msgs)
        a.send(0x3010)
        pl = a.wait(0x3012, start=n, timeout=self.wait_s) or b""
        names = {pl[4 + i * 73]: pl[5 + i * 73:21 + i * 73].split(b"\0")[0].decode("latin1")
                 for i in range((len(pl) - 4) // 73)} if len(pl) >= 4 else {}
        if slot not in names and create_name:
            n = len(a.msgs)
            a.send(0x3020, bytes([slot]) + create_name.encode("latin1")[:15].ljust(16, b"\0"))
            a.wait(0x3022, start=n, timeout=self.wait_s)
            names[slot] = create_name
        n = len(a.msgs)
        a.send(0x3100, bytes([slot])); a.wait(0x3103, start=n, timeout=max(8, self.wait_s))
        n = len(a.msgs)
        a.send(0x3114, bytes([slot])); a.wait(0x4234, start=n, timeout=self.wait_s)
        a.send(0x0003)
        time.sleep(0.1)
        a.close()
        self.names = names
        return names

    def gate_rooms(self):
        """{(b1, b2): (puerto, jugadores)} de las salas de la lista de la puerta (0x2003, tipo 3, sin My Room)."""
        g = Link(self, self.P(5730), self.gate_seq)
        g.out = self.gate_out
        g.send(0x2005, VERSION)
        g.wait(0x2004, timeout=self.wait_s)
        raw = b"".join(g.all(0x2003))
        g.send(0x0003)
        self.gate_out = g.out
        time.sleep(0.1)
        g.close()
        out = {}
        for i in range(0, len(raw) - 47, 48):
            e = raw[i:i + 48]
            if int.from_bytes(e[4:8], "big") == 3 and e[44] != 12:
                out[(e[43], e[44])] = (int.from_bytes(e[39:41], "big"), int.from_bytes(e[41:43], "big"))
        return out

    def leave_room(self):
        if self.room is not None:
            self.room.send(0x0003)
            time.sleep(0.2)
            self.room.close()
            self.room = None

    def enter_room(self, kind_index=0, lang=5, slot=0, parts=None, port=None):
        """port = puerto absoluto de la sala (de gate_rooms); si no, el de siempre 5740 + kind_index (+ offset)."""
        r = self.room = Link(self, port if port is not None else self.P(5740) + kind_index)
        r.send(0x3001); r.wait(0x3002, timeout=self.wait_s)
        r.send(0x3003, self.key); r.wait(0x3004, timeout=self.wait_s)
        n = len(r.msgs)
        r.send(0x4100, bytes([lang, slot]))
        res = r.wait(0x4101, start=n, timeout=self.wait_s) or b""
        self.pid = struct.unpack(">I", res[4:8])[0] if len(res) >= 8 else None
        r.send(0x4200); self.blocklist = r.wait(0x4201, timeout=self.wait_s) or b""
        n = len(r.msgs)
        r.send(0x4202, b"\0\0")
        r.wait(0x4213, start=n, timeout=self.wait_s)
        self.enter_list = list1_pids(r.all(0x4212, n))
        self.enter_res = r.all(0x4203, n)
        n = len(r.msgs)
        r.send(0x4170, struct.pack(">I", 1) + (parts or bytes(16)) + bytes(4)); r.wait(0x4171, start=n, timeout=self.wait_s)
        return self.pid

    def dpauth(self, code, timeout=8):
        """Ventana «Código de DUELPASS»: 0x8101 = texto en 16 B (FUN_006ce5c0) -> 0x8102 (s32, u16 carta, u8, u8 DPP,
        u16 mazo) o None. Por la conexión del lobby (enter_room antes)."""
        r = self.room
        n = len(r.msgs)
        r.send(0x8101, code.encode("latin1")[:16].ljust(16, b"\0"))
        d = r.wait(0x8102, start=n, timeout=timeout)
        return struct.unpack(">iHBBH", d[:10]) if d is not None and len(d) >= 10 else None

    def move(self, x, y, d=0):
        n = len(self.room.msgs)
        self.room.send(0x5300, struct.pack(">IHHBB", self.pid or 0, x, y, d, 0))
        return self.room.wait(0x5300, start=n)             # el eco propio

    def block_list(self):
        r = self.room
        n = len(r.msgs)
        r.send(0x4210, b"\xff\xff\xff\xff")
        r.wait(0x4213, start=n)
        time.sleep(0.3)
        return list1_pids(r.all(0x4212, n)), r.all(0x5300, n)

    def list2(self):
        r = self.room
        n = len(r.msgs)
        r.send(0x4214, b"\xff\xff\xff\xff")
        r.wait(0x4217, start=n)
        out = {}
        for d in r.all(0x4216, n):
            i = 0
            while i + 26 <= len(d):
                pid, st = struct.unpack(">IB", d[i:i + 5])
                x, y, di = struct.unpack(">HHB", d[i + 21:i + 26])
                out[pid] = (st, x, y, di)
                # como el cliente (FUN_006b8de0): 23 B tras un estado «en duelo» (pvp_hub.LIST2_DUEL_23; =0 -> 25)
                i += 26 + ((23 if os.environ.get("YO2_LIST2_DUEL_23", "1") not in ("", "0") else 25)
                           if 0x67 <= st <= 0x6a else 0)
        return out


def list1_pids(datas):
    out = []
    for d in datas:
        for i in range(0, len(d) - 35, 36):
            out.append((struct.unpack(">I", d[i:i + 4])[0], d[i + 4:i + 20].split(b"\0")[0].decode("latin1")))
    return out


# 07-10 (logseguro): el log ya no guarda los 32 B de 0x3003 del owner (antes salían de ahí). El «dueño» de las pruebas
# es un login INVENTADO (research/cuentas/cifrado.encrypt) que la instancia de prueba reconoce como el dueño por un
# alias en su copia del estado (state["logins"]["alias"][id] = owner_acct; prep_state). Contra una instancia que no
# se preparó así (dos/replay sueltos), A entra como cuenta nueva y las comprobaciones «del owner» fallan.
TEST_OWNER_LOGIN = ("duenoprueba", "clave-inventada1")    # credenciales de prueba, no de nadie (<= 16 B: MAX_CLAVE)


def _cifrado():
    import importlib.util
    m = sys.modules.get("yo2_cifrado")
    if m is None:
        sp = importlib.util.spec_from_file_location("yo2_cifrado", os.path.join(HERE, "research", "cuentas", "cifrado.py"))
        m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)
        sys.modules["yo2_cifrado"] = m
    return m


def owner_key_from_log():
    """(Nombre de antes.) Los 32 B de 0x3003 del «dueño» de las pruebas: cifrado.encrypt del login inventado."""
    return _cifrado().encrypt(*TEST_OWNER_LOGIN)


def copiar_estado(destino):
    """Copia state.json (el de este árbol) para una instancia de prueba. Sin state.json (árbol público, código abierto):
    estado nuevo, solo con la cartera del dueño (el jugador A de las pruebas entra como él) con los 10 DP de inicio de
    una cuenta nueva (persistencia NOTES §14): sin ellos el servidor rechaza sus retos (-612)."""
    origen = os.path.join(HERE, "state.json")
    if os.path.isfile(origen):
        shutil.copyfile(origen, destino)
    else:
        with open(destino, "w", encoding="utf-8", newline="\n") as f:
            f.write('{"wallet": {"mp": 0, "dpp": 10}}\n')


def prep_state(path):
    """Copia de estado de una instancia de prueba: el login inventado entra en la cuenta del dueño (alias)."""
    import json
    with open(path, encoding="utf-8") as f:
        st = json.load(f)
    owner = st.get("owner_acct") or OWNER_ACCT
    lg = st.setdefault("logins", {})
    lg.setdefault("alias", {})[_cifrado().account_id(owner_key_from_log())] = owner
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- prueba: dos jugadores
def check(results, name, cond, info=""):
    results.append((name, bool(cond)))
    print(f"  {'OK ' if cond else 'MAL'} {name}" + (f"  [{info}]" if info else ""))


def run_dos(offset, host="127.0.0.1"):
    print(f"== dos jugadores contra {host} (+{offset})")
    res = []
    okey = owner_key_from_log()
    A = FakeClient("A", okey or bytes(range(32)), host, offset, "127.0.0.2")
    B = FakeClient("B", bytes([0xA5] * 16 + list(range(16))), host, offset, "127.0.0.3")
    check(res, "puerta A", A.gate())
    check(res, "puerta B", B.gate())
    na = A.account(0)
    nb = B.account(0, create_name="Amigo")
    check(res, "A (cuenta del owner) ve su personaje de siempre", na.get(0) == "Player1" or okey is None, f"{na}")
    check(res, "B (cuenta nueva) tiene su propio personaje", nb.get(0) == "Amigo", f"{nb}")
    pa = A.enter_room(); pa_list = A.enter_list
    pb = B.enter_room(); pb_list = B.enter_list
    check(res, "pids distintos y >= 1000", pa and pb and pa != pb and min(pa, pb) >= 1000, f"A={pa} B={pb}")
    check(res, "el owner conserva pid 1000 (cuenta 0, hueco 0)", pa == 1000 or okey is None, f"{pa}")
    check(res, "B al entrar ve a A y a sí mismo en LIST1 (0x4212)",
          {pa, pb} <= {p for p, _ in pb_list}, f"{pb_list}")
    j = A.room.wait(0x3112, timeout=3, pred=lambda d: len(d) >= 6 and struct.unpack(">IB", d[:5]) == (pb, 1))
    # prueba 3: el estado de LIST2 (byte 42) tiene que ser 1: FUN_006b9480 lo toma como «op» y solo crea con 1
    check(res, "A recibe 0x3112 op 1 (entra B) de 100 B con estado 1", j is not None and len(j) == 100 and j[42] == 1,
          f"{len(j) if j else None} B")
    A.move(300, 200)
    B.move(123, 234, 3)
    mv = A.room.wait(0x5300, timeout=3, pred=lambda d: struct.unpack(">I", d[:4])[0] == pb)
    check(res, "A recibe la posición de B (0x5300)", mv is not None and struct.unpack(">IHHB", mv[:9]) == (pb, 123, 234, 3),
          mv.hex(" ") if mv else "nada")
    mb = B.room.wait(0x5300, timeout=3, pred=lambda d: struct.unpack(">I", d[:4])[0] == pa)
    check(res, "B recibe la posición de A (0x5300)", mb is not None and struct.unpack(">IHH", mb[:8]) == (pa, 300, 200))
    lst, poss = A.block_list()
    check(res, "0x4210 de A: LIST1 con los dos", {pa, pb} <= {p for p, _ in lst}, f"{lst}")
    check(res, "0x4210 de A: detrás, la última 0x5300 de B", any(struct.unpack(">I", d[:4])[0] == pb for d in poss))
    l2 = B.list2()
    check(res, "LIST2 de B: A con su posición", l2.get(pa, (0, 0, 0, 0))[1:3] == (300, 200), f"{l2}")
    check(res, "puerta otra vez (la cuenta sigue: sin desfase)", A.gate() and B.gate())
    n = len(A.room.msgs)
    B.room.send(0x0003); time.sleep(0.2); B.room.close()
    lv = A.room.wait(0x3112, timeout=3, start=n, pred=lambda d: d[:5] == struct.pack(">IB", pb, 0))
    check(res, "B sale: A recibe 0x3112 op 0", lv is not None)
    A.room.close()
    errs = A.errors + B.errors
    check(res, "numeración sin desfases en todas las conexiones de A y B", not errs, "; ".join(errs[:4]))
    return res


# ---------------------------------------------------------------- prueba: entrada no confiable
def run_basura(offset, host="127.0.0.1"):
    """Basura, cabecera de 60000 B, MD5 malo y mensajes cortos: el servidor corta esa conexión y sigue atendiendo."""
    print(f"== entrada no confiable contra +{offset}")
    res = []

    def closed_after(port, payload):
        s = socket.create_connection((host, port + offset), timeout=3)
        s.sendall(payload)
        try:
            return s.recv(1) == b""                # el servidor cierra
        except socket.timeout:
            return False
        except OSError:
            return True
        finally:
            s.close()

    big = proto.xor(struct.pack(">HHI", 0x3001, 60000, 1) + bytes(16))
    bad = bytearray(proto.pack(0x3001, b"", 1)); bad[10] ^= 0xff
    check(res, "cabecera con largo 60000: cierra", closed_after(5731, big))
    check(res, "MD5 malo: cierra", closed_after(5732, bytes(bad)))
    check(res, "basura sin formato (64 KB): cierra", closed_after(5740, os.urandom(65536)))
    # mensajes bien formados pero cortos o raros: no deben tumbar nada
    cli = FakeClient("X", bytes([7] * 32), host, offset, "127.0.0.5")
    r = Link(cli, 5740 + offset)
    for mid, d in [(0x3003, b"\1\2"), (0x4100, b""), (0x3003, bytes([7] * 32)), (0x4100, b"\5"), (0x4202, b""),
                   (0x5300, b"\0"), (0x5310, b""), (0x4170, b"\0\0"), (0x4172, b"\x09"), (0x5320, b""),
                   (0x4214, b""), (0x4288, b"\0"), (0x9999, bytes(1024))]:
        r.send(mid, d)
    time.sleep(1.5)
    r.close()
    A = FakeClient("Y", bytes([9] * 32), host, offset, "127.0.0.6")
    check(res, "después, un cliente normal sigue entrando", A.gate() and A.account(0, "Nuevo").get(0) == "Nuevo"
          and A.enter_room() is not None)
    A.room.close()
    return res


# ---------------------------------------------------------------- prueba: repetir el login de un log vivo
LINE = re.compile(r"^\[srv:(\d+)\] (<-|->) 0x([0-9a-f]{4}) #(\d+) len=(\d+)(?: md5=\w+)? ?([0-9a-f ]*)")


def parse_log(path, until=0x4288):
    """[(puerto, 'conn'|'close'|('msg', id, datos), [ids que contesta el servidor en ese puerto])] del 1.er login."""
    steps, started = [], False
    last = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r"^\[srv:(\d+)\] conexión de", line)
            if m:
                port = int(m.group(1))
                if port == 5730:
                    started = True
                if started:
                    if port == 5760:
                        break
                    steps.append([port, "conn", []])
                continue
            if not started:
                continue
            m = re.match(r"^\[srv:(\d+)\] cerrada", line)
            if m:
                steps.append([int(m.group(1)), "close", []]); continue
            m = LINE.match(line)
            if not m:
                continue
            port, way, mid, n = int(m.group(1)), m.group(2), int(m.group(3), 16), int(m.group(5))
            if way == "<-":
                if mid == until:
                    break
                if mid == 0x3003:                  # logseguro: el log solo lleva «cuenta <6 hex>»; va la clave del cliente
                    data = None
                else:
                    try:
                        data = bytes.fromhex(m.group(6).replace(" ", ""))[:n] if n else b""
                    except ValueError:             # contenido tapado (logseguro): ceros
                        data = b""
                    if len(data) != n:
                        data = data.ljust(n, b"\0")
                step = [port, ("msg", mid, data), []]
                steps.append(step); last[port] = step
            elif port in last:
                last[port][2].append(mid)
    return steps


# 07-10: también opcionales lo que el servidor manda de más desde el log congelado de la batería (06-10), todo
# aprobado: 0x5600 (LIMIT_LIST tras 0x4100/0x4200; lo comprueba aparte run_pvp), 0x4181 (My Room: el propio
# aspecto, YO2_MYROOM_AVATAR) y los DATA 0x4232 de NPCINFOLIST (cuántos bloques de 16 depende de cuántas NPC hay)
REPLAY_OPTIONAL = (0x4216, 0x421a, 0x421e, 0x4223, 0x5600, 0x4181, 0x4232)


def run_replay(offset, path, host="127.0.0.1"):
    print(f"== repetir el login de {os.path.basename(path)} contra +{offset}")
    steps = parse_log(path)
    cli = FakeClient("R", owner_key_from_log(), host, offset, "127.0.0.4")   # el log era del owner: su login de prueba
    links = {}
    diffs, total = [], 0
    for port, what, want in steps:
        if what == "conn":
            links.setdefault(port, []).append(Link(cli, port + offset, cli.gate_seq if port == 5730 else None))
            if port == 5730:
                links[port][-1].out = cli.gate_out
            continue
        if what == "close":
            if links.get(port):
                links[port].pop(0).close()
            continue
        if not links.get(port):
            continue
        lk = links[port][-1]
        _, mid, data = what
        if data is None:
            data = cli.key
        n = len(lk.msgs)
        lk.send(mid, data)
        if port == 5730:
            cli.gate_out = lk.out
        # prueba 3: LIST2..5 ahora llevan al propio jugador (pvp_hub.LIST_SELF): los DATA (0x4216/0x421a/0x421e/
        # 0x4223) pueden aparecer donde el log viejo no los tenía; se comparan START/END y lo demás
        want = [m for m in want if m not in REPLAY_OPTIONAL]
        end = time.time() + 0.6 + 0.3 * len(want)
        while time.time() < end and len([m for m in lk.msgs[n:] if m[0] not in REPLAY_OPTIONAL]) < len(want):
            time.sleep(0.05)
        time.sleep(0.15)
        got = [m[0] for m in lk.msgs[n:] if m[0] not in REPLAY_OPTIONAL]
        total += 1
        if got != want:
            diffs.append((port, mid, want, got))
    for lst in links.values():
        for lk in lst:
            lk.close()
    print(f"  {total} mensajes del cliente repetidos; {len(diffs)} con respuesta distinta a la del log")
    for port, mid, want, got in diffs[:12]:
        print(f"    {port} 0x{mid:04x}: log {[hex(x) for x in want]} / ahora {[hex(x) for x in got]}")
    for e in cli.errors[:6]:
        print("    numeración:", e)
    return [("replay: mismas respuestas", not diffs), ("replay: numeración", not cli.errors)]


# ---------------------------------------------------------------- prueba: retos y duelo PvP (fase 2)
# Los dos jugadores contestan sus decisiones con 0x7ffe («que decida bot.ts por mí»), que el servidor SOLO acepta con
# YO2_PVP_TEST=1 (instancia de prueba). Solo lo manda quien ha recibido una petición (0x6202 tipo 0x1a, diálogo 0x1b
# B=0x32, lista 0x1b B=0x33 o 0x6610): si un select fuera a la vista equivocada, el duelo se quedaría parado.
def _t6202(d):
    if len(d) < 8:
        return None, None
    t, b = struct.unpack(">II", d[:8])
    return t & 0xff, b


def is_prompt(mid, d):
    if mid == 0x6610:
        return True
    if mid != 0x6202:
        return False
    t, b = _t6202(d)
    return t == 0x1a or (t == 0x1b and b in (0x32, 0x33))


def npc_deck_ids(nid):
    """Mazo de la NPC nid (research/ia/npc_decks.json) para los jugadores falsos. Sin esa tabla (árbol público: sale de
    los ficheros del juego y no va en el código abierto), un mazo fijo de ids YO2 1-15 (los de ocg_test.py), de largo
    distinto por NPC para que las comprobaciones de «tamaño del mazo del OTRO» sigan distinguiendo a los dos."""
    import json
    ruta = os.path.join(HERE, "research", "ia", "npc_decks.json")
    if not os.path.isfile(ruta):
        base = [i for i in range(1, 16) if i != 10 for _ in range(3)]          # 42 cartas
        return base[:40 + (int(nid) % 2)], []
    d = json.load(open(ruta, encoding="utf-8"))[str(nid)]
    return list(d["main"]), list(d.get("extra") or [])


def room_msg(rival=0xffffffff, dstyle=3):
    """0x4288 REQCREATEROOM, 28 B: u8 dstyle, u8 wmode, u8 mstat, u8 con contraseña, 16 B, 4×u8, u32 rival.
    El cliente real manda rival = 0xffffffff al abrir sala desde el Menú de Duelo (log 20261006-160049).
    dstyle 4 = sala de match al mejor de 3 (log 20261009-120748, pvp-3)."""
    return bytes([dstyle, 0, 0, 0]) + bytes(16) + bytes([1, 1, 1, 0]) + struct.pack(">I", rival)


def duel_link(cli, deck):
    main, extra = deck
    d = cli.duel = Link(cli, cli.P(5760))
    d.send(0x0005); d.wait(0x0004)
    d.send(0x6001, struct.pack(">I", cli.pid))
    ok = d.wait(0x6007) is not None
    d.send(0x6600, struct.pack(">III", len(main), 0, 0))
    d.send(0x6601, struct.pack(">I%dH" % len(main), len(main), *main))
    d.send(0x6603, struct.pack(">I%dH" % len(extra), len(extra), *extra))
    d.send(0x600b)
    return ok


def auto_loop(link, start, out, surrender=False, timeout=300, tope=None):
    """Contesta con 0x7ffe cada petición (tras 0.08 s sin mensajes nuevos) hasta el 0x6202 tipo 0x4d. surrender:
    en la 1.ª petición manda «Rendirse» (0x6350 0, 0xd, 0, 10) y al diálogo, SÍ (0x6423 1).
    tope: {"max": N, "n": 0, "lock": Lock} compartido entre los auto_loop de un duelo: contestadas N peticiones entre
    todos, la siguiente se queda pendiente SIN contestar (el duelo se para esperándola) hasta que alguien sube "max";
    run_kill mata el node en ese punto fijo y luego abre el tope."""
    i, pending, last = start, False, time.time()
    prompts = autos = 0
    surr = 0
    end = time.time() + timeout
    while time.time() < end:
        with link.cv:
            new = link.msgs[i:]
            i = len(link.msgs)
        for mid, _, d in new:
            last = time.time()
            if is_prompt(mid, d):
                pending = True
                prompts += 1
            if mid == 0x649b:
                out["svr"] = d
            if mid == 0x6202 and _t6202(d)[0] == 0x4d:
                out.update(res=struct.unpack(">I", d[8:12])[0], prompts=prompts, autos=autos)
                return
        if pending and time.time() - last > 0.08 and tope is not None:
            with tope["lock"]:
                parado = tope["n"] >= tope["max"]
                if not parado:
                    tope["n"] += 1
            if parado:                                         # en el tope: la petición sigue pendiente
                time.sleep(0.02)
                continue
        if pending and time.time() - last > 0.08:
            pending = False
            if surrender and surr == 0:
                link.send(0x6350, struct.pack(">4I", 0, 0xd, 0, 10)); surr = 1
            elif surrender and surr == 1:
                link.send(0x6423, struct.pack(">I", 1)); surr = 2
            elif not (surrender and surr == 2):
                link.send(0x7ffe); autos += 1
        time.sleep(0.02)
    out.update(prompts=prompts, autos=autos, timeout=True)


def pvp_open_room(owner, other, dstyle=3):
    nb, na = len(owner.room.msgs), len(other.room.msgs)
    owner.room.send(0x4288, room_msg(dstyle=dstyle))
    ok = owner.room.wait(0x428a, start=nb) is not None
    ra = other.room.wait(0x3113, start=na, pred=lambda d: len(d) >= 12 and struct.unpack(">I", d[4:8])[0] == owner.pid)
    return ok, (struct.unpack(">I", ra[:4])[0] if ra else None)


def pvp_challenge(ch, owner, rid):
    na, nb = len(ch.room.msgs), len(owner.room.msgs)
    ch.room.send(0x4300, struct.pack(">I", rid) + bytes(16))
    off = owner.room.wait(0x4302, start=nb)
    early = ch.room.wait(0x4301, start=na, timeout=0.5)
    return off, early, na, nb


def pvp_start_duel(res, A, B, tag):
    """A (retador) y B conectan al duelo, piedra-papel-tijera con un empate y A empieza. Devuelve los índices de
    inicio de los mensajes del duelo."""
    da = A.P(5760)
    check(res, f"{tag}: los dos pasan 0x6001 (0x6007)", duel_link(A, npc_deck_ids(51)) and duel_link(B, npc_deck_ids(54)))
    pa, pb = A.duel.wait(0x600c, timeout=8), B.duel.wait(0x600c, timeout=8)
    sa, sb = A.duel.wait(0x6005), B.duel.wait(0x6005)
    check(res, f"{tag}: 0x600c a los dos con A = él (168 B)", pa and pb and len(pa) == 168 and
          struct.unpack(">II", pa[32:40]) == (A.pid, B.pid) and struct.unpack(">II", pb[32:40]) == (B.pid, A.pid))
    check(res, f"{tag}: 0x6005 = tamaño del mazo del OTRO", sa and sb and struct.unpack(">H", sa[:2])[0] == len(npc_deck_ids(54)[0])
          and struct.unpack(">H", sb[:2])[0] == len(npc_deck_ids(51)[0]), f"{sa and sa.hex()} {sb and sb.hex()}")
    for X in (A, B):
        X.duel.send(0x6100)
    ok = A.duel.wait(0x6100) is not None and B.duel.wait(0x6100) is not None
    na, nb = len(A.duel.msgs), len(B.duel.msgs)
    A.duel.send(0x6101, b"\0"); B.duel.send(0x6101, b"\0")             # empate: los dos vuelven a mandar
    ja, jb = A.duel.wait(0x6101, start=na), B.duel.wait(0x6101, start=nb)
    ok &= ja == b"\0\0" and jb == b"\0\0"
    na, nb = len(A.duel.msgs), len(B.duel.msgs)
    A.duel.send(0x6101, b"\0"); B.duel.send(0x6101, b"\1")             # (1 - 0) % 3 = 1: gana A
    ja, jb = A.duel.wait(0x6101, start=na), B.duel.wait(0x6101, start=nb)
    ok &= ja == b"\0\1" and jb == b"\0\0"
    check(res, f"{tag}: piedra-papel-tijera (empate y repetir; cada uno recibe la mano del otro)", ok, f"{ja} {jb}")
    na, nb = len(A.duel.msgs), len(B.duel.msgs)
    A.duel.send(0x6102); B.duel.send(0x6102)
    ok = A.duel.wait(0x6102, start=na) is not None and B.duel.wait(0x6102, start=nb) is not None
    A.duel.send(0x6103, b"\0")                                           # A gana y elige empezar él
    fb = B.duel.wait(0x6103, start=nb)
    check(res, f"{tag}: 0x6103 al perdedor = 1 (empieza el rival)", ok and fb == b"\1", f"{fb}")
    sa_, sb_ = len(A.duel.msgs), len(B.duel.msgs)
    A.duel.send(0x61fe, b"\1"); B.duel.send(0x61fe, b"\1")
    return sa_, sb_


def watch_loop(cli, out, timeout=300):
    """Espectador (YO2_WATCH): conecta al duelo con 0x6700 (sin 0x6001), recibe 0x6006 y 0x6701 y pide con 0x6702
    cada vez que se queda sin 0x6202 (como FUN_00720fd0 con la cola vacía), hasta el 0x6202 tipo 0x4d."""
    d = cli.duel = Link(cli, cli.P(5760))
    d.send(0x0005); d.wait(0x0004)
    d.send(0x6700)
    out["info"] = d.wait(0x6006, timeout=8)
    out["jseen"] = d.wait(0x6701, timeout=8)
    if out["jseen"] is None:
        return
    d.send(0x6702)
    i, last, asked, end, cmds = 0, time.time(), time.time(), time.time() + timeout, []
    while time.time() < end:
        with d.cv:
            new = d.msgs[i:]
            i = len(d.msgs)
        for mid, _, x in new:
            last = time.time()
            if mid == 0x6202:
                cmds.append(x)
                if _t6202(x)[0] == 0x4d:
                    out["cmds"] = cmds
                    return
        if (time.time() - last > 0.05 and last > asked) or time.time() - asked > 10:
            d.send(0x6702); asked = time.time()
        time.sleep(0.02)
    out["cmds"] = cmds
    out["timeout"] = True


def run_areas(res, A, B):
    """pvp_hub.AREA_BLOCKS (solo si el servidor de prueba lo tiene: YO2_AREA_BLOCKS=1): un bloque por área; al cruzar
    una puerta (0x5300 con la posición nueva + 0x4202 [bloque, 1]) los demás reciben 0x3112 op 1 con el bloque
    nuevo (byte 5 y último de LIST1) y esa posición en LIST2. Sala 0 = tipo 13 (Domino: áreas 0,1,2,3,9)."""
    bl = A.blocklist
    ents = [struct.unpack(">HBBB", bl[i:i + 5]) for i in range(2, len(bl) - 4, 5)]
    if len(ents) < 2:
        print("  (áreas: el servidor no tiene AREA_BLOCKS; sin pruebas de áreas)")
        return
    check(res, "áreas: BLOCKLIST con un bloque por área (área en +4, id = índice en +9)",
          [e[1] for e in ents] == [0, 1, 2, 3, 9] and [e[3] for e in ents] == list(range(len(ents))), f"{ents}")
    r0 = A.enter_res[-1] if A.enter_res else b""
    check(res, "áreas: 0x4203 al entrar = bloque 0 (área de entrada 0)", r0[4:6] == bytes(2), r0.hex())

    def cross(blk, x, y):
        na, nb = len(A.room.msgs), len(B.room.msgs)
        A.room.send(0x5300, struct.pack(">IHHBB", A.pid, x, y, 2, 0))
        A.room.send(0x4202, bytes([blk, 1]))
        A.room.wait(0x4213, start=na)
        j = B.room.wait(0x3112, start=nb, timeout=3, pred=lambda d: d[:5] == struct.pack(">IB", A.pid, 1))
        r = (A.room.all(0x4203, na) or [b""])[-1]
        own = [e for e in A.room.all(0x4212, na) for e in [e[i:i + 36] for i in range(0, len(e) - 35, 36)]
               if e[:4] == struct.pack(">I", A.pid)]
        return j, r, own

    j, r, own = cross(1, 250, 1250)
    check(res, "áreas: A cruza al área 1: su 0x4203 y su LIST1 llevan el bloque 1",
          r[4:5] == bytes([1]) and own and own[-1][35] == 1, f"{r.hex()} {[o[35] for o in own]}")
    check(res, "áreas: B recibe 0x3112 op 1 de A con bloque 1 (byte 5 y LIST1) y la posición nueva",
          j is not None and j[5] == 1 and j[41] == 1 and struct.unpack(">HH", j[59:63]) == (250, 1250),
          j.hex() if j else "nada")
    j, r, own = cross(0, 1600, 1000)
    check(res, "áreas: A vuelve al área 0: B recibe bloque 0 y la posición de la puerta",
          j is not None and j[5] == 0 and j[41] == 0 and struct.unpack(">HH", j[59:63]) == (1600, 1000),
          j.hex() if j else "nada")


def run_pvp(offset, host="127.0.0.1"):
    print(f"== PvP: retos y duelo contra {host} (+{offset})")
    res = []
    okey = owner_key_from_log()
    A = FakeClient("A", okey or bytes(range(32)), host, offset, "127.0.0.2")
    B = FakeClient("B", bytes([0xA5] * 16 + list(range(16))), host, offset, "127.0.0.3")
    A.gate(); B.gate()
    A.account(0); B.account(0, create_name="Amigo")
    A.enter_room(); B.enter_room()
    if os.environ.get("YO2_LIMIT_LIST", "") not in ("", "0"):     # research/lobby NOTES §7
        raw = b"".join(A.room.all(0x5600))
        ents = [struct.unpack(">IIIB", raw[i:i + 13]) for i in range(0, len(raw) - 12, 13)]
        check(res, "LIMIT_LIST: 0x5600 tras PSELECT (149 cartas, Pot of Greed 4844 = 0)", len(ents) == 149
              and (291, 4844, 0) in [e[1:] for e in ents], f"{len(ents)} entradas")
    run_areas(res, A, B)
    # 0) chat: sala (tipo 0) a todo el bloque y susurro (tipo 3) al pid destino, con el pid del emisor
    nb = len(B.room.msgs)
    A.room.send(0x4400, bytes([0, 0]) + struct.pack(">III", 0, 0, 1) + b"hola" + bytes(1))
    m = B.room.wait(0x4402, start=nb, pred=lambda d: struct.unpack(">I", d[2:6])[0] == A.pid and b"hola" in d)
    check(res, "chat de sala: B recibe 0x4402 de A", m is not None)
    nb = len(B.room.msgs)
    A.room.send(0x4400, bytes([0, 3]) + struct.pack(">III", 0, B.pid, 1) + b"psst" + bytes(1))
    m = B.room.wait(0x4402, start=nb, pred=lambda d: d[1] == 3 and struct.unpack(">I", d[2:6])[0] == A.pid)
    check(res, "susurro: B recibe 0x4402 tipo 3 de A", m is not None)
    # 0b) amigo: A pide, B recibe 0x4512 y acepta, A recibe 0x4515 (1, B)
    na, nb = len(A.room.msgs), len(B.room.msgs)
    A.room.send(0x4510, struct.pack(">I", B.pid))
    r = A.room.wait(0x4511, start=na)
    fo = B.room.wait(0x4512, start=nb, pred=lambda d: struct.unpack(">I", d[:4])[0] == A.pid)
    check(res, "amigo: A 0x4511 = 0 y B recibe 0x4512 con el pid de A", r == bytes(4) and fo is not None and len(fo) == 21,
          f"{r} {fo}")
    B.room.send(0x4513, struct.pack(">Ii", A.pid, 1))
    fn = A.room.wait(0x4515, start=na)
    check(res, "amigo: B acepta y A recibe 0x4515 (1, pid B)", fn is not None and struct.unpack(">iI", fn[:8]) == (1, B.pid),
          fn.hex(" ") if fn else "nada")
    # 0c) bandera: LIST1 lleva el país (cadena de 4 B tras el u8 de la entrada; «ES» por defecto)
    nb = len(B.room.msgs)
    B.room.send(0x4210, bytes([0xff] * 4))
    B.room.wait(0x4213, start=nb)
    raw = b"".join(B.room.all(0x4212, nb))
    ent = [raw[i:i + 36] for i in range(0, len(raw) - 35, 36)]
    check(res, "LIST1 con el país «ES» (bandera) en la cadena de 4 B", ent and all(e[30:32] == b"ES" for e in ent),
          " ".join(e[30:34].hex() for e in ent))
    # 1) reto rechazado
    ok, rid = pvp_open_room(B, A)
    check(res, "B abre sala: 0x428a y A recibe 0x3113 con P1 = B", ok and rid, f"sala {rid}")
    st = A.room.wait(0x3112, timeout=3, pred=lambda d: len(d) > 42 and struct.unpack(">I", d[:4])[0] == B.pid
                     and d[42] == 100)
    check(res, "A recibe 0x3112 de B con estado 100 («sala abierta»: la ficha ofrece retar)", st is not None)
    off, early, na, nb = pvp_challenge(A, B, rid or 0)
    check(res, "0x4302 con el LV real de A (byte 21)", off is not None and len(off) == 42 and off[21] >= 1,
          f"LV {off[21] if off else None}")
    check(res, "A reta: B recibe 0x4302 MATCHOFFER con el pid de A", off and struct.unpack(">I", off[:4])[0] == A.pid,
          off.hex(" ") if off else "nada")
    check(res, "A NO recibe 0x4301 hasta que B contesta", early is None)
    B.room.send(0x4303, struct.pack(">i", 0))
    r = A.room.wait(0x4301, start=na)
    check(res, "B rechaza: A recibe 0x4301 = -618", r and struct.unpack(">i", r[:4])[0] == -618, r.hex() if r else "nada")
    na = len(A.room.msgs)
    B.room.send(0x4290)
    gone = A.room.wait(0x3113, start=na, pred=lambda d: struct.unpack(">III", d[:12]) == (rid, 0, 0))
    check(res, "B borra la sala: A recibe 0x3113 con P1 = P2 = 0", gone is not None)
    # 2) reto aceptado y duelo entero con «auto»
    ok, rid = pvp_open_room(B, A)
    off, early, na, nb = pvp_challenge(A, B, rid or 0)
    B.room.send(0x4303, struct.pack(">i", 1))
    r = A.room.wait(0x4301, start=na)
    na6, nb6 = A.room.wait(0x4306, start=na), B.room.wait(0x4306, start=nb)
    full = A.room.wait(0x3113, start=na, pred=lambda d: struct.unpack(">III", d[:12]) == (rid, B.pid, A.pid))
    check(res, "B acepta: A 0x4301 = 0, 0x4306 a los dos y la sala llena (0x3113 P2 = A)",
          r == bytes(4) and na6 and nb6 and full is not None, f"{r} {na6} {nb6}")
    la, lb = len(A.room.msgs), len(B.room.msgs)
    check(res, "aceptado: A recibe 0x3112 op 0x67 («en duelo») de B",
          A.room.wait(0x3112, start=na, timeout=3, pred=lambda d: d[:5] == struct.pack(">IB", B.pid, 0x67)) is not None)
    watch = os.environ.get("YO2_WATCH", "") not in ("", "0")       # research/lobby NOTES §8
    if watch:
        C = FakeClient("C", bytes([0x5C] * 16 + list(range(16))), host, offset, "127.0.0.4")
        C.gate(); C.account(0, create_name="Miron"); C.enter_room()
    sa, sb = pvp_start_duel(res, A, B, "duelo")
    oa, ob, oc = {}, {}, {}
    ta = threading.Thread(target=auto_loop, args=(A.duel, sa, oa)); tb = threading.Thread(target=auto_loop, args=(B.duel, sb, ob))
    ta.start(); tb.start()
    if watch:
        nc = len(C.room.msgs)
        C.room.send(0x4320, struct.pack(">I", B.pid))
        wr = C.room.wait(0x4321, start=nc)
        check(res, "WATCH: 0x4320 con el pid de B -> 0x4321 s32 0 + ip + puerto de duelo",
              wr is not None and len(wr) == 21 and wr[:4] == bytes(4) and struct.unpack(">H", wr[19:21])[0] == C.P(5760),
              wr.hex(" ") if wr else "nada")
        tc = threading.Thread(target=watch_loop, args=(C, oc)); tc.start()
    ta.join(); tb.join()
    if watch:
        tc.join()
        info = oc.get("info") or b""
        check(res, "WATCH: 0x6006 de 147 B con el mazo de A y el nombre de B", len(info) == 147 and
              struct.unpack(">H", info[:2])[0] == len(npc_deck_ids(51)[0]) and info[73 + 5:73 + 5 + 5] == b"Amigo",
              info[:24].hex(" "))
        check(res, "WATCH: 0x6701 = manos que decidieron (0, 1) y empieza A (0)", oc.get("jseen") == b"\0\1\0",
              f"{oc.get('jseen')}")
        ca = [x for m, _, x in A.duel.msgs if m == 0x6202]
        cc = oc.get("cmds") or []
        check(res, "WATCH: el espectador recibe los mismos 0x6202 que A hasta el 0x4d", cc and ca[:len(cc)] == cc
              and not oc.get("timeout"), f"{len(cc)} de {len(ca)}")
        C.duel.send(0x0003)
    check(res, "duelo con «auto» hasta el final: uno gana y el otro pierde (0x4d 1/2)",
          {oa.get("res"), ob.get("res")} in ({1, 2}, {3}), f"A {oa} B {ob}")   # {3}: empate (corte por SELECT_SUM)
    check(res, "los dos han tenido que decidir (los select van a su vista)", oa.get("prompts", 0) > 0 and ob.get("prompts", 0) > 0)
    time.sleep(1.0)
    for X, n, nm in ((A, la, "A"), (B, lb, "B")):
        ids = [m[0] for m in X.room.msgs[n:]]
        check(res, f"{nm}: su lobby recibe 0x4140 y luego 0x4800", 0x4140 in ids and 0x4800 in ids
              and ids.index(0x4140) < ids.index(0x4800), " ".join(f"{i:04x}" for i in ids[-6:]))
    for X in (A, B):
        X.duel.send(0x0003)
    if os.environ.get("YO2_ROOM_REOPEN", "") not in ("", "0"):    # research/lobby NOTES §9
        na, nb = len(A.room.msgs), len(B.room.msgs)
        B.room.send(0x4210, bytes([0xff] * 4))                     # vuelta al mapa: LIST1..5 y luego 0x4280
        fr = A.room.wait(0x3113, start=na, pred=lambda d: struct.unpack(">III", d[:12]) == (rid, B.pid, 0))
        st = A.room.wait(0x3112, start=na, timeout=3, pred=lambda d: len(d) > 42 and struct.unpack(">I", d[:4])[0] == B.pid
                         and d[42] == 100)
        check(res, "ROOM_REOPEN: tras el duelo A ve la sala de B libre (P2 = 0) y a B con estado 100",
              fr is not None and st is not None, f"{fr and fr.hex(' ')}")
        nb = len(B.room.msgs)
        B.room.send(0x4280, bytes([0xff] * 4))
        B.room.wait(0x4286, start=nb)
        raw = b"".join(B.room.all(0x4284, nb))
        mine = [struct.unpack(">III", raw[i:i + 12]) for i in range(0, len(raw) - 18, 19)]
        check(res, "ROOM_REOPEN: la lista de B trae su sala (P1 = B, P2 = 0): sigue esperando retos",
              (rid, B.pid, 0) in mine, f"{mine}")
    # 3) rendición
    ok, rid = pvp_open_room(B, A)
    off, early, na, nb = pvp_challenge(A, B, rid or 0)
    B.room.send(0x4303, struct.pack(">i", 1))
    A.room.wait(0x4306, start=na); B.room.wait(0x4306, start=nb)
    sa, sb = pvp_start_duel(res, A, B, "rendición")
    oa, ob = {}, {}
    ta = threading.Thread(target=auto_loop, args=(A.duel, sa, oa, True)); tb = threading.Thread(target=auto_loop, args=(B.duel, sb, ob))
    ta.start(); tb.start(); ta.join(); tb.join()
    check(res, "A se rinde: A pierde (0x4d 2) y B gana (0x4d 1)", oa.get("res") == 2 and ob.get("res") == 1, f"A {oa} B {ob}")
    check(res, "B recibe 0x649b (2, 1) «se ha rendido»", ob.get("svr") == b"\2\1", f"{ob.get('svr')}")
    for X in (A, B):
        X.duel.send(0x0003)
    errs = A.errors + B.errors
    check(res, "numeración sin desfases (lobby y las 3 conexiones de duelo de cada uno)", not errs, "; ".join(errs[:4]))
    for X in (A, B):
        X.room.close()
    return res


def _fin(link, start):
    """(P0, P1) del 0x6202 tipo 0x4d desde start, o None."""
    d = link.wait(0x6202, start=start, timeout=15, pred=lambda x: _t6202(x)[0] == 0x4d)
    return struct.unpack(">II", d[8:16]) if d else None


def _lose(res, A, B, loser, tag):
    """loser manda 0x7ffd (prueba: «pierdo esta partida») cuando su duelo ya ha empezado. Devuelve los (P0, P1) de
    0x4d de A y de B y los índices de sus colas antes del fin."""
    X = A if loser == "A" else B
    X.duel.wait(0x6202, start=X.mark, timeout=10)
    na, nb = len(A.duel.msgs), len(B.duel.msgs)
    X.duel.send(0x7ffd)
    fa, fb = _fin(A.duel, na), _fin(B.duel, nb)
    return fa, fb, na, nb


def _next_game(res, A, B, chooser, v, tag):
    """Partida >= 2 de un match como el cliente real (log 20261009-120748): los dos reenvían el mazo (sin 0x600b) y
    0x6102; el que perdió la anterior (chooser) recibe el eco y manda 0x6103 v; el otro recibe 0x6103 (1 - v)."""
    C, O = (A, B) if chooser == "A" else (B, A)
    na, nb = len(A.duel.msgs), len(B.duel.msgs)
    nc, no = (na, nb) if chooser == "A" else (nb, na)
    for X, deck in ((A, npc_deck_ids(51)), (B, npc_deck_ids(54))):
        main, extra = deck
        X.duel.send(0x6600, struct.pack(">III", len(main), 0, len(extra)))
        X.duel.send(0x6601, struct.pack(">I%dH" % len(main), len(main), *main))
        X.duel.send(0x6603, struct.pack(">I%dH" % len(extra), len(extra), *extra))
    O.duel.send(0x6102)                                   # el ganador de la anterior manda antes (como en el log)
    O.duel.send(0x6103, b"\0")                            # y si se cuela un 0x6103 suyo, se ignora
    C.duel.send(0x6102)
    ok = A.duel.wait(0x6102, start=na) is not None and B.duel.wait(0x6102, start=nb) is not None
    check(res, f"{tag}: eco de 0x6102 a los dos (sin 0x600b ni piedra-papel-tijera)", ok)
    check(res, f"{tag}: el 0x6103 del que GANÓ la anterior se ignora", C.duel.wait(0x6103, start=nc, timeout=0.6) is None)
    C.duel.send(0x6103, bytes([v]))
    ec, fo = C.duel.wait(0x6103, start=nc), O.duel.wait(0x6103, start=no)
    check(res, f"{tag}: elige {chooser} (perdió la anterior): eco {v} y al otro {1 - v}",
          ec == bytes([v]) and fo == bytes([1 - v]), f"{ec} {fo}")
    A.mark, B.mark = len(A.duel.msgs), len(B.duel.msgs)
    A.duel.send(0x61fe, b"\1"); B.duel.send(0x61fe, b"\1")
    st = A.duel.wait(0x6202, start=A.mark, timeout=10) is not None and B.duel.wait(0x6202, start=B.mark, timeout=10)
    check(res, f"{tag}: con los dos 0x61fe arranca la partida (0x6202 a los dos)", st is not None and st is not False)


def run_pvpmatch(offset, host="127.0.0.1"):
    """Match al mejor de 3 (pvp_match.PVP_MATCH, sala dstyle 4): 2-0, 2-1 y desconexión entre partidas."""
    print(f"== PvP: match al mejor de 3 contra {host} (+{offset})")
    res = []
    okey = owner_key_from_log()
    A = FakeClient("A", okey or bytes(range(32)), host, offset, "127.0.0.2")
    B = FakeClient("B", bytes([0xA5] * 16 + list(range(16))), host, offset, "127.0.0.3")
    A.gate(); B.gate()
    A.account(0); B.account(0, create_name="Amigo")
    A.enter_room(); B.enter_room()

    def match(tag):
        ok, rid = pvp_open_room(B, A, dstyle=4)
        off, early, na, nb = pvp_challenge(A, B, rid or 0)
        B.room.send(0x4303, struct.pack(">i", 1))
        A.room.wait(0x4306, start=na); B.room.wait(0x4306, start=nb)
        la, lb = len(A.room.msgs), len(B.room.msgs)
        A.mark, B.mark = pvp_start_duel(res, A, B, tag)
        return la, lb

    def lobby_ids(X, n):
        return [m[0] for m in X.room.msgs[n:]]

    # 1) 2-0: B pierde las dos
    la, lb = match("2-0")
    fa, fb, na, nb = _lose(res, A, B, "B", "2-0 p1")
    check(res, "2-0 partida 1: 0x4d A (1, 0) y B (2, 0): el cliente sigue el match", fa == (1, 0) and fb == (2, 0), f"{fa} {fb}")
    time.sleep(0.8)
    check(res, "2-0 partida 1: sin 0x4800 ni registro (0x6010) entre partidas",
          0x4800 not in lobby_ids(A, la) + lobby_ids(B, lb) and not A.duel.all(0x6010, na) and not B.duel.all(0x6010, nb))
    sc = A.duel.all(0x6613, na)
    check(res, "2-0 partida 1: DUELSCORE de A = 1 victoria", sc and struct.unpack(">2I", sc[-1][:8]) == (1, 0),
          sc[-1][:8].hex() if sc else "nada")
    _next_game(res, A, B, "B", 0, "2-0 p2")
    fa, fb, na, nb = _lose(res, A, B, "B", "2-0 p2")
    check(res, "2-0 partida 2: 0x4d A (1, 0) y B (2, 0)", fa == (1, 0) and fb == (2, 0), f"{fa} {fb}")
    sc = A.duel.all(0x6613, na)
    check(res, "2-0: DUELSCORE final de A = 2-0", sc and struct.unpack(">2I", sc[-1][:8]) == (2, 0),
          sc[-1][:8].hex() if sc else "nada")
    time.sleep(1.0)
    for X, n, nm in ((A, la, "A"), (B, lb, "B")):
        ids = lobby_ids(X, n)
        check(res, f"2-0: {nm} recibe 0x4800 una vez, al acabar el match", ids.count(0x4800) == 1,
              " ".join(f"{i:04x}" for i in ids[-8:]))
    check(res, "2-0: registro (0x6010) a los dos al acabar", A.duel.wait(0x6010, start=na) is not None
          and B.duel.wait(0x6010, start=nb) is not None)
    for X in (A, B):
        X.duel.send(0x0003)
    # 2) 2-1: A pierde la 1, B la 2, A la 3
    la, lb = match("2-1")
    fa, fb, na, nb = _lose(res, A, B, "A", "2-1 p1")
    check(res, "2-1 partida 1: gana B (0x4d A 2, B 1; P1 = 0)", fa == (2, 0) and fb == (1, 0), f"{fa} {fb}")
    _next_game(res, A, B, "A", 1, "2-1 p2")
    fa, fb, na, nb = _lose(res, A, B, "B", "2-1 p2")
    check(res, "2-1 partida 2: gana A", fa == (1, 0) and fb == (2, 0), f"{fa} {fb}")
    time.sleep(0.5)
    check(res, "2-1 tras 1-1: todavía sin 0x4800", 0x4800 not in lobby_ids(A, la) + lobby_ids(B, lb))
    _next_game(res, A, B, "B", 0, "2-1 p3")
    fa, fb, na, nb = _lose(res, A, B, "A", "2-1 p3")
    check(res, "2-1 partida 3: gana B", fa == (2, 0) and fb == (1, 0), f"{fa} {fb}")
    sc = B.duel.all(0x6613, nb)
    check(res, "2-1: DUELSCORE final de B = 2-1", sc and struct.unpack(">2I", sc[-1][:8]) == (2, 1),
          sc[-1][:8].hex() if sc else "nada")
    time.sleep(1.0)
    check(res, "2-1: 0x4800 a los dos al acabar", lobby_ids(A, la).count(0x4800) == 1 and lobby_ids(B, lb).count(0x4800) == 1)
    for X in (A, B):
        X.duel.send(0x0003)
    # 3) desconexión entre partidas: B pierde la 1 y cierra su conexión de duelo
    la, lb = match("corte")
    fa, fb, na, nb = _lose(res, A, B, "B", "corte p1")
    check(res, "corte partida 1: gana A", fa == (1, 0) and fb == (2, 0), f"{fa} {fb}")
    time.sleep(0.3)
    na = len(A.duel.msgs)
    B.duel.close()
    sv = A.duel.wait(0x649b, start=na, timeout=5)
    check(res, "corte: A recibe 0x649b (1, 1) «<rival> se ha desconectado»", sv == b"\1\1", f"{sv}")
    check(res, "corte: A recibe 0x6613 con el marcador (1-0) y ningún 0x4d más",
          (A.duel.wait(0x6613, start=na, timeout=3) or b"")[:8] == struct.pack(">2I", 1, 0)
          and not [d for d in A.duel.all(0x6202, na) if _t6202(d)[0] == 0x4d])
    time.sleep(1.0)
    check(res, "corte: A recibe 0x4800 y el registro (0x6010)", 0x4800 in lobby_ids(A, la)
          and A.duel.wait(0x6010, start=na) is not None)
    A.duel.send(0x0003)
    errs = A.errors + B.errors
    check(res, "match: numeración sin desfases", not errs, "; ".join(errs[:4]))
    for X in (A, B):
        X.room.close()
    return res


def run_npc(offset, host="127.0.0.1", rival=84):
    """Duelo contra NPC (Aster = 84) con rendición, como los dos atascos del 07-10. El cliente real solo suelta
    CTRL_NPC_DUEL (0x14) al despedirse la NPC (FUN_0089cc50), y la despedida solo sale si el 0x43a0 llega con el
    último u8 = 0 (con 1, DAT_012bb6b0+0x9c = 1 y FUN_008603d0 no pone +0xa4): ver NPC_RULES_2008 en run.py."""
    print(f"== NPC: duelo contra la NPC {rival} y rendición contra {host} (+{offset})")
    res = []
    A = FakeClient("A", owner_key_from_log() or bytes(range(32)), host, offset, "127.0.0.2")
    A.gate(); A.account(0); A.enter_room()
    na = len(A.room.msgs)
    A.room.send(0x4288, room_msg(rival))
    ok = A.room.wait(0x428a, start=na) is not None
    ads = A.room.wait(0x43a0, start=na)
    check(res, "NPC: 0x428a y 0x43a0 con el último u8 = 0 (duelo simple: el cliente se despide y suelta 0x14)",
          ok and ads is not None and ads[-1] == 0, ads[-1:].hex() if ads else "sin 0x43a0")
    check(res, "NPC: el jugador pasa 0x6001 (0x6007)", duel_link(A, npc_deck_ids(51)))
    A.duel.wait(0x600c, timeout=8)
    for mid, data in ((0x6100, b""), (0x6101, b"\0"), (0x6102, b""), (0x6103, b"\0")):   # como el log del 07-10
        n = len(A.duel.msgs)
        A.duel.send(mid, data)
        A.duel.wait(mid, start=n)
    start = len(A.duel.msgs)
    nl = len(A.room.msgs)
    A.duel.send(0x61fe, b"\1")
    out = {}
    auto_loop(A.duel, start, out, surrender=True, timeout=120)
    end = [d for mid, _, d in A.duel.msgs[start:] if mid == 0x6202 and _t6202(d)[0] == 0x4d]
    p1 = struct.unpack(">I", end[-1][12:16])[0] if end and len(end[-1]) >= 16 else None
    check(res, "NPC: rendición -> 0x6202 tipo 0x4d con P1 = 0 (partida suelta; 5 = cierre de match)", p1 == 0,
          f"P1 {p1} {out}")
    A.room.wait(0x4800, start=nl, timeout=8)
    ids = [mid for mid, _, _ in A.room.msgs[nl:]]
    check(res, "NPC: el lobby recibe 0x4140 y luego 0x4800", 0x4140 in ids and 0x4800 in ids
          and ids.index(0x4140) < ids.index(0x4800), " ".join(f"{i:04x}" for i in ids[-6:]))
    check(res, "NPC: numeración", not A.errors, "; ".join(A.errors[:3]))
    for link in (A.duel, A.room):
        link.close()
    return res


def run_desconexion(offset, host="127.0.0.1", rival=84):
    """07-10: se corta la conexión de duelo a mitad de un duelo contra una NPC. Es la PRIMERA conexión de duelo del
    cliente, que empieza con 0x0005 antes de 0x6001 (como el cliente real: log 20261007-031049). Tiene que contar
    como derrota (duelfin h_disconnect: NPCBIN derrota, DP -1, duels_void +1) y salir en Actividad («pierde contra …»
    con «por desconexión»). Mira el state y admin_eventos de la instancia de prueba (YO2_PRUEBA_DIR)."""
    import json
    print(f"== Desconexión a mitad de un duelo contra la NPC {rival} en {host} (+{offset})")
    res = []
    tdir = os.environ.get("YO2_PRUEBA_DIR") or os.path.join(HERE, "logs", "prueba")
    stp = os.path.join(tdir, "state-prueba.json")
    sys.path.insert(0, os.path.join(HERE, "research", "persistencia"))
    import importlib.util
    sp = importlib.util.spec_from_file_location("ps_desc", os.path.join(HERE, "research", "persistencia", "proposal.py"))
    PS = importlib.util.module_from_spec(sp); sp.loader.exec_module(PS)
    name = "Cortado"

    def prof():
        st = json.load(open(stp, encoding="utf-8"))
        for a in (st.get("accounts") or {}).values():
            for slot, nm in (a.get("players") or {}).items():
                if nm == name:
                    return (a.get("profiles") or {}).get(slot)
        return None

    X = FakeClient("D", bytes([0x5D] * 16 + list(range(16))), host, offset, "127.0.0.6")
    X.gate(); X.account(0, create_name=name); X.enter_room()
    p0 = prof() or {}
    r0 = p0.get("rec", {})
    b0 = (int(r0.get("dpp", 0)), int(r0.get("duels_void", 0)), PS.npcbin_get(p0, rival) if p0 else [0, 0, 0, 0])
    na = len(X.room.msgs)
    X.room.send(0x4288, room_msg(rival))
    X.room.wait(0x43a0, start=na)
    check(res, "corte: 0x0005 y luego 0x6001 (0x6007), como la 1.ª conexión de duelo del cliente real",
          duel_link(X, npc_deck_ids(51)))
    X.duel.wait(0x600c, timeout=8)
    for mid, data in ((0x6100, b""), (0x6101, b"\0"), (0x6102, b""), (0x6103, b"\0")):
        n = len(X.duel.msgs)
        X.duel.send(mid, data)
        X.duel.wait(mid, start=n)
    start = len(X.duel.msgs)
    X.duel.send(0x61fe, b"\1")
    got = _wait_until(lambda: _prompts(X.duel, start) >= 1, 60)
    check(res, "corte: el duelo empieza (llega una petición de jugada)", got)
    t_cut = time.time()
    X.duel.close()                                  # el cliente se cae a mitad: ni 0x0003 ni rendición
    p1 = {}

    def counted():
        nonlocal p1
        p1 = prof() or {}
        return int(p1.get("rec", {}).get("duels_void", 0)) > b0[1]
    _wait_until(counted, 10)
    r1 = p1.get("rec", {})
    b1 = (int(r1.get("dpp", 0)), int(r1.get("duels_void", 0)), PS.npcbin_get(p1, rival) if p1 else [0, 0, 0, 0])
    check(res, "corte: cuenta como derrota (NPCBIN derrota +1, duels_void +1, DP -1)",
          b1[1] == b0[1] + 1 and b1[2][1] == b0[2][1] + 1 and b1[0] == max(0, b0[0] - 1), f"{b0} -> {b1}")
    evs = []
    for f in glob.glob(os.path.join(tdir, "admin_eventos", "eventos-*.jsonl")):
        for line in open(f, encoding="utf-8"):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            txt = "".join(x for x in e.get("seg", []) if isinstance(x, str))
            pj = [x for x in e.get("seg", []) if isinstance(x, dict)]
            if e.get("t", 0) >= t_cut - 1 and e.get("tipo") == "fin" and " pierde contra " in txt \
                    and "desconexi" in str(e.get("det")) and any(x.get("n") == name for x in pj):
                evs.append(e)
    check(res, "corte: Actividad «Duelo terminado: Cortado pierde contra …» (NPC · por desconexión)", len(evs) == 1,
          f"{len(evs)} eventos")
    check(res, "corte: numeración", not X.errors, "; ".join(X.errors[:3]))
    X.room.close()
    return res


_TEST = {"proc": None}            # run.py de prueba lanzado por run_pvp_prueba


def duelcore_pids(parent):
    """PIDs de los node (duelcore.mts) HIJOS del run.py de prueba (nunca otros: se filtra por el padre)."""
    ps = (f"Get-CimInstance Win32_Process -Filter \"ParentProcessId={int(parent)}\" | "
          "Where-Object { $_.CommandLine -like '*duelcore.mts*' } | ForEach-Object { $_.ProcessId }")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60)
    return [int(x) for x in r.stdout.split() if x.strip().isdigit()]


def kill_duelcore(parent):
    pids = duelcore_pids(parent)
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=30)
    return pids


def _prompts(link, start):
    with link.cv:
        return sum(1 for mid, _, d in link.msgs[start:] if is_prompt(mid, d))


def _quieto(links, quieto, tope):
    """Espera hasta que ningún link de `links` reciba nada durante `quieto` s (como mucho `tope` s)."""
    fin = time.time() + tope
    n = [len(x.msgs) for x in links]
    t = time.time()
    while time.time() < fin:
        time.sleep(0.05)
        m = [len(x.msgs) for x in links]
        if m != n:
            n, t = m, time.time()
        elif time.time() - t >= quieto:
            return True
    return False


def _wait_until(cond, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def _npc_kill_duel(res, X, tag, rival=84, kill=True):
    """Duelo de X contra una NPC. kill: en la 1.ª petición de jugada (0x1a) se mata el node de la instancia y se
    contesta «fase final» (0x002e 5): el servidor tiene que cerrar el duelo sin resultado (0x4d P0 = 3). Sin kill:
    rendición (como run_npc). Devuelve el P0 del 0x4d o None."""
    na = len(X.room.msgs)
    X.room.send(0x4288, room_msg(rival))
    X.room.wait(0x43a0, start=na)
    check(res, f"{tag}: 0x6001 (0x6007)", duel_link(X, npc_deck_ids(51)))
    X.duel.wait(0x600c, timeout=8)
    for mid, data in ((0x6100, b""), (0x6101, b"\0"), (0x6102, b""), (0x6103, b"\0")):
        n = len(X.duel.msgs)
        X.duel.send(mid, data)
        X.duel.wait(mid, start=n)
    start = len(X.duel.msgs)
    nl = len(X.room.msgs)
    X.duel.send(0x61fe, b"\1")
    out = {}
    if kill:
        i, killed, answered, end = start, [], False, time.time() + 90
        while time.time() < end and "res" not in out:
            with X.duel.cv:
                new = X.duel.msgs[i:]
                i = len(X.duel.msgs)
            for mid, _, d in new:
                if mid == 0x6202 and _t6202(d)[0] == 0x4d:
                    out["res"] = struct.unpack(">I", d[8:12])[0]
                elif is_prompt(mid, d) and not answered:
                    t, b = _t6202(d) if mid == 0x6202 else (None, None)
                    if t == 0x1a:
                        if not killed:
                            killed = kill_duelcore(_TEST["proc"].pid)
                        X.duel.send(0x002e, struct.pack(">I", 5))     # «fase final»: va a duelcore -> muerto
                        answered = True
                    elif t == 0x1b:
                        X.duel.send(0x6423, struct.pack(">I", 0))      # NO (cadena/SÍ-NO de antes de su turno)
            time.sleep(0.02)
        check(res, f"{tag}: se mata el duelcore de la instancia de prueba a mitad del duelo", killed, f"{killed}")
    else:
        auto_loop(X.duel, start, out, surrender=True, timeout=120)
    X.room.wait(0x4800, start=nl, timeout=10)
    ids = [mid for mid, _, _ in X.room.msgs[nl:]]
    check(res, f"{tag}: el lobby recibe 0x4140 y luego 0x4800", 0x4140 in ids and 0x4800 in ids
          and ids.index(0x4140) < ids.index(0x4800), " ".join(f"{i:04x}" for i in ids[-6:]))
    X.duel.send(0x0003)
    return out.get("res")


def run_descarte(offset, host="127.0.0.1", rival=84, on_first_prompt=None, intentos=3):
    """§20 del traductor (research/traductor/NOTES.md): descarte por límite de mano en la End Phase. Duelo contra la
    NPC con un mazo de 14 Pot of Greed + 26 Mystical Elf; el jugador solo activa lo que pueda de la mano y pasa a la
    End Phase (0x002e 5). Cuando llega el panel (0x6202 tipo 0x1e P0 = 1 + lista A (235, 2) + 0x1b B=0x32 P0=0xd) pulsa
    cartas de la mano con clic directo (0x6350 cmd 11) y comprueba la cuenta (N -> … -> 0) y que la carta va al
    cementerio. Al ver un descarte de 2+ cartas, se rinde. on_first_prompt(): se llama una vez, con el duelo ya en
    marcha, antes de contestar la 1.ª petición (research/traductor/test_hot_descarte.py carga ahí en caliente).
    12-10: un panel de 2+ cartas pide 8+ en la mano, y eso es azar del barajado (robar un Pot of Greed al empezar el
    turno): en ~1 de cada 10 duelos la NPC gana antes con paneles de 1 carta. Si un duelo acaba sin panel de 2+, se juega
    OTRO (hasta `intentos`); las comprobaciones son las mismas y valen para TODOS los paneles de todos los duelos."""
    print(f"== Descarte por límite de mano contra la NPC {rival} en {host} (+{offset})")
    res = []
    panels, seq, ends, links_ok, errs = [], [], [], [], []
    for k in range(intentos):
        d = _duelo_descarte(offset, host, rival, on_first_prompt if k == 0 else None)
        panels += d["panels"]; seq += d["seq"]; ends.append(d["res_end"]); links_ok.append(d["link"]); errs += d["errors"]
        if any(c[0] >= 2 for c in d["panels"]):
            break
        print(f"    (duelo {k + 1}: sin panel de 2+ cartas, fin {d['res_end']}: la mano es azar del barajado; otro duelo)")
    check(res, "descarte: el jugador pasa 0x6001 (0x6007)", all(links_ok))
    # la 1.ª cuenta tras la vista es la de la petición (= la inicial); luego una menos por clic hasta 0
    good = [c for c in panels if c[1] and c[1] == list(range(c[0], -1, -1)) and c[2] == c[0]]
    check(res, "descarte: panel con la cuenta ANTES de la vista 0xd y el texto 235 (clase 2)",
          panels and all(x[0] for x in panels) and ("A", 235, 2) in seq, f"{len(panels)} paneles")
    check(res, "descarte: la cuenta baja con cada clic hasta 0 y van al cementerio tantas cartas como pedía",
          panels and len(good) == len(panels), f"{panels}")
    check(res, "descarte: hay uno de 2+ cartas (8+ en la mano)", any(c[0] >= 2 for c in panels),
          f"{panels} en {len(ends)} duelo(s)")
    check(res, "descarte: nunca se cambia de turno con el panel abierto", not any(x == ("turno", True) for x in seq))
    check(res, "descarte: rendición -> 0x4d (P0 = 2, pierde)", ends[-1] == 2, f"{ends}")
    check(res, "descarte: numeración", not errs, "; ".join(errs[:3]))
    return res


def _duelo_descarte(offset, host, rival, on_first_prompt):
    """Un duelo de run_descarte. -> {panels, seq, res_end, link (pasó 0x6001), errors}."""
    sys.path.insert(0, os.path.join(HERE, "research", "traductor"))
    from cardmap import CardMap
    from test_translator import parse_6611, parse_6612
    cm = CardMap()
    pot, elf = cm.code_to_yo2(55144522), cm.code_to_yo2(15025844)
    A = FakeClient("A", owner_key_from_log() or bytes(range(32)), host, offset, "127.0.0.2")
    A.gate(); A.account(0); A.enter_room()
    na = len(A.room.msgs)
    A.room.send(0x4288, room_msg(rival))
    A.room.wait(0x43a0, start=na)
    linked = duel_link(A, ([pot] * 14 + [elf] * 26, []))
    A.duel.wait(0x600c, timeout=8)
    for mid, data in ((0x6100, b""), (0x6101, b"\0"), (0x6102, b""), (0x6103, b"\0")):
        n = len(A.duel.msgs)
        A.duel.send(mid, data)
        A.duel.wait(mid, start=n)
    start = len(A.duel.msgs)
    A.duel.send(0x61fe, b"\1")
    link, i, pending, last = A.duel, start, False, time.time()
    tail, hand, var1, panel, grave = {}, [], 0, False, False
    seq = []                   # lo que pasa con el panel: ("A", texto), ("var", n), ("vista",), ("clic", i), ("turno",)
    panels = []                # [cuenta inicial, [cuentas tras cada clic], cartas al cementerio]
    surr, res_end, end = 0, None, time.time() + 240
    while time.time() < end and res_end is None:
        with link.cv:
            new = link.msgs[i:]
            i = len(link.msgs)
        for mid, _, d in new:
            last = time.time()
            if mid == 0x6611:
                _, tail = parse_6611(d)
            elif mid == 0x6612:
                hand = parse_6612(d)
            elif mid == 0x6202:
                P = struct.unpack(">10I", d[:40])
                t, b = P[0] & 0xff, P[1]
                if t == 0x1a or (t == 0x1b and b in (0x32, 0x33) and P[2] != 0xd):
                    pending = True                              # petición o diálogo (el panel 0xd no se contesta)
                if t == 0x61:
                    seq.append(("A", P[2], P[3]))
                elif t == 0x1e and P[2] == 1:
                    var1 = P[3]
                    seq.append(("var", var1))
                    if panel:
                        panels[-1][1].append(var1)
                        panel = var1 > 0
                        grave = not panel                       # los 0x3f que siguen son el descarte
                elif t == 0x1b and b == 0x32 and P[2] == 0xd:
                    seq.append(("vista", var1))
                    panel = var1 > 0
                    panels.append([var1, [], 0])
                elif t == 0 and b & 0xfff == 0x04:
                    seq.append(("turno", panel))
                    grave = False
                elif t == 0 and b & 0xfff == 0x3f and grave and P[2] & 1 == 0 and (P[2] >> 1) & 0x1f == 0xb \
                        and (P[3] >> 1) & 0x1f == 0xe:
                    panels[-1][2] += 1                          # mano -> cementerio tras el descarte
                elif t == 0x4d:
                    res_end = P[2]
        if pending and time.time() - last > 0.08:
            pending = False
            if on_first_prompt is not None:
                on_first_prompt()
                on_first_prompt = None
            picks = [k for p, k, m in hand if p == 0 and m >> 11 & 1]
            acts = [k for p, k, m in hand if p == 0 and m >> 3 & 1]
            if surr == 1:
                link.send(0x6423, struct.pack(">I", 1)); surr = 2
            elif any(c[0] >= 2 for c in panels) and not panel and surr == 0:
                link.send(0x6350, struct.pack(">4I", 0, 0xd, 0, 10)); surr = 1      # ya está: rendirse
            elif panel and picks:
                seq.append(("clic", picks[-1]))
                link.send(0x6350, struct.pack(">4I", 0, 0xb, picks[-1], 11))
            elif acts and (0, 5) in tail:
                link.send(0x6350, struct.pack(">4I", 0, 0xb, acts[0], 3))       # Pot of Greed
            elif (0, 5) in tail:
                link.send(0x002e, struct.pack(">I", 5))                         # a la End Phase
            else:
                link.send(0x7ffe)                                               # cadena, etc.: bot.ts
        time.sleep(0.02)
    print(f"    paneles [cuenta, cuentas tras cada clic, al cementerio]: {panels}")
    print(f"    secuencia: {seq[:40]}")
    for lk in (A.duel, A.room):
        lk.close()
    return {"panels": panels, "seq": seq, "res_end": res_end, "link": bool(linked), "errors": list(A.errors)}


def run_kill(offset, host="127.0.0.1"):
    """B25: matar el node (duelcore) de la instancia de prueba a mitad de un duelo PvP y de uno de NPC: los clientes
    reciben el fin (0x4d P0 = 3, sin resultado) y el duelo siguiente funciona con un node nuevo."""
    print(f"== B25: duelcore muerto a mitad del duelo contra {host} (+{offset})")
    res = []
    parent = _TEST["proc"].pid
    first = duelcore_pids(parent)
    check(res, "hay un duelcore (node) hijo del run.py de prueba", first, f"{first}")
    okey = owner_key_from_log()
    A = FakeClient("A", okey or bytes(range(32)), host, offset, "127.0.0.2")
    B = FakeClient("B", bytes([0xA5] * 16 + list(range(16))), host, offset, "127.0.0.3")
    A.gate(); B.gate()
    A.account(0); B.account(0, create_name="Amigo")
    A.enter_room(); B.enter_room()
    # 1) PvP: muere node tras unas cuantas decisiones
    ok, rid = pvp_open_room(B, A)
    off, early, na, nb = pvp_challenge(A, B, rid or 0)
    B.room.send(0x4303, struct.pack(">i", 1))
    A.room.wait(0x4306, start=na); B.room.wait(0x4306, start=nb)
    la, lb = len(A.room.msgs), len(B.room.msgs)
    sa, sb = pvp_start_duel(res, A, B, "kill PvP")
    oa, ob = {}, {}
    # 12-10: el node muere en un punto FIJO del duelo (paridad T9): entre los dos se contestan 4 peticiones y la 5.ª se
    # queda sin contestar; con el duelo parado esperándola (nada nuevo en 1 s) se mata y luego se contesta: esa respuesta
    # va al node muerto y el servidor cierra el duelo. Antes se mataba «cuando se habían visto 4» mientras los dos
    # seguían contestando, y cuántos mensajes más salían antes del corte dependía de la carga del PC.
    tope = {"max": 4, "n": 0, "lock": threading.Lock()}
    ta = threading.Thread(target=auto_loop, args=(A.duel, sa, oa, False, 90, tope))
    tb = threading.Thread(target=auto_loop, args=(B.duel, sb, ob, False, 90, tope))
    ta.start(); tb.start()
    _wait_until(lambda: tope["n"] >= 4 and _prompts(A.duel, sa) + _prompts(B.duel, sb) >= 5, 60)
    _quieto((A.duel, B.duel), 1.0, 10)
    killed = kill_duelcore(parent)
    time.sleep(0.5)
    with tope["lock"]:
        tope["max"] = 1 << 30                                    # node muerto: ahora sí se contesta la petición parada
    check(res, "kill PvP: se mata el duelcore tras 4 decisiones", killed and set(killed) <= set(first), f"{killed}")
    ta.join(); tb.join()
    check(res, "kill PvP: los DOS clientes reciben el fin «sin resultado» (0x4d P0 = 3)",
          oa.get("res") == 3 and ob.get("res") == 3, f"A {oa} B {ob}")
    time.sleep(1.0)
    for X, n, nm in ((A, la, "A"), (B, lb, "B")):
        ids = [m[0] for m in X.room.msgs[n:]]
        check(res, f"kill PvP: el lobby de {nm} recibe 0x4140 y luego 0x4800", 0x4140 in ids and 0x4800 in ids
              and ids.index(0x4140) < ids.index(0x4800), " ".join(f"{i:04x}" for i in ids[-6:]))
    for X in (A, B):
        X.duel.send(0x0003)
    # 2) el PvP siguiente funciona (node nuevo): rendición de A
    B.room.send(0x4210, bytes([0xff] * 4))                       # vuelta al mapa de B (reabre su sala)
    time.sleep(0.5)
    ok, rid = pvp_open_room(B, A)
    off, early, na, nb = pvp_challenge(A, B, rid or 0)
    B.room.send(0x4303, struct.pack(">i", 1))
    A.room.wait(0x4306, start=na); B.room.wait(0x4306, start=nb)
    sa, sb = pvp_start_duel(res, A, B, "PvP tras el kill")
    oa, ob = {}, {}
    ta = threading.Thread(target=auto_loop, args=(A.duel, sa, oa, True, 90))
    tb = threading.Thread(target=auto_loop, args=(B.duel, sb, ob, False, 90))
    ta.start(); tb.start(); ta.join(); tb.join()
    second = duelcore_pids(parent)
    check(res, "PvP tras el kill: duelo normal (A se rinde: 2 / 1) con un node NUEVO",
          oa.get("res") == 2 and ob.get("res") == 1 and second and not set(second) & set(first),
          f"A {oa} B {ob} node antes {first} ahora {second}")
    for X in (A, B):
        X.duel.send(0x0003)
        X.room.close()
    # 3) NPC: muere node a mitad; 4) el duelo de NPC siguiente funciona
    C = FakeClient("C", bytes([0x3C] * 16 + list(range(16))), host, offset, "127.0.0.5")
    C.gate(); C.account(0, create_name="Npcero"); C.enter_room()
    r = _npc_kill_duel(res, C, "kill NPC", kill=True)
    check(res, "kill NPC: el cliente recibe el fin «sin resultado» (0x4d P0 = 3)", r == 3, f"{r}")
    C.room.send(0x4210, bytes([0xff] * 4))
    time.sleep(0.5)
    r = _npc_kill_duel(res, C, "NPC tras el kill", kill=False)
    third = duelcore_pids(parent)
    check(res, "NPC tras el kill: duelo normal (rendición: 0x4d P0 = 2) con otro node nuevo",
          r == 2 and third and not set(third) & set(second or []), f"{r} node {second} -> {third}")
    errs = A.errors + B.errors + C.errors
    check(res, "numeración sin desfases", not errs, "; ".join(errs[:4]))
    C.room.close()
    return res


def _dp_tools():
    """(PS, prof(nombre) -> perfil guardado en el state de la instancia de prueba, o {})."""
    import importlib.util
    import json
    tdir = os.environ.get("YO2_PRUEBA_DIR") or os.path.join(HERE, "logs", "prueba")
    stp = os.path.join(tdir, "state-prueba.json")
    sp = importlib.util.spec_from_file_location("ps_dp", os.path.join(HERE, "research", "persistencia", "proposal.py"))
    PS = importlib.util.module_from_spec(sp); sp.loader.exec_module(PS)

    def prof(name):
        try:
            st = json.load(open(stp, encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        for a in [st] + list((st.get("accounts") or {}).values()):
            for slot, nm in (a.get("players") or {}).items():
                if nm == name:
                    return (a.get("profiles") or {}).get(slot) or {}
        return {}
    return PS, prof


def _dp_pdu(PS, X, start):
    """dpp del último 0x4140 que ha llegado al lobby de X desde start (None si ninguno)."""
    pdus = X.room.all(0x4140, start)
    return PS.decode(PS.PDU_SPEC, pdus[-1])["dpp"] if pdus else None


def run_dpcoste(offset, host="127.0.0.1", rival=84):
    """Coste del duelo (research/persistencia/NOTES.md §13) con la cifra de inicio por defecto (10): personaje nuevo
    con 10 DP; PvP con rendición = 1 DP a CADA uno y el 0x4140 de cada uno lo trae; NPC con rendición = 1 DP."""
    print(f"== DP: personaje nuevo, PvP y NPC contra {host} (+{offset})")
    res = []
    PS, prof = _dp_tools()
    A = FakeClient("A", owner_key_from_log() or bytes(range(32)), host, offset, "127.0.0.2")
    B = FakeClient("B", bytes([0xD7] * 16 + list(range(16))), host, offset, "127.0.0.3")
    A.gate(); B.gate()
    A.account(0); B.account(0, create_name="DpNuevo")
    A.enter_room(); B.enter_room()
    an = A.names.get(0)
    a0, b0 = PS.dp_of(prof(an)), PS.dp_of(prof("DpNuevo"))
    check(res, "personaje nuevo: empieza con 10 DP (oficial)", b0 == 10, f"{b0}")
    check(res, "personaje que ya existía: conserva su saldo (no 10)", prof(an).get("rec", {}).get("dpp") is not None,
          f"{an}: {a0}")
    ok, rid = pvp_open_room(B, A)
    off, early, na, nb = pvp_challenge(A, B, rid or 0)
    check(res, "reto con DP de sobra: B recibe la oferta y A no recibe error", off is not None and early is None,
          f"{early and early.hex()}")
    B.room.send(0x4303, struct.pack(">i", 1))
    A.room.wait(0x4306, start=na); B.room.wait(0x4306, start=nb)
    la, lb = len(A.room.msgs), len(B.room.msgs)
    sa, sb = pvp_start_duel(res, A, B, "DP PvP")
    oa, ob = {}, {}
    ta = threading.Thread(target=auto_loop, args=(A.duel, sa, oa, True))
    tb = threading.Thread(target=auto_loop, args=(B.duel, sb, ob))
    ta.start(); tb.start(); ta.join(); tb.join()
    check(res, "PvP: A se rinde (A pierde, B gana)", oa.get("res") == 2 and ob.get("res") == 1, f"A {oa} B {ob}")
    _wait_until(lambda: PS.dp_of(prof("DpNuevo")) == b0 - 1 and PS.dp_of(prof(an)) == a0 - 1, 8)
    a1, b1 = PS.dp_of(prof(an)), PS.dp_of(prof("DpNuevo"))
    check(res, "PvP: los DOS pagan 1 DP (también el que se rinde)", (a1, b1) == (a0 - 1, b0 - 1), f"A {a0}->{a1} B {b0}->{b1}")
    A.room.wait(0x4140, start=la); B.room.wait(0x4140, start=lb)
    pa, pb = _dp_pdu(PS, A, la), _dp_pdu(PS, B, lb)
    check(res, "PvP: el 0x4140 de cada uno trae su DP nuevo", (pa, pb) == (a1, b1), f"0x4140 A {pa} B {pb}")
    for X in (A, B):
        X.duel.send(0x0003)
    B.room.send(0x4290)
    time.sleep(0.3)
    # NPC con rendición
    nl = len(B.room.msgs)
    B.room.send(0x4288, room_msg(rival))
    ads = B.room.wait(0x43a0, start=nl)
    check(res, "NPC con DP: 0x428a = 0 y 0x43a0", B.room.wait(0x428a, start=nl) == bytes(4) and ads is not None)
    check(res, "NPC: B pasa 0x6001", duel_link(B, npc_deck_ids(51)))
    B.duel.wait(0x600c, timeout=8)
    for mid, data in ((0x6100, b""), (0x6101, b"\0"), (0x6102, b""), (0x6103, b"\0")):
        n = len(B.duel.msgs)
        B.duel.send(mid, data)
        B.duel.wait(mid, start=n)
    start = len(B.duel.msgs)
    B.duel.send(0x61fe, b"\1")
    out = {}
    auto_loop(B.duel, start, out, surrender=True, timeout=120)
    B.room.wait(0x4800, start=nl, timeout=8)
    _wait_until(lambda: PS.dp_of(prof("DpNuevo")) == b1 - 1, 8)
    b2 = PS.dp_of(prof("DpNuevo"))
    check(res, "NPC: rendirse también cuesta 1 DP", b2 == b1 - 1, f"{b1}->{b2}")
    check(res, "NPC: el 0x4140 del fin trae el DP nuevo", _dp_pdu(PS, B, nl) == b2, f"{_dp_pdu(PS, B, nl)}")
    B.duel.send(0x0003)
    check(res, "DP: numeración", not (A.errors + B.errors), "; ".join((A.errors + B.errors)[:3]))
    for X in (A, B):
        X.room.close()
    return res


def run_dpcero(offset, host="127.0.0.1", rival=84):
    """Instancia con YO2_DP_INICIO=0: el personaje nuevo no tiene DP. Reto a NPC -> 0x428a -612 sin 0x43a0 ni «en duelo»
    para los demás; retar la sala de quien no tiene DP -> -614; retar sin DP -> -612."""
    print(f"== DP a 0: rechazos -612/-614 contra {host} (+{offset})")
    res = []
    PS, prof = _dp_tools()
    A = FakeClient("A", owner_key_from_log() or bytes(range(32)), host, offset, "127.0.0.2")
    B = FakeClient("B", bytes([0xD8] * 16 + list(range(16))), host, offset, "127.0.0.3")
    A.gate(); B.gate()
    A.account(0); B.account(0, create_name="SinDP")
    A.enter_room(); B.enter_room()
    check(res, "YO2_DP_INICIO=0: el personaje nuevo empieza con 0 DP", PS.dp_of(prof("SinDP")) == 0,
          f"{PS.dp_of(prof('SinDP'))}")
    nl, na = len(B.room.msgs), len(A.room.msgs)
    B.room.send(0x4288, room_msg(rival))
    r = B.room.wait(0x428a, start=nl)
    late = B.room.wait(0x43a0, start=nl, timeout=1.5)
    check(res, "NPC sin DP: 0x428a = -612 («No tienes suficientes DUELPASS points») y sin 0x43a0",
          r is not None and struct.unpack(">i", r[:4])[0] == -612 and late is None, f"{r and r.hex()} {late}")
    busy = A.room.wait(0x3112, start=na, timeout=1.0, pred=lambda d: d[:5] == struct.pack(">IB", B.pid, 0x67))
    check(res, "NPC sin DP: los demás NO le ven «en duelo»", busy is None)
    ok, rid = pvp_open_room(B, A)                   # (el cliente real no le dejaría abrirla: aquí sí, para probar)
    off, early, na, nb = pvp_challenge(A, B, rid or 0)
    check(res, "retar la sala de quien no tiene DP: -614 y no le llega la oferta",
          early is not None and struct.unpack(">i", early[:4])[0] == -614 and off is None,
          f"{early and early.hex()} {off}")
    B.room.send(0x4290)
    time.sleep(0.3)
    ok, rid = pvp_open_room(A, B)
    off, early, nb, na = pvp_challenge(B, A, rid or 0)
    check(res, "retar sin DP: -612 y no le llega la oferta al dueño",
          early is not None and struct.unpack(">i", early[:4])[0] == -612 and off is None,
          f"{early and early.hex()} {off}")
    A.room.send(0x4290)
    check(res, "DP a 0: numeración", not (A.errors + B.errors), "; ".join((A.errors + B.errors)[:3]))
    for X in (A, B):
        X.room.close()
    return res


def _del_player(cli, slot):
    """0x3030 DELPLAYER por el puerto de cuentas -> 0x3032 (bytes o None)."""
    a = Link(cli, cli.P(5731))
    a.send(0x3001); a.wait(0x3002)
    a.send(0x3003, cli.key); a.wait(0x3004)
    n = len(a.msgs)
    a.send(0x3030, bytes([slot]))
    r = a.wait(0x3032, start=n)
    a.send(0x0003); time.sleep(0.1); a.close()
    return r


def _trunk_sum(p):
    return sum(int(v) for v in (p.get("trunk") or {}).values())


def _pvp_room_duel(res, A, B, tag):
    """B abre sala, A reta, B acepta, los dos entran al duelo. Devuelve (sa, sb, la, lb)."""
    ok, rid = pvp_open_room(B, A)
    off, early, na, nb = pvp_challenge(A, B, rid or 0)
    B.room.send(0x4303, struct.pack(">i", 1))
    A.room.wait(0x4306, start=na); B.room.wait(0x4306, start=nb)
    la, lb = len(A.room.msgs), len(B.room.msgs)
    sa, sb = pvp_start_duel(res, A, B, tag)
    return sa, sb, la, lb


def run_pvpcarta(offset, host="127.0.0.1"):
    """research/persistencia/NOTES.md §14. (1) DP de inicio una vez por CUENTA: 1.er personaje 10, 2.º 0, borrar y
    volver a crear 0. (2) Carta tras el PvP: rendición -> a los dos 0x4800 con los sobres; cada uno manda 0x4802 con
    uno y recibe 0x4803 con UNA carta, que queda en su baúl; el 2.º 0x4802 ya no es de PvP. (3) desconexión a mitad:
    el que se cae recibe la carta directa en el baúl; el que se queda, el 0x4800 con sobres. Con YO2_PVP_CARTA=0
    (modo pvpcarta0): 0x4800 vacío y nada pendiente."""
    off_mode = os.environ.get("YO2_PVP_CARTA") == "0"
    print(f"== Carta tras el PvP y DP por cuenta contra {host} (+{offset})" + (" [YO2_PVP_CARTA=0]" if off_mode else ""))
    res = []
    PS, prof = _dp_tools()
    if not off_mode:
        C = FakeClient("C", bytes([0xC4] * 16 + list(range(16))), host, offset, "127.0.0.4")
        C.gate()
        C.account(0, create_name="CuentaUno")
        C.account(1, create_name="CuentaDos")
        d1, d2 = PS.dp_of(prof("CuentaUno")), PS.dp_of(prof("CuentaDos"))
        check(res, "DP por cuenta: 1.er personaje 10, 2.º personaje de la MISMA cuenta 0", (d1, d2) == (10, 0),
              f"{d1} / {d2}")
        r = _del_player(C, 1)
        C.account(1, create_name="CuentaTres")
        d3 = PS.dp_of(prof("CuentaTres"))
        check(res, "DP por cuenta: borrar y volver a crear personaje no da otros 10", r is not None and d3 == 0, f"{d3}")
        check(res, "DP por cuenta: el 1.er personaje conserva los suyos", PS.dp_of(prof("CuentaUno")) == 10)
    A = FakeClient("A", owner_key_from_log() or bytes(range(32)), host, offset, "127.0.0.2")
    B = FakeClient("B", bytes([0xCA] * 16 + list(range(16))), host, offset, "127.0.0.3")
    A.gate(); B.gate()
    A.account(0); B.account(0, create_name="CartaPvP")
    A.enter_room(); B.enter_room()
    an = A.names.get(0)
    ta0, tb0 = _trunk_sum(prof(an)), _trunk_sum(prof("CartaPvP"))
    # (2) rendición
    sa, sb, la, lb = _pvp_room_duel(res, A, B, "carta")
    oa, ob = {}, {}
    t1 = threading.Thread(target=auto_loop, args=(A.duel, sa, oa, True))
    t2 = threading.Thread(target=auto_loop, args=(B.duel, sb, ob))
    t1.start(); t2.start(); t1.join(); t2.join()
    check(res, "carta: A se rinde (A pierde, B gana)", oa.get("res") == 2 and ob.get("res") == 1, f"A {oa} B {ob}")
    la4, lb4 = A.room.wait(0x4800, start=la, timeout=8), B.room.wait(0x4800, start=lb, timeout=8)
    for X, n, nm in ((A, la, "A"), (B, lb, "B")):
        ids = [m[0] for m in X.room.msgs[n:]]
        check(res, f"carta: {nm} recibe 0x4140 y luego 0x4800", 0x4140 in ids and 0x4800 in ids
              and ids.index(0x4140) < ids.index(0x4800), " ".join(f"{i:04x}" for i in ids[-6:]))
    if off_mode:
        check(res, "YO2_PVP_CARTA=0: 0x4800 vacío a los dos y nada pendiente",
              la4 == b"" and lb4 == b"" and not prof(an).get("pvp_card_due") and not prof("CartaPvP").get("pvp_card_due"),
              f"{la4} {lb4}")
    else:
        packs = [list(struct.unpack(">%dH" % (len(x) // 2), x)) if x else [] for x in (la4, lb4)]
        check(res, "carta: el 0x4800 de cada uno trae los sobres a elegir (PVP_CARD_PACKS)",
              packs[0] == list(PS.PVP_CARD_PACKS) and packs[1] == list(PS.PVP_CARD_PACKS), f"{packs}")
        check(res, "carta: queda 1 carta pendiente en cada perfil",
              len(prof(an).get("pvp_card_due") or []) == 1 and len(prof("CartaPvP").get("pvp_card_due") or []) == 1)
        for X, nm, pk, t0, who in ((A, "A", packs[0][-1] if packs[0] else 1, ta0, an),
                                   (B, "B", packs[1][1] if len(packs[1]) > 1 else 1, tb0, "CartaPvP")):
            n = len(X.room.msgs)
            X.room.send(0x4802, struct.pack(">H", pk))
            r = X.room.wait(0x4803, start=n, timeout=8)
            ok = r is not None and len(r) == 7 and r[:4] == bytes(4)
            _wait_until(lambda: _trunk_sum(prof(who)) == t0 + 1, 5)
            check(res, f"carta: {nm} elige el sobre {pk}: 0x4803 con UNA carta y su baúl +1, sin pendientes",
                  ok and _trunk_sum(prof(who)) == t0 + 1 and not prof(who).get("pvp_card_due"),
                  f"{r and r.hex(' ')} baúl {t0}->{_trunk_sum(prof(who))}")
        n = len(B.room.msgs)
        tb1 = _trunk_sum(prof("CartaPvP"))
        B.room.send(0x4802, struct.pack(">H", packs[1][1] if len(packs[1]) > 1 else 1))
        r = B.room.wait(0x4803, start=n, timeout=8)
        check(res, "carta: un 2.º 0x4802 ya no da la carta de PvP (va al manejador de siempre)",
              r is None or len(r) != 7, f"{r and len(r)}")
        for X in (A, B):
            X.duel.send(0x0003)
        B.room.send(0x4290)
        time.sleep(0.3)
        # (3) desconexión a mitad: B se cae
        tb1 = _trunk_sum(prof("CartaPvP"))
        sa, sb, la, lb = _pvp_room_duel(res, A, B, "carta-corte")
        oa = {}
        t1 = threading.Thread(target=auto_loop, args=(A.duel, sa, oa))
        t1.start()
        got = _wait_until(lambda: _prompts(A.duel, sa) >= 1, 60)
        B.duel.close()
        t1.join()
        check(res, "carta-corte: B se cae a mitad y A gana", got and oa.get("res") == 1, f"{oa}")
        la4 = A.room.wait(0x4800, start=la, timeout=8)
        _wait_until(lambda: _trunk_sum(prof("CartaPvP")) == tb1 + 1, 8)
        check(res, "carta-corte: el que se queda recibe 0x4800 con sobres",
              la4 is not None and len(la4) == 2 * len(PS.PVP_CARD_PACKS), f"{la4}")
        check(res, "carta-corte: el que se cae recibe la carta directa en el baúl (nada pendiente)",
              _trunk_sum(prof("CartaPvP")) == tb1 + 1 and not prof("CartaPvP").get("pvp_card_due"),
              f"{tb1}->{_trunk_sum(prof('CartaPvP'))}")
        time.sleep(0.5)
        check(res, "carta-corte: al que se cae no le llega 0x4800 por el lobby (no hay elector)",
              0x4800 not in [m[0] for m in B.room.msgs[lb:]])
        A.duel.send(0x0003)
    errs = A.errors + B.errors
    check(res, "carta: numeración", not errs, "; ".join(errs[:3]))
    for X in (A, B):
        X.room.close()
    return res


def _npc_start(X, rival=84):
    """X reta a la NPC, conecta al duelo, piedra (0) y empieza él (0x6103 0); devuelve (ok, nombre de la NPC del 0x43a0,
    índice del primer mensaje del duelo tras el 0x61fe)."""
    na = len(X.room.msgs)
    X.room.send(0x4288, room_msg(rival))
    ads = X.room.wait(0x43a0, start=na) or b""
    ok = duel_link(X, npc_deck_ids(51))
    X.duel.wait(0x600c, timeout=8)
    for mid, data in ((0x6100, b""), (0x6101, b"\0"), (0x6102, b""), (0x6103, b"\0")):
        n = len(X.duel.msgs)
        X.duel.send(mid, data)
        X.duel.wait(mid, start=n)
    start = len(X.duel.msgs)
    X.duel.send(0x61fe, b"\1")
    return ok, ads[4:20].split(b"\0")[0], start


def _npc_turns(X, start, n, timeout=90):
    """Contesta n peticiones de jugada del duelo de NPC de X (0x1a -> 0x002e 5 «fase final», 0x1b -> 0x6423 0 «no»)
    desde el índice start. Devuelve el índice por el que va."""
    i, done, pend, last, end = start, 0, None, time.time(), time.time() + timeout
    while time.time() < end and done < n:
        with X.duel.cv:
            new = X.duel.msgs[i:]
            i = len(X.duel.msgs)
        for mid, _, d in new:
            last = time.time()
            if mid == 0x6202 and _t6202(d)[0] in (0x1a, 0x1b):
                pend = _t6202(d)[0]
            if mid == 0x6202 and _t6202(d)[0] == 0x4d:
                return i
        if pend is not None and time.time() - last > 0.1:
            X.duel.send(0x002e, struct.pack(">I", 5)) if pend == 0x1a else X.duel.send(0x6423, struct.pack(">I", 0))
            pend, done = None, done + 1
        time.sleep(0.02)
    return i


def _npc_surrender(X, i, timeout=60):
    """Rendición en la siguiente petición de jugada: 0x6350 (0, 0xd, 0, 10) y SÍ al diálogo. Devuelve (índices del
    diálogo [desde, hasta) en X.duel.msgs, P0 del 0x4d o None)."""
    end, pend, last, dlg = time.time() + timeout, False, time.time(), [None, None]
    while time.time() < end:
        with X.duel.cv:
            new = X.duel.msgs[i:]
            i = len(X.duel.msgs)
        for mid, _, d in new:
            last = time.time()
            if mid == 0x6202 and _t6202(d)[0] == 0x4d:
                return dlg, struct.unpack(">I", d[8:12])[0]
            if is_prompt(mid, d):
                pend = True
        if pend and time.time() - last > 0.1:
            pend = False
            if dlg[0] is None:
                dlg[0] = len(X.duel.msgs)
                X.duel.send(0x6350, struct.pack(">4I", 0, 0xd, 0, 10))
            elif dlg[1] is None:
                dlg[1] = len(X.duel.msgs)
                X.duel.send(0x6423, struct.pack(">I", 1))
        time.sleep(0.02)
    return dlg, None


def run_npcmirar(offset, host="127.0.0.1", rival=84):
    """07-10 (AÑADIDO NUESTRO, aprobado por el owner; research/lobby/NOTES.md «Espectador de duelos contra NPC»): P
    reta a una NPC y W, en el mismo bloque, ve la pose «en duelo» (0x3112 op 0x67 + nombre de la NPC) solo cuando el
    duelo EMPIEZA, pulsa Mirar (0x4320) a mitad y ve lo mismo que P desde el principio (sin el diálogo «¿Te rindes?»);
    al acabar, op 1 y el espectador sale. Luego, P se desconecta a mitad con W mirando: W recibe el fin. W no paga DP
    ni le cuenta nada en su perfil."""
    print(f"== NPC: pose «en duelo» y espectador del duelo contra la NPC {rival} en {host} (+{offset})")
    res = []
    PS, prof = _dp_tools()
    P = FakeClient("P", bytes([0x9A] * 16 + list(range(16))), host, offset, "127.0.0.2")
    W = FakeClient("W", bytes([0x9B] * 16 + list(range(16))), host, offset, "127.0.0.3")
    P.gate(); W.gate()
    P.account(0, create_name="RetaNpc"); W.account(0, create_name="Miron")
    P.enter_room(); W.enter_room()
    pose = lambda s, op: (lambda d: d[:5] == struct.pack(">IB", P.pid, op))   # noqa: E731
    w0 = prof("Miron")
    wdp0, wrec0, pdp0 = PS.dp_of(w0), dict(w0.get("rec") or {}), PS.dp_of(prof("RetaNpc"))
    # 1) reto aceptado pero sin duelo: nadie le ve «en duelo» y no hay nada que mirar
    nw, npl = len(W.room.msgs), len(P.room.msgs)
    P.room.send(0x4288, room_msg(rival))
    ok = P.room.wait(0x428a, start=npl) == bytes(4) and P.room.wait(0x43a0, start=npl) is not None
    early = W.room.wait(0x3112, start=nw, timeout=2.0, pred=pose(W, 0x67))
    check(res, "reto a la NPC aceptado sin empezar el duelo: W NO ve la pose", ok and early is None, f"{early}")
    W.room.send(0x4320, struct.pack(">I", P.pid))
    r = W.room.wait(0x4321, start=nw)
    check(res, "sin duelo empezado: Mirar -> 0x4321 = 1", r is not None and r[:4] == struct.pack(">I", 1))
    n = len(P.room.msgs)
    P.room.send(0x4210, bytes([0xff] * 4)); P.room.wait(0x4213, start=n)     # vuelve al mapa sin jugar
    # 2) duelo de verdad; W entra a mirar a mitad
    nw = len(W.room.msgs)
    ok, npc_name, start = _npc_start(P, rival)
    st = W.room.wait(0x3112, start=nw, timeout=6, pred=pose(W, 0x67))
    check(res, "el duelo empieza: W recibe 0x3112 op 0x67 + nombre de la NPC (LV/V/D/título 0)",
          ok and st is not None and len(st) == 29 and st[6:22].split(b"\0")[0] == npc_name and st[22:] == bytes(7),
          st.hex(" ") if st else "nada")
    i = _npc_turns(P, start, 2)
    nw = len(W.room.msgs)
    W.room.send(0x4320, struct.pack(">I", P.pid))
    wr = W.room.wait(0x4321, start=nw)
    check(res, "Mirar a mitad: 0x4321 = 0 + ip + puerto de duelo",
          wr is not None and len(wr) == 21 and wr[:4] == bytes(4) and struct.unpack(">H", wr[19:21])[0] == W.P(5760),
          wr.hex(" ") if wr else "nada")
    ow = {}
    tw = threading.Thread(target=watch_loop, args=(W, ow, 120)); tw.start()
    i = _npc_turns(P, i, 2)
    nw = len(W.room.msgs)
    dlg, p0 = _npc_surrender(P, i)
    tw.join(130)
    check(res, "P se rinde: 0x4d", p0 is not None, f"{p0} {dlg}")
    info = ow.get("info") or b""
    check(res, "espectador: 0x6006 de 147 B (mazo y nombre de P; la NPC de jugador 1)", len(info) == 147 and
          struct.unpack(">H", info[:2])[0] == len(npc_deck_ids(51)[0]) and info[5:12] == b"RetaNpc"
          and info[78:94].split(b"\0")[0] == npc_name and struct.unpack(">H", info[73:75])[0] > 0, info[:24].hex(" "))
    check(res, "espectador: 0x6701 = manos (0 de P, 1 de la NPC) y empieza P (0)", ow.get("jseen") == b"\0\1\0",
          f"{ow.get('jseen')}")
    cp = [x for k, (m, _, x) in enumerate(P.duel.msgs) if k >= start and m == 0x6202
          and not (dlg[0] is not None and dlg[0] <= k < (dlg[1] or 1 << 30))]
    cw = ow.get("cmds") or []
    check(res, "espectador: los mismos 0x6202 que P DESDE EL PRINCIPIO (sin el «¿Te rindes?») hasta el 0x4d",
          cw == cp and not ow.get("timeout"), f"{len(cw)} de {len(cp)}")
    back = W.room.wait(0x3112, start=nw, timeout=6, pred=pose(W, 1))
    check(res, "al acabar: W recibe 0x3112 op 1 (sin pose)", back is not None)
    W.duel.send(0x0003); time.sleep(0.2); W.duel.close()
    P.duel.send(0x0003)
    n = len(W.room.msgs)
    W.room.send(0x4320, struct.pack(">I", P.pid))
    r = W.room.wait(0x4321, start=n)
    check(res, "duelo acabado: Mirar -> 0x4321 = 1", r is not None and r[:4] == struct.pack(">I", 1))
    _wait_until(lambda: PS.dp_of(prof("RetaNpc")) == pdp0 - 1, 8)
    check(res, "P paga 1 DP por el duelo (como siempre)", PS.dp_of(prof("RetaNpc")) == pdp0 - 1,
          f"{pdp0} -> {PS.dp_of(prof('RetaNpc'))}")
    n = len(P.room.msgs)
    P.room.send(0x4210, bytes([0xff] * 4)); P.room.wait(0x4213, start=n)
    # 3) P se cae a mitad con W mirando: W recibe el fin y la pose se quita
    nw = len(W.room.msgs)
    ok, _, start = _npc_start(P, rival)
    st = W.room.wait(0x3112, start=nw, timeout=6, pred=pose(W, 0x67))
    _npc_turns(P, start, 1)
    nw = len(W.room.msgs)
    W.room.send(0x4320, struct.pack(">I", P.pid))
    wr = W.room.wait(0x4321, start=nw)
    ow = {}
    tw = threading.Thread(target=watch_loop, args=(W, ow, 60)); tw.start()
    _wait_until(lambda: ow.get("jseen") is not None, 8)
    time.sleep(0.5)
    P.duel.close()                                 # se cae: ni 0x0003 ni rendición
    tw.join(70)
    cw = ow.get("cmds") or []
    check(res, "P se cae con W mirando: W recibe el fin (0x4d) sin quedarse colgado", ok and st is not None and wr
          and wr[:4] == bytes(4) and cw and _t6202(cw[-1])[0] == 0x4d and not ow.get("timeout"),
          f"{len(cw)} comandos {ow.get('timeout')}")
    back = W.room.wait(0x3112, start=nw, timeout=6, pred=pose(W, 1))
    check(res, "P se cae: W recibe 0x3112 op 1 (sin pose)", back is not None)
    W.duel.send(0x0003); time.sleep(0.2); W.duel.close()
    w1 = prof("Miron")
    check(res, "el espectador no paga DP ni le cuenta nada (rec igual)", PS.dp_of(w1) == wdp0
          and dict(w1.get("rec") or {}) == wrec0, f"DP {wdp0} -> {PS.dp_of(w1)}")
    errs = P.errors + W.errors
    check(res, "npcmirar: numeración", not errs, "; ".join(errs[:3]))
    for X in (P, W):
        X.room.close()
    return res


def _quiet_loop(link, start, out, timeout=60):
    """Jugador que NO contesta nada (límite de tiempo): apunta la 1.ª petición, los 0x6461, el 0x649b y el 0x4d."""
    i, end = start, time.time() + timeout
    out.setdefault("timers", [])
    while time.time() < end:
        with link.cv:
            new = link.msgs[i:]
            i = len(link.msgs)
        for mid, _, d in new:
            if is_prompt(mid, d) and "t_prompt" not in out:
                out["t_prompt"] = time.time()
            if mid == 0x6461 and len(d) >= 6:
                out["timers"].append((d[0], struct.unpack(">i", d[1:5])[0], d[5]))
            if mid == 0x649b:
                out["svr"] = d
            if mid == 0x6202 and _t6202(d)[0] == 0x4d:
                out.update(res=struct.unpack(">I", d[8:12])[0], t_end=time.time())
                return
        time.sleep(0.02)
    out["timeout"] = True


def run_pvptiempo(offset, host="127.0.0.1"):
    """Límite de tiempo (research/traductor/TIEMPO.md «Implementado»), con YO2_TURN_TIMER/YO2_TIEMPO_* cortos (los
    pone main): (1) A no contesta en su turno -> 0x649b (3, 0) a A, (3, 1) a B, A pierde y B gana, 0x4140 + 0x4800;
    (2) duelo entero con respuestas rápidas: nadie pierde por tiempo; (3) sala con «Límite de Tiempo» OFF: sin
    derrota; (4) contra NPC no hay límite."""
    turn = float(os.environ.get("YO2_TURN_TIMER") or 180)
    margin = float(os.environ.get("YO2_TIEMPO_MARGEN") or 3)
    print(f"== Límite de tiempo contra {host} (+{offset}): {turn:g} s por turno, margen {margin:g} s")
    res = []
    A = FakeClient("A", owner_key_from_log() or bytes(range(32)), host, offset, "127.0.0.2")
    B = FakeClient("B", bytes([0xD7] * 16 + list(range(16))), host, offset, "127.0.0.3")
    A.gate(); B.gate()
    A.account(0); B.account(0, create_name="Reloj")
    A.enter_room(); B.enter_room()
    # (1) A empieza y no contesta
    sa, sb, la, lb = _pvp_room_duel(res, A, B, "tiempo")
    oa, ob = {}, {}
    t1 = threading.Thread(target=_quiet_loop, args=(A.duel, sa, oa, turn + margin + 30))
    t2 = threading.Thread(target=auto_loop, args=(B.duel, sb, ob, False, turn + margin + 30))
    t1.start(); t2.start(); t1.join(); t2.join()
    dt = oa.get("t_end", 0) - oa.get("t_prompt", 0)
    check(res, "tiempo: A recibe su reloj corriendo (0x6461 lado 0, lleno, sin pausa)",
          (0, int(round(turn)), 0) in oa.get("timers", []), f"{oa.get('timers', [])[:4]}")
    check(res, "tiempo: A no contesta -> A pierde (0x4d 2) y B gana (0x4d 1)", oa.get("res") == 2 and ob.get("res") == 1,
          f"A {oa.get('res')} B {ob.get('res')}")
    check(res, "tiempo: 0x649b (3, 0) a A y (3, 1) a B «Se acabó el tiempo de A»",
          oa.get("svr") == b"\3\0" and ob.get("svr") == b"\3\1", f"{oa.get('svr')} {ob.get('svr')}")
    check(res, f"tiempo: acaba tras ~{turn:g} s + margen desde la petición", turn - 0.5 <= dt <= turn + margin + 6,
          f"{dt:.1f} s")
    for X, n, nm in ((A, la, "A"), (B, lb, "B")):
        X.room.wait(0x4800, start=n, timeout=8)
        ids = [m[0] for m in X.room.msgs[n:]]
        check(res, f"tiempo: {nm}: su lobby recibe 0x4140 y luego 0x4800", 0x4140 in ids and 0x4800 in ids
              and ids.index(0x4140) < ids.index(0x4800), " ".join(f"{i:04x}" for i in ids[-6:]))
    for X in (A, B):
        X.duel.send(0x0003)
    # (2) duelo entero, los dos contestan rápido
    sa, sb, la, lb = _pvp_room_duel(res, A, B, "rápido")
    oa, ob = {}, {}
    t1 = threading.Thread(target=auto_loop, args=(A.duel, sa, oa)); t2 = threading.Thread(target=auto_loop, args=(B.duel, sb, ob))
    t1.start(); t2.start(); t1.join(); t2.join()
    check(res, "rápido: duelo entero sin derrota por tiempo (0x4d 1/2 y sin 0x649b código 3)",
          {oa.get("res"), ob.get("res")} in ({1, 2}, {3}) and (oa.get("svr") or b"\0")[:1] != b"\3"
          and (ob.get("svr") or b"\0")[:1] != b"\3", f"A {oa} B {ob}")
    for X in (A, B):
        X.duel.send(0x0003)
    # (3) sala con «Límite de Tiempo» OFF (0x4288 byte 22 = 0)
    nb, na = len(B.room.msgs), len(A.room.msgs)
    B.room.send(0x4288, bytes([3, 0, 0, 0]) + bytes(16) + bytes([0, 1, 0, 0]) + struct.pack(">I", 0xffffffff))
    B.room.wait(0x428a, start=nb)
    ra = A.room.wait(0x3113, start=na, pred=lambda d: len(d) >= 12 and struct.unpack(">I", d[4:8])[0] == B.pid
                     and d[17] == 0)
    rid = struct.unpack(">I", ra[:4])[0] if ra else 0
    check(res, "sala OFF: A ve la sala de B con «Tiempo» = 0 (0x3113 byte 17)", ra is not None)
    off, early, na, nb = pvp_challenge(A, B, rid)
    B.room.send(0x4303, struct.pack(">i", 1))
    A.room.wait(0x4306, start=na); B.room.wait(0x4306, start=nb)
    sa, sb = pvp_start_duel(res, A, B, "sala OFF")
    oa, ob = {}, {}
    _quiet_loop(A.duel, sa, oa, turn + margin + 3)
    check(res, f"sala OFF: {turn + margin + 3:g} s sin contestar y el duelo sigue (sin 0x649b ni 0x4d)",
          oa.get("timeout") and "res" not in oa and "svr" not in oa, f"{oa}")
    auto_loop(A.duel, sa, oa, True, 60)                          # A se rinde para acabar
    check(res, "sala OFF: después A se rinde (0x4d 2)", oa.get("res") == 2, f"{oa}")
    for X in (A, B):
        X.duel.send(0x0003)
    # (4) contra NPC: sin límite
    na = len(A.room.msgs)
    A.room.send(0x4288, room_msg(84))
    A.room.wait(0x43a0, start=na)
    duel_link(A, npc_deck_ids(51))
    A.duel.wait(0x600c, timeout=8)
    for mid, data in ((0x6100, b""), (0x6101, b"\0"), (0x6102, b""), (0x6103, b"\0")):
        n = len(A.duel.msgs)
        A.duel.send(mid, data)
        A.duel.wait(mid, start=n)
    start = len(A.duel.msgs)
    A.duel.send(0x61fe, b"\1")
    on = {}
    _quiet_loop(A.duel, start, on, turn + margin + 3)
    check(res, f"NPC: {turn + margin + 3:g} s sin contestar y el duelo sigue (sin 0x649b ni 0x4d)",
          on.get("timeout") and "res" not in on and "svr" not in on and "t_prompt" in on, f"{on}")
    auto_loop(A.duel, start, on, True, 60)
    check(res, "NPC: después A se rinde (0x4d 2)", on.get("res") == 2, f"{on}")
    errs = A.errors + B.errors
    check(res, "tiempo: numeración sin desfases", not errs, "; ".join(errs[:4]))
    for X in (A, B):
        X.room.close()
    return res


def run_pvp_prueba(offset, runner=None):
    """Instancia de prueba (como run_prueba) con YO2_PVP_TEST=1 y pausa de duelo corta; pasa run_pvp (o runner).
    YO2_PRUEBA_DIR = otra carpeta para la copia del estado y el log (dos agentes probando a la vez no se pisan)."""
    tdir = os.environ.get("YO2_PRUEBA_DIR") or os.path.join(HERE, "logs", "prueba")
    os.makedirs(tdir, exist_ok=True)
    st = os.path.join(tdir, "state-prueba.json")
    copiar_estado(st)                                            # copia: la viva no se toca
    for f in glob.glob(st + ".*"):
        os.remove(f)
    prep_state(st)                                               # logseguro: el dueño de prueba = login inventado
    # 07-10: cuentas nuevas con 32 B cualesquiera = login de antes (research/cuentas: YO2_LOGIN_ESTRICTO)
    env = dict(os.environ, YO2_LOGIN_ESTRICTO="0", YO2_NO_CLIENT="1", YO2_PORT_OFFSET=str(offset), YO2_STATE=st, YO2_LOGDIR=tdir,
               YO2_BIND="127.0.0.1", YO2_PVP_TEST="1", YO2_DUEL_PAUSE="0.002")
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "run.py"), "0"], env=env, cwd=HERE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    _TEST["proc"] = proc                                         # B25: run_kill mata el node de ESTA instancia
    try:
        for _ in range(150):
            t = socket.socket(); t.settimeout(0.2)
            up = t.connect_ex(("127.0.0.1", 5730 + offset)) == 0
            t.close()
            if up:
                break
            if proc.poll() is not None:
                sys.exit(f"run.py de prueba terminó al arrancar (código {proc.returncode}); mira {tdir}")
            time.sleep(0.2)
        time.sleep(4.0)                                          # duelcore arranca en ~3 s
        res = (runner or run_pvp)(offset)
    finally:
        proc.terminate()                                         # solo la instancia de prueba que lanzamos aquí
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
    bad = [n for n, ok in res if not ok]
    print(f"== RESULTADO PvP: {len(res) - len(bad)}/{len(res)} bien" + (f"; mal: {bad}" if bad else ""))
    return not bad


def run_salas(offset, host="127.0.0.1"):
    """10-10, salas por idioma 1:1 (ROOM_PER_LANG, research/lobby/NOTES.md): A con el juego en español (0x4100 = 5) y B
    en francés (3). Misma sala de idioma = se ven; salas de idiomas distintos = no; mundial (b1 = 6) = sí."""
    print(f"== Salas por idioma contra {host} (+{offset})")
    res = []
    okey = owner_key_from_log()
    A = FakeClient("A", okey or bytes(range(32)), host, offset, "127.0.0.2")
    B = FakeClient("B", bytes([0xA5] * 16 + list(range(16))), host, offset, "127.0.0.3")
    rooms = A.gate_rooms()
    B.gate()
    A.account(0); B.account(0, create_name="Amigo")
    ports = {p for p, _ in rooms.values()}
    check(res, "puerta: 88 salas (8 idiomas × 11 tipos), un puerto cada una", len(rooms) == 88 and len(ports) == 88,
          f"{len(rooms)} salas, {len(ports)} puertos")
    check(res, "puerta: la mundial (b1 6) en 5740+i, las de idioma desde 5800 (+ offset)",
          rooms.get((6, 13), (0,))[0] == 5740 + offset and rooms.get((0, 13), (0,))[0] == 5800 + offset,
          f"mundial {rooms.get((6, 13))}, japonés {rooms.get((0, 13))}")
    ES, FR, WORLD, DOMINO = 5, 4, 6, 13          # b1 (FUN_00a54b80): 4 francés, 5 español, 6 mundial; b2 13 Domino City

    def pareja(tag, ra, rb, ven):
        A.enter_room(lang=5, port=rooms[ra][0])
        B.enter_room(lang=3, port=rooms[rb][0])
        b_ve_a = A.pid in [p for p, _ in B.enter_list]          # LIST1 al entrar B (yo primero y luego los demás)
        a_ve_b = B.pid in [p for p, _ in A.block_list()[0]]
        check(res, f"{tag}: {'se ven' if ven else 'NO se ven'} (A {ra}, B {rb})",
              (b_ve_a, a_ve_b) == (ven, ven) and A.pid and B.pid and A.pid != B.pid,
              f"B ve a A {b_ve_a}, A ve a B {a_ve_b} (pids {A.pid}, {B.pid})")
        n = A.gate_rooms()
        A.leave_room(); B.leave_room()
        time.sleep(0.3)
        return n

    n = pareja("misma sala española", (ES, DOMINO), (ES, DOMINO), True)
    check(res, "puerta: la sala española cuenta 2 y la francesa 0", n[(ES, DOMINO)][1] == 2 and n[(FR, DOMINO)][1] == 0,
          f"es {n[(ES, DOMINO)]}, fr {n[(FR, DOMINO)]}")
    n = pareja("española y francesa", (ES, DOMINO), (FR, DOMINO), False)
    check(res, "puerta: 1 en la española y 1 en la francesa", n[(ES, DOMINO)][1] == 1 and n[(FR, DOMINO)][1] == 1,
          f"es {n[(ES, DOMINO)]}, fr {n[(FR, DOMINO)]}")
    n = pareja("mundial (idiomas mezclados)", (WORLD, DOMINO), (WORLD, DOMINO), True)
    check(res, "puerta: la mundial cuenta 2", n[(WORLD, DOMINO)][1] == 2, f"{n[(WORLD, DOMINO)]}")
    pareja("mundial y española", (WORLD, DOMINO), (ES, DOMINO), False)
    # Academia (puerta roja, tipo 14): mismo idioma de sala, el área de entrada sigue siendo la de la Academia (5)
    A.enter_room(lang=5, port=rooms[(ES, 14)][0])
    area = A.enter_res[0][5] if A.enter_res and len(A.enter_res[0]) >= 6 else None
    A.leave_room()
    check(res, "Academia española: ENTERBLOCKRESULT con el área 5", area == 5, f"área {area}")
    return res


def latest_live_log():
    """El log vivo más reciente con UN solo cliente (replay repite el login de uno; uno con dos clientes mezcla)."""
    logs = sorted(glob.glob(os.path.join(HERE, "logs", "*.log")), key=os.path.getmtime, reverse=True)
    for path in logs:
        with open(path, encoding="utf-8", errors="replace") as f:
            ips = set(re.findall(r"\] conexión de \('([0-9.]+)'", f.read()))
        if len(ips) <= 1:
            return path
    return logs[0] if logs else None


def _baul_saneado(perfil):
    """Baúl del perfil como lo deja el servidor al cargar: sin los ids que el cliente no pinta (sanitize_trunk, B05).
    Sin esto, una carta que sale de valid_cards.json después de guardarse state.json (p. ej. la 3255 en 7d4a8913) hace
    que «perfil del owner intacto» falle aunque la prueba no haya tocado el baúl."""
    import copy
    import importlib.util
    trunk = perfil.get("trunk")
    try:
        sp = importlib.util.spec_from_file_location("fc_pp_baul", os.path.join(HERE, "research", "persistencia",
                                                                               "proposal.py"))
        pp = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(pp)
    except Exception as e:                                        # sin el módulo, la comparación de siempre
        print(f"  (sin research/persistencia para sanear el baúl: {e!r})")
        return trunk
    p = copy.deepcopy(perfil)
    quitados = pp.sanitize_trunk(p) if trunk is not None else []
    if quitados:
        print(f"  baúl de state.json saneado como al cargar: fuera {sorted(quitados)[:10]}")
    return p.get("trunk") if trunk is not None else None


def run_prueba(offset):
    tdir = os.path.join(HERE, "logs", "prueba")
    os.makedirs(tdir, exist_ok=True)
    st = os.path.join(tdir, "state-prueba.json")
    copiar_estado(st)                                            # copia: la viva no se toca
    for f in glob.glob(st + ".*"):
        os.remove(f)
    prep_state(st)                                               # logseguro: el dueño de prueba = login inventado
    # 07-10: cuentas nuevas con 32 B cualesquiera = login de antes (research/cuentas: YO2_LOGIN_ESTRICTO)
    env = dict(os.environ, YO2_LOGIN_ESTRICTO="0", YO2_NO_CLIENT="1", YO2_PORT_OFFSET=str(offset), YO2_STATE=st, YO2_LOGDIR=tdir,
               YO2_BIND="127.0.0.1")
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "run.py"), "0"], env=env, cwd=HERE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        for _ in range(150):
            t = socket.socket(); t.settimeout(0.2)
            up = t.connect_ex(("127.0.0.1", 5730 + offset)) == 0
            t.close()
            if up:
                break
            if proc.poll() is not None:
                sys.exit(f"run.py de prueba terminó al arrancar (código {proc.returncode}); mira {tdir}")
            time.sleep(0.2)
        time.sleep(1.0)
        res = run_dos(offset)
        res += run_basura(offset)
        log = latest_live_log()
        if log:
            res += run_replay(offset, log)
    finally:
        proc.terminate()                                         # solo la instancia de prueba que lanzamos aquí
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
    import json
    s = json.load(open(st, encoding="utf-8"))
    orig = json.load(open(os.path.join(HERE, "state.json"), encoding="utf-8"))
    print("== state de prueba tras la prueba")
    same = s.get("profiles", {}).get("0", {}).get("trunk") == _baul_saneado(orig.get("profiles", {}).get("0", {}))
    lv = s.get("profiles", {}).get("0", {}).get("rec", {}).get("lv")
    accts = {k: (v.get("idx"), v.get("players")) for k, v in s.get("accounts", {}).items()}
    print(f"  owner_acct={s.get('owner_acct')} players={s.get('players')} LV={lv} baúl igual={same} cuentas={accts}")
    res.append(("perfil del owner intacto (nombre, LV, baúl)", s.get("players") == orig.get("players") and same
                and lv == orig.get("profiles", {}).get("0", {}).get("rec", {}).get("lv")))
    res.append(("cuenta nueva aparte en state['accounts']", any(p == {"0": "Amigo"} for _, p in accts.values())))
    bad = [n for n, ok in res if not ok]
    print(f"== RESULTADO: {len(res) - len(bad)}/{len(res)} bien" + (f"; mal: {bad}" if bad else ""))
    return not bad


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("modo", choices=["prueba", "dos", "replay", "pvp", "npc", "kill", "desconexion", "dpcoste",
                                     "dpcero", "pvpcarta", "pvpcarta0", "pvptiempo", "npcmirar", "descarte",
                                     "pvpmatch", "salas"])
    ap.add_argument("--offset", type=int, default=100)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--log", default=None)
    a = ap.parse_args()
    if a.offset == 0:
        sys.exit("offset 0 = la instancia viva del owner: no.")
    if a.modo == "prueba":
        sys.exit(0 if run_prueba(a.offset) else 1)
    if a.modo == "npc":                       # duelo de NPC + rendición (atasco 07-10), instancia de prueba propia
        sys.exit(0 if run_pvp_prueba(a.offset, run_npc) else 1)
    if a.modo == "descarte":                  # §20 traductor: descarte por límite de mano (panel CUDiscardDialog)
        sys.exit(0 if run_pvp_prueba(a.offset, run_descarte) else 1)
    if a.modo == "desconexion":               # 07-10: corte a mitad de un duelo de NPC = derrota + Actividad
        sys.exit(0 if run_pvp_prueba(a.offset, run_desconexion) else 1)
    if a.modo == "dpcoste":                   # coste del duelo (persistencia NOTES §13): 10 al empezar, PvP y NPC
        sys.exit(0 if run_pvp_prueba(a.offset, run_dpcoste) else 1)
    if a.modo == "dpcero":                    # ídem con YO2_DP_INICIO=0: rechazos −612/−614
        os.environ["YO2_DP_INICIO"] = "0"
        sys.exit(0 if run_pvp_prueba(a.offset, run_dpcero) else 1)
    if a.modo in ("pvpcarta", "pvpcarta0"):   # carta tras el PvP y DP de inicio por cuenta (persistencia NOTES §14)
        os.environ["YO2_PVP_CARTA"] = "0" if a.modo == "pvpcarta0" else "1"
        sys.exit(0 if run_pvp_prueba(a.offset, run_pvpcarta) else 1)
    if a.modo == "pvptiempo":                 # límite de tiempo (TIEMPO.md «Implementado»), con tiempos cortos
        for k, v in (("YO2_TURN_TIMER", "6"), ("YO2_TIEMPO_RESPUESTA", "3"), ("YO2_TIEMPO_MARGEN", "0.5")):
            os.environ.setdefault(k, v)
        os.environ["YO2_LIMITE_TIEMPO"] = "1"
        sys.exit(0 if run_pvp_prueba(a.offset, run_pvptiempo) else 1)
    if a.modo == "npcmirar":                  # 07-10: pose «en duelo» y espectador de un duelo contra NPC
        sys.exit(0 if run_pvp_prueba(a.offset, run_npcmirar) else 1)
    if a.modo == "kill":                      # B25: node muerto a mitad de un duelo PvP y de NPC (instancia propia)
        sys.exit(0 if run_pvp_prueba(a.offset, run_kill) else 1)
    if a.modo == "pvpmatch":                  # 10-10: match al mejor de 3 (pvp_match.PVP_MATCH), instancia propia
        sys.exit(0 if run_pvp_prueba(a.offset, run_pvpmatch) else 1)
    if a.modo == "salas":                     # 10-10: salas por idioma 1:1 (YO2_ROOM_PER_LANG=1), instancia propia
        os.environ["YO2_ROOM_PER_LANG"] = "1"
        sys.exit(0 if run_pvp_prueba(a.offset, run_salas) else 1)
    if a.modo == "pvp":                       # arranca su propia instancia de prueba (YO2_PVP_TEST=1)
        sys.exit(0 if run_pvp_prueba(a.offset) else 1)
    if a.modo == "dos":
        r = run_dos(a.offset, a.host)
    else:
        r = run_replay(a.offset, a.log or latest_live_log(), a.host)
    sys.exit(0 if all(ok for _, ok in r) else 1)
