"""Bus de eventos (REESTRUCTURA.md §2.5). Paso S1: API y pruebas, SIN suscriptores ni emisores todavía.

    bus.emit("on_select", sess)        avisa a todos los suscriptores; nunca lanza, no devuelve nada
    bus.ask("login_refused", acct)     consulta: el primer valor que no sea None (vetos/sanciones del panel)
    bus.subscribe("on_select", fn, dueño="admin.eventos")

Sustituirá a `_admin_ev()` de run.py/pvp_hub/pvp_match/LP/TP y a las búsquedas de `sys.modules["yo2_eventos"]` (paso
R1). Hasta entonces el bus no tiene suscriptores y no pasa nada, igual que hoy sin panel.

Reglas (las mismas de `_admin_ev`): un suscriptor que falla NUNCA rompe al emisor; se anota en el log y se sigue con
el siguiente. Cada suscripción lleva un `dueño` (nombre del módulo): recargar ese módulo quita solo las suyas
(`quitar(dueño)`) y las vuelve a poner (`restaurar(instantánea)` si la recarga falla).
"""
import threading


class Bus:
    def __init__(self, log=None):
        self._log = log                       # callable(str) o None
        self._lock = threading.RLock()
        self._subs = {}                       # evento -> [(fn, dueño)]
        self.fallos = 0                       # cuántos suscriptores han lanzado (diagnóstico)

    # ------------------------------------------------------------------ registro
    def subscribe(self, evento, fn, dueño=None):
        """Apunta `fn` al evento. Devuelve `fn` (sirve de decorador). Apuntar dos veces lo mismo (misma función y
        mismo dueño) no la duplica: el registro es idempotente, como `setup(srv)` debe serlo."""
        if not callable(fn):
            raise TypeError(f"suscriptor no invocable para {evento!r}: {fn!r}")
        with self._lock:
            lista = self._subs.setdefault(str(evento), [])
            if not any(f is fn and d == dueño for f, d in lista):
                lista.append((fn, dueño))
        return fn

    def unsubscribe(self, evento, fn):
        """Quita `fn` de un evento. Devuelve cuántas entradas quitó."""
        with self._lock:
            lista = self._subs.get(str(evento), [])
            nuevas = [(f, d) for f, d in lista if f is not fn]
            quitadas = len(lista) - len(nuevas)
            self._subs[str(evento)] = nuevas
            return quitadas

    def quitar(self, dueño):
        """Quita TODAS las suscripciones de un dueño. Devuelve una instantánea para `restaurar()`."""
        snap = []
        with self._lock:
            for ev, lista in self._subs.items():
                snap += [(ev, i, f, d) for i, (f, d) in enumerate(lista) if d == dueño]
                self._subs[ev] = [(f, d) for f, d in lista if d != dueño]
        return snap

    def restaurar(self, snap):
        """Vuelve a poner lo que devolvió `quitar()`, cada una en su posición de antes (rollback de una recarga)."""
        with self._lock:
            for ev, i, f, d in sorted(snap, key=lambda x: x[1]):
                lista = self._subs.setdefault(ev, [])
                if not any(g is f and e == d for g, e in lista):
                    lista.insert(min(i, len(lista)), (f, d))

    def suscriptores(self, evento):
        with self._lock:
            return [f for f, _ in self._subs.get(str(evento), [])]

    def eventos(self):
        with self._lock:
            return sorted(e for e, lista in self._subs.items() if lista)

    def duenos(self):
        with self._lock:
            return sorted({d for lista in self._subs.values() for _, d in lista if d is not None})

    # ------------------------------------------------------------------ uso
    def _copia(self, evento):
        with self._lock:
            return list(self._subs.get(str(evento), ()))

    def _fallo(self, evento, fn, dueño, e):
        self.fallos += 1
        if self._log is not None:
            try:
                self._log(f"[bus] {evento}: {getattr(fn, '__name__', fn)!r} (dueño {dueño!r}) falló: {e!r}")
            except Exception:
                pass

    def emit(self, evento, *a, **k):
        """Avisa a todos, en el orden en que se apuntaron. Nunca lanza. No devuelve nada."""
        for fn, dueño in self._copia(evento):
            try:
                fn(*a, **k)
            except Exception as e:
                self._fallo(evento, fn, dueño, e)

    def ask(self, evento, *a, **k):
        """Pregunta: el primer valor que no sea None. Un suscriptor que falla se salta. Sin respuesta: None."""
        for fn, dueño in self._copia(evento):
            try:
                v = fn(*a, **k)
            except Exception as e:
                self._fallo(evento, fn, dueño, e)
                continue
            if v is not None:
                return v
        return None
