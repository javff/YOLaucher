"""Sesión y vista por cuenta (paso E3 de la reestructura, REESTRUCTURA.md §3.4): `CTX`/`cur_sess`, `load_state`/`save_state`
(la vista de la cuenta del hilo sobre state.json) y `register_account`.

Es el bloque de run.py «PvP fase 1 (V02, P0-2/P1-15)» MOVIDO sin cambiar la lógica. run.py conserva los MISMOS nombres con

    from yo2srv.cuentas import sesion as _sesion
    _sesion.setup(SRV)
    from yo2srv.cuentas.sesion import (ACCT_KEYS, HUB_KEYS, OWNER_ACCT, MAX_SAVED_ACCOUNTS, CTX, SIN_SESION, ...)

y por eso la consola 5799, el panel, el hub (`env["register_account"]`) y los hot_* los siguen encontrando en el namespace.

Reglas (REESTRUCTURA.md §2.3):
- `CTX` (threading.local), `SIN_SESION` y la excepción `SinSesion` son UN objeto cada uno, el mismo aquí y en el namespace
  (identidad: «CTX.sess is SIN_SESION» se compara por todo el servidor). Aquí dentro se usan directamente.
- Todo lo demás que antes era global de run.py se lee como `srv.X` en el momento de usarlo, porque alguien lo puede
  reasignar o parchear en el namespace: las funciones (`srv.load_state_raw`/`srv.save_state_raw`, que el panel envuelve;
  `srv.log`, que sustituye hot_logseguro; `srv.cur_sess`…) y también las constantes (`srv.ACCT_KEYS` lo amplían
  hot_duelpass_saldo/hot_pvp_carta; `srv.MAX_SAVED_ACCOUNTS` lo sube carga.py por la consola; `srv.pvp_hub`).
- Importar el módulo no abre ficheros ni arranca hilos.
"""
import datetime
import os
import shutil
import threading

from yo2srv import rutas as _rutas

srv = None   # el Server; lo fija setup(). Hasta entonces nada de este módulo que use srv se puede llamar.


def setup(servidor):
    """Enlaza el módulo con el Server (idempotente). run.py lo llama justo antes de importar los nombres."""
    global srv
    srv = servidor
    return servidor


def keep(viejo, servidor):
    """Recarga en caliente (yo2srv/hot.py): el módulo nuevo conserva los MISMOS CTX, SIN_SESION y excepción que el viejo
    (las sesiones y los hilos en curso los comparan por identidad)."""
    global CTX, SIN_SESION, _SinSesion, SinSesion
    CTX, SIN_SESION, _SinSesion, SinSesion = viejo.CTX, viejo.SIN_SESION, viejo._SinSesion, viejo.SinSesion


# ---- PvP fase 1 (V02, P0-2/P1-15): un perfil por CUENTA sin mover el del dueño.
# La cuenta del dueño (Player1: hueco 0, LV 20, baúl, NPCBIN) se queda donde estaba, en la raíz de state.json; las
# demás van en state["accounts"][clave] = {idx, players, profiles, active_slot, profiles_borrados}. Ningún módulo de
# research cambia: load_state() devuelve, en el hilo de una sesión de otra cuenta, una VISTA con las claves de esa
# cuenta en su sitio de siempre (players/profiles/active_slot...), y save_state(vista) las guarda en su cuenta. Las
# claves de cuenta del dueño solo las escribe un hilo del dueño (o uno sin sesión: consola, arranque), así que otro
# jugador no puede pisar su perfil. «accounts» y «owner_acct» solo las escribe register_account (nunca una copia
# vieja que alguien cargó antes). La sesión del hilo: CTX.sess (la pone serve() antes de cada mensaje, el trabajador
# del duelo y los temporizadores del lobby).
# 07-10 B70: «dp_pases» = pases DUELPASS sin abrir de la CUENTA (research/persistencia/proposal.py, DP_PASS_KEY).
# 07-10 §14: «dp_inicio» = la CUENTA ya dio sus 10 DP de inicio (persistencia DP_START_KEY; solo el 1.er personaje).
ACCT_KEYS = ("players", "profiles", "active_slot", "wallet", "profiles_borrados", "dp_pases", "dp_inicio")
HUB_KEYS = ("accounts", "owner_acct", "logins")   # «logins» (07-10): alias de contraseña y usuarios, lo escribe el panel
# Cuenta que hereda el perfil de la raíz si state.json aún no tiene «owner_acct»: resumen sha256 de los 32 B de
# 0x3003 del cliente del owner (iguales en todos los logs). Así da igual quién entre primero tras el cambio.
# P1 (empaquetado, 11-10): en modo paquete (YO2_PAQUETE=1) NO hay dueño por defecto (None salvo YO2_OWNER_ACCT): el
# servidor de otro no le da a nadie el perfil de la raíz de state.json; todas las cuentas van a state["accounts"] y
# empiezan igual (Lv1, baúl «all3»). Con None, register_account nunca da «es el dueño» (ninguna cuenta es None) y el
# panel/el hub comparan `acct == None` -> falso. En desarrollo, el de siempre.
OWNER_ACCT = os.environ.get("YO2_OWNER_ACCT", None if _rutas.paquete() else "0000000000000000")
# Tope de cuentas GUARDADAS (YO2_MAX_CUENTAS). Con LOGIN_ESTRICTO solo cuenta para las que crea el panel (sec_cuentas);
# sin él, register_account tampoco crea más y HUB.login contesta −200 (nunca «ok» sin sesión, CARGA.md §4.6).
MAX_SAVED_ACCOUNTS = int(os.environ.get("YO2_MAX_CUENTAS", "64") or 64)
CTX = threading.local()   # el MISMO objeto en este módulo y en el namespace de run.py (la sesión del hilo: CTX.sess)

# 07-10 (CARGA.md §4.6, fallo grave): una conexión de juego SIN sesión (0x3003 rechazado, tope, o mensajes sin haber
# hecho 0x3003) tenía CTX.sess = None, y None era «el dueño»: load_state()/save_state()/my_pid() le daban el perfil
# de Player1 (pid 1000, baúl, saldo). Ahora:
#  - Los hilos de red (lector «conn-<puerto>», trabajador «duelo-…», escucha «serve-<puerto>») llevan SIEMPRE una
#    sesión o el marcador SIN_SESION en CTX.sess; y si en uno de ellos CTX.sess vale None (hilos de antes de una
#    recarga, o un «old = CTX.sess … CTX.sess = old»), cuenta también como sin sesión.
#  - Con SIN_SESION, load_state() y save_state() lanzan SinSesion (el manejador no hace nada) y my_pid() da 0.
#  - None = dueño solo FUERA de los hilos de red: consola 5799, panel (con su sesión falsa o sin ella), arranque.
# El marcador no admite atributos nuevos (s.duel = … falla) y su cuenta no es vacía (research/mundo cae al dueño con
# una cuenta vacía).
class _SinSesion:
    __slots__ = ()
    owner, acct, idx, pid, slot, name, lang = False, "sin-sesion", None, None, None, "", 0
    lobby = block = pos = world = duel = None
    state, ablock = 0, 0
    conns = __import__("types").MappingProxyType({})   # de solo lectura: nadie apunta conexiones aquí
    def __bool__(self):
        return False
    def __repr__(self):
        return "<sin sesión>"

class SinSesion(RuntimeError):
    """load_state/save_state desde una conexión de juego sin sesión: no se lee ni se toca ningún perfil."""

SIN_SESION = _SinSesion()   # (es falso: no se puede escribir «SIN_SESION or …»); un solo objeto, el mismo en el namespace
NET_THREADS = ("conn-", "duelo-", "serve-")      # nombres de los hilos que atienden la red (serve/_handle/_duel_worker)

def _net_thread():
    return threading.current_thread().name.startswith(NET_THREADS)

def cur_sess():
    s = getattr(CTX, "sess", None)
    if s is None and srv._net_thread():
        return SIN_SESION
    return s

def _no_sess_ctx(s=None):
    return (srv.cur_sess() if s is None else s) is SIN_SESION

def _is_owner_ctx(s):
    if s is None:
        s = srv.cur_sess()
        if s is None:
            return True                          # consola, panel sin sesión falsa, arranque
    if s is SIN_SESION:
        return False
    return bool(getattr(s, "owner", False))

def load_state():
    s = srv.cur_sess()
    if s is SIN_SESION:
        raise SinSesion("conexión sin sesión: no se lee ningún perfil")
    st = srv.load_state_raw()
    if srv._is_owner_ctx(s):
        return st
    a = st.setdefault("accounts", {}).setdefault(s.acct, {"idx": s.idx})
    v = {k: x for k, x in st.items() if k not in srv.ACCT_KEYS and k not in srv.HUB_KEYS}
    for k in srv.ACCT_KEYS:
        if k in a:
            v[k] = a[k]
    v.setdefault("players", a.setdefault("players", {}))
    v["_acct"] = s.acct
    return v

def save_state(st):
    """Guarda st (raíz o vista de una cuenta) sin tocar las claves de las demás cuentas."""
    if not st.get("_acct") and srv._no_sess_ctx():
        raise SinSesion("conexión sin sesión: no se guarda la raíz (perfil del dueño)")
    with srv.STATE_LOCK:
        acct = st.get("_acct")
        try:
            disk = srv.load_state_raw()
        except Exception as e:
            if acct:
                srv.log(f"[ERROR] state.json ilegible ({e!r}): no se guarda la cuenta {acct}")
                return
            disk = {}
        out = {k: x for k, x in st.items() if k != "_acct" and k not in srv.HUB_KEYS}
        if acct:
            for k in srv.ACCT_KEYS:                  # las del dueño, tal como están en disco
                out.pop(k, None)
                if k in disk:
                    out[k] = disk[k]
            entry = disk.setdefault("accounts", {}).setdefault(acct, {})
            for k in srv.ACCT_KEYS:
                if k in st:
                    entry[k] = st[k]
        for k in srv.HUB_KEYS:
            if k in disk:
                out[k] = disk[k]
            elif k in st and not acct:
                out[k] = st[k]
        srv.save_state_raw(out)

def register_account(acct):
    """(índice, ¿es el dueño?) de una cuenta; la apunta en state.json la primera vez. La primera escritura de
    «accounts»/«owner_acct» deja antes una copia state.json.bak-pvp1-<fecha> (migración, nada se mueve)."""
    with srv.STATE_LOCK:
        st = srv.load_state_raw()
        owner = st.get("owner_acct")
        if owner is None and acct == srv.OWNER_ACCT:
            owner = acct
        if acct == owner:
            if st.get("owner_acct") != acct:
                srv._hub_write(st, owner_acct=acct)
                srv.log(f"[cuentas] la cuenta {acct} es la del dueño: usa el perfil de siempre ({st.get('players')})")
            return 0, True
        accts = st.get("accounts", {})
        if acct in accts:
            return int(accts[acct].get("idx", 1)), False
        if getattr(srv.pvp_hub, "LOGIN_ESTRICTO", False):
            # 07-10: con el login 1:1 las cuentas solo se crean desde el panel (HUB.login ya contesta −240 antes)
            srv.log(f"[cuentas] cuenta {acct[:6]}… no existe y LOGIN_ESTRICTO está encendido: no se crea")
            return None, False
        if len(accts) >= srv.MAX_SAVED_ACCOUNTS:
            srv.log(f"[cuentas] tope de {srv.MAX_SAVED_ACCOUNTS} cuentas en state.json: {acct} rechazada")
            return None, False
        idx = 1 + max([int(a.get("idx", 0)) for a in accts.values()] + [0])
        accts = dict(accts); accts[acct] = {"idx": idx, "players": {}, "profiles": {}, "active_slot": 0}
        srv._hub_write(st, accounts=accts)
        srv.log(f"[cuentas] cuenta nueva {acct}: índice {idx}")
        return idx, False

def _hub_write(st, **kv):
    if not any(k in st for k in srv.HUB_KEYS) and os.path.exists(srv.STATE_PATH):
        dst = srv.STATE_PATH + ".bak-pvp1-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copyfile(srv.STATE_PATH, dst)
        srv.log(f"[cuentas] primera escritura de cuentas: copia previa en {os.path.basename(dst)}")
    st.update(kv)
    srv.save_state_raw(st)
