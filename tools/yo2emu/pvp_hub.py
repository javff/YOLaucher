"""Sesiones por cuenta y presencia en el bloque: fase 1 del «plan mínimo» PvP (research/audit/social_pvp.md §3,
pasos 3 y 4; BACKLOG V02, V04, P1-9 y P1-15).

- Session: una por CUENTA. La clave es un resumen (sha256, 16 hex) de los 32 B de 0x3003 AUTH: el cliente los manda
  cifrados y siempre iguales para el mismo usuario y clave (0x3002 va vacío; en todos los logs, los mismos 32 B). No se
  guardan los 32 B, solo el resumen. pid = 1000 + 4·índice de cuenta + hueco: nunca 1-98 (el lobby los toma por NPC,
  research/lobby _npc_by_pid) ni >= 100000 (FAKE_BASE, salas de NPC).
- Hub: bloques (puerto de la sala, idioma) -> {pid: Session}. Lista del bloque con todos (LIST1..5), 0x3112 al entrar y
  salir, y reenvío de 0x5300/0x5310/0x4180/0x4181 a los demás por la cola de envío de cada conexión (Conn.post: el
  hilo del que habla no duerme por cada receptor, V10).

Formatos (sacados del cliente, social_pvp.md P0-4):
- LIST1 0x4212 (FUN_006b8b70 -> FUN_006b8c30): 36 B; es block_player() de run.py.
- LIST2 0x4216 (FUN_006b8d10 -> FUN_006b8de0/FUN_006b8eb0): u32 pid, u8 estado, 16 B piezas, u16 x, u16 y, u8 dir;
  si estado está en 0x67..0x6a, + 16 B nombre, u8, 3×u16. Solo actualiza a quien ya está en LIST1.
- LIST3/LIST4 0x421a/0x421e (FUN_006b8fc0 / FUN_006b9100): u32 pid + 32 B (comentario 0 / 1).
- LIST5 0x4223 (FUN_006b9250 -> FUN_006b9320): u32 pid, u32 equipo, 16 B nombre del equipo, 16 B bandera.
- 0x3112 (FUN_006b9480): u32 pid, u8 op, u8 bloque; op 1 = entra (LIST1 36 B + LIST2 sin pid + LIST5 sin pid),
  op 0 = sale (nada más), op 0x67..0x6a = cambio de estado (16 B nombre, u8, 3×u16).
- 0x5300 (FUN_006c2130): u32 pid, u16 x, u16 y, u8 dir, u8; se ignora si el pid no está en la lista (+0x9a4).
- 0x4180 (FUN_006b9820): u32 pid, u8 índice, 32 B. 0x4181 (FUN_006b9930): u32 pid, u32 mundo, 16 B piezas.

Estados 0x64..0x6a del jugador (+0x130), del cliente: FUN_00880000 (switch en 00880000.c:6157) cuenta 100/0x65 en
DAT_012bb6b0+0x518, 0x66 y 0x69/0x6a en +0x514, y 0x67..0x6a en +0x51c; la ficha del jugador (00890000.c:3756) pinta
la variante «ocupado» si el estado está en 100..0x6a y entonces enseña el nombre de +0x162 (el rival), +0x173 y el
título de +0x178 (FUN_0068fc50). Lectura (P, sin probar en vivo): 0x67/0x68 = en duelo; 0x69/0x6a = en duelo y
además algo de la cuenta 0x514 (¿sala abierta / match?). set_state() lo avisa al bloque; si el mapa pinta una
animación con eso está por ver en vivo con dos clientes.
"""
import collections
import hashlib
import os
import socket
import struct
import sys
import threading
import time

PID_BASE = 1000
# Tope de SESIONES en memoria a la vez (YO2_MAX_SESIONES). 07-10 (CARGA.md §4.6): al llegar al tope, 0x3004 −200 y
# cierre (antes: «ok» sin sesión = el cliente operaba sobre el perfil del dueño). Las sesiones sin ninguna conexión
# viva se liberan SESSION_RELEASE_S después (YO2_SESION_LIBERAR_S), así que cuenta «a la vez», no «desde el arranque».
MAX_ACCOUNTS = int(os.environ.get("YO2_MAX_SESIONES", "64") or 64)
SESSION_RELEASE_S = float(os.environ.get("YO2_SESION_LIBERAR_S", "120") or 120)
DUEL_STATE = 0x67            # «en duelo» (P, ver arriba)
LIST_SELF = True             # LIST2..5 con el propio jugador primero, como LIST1 (ver list_chunks)
# Pose de duelo vista desde fuera: FUN_009d3820 (refresco de los muñecos del mapa) pone la animación de duelo
# (+0x350 = 4/5/6) a quien tiene reg+0x130 en 0x67..0x6a; fuera de ese rango, la normal. reg+0x130 lo pone 0x3112 con
# op 0x67..0x6a (FUN_006b9480) o el estado de LIST2.  C. En PvP se manda al aceptar el reto (research/lobby
# _challenge_reply) y vuelve a 1 al volver al mapa (0x4202/0x4210). Contra una NPC NO hay evidencia de que el
# oficial lo hiciera (los estados 0x97/0x98 de FUN_0088d170 quizá sean eso). AÑADIDO NUESTRO, aprobado por el owner el
# 07-10 (no 1:1): encendido; lo pone npc_watch.py al EMPEZAR el duelo (0x61fe), con el nombre de la NPC y LV/V/D/título
# 0, y lo quita al acabar (o al volver al mapa: 0x4202/0x4210/0x5204). Con él sale «Mirar» (espectador de NPC).
NPC_DUEL_STATE = os.environ.get("YO2_NPC_DUEL_STATE", "1") not in ("", "0")   # =0 lo apaga
# RIVAL_CARD (agente mundo, 06-10): los 23 B que siguen al estado 0x67..0x6a son los DATOS DEL RIVAL que la ficha del
# jugador «en duelo» pinta igual que los propios (FUN_006b9480 -> reg+0x162 nombre, +0x173 LV, +0x174 victorias,
# +0x176 derrotas, +0x178 título; FUN_0089b290 los pasa a la ficha como +0xa4/+0xb2/+0xba/+0x112 = LIST1). Iban a 0:
# «Lv0, 0 V / 0 D, Duelista principiante» para cualquier rival. Corrección con evidencia C: encendido; =0 lo apaga.
# Contra una NPC no hay récord ni LV que mandar: nombre y ceros (como antes).
RIVAL_CARD = os.environ.get("YO2_RIVAL_CARD", "1") not in ("", "0")
PRESENT_STATE = 1            # estado de LIST2 de un jugador presente: el 0x3112 op 1 solo crea el registro con 1 (C)
# 07-10 (sala tras el PvP, research/lobby/NOTES.md «§9b El estado que ven los demás viaja en LIST1»): el estado de otro
# jugador (reg+0x130: nombre en azul, «Retar», icono, pose) SOLO lo escriben LIST1 (byte 34 de la entrada de 0x4212 ->
# struct+0x66, FUN_006b8b70 -> FUN_006b8c30) y 0x3112 (FUN_006b9480). LIST2 (FUN_006b8d10 -> FUN_006b8eb0) NO lo toca:
# solo lee el +0x130 que ya hay para saber si copia los 23 B «en duelo». Nuestro LIST1 iba con 0: cada vez que alguien
# vuelve a pedir las listas (vuelta al mapa, cerrar un menú: 0x4210..) TODOS los demás le quedaban en 0 (sin azul, sin
# «Retar», sin pose) hasta el siguiente 0x3112. Con LIST1_STATE la entrada de LIST1 lleva el estado de verdad (si no es
# 0; con 0 sigue igual).  C (cliente) / P (que el oficial lo mandara ahí: es el único sitio que lo deja puesto)
LIST1_STATE = os.environ.get("YO2_LIST1_STATE", "1") not in ("", "0")
# LIST2 «en duelo»: tras el estado 0x67..0x6a el cliente lee 23 B (FUN_006b8de0: 16 B nombre, u8, 3×u16 = lo mismo
# que 0x3112), no 25. Con 25 la entrada siguiente se leía desplazada 2 B y tras la última salía una entrada fantasma
# con lo que quedara en el búfer de recepción.  C.  YO2_LIST2_DUEL_23=0 vuelve a los 25.
LIST2_DUEL_23 = os.environ.get("YO2_LIST2_DUEL_23", "1") not in ("", "0")
# Reanunciar (0x3112 op 1) al que llega cuando manda su PRIMERA 0x5300, ya con posición. NO está en lo que sabemos
# del oficial: apagado. Probar antes en vivo con un solo envío desde la consola (ver HANDOFF, prueba 2).
REANNOUNCE_ON_FIRST_POS = False
# Entrar/salir de un ÁREA (Academia 5 <-> Tienda 4…) vistos por los demás (research/lobby/NOTES.md, «Entrar/salir de
# mapa vistos por otros»). Cliente (C): cada área es un BLOQUE. Entrada de BLOCKLIST = u16, u8 ÁREA (+4), u8 tipo de
# sala (+8, lo lee el HUD), u8 id de bloque (+9); al cruzar una puerta busca la entrada de su área de destino y manda
# 0x4202 [id, 1] (0x9fc0b7-0x9fc112), 0x4203 le fija el bloque (+0x9ef, FUN_006b8840) y el mapa solo pinta a los de
# la lista con reg+0x131 (bloque de 0x3112/LIST1) == +0x9ef; a los demás los quita en el acto (FUN_00a02ad0).
# Con una sola entrada (área = idioma, id 0) todos estaban en el bloque 0: el que se iba seguía en el mapa andando
# hacia su posición del área nueva (la 0x5300 que manda justo antes de 0x4202, FUN_00a01510). Servidor = P: una
# entrada por área del mundo de la sala (id = índice) y 0x3112 op 1 con el bloque nuevo a los demás. Apagado (cambia
# BLOCKLIST); YO2_AREA_BLOCKS=1 o pvp_hub.AREA_BLOCKS = True lo enciende.
AREA_BLOCKS = os.environ.get("YO2_AREA_BLOCKS", "1") not in ("", "0")   # 06-10: activado por defecto con el OK del owner (YO2_AREA_BLOCKS=0 lo apaga)
WORLD_AREAS = {0: (0, 1, 2, 3, 9), 1: (4, 5, 6, 7)}   # áreas de cada mundo (tabla de conexiones 0xd2d5d4)


def acct_key(data):
    """Resumen de los 32 B de 0x3003 TAL CUAL (id «de hoy»; no se guardan los bytes: son el usuario y la clave
    cifrados). Desde el 07-10 la cuenta sale de login_ids()/Hub.account_of (forma canónica, research/cuentas)."""
    return hashlib.sha256(bytes(data[:32])).hexdigest()[:16]


# ---- Login 1:1 (07-10, research/cuentas/NOTES.md). C = visto en el cliente.
# - Id de cuenta = cifrado.account_id(blob): sha256 del blob con los restos tras el NUL del usuario puestos a cero (el
#   cliente no limpia el campo entre intentos: mismo usuario y clave daban dos ids). Respaldo: si el canónico no
#   existe pero el de hoy (acct_key) sí, se usa ese (cuentas antiguas). Coinciden siempre que no hay restos.
# - state["logins"] (lo escribe el panel, Comunidad › Cuentas): {"alias": {id: cuenta}, "usuarios": {cuenta: usuario}}.
#   Cambiar la contraseña = alias id_nuevo -> cuenta; una cuenta que es destino de algún alias ya NO entra por su id
#   propio (el viejo deja de valer). Nunca se guarda la contraseña ni el blob.
# - LOGIN_ESTRICTO (por defecto encendido; YO2_LOGIN_ESTRICTO=0 = como antes, cualquier 0x3003 crea cuenta): una
#   cuenta que no está en state (accounts / owner_acct / alias) recibe 0x3004 con −240 (FUN_006a6eb0 -> texto 3107
#   «Código de Usuario o Contraseña incorrecta», C; el oficial no distinguía usuario y clave) y la conexión se cierra
#   1,5 s después, como el veto (−246). No se crea nada.
# - −242 «ya está en uso»: ver LOGIN_EN_USO más abajo (07-10, research/cuentas/NOTES.md §6).
LOGIN_ESTRICTO = os.environ.get("YO2_LOGIN_ESTRICTO", "1") not in ("", "0")
AUTH_BAD_LOGIN = -240            # 0xffffff10 -> 3107 (C)
# Servidor lleno (tope de sesiones o de cuentas guardadas): −200 -> 3126 «No se pudo conectar al servidor.» (C, el
# traductor FUN_006a6eb0). Es el texto del oficial que mejor dice «ahora no se puede entrar» sin culpar a la clave (P).
AUTH_SERVER_FULL = -200
# −242 «ya está en uso» (07-10, research/cuentas/NOTES.md §6). FUN_006a6eb0: 0xffffff0e -> texto 4532 «Este Código de
# Usuario ya está en uso.» (ja «すでにログインしています。», en «This User ID is already logged in.») (C). El cliente no
# tiene ningún texto de «te han echado porque has entrado desde otro sitio» (P): el oficial rechazaba al SEGUNDO, no
# echaba al primero. Regla (Hub.in_use_by): un 0x3003 de una cuenta cuya sesión tiene OTRA conexión «en uso» recibe
# −242 y se cierra como el veto. «En uso» = no cerrada, sin 0x0003 (despedida) y con algún mensaje recibido hace
# <= LOGIN_EN_USO_VIVA_S: el cliente manda 0x0005 cada 60 s por CADA conexión (logs del 07-10, C) y corta él solo si
# en 120 s no recibe nada (FUN_0068d080, C); 90 s = un latido + margen (D). Así una conexión muerta sin despedida
# (red caída, túnel) deja de contar en <= 90 s sin esperar al barrido (120 s) ni a IDLE_TIMEOUT (600 s); un cliente
# cerrado o matado manda RST/FIN y deja de contar al momento. En 5731 cuentan las conexiones de cualquier puerto salvo
# la puerta; en 5732/salas, solo las del mismo puerto (el propio cliente solapa 5731 -> 5732 -> sala). Cuentas
# distintas en el mismo PC o por el túnel no se tocan (es por cuenta, no por IP). YO2_LOGIN_EN_USO=0 lo apaga
# (en caliente: pvp_hub.LOGIN_EN_USO = False).
LOGIN_EN_USO = os.environ.get("YO2_LOGIN_EN_USO", "1") not in ("", "0")
LOGIN_EN_USO_VIVA_S = float(os.environ.get("YO2_LOGIN_EN_USO_VIVA_S", "90") or 90)
AUTH_IN_USE = -242               # 0xffffff0e -> 4532 (C)
LOGINS_KEY = "logins"
REJECT_CLOSE_S = 1.5
REJECTED = collections.deque(maxlen=20)   # últimos 0x3003 rechazados: {"id": 6 hex, "t", "puerto"}; sin bytes
_CIF = {}


def _cifrado():
    """research/cuentas/cifrado.py (una vez, en sys.modules["yo2_cifrado"]). None si no carga."""
    m = sys.modules.get("yo2_cifrado")
    if m is None and "err" not in _CIF:
        try:
            import importlib.util
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "research", "cuentas", "cifrado.py")
            sp = importlib.util.spec_from_file_location("yo2_cifrado", p)
            m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)
            sys.modules["yo2_cifrado"] = m
        except Exception as e:                       # noqa: BLE001
            _CIF["err"] = repr(e)
            m = None
    return m


def login_ids(data):
    """(id canónico, id de hoy) de los 32 B de 0x3003. Sin cifrado.py, los dos = el de hoy."""
    legacy = acct_key(data)
    m = _cifrado()
    try:
        return (m.account_id(bytes(data[:32])) if m is not None else legacy), legacy
    except Exception:                                # noqa: BLE001
        return legacy, legacy


def resolve_account(st, canon, legacy, owner_default=None):
    """(cuenta, ¿existe?) para estos ids con el estado st. Orden: alias (contraseña cambiada) del canónico y del de
    hoy; luego cuenta directa (canónico, después el de hoy) si no está tapada por un alias. Si no existe: canónico."""
    st = st or {}
    accts = st.get("accounts") or {}
    owner = st.get("owner_acct") or owner_default
    alias = ((st.get(LOGINS_KEY) or {}).get("alias")) or {}
    ok = lambda a: bool(a) and (a in accts or a == owner)          # noqa: E731
    for k in (canon, legacy):
        if k in alias and ok(alias[k]):
            return alias[k], True
    covered = set(alias.values())
    for k in (canon, legacy):
        if k not in covered and ok(k):
            return k, True
    return canon, False


def _close_sock(c):
    try:
        c.sock.shutdown(socket.SHUT_RDWR)
    except (OSError, AttributeError):
        pass
    try:
        c.sock.close()
    except (OSError, AttributeError):
        pass


class Session:
    def __init__(self, acct, idx, owner):
        self.acct, self.idx, self.owner = acct, idx, owner
        self.slot = None
        self.pid = None
        self.name = ""
        self.lang = 0
        self.conns = {}          # puerto -> conexión más reciente de este cliente (Conn.stale/live_conn)
        self.lobby = None        # conexión de lobby/sala del último 0x4100 (por ella se le avisa)
        self.block = None        # clave (puerto, idioma) del bloque en el que está, o None
        self.pos = None          # últimos 10 B de 0x5300, ya con su pid
        self.parts = bytes(16)   # piezas del avatar (0x4170), las del último mundo que llegó
        self.parts_w = {}        # mundo (0 Domino, 1 Academia) -> 16 B: el cliente manda 0x4170 de los DOS mundos
        self.world = 1           # 0 Domino, 1 Academia (0x4170)
        self.comments = [bytes(32), bytes(32)]   # 0x4172 índice 0 y 1
        self.state = 0           # +0x130 en los demás clientes (LIST2 +0x66)
        self.state_extra = b""   # 23 B si el estado está en 0x67..0x6a (LIST2: ver LIST2_DUEL_23)
        self.ablock = 0          # bloque = área dentro de la sala (AREA_BLOCKS): 0x4203, 0x3112 y LIST1
        self.gone = None         # hora desde la que no le queda ninguna conexión viva (Hub.sweep la libera)

    def __repr__(self):
        return f"<sesión {self.acct} pid={self.pid} {self.name!r}{' dueño' if self.owner else ''}>"


def _pad(b, n):
    b = bytes(b or b"")[:n]
    return b + bytes(n - len(b))


def _name16(text):
    raw = str(text or "").encode("latin1", "replace")[:15]
    return raw + bytes(16 - len(raw))


def _admin_ev(name, *a, **k):
    """Panel (research/admin/eventos.py, 07-10): evento para «Actividad». Sin panel no hace nada; nunca lanza."""
    try:
        import sys as _sys
        m = _sys.modules.get("yo2_eventos")
        f = getattr(m, name, None) if m is not None else None
        return f(*a, **k) if callable(f) else None
    except Exception:                                # noqa: BLE001
        return None


class Hub:
    def __init__(self, env):
        self.env = env                       # globals() VIVO de run.py (block_player, log, register_account)
        self.lock = threading.RLock()
        self.sessions = {}                   # acct -> Session
        self.blocks = {}                     # (puerto, idioma) -> {pid: Session}

    # ------------------------------------------------------------ utilidades
    def log(self, *a):
        (self.env.get("log") or print)(*a)

    def _post(self, s, mid, data, pause=0.02):
        """A la conexión de lobby de s, por su cola (no duerme aquí). Nunca lanza."""
        c = s.lobby
        if c is None or getattr(c, "closed", False):
            return
        try:
            (c.post if hasattr(c, "post") else c.send)(mid, data, pause)
        except Exception as e:
            self.log(f"[hub] aviso 0x{mid:04x} a {s!r}: {e!r}")

    # ------------------------------------------------------------ sesiones (V02)
    def login(self, c, data):
        """0x3003 en cualquier puerto: sesión de la cuenta. None = rechazado: SIEMPRE con su 0x3004 negativo y la
        conexión cerrada (07-10, CARGA.md §4.6: antes, con el tope, run.py contestaba «ok» y la conexión seguía sin
        sesión, que load_state() tomaba por el dueño). Cuenta en uso por otro cliente (in_use_by) -> −242."""
        if len(data) < 32:
            self.reject(c, AUTH_BAD_LOGIN, "")
            return None
        acct, known = self.account_of(data)
        if not known and LOGIN_ESTRICTO:
            self.reject(c, AUTH_BAD_LOGIN, acct)
            return None
        code, busy = None, None
        if acct not in self.sessions and len(self.sessions) >= MAX_ACCOUNTS:
            self.sweep()                             # las que ya se fueron (por si su temporizador no ha saltado)
        with self.lock:
            s = self.sessions.get(acct)
            if s is not None and LOGIN_EN_USO:
                busy = self.in_use_by(s, c)          # 07-10: −242 si OTRO cliente la tiene en uso
            if busy is not None:
                s, code = None, AUTH_IN_USE
            elif s is None:
                if len(self.sessions) >= MAX_ACCOUNTS:
                    self.log(f"[hub] tope de {MAX_ACCOUNTS} sesiones a la vez: 0x3003 de {acct[:6]}… rechazado (−200)")
                    code = AUTH_SERVER_FULL
                else:
                    idx, owner = self.env["register_account"](acct)
                    if idx is None:                  # tope de cuentas guardadas (o la cuenta ya no existe)
                        code = AUTH_BAD_LOGIN if LOGIN_ESTRICTO else AUTH_SERVER_FULL
                    else:
                        s = self.sessions[acct] = Session(acct, idx, owner)
                        self.log(f"[hub] cuenta {acct}: índice {idx}{' (dueño: perfil de siempre)' if owner else ''}")
            if s is not None:
                s.conns[c.port] = c
                s.gone = None
        if s is None:
            if busy is not None:
                rx = getattr(busy, "rx_t", None)
                self.log(f"[cuentas] 0x3003 de {acct[:6]}… (puerto {getattr(c, 'port', '?')}): la cuenta ya está en uso "
                         f"(conexión del puerto {getattr(busy, 'port', '?')}"
                         f"{', misma IP' if getattr(busy, 'peer', 1) == getattr(c, 'peer', 2) else ''}, último "
                         f"mensaje hace {int(time.time() - rx) if rx else '?'} s): 0x3004 {AUTH_IN_USE}")
            self.reject(c, code if code is not None else AUTH_SERVER_FULL, acct)
            return None
        c.sess = s
        return s

    def in_use_by(self, s, c, now=None):
        """−242 (07-10): la conexión de s, distinta de c, que hace que la cuenta esté «en uso» para el 0x3003 de c; None
        si ninguna. Cuenta una conexión no cerrada, sin 0x0003 (c.bye, lo pone _handle) y con un mensaje recibido hace
        <= LOGIN_EN_USO_VIVA_S (c.rx_t, lo pone Conn.saw_client; sin rx_t = de antes de la carga en caliente: no
        cuenta). Nunca la puerta. Si c no es de 5731 (cuentas), solo las del MISMO puerto: el cliente abre 5732 antes
        de despedirse de 5731 y la sala tras despedirse del lobby; un segundo cliente entra siempre por 5731."""
        now = time.time() if now is None else now
        game, gate = self.env.get("GAME_PORT"), self.env.get("GATE_PORT")
        port = getattr(c, "port", None)
        seen = set()
        for o in list(getattr(s, "conns", {}).values()) + [getattr(s, "lobby", None)]:
            if o is None or o is c or id(o) in seen:
                continue
            seen.add(id(o))
            op = getattr(o, "port", None)
            if getattr(o, "closed", False) or getattr(o, "bye", False) or op == gate:
                continue
            if port != game and op != port:
                continue
            rx = getattr(o, "rx_t", None)
            if rx is None or now - rx > LOGIN_EN_USO_VIVA_S:
                continue
            return o
        return None

    # ------------------------------------------------------------ liberar sesiones (07-10, CARGA.md §4.6)
    @staticmethod
    def _live(s):
        """¿Le queda a s alguna conexión abierta (cualquier puerto, o la de lobby)?"""
        cs = list(getattr(s, "conns", {}).values()) + [getattr(s, "lobby", None)]
        return any(c is not None and not getattr(c, "closed", False) for c in cs)

    def sweep(self, now=None):
        """Libera las sesiones sin ninguna conexión viva desde hace SESSION_RELEASE_S o más (y sin un PvP reservado o
        en juego). Si aún estaba en un bloque, sale de él antes (0x3112 op 0 a los demás). La siguiente 0x3003 de esa
        cuenta crea una sesión nueva con el mismo índice (mismos pid). Devuelve cuántas sin conexión quedan."""
        now = time.time() if now is None else now
        pvp = self.env.get("PVP")
        cand, out, pending = [], [], 0
        with self.lock:
            for acct, s in list(self.sessions.items()):
                if self._live(s):
                    s.gone = None
                    continue
                if getattr(s, "gone", None) is None:     # sesión de antes de la recarga o sin on_close: desde ahora
                    s.gone = now
                if now - s.gone < SESSION_RELEASE_S:
                    pending += 1
                else:
                    cand.append(s)
        for s in cand:                               # PVP fuera del cerrojo del hub (tiene el suyo)
            try:
                busy = pvp is not None and s.pid is not None and pvp.by_pid(s.pid) is not None
            except Exception:                        # noqa: BLE001
                busy = False
            with self.lock:
                if busy or self.sessions.get(s.acct) is not s or self._live(s):
                    pending += 1 if busy else 0
                    continue
                del self.sessions[s.acct]
                out.append(s)
        for s in out:
            if s.block is not None:
                self.leave(s, "sesión liberada")
            self.log(f"[hub] sesión de {s.acct[:6]}… liberada (sin conexiones desde hace {int(now - s.gone)} s)")
        return pending

    def _schedule_sweep(self, delay=None):
        """Un solo temporizador a la vez; vuelve a programarse mientras queden sesiones sin conexión."""
        with self.lock:
            t = getattr(self, "_sweep_t", None)
            if t is not None and t.is_alive():
                return
            t = self._sweep_t = threading.Timer(SESSION_RELEASE_S + 1 if delay is None else delay, self._sweep_timer)
            t.daemon = True
            t.start()

    def _sweep_timer(self):
        self._sweep_t = None
        try:
            if self.sweep():
                self._schedule_sweep()
        except Exception as e:                       # noqa: BLE001
            self.log(f"[hub] liberar sesiones: {e!r}")

    def account_of(self, data):
        """(cuenta, ¿existe?) de un 0x3003 (resolve_account sobre el estado guardado). Sin load_state_raw en el
        entorno (pruebas sueltas del hub) no hay nada que mirar: el id de hoy, como antes, y «existe»."""
        canon, legacy = login_ids(data)
        load = self.env.get("load_state_raw")
        if load is None:
            return legacy, True
        try:
            st = load()
        except Exception as e:                       # noqa: BLE001
            self.log(f"[cuentas] estado ilegible al comprobar un 0x3003: {e!r}")
            return canon, False
        return resolve_account(st, canon, legacy, self.env.get("OWNER_ACCT"))

    def reject(self, c, code, acct=""):
        """0x3004 con un resultado negativo (el cliente enseña su texto, manda 0x0003 y cierra; C) y cierre de la
        conexión REJECT_CLOSE_S después, como el veto del panel. Ni usuario ni bytes en el log: 6 hex del id."""
        port = getattr(c, "port", "?")
        REJECTED.append({"id": str(acct or "")[:6], "t": time.time(), "puerto": port, "codigo": code})
        self.log(f"[cuentas] 0x3003 rechazado (cuenta {str(acct or '')[:6]}…, puerto {port}): 0x3004 {code} y "
                 f"conexión cerrada")
        try:
            c.send(0x3004, struct.pack(">iI", code, 0) + b"ESP\0")
        except Exception as e:                       # noqa: BLE001
            self.log(f"[cuentas] 0x3004 {code}: {e!r}")
        c.closed = True
        t = threading.Timer(REJECT_CLOSE_S, _close_sock, (c,)); t.daemon = True; t.start()
        _admin_ev("on_login_rejected", acct, port, code)    # panel (si algún día lo apunta); nunca lanza

    def pid_for(self, s, slot):
        return PID_BASE + 4 * (s.idx if s is not None else 0) + (int(slot or 0) & 3)

    def select(self, c, slot, lang, name):
        """0x4100 PSELECT: hueco, pid, idioma y conexión de lobby de la sesión."""
        s = getattr(c, "sess", None)
        if s is None:
            return None
        with self.lock:
            s.slot = int(slot) & 3
            s.pid = self.pid_for(s, s.slot)
            s.name, s.lang, s.lobby = name or "Duelista", lang, c
        return s

    def by_pid(self, pid):
        with self.lock:
            for s in self.sessions.values():
                if s.pid == pid:
                    return s
        return None

    def attach(self, c, pid):
        """Puerto de duelo: no hay 0x3003; la sesión sale del pid de 0x6001 (u32)."""
        s = self.by_pid(pid)
        if s is not None:
            with self.lock:
                c.sess = s
                s.conns[c.port] = c
        return s

    # ------------------------------------------------------------ presencia (V04)
    def others(self, s):
        with self.lock:
            if s is None or s.block is None:
                return []
            return [o for o in self.blocks.get(s.block, {}).values() if o is not s]

    def list1_entry(self, o):
        """Entrada de LIST1 (0x4212, 36 B) de otro jugador: con su sesión (su LV/victorias/título, prueba 2) y, con
        LIST1_STATE, su estado en el byte 34 (struct+0x66 -> reg+0x130, lo único que lo deja puesto: ver la flag)."""
        b = self.env["block_player"](o.pid, o.name, o)
        st = (getattr(o, "state", 0) or 0) & 0xff
        if LIST1_STATE and st and isinstance(b, (bytes, bytearray)) and len(b) == 36:
            b = bytes(b[:34]) + bytes([st]) + bytes(b[35:])
        return b

    def block_world(self, key):
        """Mundo de un bloque (puerto de sala, idioma): 1 Academia si su tipo es de la puerta roja, si no 0."""
        try:
            kinds, base = self.env["ROOM_KINDS"], self.env["ROOM_PORT_BASE"]
            return 1 if kinds[key[0] - base] in self.env["ACAD_KINDS"] else 0
        except Exception:
            return None

    def list2_body(self, o, world=None):
        """LIST2 sin pid: u8 estado, 16 B piezas, u16 x, u16 y, u8 dir (+ 23 B si estado 0x67..0x6a, LIST2_DUEL_23).
        Prueba 3 (skins): FUN_006b8eb0 guarda las piezas en reg+0x134 + MUNDO*0x14, con el mundo del que RECIBE
        (DAT_012bb6b0+0x448; desensamblado 0x6b8ec4-0x6b8ecd), no hay mundo en el mensaje: van las piezas de
        ese mundo (el del bloque), no las del último 0x4170, que puede ser del otro mundo.  C"""
        if world is None:
            world = self.block_world(o.block) if o.block is not None else None
        parts = o.parts_w.get(world, o.parts) if world is not None else o.parts
        x = y = d = 0
        if o.pos and len(o.pos) >= 9:
            x, y, d = struct.unpack(">HHB", o.pos[4:9])
        # Prueba 3 (A): el estado va a 1 si no hay otro. En FUN_006b9480 (0x3112) la «op» es el byte +0x66 de la
        # misma estructura (local_5a = local_c0+0x66: la pila se pone a cero entera, 0x30 ints) y FUN_006b8de0 (LIST2)
        # escribe ENCIMA el estado en ese +0x66; después «if (local_5a == 1)» crea el registro y «== 0» lo BORRA.
        # Con estado 0, el op 1 de un nuevo no creaba nada (el HUD seguía en «1/1000») y set_state(0) borraba al
        # jugador de la lista de los demás. 1 = presente normal (FUN_00880000 solo cuenta aparte 100..0x6a).  C
        b = bytes([(o.state or PRESENT_STATE) & 0xff]) + _pad(parts, 16) + struct.pack(">HHB", x, y, d)
        if 0x67 <= o.state <= 0x6a:
            b += _pad(o.state_extra, 23 if LIST2_DUEL_23 else 25)    # FUN_006b8de0 lee 23 (LIST2_DUEL_23)
        return b

    def rival_card(self, o):
        """(LV, victorias, derrotas, título) de la sesión o como en su LIST1 (36 B: [20], [21:23], [23:25], [25:27])
        para los 23 B de «en duelo» de su rival; None con RIVAL_CARD apagado o si no se puede."""
        if not RIVAL_CARD or o is None or getattr(o, "pid", None) is None:
            return None
        try:
            e = self.list1_entry(o)
            return (e[20],) + struct.unpack(">HHH", e[21:27])
        except Exception as ex:                      # noqa: BLE001
            self.log(f"[hub] datos de rival de {o!r}: {ex!r}")
            return None

    def list5_body(self, o):
        return struct.pack(">I", 0) + bytes(16) + bytes(16)        # sin equipos todavía (V18)

    def join_msg(self, o):
        head = struct.pack(">IBB", o.pid, 1, self.ablock_of(o))       # u8 bloque -> reg+0x131 (AREA_BLOCKS)
        return head + self.list1_entry(o) + self.list2_body(o) + self.list5_body(o)

    # ------------------------------------------------------------ áreas = bloques (AREA_BLOCKS)
    def area_list(self, key):
        """Áreas de la sala (una entrada de BLOCKLIST por área, id = índice) o None (flag apagado / My Room)."""
        if not AREA_BLOCKS or key is None:
            return None
        return WORLD_AREAS.get(self.block_world(key))

    def blocklist(self, key, kind):
        """Datos de 0x4201 con un bloque por área (u16 máx., y por entrada u16 0, u8 área, u8 tipo, u8 id); None =
        la lista de siempre."""
        areas = self.area_list(key)
        if not areas:
            return None
        return struct.pack(">H", 8) + b"".join(struct.pack(">HBBB", 0, a, kind & 0xff, i) for i, a in enumerate(areas))

    def area_block(self, s, key, data, area):
        """Bloque para 0x4203 (+0x9ef): al entrar en la sala (data[1] == 0) el del área de entrada; en un cambio de
        área (data[1] != 0) el que pide el cliente (data[0], ya buscado por área en BLOCKLIST). 0 sin el flag."""
        areas = self.area_list(key)
        if not areas or s is None:
            return 0
        if len(data) >= 2 and data[1] != 0:
            return data[0] if data[0] < len(areas) else getattr(s, "ablock", 0)
        return areas.index(area) if area in areas else 0

    def ablock_of(self, s):
        return (getattr(s, "ablock", 0) or 0) & 0xff if AREA_BLOCKS and s is not None else 0

    def enter(self, s, key, ablock=None):
        """0x4202 ENTERBLOCK. Devuelve los miembros (s el primero). A los demás, 0x3112 op 1 si s acaba de llegar
        (el cliente manda 0x4202 dos veces seguidas: la segunda no se vuelve a anunciar) o, con AREA_BLOCKS, si ha
        cambiado de área (bloque nuevo + la posición que mandó justo antes)."""
        if s is not None and ablock is not None and key is None:
            s.ablock = 0                                   # My Room: su LIST1 tiene que ir con el bloque de 0x4203
        if s is None or s.pid is None or key is None:
            return [s] if s is not None else []
        with self.lock:
            moved = AREA_BLOCKS and ablock is not None and ablock != getattr(s, "ablock", 0)
            if ablock is not None:
                s.ablock = ablock
            fresh = not (s.block == key and self.blocks.get(key, {}).get(s.pid) is s)
            if fresh:
                if s.block is not None:
                    self.leave(s, "cambia de bloque")
                s.block, s.pos, s.state, s.state_extra = key, None, 0, b""
                self.blocks.setdefault(key, {})[s.pid] = s
            rest = self.others(s)
        if fresh:
            msg = self.join_msg(s)
            for o in rest:
                self._post(o, 0x3112, msg)
            self.log(f"[hub] {s!r} entra en el bloque {key}: {len(rest)} más")
        elif moved:
            msg = self.join_msg(s)
            for o in rest:
                self._post(o, 0x3112, msg)
            self.log(f"[hub] {s!r} cambia de área en {key}: bloque {s.ablock}, pos {s.pos[4:8].hex() if s.pos else '-'}"
                     f" ({len(rest)} más)")
        if fresh or moved:
            _admin_ev("on_map", s, key, moved)               # panel: «X entra en Domino · Clock Square»
        return [s] + rest

    def leave(self, s, why=""):
        """Sale del bloque: 0x3112 (pid, 0, 0) a los que quedan."""
        with self.lock:
            key = s.block if s is not None else None
            if key is None:
                return
            members = self.blocks.get(key, {})
            if members.get(s.pid) is s:
                members.pop(s.pid, None)
            if not members:
                self.blocks.pop(key, None)
            s.block = None
            rest = list(members.values())
        for o in rest:
            self._post(o, 0x3112, struct.pack(">IBB", s.pid, 0, 0))
        self.log(f"[hub] {s!r} sale del bloque {key} ({why}): quedan {len(rest)}")

    def on_close(self, c, why=""):
        """Conexión cerrada (socket o 0x0003): si era la de lobby de su sesión, sale del bloque (P1-9)."""
        s = getattr(c, "sess", None)
        if s is None:
            return
        with self.lock:
            if s.conns.get(c.port) is c and why != "0x0003":
                s.conns.pop(c.port, None)
            mine = s.lobby is c
            gone = why != "0x0003" and not self._live(s)
            if gone and getattr(s, "gone", None) is None:
                s.gone = time.time()
        if mine:
            self.leave(s, why or "cerrada")
        if gone:
            self._schedule_sweep()                   # 07-10: se libera si en SESSION_RELEASE_S no vuelve

    def set_state(self, s, state, rival_name="", b=0, u=(0, 0, 0)):
        """Avisa al bloque de un cambio de estado (0x3112 op = estado 0x67..0x6a: 16 B nombre, u8, 3×u16;
        FUN_006b9480). Con otro estado (0, 100 «sala abierta»…) se reanuncia con op 1 y LIST2 con ese estado (0 ->
        PRESENT_STATE): para alguien que ya está en la lista, FUN_006b9480 toma el estado de LIST2 como «op» y lo
        guarda en +0x130 (con 0 lo borraría). Para «en duelo con X» (DUEL_STATE): ver el docstring del módulo."""
        if s is None or s.pid is None:
            return
        with self.lock:
            s.state = int(state) & 0xff
            s.state_extra = (_name16(rival_name) + struct.pack(">BHHH", b & 0xff, *[v & 0xffff for v in u])
                             if 0x67 <= s.state <= 0x6a else b"")
            rest = self.others(s)
        if 0x67 <= s.state <= 0x6a:
            msg = struct.pack(">IBB", s.pid, s.state, self.ablock_of(s)) + s.state_extra
        else:
            msg = self.join_msg(s)
        for o in rest:
            self._post(o, 0x3112, msg)

    def on_lobby_msg(self, c, msg_id, data):
        """Antes de contestar en lobby_reply: apunta lo que los demás tienen que ver y se lo reenvía. No contesta a c
        (las respuestas propias siguen en run.py igual que con un solo jugador). Datos validados: entrada no fiable."""
        s = getattr(c, "sess", None)
        if s is None:
            return
        inblock = s.block is not None and s.lobby is c
        if msg_id == 0x0003:
            if s.lobby is c:
                self.leave(s, "0x0003")
            return
        if msg_id == 0x4170 and len(data) >= 20:          # REGAVATARINFO: u32 mundo, 16 B piezas, u32
            s.world = struct.unpack(">I", data[:4])[0] & 0xff
            s.parts = bytes(data[4:20])
            s.parts_w[s.world] = s.parts
            if inblock:
                for o in self.others(s):
                    self._post(o, 0x4181, struct.pack(">II", s.pid, s.world) + s.parts)
        elif msg_id == 0x4172 and len(data) >= 33:        # REGPLAYERCOMMENT: u8 índice, 32 B
            i = data[0] & 1
            s.comments[i] = bytes(data[1:33])
            if inblock:
                for o in self.others(s):
                    self._post(o, 0x4180, struct.pack(">IB", s.pid, i) + s.comments[i])
        elif msg_id in (0x5300, 0x5310) and inblock and s.pid is not None:
            # El pid de los datos se sustituye por el de la sesión: un cliente no puede mover a otro.
            if msg_id == 0x5300:
                if len(data) != 10:
                    return
                first = s.pos is None
                s.pos = struct.pack(">I", s.pid) + bytes(data[4:10])
                fwd = s.pos
                if first and REANNOUNCE_ON_FIRST_POS:
                    # Prueba 2, fallo 1 (sin causa demostrada): el 0x3112 op 1 de enter() sale con LIST2 en (0,0)
                    # porque aún no hay posición; los que ya estaban no crean el muñeco del nuevo hasta volver a
                    # entrar. Con el flag, op 1 otra vez con la posición real (para un pid ya en la lista
                    # FUN_006b9480 solo reescribe sus datos, FUN_006b8c30/FUN_006b8eb0, y pone +0x9b8 = 1).
                    msg = self.join_msg(s)
                    for o in self.others(s):
                        self._post(o, 0x3112, msg)
            else:
                if len(data) < 6 or len(data) > 64:
                    return
                fwd = struct.pack(">I", s.pid) + bytes(data[4:])
            for o in self.others(s):
                self._post(o, msg_id, fwd)
        # 0x4288 con rival NPC: la pose «en duelo» ya no se pone aquí (07-10) sino en npc_watch.py al EMPEZAR el duelo
        # (0x61fe): así no sale si el reto se rechaza (dp_npc_gate, −612) ni si el duelo no llega a empezar.
        elif (msg_id in (0x4202, 0x4210) or (msg_id == 0x5204 and getattr(s, "npc_pose", False))) \
                and 0x67 <= s.state <= 0x6a:               # vuelve del duelo al mapa
            # (prueba 3: solo los estados «en duelo»; el 100 de «sala abierta» se mantiene hasta borrar la sala,
            # y el cliente manda 0x4210 al refrescar la lista). 0x5204 (ITEMGET al volver al mapa) solo para la pose
            # de NPC (npc_watch ya la quita al acabar el duelo: esto es por si acaso)
            s.npc_pose = False
            self.set_state(s, 0)

    def list_chunks(self, c, req):
        """Datos de LIST2..5 (sin START/END): él mismo (LIST_SELF) y los OTROS del bloque.
        Prueba 3 (skins): las piezas de un jugador solo se escriben en su registro (reg+0x134+mundo*0x14) con LIST2,
        0x3112 op 1 (los dos por FUN_006b8eb0) o 0x4181 (FUN_006b9930, desensamblado 0x6b99b8-0x6b99cb). LIST1
        ya lleva al propio jugador; si LIST2 no lo lleva, su registro se queda sin piezas y su muñeco sale con el
        aspecto por defecto (pelo naranja, chaqueta roja: lo que vio cada uno de sí mismo).  C (escritores) / P"""
        s = getattr(c, "sess", None)
        inb = s is not None and s.lobby is c and s.block is not None
        rest = self.others(s) if inb else []
        if inb and LIST_SELF:
            rest = [s] + rest
        if not rest:
            return []
        if req == 0x4214:
            entries = [struct.pack(">I", o.pid) + self.list2_body(o) for o in rest]
        elif req in (0x4218, 0x421c):
            i = 0 if req == 0x4218 else 1
            entries = [struct.pack(">I", o.pid) + _pad(o.comments[i], 32) for o in rest]
        elif req in (0x4220, 0x4221):
            entries = [struct.pack(">I", o.pid) + self.list5_body(o) for o in rest]
        else:
            return []
        out, cur = [], b""
        for e in entries:                                  # máx. 1024 B por mensaje (FUN_00689ea0)
            if len(cur) + len(e) > 1024:
                out.append(cur); cur = b""
            cur += e
        return out + ([cur] if cur else [])

    def avatar_of(self, pid):
        """(nombre, x, y, dir) de otro jugador conectado para 0x5340, o None."""
        s = self.by_pid(pid)
        if s is None:
            return None
        x, y, d = (struct.unpack(">HHB", s.pos[4:9]) if s.pos else (400, 300, 0))
        return s.name, x, y, d
