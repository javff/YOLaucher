"""Estado persistente del servidor: carga y guardado de state.json (o del almacén SQLite), con cerrojo y copias.

Paso E2 de REESTRUCTURA.md: es el bloque de run.py que va de `STATE_PATH` hasta la copia de arranque, MOVIDO tal cual
(cortar y pegar con los ajustes mínimos). run.py conserva los mismos nombres con

    from yo2srv.datos import estado as _estado
    _estado.setup(SRV)
    from yo2srv.datos.estado import (STATE_PATH, STATE_LOCK, STATE_OK, STATE_KEEP_BOOT, _read_json, load_state_raw,
                                     _write_atomic, save_state_raw, _boot_backup, STORAGE, STORE)

y por eso el namespace (la consola 5799, el panel, los hot_*, `PP.RAW_STATE`…) los sigue encontrando.

Reglas (REESTRUCTURA.md §2.3):
- `setup(srv)` se llama ANTES de importar los nombres: crea el almacén SQLite si toca (necesita `srv.log`).
- Lo que alguien puede reasignar en el namespace se lee como `srv.X` en el momento de usarlo: `srv.log` (hot_logseguro
  lo sustituye) y `srv.STORE`. Quien GUARDE desde otro módulo tiene que llamar a `srv.save_state_raw`, nunca a la de este
  módulo: `sec_ranking_balance` envuelve `ns["save_state_raw"]` (registro de saldos) y `carga.py` también. Aquí dentro
  nadie llama a `save_state_raw`/`load_state_raw`.
- `STATE_LOCK` es el MISMO objeto (RLock) en el namespace y aquí; `STATE_PATH`, `STATE_OK` y `STATE_KEEP_BOOT` son
  constantes de arranque.
- Importar el módulo no abre ficheros ni arranca hilos (solo una consulta de existencia para elegir el almacén);
  `setup` es lo único que puede abrir la base.

Las instancias de prueba apuntan a otro fichero con YO2_STATE (antes `ocg_test.py` y `pvp_test.py` reescribían la línea
`STATE_PATH = …` del texto de run.py; ahora fijan la variable de entorno ANTES de ejecutarlo).
"""
import datetime
import json
import os
import shutil
import sys
import threading
import time

from yo2srv import rutas as _rutas

srv = None   # lo fija setup(srv)

STATE_PATH = _rutas.state_path()   # <emu>/state.json, o la ruta absoluta de YO2_STATE (cuenta de pruebas: personajes creados)
# B24: state.json con cerrojo común y escritura atómica. Antes: load_state devolvía {"players": {}} ante CUALQUIER
# error (p. ej. leer mientras otro hilo escribía) y el siguiente guardado borraba el perfil; save_state truncaba y
# reescribía en sitio. Ahora:
#  - STATE_LOCK (RLock, reentrante) cubre lecturas y escrituras. Quien haga leer-modificar-guardar debe cogerlo
#    alrededor de todo (`with STATE_LOCK: st = load_state(); ...; save_state(st)`) para no pisar a otro hilo.
#  - save_state escribe en .tmp, lo vuelca a disco y hace os.replace (atómico); luego deja la misma versión en
#    state.json.ok (última copia buena).
#  - load_state, si state.json no se puede leer o está roto, usa state.json.ok; si tampoco, LANZA (nunca devuelve
#    un estado vacío que luego se guardaría encima). Solo si no existe ninguno de los dos empieza de cero.
STATE_LOCK = threading.RLock()
STATE_OK = STATE_PATH + ".ok"
STATE_KEEP_BOOT = 10            # copias state.json.bak-arranque-* que se conservan (una por arranque)


def _log(*a):
    """El `log` vivo del namespace de run.py (hot_logseguro lo sustituye); antes de setup(), a stderr."""
    if srv is not None:
        srv.log(*a)
    else:
        print(*a, file=sys.stderr)


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def load_state_raw():
    with STATE_LOCK:
        last = None
        for attempt in range(3):                 # un PermissionError puntual (antivirus, editor) se reintenta
            try:
                return _read_json(STATE_PATH)
            except FileNotFoundError:
                break
            except PermissionError as e:
                last = e; time.sleep(0.05)
            except Exception as e:
                last = e; break
        if last is not None:
            _log(f"[ERROR] state.json ilegible ({last!r}): uso la última copia buena {os.path.basename(STATE_OK)}")
        try:
            return _read_json(STATE_OK)
        except FileNotFoundError:
            pass
        except Exception as e:
            _log(f"[ERROR] {os.path.basename(STATE_OK)} también ilegible: {e!r}")
        if os.path.exists(STATE_PATH) or os.path.exists(STATE_OK):
            raise RuntimeError("state.json ilegible y sin copia buena: no se sigue para no sobrescribir el perfil")
        return {"players": {}}

def _write_atomic(path, text, sync=True):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text); f.flush()
        if sync:
            os.fsync(f.fileno())
    for attempt in range(20):                    # en Windows os.replace falla si otro proceso tiene el destino abierto
        try:
            os.replace(tmp, path)
            return True
        except PermissionError:
            time.sleep(0.05)
    return False

def save_state_raw(st):
    text = json.dumps(st, ensure_ascii=False, indent=1)      # antes del cerrojo: si st no es serializable, no toca nada
    with STATE_LOCK:
        if not _write_atomic(STATE_PATH, text):
            # el fichero sigue abierto por otro proceso tras 1 s: último recurso, escribir en sitio (como antes)
            _log("[ERROR] state.json: os.replace bloqueado; se escribe en sitio")
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                f.write(text)
            try:
                os.remove(STATE_PATH + ".tmp")
            except OSError:
                pass
        if not _write_atomic(STATE_OK, text, sync=False):
            _log("[aviso] no se pudo actualizar state.json.ok")

def _boot_backup():
    """Copia de state.json al arrancar (state.json.bak-arranque-<fecha-hora>); se conservan las STATE_KEEP_BOOT últimas."""
    if not os.path.exists(STATE_PATH):
        return
    try:
        dst = STATE_PATH + ".bak-arranque-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copyfile(STATE_PATH, dst)
        d, base = os.path.split(STATE_PATH)
        olds = sorted(f for f in os.listdir(d) if f.startswith(base + ".bak-arranque-"))
        for f in olds[:-STATE_KEEP_BOOT]:
            os.remove(os.path.join(d, f))
        try:                                     # si state.json es válido y aún no hay .ok, se crea ya
            _read_json(STATE_PATH)
            if not os.path.exists(STATE_OK):
                shutil.copyfile(STATE_PATH, STATE_OK)
        except Exception as e:
            _log(f"[ERROR] state.json ilegible al arrancar ({e!r}); copia guardada en {os.path.basename(dst)}")
    except Exception as e:
        _log(f"[aviso] copia de arranque de state.json: {e!r}")

# Almacén SQLite (storage/NOTES.md). YO2_STORAGE=sqlite: load_state_raw/save_state_raw pasan a un historial de eventos
# encadenado en <STATE_PATH>.sqlite (cifrado con SQLCipher); el resto del código no cambia (mismo dict). La primera
# vez importa state.json sin tocarlo. Por defecto (json) todo sigue como arriba.
# 07-10: si ya existe la base junto al state (se migró), por defecto sqlite: arrancar sin la variable volvería al JSON
# viejo y se perderían los cambios hechos desde entonces. Las instancias de prueba (state-prueba.json sin base) siguen en json.
STORAGE = (os.environ.get("YO2_STORAGE") or ("sqlite" if os.path.exists(STATE_PATH + ".sqlite") else "json")).strip().lower()
STORE = None   # lo crea setup(srv) si STORAGE == "sqlite" (o lo hereda del namespace, como `globals().get("STORE")`)


def _sqlite_load_state_raw():
    return srv.STORE.load()

def _sqlite_save_state_raw(st):
    srv.STORE.save(st)

def _sqlite_boot_backup():
    try:
        srv.STORE.boot_backup(keep=STATE_KEEP_BOOT)
    except Exception as e:
        _log(f"[aviso] copia de arranque de la base: {e!r}")


def setup(srv_):
    """Engancha el módulo al servidor y deja listo el almacén. Idempotente. Se llama antes de importar los nombres a run.py
    (así `STORE`, `load_state_raw`, `save_state_raw` y `_boot_backup` ya son los de STORAGE cuando run.py los copia)."""
    global srv, STORE, load_state_raw, save_state_raw, _boot_backup
    srv = srv_
    STORE = srv.ns.get("STORE") or STORE
    if STORAGE == "sqlite":
        if STORE is None:
            from storage import open_store as _open_store
            STORE = _open_store(STATE_PATH, log=srv.log, lock=STATE_LOCK)
        srv.log(f"[almacén] YO2_STORAGE=sqlite: {STORE.db_path} ({STORE.head[0]} eventos)")
        srv.ns["STORE"] = STORE                 # _sqlite_* la leen como srv.STORE (y run.py la importará con el mismo valor)

        load_state_raw = _sqlite_load_state_raw
        save_state_raw = _sqlite_save_state_raw
        _boot_backup = _sqlite_boot_backup
        for f, nombre in ((load_state_raw, "load_state_raw"), (save_state_raw, "save_state_raw"),
                          (_boot_backup, "_boot_backup")):
            f.__name__ = f.__qualname__ = nombre
    return STORE
