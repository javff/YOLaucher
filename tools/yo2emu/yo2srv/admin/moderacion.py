"""Moderación del servidor (11-10): lo que el anfitrión necesita para llevar su servidor, SIN depender del panel.

Antes vivía en el panel privado del owner (research/admin/panel.py y sec_cuentas.py); el panel no va en el paquete público y
el lanzador necesita esto. Se ha MOVIDO aquí (no copiado) y el panel lo importa y lo llama, así que el panel del owner se
comporta igual: mismos ficheros en disco (admin_vetados.json, admin_auditoria.log, state.json.bak-admin-*) y mismos mensajes.

  vetos        load_bans / save_bans / is_banned / bans_path      admin_vetados.json junto a state.json
  login        instalar_veto() / install_login_hook()              envuelve HUB.login: cuenta vetada -> 0x3004 −246 y conexión
                                                                   cerrada. Lo instala yo2srv/arranque.py SIEMPRE (haya o no
                                                                   panel); el panel solo le añade sus ganchos (HOOKS)
  jugadores    sessions / online / jugadores_basico / kick / ban / unban
  nivel        set_level / nivel_guardado (12-10; antes solo en el panel): rec["lv"] + 0x4140 al momento
  puntos       set_points / puntos_guardados / apply_money (12-10): DP (rec["dpp"]) y MP; el panel suma con apply_money
  cuentas      cuentas / crear_cuenta / cambiar_clave / borrar_cuenta   modo LOGIN_ESTRICTO (research/cuentas/cifrado.py)
  avisos       chat_notice (0x4402) y mainte_notice (0x4700)
  copias       backup_state / backups
  /publico     public_status

Reglas: nada de aquí abre puertos ni lanza hilos salvo el cierre diferido de una conexión vetada. El módulo lee el namespace de
run.py (`NS`, el mismo dict que `srv.ns`) en el momento de usarlo. Las contraseñas solo viven en memoria el tiempo de calcular
el id de la cuenta: no van al log, a la auditoría ni a ningún fichero. Los errores son ApiError (mensaje en español, `code` HTTP
y `clave` estable para que una interfaz en otro idioma los traduzca).
"""
import datetime
import glob
import json
import os
import re
import shutil
import socket
import struct
import sys
import threading
import time
import types
import unicodedata

EMU = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

NS = {}                        # globals() VIVOS de run.py (los fija setup() o, con el panel suelto, enlazar())
srv = None
LOGIN_T = {}                   # cuenta -> hora en que entró (su sesión pasó de desconectada a conectada)
BANS_LOCK = threading.RLock()
AUDIT_LOCK = threading.Lock()
HOOKS = {"rechazo": None, "flags": None}   # ganchos del panel en el login: rechazo(acct) -> truthy; flags(acct) -> int
NOTICE_MAX = 200               # caracteres del aviso de chat (0x4402 cabe en 1024 B de sobra)
SYSTEM_NAME = "SISTEMA"        # nombre del remitente de los avisos de chat (pid 0)
MAX_LEN = 16                   # usuario y contraseña: 16 B cp1252 (campos de 17 B del cliente, C)
KEY = "logins"
ZONES = {0: "Clock Square", 1: "Game Shop", 2: "Pier", 3: "Kaiba Corp.", 9: "R&D Lab", 4: "Duel Academy",
         5: "Store", 6: "Duel Space", 7: "Slifer Red"}
LOCK = threading.RLock()       # una escritura de cuentas a la vez


class ApiError(Exception):
    """Error con mensaje en español, código HTTP y una clave corta y estable (`clave`) para traducirlo."""

    def __init__(self, msg, code=400, clave=None):
        super().__init__(msg)
        self.code = code
        self.clave = clave or "error"


# ---------------------------------------------------------------- enlace con el servidor
def setup(servidor):
    """Enlaza el módulo con el Server (idempotente). arranque.py / yo2srv.admin.setup lo llaman antes de usarlo."""
    global srv, NS
    srv = servidor
    NS = servidor.ns
    return servidor


def enlazar(ns):
    """Para quien tiene el namespace pero no un Server (el panel suelto de las pruebas): usa ese dict."""
    global NS
    NS = ns
    return ns


def log(msg):
    (NS.get("log") or print)(msg)


def _write_text(path, text, newline="\n"):
    """UTF-8 sin BOM, escritura atómica (tmp + replace)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline=newline) as f:
        f.write(text)
    os.replace(tmp, path)


def _alive(c):
    return c is not None and not getattr(c, "closed", False)


def _hub():
    return NS.get("HUB")


def _sessions():
    hub = _hub()
    if hub is None:
        return []
    with hub.lock:
        return list(hub.sessions.values())


def _online(s):
    return any(_alive(c) for c in list(getattr(s, "conns", {}).values()) + [getattr(s, "lobby", None)])


# ---------------------------------------------------------------- auditoría
def bans_path():
    sp = NS.get("STATE_PATH") or os.path.join(EMU, "state.json")
    return os.path.join(os.path.dirname(os.path.abspath(sp)), "admin_vetados.json")


def audit_path():
    return os.path.join(os.path.dirname(bans_path()), "admin_auditoria.log")


def _clip(t, n=60):
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(t if t is not None else ""))[:n]


def auditar(action, target, detail="", ocultar=()):
    """Una línea por acción: fecha, acción, destino y detalle (el formato de admin_auditoria.log del panel). Nunca un token
    (`ocultar` = textos que se tapan), una contraseña ni el texto de un mensaje."""
    line = "\t".join([datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), _clip(action, 20), _clip(target, 80),
                      _clip(detail, 200)])
    for tok in ocultar:
        if tok:
            line = line.replace(tok, "[token]")
    try:
        with AUDIT_LOCK:
            os.makedirs(os.path.dirname(audit_path()), exist_ok=True)
            with open(audit_path(), "a", encoding="utf-8", newline="\n") as f:
                f.write(line + "\n")
    except OSError as e:
        log(f"[admin] no se pudo escribir la auditoría ({e!r})")


# ---------------------------------------------------------------- vetos (se aplican al iniciar sesión)
def load_bans():
    with BANS_LOCK:
        try:
            with open(bans_path(), encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}


def save_bans(d):
    with BANS_LOCK:
        p = bans_path()
        if os.path.exists(p):
            shutil.copyfile(p, p + ".bak-admin")
        _write_text(p, json.dumps(d, ensure_ascii=False, indent=1))


def is_banned(acct):
    return bool(acct) and acct in load_bans()


def _close_conn(c):
    """Cierra el socket de c: el lector de run.py sale del bucle y hace la limpieza de siempre (on_conn_closed)."""
    try:
        c.sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        c.sock.close()
    except OSError:
        pass


def install_login_hook():
    """Envuelve HUB.login (0x3003 en cualquier puerto): cuenta vetada -> se cierra la conexión sin sesión; si no,
    apunta la hora de entrada. Con la lista de vetos vacía no cambia nada para el cliente. Idempotente: si ya hay un
    envoltorio (el de yo2srv o el de un panel antiguo, marcado `_admin`) no pone otro."""
    hub = _hub()
    if hub is None or getattr(hub.login, "_admin", False):
        return False
    orig = hub.login
    acct_key = getattr(NS.get("pvp_hub"), "acct_key", None)

    def login(c, data, _orig=orig):
        acct = acct_key(data) if acct_key is not None and len(data) >= 32 else None
        if acct and callable(getattr(hub, "account_of", None)):   # sec_cuentas 07-10: la cuenta de verdad (canónica/alias)
            try:
                acct = hub.account_of(data)[0]
            except Exception:                        # noqa: BLE001
                pass
        if acct and (is_banned(acct) or (HOOKS["rechazo"] is not None and HOOKS["rechazo"](acct))):
            # 07-10 (sanciones 1:1): 0x3004 con resultado −246 -> el cliente dice 4583 «Tienes una prohibición de
            # ingreso» (FUN_006a6eb0, C) y no entra; después se cierra la conexión (antes: solo se cerraba).
            log(f"[admin] cuenta vetada {acct[:6]}… intenta entrar (puerto {getattr(c, 'port', '?')}): 0x3004 −246 y conexión cerrada")
            try:
                c.send(0x3004, NS["proto"].W().u32(0xffffff0a).u32(0).s("ESP", 4).bytes())
            except Exception as e:                   # noqa: BLE001
                log(f"[admin] 0x3004 de prohibición: {e!r}")
            c.closed = True
            t = threading.Timer(1.5, _close_conn, (c,)); t.daemon = True; t.start()
            return None
        prev = hub.sessions.get(acct) if acct else None
        was = prev is not None and any(_alive(x) for x in prev.conns.values() if x is not c)
        s = _orig(c, data)
        if s is not None and (not was or s.acct not in LOGIN_T):
            LOGIN_T[s.acct] = time.time()
        if HOOKS["flags"] is not None:
            c.admin_flags = int(HOOKS["flags"](acct) or 0) if acct else 0   # bits de sanción de 0x3004 (run.py)
        return s
    login._admin = True
    hub.login = login
    return True


instalar_veto = install_login_hook   # el nombre que usa yo2srv/arranque.py


# ---------------------------------------------------------------- cuentas y jugadores
def _account(acct):
    """(idx, ¿dueño?) de una cuenta guardada; ApiError si no existe."""
    st = NS["load_state_raw"]()
    owner = st.get("owner_acct") or NS.get("OWNER_ACCT")
    if acct == owner:
        return 0, True
    a = (st.get("accounts") or {}).get(acct)
    if a is None:
        raise ApiError("esa cuenta no está en state.json", clave="no_existe")
    return int(a.get("idx", 0)), False


def _session_of(acct):
    for s in _sessions():
        if s.acct == acct:
            return s
    return None


def nombres_de(acct, st=None):
    """Nombres de los personajes guardados de una cuenta (por hueco)."""
    st = st if st is not None else NS["load_state_raw"]()
    owner = st.get("owner_acct") or NS.get("OWNER_ACCT")
    players = st.get("players") if acct == owner else ((st.get("accounts") or {}).get(acct) or {}).get("players")
    return [name for _slot, name in sorted((players or {}).items())]


def resolver(texto, universo=None):
    """El id completo de una cuenta a partir de su id o de un prefijo de >= 6 hex (lo que enseña el lanzador). `universo` =
    las cuentas entre las que buscar (por defecto, las guardadas en state.json, la del dueño incluida). ApiError si no hay
    ninguna o hay varias."""
    t = str(texto or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{6,64}", t):
        raise ApiError("identificador de cuenta no válido", clave="cuenta_invalida")
    if universo is None:
        st = NS["load_state_raw"]()
        owner = st.get("owner_acct") or NS.get("OWNER_ACCT")
        universo = set((st.get("accounts") or {}).keys()) | ({owner} if owner else set())
    hay = sorted(a for a in universo if str(a).lower().startswith(t))
    if not hay:
        raise ApiError("esa cuenta no existe", 404, clave="no_existe")
    if len(hay) > 1:
        raise ApiError("ese identificador corto vale para varias cuentas", clave="ambigua")
    return hay[0]


def ubicacion(s):
    """Dónde está una sesión, sin textos de interfaz: {mundo: Domino|Academia|None, zona (inglés)|None, sala: tipo|None}."""
    key = getattr(s, "block", None)
    vacio = {"mundo": None, "zona": None, "sala": None}
    if key is None:
        return vacio
    try:
        port = key[0]
        r = NS["room_of_port"](port) if callable(NS.get("room_of_port")) else None
        kind = NS["ROOM_KINDS"][r[1]] if r else NS["ROOM_KINDS"][port - NS["ROOM_PORT_BASE"]]
        mundo = "Academia" if kind in NS["ACAD_KINDS"] else "Domino"
        hub = _hub()
        areas = hub.area_list(key) if hasattr(hub, "area_list") else None
        ab = getattr(s, "ablock", 0) or 0
        zona = ZONES.get(areas[ab]) if areas and ab < len(areas) else None
        return {"mundo": mundo, "zona": zona, "sala": kind}
    except Exception:                                # noqa: BLE001
        return vacio


def jugadores_basico():
    """Los jugadores conectados ahora, lo mínimo (sin IP, sin aspecto ni ficha): [{acct, nombre, dueno, estado, mundo,
    zona, sala, desde}] con estado duelo|sala|mapa|menu. Es lo que usan /publico y la API de gestión del lanzador."""
    out = []
    owner_acct = NS.get("OWNER_ACCT")
    for s in _sessions():
        if not _online(s):
            continue
        state = int(getattr(s, "state", 0) or 0)
        u = ubicacion(s)
        duel, room = 0x67 <= state <= 0x6a, state == 100
        slot = getattr(s, "slot", None)
        out.append({"acct": s.acct, "nombre": s.name or "", "dueno": bool(s.owner) or (owner_acct is not None and s.acct == owner_acct),
                    "estado": "duelo" if duel else "sala" if room else "mapa" if u["mundo"] else "menu",
                    "mundo": u["mundo"], "zona": u["zona"], "sala": u["sala"], "desde": LOGIN_T.get(s.acct),
                    "hueco": int(slot) if isinstance(slot, int) and 0 <= slot <= 3 else None})
    return out


def kick(acct, why="expulsado desde el panel"):
    s = _session_of(acct)
    if s is None or not _online(s):
        raise ApiError("ese jugador no está conectado", clave="no_conectado")
    conns = {id(c): c for c in list(s.conns.values()) + [s.lobby] if c is not None}
    for c in conns.values():
        _close_conn(c)
    LOGIN_T.pop(acct, None)
    log(f"[admin] {s.name or acct[:6]} {why} ({len(conns)} conexiones cerradas)")
    return len(conns)


def ban(acct, motivo=""):
    idx, owner = _account(acct)
    if owner:
        raise ApiError("la cuenta del dueño no se puede vetar", clave="dueno")
    names = nombres_de(acct)
    with BANS_LOCK:
        d = load_bans()
        d[acct] = {"nombres": names, "motivo": str(motivo or "")[:200],
                   "fecha": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
        save_bans(d)
    log(f"[admin] cuenta {acct[:6]}… vetada ({', '.join(names) or 'sin personajes'})")
    s = _session_of(acct)
    if s is not None and _online(s):
        kick(acct, "expulsado (vetado)")
    return d[acct]


def unban(acct):
    with BANS_LOCK:
        d = load_bans()
        if d.pop(acct, None) is None:
            raise ApiError("esa cuenta no está vetada", clave="no_vetada")
        save_bans(d)
    log(f"[admin] veto quitado a la cuenta {acct[:6]}…")


# ---------------------------------------------------------------- perfil de una cuenta (12-10: salido del panel)
def _pp():
    pp = NS.get("PP")
    if pp is None:
        raise ApiError("la persistencia (research/persistencia) no está cargada en run.py")
    return pp


def _with_profile(acct, slot, fn):
    """fn(perfil, state) dentro del cerrojo de state.json, con load_state/save_state de run.py viendo la cuenta
    acct (CTX.sess falsa en ESTE hilo). Antes de guardar: copia state.json.bak-admin. Devuelve lo que dé fn."""
    idx, owner = _account(acct)
    pp = _pp()
    ctx = NS["CTX"]
    old = getattr(ctx, "sess", None)
    with NS["STATE_LOCK"]:
        ctx.sess = types.SimpleNamespace(acct=acct, idx=idx, owner=owner)
        try:
            st = NS["load_state"]()
            if str(int(slot)) not in (st.get("players") or {}):
                raise ApiError("ese personaje no existe", clave="sin_personaje")
            p = pp.profile(st, int(slot), create=True)
            res = fn(p, st)
            _backup_rolling()
            NS["save_state"](st)
            return res
        finally:
            ctx.sess = old


def _push_pdu(acct, slot):
    """Si el jugador está conectado con ese personaje y no está en duelo: 0x4140 PLAYERDATAUPDATE con su saldo nuevo
    (lo mismo que manda la tienda tras comprar). Devuelve True si se mandó."""
    s = _session_of(acct)
    if s is None or not _alive(getattr(s, "lobby", None)) or s.slot != int(slot):
        return False
    if 0x67 <= int(getattr(s, "state", 0) or 0) <= 0x6a:
        return False
    idx, owner = _account(acct)
    ctx = NS["CTX"]; old = getattr(ctx, "sess", None)
    with NS["STATE_LOCK"]:
        ctx.sess = types.SimpleNamespace(acct=acct, idx=idx, owner=owner)
        try:
            p = _pp().profile(NS["load_state"](), int(slot), create=False)
        finally:
            ctx.sess = old
    if p is None:
        return False
    _hub()._post(s, 0x4140, _pp().player_data_update(p))
    return True


# ---------------------------------------------------------------- nivel (07-10 en el panel; 12-10 aquí, para el lanzador)
# rec["lv"] del perfil: u8 en 0x4140 PLAYERDATAUPDATE (reg+0xa4), PSELECT, PLAYERLIST 0x3012 y LIST1 0x4212
# (run.block_player). El cliente admite 0-255 (u8); no se ha encontrado un tope propio en el exe: 1-99 por decisión
# del owner. Nada del servidor lo recalcula (duelfin, persistencia y pvp_match solo tocan duelos/victorias/MP).
# El cliente filtra las salas de la puerta por este LV (research/lobby/NOTES.md «Sala de Principiantes por nivel»): LV 0-1
# solo ve las Salas de Principiantes; LV >= 2, todas menos esas. Lo lee cada vez que pinta la lista: basta el 0x4140.
LV_MIN, LV_MAX = 1, 99


def _raw_profile(st, acct, slot, owner):
    if owner:
        return (st.get("profiles") or {}).get(str(slot))
    return (((st.get("accounts") or {}).get(acct) or {}).get("profiles") or {}).get(str(slot))


def nivel_guardado(st, acct, slot, owner=None):
    """rec["lv"] del personaje (acct, hueco) en un state.json CRUDO (load_state_raw), o None si no hay perfil todavía.
    Solo lee: no crea perfiles ni toca el disco."""
    if slot is None:
        return None
    if owner is None:
        owner = acct == _owner(st)
    p = _raw_profile(st, acct, slot, owner)
    if not isinstance(p, dict):
        return None
    try:
        return int((p.get("rec") or {}).get("lv", 0) or 0)
    except (TypeError, ValueError):
        return None


def set_level(acct, slot, lv):
    """Pone rec["lv"] del personaje (acct, hueco). Si está conectado con ese personaje y fuera de un duelo, 0x4140 al
    momento (el cliente reescribe su LV); los demás lo ven en la lista del bloque al volver a cargarla."""
    if isinstance(lv, bool) or not isinstance(lv, (int, str)):
        raise ApiError("lv: un número", clave="nivel")
    try:
        lv = int(str(lv).strip())
    except ValueError:
        raise ApiError("lv: un número", clave="nivel")
    if not LV_MIN <= lv <= LV_MAX:
        raise ApiError(f"el nivel va de {LV_MIN} a {LV_MAX}", clave="nivel")

    def apply(p, st):
        r = p.setdefault("rec", {})
        old = int(r.get("lv", 0) or 0)
        r["lv"] = lv
        return {"antes": old, "ahora": lv, "nombre": p.get("name", "")}

    res = _with_profile(acct, slot, apply)
    res["aviso_al_momento"] = _push_pdu(acct, slot)
    s = _session_of(acct)
    res["en_linea"] = bool(s is not None and _online(s))
    log(f"[admin] nivel de {res['nombre']} (cuenta {acct[:6]}…, hueco {slot}): {res['antes']} -> {lv}"
        + (" (0x4140 mandado)" if res["aviso_al_momento"] else ""))
    return res


# ---------------------------------------------------------------- puntos: DP (DUELPASS) y MP (12-10, para el lanzador)
# rec["dpp"] = puntos DUELPASS del personaje (cliente: registro+0x1248 vía PSELECTRESULT/0x4140; el duelo cuesta
# DUEL_DP_COST y el cliente pide >= 1 en sala simple y >= 3 en match, research/persistencia/NOTES.md §13).
# rec["mp"] = MP de la tienda (registro+0xac). Los dos son u32 en 0x4140; el tope es nuestro (como el panel: 10 millones).
# El panel (research/admin/panel.py give) suma con apply_money: la misma regla (nunca por debajo de 0 ni por encima del tope).
POINTS_MAX = 9_999_999
POINTS = {"dp": "dpp", "mp": "mp"}            # nombre de la API -> clave de rec


def apply_money(r, money, modo="sumar", tope=POINTS_MAX):
    """Aplica money ({clave de rec: cantidad}) a rec r. modo «sumar» (negativo = quitar) o «poner». Recorta a 0..tope
    (u32 en el cliente; el panel pasa 0xffffffff, como siempre). Devuelve {clave: (antes, después)} de lo que se tocó."""
    out = {}
    for k, v in money.items():
        if v is None:
            continue
        try:
            old = int(r.get(k, 0) or 0)
        except (TypeError, ValueError):
            old = 0
        new = int(v) if modo == "poner" else old + int(v)
        r[k] = max(0, min(new, tope))
        out[k] = (old, r[k])
    return out


def puntos_guardados(st, acct, slot, owner=None):
    """{"dp": rec["dpp"], "mp": rec["mp"]} del personaje en un state.json CRUDO, o None si no hay perfil todavía. Un
    perfil sin «dpp» vale lo que dice la persistencia (PP.dp_of: LEGACY_DPP). Solo lee."""
    if slot is None:
        return None
    if owner is None:
        owner = acct == _owner(st)
    p = _raw_profile(st, acct, slot, owner)
    if not isinstance(p, dict):
        return None
    r = p.get("rec") or {}
    try:
        pp = NS.get("PP")
        dp = int(pp.dp_of(p)) if pp is not None and hasattr(pp, "dp_of") else int(r.get("dpp", 0) or 0)
        return {"dp": dp, "mp": int(r.get("mp", 0) or 0)}
    except (TypeError, ValueError):
        return None


def _cantidad(v, what, negativo):
    if v is None or v == "":
        return None
    if isinstance(v, bool) or not isinstance(v, (int, str)):
        raise ApiError(f"{what}: un número entero", clave="puntos")
    try:
        n = int(str(v).strip())
    except ValueError:
        raise ApiError(f"{what}: un número entero", clave="puntos")
    if abs(n) > POINTS_MAX or (n < 0 and not negativo):
        raise ApiError(f"{what}: de {'-' if negativo else ''}{POINTS_MAX if negativo else 0} a {POINTS_MAX}", clave="puntos")
    return n


def set_points(acct, slot, dp=None, mp=None, modo="poner"):
    """Puntos DUELPASS (dp) y/o MP del personaje (acct, hueco). modo «poner» (0..POINTS_MAX) o «sumar» (puede ser negativo;
    nunca deja menos de 0). None = no tocar. Si está conectado con ese personaje y fuera de un duelo, 0x4140 al momento
    (el cliente reescribe +0x1248 y +0xac: la ventana DUELPASS y la tienda ya lo ven)."""
    if modo not in ("poner", "sumar"):
        raise ApiError("modo: «poner» o «sumar»", clave="modo")
    neg = modo == "sumar"
    money = {"dpp": _cantidad(dp, "DP", neg), "mp": _cantidad(mp, "MP", neg)}
    if all(v is None for v in money.values()):
        raise ApiError("di cuántos DP o MP", clave="puntos")

    def apply(p, st):
        r = p.setdefault("rec", {})
        if money["dpp"] is not None and "dpp" not in r and hasattr(_pp(), "dp_of"):
            r["dpp"] = _pp().dp_of(p)                 # perfil viejo sin saldo: parte del que ya tenía (LEGACY_DPP)
        ch = apply_money(r, money, modo)
        return {"antes": {a: ch[k][0] for a, k in POINTS.items() if k in ch},
                "ahora": {"dp": int(r.get("dpp", 0) or 0), "mp": int(r.get("mp", 0) or 0)}, "nombre": p.get("name", "")}

    res = _with_profile(acct, slot, apply)
    res["aviso_al_momento"] = _push_pdu(acct, slot)
    s = _session_of(acct)
    res["en_linea"] = bool(s is not None and _online(s))
    log(f"[admin] puntos de {res['nombre']} (cuenta {acct[:6]}…, hueco {slot}, {modo}): "
        + ", ".join(f"{a.upper()} {res['antes'][a]} -> {res['ahora'][a]}" for a in res["antes"])
        + (" (0x4140 mandado)" if res["aviso_al_momento"] else ""))
    return res


# ---------------------------------------------------------------- copias de state.json
def _backup_rolling():
    store = NS.get("STORE")                       # 07-10: YO2_STORAGE=sqlite -> copia de la base (state.json se queda viejo)
    if store is not None:
        store.backup_to(store.db_path + ".bak-admin")
        return
    sp = NS["STATE_PATH"]
    if os.path.exists(sp):
        shutil.copyfile(sp, sp + ".bak-admin")


def backup_state():
    store = NS.get("STORE")                       # 07-10: YO2_STORAGE=sqlite -> copia en caliente de la base
    sp = store.db_path if store is not None else NS["STATE_PATH"]
    dst = sp + ".bak-admin-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    with NS["STATE_LOCK"]:
        if store is not None:
            store.backup_to(dst)
        else:
            shutil.copyfile(sp, dst)
    log(f"[admin] copia de state.json: {os.path.basename(dst)}")
    return os.path.basename(dst)


def _mtime(f):
    return datetime.datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M:%S")


def backups():
    sp = NS["STORE"].db_path if NS.get("STORE") is not None else NS["STATE_PATH"]
    fs = sorted((f for f in glob.glob(sp + ".bak-admin-*") if not f.endswith((".tmp", "-journal", "-wal", "-shm"))),
                reverse=True)[:20]
    out = []
    for f in fs:          # sin los ficheros de paso de una copia en marcha ni los que desaparecen entre glob y stat
        try:
            out.append({"fichero": os.path.basename(f), "kb": os.path.getsize(f) // 1024, "fecha": _mtime(f)})
        except OSError:
            continue
    return out


# ---------------------------------------------------------------- avisos
def _s16(text):
    raw = str(text).encode("latin1", "replace")[:15]
    return raw + bytes(16 - len(raw))


def chat_notice(text, channel=0):
    """0x4402 CHATNOTICE (FUN_006bb780: u8, u8 canal, u32 pid, u32 idioma, nombre16, cadena) a todos los
    conectados, de «SISTEMA» (pid 0). El cliente lo ignora en My Room. Devuelve a cuántos se mandó."""
    text = str(text or "").strip()
    if not text:
        raise ApiError("el aviso está vacío", clave="aviso_vacio")
    if int(channel) not in (0, 1):
        raise ApiError("canal: 0 general o 1 regional")
    body = text.replace("\r", " ").replace("\n", " ")[:NOTICE_MAX].encode("latin1", "replace")
    msg = struct.pack(">BBII", 0, int(channel), 0, 2) + _s16(SYSTEM_NAME) + body + bytes(1)
    hub, n = _hub(), 0
    for s in _sessions():
        if _alive(getattr(s, "lobby", None)):
            hub._post(s, 0x4402, msg); n += 1
    log(f"[admin] aviso de chat a {n} jugadores: {text[:NOTICE_MAX]!r}")
    return n


MAINTE_HOOKS = ("maintenance_notice", "admin_maintenotice", "send_maintenotice", "maintenotice")


def _mainte_fn():
    """Enganche para 0x4700 MAINTENOTICE (lo implementa otro agente en research/lobby/proposal.py): la primera
    función que exista de MAINTE_HOOKS en LP, llamada como fn(texto)."""
    lp = NS.get("LP")
    for name in MAINTE_HOOKS:
        f = getattr(lp, name, None) if lp is not None else None
        if callable(f):
            return name, f
    f = NS.get("admin_maintenotice")
    return ("admin_maintenotice", f) if callable(f) else (None, None)


def mainte_notice(text):
    name, f = _mainte_fn()
    if f is None:
        raise ApiError("el aviso de mantenimiento (0x4700) aún no está implementado en el lobby", 501)
    text = str(text or "").strip()[:NOTICE_MAX]
    if name == "maintenance_notice":      # 0x4700 lleva SEGUNDOS que faltan (el cliente pinta horas:minutos), no texto
        m = re.search(r"\d+(?:[.,]\d+)?", text)
        if not m:
            raise ApiError("escribe los minutos que faltan para el mantenimiento (p. ej. 10)", 400)
        mins = float(m.group(0).replace(",", "."))
        r = f(int(mins * 60))
        log(f"[admin] aviso de mantenimiento: {mins:g} min -> {r} cliente(s)")
        return r
    r = f(text)
    log(f"[admin] aviso de mantenimiento ({name}): {text!r}")
    return r


# ---------------------------------------------------------------- /publico
def public_status():
    """11-10 GET /publico, SIN token pero solo desde 127.0.0.1: lo poco que el lanzador pone en la ficha pública del
    servidor (launcher/sala/runpy.go EstadoPanel, NOTES-red.md §8.5). Sin cuentas, nombres ni IPs.
    «duelos» = jugadores que están ahora en un duelo (un PvP cuenta 2)."""
    on = jugadores_basico()
    hub = sys.modules.get("pvp_hub") or NS.get("pvp_hub")
    return {"jugadores": len(on), "duelos": sum(1 for x in on if x["estado"] == "duelo"),
            "perfil": os.environ.get("YO2_PERFIL") or "vanilla", "version_juego": "080912",
            "salas": ["Mundial", "Por idioma"] if NS.get("ROOM_PER_LANG") else ["Mundial"],
            "cuentas": "cerradas" if getattr(hub, "LOGIN_ESTRICTO", False) else "libre"}


# ---------------------------------------------------------------- cuentas (modo cerrado: research/cuentas/cifrado.py)
# Id de una cuenta = cifrado.account_id(cifrado.encrypt(usuario, contraseña)): los 32 B que mandaría el cliente en 0x3003, en
# forma canónica, y su sha256 (16 hex), igual que pvp_hub.login_ids. state["logins"] (clave del hub: run.HUB_KEYS, nadie más
# la pisa): {"alias": {id: cuenta}, "usuarios": {cuenta: usuario}}. «usuarios» solo tiene las cuentas creadas desde aquí (el
# usuario, nunca la contraseña). Cambiar la contraseña = quitar los alias que apuntan a la cuenta y poner id_nuevo -> cuenta:
# los datos siguen en la misma cuenta (mismo índice, mismos pid) y el id viejo deja de valer (pvp_hub.resolve_account).
# La contraseña solo se usa en memoria para calcular el id: no va al log, ni a la auditoría, ni a ningún fichero. Los errores
# tampoco la llevan. El usuario tampoco va al log (solo los 6 primeros hex de la cuenta).
def _cif():
    hub_mod = NS.get("pvp_hub")
    m = hub_mod._cifrado() if hub_mod is not None and hasattr(hub_mod, "_cifrado") else sys.modules.get("yo2_cifrado")
    if m is None:
        raise ApiError("research/cuentas/cifrado.py no está cargado", 503, clave="sin_cifrado")
    return m


def _hubmod():
    return NS.get("pvp_hub")


def _logins(st):
    lg = st.get(KEY) if isinstance(st.get(KEY), dict) else {}
    alias = lg.get("alias") if isinstance(lg.get("alias"), dict) else {}
    users = lg.get("usuarios") if isinstance(lg.get("usuarios"), dict) else {}
    return dict(alias), dict(users)


def _owner(st):
    return st.get("owner_acct") or NS.get("OWNER_ACCT")


def _exists(st, acct):
    return bool(acct) and (acct == _owner(st) or acct in (st.get("accounts") or {}))


def _online_accts():
    return {s.acct for s in _sessions() if _online(s)}


def cuentas():
    st = NS["load_state_raw"]()
    alias, users = _logins(st)
    covered = set(alias.values())
    on = _online_accts()
    owner = _owner(st)
    rows = []

    def row(acct, idx, is_owner, players):
        rows.append({"acct": acct, "corto": acct[:6], "idx": idx, "dueno": is_owner,
                     "usuario": users.get(acct), "personajes": [{"hueco": int(k), "nombre": v,
                                                                 "nivel": nivel_guardado(st, acct, k, is_owner),
                                                                 **(puntos_guardados(st, acct, k, is_owner)
                                                                    or {"dp": None, "mp": None})}
                                                                for k, v in sorted((players or {}).items())],
                     "en_linea": acct in on, "entro": LOGIN_T.get(acct), "clave_cambiada": acct in covered,
                     "vetado": is_banned(acct)})
    if owner:
        row(owner, 0, True, st.get("players"))
    for acct, a in sorted((st.get("accounts") or {}).items(), key=lambda kv: int(kv[1].get("idx", 0))):
        row(acct, int(a.get("idx", 0)), False, a.get("players"))
    hm = _hubmod()
    rej = [dict(r) for r in list(getattr(hm, "REJECTED", []) or [])][::-1]
    return {"cuentas": rows, "estricto": bool(getattr(hm, "LOGIN_ESTRICTO", False)), "rechazados": rej,
            "max": MAX_LEN, "ahora": time.time()}


def _text(v, what):
    """Usuario o contraseña: 1-16 caracteres cp1252 imprimibles. El mensaje de error NUNCA lleva el valor."""
    if not isinstance(v, str) or not v:
        raise ApiError(f"falta {what}", clave="entrada")
    if any(unicodedata.category(ch)[0] == "C" for ch in v):
        raise ApiError(f"{what}: hay caracteres de control", clave="entrada")
    try:
        b = v.encode("cp1252")
    except UnicodeEncodeError:
        raise ApiError(f"{what}: hay caracteres que el cliente no puede escribir (solo los de Windows-1252)", clave="entrada") from None
    if len(b) > MAX_LEN:
        raise ApiError(f"{what}: máximo {MAX_LEN} caracteres", clave="entrada")
    return v


def _id_of(usuario, clave):
    c = _cif()
    try:
        return c.account_id(c.encrypt(usuario, clave))
    except ValueError:
        raise ApiError("usuario o contraseña no válidos", clave="entrada") from None


def _write(fn):
    """fn(st) -> resultado, con el cerrojo de state; copia previa y guardado con las funciones de run.py."""
    with NS["STATE_LOCK"]:
        st = NS["load_state_raw"]()
        res = fn(st)
        _backup_rolling()
        NS["save_state_raw"](st)
    return res


def _set_logins(st, alias, users):
    st[KEY] = {"alias": alias, "usuarios": users}


def _taken(st, alias, new_id, acct=None):
    """¿new_id ya es la entrada de OTRA cuenta (como cuenta directa no tapada o como alias)?"""
    if new_id in alias and alias[new_id] != acct:
        return True
    return new_id != acct and _exists(st, new_id) and new_id not in set(alias.values())


def asegurar_claves_hub():
    """Que save_state no pise «logins» (un run.py viejo sin reiniciar no lo trae en HUB_KEYS)."""
    hk = NS.get("HUB_KEYS")
    if isinstance(hk, tuple) and KEY not in hk:
        NS["HUB_KEYS"] = hk + (KEY,)


def crear_cuenta(usuario, clave):
    usuario, clave = _text(usuario, "el usuario"), _text(clave, "la contraseña")
    new_id = _id_of(usuario, clave)

    def fn(st):
        alias, users = _logins(st)
        if _exists(st, new_id) or new_id in alias:
            raise ApiError("ya hay una cuenta con ese usuario y esa contraseña", clave="ya_existe")
        if usuario in users.values():
            raise ApiError("ya hay una cuenta creada desde el panel con ese usuario", clave="usuario_repetido")
        accts = dict(st.get("accounts") or {})
        mx = int(NS.get("MAX_SAVED_ACCOUNTS", 64) or 64)
        if len(accts) >= mx:
            raise ApiError(f"tope de {mx} cuentas", clave="tope")
        idx = 1 + max([int(a.get("idx", 0)) for a in accts.values()] + [0])
        accts[new_id] = {"idx": idx, "players": {}, "profiles": {}, "active_slot": 0}   # como register_account
        st["accounts"] = accts
        users[new_id] = usuario
        _set_logins(st, alias, users)
        return {"acct": new_id, "corto": new_id[:6], "idx": idx}
    with LOCK:
        res = _write(fn)
    log(f"[admin] cuenta {new_id[:6]}… creada desde el panel (índice {res['idx']})")
    return res


def cambiar_clave(acct, clave, usuario=None):
    acct = str(acct or "")
    clave = _text(clave, "la contraseña")
    with LOCK:
        st0 = NS["load_state_raw"]()
        if not _exists(st0, acct):
            raise ApiError("esa cuenta no existe", 404, clave="no_existe")
        _, users0 = _logins(st0)
        u = users0.get(acct)
        if u is None:                            # cuenta de antes del panel: el usuario no está guardado
            u = _text(usuario, "el usuario (no está guardado para esta cuenta)")
        elif usuario not in (None, "") and usuario != u:
            raise ApiError("ese no es el usuario de esta cuenta", clave="usuario_otro")
        new_id = _id_of(u, clave)

        def fn(st):
            if not _exists(st, acct):
                raise ApiError("esa cuenta no existe", 404, clave="no_existe")
            alias, users = _logins(st)
            if _taken(st, alias, new_id, acct):
                raise ApiError("ese usuario y esa contraseña ya son de otra cuenta", clave="ya_existe")
            alias = {k: v for k, v in alias.items() if v != acct}
            alias[new_id] = acct
            _set_logins(st, alias, users)
            return {"acct": acct, "corto": acct[:6], "usuario_guardado": acct in users}
        res = _write(fn)
    log(f"[admin] contraseña de la cuenta {acct[:6]}… cambiada desde el panel")
    return res


def borrar_cuenta(acct, confirmar=False):
    acct = str(acct or "")
    if confirmar is not True:
        raise ApiError("falta la confirmación", clave="sin_confirmar")
    if acct in _online_accts():
        raise ApiError("esa cuenta está conectada", 409, clave="conectada")

    def fn(st):
        if acct == _owner(st):
            raise ApiError("la cuenta del dueño no se borra", clave="dueno")
        accts = dict(st.get("accounts") or {})
        a = accts.get(acct)
        if a is None:
            raise ApiError("esa cuenta no existe", 404, clave="no_existe")
        if a.get("players"):
            raise ApiError("esa cuenta tiene personajes: solo se borran cuentas vacías", clave="con_personajes")
        accts.pop(acct)
        st["accounts"] = accts
        alias, users = _logins(st)
        alias = {k: v for k, v in alias.items() if v != acct and k != acct}
        users.pop(acct, None)
        _set_logins(st, alias, users)
        return {"acct": acct, "corto": acct[:6], "borrada": True}
    with LOCK:
        res = _write(fn)
    hub = _hub()
    if hub is not None:
        with hub.lock:
            s = hub.sessions.get(acct)
            if s is not None and not _online(s):
                hub.sessions.pop(acct, None)   # sesión vieja en memoria: si no, by_pid podría dar con ella
    LOGIN_T.pop(acct, None)
    log(f"[admin] cuenta vacía {acct[:6]}… borrada desde el panel")
    return res
