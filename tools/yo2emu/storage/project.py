"""Proyección del estado (dict de state.json) a tablas de estado, y vuelta (para verificar).

Las tablas se DERIVAN del historial: se pueden borrar y reconstruir. Cada contenedor que se saca a su tabla deja en su
padre un marcador {"$tabla": nombre}; así el JSON del padre conserva qué claves había y en qué orden.

  accounts      una fila por cuenta (la del dueño = "_owner", con sus claves de la raíz); data = resto de la cuenta
  characters    una por personaje (cuenta, hueco); columnas de consulta (nombre, LV, MP, DPP, título, player_id)
                + data = el perfil sin las partes de abajo
  trunk         baúl: (cuenta, hueco, carta) -> cantidad
  decks         mazos: (cuenta, hueco, nombre) -> JSON
  npc_progress  npcbin: (cuenta, hueco, npc) -> JSON
  friends       amigos del perfil: (cuenta, hueco, posición) -> JSON
  titles        PBIN (títulos/derechos): (cuenta, hueco) -> lista JSON
  mail          lobby.mail: (posición) -> correo
  teams         equipos.teams: (id) -> equipo
  kv            el resto: "[]" = esqueleto de la raíz; '["clave"]' = valor de cada clave de la raíz sin tabla propia

El orden de claves DENTRO de baúl, mazos, npcbin, cuentas, perfiles y equipos no se guarda en las tablas (sí en el
historial): la vuelta desde tablas se compara con == (sin orden), la exportación sale del historial.
"""
import json

from .chain import ACCT_KEYS, OWNER, dumps

PARTS = {"trunk": "trunk", "decks": "decks", "npcbin": "npc_progress", "friends": "friends", "pbin": "titles"}
PART_TYPE = {"trunk": dict, "decks": dict, "npcbin": dict, "friends": list, "pbin": list}
PART_KEYCOL = {"trunk": "card", "decks": "name", "npcbin": "npc"}
CHAR_TABLES = ("characters", "trunk", "decks", "npc_progress", "friends", "titles")
ACCOUNT_TABLES = ("accounts",) + CHAR_TABLES
COLUMNS = {
    "accounts": ("acct", "is_owner", "idx", "active_slot", "data"),
    "characters": ("acct", "slot", "name", "lv", "mp", "dpp", "title", "player_id", "data"),
    "trunk": ("acct", "slot", "card", "qty", "raw"),
    "decks": ("acct", "slot", "name", "data"),
    "npc_progress": ("acct", "slot", "npc", "data"),
    "friends": ("acct", "slot", "pos", "value"),
    "titles": ("acct", "slot", "pbin"),
    "mail": ("pos", "mail_id", "from_pid", "to_pid", "data"),
    "teams": ("team_id", "data"),
    "kv": ("path", "value"),
}
INSERT = {t: "INSERT INTO %s (%s) VALUES (%s)" % (t, ", ".join(c), ", ".join("?" * len(c)))
          for t, c in COLUMNS.items()}
ROOT_PATH = "[]"


def M(t):
    return {"$tabla": t}


def _int(v):
    return v if type(v) is int else None


def kv_path(k):
    return dumps([k])


# ---------------------------------------------------------------- estado -> filas
def char_rows(acct, slot, prof, only=None):
    """Filas (tabla, tupla) de un personaje. only = conjunto de tablas a generar (None = todas)."""
    out = []
    if type(prof) is not dict:
        if only is None or "characters" in only:
            out.append(("characters", (acct, slot, None, None, None, None, None, None, dumps(prof))))
        return out
    skel = {}
    for k, v in prof.items():
        t = PARTS.get(k)
        if t is None or type(v) is not PART_TYPE[k]:
            skel[k] = v
            continue
        skel[k] = M(t)
        if only is not None and t not in only:
            continue
        if k == "trunk":
            out += [("trunk", (acct, slot, c, q if type(q) is int else None, None if type(q) is int else dumps(q)))
                    for c, q in v.items()]
        elif k in ("decks", "npcbin"):
            out += [(t, (acct, slot, n, dumps(d))) for n, d in v.items()]
        elif k == "friends":
            out += [("friends", (acct, slot, i, dumps(x))) for i, x in enumerate(v)]
        else:
            out.append(("titles", (acct, slot, dumps(v))))
    if only is None or "characters" in only:
        rec = prof.get("rec") if type(prof.get("rec")) is dict else {}
        name = prof.get("name") if type(prof.get("name")) is str else None
        out.append(("characters", (acct, slot, name, _int(rec.get("lv")), _int(rec.get("mp")), _int(rec.get("dpp")),
                                   _int(rec.get("title")), _int(rec.get("player_id")), dumps(skel))))
    return out


def account_row(acct, ad, is_owner):
    if type(ad) is not dict:
        return ("accounts", (acct, is_owner, None, None, dumps(ad)))
    skel = {k: (M("characters") if k == "profiles" and type(v) is dict else v) for k, v in ad.items()}
    return ("accounts", (acct, is_owner, _int(ad.get("idx")), _int(ad.get("active_slot")), dumps(skel)))


def account_rows(acct, ad, is_owner):
    out = [account_row(acct, ad, is_owner)]
    if type(ad) is dict and type(ad.get("profiles")) is dict:
        for slot, prof in ad["profiles"].items():
            out += char_rows(acct, slot, prof)
    return out


def owner_dict(state):
    if type(state) is not dict:
        return None
    d = {k: v for k, v in state.items() if k in ACCT_KEYS}
    return d or None


def get_account(state, acct):
    if acct == OWNER:
        return owner_dict(state)
    a = state.get("accounts") if type(state) is dict else None
    if type(a) is dict and acct in a:
        return a[acct]
    return None


def root_skeleton(state):
    skel = {}
    for k, v in state.items():
        if k in ACCT_KEYS:
            skel[k] = M("owner")
        elif k == "accounts" and type(v) is dict:
            skel[k] = M("accounts")
        else:
            skel[k] = M("kv")
    return ("kv", (ROOT_PATH, dumps(skel)))


def kv_value_rows(k, v, only_row=False):
    """Fila kv de una clave de la raíz (+ correo / equipos si es lobby / equipos)."""
    out = []
    val = v
    if k == "lobby" and type(v) is dict and type(v.get("mail")) is list:
        val = {kk: (M("mail") if kk == "mail" else x) for kk, x in v.items()}
        if not only_row:
            out += mail_rows(v["mail"])
    elif k == "equipos" and type(v) is dict and type(v.get("teams")) is dict:
        val = {kk: (M("teams") if kk == "teams" else x) for kk, x in v.items()}
        if not only_row:
            out += [("teams", (str(tid), dumps(t))) for tid, t in v["teams"].items()]
    out.append(("kv", (kv_path(k), dumps(val))))
    return out


def mail_rows(mail):
    out = []
    for i, m in enumerate(mail):
        d = m if type(m) is dict else {}
        out.append(("mail", (i, _int(d.get("id")), _int(d.get("from")), _int(d.get("to")), dumps(m))))
    return out


def project(state):
    """Todas las filas de un estado completo."""
    if type(state) is not dict:
        return [("kv", (ROOT_PATH, dumps(state)))]
    out = [root_skeleton(state)]
    od = owner_dict(state)
    if od is not None:
        out += account_rows(OWNER, od, 1)
    for k, v in state.items():
        if k in ACCT_KEYS:
            continue
        if k == "accounts" and type(v) is dict:
            for a, ad in v.items():
                out += account_rows(a, ad, 0)
        else:
            out += kv_value_rows(k, v)
    return out


# ---------------------------------------------------------------- qué refrescar tras un cambio
def scopes_for(path):
    p = list(path)
    if not p:
        return [("all",)]
    k = p[0]
    if k in ACCT_KEYS:
        sc = _acct_scopes(OWNER, p)
        return sc + ([("root",)] if len(p) == 1 else [])
    if k == "accounts":
        if len(p) == 1:
            return [("accounts_others",), ("root",)]
        if len(p) == 2:
            return [("account", p[1])]
        return _acct_scopes(p[1], p[2:])
    if len(p) == 1:
        return [("kvkey", k), ("root",)]
    if k == "lobby" and p[1] == "mail":
        return [("kvrow", k), ("mail",)]
    if k == "equipos" and p[1] == "teams":
        if len(p) == 2:
            return [("kvrow", k), ("teams",)]
        return [("team", p[2])]
    return [("kvrow", k)]


def _acct_scopes(acct, sub):
    if sub[0] != "profiles":
        return [("acctrow", acct)]
    if len(sub) == 1:
        return [("acctrow", acct), ("chars", acct)]
    slot = sub[1]
    if len(sub) == 2:
        return [("char", acct, slot)]
    f = sub[2]
    if f in PARTS:
        if len(sub) == 3:
            return [("charrow", acct, slot), ("part", acct, slot, f)]
        if f in PART_KEYCOL:
            return [("partitem", acct, slot, f, sub[3])]
        return [("part", acct, slot, f)]
    return [("charrow", acct, slot)]


def _profile(state, acct, slot):
    ad = get_account(state, acct)
    if type(ad) is dict and type(ad.get("profiles")) is dict and slot in ad["profiles"]:
        return True, ad["profiles"][slot]
    return False, None


def refresh(scope, state):
    """(borrados [(sql, params)], filas nuevas [(tabla, tupla)]) para un ámbito."""
    kind = scope[0]
    if kind == "all":
        return [("DELETE FROM %s" % t, ()) for t in COLUMNS], project(state)
    if kind == "root":
        return [("DELETE FROM kv WHERE path = ?", (ROOT_PATH,))], [root_skeleton(state)]
    if kind in ("kvkey", "kvrow"):
        k = scope[1]
        dels = [("DELETE FROM kv WHERE path = ?", (kv_path(k),))]
        if kind == "kvkey":
            if k == "lobby":
                dels.append(("DELETE FROM mail", ()))
            elif k == "equipos":
                dels.append(("DELETE FROM teams", ()))
        rows = kv_value_rows(k, state[k], only_row=(kind == "kvrow")) if k in state else []
        return dels, rows
    if kind == "mail":
        lob = state.get("lobby")
        rows = mail_rows(lob["mail"]) if type(lob) is dict and type(lob.get("mail")) is list else []
        return [("DELETE FROM mail", ())], rows
    if kind in ("teams", "team"):
        eq = state.get("equipos")
        teams = eq["teams"] if type(eq) is dict and type(eq.get("teams")) is dict else {}
        if kind == "teams":
            return [("DELETE FROM teams", ())], [("teams", (str(t), dumps(v))) for t, v in teams.items()]
        tid = scope[1]
        rows = [("teams", (str(tid), dumps(teams[tid])))] if tid in teams else []
        return [("DELETE FROM teams WHERE team_id = ?", (str(tid),))], rows
    if kind == "accounts_others":
        dels = [("DELETE FROM %s WHERE acct <> ?" % t, (OWNER,)) for t in ACCOUNT_TABLES]
        rows = []
        a = state.get("accounts")
        if type(a) is dict:
            for acct, ad in a.items():
                rows += account_rows(acct, ad, 0)
        return dels, rows
    acct = scope[1]
    if kind == "account":
        ad = get_account(state, acct)
        dels = [("DELETE FROM %s WHERE acct = ?" % t, (acct,)) for t in ACCOUNT_TABLES]
        return dels, (account_rows(acct, ad, int(acct == OWNER)) if ad is not None else [])
    if kind == "acctrow":
        ad = get_account(state, acct)
        return ([("DELETE FROM accounts WHERE acct = ?", (acct,))],
                [account_row(acct, ad, int(acct == OWNER))] if ad is not None else [])
    if kind == "chars":
        ad = get_account(state, acct)
        rows = []
        if type(ad) is dict and type(ad.get("profiles")) is dict:
            for slot, prof in ad["profiles"].items():
                rows += char_rows(acct, slot, prof)
        return [("DELETE FROM %s WHERE acct = ?" % t, (acct,)) for t in CHAR_TABLES], rows
    slot = scope[2]
    has, prof = _profile(state, acct, slot)
    if kind == "char":
        return ([("DELETE FROM %s WHERE acct = ? AND slot = ?" % t, (acct, slot)) for t in CHAR_TABLES],
                char_rows(acct, slot, prof) if has else [])
    if kind == "charrow":
        return ([("DELETE FROM characters WHERE acct = ? AND slot = ?", (acct, slot))],
                char_rows(acct, slot, prof, only={"characters"}) if has else [])
    f = scope[3]
    t = PARTS[f]
    if kind == "part":
        return ([("DELETE FROM %s WHERE acct = ? AND slot = ?" % t, (acct, slot))],
                char_rows(acct, slot, prof, only={t}) if has else [])
    if kind == "partitem":
        key = scope[4]
        col = PART_KEYCOL[f]
        dels = [("DELETE FROM %s WHERE acct = ? AND slot = ? AND %s = ?" % (t, col), (acct, slot, key))]
        part = prof.get(f) if has and type(prof) is dict else None
        if type(part) is not dict or key not in part:
            return dels, []
        sub = {f: {key: part[key]}}
        return dels, char_rows(acct, slot, sub, only={t})
    raise ValueError(f"ámbito desconocido {scope!r}")


# ---------------------------------------------------------------- tablas -> estado (verificación)
def _fill(skel, parts):
    """Sustituye los marcadores {"$tabla": t} de un dict por parts[t] (si no hay filas: vacío del tipo)."""
    if type(skel) is not dict:
        return skel
    out = {}
    for k, v in skel.items():
        if type(v) is dict and len(v) == 1 and "$tabla" in v:
            out[k] = parts.get(v["$tabla"], [] if v["$tabla"] in ("friends", "titles", "mail") else {})
        else:
            out[k] = v
    return out


def from_tables(ad):
    """Reconstruye el estado leyendo solo las tablas de estado."""
    L = json.loads
    kv = {p: v for p, v in ad.query("SELECT path, value FROM kv")}
    if ROOT_PATH not in kv:
        return {}
    root_skel = L(kv[ROOT_PATH])
    if type(root_skel) is not dict:
        return root_skel
    chars = {}
    for acct, slot, data in ad.query("SELECT acct, slot, data FROM characters"):
        chars.setdefault(acct, {})[slot] = {"skel": L(data), "parts": {}}

    def part(acct, slot, t, val):
        c = chars.setdefault(acct, {}).setdefault(slot, {"skel": None, "parts": {}})
        p = c["parts"]
        if t == "titles":
            p["titles"] = val
        elif t == "friends":
            p.setdefault("friends", []).append(val)
        else:
            p.setdefault(t, {})[val[0]] = val[1]

    for acct, slot, card, qty, raw in ad.query("SELECT acct, slot, card, qty, raw FROM trunk"):
        part(acct, slot, "trunk", (card, qty if raw is None else L(raw)))
    for t, col in (("decks", "name"), ("npc_progress", "npc")):
        for acct, slot, key, data in ad.query("SELECT acct, slot, %s, data FROM %s" % (col, t)):
            part(acct, slot, t, (key, L(data)))
    for acct, slot, pos, value in ad.query("SELECT acct, slot, pos, value FROM friends ORDER BY acct, slot, pos"):
        part(acct, slot, "friends", L(value))
    for acct, slot, pbin in ad.query("SELECT acct, slot, pbin FROM titles"):
        part(acct, slot, "titles", L(pbin))

    def build_account(acct, data):
        skel = L(data)
        profs = {}
        for slot, c in chars.get(acct, {}).items():
            profs[slot] = _fill(c["skel"], c["parts"]) if type(c["skel"]) is dict else c["skel"]
        return _fill(skel, {"characters": profs}) if type(skel) is dict else skel

    accounts = {}
    owner = None
    for acct, is_owner, data in ad.query("SELECT acct, is_owner, data FROM accounts"):
        if is_owner:
            owner = build_account(acct, data)
        else:
            accounts[acct] = build_account(acct, data)
    mail = [L(d) for _, d in ad.query("SELECT pos, data FROM mail ORDER BY pos")]
    teams = {t: L(d) for t, d in ad.query("SELECT team_id, data FROM teams")}
    out = {}
    for k, m in root_skel.items():
        t = m.get("$tabla") if type(m) is dict else None
        if t == "owner":
            out[k] = owner[k]
        elif t == "accounts":
            out[k] = accounts
        else:
            v = L(kv[kv_path(k)])
            out[k] = _fill(v, {"mail": mail, "teams": teams}) if k in ("lobby", "equipos") else v
    return out
