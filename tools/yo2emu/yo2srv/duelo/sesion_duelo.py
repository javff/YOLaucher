"""Estado de duelo por sesión y ciclo de vida de las conexiones de duelo (paso E6 de la reestructura, REESTRUCTURA.md §3.4):
`duel_state`, `df_for`, `df_end_mark`, `duel_rival`, `duel_begin`, `duel_ip_for`, el abandono (`_abandon_duel`) y
`on_conn_closed`/`on_conn_accepted`, más los dos avisos al panel del duelo contra NPC (`_npc_name`, `_admin_npc`).

Es el código de run.py MOVIDO sin cambiar la lógica. Reglas (REESTRUCTURA.md §2.3):
  - `DUEL` y `LOBBY_ROOM` (duelo en curso y conexión de sala de la consola / ocg_test, que sobreviven a las recargas) son el
    MISMO dict en este módulo y en el namespace de run.py: `setup()` adopta el del namespace si ya había uno.
  - todo lo demás (funciones y objetos que alguien puede reasignar o parchear: `DF`, `DFM`, `LP`, `HUB`, `OCG`,
    `df_for`, `on_conn_closed`, `current_conn`, `live_conn`, `load_state`…, y las banderas) se lee como `srv.X` en el
    momento de usarlo; lo que puede no existir (`DT`, `TP`, `TO`) se lee con `getattr(srv, "X", None)` (equivale al
    `globals().get("X")` de antes y funciona igual con el `srv` mínimo que las cargas en caliente ponen en un vivo viejo).
  - `CLOSE_OLD_DUEL_SOCK` (bandera del panel, DUDOSA) la fija yo2srv/arranque.py en el namespace y aquí se lee como
    `srv.CLOSE_OLD_DUEL_SOCK`.
`on_conn_closed` lo llama `_handle`/`serve` (yo2srv/red/servidor.py) como `srv.on_conn_closed`: la carga en caliente de
research/seguridad_duelo/hot_reserva.py le cambia el `__code__` (por eso solo lee `srv.X`, `getattr` y builtins).
"""
import os
import threading

srv = None      # el Server; lo fija setup(). Hasta entonces nada de este módulo se puede llamar.

DUEL = {"rival": 0}   # duelo en curso (lo fija REQCREATEROOM en el lobby)
LOBBY_ROOM = {"c": None}


def setup(servidor):
    """Enlaza el módulo con el Server (idempotente). run.py lo llama (yo2srv.duelo.setup) justo antes de importar los
    nombres de aquí. DUEL y LOBBY_ROOM sobreviven a una re-ejecución de run.py en el mismo namespace."""
    global srv, DUEL, LOBBY_ROOM
    srv = servidor
    DUEL = servidor.ns.get("DUEL") or DUEL
    LOBBY_ROOM = servidor.ns.get("LOBBY_ROOM") or LOBBY_ROOM
    return servidor


def _npc_name(rival):
    """Nombre de la NPC (para el panel); "" si no se sabe."""
    try:
        return next((n[1] for n in srv.NPCS if n[0] == rival), "")
    except Exception:
        return ""

def _admin_npc(kind, c, *a, rival=None):
    """Panel: inicio o fin de un duelo contra una NPC. Todo dentro del try: nunca rompe el duelo."""
    try:
        s = getattr(c, "sess", None)
        srv._admin_ev(kind, s, srv._npc_name(srv.duel_rival(s) if rival is None else rival), *a)
    except Exception:
        pass

# V08/V09 (P0-8): estado de duelo POR SESIÓN. s.duel = {"rival": NPC, "lobby": conexión de sala del 0x4288 o del reto,
# "df": sus manejadores de duelfin}. Cada make_handlers lleva su propio sess {"end", "npc", "ask"}: dos duelos a la vez
# (NPC o PvP) ya no comparten DuelEnd ni conexión de lobby. Las conexiones sin sesión (ocg_test, consola) siguen con
# DUEL/LOBBY_ROOM/DF globales, que duel_begin también apunta para la consola («desatascar» de arriba).
def duel_state(s=None):
    s = s if s is not None else srv.cur_sess()
    if s is None or s is srv.SIN_SESION:         # 07-10: sin sesión, lo global (como antes de 0x6001); su
        return None                              # duelfin global lanza SinSesion si intenta leer o guardar un perfil
    d = getattr(s, "duel", None)
    if d is None:
        d = s.duel = {"rival": 0, "lobby": None, "df": None}
    df = d.get("df")
    if df is not None and d.get("dfm") is not None and d["dfm"] is not getattr(srv, "DFM", None):
        # R4: duelfin se recargó en caliente (srv.DFM es otro módulo). La sesión pasa al nuevo en cuanto no tiene un duelo en
        # curso (un DuelEnd sin acabar en sess["end"], o «¿te rindes?» abierto): el duelo que empezó con el viejo acaba con el
        # viejo. Un DuelEnd con over=True es un duelo ya terminado (el cliente no siempre manda el 0x0003 que lo borra).
        sess = df.get("sess") or {}
        if getattr(sess.get("end"), "over", True) and not sess.get("ask"):
            d["df"] = None
    if d.get("df") is None and getattr(srv, "DFM", None) is not None and srv.DF is not None:
        d["dfm"] = srv.DFM
        d["df"] = srv.DFM.make_handlers(srv.load_state, srv.save_state, lambda: srv.live_conn(d["lobby"]),
                                        confirm_surrender=True)
    return d

# Solo se usa lo de la sesión si duel_begin lo apuntó (d["lobby"]): con una recarga en caliente de solo este bloque
# (reload_duel.py) el lobby_reply viejo sigue apuntando los globales, y un duelfin de sesión sin h_autoduelstart ni
# conexión de lobby dejaría los resultados sin «Siguiente».
def df_for(c):
    d = srv.duel_state(getattr(c, "sess", None)) if getattr(c, "sess", None) is not None else None
    if d is not None and d.get("df") is not None and d.get("lobby") is not None:
        return d["df"]
    return getattr(srv, "DF", None)

def df_end_mark(c, DF, msg_id):
    """B58: apunta en c.df_end la partida de duelfin de esta conexión de duelo (on_conn_closed solo cuenta la
    desconexión como derrota si sigue siendo la misma). 07-10: NO con 0x0005: es el primer mensaje de la primera
    conexión de duelo de cada cliente (log 20261007-031049 y todos los de 06-10: 0x0005 #1 y luego 0x6001 #2) y
    llega antes de 0x6001, que es el que engancha la sesión (c.sess). Con él se cogía el duelfin global, no el de la
    sesión, y al cortarse ese duelo no se contaba la derrota ni salía en Actividad."""
    if DF is not None and not hasattr(c, "df_end") and msg_id != 0x0005:
        c.df_end = DF["sess"].get("end")

def duel_rival(s=None):
    d = srv.duel_state(s)
    return d["rival"] if d is not None and d.get("lobby") is not None else DUEL.get("rival", 0)

def duel_begin(c, rival, mode=0):
    """Reto a una NPC (0x4288 con rival, o reto de NPC aceptado en la sala: research/lobby _duel_notice).
    mode = último u8 de 0x43a0 (1 solo con NPC_RULES_2008 npc_window: duelfin cierra el «match» tras 1 partida)."""
    DUEL["rival"] = rival
    LOBBY_ROOM["c"] = c
    d = srv.duel_state(getattr(c, "sess", None)) if getattr(c, "sess", None) is not None else None
    if d is not None:
        d["rival"], d["lobby"] = rival, c
    df = d["df"] if d is not None and d.get("df") is not None else getattr(srv, "DF", None)
    if df is not None:
        df["h_autoduelstart"](rival, mode)
    srv._admin_npc("npc_start", c, rival=rival)   # panel: «X reta a Jaden Yuki»
    # 07-10: la pose «en duelo» contra una NPC (pvp_hub.NPC_DUEL_STATE) ya NO se pone aquí (ni en el 0x4288 de
    # pvp_hub): la pone npc_watch.py cuando el duelo EMPIEZA (0x61fe), para que no salga si el duelo no llega a empezar.

def duel_ip_for(c):
    """IP para 0x4306 DUELSERVERNOTICE en un reto PvP: YO2_PUBLIC_IP si está puesta; si no, la IP local por la que
    entró ESE cliente (getsockname: con YO2_BIND=0.0.0.0 o varias IPs, cada uno recibe la suya); si no, PUBLIC_IP."""
    if os.environ.get("YO2_PUBLIC_IP"):
        return srv.PUBLIC_IP
    try:
        ip = c.sock.getsockname()[0]
        if ip and ip != "0.0.0.0":
            return ip
    except Exception:
        pass
    return srv.PUBLIC_IP


# B58/B59: ciclo de vida de las conexiones. serve() llama a on_conn_accepted al aceptar y a on_conn_closed al salir del
# bucle de lectura; lobby_reply llama a on_conn_closed(c, "0x0003") cuando el cliente cierra la sesión.
# (CLOSE_OLD_DUEL_SOCK, la bandera de on_conn_accepted, la fija yo2srv/arranque.py: ver el docstring del módulo.)
def on_conn_closed(c, why="cerrada"):
    """Limpieza al cerrar una conexión (puede llamarse varias veces). Solo con el socket cerrado de verdad se marca
    c.closed (Conn.send deja de mandar por ella); con 0x0003 solo se avisa al lobby."""
    if why != "0x0003":
        c.closed = True
        q = getattr(c, "duel_q", None)
        if q is not None:
            q.put(None)                          # para el hilo trabajador del duelo de esta conexión
        if hasattr(c, "stop_post"):
            c.stop_post()                        # y el de su cola de envío (V10)
        if getattr(srv, "DT", None) is not None: # seguridad_duelo: mueren las reservas de los 0x4306 de esta conexión
            try:
                srv.DT.drop_conn(c)
            except Exception as e:
                srv.log(f"[reserva] drop_conn: {e!r}")
    try:
        srv.HUB.on_close(c, why)                 # P1-9: sale del bloque -> 0x3112 op 0 a los demás
    except Exception as e:
        srv.log(f"[hub] on_close: {e!r}")
    if why != "0x0003":
        srv._admin_ev("on_close", c)             # panel: «X sale del juego» si ya no le queda ninguna conexión
    if srv.LP is not None and hasattr(srv.LP, "on_close"):
        try:
            srv.LP.on_close(c)                   # salas, reto pendiente y LOBBY_ROOM["c"] de esa conexión (lote L6)
            if getattr(srv, "TP", None) is not None: srv.TP.on_close(c)   # intercambio en curso de esa conexión
            if getattr(srv, "TO", None) is not None: srv.TO.on_close(c)   # deja de mirar el cuadro del torneo
        except Exception as e:
            srv.log(f"[lobby] on_close: {e!r}")
    if c.port != srv.DUEL_PORT or why == "0x0003":
        return
    if getattr(c, "pvp", None) is not None:      # PvP (P1-9): al otro 0x649b + su victoria; lo hace su PvpMatch
        try:
            c.pvp.on_close(c)
        except Exception as e:
            srv.log(f"[pvp] on_close: {e!r}")
        return
    if getattr(c, "booked_fail", None):           # seguridad_duelo: 0x6001 rechazado, nunca tuvo sesión ni duelo
        return
    s = getattr(c, "ocg", None)
    if srv.current_conn(c) is not c:
        return                                   # un socket de duelo viejo: su partida ya se cerró al llegar el nuevo
    if s is not None and s.over:
        return                                   # el duelo ya acabó con su resultado
    # el socket del duelo en curso se cae sin fin (cliente cerrado o caído): duelfin lo cuenta como derrota, pero
    # solo si la partida de duelfin sigue siendo la de esta conexión (si ya hay otro 0x4288, no se toca)
    srv.log(f"[duelo] el socket de duelo se cierra con el duelo {getattr(s, 'id', '-')} sin acabar")
    srv._abandon_duel(c)
    df = srv.df_for(c)                           # V08: el duelfin de SU sesión (antes el global)
    if df is not None and df["sess"].get("end") is getattr(c, "df_end", None):
        old = getattr(srv.CTX, "sess", None)
        srv.CTX.sess = getattr(c, "sess", None)  # _handle ya puso CTX.sess = None: la derrota va al perfil de c
        try:
            df["h_disconnect"](c)
            srv._admin_npc("npc_end", c, 1, "disconnect")
        except Exception as e:
            srv.log(f"[duelfin] h_disconnect: {e!r}")
        finally:
            srv.CTX.sess = old

def _abandon_duel(c):
    """Marca la sesión ocg de c como acabada y cierra su duelo en duelcore, sin bloquear al que llama (el bombeo
    puede tener el candado de la sesión durante todo el turno de la NPC)."""
    s = getattr(c, "ocg", None)
    if s is None or s.over:
        return
    s.over = True                                # pump lo mira en cada vuelta
    def end():
        try:
            srv.OCG.core().end(s.id)
            srv.log(f"[duelo] duelo {s.id} cerrado en duelcore")
        except Exception as e:
            srv.log(f"[duelo] cerrar el duelo {s.id} en duelcore: {e!r}")
    threading.Thread(target=end, daemon=True, name=f"fin-duelo-{s.id}").start()

def on_conn_accepted(c, old):
    """Nueva conexión c en un puerto donde ya había otra (old). En el puerto de duelo el cliente no cierra nunca el
    socket viejo: su sesión ocg queda abandonada (se cierra en duelcore) y Conn.send ya no manda por él."""
    if old is None or old is c:
        return
    if c.port == srv.DUEL_PORT:
        if getattr(old, "ocg", None) is not None and not old.ocg.over:
            srv.log(f"[duelo] nueva conexión de duelo con el duelo {old.ocg.id} sin acabar: se abandona")
            srv._abandon_duel(old)
        q = getattr(old, "duel_q", None)
        if q is not None:
            q.put(None)
        if srv.CLOSE_OLD_DUEL_SOCK:
            old.closed = True
            try:
                old.sock.close()
            except OSError:
                pass
