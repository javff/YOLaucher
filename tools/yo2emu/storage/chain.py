"""Historial encadenado: hash de cada evento, diferencias entre dos estados (ops) y su aplicación.

Un evento = {seq, ts, acct, slot, kind, data, prev_hash, hash, sig}. data es texto JSON guardado TAL CUAL (el hash
se calcula sobre ese texto, así que verificar no depende de cómo se serialice). Tipos:
  importacion_inicial  data = {"state": <estado completo>, "origen": ...}
  resto                data = {"ops": [["set", [ruta...], valor] | ["del", [ruta...]], ...]}

Las ops conservan el orden de las claves: aplicar las ops de diff(a, b) sobre a da b con el MISMO orden (por eso
exportar a state.json sale idéntico byte a byte).
"""
import datetime
import hashlib
import json

GENESIS = "0" * 64
ACCT_KEYS = ("players", "profiles", "active_slot", "wallet", "profiles_borrados")   # = run.py
OWNER = "_owner"          # cuenta del dueño (sus claves están en la raíz de state.json)


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def now_ts():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


def event_hash(prev_hash, seq, ts, acct, slot, kind, data_text):
    """sha256 sobre los campos con prefijo de longitud (sin ambigüedad entre campos)."""
    h = hashlib.sha256()
    for part in (prev_hash, str(int(seq)), ts, acct or "", "" if slot is None else str(slot), kind, data_text):
        b = part.encode("utf-8")
        h.update(len(b).to_bytes(8, "big"))
        h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------- igualdad estricta (True != 1, orden de claves)
def same(a, b):
    ta = type(a)
    if ta is not type(b):
        return False
    if ta is dict:
        if len(a) != len(b) or list(a) != list(b):
            return False
        return all(same(a[k], b[k]) for k in a)
    if ta is list:
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def diff(old, new, path=(), ops=None):
    """Lista de ops que lleva old a new. Baja por los dict mientras el orden de claves lo permita (las que quedan
    en su sitio y las nuevas al final); si el orden cambia, o es una lista u otro valor, reemplaza el nodo entero."""
    if ops is None:
        ops = []
    if type(old) is dict and type(new) is dict:
        if old is new:
            return ops
        nk = list(new)
        kept = [k for k in old if k in new]
        if kept != nk[:len(kept)]:
            ops.append(["set", list(path), new])
            return ops
        for k in old:
            if k not in new:
                ops.append(["del", list(path) + [k]])
        for k in kept:
            diff(old[k], new[k], path + (k,), ops)
        for k in nk[len(kept):]:
            ops.append(["set", list(path) + [k], new[k]])
    elif not same(old, new):
        ops.append(["set", list(path), new])
    return ops


def apply_ops(state, ops):
    """Aplica ops sobre state (lo modifica) y devuelve el estado resultante (la raíz puede cambiar)."""
    for op in ops:
        kind, path = op[0], op[1]
        if not path:
            if kind != "set":
                raise ValueError("no se puede borrar la raíz")
            state = op[2]
            continue
        node = state
        for k in path[:-1]:
            node = node[k]
        if kind == "set":
            node[path[-1]] = op[2]
        elif kind == "del":
            del node[path[-1]]
        else:
            raise ValueError(f"op desconocida {kind!r}")
    return state


# ---------------------------------------------------------------- a quién y a qué toca un cambio
_PART_KIND = {"trunk": "baul", "decks": "mazos", "npcbin": "npcbin", "friends": "amigos", "pbin": "titulos",
              "rec": "ficha", "avatar": "aspecto", "name": "perfil"}


def locate(path):
    """(cuenta, hueco, categoría) de una ruta del estado."""
    if not path:
        return None, None, "estado"
    k = path[0]
    if k in ACCT_KEYS:
        acct, sub = OWNER, list(path)
    elif k == "accounts":
        if len(path) == 1:
            return None, None, "cuentas"
        acct, sub = path[1], list(path[2:])
        if not sub:
            return acct, None, "cuenta"
    elif k == "lobby":
        return None, None, "correo" if len(path) > 1 and path[1] == "mail" else "lobby"
    elif k == "equipos":
        return None, None, "equipos"
    elif k == "owner_acct":
        return None, None, "cuentas"
    else:
        return None, None, str(k)[:40]
    if sub[0] == "profiles":
        slot = sub[1] if len(sub) > 1 else None
        part = sub[2] if len(sub) > 2 else None
        return acct, slot, _PART_KIND.get(part, "perfil")
    if sub[0] == "wallet":
        return acct, None, "monedero"
    return acct, None, "cuenta"


def describe(ops):
    """(acct, slot, kind) del evento: comunes a todas las ops o None / «varios»."""
    locs = [locate(op[1]) for op in ops]
    accts = {a for a, _, _ in locs}
    slots = {(a, s) for a, s, _ in locs}
    kinds = {c for _, _, c in locs}
    acct = accts.pop() if len(accts) == 1 else None
    slot = next(iter(slots))[1] if len(slots) == 1 else None
    kind = kinds.pop() if len(kinds) == 1 else "varios"
    return acct, slot, kind
