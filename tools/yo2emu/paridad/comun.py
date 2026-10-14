"""Piezas comunes de paridad/ (REESTRUCTURA.md §3.1-§3.3): rutas, puertos permitidos, cerrojo de la paridad e
instancia de PRUEBA de run.py. Nada de aquí habla con el servidor vivo.

Puertos de una instancia con desplazamiento o: 5730-5799 + o (puerta, cuentas, lobby, salas, duelo, consola),
8730 + o (HTTP), 8790 + o (panel) y 8791 + o (API de gestión del lanzador, 11-10). Prohibido: o = 0 (el vivo), o = 3000
(8730 = HTTP del vivo) y cualquier o que pise 5730-5799, 8730, 8790, 8791 (vivo) o 6463 (Discord).
"""
import glob
import json
import os
import shutil
import socket
import subprocess
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
EMU = os.path.dirname(AQUI)                       # tools/yo2emu de ESTA copia de trabajo
OUT = os.path.join(AQUI, "_out")
FIX = os.path.join(AQUI, "_fixtures")
# Datos comunes a todas las worktrees (12-10, fuera de cualquier worktree para poder quitarlas): el cerrojo de la paridad
# (.lock) y la copia maestra de los datos de prueba que no van en git (_fixtures/, _out/base.json, _out/paridad-base/).
# worktree.ps1 copia esos datos a cada worktree nueva; asegurar_datos() rellena lo que falte al arrancar la batería.
COMUN = os.environ.get("YO2_PARIDAD_COMUN") or os.path.join(os.path.expanduser("~"), "DuelCards-trabajo",
                                                             "paridad-comun")
DATOS_COMUNES = [("_fixtures", "state-paridad.json"), ("_fixtures", "replay.log"), ("_out", "base.json"),
                 ("_out", "paridad-base", "paridad-base.json"), ("_out", "paridad-base", "mascara_por_id.json")]


def asegurar_datos(avisar=True):
    """Copia (nunca enlaza ni pisa) de COMUN a esta worktree los datos de prueba que le falten. Copia y no unión: rehacer
    la base en una worktree (--etiqueta base, grabar.py --base) no debe cambiar la de las demás."""
    copiados = []
    for partes in DATOS_COMUNES:
        dst, src = os.path.join(AQUI, *partes), os.path.join(COMUN, *partes)
        if os.path.exists(dst) or not os.path.exists(src):
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        copiados.append(os.path.join(*partes))
    if copiados and avisar:
        print(f"   datos de prueba copiados de {COMUN}: {', '.join(copiados)}")
    return copiados


def ruta_cerrojo():
    """YO2_CERROJO manda; si no, el cerrojo común (COMUN/.lock) si existe la carpeta; si no, el de esta worktree."""
    if os.environ.get("YO2_CERROJO"):
        return os.environ["YO2_CERROJO"]
    if os.path.isdir(COMUN):
        return os.path.join(COMUN, ".lock")
    return os.path.join(OUT, ".lock")
OFFSET_PARIDAD = 2200                             # §3.1: la paridad SIEMPRE con 2200 y cerrojo
VIVO = set(range(5730, 5800)) | {8730, 8790, 8791}
OTROS = {6463}                                    # Discord


def puertos(o):
    return sorted({p + o for p in range(5730, 5800)} | {8730 + o, 8790 + o, 8791 + o})


def offset_valido(o):
    """None si o es usable; si no, el motivo."""
    if o in (0, 3000):
        return f"offset {o} prohibido (0 = el vivo, 3000 = su HTTP)"
    malos = sorted(set(puertos(o)) & (VIVO | OTROS))
    if malos:
        return f"offset {o} pisa puertos del vivo u ocupados por otros: {malos[:5]}"
    return None


def ocupados(o):
    """Puertos de la instancia o que ya escuchan (otra instancia viva o de otro agente)."""
    res = []
    for p in puertos(o):
        s = socket.socket()
        s.settimeout(0.05)
        try:
            if s.connect_ex(("127.0.0.1", p)) == 0:
                res.append(p)
        finally:
            s.close()
    return res


def puerto_ocupado(p):
    """¿Escucha alguien en 127.0.0.1:p?"""
    s = socket.socket()
    s.settimeout(0.2)
    try:
        return s.connect_ex(("127.0.0.1", p)) == 0
    finally:
        s.close()


def esperar_puerto(p, proc=None, seg=60.0):
    fin = time.time() + seg
    while time.time() < fin:
        s = socket.socket()
        s.settimeout(0.2)
        ok = s.connect_ex(("127.0.0.1", p)) == 0
        s.close()
        if ok:
            return True
        if proc is not None and proc.poll() is not None:
            return False
        time.sleep(0.2)
    return False


# ------------------------------------------------------------------------------------------------ cerrojo
class Cerrojo:
    """Una paridad a la vez en el PC: fichero con el pid, el común a todas las worktrees (ruta_cerrojo(): YO2_CERROJO, o
    COMUN/.lock, o _out/.lock de la worktree si no hay carpeta común). Un cerrojo de un proceso muerto se retira."""

    def __init__(self, ruta=None):
        self.ruta = ruta or ruta_cerrojo()

    @staticmethod
    def _vivo(pid):
        if pid <= 0:
            return False
        if os.name == "nt":
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
            return str(pid) in r.stdout
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def __enter__(self):
        os.makedirs(os.path.dirname(self.ruta), exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.ruta, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    pid = int(open(self.ruta).read().strip() or 0)
                except (OSError, ValueError):
                    pid = 0
                if self._vivo(pid):
                    sys.exit(f"otra paridad en marcha (pid {pid}, {self.ruta}): una a la vez")
                os.remove(self.ruta)                  # cerrojo huérfano
        sys.exit(f"no pude coger el cerrojo {self.ruta}")

    def __exit__(self, *a):
        try:
            os.remove(self.ruta)
        except OSError:
            pass


# ------------------------------------------------------------------------------------------------ instancia
def fake_client():
    if EMU not in sys.path:
        sys.path.insert(0, EMU)
    import fake_client as FC  # noqa: E402
    return FC


class Instancia:
    """run.py de PRUEBA: copia del estado congelado, YO2_NO_CLIENT=1, offset propio, log y TEMP en su carpeta.
    Igual que fake_client.run_pvp_prueba, pero con todo parametrizado (estado, entorno, carpeta)."""

    def __init__(self, offset, carpeta, estado, entorno=None, espera_duelcore=4.0, semilla=None):
        motivo = offset_valido(offset)
        if motivo:
            sys.exit(motivo)
        self.off, self.dir, self.estado_src = offset, os.path.abspath(carpeta), estado
        self.entorno = dict(entorno or {})
        self.espera = espera_duelcore
        self.semilla = semilla
        self.proc = None

    def __enter__(self):
        occ = ocupados(self.off)
        if occ:
            sys.exit(f"puertos ocupados para el offset {self.off}: {occ[:6]}")
        os.makedirs(self.dir, exist_ok=True)
        tmp = os.path.join(self.dir, "tmp")
        os.makedirs(tmp, exist_ok=True)
        self.st = os.path.join(self.dir, "state-prueba.json")
        shutil.copyfile(self.estado_src, self.st)
        for f in glob.glob(self.st + ".*"):
            os.remove(f)
        fake_client().prep_state(self.st)            # el login inventado de fake_client entra como el dueño
        env = dict(os.environ, YO2_LOGIN_ESTRICTO="0", YO2_NO_CLIENT="1", YO2_PORT_OFFSET=str(self.off),
                   YO2_STATE=self.st, YO2_LOGDIR=self.dir, YO2_BIND="127.0.0.1", YO2_STORAGE="json",
                   YO2_PERFIL=os.environ.get("YO2_PERFIL_PRUEBA") or "vanilla", TEMP=tmp, TMP=tmp, PYTHONIOENCODING="utf-8")
        if self.semilla is not None:
            # paridad: azar con semilla fija (semilla/sitecustomize.py) y hash de str fijo (orden de los set)
            env["PYTHONPATH"] = os.pathsep.join(x for x in (os.path.join(AQUI, "semilla"), env.get("PYTHONPATH")) if x)
            env["YO2_PARIDAD_SEMILLA"] = str(self.semilla)
            env["PYTHONHASHSEED"] = "0"
        env.update({k: str(v) for k, v in self.entorno.items()})
        self.salida = open(os.path.join(self.dir, "stdout.txt"), "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen([sys.executable, os.path.join(EMU, "run.py"), "0"], env=env, cwd=EMU,
                                     stdout=self.salida, stderr=subprocess.STDOUT)
        if not esperar_puerto(5730 + self.off, self.proc, 60):
            self.__exit__()
            sys.exit(f"run.py de prueba no arrancó (código {self.proc.poll()}); mira {self.dir}")
        time.sleep(self.espera)                      # duelcore arranca en ~3 s
        return self

    def __exit__(self, *a):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()                    # solo la instancia que lanzamos aquí
            try:
                self.proc.wait(8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        try:
            self.salida.close()
        except Exception:
            pass
        # que no quede nada escuchando antes de la siguiente
        fin = time.time() + 10
        while time.time() < fin and ocupados(self.off):
            time.sleep(0.3)


def leer_json(ruta, defecto=None):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return defecto


def escribir_json(ruta, datos):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(datos, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ruta)
