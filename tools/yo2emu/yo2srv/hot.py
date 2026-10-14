"""Recarga en caliente genérica por módulo (REESTRUCTURA.md §2.7). Paso S1: API; paso R4: módulos registrados de verdad.

USO (desde fuera, contra el servidor en marcha; la consola 5799 ejecuta cada línea en el namespace de run.py):

    python admin.py "hot()"                                  # lista lo registrado y su sha8 (sale en el log, líneas «[hot]»)
    python admin.py "hot('lobby.lp')"                        # recarga un módulo
    python admin.py "hot('duelo.traductor', 'duelo.ocg')"    # varios, en ese orden
    python admin.py "SRV.hot.recargar('lobby.lp')"           # lo mismo sin el atajo; devuelve {nombre: resultado}
    # el resultado de cada recarga queda en el log («[recarga] lobby.lp ok (0.04 s) sha=1a2b3c4d antes=9f8e7d6c»; con el
    # fichero idéntico al cargado añade «sin cambios») y en NS["_HOT_LAST"]. Un fallo sale como «[recarga] X NO recargado: ...».

Qué hay registrado, cómo se vuelve a cablear cada módulo y qué conserva: yo2srv/recarga.py (el único sitio donde se
registran módulos; este fichero es el motor y no sabe nada de ninguno). Registrar uno nuevo:

    srv.hot.registrar("lobby.lp", fichero, ns_nombre="LP", sys_nombre="research_lobby", cargado=True,
                      keep=fn, setup=fn, post=fn, dependientes=("duelo.ocg",), dicts=("DEFAULT_CFG",))

Pasos de `recargar` (los del plan, ahora por GRUPO: lo pedido + sus dependientes, p. ej. recargar el traductor vuelve a
ejecutar ocg_duel, que lo importa por nombre):
  1. Lee y compila TODOS los ficheros del grupo; si uno no compila, no se toca nada.
  2. Ejecuta cada uno en un módulo NUEVO (no `importlib.reload`): lo que está en curso (un duelo, un PvpMatch…) conserva
     sus clases viejas. Mientras se ejecuta vive en sys.modules con un nombre privado («<nombre>__hot<n>»), para que lo
     que mire sys.modules[__name__] (dataclasses…) funcione. Los miembros ya ejecutados del grupo se ven bajo su nombre
     real mientras se ejecutan los siguientes (así el ocg_duel nuevo importa el traductor NUEVO), y se retiran al acabar.
  3. Lo que se cambió EN VIVO (banderas del registro de config.py cuya liga cae en el módulo, claves de los dicts
     declarados en `dicts=`) se vuelve a poner en el módulo nuevo: «vivo ≠ el que tenía el módulo al cargarse». Un
     módulo cuyo fichero cambió el valor por defecto de una bandera que NADIE tocó, toma el nuevo.
  4. Con el router EN PAUSA (cerrojo lector/escritor de router.py, tope 20 s; el despacho de hoy NO lo toma, ver abajo):
     `keep(viejo, nuevo)` pasa el estado que debe sobrevivir, quita las entradas del router/bus cuyo dueño es el módulo,
     `setup(nuevo)` cablea (SIEMPRE contra el módulo nuevo, sin hacerlo visible aún), se cambian sys.modules[...] y
     srv.ns[...] (una asignación por nombre) y `post` hace lo que necesita ver ya el módulo nuevo en el namespace.
  5. Si algo falla en 3 o 4: vuelve todo (router, bus, sys.modules, ns y lo que los ganchos apuntaron en `tx`) a como
     estaba y lo dice. Un fallo en 1 o 2 ni siquiera llega a tocar nada.
  6. Queda en el log «[recarga] <nombre> ok …» y en `srv.ns["_HOT_LAST"]`.

Ganchos (todos opcionales). Reciben `tx`, el deshacer del módulo: lo que cambien FUERA del módulo por estas vías se revierte
si el grupo se deshace:
    tx.set_item(d, k, v) / tx.set_attr(o, n, v)      cambia y apunta cómo estaba
    tx.proteger_item(d, k) / tx.proteger_attr(o, n)  apunta cómo está ahora, antes de que un `install` lo toque
    tx.pop_item(d, k) / tx.undo(fn)                  quita una clave / función de deshacer a medida
Los tres ganchos:
    keep(nuevo, viejo, srv, tx)    estado vivo que pasa del módulo viejo al nuevo (aún sin ser visible)
    setup(nuevo, viejo, srv, tx)   cablea el nuevo contra el resto (HANDLERS, envoltorios…); `viejo` puede ser None
    post(nuevo, viejo, srv, tx)    tras cambiar sys.modules/ns (reasignar la clase de una instancia viva, recargar NPCS…)
Si no hay gancho externo se usan `keep(viejo, srv)` y `setup(srv)` del propio módulo (la forma del plan §2.3).

DESPACHO Y ATOMICIDAD: hoy el despacho NO pasa por router.py (va por HANDLERS → lobby_reply → `srv.LP.handle`…), así que
la pausa del router no frena mensajes. Por eso el cableado se arma entero sobre el módulo nuevo ANTES de hacerlo visible
y lo visible cambia con UNA asignación (`srv.ns["LP"] = nuevo`): un mensaje ve el módulo viejo completo o el nuevo
completo. Cuando R3 meta el despacho en el router (cerrojo de lectura), la pausa pasa a ser real sin cambiar nada aquí.

NO se recarga lo registrado con `noreload=True` (duelcore, red/proto, red/conexion: llevan estado que no se puede
pasar; siguen pidiendo reiniciar, como hoy) ni lo que sus ganchos no sepan cablear sin riesgo (el motivo va en el mensaje).

El dueño de las entradas del router/bus es el `nombre` con el que se registró el módulo.
"""
import copy
import hashlib
import importlib.machinery
import importlib.util
import os
import sys
import threading
import time
import traceback
import types

from . import config as _config

SIN = object()


def sha_fichero(ruta):
    """sha1 (hex) del contenido del fichero, o None si no se puede leer."""
    try:
        with open(ruta, "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()
    except OSError:
        return None


class Tx:
    """Deshacer de lo que un gancho cambia FUERA de su módulo (se ejecuta al revés si el grupo se deshace)."""

    def __init__(self):
        self._deshacer = []

    def undo(self, fn):
        self._deshacer.append(fn)

    def set_item(self, d, k, v):
        ant = d.get(k, SIN)
        d[k] = v
        self._deshacer.append(lambda: d.pop(k, None) if ant is SIN else d.__setitem__(k, ant))

    def pop_item(self, d, k):
        ant = d.get(k, SIN)
        d.pop(k, None)
        if ant is not SIN:
            self._deshacer.append(lambda: d.__setitem__(k, ant))

    def set_attr(self, o, n, v):
        ant = vars(o).get(n, SIN) if hasattr(o, "__dict__") else getattr(o, n, SIN)
        setattr(o, n, v)
        self._deshacer.append(lambda: delattr(o, n) if ant is SIN else setattr(o, n, ant))

    def proteger_item(self, d, k):
        """Apunta cómo está d[k] ahora (sin cambiarlo): si el grupo se deshace, vuelve a estar así (o sin la clave)."""
        ant = d.get(k, SIN)
        self._deshacer.append(lambda: d.pop(k, None) if ant is SIN else d.__setitem__(k, ant))

    def proteger_attr(self, o, n):
        """Lo mismo para un atributo (de un módulo o una instancia)."""
        ant = vars(o).get(n, SIN) if hasattr(o, "__dict__") else getattr(o, n, SIN)
        self._deshacer.append(lambda: delattr(o, n) if ant is SIN else setattr(o, n, ant))

    def deshacer(self, anota=None):
        for fn in reversed(self._deshacer):
            try:
                fn()
            except Exception as e:                      # noqa: BLE001  deshacer lo máximo posible
                if anota is not None:
                    anota(f"[recarga] al deshacer: {e!r}")
        self._deshacer.clear()


class Modulo:
    def __init__(self, nombre, fichero, ns_nombre=None, sys_nombre=None, noreload=False, *, motivo="",
                 dependientes=(), keep=None, setup=None, post=None, dicts=(), liga_nombres=None, trcfg=False, parches=()):
        self.nombre = nombre
        self.fichero = os.path.abspath(fichero)
        self.ns_nombre = ns_nombre
        self.sys_nombre = sys_nombre or nombre
        self.noreload = noreload
        self.motivo = motivo
        self.dependientes = tuple(dependientes)
        self.keep, self.setup, self.post = keep, setup, post
        self.dicts = tuple(dicts)                   # dicts del módulo cuyas claves cambiadas en vivo se conservan
        self.trcfg = trcfg                          # sus banderas con liga ("trcfg", clave) viven en DEFAULT_CFG
        self.parches = frozenset(parches)           # funciones del módulo que OTROS envuelven y cuyo gancho ya las vuelve a envolver
        self.liga_nombres = frozenset(liga_nombres if liga_nombres is not None
                                      else (x for x in (ns_nombre, sys_nombre) if x))
        self.generacion = 0
        self.sha = None                             # sha1 del fichero tal como está cargado (None = desconocido)
        self.base = None                            # {"flags": {id: valor}, "dicts": {nombre: dict}} del módulo al cargarse

    def __repr__(self):
        return f"<Modulo {self.nombre} {self.fichero}>"


class Hot:
    def __init__(self, srv):
        self.srv = srv
        self.registro = {}                    # nombre -> Modulo (en orden de registro = orden de dependencia)
        self._lock = threading.RLock()        # una recarga a la vez

    def registrar(self, nombre, fichero, *, ns_nombre=None, sys_nombre=None, noreload=False, motivo="", dependientes=(),
                  keep=None, setup=None, post=None, dicts=(), liga_nombres=None, trcfg=False, parches=(), cargado=False):
        """Registra un módulo. `cargado=True`: ya está cargado tal como está en disco (arranque); se anotan su sha y sus
        valores «de fábrica» para saber después qué se cambió en vivo."""
        m = Modulo(nombre, fichero, ns_nombre, sys_nombre, noreload, motivo=motivo, dependientes=dependientes, keep=keep,
                   setup=setup, post=post, dicts=dicts, liga_nombres=liga_nombres, trcfg=trcfg, parches=parches)
        self.registro[nombre] = m
        if cargado:
            self.fijar_base(nombre)
        return m

    def fijar_base(self, nombre):
        """Toma como «de fábrica» lo que el módulo cargado tiene AHORA (llamar antes de aplicar perfil/cambios en vivo)."""
        m = self.registro[nombre]
        mod = self._vivo(m)
        m.sha = sha_fichero(m.fichero)
        m.base = self._leer_bases(m, mod) if mod is not None else None
        return m

    # ------------------------------------------------------------------ consulta
    def listar(self):
        """{nombre: {sha, disco, cambiado, noreload, motivo, dependientes, fichero}}: lo registrado y si el disco ya no
        es lo cargado (lo que una recarga recogería)."""
        out = {}
        for n, m in self.registro.items():
            actual = sha_fichero(m.fichero)
            out[n] = {"sha": (m.sha or "")[:8] or None, "disco": (actual or "")[:8] or None,
                      "cambiado": bool(m.sha and actual and m.sha != actual), "noreload": m.noreload, "motivo": m.motivo,
                      "dependientes": list(m.dependientes), "fichero": m.fichero}
        return out

    def _vivo(self, m):
        mod = sys.modules.get(m.sys_nombre)
        if mod is None and m.ns_nombre:
            c = self.srv.ns.get(m.ns_nombre)
            mod = c if isinstance(c, types.ModuleType) else None
        return mod

    # ------------------------------------------------------------------ estado vivo (banderas y dicts)
    @staticmethod
    def _puntos_flag(m):
        out = []
        for f in _config.REGISTRO:
            lg = f.liga
            if lg[0] == "mod" and lg[1] in m.liga_nombres:
                out.append((f.id, ("a", lg[2])))
            elif lg[0] == "moddict" and lg[1] in m.liga_nombres:
                out.append((f.id, ("d", lg[2], lg[3])))
            elif lg[0] == "trcfg" and m.trcfg:
                out.append((f.id, ("d", "DEFAULT_CFG", lg[1])))
        return out

    @staticmethod
    def _leer_punto(mod, spec):
        if spec[0] == "a":
            return getattr(mod, spec[1], SIN)
        d = getattr(mod, spec[1], None)
        return d.get(spec[2], SIN) if isinstance(d, dict) else SIN

    @staticmethod
    def _escribir_punto(mod, spec, v):
        if spec[0] == "a":
            setattr(mod, spec[1], v)
        else:
            d = getattr(mod, spec[1], None)
            if isinstance(d, dict):
                d[spec[2]] = v

    def _leer_bases(self, m, mod):
        def cp(v):
            try:
                return copy.deepcopy(v)
            except Exception:                           # noqa: BLE001
                return v
        flags = {}
        for fid, spec in self._puntos_flag(m):
            v = self._leer_punto(mod, spec)
            if v is not SIN:
                flags[fid] = cp(v)
        dicts = {}
        for dn in m.dicts:
            d = getattr(mod, dn, None)
            if isinstance(d, dict):
                dicts[dn] = {k: cp(v) for k, v in d.items()}
        return {"flags": flags, "dicts": dicts}

    def _cambios_vivos(self, m, viejo):
        """Lo que en el módulo cargado vale distinto de lo que tenía al cargarse: [(id, spec, valor)], [(dict, clave, v)]."""
        fl, dc = [], []
        if viejo is None or m.base is None:
            return fl, dc
        for fid, spec in self._puntos_flag(m):
            vivo, base = self._leer_punto(viejo, spec), m.base["flags"].get(fid, SIN)
            if vivo is SIN or base is SIN:
                continue
            if vivo != base or type(vivo) is not type(base):
                fl.append((fid, spec, vivo))
        for dn in m.dicts:
            d, bd = getattr(viejo, dn, None), m.base["dicts"].get(dn)
            if isinstance(d, dict) and bd is not None:
                for k, v in list(d.items()):
                    if k not in bd or bd[k] != v:
                        dc.append((dn, k, v))
        return fl, dc

    def _aplicar_vivos(self, nuevo, cambios):
        """Pone en el módulo nuevo lo cambiado en vivo. Devuelve los nombres aplicados (para el log)."""
        fl, dc = cambios
        hechos = []
        for fid, spec, v in fl:
            if self._leer_punto(nuevo, spec) is not SIN:
                self._escribir_punto(nuevo, spec, copy.copy(v) if isinstance(v, (dict, list, set)) else v)
                hechos.append(fid)
        for dn, k, v in dc:
            d = getattr(nuevo, dn, None)
            if isinstance(d, dict):
                d[k] = v
                if f"{dn}[{k!r}]" not in hechos:
                    hechos.append(f"{dn}[{k!r}]")
        return hechos

    # ------------------------------------------------------------------ recarga
    def _cierre(self, nombre):
        """El módulo y sus dependientes (transitivo) recargables, sin repetir."""
        vistos, pend = [], [nombre]
        while pend:
            n = pend.pop(0)
            m = self.registro.get(n)
            if m is None or m.noreload or n in vistos:
                continue
            vistos.append(n)
            pend.extend(m.dependientes)
        return vistos

    def recargar(self, *nombres, pausa_s=20.0):
        """Recarga uno o varios módulos registrados (y los que dependen de ellos). Devuelve {nombre: resultado}, con
        «ok» o «error: ...». Los pedidos van primero y en el orden dado; los arrastrados por
        dependencia, detrás. Módulos sin relación entre sí se recargan por separado (el fallo de uno no frena a otro)."""
        res = {}
        pedidos = []
        for n in nombres:
            m = self.registro.get(n)
            if m is None:
                res[n] = self._falla(n, f"módulo no registrado ({sorted(self.registro)})")
            elif m.noreload:
                res[n] = self._falla(n, "no se recarga en caliente: hay que reiniciar" + (f" ({m.motivo})" if m.motivo else ""))
            elif n not in pedidos:
                pedidos.append(n)
        miembros = set()
        for n in pedidos:
            miembros.update(self._cierre(n))
        orden = [n for n in self.registro if n in miembros]          # orden de registro = de dependencia
        # componentes conexos (por las aristas «dependiente»)
        padre = {n: n for n in orden}

        def raiz(x):
            while padre[x] != x:
                padre[x] = padre[padre[x]]
                x = padre[x]
            return x
        for n in orden:
            for d in self.registro[n].dependientes:
                if d in padre:
                    padre[raiz(d)] = raiz(n)
        grupos = {}
        for n in orden:
            grupos.setdefault(raiz(n), []).append(n)
        por_nombre = {}
        for g in grupos.values():
            r = self._grupo([self.registro[n] for n in g], set(pedidos), pausa_s)
            por_nombre.update(r)
        for n in pedidos:
            res[n] = por_nombre.get(n, res.get(n))
        for n, v in por_nombre.items():
            res.setdefault(n, v)
        return res

    def _anota(self, texto):
        try:
            self.srv.log(texto)
        except Exception:
            pass

    def _grupo(self, G, pedidos, pausa_s):
        """Una transacción para el grupo G (módulos en orden de dependencia). Devuelve {nombre: resultado}."""
        t0 = time.time()
        srv, ns = self.srv, self.srv.ns
        nombres = [m.nombre for m in G]

        def fallo(culpable, motivo, traza=None):
            out = {}
            for m in G:
                if m is culpable or len(G) == 1:
                    out[m.nombre] = self._falla(m.nombre, motivo, traza)
                else:
                    out[m.nombre] = self._falla(m.nombre, f"no recargado: se deshace el grupo {nombres} por "
                                                          f"{culpable.nombre} ({motivo})")
            return out

        with self._lock:
            # 1. leer y compilar todo: un error de sintaxis o un fichero ausente no toca nada
            codigos, shas = {}, {}
            for m in G:
                try:
                    with open(m.fichero, "rb") as f:
                        datos = f.read()
                    codigos[m.nombre] = compile(datos, m.fichero, "exec", dont_inherit=True)
                    shas[m.nombre] = hashlib.sha1(datos).hexdigest()
                except (OSError, SyntaxError, ValueError, OverflowError, RecursionError) as e:
                    return fallo(m, f"no compila: {e!r}")
            # 2. los módulos que hay ahora y lo que valen en vivo
            previos = {}
            for m in G:
                viejo = sys.modules.get(m.sys_nombre)
                vns = ns.get(m.ns_nombre, SIN) if m.ns_nombre else SIN
                if viejo is None and isinstance(vns, types.ModuleType):
                    viejo = vns
                previos[m.nombre] = (viejo, vns, sys.modules.get(m.sys_nombre, SIN))
            vivos = {m.nombre: self._cambios_vivos(m, previos[m.nombre][0]) for m in G}
            # 3. ejecutar cada uno en un módulo NUEVO, con nombre privado mientras corre
            nuevos, visibles = {}, []
            try:
                for m in G:
                    m.generacion += 1
                    privado = f"{m.sys_nombre}__hot{m.generacion}"
                    try:
                        # con loader explícito vale cualquier extensión (si no, spec_from_file_location devuelve None)
                        spec = importlib.util.spec_from_file_location(
                            privado, m.fichero, loader=importlib.machinery.SourceFileLoader(privado, m.fichero))
                        nuevo = importlib.util.module_from_spec(spec)
                        sys.modules[privado] = nuevo
                        exec(codigos[m.nombre], nuevo.__dict__)
                    except BaseException as e:           # SystemExit incluido: un módulo no puede parar el servidor
                        if isinstance(e, KeyboardInterrupt):
                            raise
                        return fallo(m, f"falló al ejecutarse: {e!r}")
                    finally:
                        sys.modules.pop(privado, None)
                    nuevo.__name__ = m.sys_nombre         # el nombre real; __module__ de sus clases conserva el privado
                    nuevos[m.nombre] = nuevo
                    visibles.append((m.sys_nombre, sys.modules.get(m.sys_nombre, SIN)))
                    sys.modules[m.sys_nombre] = nuevo     # lo ven los del grupo que se ejecutan después
            finally:
                for nom, ant in reversed(visibles):       # y se retira: el cambio de verdad va tras `setup`
                    if ant is SIN:
                        sys.modules.pop(nom, None)
                    else:
                        sys.modules[nom] = ant
            # 4. lo cambiado en vivo (banderas, claves de DEFAULT_CFG…) al módulo nuevo, y sus valores «de fábrica»
            bases, aplicados = {}, {}
            for m in G:
                nuevo = nuevos[m.nombre]
                bases[m.nombre] = self._leer_bases(m, nuevo)
                try:
                    aplicados[m.nombre] = self._aplicar_vivos(nuevo, vivos[m.nombre])
                except Exception as e:                      # noqa: BLE001
                    aplicados[m.nombre] = []
                    self._anota(f"[recarga] {m.nombre}: no se pudieron conservar las banderas vivas: {e!r}")
            # 4b. avisa de lo que otros módulos habían envuelto en el viejo y el nuevo no lleva (y ningún gancho recablea)
            for m in G:
                for k, de in self._parches_externos(previos[m.nombre][0], nuevos[m.nombre]):
                    if k not in m.parches:
                        self._anota(f"[recarga] AVISO {m.nombre}.{k} llevaba un envoltorio de {de} que el módulo nuevo NO lleva "
                                    f"(hay que volver a instalarlo desde allí)")
            # 5. keep + setup + cambio + post, con el router en pausa
            txs = {m.nombre: Tx() for m in G}
            snaps = {}
            try:
                with srv.router.pausa(pausa_s):
                    for m in G:
                        viejo = previos[m.nombre][0]
                        self._gancho(m, "keep", nuevos[m.nombre], viejo, txs[m.nombre])
                    for m in G:
                        snaps[m.nombre] = (srv.router.quitar(m.nombre), srv.bus.quitar(m.nombre))
                    for m in G:
                        self._gancho(m, "setup", nuevos[m.nombre], previos[m.nombre][0], txs[m.nombre])
                    for m in G:
                        sys.modules[m.sys_nombre] = nuevos[m.nombre]
                        if m.ns_nombre:
                            ns[m.ns_nombre] = nuevos[m.nombre]
                    for m in G:
                        self._gancho(m, "post", nuevos[m.nombre], previos[m.nombre][0], txs[m.nombre])
            except BaseException as e:
                if isinstance(e, KeyboardInterrupt):
                    raise
                traza = traceback.format_exc()
                # vuelta atrás: lo que registró el nuevo se va; lo del viejo vuelve donde estaba
                for m in reversed(G):
                    txs[m.nombre].deshacer(self._anota)
                for m in G:
                    if m.nombre in snaps:             # solo si ya se habían quitado (un fallo en keep no toca el router)
                        srv.router.quitar(m.nombre)
                        srv.bus.quitar(m.nombre)
                        srv.router.restaurar(snaps[m.nombre][0])
                        srv.bus.restaurar(snaps[m.nombre][1])
                    viejo, vns, vsys = previos[m.nombre]
                    if vsys is SIN:
                        sys.modules.pop(m.sys_nombre, None)
                    else:
                        sys.modules[m.sys_nombre] = vsys
                    if m.ns_nombre:
                        if vns is SIN:
                            ns.pop(m.ns_nombre, None)
                        else:
                            ns[m.ns_nombre] = vns
                culpable = next((m for m in G if m.nombre in getattr(e, "_hot_modulo", ())), G[0])
                return fallo(culpable, f"se deshace ({e!r})", traza)
        # 6. cuentas: huella, bases nuevas y log
        seg = round(time.time() - t0, 3)
        out = {}
        hora = time.strftime("%H:%M:%S")
        for m in G:
            antes, sha = m.sha, shas[m.nombre]
            sin_cambios = antes is not None and antes == sha
            m.sha, m.base = sha, bases[m.nombre]
            out[m.nombre] = "ok"                      # «sin cambios» solo en el log y en _HOT_LAST
            extra = (f" | banderas vivas conservadas: {', '.join(aplicados[m.nombre])}" if aplicados[m.nombre] else "")
            por = "" if m.nombre in pedidos else " (por dependencia)"
            self._anota(f"[recarga] {m.nombre} ok ({seg} s) sha={sha[:8]} antes={(antes or '?')[:8]}"
                        + (" sin cambios" if sin_cambios else "") + por + extra)
            ns["_HOT_LAST"] = {"modulo": m.nombre, "resultado": "ok", "segundos": seg, "hora": hora, "sha": sha[:8],
                               "antes": (antes or "")[:8] or None, "sin_cambios": sin_cambios}
        return out

    @staticmethod
    def _parches_externos(viejo, nuevo):
        """[(nombre, módulo que lo puso)]: funciones que en el módulo viejo ya no son suyas (otro las sustituyó por un
        envoltorio) y que el nuevo define por su cuenta."""
        if viejo is None:
            return []
        out = []
        for k, v in list(vars(viejo).items()):
            g = getattr(v, "__globals__", None)
            if g is None or g is viejo.__dict__:
                continue
            if getattr(vars(nuevo).get(k), "__globals__", None) is nuevo.__dict__:
                out.append((k, g.get("__name__", "?")))
        return out

    def _gancho(self, m, que, nuevo, viejo, tx):
        """Llama al gancho externo del módulo (o al del propio módulo para keep/setup, forma del plan §2.3)."""
        try:
            if que == "keep" and viejo is None:
                return                                    # primera carga: no hay estado que pasar
            fn = getattr(m, que)
            if fn is not None:
                fn(nuevo, viejo, self.srv, tx)
            elif que == "keep" and hasattr(nuevo, "keep"):
                nuevo.keep(viejo, self.srv)
            elif que == "setup" and hasattr(nuevo, "setup"):
                nuevo.setup(self.srv)
        except BaseException as e:
            if not isinstance(e, KeyboardInterrupt):
                try:
                    e._hot_modulo = (m.nombre,)         # quién falló, para el mensaje
                except Exception:                       # noqa: BLE001
                    pass
            raise

    def _falla(self, nombre, motivo, traza=None):
        texto = f"error: {motivo}"
        self.srv.ns["_HOT_LAST"] = {"modulo": nombre, "resultado": texto, "hora": time.strftime("%H:%M:%S")}
        self._anota(f"[recarga] {nombre} NO recargado: {motivo}"
                    + (f" | {traza.replace(chr(10), ' | ')}" if traza else ""))
        return texto
