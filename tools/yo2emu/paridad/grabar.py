"""Grabación de la paridad de bytes (REESTRUCTURA.md §3.3): lo que ve el cliente, byte a byte, en escenarios fijos.

    python paridad/grabar.py --preparar            # una vez: congela _fixtures/state-paridad.json y _fixtures/replay.log
    python paridad/grabar.py --base                # línea base: 3 pasadas + máscaras automáticas (comparar.py)
    python paridad/grabar.py --base --escenarios kill   # regraba SOLO kill (3 pasadas) y lo cambia en la base
    python paridad/grabar.py --etiqueta E1a        # una pasada; luego: python paridad/comparar.py E1a
    python paridad/grabar.py --etiqueta x --escenarios prueba,sondeo

Siempre offset 2200 y con cerrojo (_out/.lock): una paridad a la vez en el PC. Cada escenario arranca SU instancia de
prueba (estado = copia de _fixtures/state-paridad.json; log, estado y TEMP en su carpeta) con YO2_TAP puesto para los
clientes falsos; nada toca el servidor vivo ni sus ficheros.
Salida: _out/grab/<etiqueta>/<n>/<escenario>/{tap/*.jsonl, inst/ (log y estado de la instancia), salida.txt,
resultado.json}.
"""
import argparse
import contextlib
import glob
import io
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comun  # noqa: E402

ESTADO = os.path.join(comun.FIX, "state-paridad.json")
REPLAY = os.path.join(comun.FIX, "replay.log")


def cfg():
    return comun.leer_json(os.path.join(comun.AQUI, "escenarios.json"))


# ------------------------------------------------------------------------------------------------ fixtures
def arbol_principal():
    r = subprocess.run(["git", "-C", comun.EMU, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                       capture_output=True, text=True)
    return os.path.dirname(r.stdout.strip()) if r.returncode == 0 else None


def _un_cliente(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        ips = set(re.findall(r"\] conexión de \('([0-9.]+)'", f.read()))
    return len(ips) <= 1


def preparar(forzar=False, log=None):
    """Congela el estado y el log del replay (copias; ignoradas por git: son datos de jugadores)."""
    os.makedirs(comun.FIX, exist_ok=True)
    if forzar or not os.path.exists(ESTADO):
        shutil.copyfile(os.path.join(comun.EMU, "state.json"), ESTADO)
        print(f"estado congelado: {ESTADO} (de {os.path.join(comun.EMU, 'state.json')})")
    else:
        print(f"estado ya congelado: {ESTADO}")
    if not forzar and os.path.exists(REPLAY):
        print(f"replay ya congelado: {REPLAY}")
        return
    if not log:
        # logs/ no está en git: se buscan en la copia de trabajo y, si no hay, en el árbol principal (SOLO lectura)
        cands = []
        for raiz in (comun.EMU, os.path.join(arbol_principal() or "", "tools", "yo2emu")):
            cands += glob.glob(os.path.join(raiz, "logs", "*.log"))
        cands.sort(key=os.path.getmtime, reverse=True)
        FC = comun.fake_client()             # un solo cliente y un login de verdad (los logs de prueba no tienen)
        log = next((p for p in cands if os.path.getsize(p) < 60e6 and _un_cliente(p) and len(FC.parse_log(p)) >= 30),
                   None)
        if not log:
            sys.exit("no encuentro un log con un solo cliente para el replay (usa --log)")
    FC = comun.fake_client()
    pasos = FC.parse_log(log)
    # recorta el log a lo que usa parse_log (hasta la 1.ª conexión al duelo o el primer 0x4288) + un margen
    out, started = [], False
    with open(log, encoding="utf-8", errors="replace") as f:
        for line in f:
            out.append(line)
            if re.match(r"^\[srv:5730\] conexión de", line):
                started = True
            if started and (re.match(r"^\[srv:5760\] conexión de", line) or re.match(r"^\[srv:\d+\] <- 0x4288 ", line)):
                break
    with open(REPLAY, "w", encoding="utf-8", newline="\n") as f:
        f.writelines(out)
    assert FC.parse_log(REPLAY) == pasos, "el recorte cambia lo que repite el replay"
    print(f"replay congelado: {REPLAY} ({len(out)} líneas de {log}; {len(pasos)} pasos)")


# ------------------------------------------------------------------------------------------------ grabación
def _runners(FC):
    import sondeo
    return {
        "dos": FC.run_dos,
        "basura": FC.run_basura,
        "replay": lambda o: FC.run_replay(o, REPLAY),
        "npc": FC.run_npc,
        "pvp": FC.run_pvp,
        "kill": FC.run_kill,
        "sondeo": sondeo.run_sondeo,
    }


def grabar_escenario(nombre, esc, carpeta, C):
    FC = comun.fake_client()
    runners = _runners(FC)
    if os.path.isdir(carpeta):
        shutil.rmtree(carpeta)
    os.makedirs(carpeta)
    tap = os.path.join(carpeta, "tap")
    entorno = dict(C["entorno_comun"], **esc.get("entorno", {}))
    guardado = dict(os.environ)
    with FC._TAP["lock"]:                          # conexiones numeradas desde 0 en cada escenario
        if FC._TAP["f"] is not None:
            FC._TAP["f"].close()
        FC._TAP.update(f=None, dir=None, con={})
    t0 = time.time()
    res, err = [], None
    buf = io.StringIO()
    try:
        os.environ.update(entorno)                 # los runners de fake_client leen YO2_LIMIT_LIST, YO2_WATCH...
        inst_dir = os.path.join(carpeta, "inst")
        os.environ["YO2_PRUEBA_DIR"] = inst_dir    # desconexion/dpcoste leen el estado de la instancia de aquí
        with comun.Instancia(C["offset"], inst_dir, ESTADO, entorno, esc.get("espera", 4.0),
                             semilla=C.get("semilla")) as inst:
            FC._TEST["proc"] = inst.proc           # kill: mata el node de ESTA instancia
            os.environ["YO2_TAP"] = tap
            with contextlib.redirect_stdout(buf):
                for paso in esc["pasos"]:
                    res += runners[paso](C["offset"])
            os.environ.pop("YO2_TAP", None)
            time.sleep(0.5)                        # que los hilos _rx apunten lo último
    except SystemExit as e:
        err = f"SystemExit {e.code}"
    except Exception as e:                         # una excepción del runner = fallo del escenario, no de grabar
        import traceback
        err = traceback.format_exc()
    finally:
        os.environ.clear()
        os.environ.update(guardado)
        with FC._TAP["lock"]:
            if FC._TAP["f"] is not None:
                FC._TAP["f"].close()
            FC._TAP.update(f=None, dir=None)
    with open(os.path.join(carpeta, "salida.txt"), "w", encoding="utf-8") as f:
        f.write(buf.getvalue() + (f"\n\nERROR:\n{err}\n" if err else ""))
    mal = [n for n, ok in res if not ok]
    r = {"escenario": nombre, "ok": len(res) - len(mal), "total": len(res), "mal": mal, "error": err,
         "segundos": round(time.time() - t0, 1)}
    comun.escribir_json(os.path.join(carpeta, "resultado.json"), r)
    return r


def grabar(etiqueta, veces=1, solo=None):
    C = cfg()
    if C["offset"] != comun.OFFSET_PARIDAD:
        sys.exit(f"escenarios.json: offset {C['offset']} != {comun.OFFSET_PARIDAD} (la paridad usa SIEMPRE 2200)")
    for need in (ESTADO, REPLAY):
        if not os.path.exists(need):
            sys.exit(f"falta {need}: python paridad/grabar.py --preparar")
    nombres = [n for n in C["escenarios"] if not solo or n in solo]
    base = os.path.join(comun.OUT, "grab", etiqueta)
    resumen = []
    t0 = time.time()
    with comun.Cerrojo():
        for k in range(veces):
            for n in nombres:
                r = grabar_escenario(n, C["escenarios"][n], os.path.join(base, str(k), n), C)
                resumen.append(dict(r, pasada=k))
                estado = "ERROR" if r["error"] else f"{r['ok']}/{r['total']}"
                print(f"  [{etiqueta} {k}] {n:8s} {estado:>8s}  {r['segundos']:6.1f} s"
                      + (f"  mal: {r['mal']}" if r["mal"] else "") + (f"  ({r['error'].splitlines()[-1]})" if r["error"] else ""))
    comun.escribir_json(os.path.join(base, "resumen.json"), {"etiqueta": etiqueta, "veces": veces,
                                                             "segundos": round(time.time() - t0, 1), "escenarios": resumen})
    print(f"== grabado {etiqueta}: {veces} pasada(s), {round(time.time() - t0)} s -> {base}")
    return resumen


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preparar", action="store_true", help="congela estado y log del replay en _fixtures/")
    ap.add_argument("--forzar", action="store_true", help="con --preparar: vuelve a congelar aunque ya existan")
    ap.add_argument("--log", help="con --preparar: log del replay (por defecto el más reciente con un solo cliente)")
    ap.add_argument("--base", action="store_true", help="línea base: etiqueta 'base', 3 pasadas y máscaras")
    ap.add_argument("--etiqueta", default="actual")
    ap.add_argument("--veces", type=int, default=None)
    ap.add_argument("--escenarios", default="", help="lista separada por comas (por defecto, todos)")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.preparar:
        preparar(a.forzar, a.log)
        return 0
    solo = [x for x in a.escenarios.split(",") if x] or None
    comun.asegurar_datos()                         # _fixtures y paridad-base que falten, de la carpeta común
    if a.base:
        grabar("base", a.veces or 3, solo)
        import comparar
        return comparar.construir_base(solo=solo)
    grabar(a.etiqueta, a.veces or 1, solo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
