"""Modo paquete y salida limpia de run.py (P1, empaquetado para el Discord, 11-10; paquete/RUTAS.md).

    from yo2srv import paquete as _paquete          # al principio de run.py (anota la hora de arranque)
    _paquete.comprobar(SRV)                          # tras arranque.cargar(SRV), antes de abrir puertos

`comprobar(srv)` hace dos cosas:
  1. SIEMPRE: registra (atexit) el borrado de %LOCALAPPDATA%\\YO2Online\\consola_<ADMIN_PORT>.txt al salir (antes se
     quedaba un token por arranque: cientos). Solo se borra si lo escribió ESTE proceso (fecha >= su arranque): si
     run.py sale porque los puertos ya están ocupados (comprobar_puertos), el token es el de la instancia que los tiene
     y NO se toca. Un cierre a la fuerza (taskkill /F, cerrar la ventana) no pasa por atexit: ese token se queda, como
     antes.
  2. Solo con YO2_PAQUETE=1: para el arranque, con un mensaje en INGLÉS (lo leen los jugadores que hospedan), si falta
     algún dato (rutas.faltan_datos) o si el motor de reglas (ocg_duel: duelcore + CardMap) no carga. Sin reglas el
     servidor arrancaba igual y los duelos no funcionaban (p. ej. sin Pillow, antes de P1). En desarrollo no para nunca:
     ahí el `[ocg] ocg_duel.py no cargado` sigue siendo solo una línea del log, como siempre.

Importar este módulo no abre ficheros ni arranca hilos.
"""
import atexit
import importlib
import os
import sys
import time

from . import rutas

_T0 = time.time()           # arranque del proceso (run.py importa este módulo antes de abrir la consola)
SALIDA_FALTAN = 3           # código de salida: faltan datos del paquete
SALIDA_OCG = 4              # código de salida: el motor de reglas no carga


def _log(srv, texto):
    try:
        srv.log(texto)
    except Exception:
        pass


def parar(srv, titulo, detalles=(), codigo=SALIDA_OCG):
    """Mensaje claro (inglés) a stderr y al log, y SystemExit(codigo). Solo se usa en modo paquete."""
    lineas = ["", "=" * 78, f"YO2 server: {titulo}", ""] + [f"  {d}" for d in detalles] + [
        "", "The server cannot run like this and has stopped. Re-extract the package (or run the launcher's",
        "first-start setup again) and try again. If it keeps happening, send this message and the log to the host.",
        "=" * 78, ""]
    texto = "\n".join(lineas)
    _log(srv, f"[paquete] PARADA: {titulo} | " + " | ".join(str(d) for d in detalles))
    try:
        print(texto, file=sys.stderr, flush=True)
    except Exception:
        pass
    raise SystemExit(codigo)


def _motor(srv):
    """None si el motor de reglas está listo; si no, (motivo en inglés, detalle)."""
    ocg = srv.ns.get("OCG")
    if ocg is None:
        # arranque.cargar solo dejó «[ocg] ocg_duel.py no cargado: …» en el log; se repite el import para el motivo
        try:
            ocg = importlib.import_module("ocg_duel")
        except Exception as e:                          # noqa: BLE001
            return "the duel rules engine (ocg_duel) could not be loaded", f"{type(e).__name__}: {e}"
        srv.ns["OCG"] = ocg
    try:
        ocg.core(srv.log)                               # el mismo node que arrancó warm_up (con su candado)
        ocg.cardmap()
    except Exception as e:                              # noqa: BLE001
        return "the duel rules engine (duelcore / card map) did not start", f"{type(e).__name__}: {e}"
    return None


def comprobar(srv, env=None):
    """Ver el docstring del módulo. Devuelve True si está en modo paquete (y todo está bien)."""
    _registrar_token(srv)
    if not rutas.paquete(env):
        return False
    falta = rutas.faltan_datos(env)
    if falta:
        parar(srv, "some files the server needs are missing", ["Missing:"] + [f"  {f}" for f in falta]
              + ["", f"Game data folder (YO2_JUEGO_DATOS): {rutas.juego_datos(env)}",
                 f"Card database folder (YO2_VENDOR):  {rutas.vendor(env)}"], codigo=SALIDA_FALTAN)
    fallo = _motor(srv)
    if fallo is not None:
        parar(srv, fallo[0], [fallo[1], "", f"Node: {rutas.node(env)}", f"Engine: {rutas.duelcore(env)}",
                              f"Card database folder (YO2_VENDOR): {rutas.vendor(env)}"], codigo=SALIDA_OCG)
    _log(srv, f"[paquete] modo paquete: datos {rutas.juego_datos(env)}, vendor {rutas.vendor(env)}, motor "
              f"{rutas.duelcore(env)}; estado {srv.ns.get('STATE_PATH')}; log {srv.ns.get('LOGDIR')}; reglas listas")
    return True


# ------------------------------------------------------------------------------------------------ token de la consola
def borrar_token(puerto, desde=None):
    """Borra consola_<puerto>.txt si se escribió a partir de `desde` (por defecto, el arranque de este proceso).
    Devuelve True si lo borró. Nunca lanza."""
    try:
        ruta = rutas.token_consola(puerto)
        if os.path.getmtime(ruta) >= (_T0 if desde is None else desde) - 1.0:
            os.remove(ruta)
            return True
    except (OSError, TypeError, ValueError):
        pass
    return False


_REGISTRADO = []


def _registrar_token(srv):
    if _REGISTRADO:
        return
    _REGISTRADO.append(True)

    def al_salir():
        try:
            borrar_token(srv.ns.get("ADMIN_PORT"))
        except Exception:
            pass
    atexit.register(al_salir)
