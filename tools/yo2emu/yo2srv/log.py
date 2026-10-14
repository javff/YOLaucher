"""Log del servidor: `log()`, máscaras de logseguro, excepthooks y `_admin_ev` (REESTRUCTURA.md §2.2, paso E1a).

Paso E1a: esto es lo que hasta el 10-10 estaba en las líneas 1-125 de run.py (`log`, `logf`, `log_path`, `_LOG_LOCK`,
`_LOG_T`, `_logseg`, `_LOGSEG`, `_log_mask`, `_rx_line`, `_thread_excepthook`, `_admin_ev`). run.py conserva TODOS esos
nombres (`from yo2srv.log import (...)` + `iniciar()`), así que la consola 5799, el panel, ocg_test/pvp_test y los
hot_*.py los siguen encontrando.

Lo reasignable en caliente se busca en el namespace de run.py en CADA llamada (regla de oro de §2.3), no como global de
este módulo: `_log_mask` (test_logseguro la sustituye por la consola para simular el log viejo), `log` (hot_logseguro.py
la envuelve; los excepthooks escriben por la envoltura). `_LOGSEG` es la MISMA lista que usa run.py (hot_logseguro hace
`_LOGSEG[:] = [m]`).

Diferencia de comportamiento, deliberada: si la máscara lanzara, la línea se oculta (antes la excepción salía de `log()`).
Lo demás es igual (B26/B27 de run.py):
  - la línea pasa por la máscara de logseguro ANTES de escribirse (nunca se escribe una contraseña en claro);
  - el fichero va primero (con candado) y el print después; ninguno de los dos puede lanzar;
  - cada vez que cambia el segundo va antes una línea «[t] HH:MM:SS» (no rompe los grep '^\\[srv').

Importar este módulo NO abre ficheros ni arranca hilos (yo2srv/tests/test_importar.py): el fichero se abre en `iniciar()`.

Rotación (P1, 11-10, empaquetado): los logs crecían sin límite. `limites()` da el tope por fichero y cuántos se guardan:
  YO2_LOG_MAX_MB     MB por fichero de log antes de pasar a uno nuevo (AAAAMMDD-HHMMSS.log con la hora del cambio, en la
                     misma carpeta: «el log más reciente» sigue siendo el último por nombre); también corta la traza de
                     un duelo del motor (logs/ocg) que pase de ese tamaño
  YO2_LOG_KEEP       logs AAAAMMDD-HHMMSS.log que se guardan en la carpeta (los más viejos se borran)
  YO2_LOG_KEEP_OCG   trazas del motor (logs/ocg/*-duelo*.jsonl) que se guardan
Por defecto: en modo paquete 20 MB, 10 logs y 200 trazas; en desarrollo 0 = sin tope (como antes: los logs y las trazas
del owner son material de investigación y las pruebas rejuegan trazas viejas por nombre). Solo se borran ficheros con
ESOS nombres y en ESAS carpetas.
"""
import datetime
import importlib.util
import os
import re
import sys
import threading
import time
import traceback

from . import rutas

_LOGSEG = []          # [módulo | None]: se carga una sola vez (como `_LOGSEG` de run.py)


def logseguro(emu=None):
    """research/cuentas/logseguro.py (reglas de máscara). None si no carga (entonces mandan las máscaras brutas)."""
    if not _LOGSEG:
        try:
            ruta = os.path.join(emu or rutas.EMU, "research", "cuentas", "logseguro.py")
            sp = importlib.util.spec_from_file_location("yo2_logseguro", ruta)
            m = importlib.util.module_from_spec(sp)
            sp.loader.exec_module(m)
            sys.modules["yo2_logseguro"] = m
            _LOGSEG.append(m)
        except Exception:
            _LOGSEG.append(None)
    return _LOGSEG[0]


def log_mask(line):
    """Línea del log sin datos sensibles (0x3003, contraseñas, códigos, volcados con esos mensajes)."""
    m = logseguro()
    if m is not None:
        return m.mask_line(line)
    if line.startswith(("[srv:", "[game:", "[persistencia] DPAUTH")) and any(k in line for k in (
            "<- 0x3003", "<- 0x4288", "<- 0x4300", "<- 0x81", "ID[%s]", "PW[%s]", "パスワード", "password:%s", "DPAUTH")):
        return line.split("  ||  ")[0].split(" md5=")[0] + " [oculto: logseguro.py no cargado]"
    if line[:8].isalnum() and line[8:10] == "  ":                  # volcado hex del gancho: sin el módulo, fuera
        return line[:8] + "  [volcado oculto: logseguro.py no cargado]"
    return line


def rx_line(port, msg_id, param, data, ok):
    """«[srv:p] <- 0x…» de un mensaje recibido: hex + repr, salvo 0x3003/0x4288/0x4300/0x81xx (logseguro)."""
    m = logseguro()
    if m is not None:
        return m.rx_line(port, msg_id, param, data, ok)
    head = f"[srv:{port}] <- 0x{msg_id:04x} #{param} len={len(data)} md5={'ok' if ok else 'MAL'}"
    if msg_id in (0x3003, 0x4288, 0x4300) or (msg_id >> 8) == 0x81:
        return head + " [contenido oculto]"
    return f"{head} {data.hex(' ')} | {data!r}"[:400]


def reconfigurar_stdout():
    """B26: con stdout redirigido Python usa cp1252 y una línea en japonés lanzaba en print(). Nunca lanza."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ------------------------------------------------------------------------------------------------ rotación (P1)
LOG_RE = re.compile(r"^\d{8}-\d{6}(?:-\d+)?\.log$")              # los logs de run.py (y solo esos)
TRAZA_RE = re.compile(r"^\d{8}-\d{6}-duelo.+\.jsonl$")           # las trazas del motor (ocg_duel.TracedCore)
_ORDEN_RE = re.compile(r"^(\d{8}-\d{6})(?:-(\d+))?\.")


def _entero(v, defecto):
    try:
        return max(0, int(float(v))) if v not in (None, "") else defecto
    except (TypeError, ValueError):
        return defecto


def limites(env=None):
    """(bytes máx. por fichero, logs que se guardan, trazas del motor que se guardan); 0 = sin tope. Ver el docstring."""
    e = os.environ if env is None else env
    paq = rutas.paquete(e)
    mb = _entero(e.get("YO2_LOG_MAX_MB"), 20 if paq else 0)
    return (mb * 1024 * 1024, _entero(e.get("YO2_LOG_KEEP"), 10 if paq else 0),
            _entero(e.get("YO2_LOG_KEEP_OCG"), 200 if paq else 0))


def podar(carpeta, patron, guardar, por_fecha=False):
    """Borra de `carpeta` los ficheros cuyo NOMBRE casa con `patron` hasta dejar los `guardar` más recientes (por nombre,
    que lleva la fecha; o por fecha de modificación con por_fecha). guardar <= 0: nada. Devuelve los borrados. Nunca lanza
    (un fichero abierto por otro proceso se salta)."""
    if not guardar or guardar <= 0:
        return []
    try:
        fs = [f for f in os.listdir(carpeta) if patron.match(f) and os.path.isfile(os.path.join(carpeta, f))]
    except OSError:
        return []
    if por_fecha:
        def clave(f):
            try:
                return (os.path.getmtime(os.path.join(carpeta, f)), f)
            except OSError:
                return (0.0, f)
        fs.sort(key=clave)
    else:                                         # por nombre, con «-1», «-2» (mismo segundo) DESPUÉS del que no lo lleva
        def clave(f):
            m = _ORDEN_RE.match(f)
            return (m.group(1), int(m.group(2) or 0), f) if m else (f, 0, f)
        fs.sort(key=clave)
    borrados = []
    for f in fs[:max(0, len(fs) - guardar)]:
        try:
            os.remove(os.path.join(carpeta, f))
            borrados.append(f)
        except OSError:
            pass
    return borrados


def _nombre_nuevo(logdir, actual=None):
    """<logdir>/AAAAMMDD-HHMMSS.log que no exista y que vaya DESPUÉS de `actual` por nombre (si es del mismo segundo,
    o de uno posterior por un reloj que retrocede: -1, -2…). Así «el último por nombre» es siempre el que se escribe."""
    base, n = datetime.datetime.now().strftime("%Y%m%d-%H%M%S"), 0
    m = _ORDEN_RE.match(os.path.basename(actual or ""))
    if m and m.group(1) >= base:
        base, n = m.group(1), int(m.group(2) or 0) + 1
    while True:
        ruta = os.path.join(logdir, f"{base}-{n}.log" if n else f"{base}.log")
        if not os.path.exists(ruta):
            return ruta
        n += 1


class Log:
    """Objeto con `__call__(*a)`: lo que hoy es la función `log` de run.py. `Log(logdir)` abre
    `<logdir>/AAAAMMDD-HHMMSS.log` (modo "w", como run.py); `Log(fichero=f)` usa un fichero ya abierto (pruebas).
    max_bytes/guardar (P1): al pasar de max_bytes se cierra el fichero y se abre otro con la hora del cambio, y se dejan
    solo los `guardar` logs más recientes de la carpeta; `al_rotar(f, ruta)` se llama con el nuevo (run.py pone al día
    `logf`/`log_path`). 0 = sin tope (lo de siempre). Con `fichero=` no se rota nunca."""

    def __init__(self, logdir=None, *, fichero=None, mascara=log_mask, consola=True, max_bytes=0, guardar=0,
                 al_rotar=None):
        self.mascara = mascara
        self.consola = consola
        self._lock = threading.Lock()
        self._t = [None]                 # = _LOG_T de run.py (segundo de la última marca [t])
        self.max_bytes, self.guardar, self.al_rotar = int(max_bytes or 0), int(guardar or 0), al_rotar
        self._n = 0                      # bytes escritos en el fichero actual (aprox.: caracteres)
        self.logdir = None
        if fichero is not None:
            self.f, self.path = fichero, getattr(fichero, "name", None)
            self.max_bytes = 0
        else:
            logdir = self.logdir = logdir or rutas.logdir()
            os.makedirs(logdir, exist_ok=True)
            self.path = os.path.join(logdir, datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".log")
            self.f = open(self.path, "w", encoding="utf-8")
            podar(logdir, LOG_RE, self.guardar)

    def _rotar(self):
        """Con el candado cogido: cierra el fichero lleno y abre otro. Si no se puede abrir, sigue con el de antes."""
        try:
            ruta = _nombre_nuevo(self.logdir, self.path)
            f = open(ruta, "w", encoding="utf-8")
        except Exception:
            self._n = 0                  # no reintentar en cada línea; se probará otra vez tras max_bytes más
            return
        viejo, self.f, self.path, self._n = self.f, f, ruta, 0
        self._t[0] = None                # el fichero nuevo empieza con su «[t] HH:MM:SS»
        try:
            viejo.write(f"[log] sigue en {os.path.basename(ruta)}\n")
            viejo.close()
        except Exception:
            pass
        if self.al_rotar is not None:
            try:
                self.al_rotar(f, ruta)
            except Exception:
                pass
        podar(self.logdir, LOG_RE, self.guardar)

    def __call__(self, *a):
        """Línea al log (fichero primero) y a la consola. Nunca lanza."""
        try:
            line = " ".join(str(x) for x in a)
        except Exception as e:
            line = f"[log] línea no imprimible: {e!r}"
        try:
            line = self.mascara(line)
        except Exception:
            line = "[log] línea oculta: falló la máscara"          # ante la duda, nunca en claro
        now = time.strftime("%H:%M:%S")
        try:
            with self._lock:
                if now != self._t[0]:
                    self._t[0] = now
                    self.f.write(f"[t] {now}\n")
                    self._n += 13
                self.f.write(line + "\n")
                self.f.flush()
                if self.max_bytes:
                    self._n += len(line) + 1          # caracteres ~ bytes (de sobra para un tope de MB)
                    if self._n >= self.max_bytes:
                        self._rotar()
        except Exception:
            pass
        if self.consola:
            try:
                print(line)
            except Exception:
                pass

    def cerrar(self):
        try:
            self.f.close()
        except Exception:
            pass


def _hooks_para(log):
    """(hook de hilos, hook del hilo principal) que escriben por `log` (una llamada, buscada en cada excepción)."""
    def hilo(args):
        if args.exc_type is SystemExit:
            return
        nombre = args.thread.name if args.thread is not None else "?"
        tb = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
        log(f"[EXC] hilo {nombre}: " + tb.replace("\n", " | "))

    def principal(t, v, tb):
        log("[EXC] " + "".join(traceback.format_exception(t, v, tb)).replace("\n", " | "))

    return hilo, principal


def instalar_excepthooks(log):
    """B02: las excepciones de los hilos y del hilo principal van al log como [EXC]. Devuelve los hooks viejos."""
    viejos = (threading.excepthook, sys.excepthook)
    threading.excepthook, sys.excepthook = _hooks_para(log)
    return viejos


def restaurar_excepthooks(viejos):
    threading.excepthook, sys.excepthook = viejos


# ------------------------------------------------------------------------------------------------ el log de run.py
_NS = {}            # namespace compartido (run.py: globals()); lo fija iniciar()
_VIVO = [None]      # el Log del proceso (lo que escribe `log`)


def _mascara_ns(line):
    """`_log_mask` del namespace, buscada en CADA línea (test_logseguro la sustituye por la consola); sin ella, la de aquí."""
    f = _NS.get("_log_mask")
    return (f if callable(f) else log_mask)(line)


def _log_ns(*a):
    """El `log` del namespace en este momento (puede ser la envoltura de hot_logseguro); sin él, el de aquí."""
    f = _NS.get("log")
    return (f if callable(f) else log)(*a)


def log(*a):
    """El `log` de run.py: línea al log (fichero primero) y a la consola. Nunca lanza. Antes de `iniciar()` va a stderr
    (y tapada, por si acaso)."""
    lg = _VIVO[0]
    if lg is not None:
        return lg(*a)
    try:
        print(log_mask(" ".join(str(x) for x in a)), file=sys.stderr)
    except Exception:
        pass


_thread_excepthook, _sys_excepthook = _hooks_para(_log_ns)


def iniciar(ns, logdir):
    """Lo que hacían las líneas 28-32, 34-40 y 117-118 de run.py: abre `<logdir>/AAAAMMDD-HHMMSS.log` (modo "w"),
    pasa stdout a utf-8 e instala los excepthooks. `ns` es el namespace compartido (globals() de run.py).
    Devuelve (logf, log_path, _LOG_LOCK, _LOG_T): los cuatro nombres que run.py conserva."""
    global _NS
    _NS = ns                  # el dict de verdad, no una copia: lo reasignado en caliente se ve en la siguiente línea
    max_bytes, guardar, _ = limites()
    lg = Log(logdir, mascara=_mascara_ns, max_bytes=max_bytes, guardar=guardar,
             al_rotar=lambda f, ruta: ns.update(logf=f, log_path=ruta))   # P1: el panel lee log_path del namespace
    _VIVO[0] = lg
    reconfigurar_stdout()
    threading.excepthook = _thread_excepthook
    sys.excepthook = _sys_excepthook
    return lg.f, lg.path, lg._lock, lg._t


# ------------------------------------------------------------------------------------------------ panel
def _admin_ev(name, *a, **k):
    """Panel (research/admin/eventos.py, 07-10): apunta un evento para «Actividad». Sin panel no hace nada y un
    fallo aquí nunca rompe el juego."""
    try:
        m = sys.modules.get("yo2_eventos")
        f = getattr(m, name, None) if m is not None else None
        return f(*a, **k) if callable(f) else None
    except Exception:
        return None
