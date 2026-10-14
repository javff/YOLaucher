"""Registro de manejadores por (tipo de puerto, id de mensaje) (REESTRUCTURA.md §2.4). Paso S1: API y pruebas, SIN
usar todavía: `HANDLERS[puerto]` sigue apuntando a gate_reply/game_reply/lobby_reply/duel_reply de run.py.

    router.on("lobby", 0x4400, h_chat, dueño="lobby.lp")        manejador principal
    router.wrap("lobby", 0x4400, gm_chat, dueño="mundo.mapas")  envoltorio: gm_chat(siguiente, c, id, param, data)
    router.pre("lobby", hub_observa, dueño="lobby.hub")         observador ANTES de contestar (HUB.on_lobby_msg…)
    router.fallback("lobby", auto_reply, dueño="red.auto")      respuesta por defecto si nadie contestó
    router.dispatch("lobby", c, msg_id, param, data) -> bool    True = contestado

Convención de los manejadores: `h(c, msg_id, param, data)`. Devolver **False** = «no es mío, sigue» (cualquier otra
cosa, incluido None, = contestado), igual que `LP.handle` de hoy (`h(...) is not False`).

Regla B60 (de `LP.handle`): si un manejador lanza DESPUÉS de haber mandado algo por `c.send`, cuenta como contestado
(y se anota) para que no se conteste dos veces; si lanza sin haber mandado nada, se anota y cuenta como no contestado
(sigue la cadena: el fallback).

Dueños: cada registro lleva el dueño (nombre del módulo). `quitar(dueño)` retira SOLO lo suyo (devuelve una
instantánea para `restaurar()` si la recarga falla). Si dos dueños registran el mismo (tipo, id), manda el último;
al quitar el último, vuelve el anterior (hoy: `LP.HANDLERS.update(TP.HANDLERS)` pisa el 0x7000 de LP).

Pausa: `dispatch` toma un cerrojo de LECTURA; `pausa()` toma el de ESCRITURA (tope 20 s, como los «puertos en espera»
de reload_duel hoy). Con la pausa tomada no se despacha nada nuevo y se espera a que acaben los despachos en curso;
es lo que usará hot.py para cambiar entradas sin que un mensaje vea la tabla a medias.
"""
import contextlib
import threading
import time

TIPOS = ("puerta", "cuentas", "lobby", "duelo")

# Puertos -> tipo (offset aparte): 5730 puerta, 5731 cuentas, 5732 y salas 5740-5750 = lobby, 5760 duelo.
TIPO_POR_PUERTO = {5730: "puerta", 5731: "cuentas", 5732: "lobby", 5760: "duelo", **{p: "lobby" for p in range(5740, 5751)}}

# Orden de hoy, escrito como tabla (la R3 lo convierte en registros y en la prueba de equivalencia de despacho).
# Fuente: §2.4 del plan y run.py (lobby_reply, línea ~1239).
ORDEN = {
    "puerta": ("gate_reply",),
    "cuentas": ("game_reply",),
    "lobby": (
        "login 0x3001/0x3003 -> cuentas",
        "dp_npc_gate (0x4288)",
        "observadores: HUB.select + _admin_ev('on_select') con 0x4100, HUB.on_lobby_msg",
        "pasos previos de LP.handle: LIMIT_LIST con 0x4100, ROOM_REOPEN con 0x4210",
        "manejador registrado (LP.HANDLERS + EQ/TP/TO + envoltorios NR 0x4802, MW 0x4400)",
        "efectos posteriores: AV_STATE con 0x4170, sync_player_id con 0x4100, myroom_self_avatar",
        "PH_LOBBY (PSELECTRESULT, DUELPASS, decks, objetos, título, avatar)",
        "ramas propias de lobby_reply",
        "auto_reply",
        "sin respuesta",
    ),
    "duelo": ("PVP.route", "rendición DF", "ocg", "duel_reply base"),
}


class PausaVencida(TimeoutError):
    """No se pudo tomar la pausa en el tiempo dado (hay despachos que no terminan)."""


class _RW:
    """Cerrojo lector/escritor mínimo. Con un escritor esperando no entran lectores nuevos (no hay inanición)."""

    def __init__(self):
        self._c = threading.Condition()
        self._lectores = 0
        self._escritor = False
        self._esperando = 0

    @contextlib.contextmanager
    def leer(self):
        with self._c:
            while self._escritor or self._esperando:
                self._c.wait()
            self._lectores += 1
        try:
            yield
        finally:
            with self._c:
                self._lectores -= 1
                if not self._lectores:
                    self._c.notify_all()

    @contextlib.contextmanager
    def escribir(self, timeout):
        fin = time.monotonic() + timeout
        with self._c:
            self._esperando += 1
            try:
                while self._escritor or self._lectores:
                    resto = fin - time.monotonic()
                    if resto <= 0:
                        raise PausaVencida(f"el router no se pudo pausar en {timeout} s")
                    self._c.wait(resto)
            finally:
                self._esperando -= 1
            self._escritor = True
        try:
            yield
        finally:
            with self._c:
                self._escritor = False
                self._c.notify_all()


class Router:
    def __init__(self, log=None):
        self._log = log
        self._rw = _RW()
        self._tabla = threading.RLock()       # protege las estructuras (no el despacho)
        self._on = {}                         # (tipo, id) -> [(h, dueño)]
        self._wrap = {}                       # (tipo, id) -> [(f, dueño)]  (el último registrado es el más externo)
        self._pre = {}                        # tipo -> [(f, dueño, ids|None)]
        self._fallback = {}                   # tipo -> [(f, dueño)]
        self.errores = 0

    # ------------------------------------------------------------------ registro
    @staticmethod
    def _tipo(tipo):
        if tipo not in TIPOS:
            raise ValueError(f"tipo de puerto desconocido: {tipo!r} (uno de {TIPOS})")
        return tipo

    @staticmethod
    def _sustituir(lista, entrada, dueño, por_dueño):
        """Registro idempotente: si ese dueño ya tenía una entrada (por_dueño=True), la sustituye en su sitio."""
        if por_dueño:
            for i, e in enumerate(lista):
                if e[1] == dueño and dueño is not None:
                    lista[i] = entrada
                    return
        lista.append(entrada)

    def on(self, tipo, msg_id, h, dueño=None):
        if not callable(h):
            raise TypeError(f"manejador no invocable: {h!r}")
        with self._tabla:
            self._sustituir(self._on.setdefault((self._tipo(tipo), int(msg_id)), []), (h, dueño), dueño, True)
        return h

    def wrap(self, tipo, msg_id, f, dueño=None):
        """f(siguiente, c, msg_id, param, data): llama a `siguiente(c, msg_id, param, data)` si quiere seguir."""
        if not callable(f):
            raise TypeError(f"envoltorio no invocable: {f!r}")
        with self._tabla:
            self._sustituir(self._wrap.setdefault((self._tipo(tipo), int(msg_id)), []), (f, dueño), dueño, True)
        return f

    def pre(self, tipo, f, dueño=None, ids=None):
        """Observador antes de contestar: f(c, msg_id, param, data). `ids` limita a esos mensajes (None = todos).
        Nunca contesta por el router y sus fallos no paran nada."""
        if not callable(f):
            raise TypeError(f"observador no invocable: {f!r}")
        ids = None if ids is None else frozenset(int(i) for i in ids)
        with self._tabla:
            self._sustituir(self._pre.setdefault(self._tipo(tipo), []), (f, dueño, ids), dueño, False)
        return f

    def fallback(self, tipo, f, dueño=None):
        if not callable(f):
            raise TypeError(f"fallback no invocable: {f!r}")
        with self._tabla:
            self._sustituir(self._fallback.setdefault(self._tipo(tipo), []), (f, dueño), dueño, True)
        return f

    def quitar(self, dueño):
        """Retira TODO lo de un dueño. Devuelve la instantánea (con la posición de cada entrada) para `restaurar()`."""
        snap = {"on": [], "wrap": [], "pre": [], "fallback": []}
        with self._tabla:
            for nombre, tabla in (("on", self._on), ("wrap", self._wrap), ("pre", self._pre),
                                  ("fallback", self._fallback)):
                for k, lista in tabla.items():
                    snap[nombre] += [(k, i, e) for i, e in enumerate(lista) if e[1] == dueño]
                    tabla[k] = [e for e in lista if e[1] != dueño]
        return snap

    def restaurar(self, snap):
        """Vuelve a poner lo que quitó `quitar()`, cada entrada en su posición de antes (rollback de una recarga)."""
        with self._tabla:
            for nombre, tabla in (("on", self._on), ("wrap", self._wrap), ("pre", self._pre),
                                  ("fallback", self._fallback)):
                for k, i, e in sorted(snap[nombre], key=lambda x: x[1]):
                    lista = tabla.setdefault(k, [])
                    lista.insert(min(i, len(lista)), e)

    # ------------------------------------------------------------------ consulta
    def manejador(self, tipo, msg_id):
        """El manejador que contestaría (el último registrado) o None."""
        with self._tabla:
            lista = self._on.get((tipo, int(msg_id)))
            return lista[-1][0] if lista else None

    def ids(self, tipo):
        with self._tabla:
            return sorted(i for (t, i), lista in self._on.items() if t == tipo and lista)

    def duenos(self):
        with self._tabla:
            out = set()
            for d in (self._on, self._wrap):
                out |= {e[1] for lista in d.values() for e in lista}
            out |= {e[1] for lista in self._pre.values() for e in lista}
            out |= {e[1] for lista in self._fallback.values() for e in lista}
            out.discard(None)
            return sorted(out)

    def tabla(self):
        """{(tipo, id): [dueño, ...]} de los manejadores principales (para depurar y para la prueba de equivalencia)."""
        with self._tabla:
            return {k: [d for _, d in lista] for k, lista in self._on.items() if lista}

    # ------------------------------------------------------------------ despacho
    def pausa(self, timeout=20.0):
        """Con la pausa tomada no se despacha nada. `with router.pausa(): ...`"""
        return self._rw.escribir(timeout)

    def _anota(self, texto):
        if self._log is not None:
            try:
                self._log(texto)
            except Exception:
                pass

    def _cadena(self, tipo, msg_id):
        with self._tabla:
            lista = self._on.get((tipo, msg_id))
            if not lista:
                return None
            h = lista[-1][0]
            envs = [f for f, _ in self._wrap.get((tipo, msg_id), ())]
        def montar(f, siguiente):
            return lambda c, i, p, d: f(siguiente, c, i, p, d)
        fn = h
        for f in envs:                        # el último registrado queda más externo
            fn = montar(f, fn)
        return fn

    def dispatch(self, tipo, c, msg_id, param, data):
        """True si algo contestó. Ver el docstring del módulo (convención, B60, orden)."""
        msg_id = int(msg_id)
        with self._rw.leer():
            with self._tabla:
                pres = list(self._pre.get(tipo, ()))
                fbs = [f for f, _ in self._fallback.get(tipo, ())]
            for f, dueño, ids in pres:
                if ids is not None and msg_id not in ids:
                    continue
                try:
                    f(c, msg_id, param, data)
                except Exception as e:
                    self.errores += 1
                    self._anota(f"[router] {tipo} 0x{msg_id:04x}: observador de {dueño!r} falló: {e!r}")
            fn = self._cadena(tipo, msg_id)
            if fn is not None:
                if self._llamar(tipo, fn, c, msg_id, param, data):
                    return True
            if fbs:
                return self._llamar(tipo, fbs[-1], c, msg_id, param, data)
            return False

    def _llamar(self, tipo, fn, c, msg_id, param, data):
        """Llama a fn contando los c.send (regla B60). True = contestado."""
        enviados = [0]
        propio = "send" in getattr(c, "__dict__", {})
        orig = getattr(c, "send", None)

        def contando(*a, **k):
            enviados[0] += 1
            return orig(*a, **k)

        envuelto = False
        if orig is not None:
            try:
                c.send = contando
                envuelto = True
            except Exception:                 # objeto sin __dict__: sin cuenta (como LP.handle)
                envuelto = False
        try:
            return fn(c, msg_id, param, data) is not False
        except Exception as e:
            self.errores += 1
            if enviados[0]:
                self._anota(f"[router] {tipo} 0x{msg_id:04x}: error tras mandar {enviados[0]} mensaje(s): {e!r} "
                            f"(no se repite respuesta)")
                return True
            self._anota(f"[router] {tipo} 0x{msg_id:04x}: error: {e!r}")
            return False
        finally:
            if envuelto:
                try:
                    if propio:
                        c.send = orig
                    else:
                        del c.send
                except Exception:
                    pass
