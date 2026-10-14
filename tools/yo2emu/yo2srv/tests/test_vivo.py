"""Prueba VIVA de S1: arranca run.py como instancia de PRUEBA (nunca la viva) y comprueba, por SU consola, que
`SRV` existe y está enlazado al namespace, que las rutas resuelven a lo mismo que hoy y que el registro de banderas
lee, en vivo, EXACTAMENTE los valores del perfil vanilla.

    python yo2srv/tests/test_vivo.py [--offset N | --banda 1900]      (sale con 1 si algo falla)

Cómo: la consola de la instancia (5799 + offset, con su token) ejecuta un script que escribe un JSON; la consola solo
contesta «ok»/«error». Sin variables YO2_* de juego (se limpian las del entorno de esta prueba): lo que se compara es el
valor por defecto del código, no el de un entorno de pruebas anteriores. Excepción: YO2_LOGIN_ESTRICTO=1 (la instancia de
prueba de comun.Instancia lo apaga para que fake_client entre).

Ruido conocido: no. Offset 0, 3000 y los puertos del vivo (5730-5799, 8730, 8790) los rechaza comun.Instancia.
"""
import argparse
import json
import os
import socket
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
EMU = os.path.normpath(os.path.join(AQUI, "..", ".."))
PARIDAD = os.path.join(EMU, "paridad")

SCRIPT = r'''
import json, os, sys, traceback
res = {"ok": True, "checks": {}}
try:
    SRV = NS["SRV"]
    from yo2srv import config as C, rutas as RU
    res["checks"]["SRV.HUB is HUB"] = SRV.HUB is NS["HUB"]
    res["checks"]["SRV.hub is HUB (alias)"] = SRV.hub is NS["HUB"]
    res["checks"]["SRV.ns is globals()"] = SRV.ns is NS
    res["checks"]["SRV.LP is LP, SRV.PVP is PVP, SRV.STORE is STORE, SRV.CTX is CTX"] = (
        SRV.LP is NS["LP"] and SRV.PVP is NS["PVP"] and SRV.STORE is NS["STORE"] and SRV.CTX is NS["CTX"])
    res["checks"]["SRV.cfg.ns is globals()"] = SRV.cfg.ns is NS
    res["checks"]["reasignar en el namespace se ve desde SRV"] = (
        NS.__setitem__("_PRUEBA_S1", 1) or SRV._PRUEBA_S1 == 1) and NS.pop("_PRUEBA_S1") == 1
    res["checks"]["bus y router sin nada registrado (S1)"] = SRV.bus.eventos() == [] and SRV.router.tabla() == {}
    res["checks"]["hot (R4): módulos registrados, los 3 noreload con motivo y hot() en la consola"] = (
        len(SRV.hot.registro) >= 16 and callable(NS.get("hot"))
        and all(SRV.hot.registro[n].noreload and SRV.hot.registro[n].motivo for n in ("duelcore", "red.proto", "red.conexion")))
    res["checks"]["el log de SRV es el de run.py"] = SRV.ns["log"] is NS["log"]
    # rutas = lo de run.py
    r = SRV.rutas
    res["checks"]["rutas.emu == HERE"] = os.path.normcase(r.emu) == os.path.normcase(NS["HERE"])
    res["checks"]["rutas.game == GAME"] = r.game == NS["GAME"]
    res["checks"]["rutas.packinfo == PACKINFO"] = r.packinfo == NS["PACKINFO"]
    res["checks"]["rutas.state_path() == STATE_PATH"] = RU.state_path() == NS["STATE_PATH"]
    res["checks"]["rutas.logdir() == LOGDIR"] = RU.logdir() == NS["LOGDIR"]
    res["checks"]["rutas.port_offset() == PORT_OFFSET"] = RU.port_offset() == NS["PORT_OFFSET"]
    res["checks"]["el fichero del token de la consola está donde dice rutas"] = os.path.exists(
        RU.token_consola(NS["ADMIN_PORT"]))
    res["checks"]["net_hook.js y www existen"] = os.path.isfile(r.net_hook) and os.path.isdir(r.www)
    # banderas: vivo == vanilla
    cfg = SRV.cfg
    filas = cfg.todas()
    res["n_banderas"] = len(filas)
    res["no_cargadas"] = [d["id"] for d in filas if not d["existe"]]
    res["distintas"] = {}
    for f in C.REGISTRO:
        if f.deploy or f.liga[0] == "env" or f.vanilla is C.DERIVADA:
            continue
        v = cfg.leer(f.id)
        if v is C.NO_CARGADA:
            continue
        if v != f.vanilla:
            res["distintas"][f.id] = [repr(v), repr(f.vanilla)]
    res["comparadas"] = sum(1 for f in C.REGISTRO if not (f.deploy or f.liga[0] == "env" or f.vanilla is C.DERIVADA))
    # lo del panel: mismo valor que su flags()
    panel = {d["id"]: d for d in NS["ADMIN_WEB"].flags()}
    dif_panel = {}
    for fid, d in panel.items():
        v = cfg.leer(fid)
        if (v is C.NO_CARGADA) == d["existe"] or (d["existe"] and v != d["valor"]):
            dif_panel[fid] = [repr(v), repr(d["valor"])]
    res["panel_n"] = len(panel)
    res["panel_distintas"] = dif_panel
    res["checks"]["el panel no tiene banderas que el registro no conozca"] = all(i in C.BY_ID for i in panel)
    res["estado"] = SRV.estado()
    # lo que Server NO debe haber metido en el namespace
    res["ns_con_nombre_de_servicio"] = [k for k in ("cfg", "bus", "router", "rutas", "_HOT_LAST") if k in NS]   # `hot` (R4) es la función de la consola
except Exception:
    res["ok"] = False
    res["error"] = traceback.format_exc()
with open(SALIDA, "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=1, default=repr)
'''


def consola(off, linea):
    tok = open(os.path.join(os.environ.get("LOCALAPPDATA", ""), "YO2Online", f"consola_{5799 + off}.txt"),
               encoding="ascii").read().strip()
    s = socket.create_connection(("127.0.0.1", 5799 + off), timeout=60)
    try:
        s.sendall(("AUTH " + tok + "\n" + linea + "\n").encode("utf-8"))
        s.shutdown(socket.SHUT_WR)
        return s.recv(65536).decode("utf-8", "replace").strip()
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offset", type=int, default=None)
    ap.add_argument("--banda", type=int, default=1900)
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if PARIDAD not in sys.path:
        sys.path.insert(0, PARIDAD)
    import comun
    estado = os.path.join(comun.FIX, "state-paridad.json")
    if not os.path.exists(estado):
        sys.exit(f"falta {estado} (python paridad/grabar.py --preparar)")
    if a.offset is not None:
        off = a.offset
    else:
        off = next((a.banda + 10 * k for k in range(20) if not comun.offset_valido(a.banda + 10 * k)
                    and not comun.ocupados(a.banda + 10 * k)), None)
        if off is None:
            sys.exit(f"no queda ningún offset libre en la banda {a.banda}")
    motivo = comun.offset_valido(off)
    if motivo:
        sys.exit(motivo)
    for k in [k for k in os.environ if k.startswith("YO2_")]:      # sin restos de otras pruebas: valores por defecto
        del os.environ[k]
    carpeta = os.path.join(comun.OUT, "yo2srv-vivo", f"inst-{off}")
    if os.path.isdir(carpeta):
        import shutil
        shutil.rmtree(carpeta, ignore_errors=True)
    os.makedirs(carpeta, exist_ok=True)
    salida = os.path.join(carpeta, "volcado.json")
    script = os.path.join(carpeta, "volcado.py")
    with open(script, "w", encoding="utf-8", newline="\n") as f:
        f.write(SCRIPT)
    entorno = {"YO2_LOGIN_ESTRICTO": "1", "YO2_ADMIN_PANEL": "1",
               "YO2_ADMIN_TOKEN_FILE": os.path.join(carpeta, "admin_token.txt")}
    linea = (f"exec(compile(open({script!r}, encoding='utf-8').read(), 'yo2srv-vivo', 'exec'), "
             f"{{'NS': globals(), 'SALIDA': {salida!r}}})")
    print(f"== yo2srv en vivo: instancia de prueba en el offset {off}")
    # la comprobación del plan, tal cual, además del script: SRV.HUB is HUB por la consola, resultado a fichero
    simple = os.path.join(carpeta, "simple.txt")
    linea_simple = f"open({simple!r}, 'w').write(str(SRV.HUB is HUB))"
    with comun.Instancia(off, carpeta, estado, entorno, espera_duelcore=3.0):
        r1 = consola(off, linea_simple)
        r2 = consola(off, linea)
        for _ in range(50):
            if os.path.exists(salida):
                break
            time.sleep(0.1)
    res = []

    def check(nombre, cond, info=""):
        res.append((nombre, bool(cond)))
        print(f"  {'OK ' if cond else 'MAL'} {nombre}" + (f"  [{info}]" if info and not cond else ""))

    check("la consola contesta «ok» (SRV.HUB is HUB)", r1 == "ok", r1)
    check("la consola contesta «ok» (volcado)", r2 == "ok", r2)
    check("SRV.HUB is HUB (por la consola, fichero aparte)",
          os.path.exists(simple) and open(simple, encoding="utf-8").read() == "True")
    d = comun.leer_json(salida) or {}
    check("el volcado se escribió sin error", d.get("ok") is True, d.get("error", "sin fichero")[-600:])
    for nombre, v in (d.get("checks") or {}).items():
        check(nombre, v is True)
    check("ninguna bandera 'no cargada' en la instancia", not d.get("no_cargadas"), d.get("no_cargadas"))
    n = d.get("comparadas", 0)
    check(f"banderas vivas == vanilla ({n} comparadas de {d.get('n_banderas')})", n > 100 and not d.get("distintas"),
          json.dumps(d.get("distintas"), ensure_ascii=False))
    check(f"banderas del panel ({d.get('panel_n')}): mismo valor por SRV.cfg y por ADMIN_WEB.flags()",
          (d.get("panel_n") or 0) >= 35 and not d.get("panel_distintas"), json.dumps(d.get("panel_distintas")))
    check("Server no mete servicios en el namespace", not d.get("ns_con_nombre_de_servicio"),
          d.get("ns_con_nombre_de_servicio"))
    bad = [n for n, ok in res if not ok]
    print(f"== RESULTADO yo2srv en vivo: {len(res) - len(bad)}/{len(res)} bien" + (f"; mal: {bad}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
