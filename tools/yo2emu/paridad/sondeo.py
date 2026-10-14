"""Sondeo de la paridad (REESTRUCTURA.md §3.3): entra con una cuenta nueva y manda, en cuentas (5731), lobby My Room
(5732) y una sala (5740), CADA id de petición conocido con datos mínimos (ceros del tamaño que escribe el cliente,
protocol.json), apuntando lo que contesta. Cubre tienda, correo, amigos, equipos, intercambio, torneos, DUELPASS...
que los modos de fake_client no tocan. Lo graba el tap de fake_client (YO2_TAP) como cualquier escenario.

    python paridad/grabar.py --etiqueta x --escenarios sondeo     # lo normal: dentro de la paridad (offset 2200)
    python paridad/sondeo.py --listar [--escribir]                 # vuelve a sacar los ids del código (instancia de
                                                                   # prueba propia, offset 2200 con cerrojo) y los
                                                                   # compara con los congelados en escenarios.json

La lista que se usa es la CONGELADA en escenarios.json («sondeo»): si se sacara del código en cada pasada, mover
código de sitio cambiaría el escenario. Los ids nuevos se añaden a mano (o con --escribir) y se rehace la base.
"""
import argparse
import json
import os
import re
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comun  # noqa: E402

TAM = {"u8": 1, "u16": 2, "u32": 4, "s32": 4}
SIN_SONDEO = {0x0003, 0x0005, 0x3001, 0x3003}     # cierre, keepalive y login: los manda el propio sondeo


def _tam(fmt):
    n = 0
    for f in fmt:
        if f in TAM:
            n += TAM[f]
        elif f.startswith("bytes") and f[5:].isdigit():
            n += int(f[5:])
    return n


def _payload(mid, conf, pid):
    """Datos del id: el de 'datos' en escenarios.json (hex; {pid} = pid propio en 4 B) o ceros del tamaño del
    formato de escritura del cliente (protocol.json); sin formato, vacío."""
    h = conf.get("datos", {}).get(f"0x{mid:04x}")
    if h is not None:
        return bytes.fromhex(h.replace("{pid}", f"{pid or 0:08x}").replace(" ", ""))
    return bytes(conf.get("tam", {}).get(f"0x{mid:04x}", 0))


def _quieto(link, n0, maximo=1.2, calma=0.15):
    """Espera a que el servidor deje de contestar: calma s sin mensajes nuevos (tope maximo s)."""
    fin = time.time() + maximo
    ult, n = time.time(), len(link.msgs)
    while time.time() < fin and not link.closed:
        time.sleep(0.03)
        m = len(link.msgs)
        if m != n:
            n, ult = m, time.time()
        elif time.time() - ult >= calma:
            break
    return [x[0] for x in link.msgs[n0:]]


def _login(FC, cli, puerto, sala=False):
    if sala:
        cli.enter_room()
        return cli.room
    lk = FC.Link(cli, cli.P(puerto))
    lk.send(0x3001); lk.wait(0x3002, timeout=cli.wait_s)
    lk.send(0x3003, cli.key); lk.wait(0x3004, timeout=cli.wait_s)
    if puerto == 5732:
        n = len(lk.msgs)
        lk.send(0x4100, bytes([5, 0]))
        lk.wait(0x4101, start=n, timeout=cli.wait_s)
        _quieto(lk, n)
    return lk


def _sondear(FC, cli, puerto, ids, conf, sala=False):
    """[(id, [ids de respuesta] | 'cerrada')] mandando cada id por la misma conexión (otra si el servidor cierra)."""
    out = []
    lk = _login(FC, cli, puerto, sala)
    for mid in ids:
        if lk.closed:
            lk = _login(FC, cli, puerto, sala)
        n0 = len(lk.msgs)
        try:
            lk.send(mid, _payload(mid, conf, cli.pid))
        except OSError:
            out.append((mid, "cerrada"))
            lk.closed = True
            continue
        r = _quieto(lk, n0)
        out.append((mid, "cerrada" if lk.closed and not r else r))
    try:
        lk.send(0x0003)
    except OSError:
        pass
    time.sleep(0.1)
    lk.close()
    return out


def run_sondeo(offset, host="127.0.0.1"):
    """Runner de grabar.py (mismo contrato que los run_* de fake_client: lista de (nombre, ok))."""
    FC = comun.fake_client()
    conf = comun.leer_json(os.path.join(comun.AQUI, "escenarios.json"))["sondeo"]
    print(f"== sondeo contra +{offset}")
    res = []
    S = FC.FakeClient("S", bytes([0x50] * 16 + list(range(16))), host, offset, "127.0.0.7")
    FC.check(res, "sondeo: puerta", S.gate())
    nm = S.account(0, create_name="Sonda")
    FC.check(res, "sondeo: cuenta nueva con personaje", nm.get(0) == "Sonda", f"{nm}")
    ids = {k: [int(x, 16) for x in v] for k, v in conf["ids"].items()}
    total = {}
    for tipo, puerto, sala in (("cuentas", 5731, False), ("lobby", 5732, False), ("sala", 5740, True)):
        r = _sondear(FC, S, puerto, ids[tipo], conf, sala)
        total[tipo] = r
        sin = sum(1 for _, x in r if x == [])
        cer = sum(1 for _, x in r if x == "cerrada")
        FC.check(res, f"sondeo {tipo}: {len(r)} ids mandados", len(r) == len(ids[tipo]),
                 f"{sin} sin respuesta, {cer} con cierre")
    FC.check(res, "sondeo: numeración sin desfases", not S.errors, "; ".join(S.errors[:4]))
    tap = os.environ.get("YO2_TAP")
    if tap:
        comun.escribir_json(os.path.join(os.path.dirname(tap), "sondeo.json"),
                            {t: [[f"0x{m:04x}", x if x == "cerrada" else [f"0x{y:04x}" for y in x]] for m, x in r]
                             for t, r in total.items()})
    return res


# ------------------------------------------------------------------------------------------------ --listar
def _ids_estaticos():
    """Ids de petición que se ven sin arrancar nada: protocol.json (envíos del cliente), ramas msg_id de
    game_reply (yo2srv/cuentas/personajes.py)/lobby_reply (yo2srv/lobby/lobby.py) y claves de auto_replies.json."""
    raiz = os.path.dirname(os.path.dirname(comun.EMU))
    proto = comun.leer_json(os.path.join(raiz, "_original", "ghidra", "protocol.json"), {})
    envios = {int(k, 16): v.get("writes", []) for k, v in proto.get("send", {}).items()}
    ramas = {"cuentas": set(), "lobby": set()}
    # lobby_reply salió a yo2srv/lobby/lobby.py (E7) y game_reply a yo2srv/cuentas/personajes.py (E8; sigue run.py por si
    # una copia vieja lo tiene). Cada fichero por separado: el último «def» de uno no debe tragarse el texto del siguiente.
    for rel in ("run.py", os.path.join("yo2srv", "lobby", "lobby.py"), os.path.join("yo2srv", "cuentas", "personajes.py")):
        src = open(os.path.join(comun.EMU, rel), encoding="utf-8").read()
        trozos = re.split(r"^def (\w+)\(", src, flags=re.M)
        for i in range(1, len(trozos) - 1, 2):
            nombre, cuerpo = trozos[i], trozos[i + 1]
            dest = {"game_reply": "cuentas", "lobby_reply": "lobby"}.get(nombre)
            if not dest:
                continue
            for m in re.finditer(r"msg_id (?:==|in) \(?((?:0x[0-9a-fA-F]{4}(?:,\s*)?)+)", cuerpo):
                ramas[dest] |= {int(x, 16) for x in re.findall(r"0x[0-9a-fA-F]{4}", m.group(1))}
    auto = {int(k, 16) for k in comun.leer_json(os.path.join(comun.EMU, "auto_replies.json"), {})}
    return envios, ramas, auto


def _ids_vivos(offset):
    """Claves de LP.HANDLERS (con EQ/TP/TO/MW/NR), PH_GAME y PH_LOBBY de una instancia de PRUEBA, por su consola."""
    import grabar
    out = os.path.join(comun.OUT, "sondeo-listar")
    dump = os.path.join(out, "handlers.json")
    linea = ("__import__('json').dump({'lp': sorted(LP.HANDLERS), 'ph_game': sorted(PH_GAME), "
             f"'ph_lobby': sorted(PH_LOBBY)}}, open({dump!r}, 'w'))")
    with comun.Instancia(offset, os.path.join(out, "inst"), grabar.ESTADO, {"YO2_PVP_TEST": "1"}, 4.0):
        port = 5799 + offset
        tok = open(os.path.join(os.environ.get("LOCALAPPDATA", ""), "YO2Online", f"consola_{port}.txt"),
                   encoding="ascii").read().strip()
        import socket
        s = socket.create_connection(("127.0.0.1", port))
        s.sendall(("AUTH " + tok + "\n" + linea + "\n").encode())
        s.shutdown(socket.SHUT_WR)
        r = s.recv(4096).decode().strip()
        s.close()
        if r != "ok":
            sys.exit(f"consola de la instancia de prueba: {r}")
    return comun.leer_json(dump)


def listar(escribir=False):
    envios, ramas, auto = _ids_estaticos()
    with comun.Cerrojo():
        vivos = _ids_vivos(comun.OFFSET_PARIDAD)
    lobby = (set(vivos["lp"]) | set(vivos["ph_lobby"]) | ramas["lobby"]
             | {m for m in envios if m >= 0x4000} | {m for m in auto if 0x4000 <= m < 0x6000 or m >= 0x7000})
    cuentas = set(vivos["ph_game"]) | ramas["cuentas"] | {m for m in envios if m < 0x4000} | {m for m in auto if m < 0x4000}
    lobby = {m for m in lobby if not 0x6000 <= m < 0x7000} - SIN_SONDEO      # 0x6xxx = duelo (puerto 5760)
    cuentas -= SIN_SONDEO
    nuevo = {"cuentas": [f"0x{m:04x}" for m in sorted(cuentas)], "lobby": [f"0x{m:04x}" for m in sorted(lobby)],
             "sala": [f"0x{m:04x}" for m in sorted(lobby)]}
    path = os.path.join(comun.AQUI, "escenarios.json")
    C = comun.leer_json(path)
    viejo = C.get("sondeo", {}).get("ids", {})
    for t in nuevo:
        a, b = set(viejo.get(t, [])), set(nuevo[t])
        print(f"{t}: {len(b)} ids en el código; congelados {len(a)}; nuevos {sorted(b - a)}; ya no están {sorted(a - b)}")
    if escribir:
        so = C.setdefault("sondeo", {})
        so["ids"] = nuevo
        so["tam"] = {f"0x{m:04x}": _tam(f) for m, f in sorted(envios.items()) if _tam(f)}
        so.setdefault("datos", {})
        comun.escribir_json(path, C)
        print(f"escrito en {path}: rehacer la base (python paridad/grabar.py --base)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--listar", action="store_true")
    ap.add_argument("--escribir", action="store_true", help="con --listar: congela la lista nueva en escenarios.json")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.listar:
        listar(a.escribir)
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
