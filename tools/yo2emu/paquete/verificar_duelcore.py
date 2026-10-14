"""Comprueba que duelcore.mjs (bundle_duelcore.py) juega EXACTAMENTE igual que duelcore.mts lanzado con tsx.

    python paquete/verificar_duelcore.py [--mjs paquete/_build/duelcore.mjs] [--duelos 12] [--node node] [--vendor <dir>]

Qué hace:
 1. Copia el .mjs a una carpeta temporal FUERA del repo (así se ve que no depende de node_modules ni de rutas relativas)
    y arranca los dos motores con el mismo VENDOR_DIR.
 2. Juega los mismos duelos en los dos (semillas fijas, alternando quién empieza, reglas MR5 y MR1, más un duelo en modo
    cerebro con la NPC decidida por SimplePolicy desde Python) y compara la secuencia COMPLETA de eventos, quitando solo
    lo que depende del reloj (`ms`, `load_ms`, `pid`).
 3. Pasa con el .mjs las pruebas de duelcore/: test_bucle.py (20 duelos + reproducibilidad), test_errores.py y
    test_cerebro.py.
Sale con 0 si todo es igual y las pruebas dan OK.
"""
import argparse
import contextlib
import io
import os
import runpy
import shutil
import sys
import tempfile
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
EMU = os.path.dirname(AQUI)
ROOT = os.path.normpath(os.path.join(EMU, "..", ".."))
DCDIR = os.path.join(EMU, "duelcore")
sys.path.insert(0, DCDIR)

import duelcore  # noqa: E402
from duelcore import DuelCore  # noqa: E402
from pruebas_comun import DECKS, SimplePolicy, play_duel  # noqa: E402

RELOJ = {"ms", "load_ms", "pid"}


def limpio(ev):
    if isinstance(ev, dict):
        return {k: limpio(v) for k, v in ev.items() if k not in RELOJ}
    if isinstance(ev, list):
        return [limpio(x) for x in ev]
    return ev


class Politica(SimplePolicy):
    """SimplePolicy para cualquier jugador (copia de la de test_cerebro.py: free_places() da player 0 = «yo»)."""

    def __init__(self, me):
        super().__init__()
        self.me = me

    def __call__(self, sel):
        r = super().__call__(sel)
        if r and "places" in r:
            for pl in r["places"]:
                pl["player"] = self.me if pl["player"] == 0 else 1 - self.me
        return r


def duelos(dc, n):
    """Lista de (nombre, eventos) jugando con dc."""
    out = []
    for i in range(n):
        rule = "MR1" if i % 3 == 2 else "MR5"
        log = []
        r = play_duel(dc, f"v{i}", 5000 + i * 104729, first=i % 2, max_turns=60, log=log, rule=rule)
        out.append((f"v{i} {rule} first={i % 2} gana={r['winner']} turnos={r['turns']} dec={r['decisions']}", log))
    # modo cerebro: los dos jugadores los decide Python (SimplePolicy, como test_cerebro.py); si una política no sabe
    # contestar o el core pide repetir, contesta bot.ts (`auto`), que también es determinista con la semilla
    log, pols = [], {0: Politica(0), 1: Politica(1)}
    dc.new("cer", DECKS, first=1, seed=777, brain=[True, True])
    while True:
        evs = dc.wait("cer", timeout=30)
        log += evs
        last = evs[-1]
        if last["ev"] != "select":
            break
        p = last["player"]
        r = None if last.get("retry") else pols[p](last)
        dc.auto("cer") if r is None else dc.respond("cer", r, player=p)
    out.append((f"cerebro gana={last.get('winner')} turnos={last.get('turns')}", log))
    out.append(("cardinfo", dc.cardinfo([89631139, 46986414, 83764718, 4206964])))
    return out


def con_lanzador(launch, script, args):
    """Ejecuta una prueba de duelcore/ con DuelCore() arrancando `launch` en vez de tsx. Devuelve (rc, salida)."""
    orig = duelcore._find_launch
    duelcore._find_launch = lambda: list(launch)
    buf = io.StringIO()
    rc = 0
    old_argv, old_cwd = sys.argv, os.getcwd()
    try:
        sys.argv = [script] + list(args)
        os.chdir(DCDIR)
        with contextlib.redirect_stdout(buf):
            try:
                runpy.run_path(os.path.join(DCDIR, script), run_name="__main__")
            except SystemExit as e:
                rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
            except Exception as e:  # noqa: BLE001
                print(f"EXCEPCIÓN {type(e).__name__}: {e}")
                rc = 1
    finally:
        sys.argv = old_argv
        os.chdir(old_cwd)
        duelcore._find_launch = orig
    return rc, buf.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mjs", default=os.path.join(AQUI, "_build", "duelcore.mjs"))
    ap.add_argument("--duelos", type=int, default=12)
    ap.add_argument("--node", default=shutil.which("node") or "node")
    ap.add_argument("--vendor", default=os.path.join(ROOT, "_vendor"))
    a = ap.parse_args()

    os.environ["VENDOR_DIR"] = os.path.abspath(a.vendor)
    tmp = tempfile.mkdtemp(prefix="duelcore_mjs_")
    mjs = os.path.join(tmp, "duelcore.mjs")
    shutil.copy2(a.mjs, mjs)
    launch = [a.node, "--disable-warning=ExperimentalWarning", mjs]
    print(f"node: {a.node}  VENDOR_DIR={os.environ['VENDOR_DIR']}\nmjs:  {mjs} ({os.path.getsize(mjs):,} B)")
    fallos = []
    try:
        t = time.perf_counter()
        with DuelCore() as dc:
            ref = duelos(dc, a.duelos)
            ready_tsx = dc.ready
        t_tsx = time.perf_counter() - t
        t = time.perf_counter()
        with DuelCore(launch=launch) as dc:
            nue = duelos(dc, a.duelos)
            ready_mjs = dc.ready
        t_mjs = time.perf_counter() - t
        print(f"tsx: {ready_tsx.get('cards')} cartas, carga {ready_tsx.get('load_ms')} ms, total {t_tsx:.1f} s")
        print(f"mjs: {ready_mjs.get('cards')} cartas, carga {ready_mjs.get('load_ms')} ms, total {t_mjs:.1f} s")
        if ready_tsx.get("cards") != ready_mjs.get("cards"):
            fallos.append("distinto nº de cartas cargadas")
        nev = 0
        for (na, la), (nb, lb) in zip(ref, nue):
            igual = limpio(la) == limpio(lb)
            nev += len(la)
            print(f"  {'IGUAL   ' if igual else 'DISTINTO'} {na} | {nb}  ({len(la)} eventos)")
            if not igual:
                fallos.append(na)
        if len(ref) != len(nue):
            fallos.append("distinto número de duelos")
        print(f"comparados {len(ref)} duelos/consultas, {nev} eventos")

        for script, args in (("test_bucle.py", ["20"]), ("test_errores.py", []), ("test_cerebro.py", [])):
            rc, salida = con_lanzador(launch, script, args)
            ult = [x for x in salida.strip().splitlines() if x.strip()][-1:] or [""]
            print(f"  {script} {' '.join(args)} con el .mjs: rc={rc}  {ult[0][:150]}")
            if rc != 0:
                fallos.append(script)
                print(salida[-3000:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("RESULTADO:", "OK" if not fallos else f"FALLOS {fallos}")
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()
