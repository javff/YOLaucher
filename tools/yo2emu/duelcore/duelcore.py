"""Cliente Python de duelcore (motor de reglas ocgcore en un proceso Node aparte).

Uso mínimo:

    from duelcore import DuelCore
    dc = DuelCore()                      # arranca node + tsx y espera "ready"
    dc.new(1, decks=[{"main": [...]}, {"main": [...]}], first=0, seed=42)
    evs = dc.wait(1)                     # mensajes hasta la siguiente decisión del jugador 0
    sel = evs[-1]                        # {"ev": "select", "type": "SELECT_IDLECMD", ...}
    dc.respond(1, {"type": "SELECT_IDLECMD", "action": "TO_EP", "index": None})
    ...
    dc.close()

Los eventos llegan por un hilo lector. Se pueden consumir de dos formas:
  - callback: DuelCore(on_event=f) llama a f(evento) desde el hilo lector;
  - colas: dc.wait(id) / dc.next_event(id) devuelven los eventos de ese duelo en orden.

Protocolo y formato de cada mensaje: README.md.
"""
from __future__ import annotations

import collections
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]                      # .../DuelCards
SERVER = REPO / "apps" / "server"
SCRIPT = HERE / "duelcore.mts"
try:                                        # node, motor y _vendor: yo2srv/rutas.py (P1, empaquetado). Sin YO2_NODE,
    from yo2srv import rutas as _rutas      # YO2_DUELCORE ni YO2_VENDOR, lo de siempre (node del PATH + tsx + SCRIPT)
except ImportError:
    import sys as _sys
    _sys.path.append(str(HERE.parent))      # tools/yo2emu
    from yo2srv import rutas as _rutas

# Eventos que cierran un "turno de conversación": tras ellos el core espera algo de nosotros
STOP_EVENTS = {"select", "end", "error"}


class DuelCoreError(RuntimeError):
    pass


def _find_launch() -> list[str]:
    """Comando para arrancar duelcore.mts.

    Preferido: `node --import <tsx loader> duelcore.mts` -> UN solo proceso, que muere al cerrar
    stdin. tsx.CMD (node_modules\\.bin) pasa por cmd.exe y lanza un node hijo: en Windows, matar
    el padre dejaría el hijo huérfano, así que solo se usa si no se encuentra el loader.
    """
    node = _rutas.node()
    script = Path(_rutas.duelcore())
    if script.suffix.lower() in (".mjs", ".js"):
        # paquete: duelcore.mjs de un solo fichero (esbuild) -> `node <mjs>`, sin tsx ni node_modules
        if not node:
            raise DuelCoreError("no encuentro node (YO2_NODE o el PATH)")
        return [node, "--disable-warning=ExperimentalWarning", str(script)]
    tsx_pkg = SERVER / "node_modules" / "tsx"
    loader = Path(os.path.realpath(tsx_pkg)) / "dist" / "loader.mjs"
    if node and loader.exists():
        return [node, "--disable-warning=ExperimentalWarning", "--import", loader.as_uri(), str(script)]
    bin_dir = SERVER / "node_modules" / ".bin"
    for name in ("tsx.CMD", "tsx.cmd", "tsx"):
        cand = bin_dir / name
        if cand.exists():
            return [str(cand), str(script)]
    raise DuelCoreError(f"no encuentro node+tsx (buscado {loader} y {bin_dir})")


class DuelCore:
    def __init__(self, on_event=None, start_timeout: float = 60.0, launch: list[str] | None = None):
        self.on_event = on_event
        self.cmd = launch or _find_launch()
        self._queues: dict[str, queue.Queue] = collections.defaultdict(queue.Queue)
        self._global: queue.Queue = queue.Queue()     # eventos sin id (ready, pong, cardinfo...)
        self._lock = threading.Lock()
        self.stderr_tail: collections.deque[str] = collections.deque(maxlen=200)
        self.ready: dict | None = None
        # cwd: apps/server (tsx resuelve ahí sus node_modules); un .mjs empaquetado, su carpeta. VENDOR_DIR (data.ts):
        # con YO2_VENDOR, esa; con un .mjs sin VENDOR_DIR puesta a mano, rutas.vendor() (el .mjs ya no está en
        # apps/server/src/duel y su cálculo relativo no valdría). Con tsx y sin YO2_VENDOR, nada: como siempre.
        mjs = next((Path(a) for a in self.cmd[1:] if a.lower().endswith((".mjs", ".js")) and not a.startswith("file:")), None)
        env = None
        if os.environ.get("YO2_VENDOR") or (mjs is not None and not os.environ.get("VENDOR_DIR")):
            env = dict(os.environ, VENDOR_DIR=_rutas.vendor())
        self.proc = subprocess.Popen(
            self.cmd,
            cwd=str(mjs.parent if mjs is not None else SERVER),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(target=self._read_stdout, name="duelcore-out", daemon=True).start()
        threading.Thread(target=self._read_stderr, name="duelcore-err", daemon=True).start()
        try:
            ev = self._global.get(timeout=start_timeout)
        except queue.Empty:
            self.close()
            raise DuelCoreError("duelcore no arrancó a tiempo:\n" + "\n".join(self.stderr_tail))
        if ev.get("ev") != "ready":
            raise DuelCoreError(f"primer evento inesperado: {ev}")
        self.ready = ev

    # -- hilos lectores ---------------------------------------------------
    @staticmethod
    def _key(duel_id) -> str:
        return json.dumps(duel_id)

    def _read_stdout(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                self.stderr_tail.append("[stdout no JSON] " + line)
                continue
            if self.on_event:
                try:
                    self.on_event(ev)
                except Exception as e:  # el callback no debe matar el lector
                    self.stderr_tail.append(f"[callback] {e!r}")
            if "id" in ev and ev["id"] is not None and ev.get("ev") not in ("ready", "pong", "cardinfo", "list"):
                self._queues[self._key(ev["id"])].put(ev)
            else:
                self._global.put(ev)
        self._global.put({"ev": "exit", "code": self.proc.poll()})

    def _read_stderr(self):
        for line in self.proc.stderr:
            self.stderr_tail.append(line.rstrip())

    # -- envío --------------------------------------------------------------
    def send(self, obj: dict):
        if self.proc.poll() is not None:
            raise DuelCoreError("duelcore terminó:\n" + "\n".join(self.stderr_tail))
        data = json.dumps(obj, separators=(",", ":")) + "\n"
        with self._lock:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()

    def new(self, duel_id, decks, first: int = 0, seed=None, lp: int = 8000, bot=(False, True),
            auto_chain: bool = True, shuffle: bool = True, rule: str | None = None, brain=None):
        """decks = [{"main": [passcodes], "extra": [...]}, {...}] (jugador 0 = humano).

        brain = "python" (jugador 1) o [j0, j1]: esos jugadores no los juega bot.ts; sus `select`
        llevan `view` (el tablero visto por ese jugador) para que decida un cerebro externo.
        rule = "MR5" (por defecto), "MR1".."MR4", "GOAT"...; sin rule manda la variable YO2_DUEL_RULE (si no, "MR5").
        Sirve para pasar las pruebas con otra regla sin tocarlas (YO2_DUEL_RULE=MR1 python test_translator.py 30).
        """
        rule = rule or os.environ.get("YO2_DUEL_RULE") or "MR5"
        cmd = {"cmd": "new", "id": duel_id, "decks": decks, "first": first, "lp": lp,
               "bot": list(bot), "auto_chain": auto_chain, "shuffle": shuffle, "rule": rule}
        if seed is not None:
            cmd["seed"] = seed
        if brain is not None:
            cmd["brain"] = brain if isinstance(brain, str) else list(brain)
        self.send(cmd)

    def respond(self, duel_id, response: dict, player: int | None = None):
        """player (opcional): duelcore comprueba que la decisión pendiente es de ese jugador."""
        cmd = {"cmd": "respond", "id": duel_id, "response": response}
        if player is not None:
            cmd["player"] = player
        self.send(cmd)

    def auto(self, duel_id):
        """Que bot.ts conteste por el jugador 0 la decisión pendiente."""
        self.send({"cmd": "auto", "id": duel_id})

    def surrender(self, duel_id, player: int = 0):
        self.send({"cmd": "surrender", "id": duel_id, "player": player})

    def end(self, duel_id):
        self.send({"cmd": "end", "id": duel_id})

    # -- recepción ----------------------------------------------------------
    def next_event(self, duel_id, timeout: float | None = 30.0) -> dict:
        try:
            return self._queues[self._key(duel_id)].get(timeout=timeout)
        except queue.Empty:
            raise DuelCoreError(f"sin eventos del duelo {duel_id} en {timeout}s") from None

    def wait(self, duel_id, timeout: float = 30.0) -> list[dict]:
        """Eventos del duelo hasta el siguiente select/end/error (incluido, el último de la lista)."""
        out = []
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise DuelCoreError(f"duelo {duel_id}: sin select/end en {timeout}s (¿cuelgue?)")
            ev = self.next_event(duel_id, timeout=left)
            out.append(ev)
            if ev.get("ev") in STOP_EVENTS:
                return out

    def _global_call(self, cmd: dict, want: str, timeout: float = 30.0) -> dict:
        self.send(cmd)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ev = self._global.get(timeout=max(0.01, deadline - time.monotonic()))
            if ev.get("ev") == want:
                return ev
            if ev.get("ev") == "error":
                raise DuelCoreError(ev.get("error"))
        raise DuelCoreError(f"sin respuesta a {cmd.get('cmd')}")

    def query(self, duel_id, timeout: float = 30.0) -> dict:
        """Estado del tablero (mano, zonas, cementerio...). Ojo: no mezclar con wait() en otro hilo."""
        self.send({"cmd": "query", "id": duel_id})
        while True:
            ev = self.next_event(duel_id, timeout=timeout)
            if ev.get("ev") in ("state", "error"):
                if ev["ev"] == "error":
                    raise DuelCoreError(ev.get("error"))
                return ev

    def cardinfo(self, codes) -> list[dict]:
        return self._global_call({"cmd": "cardinfo", "codes": list(codes)}, "cardinfo")["cards"]

    def ping(self) -> dict:
        return self._global_call({"cmd": "ping"}, "pong")

    def close(self, timeout: float = 5.0):
        """Cerrar stdin = el proceso destruye los duelos y sale solo."""
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
