# -*- coding: utf-8 -*-
# Ghidra (Jython 2.7) - saca el protocolo YG_MSG_* entero del cliente a un JSON.
#   argumentos: <salida.json>
# Recepción: funciones que usan cadenas "get msg:YG_MSG_*" (distribuidores) -> id de mensaje, nombre, manejador
#   y la secuencia de lecturas del manejador (FUN_0068xxxx: u8/u16/u32/bytes/cadena; LOOP = «mientras quede»).
# Envío: funciones con "request 'YG_MSG_*' sent" -> id (FUN_0068a5f0) y secuencia de escrituras.
import re, json
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor

out_path = getScriptArgs()[0]
dec = DecompInterface(); dec.openProgram(currentProgram); mon = ConsoleTaskMonitor()
fm = currentProgram.getFunctionManager(); rm = currentProgram.getReferenceManager()
listing = currentProgram.getListing(); mem = currentProgram.getMemory()

READ = {"0068ac10": "u8", "0068ac60": "u8", "0068acb0": "u16", "0068ad20": "u16", "0068ad90": "s32",
        "0068ae20": "u32", "0068b020": "bytes", "0068b0b0": "cstr", "0068b150": "LOOP"}
WRITE = {"0068a6d0": "u8", "0068a740": "u8", "0068a7b0": "u16", "0068a830": "u16", "0068a8b0": "s32",
         "0068a930": "u32", "0068aaf0": "bytes"}
CALL = re.compile(r"(?:thunk_)?FUN_([0-9a-f]{8})\s*\(([^;]*)\)")
ccache = {}

def real(addr_hex):
    f = fm.getFunctionAt(toAddr(int(addr_hex, 16)))
    if f is None: return None
    if f.isThunk():
        t = f.getThunkedFunction(True)
        if t is not None: return t
    return f

def ctext(f):
    k = f.getEntryPoint().getOffset()
    if k not in ccache:
        r = dec.decompileFunction(f, 120, mon)
        ccache[k] = r.getDecompiledFunction().getC() if r.decompileCompleted() else ""
    return ccache[k]

def cstring(addr_hex):
    try:
        a = toAddr(int(addr_hex, 16)); bs = []
        for i in range(200):
            b = mem.getByte(a.add(i)) & 0xff
            if b == 0: break
            bs.append(chr(b))
        return "".join(bs).strip()
    except Exception:
        return None

def ops(f, table, depth=0, seen=None):
    """Secuencia de lecturas/escrituras en orden del texto, entrando un nivel en funciones auxiliares."""
    seen = seen or set()
    seen.add("%08x" % f.getEntryPoint().getOffset())
    res = []
    body = ctext(f); body = body[body.find("{"):]          # sin la firma (si no, se cuenta a sí misma)
    for m in CALL.finditer(body):
        h, args = m.group(1), m.group(2)
        g = real(h)
        key = ("%08x" % g.getEntryPoint().getOffset()) if g else h
        if key in table:
            t = table[key]
            if t == "bytes":
                n = re.findall(r"(0x[0-9a-f]+|\b\d+\b)\s*$", args)
                t = "bytes%d" % int(n[0], 0) if n else "bytes?"
            elif t == "cstr":
                t = "cstr"
            res.append(t)
        elif g is not None and depth < 2 and key not in seen and key.startswith("006") and not key.startswith("0068"):
            seen.add(key)
            sub = ops(g, table, depth + 1, seen)
            if sub: res.append({"call": key, "ops": sub})
    return res

def funcs_with(prefix):
    found = {}
    for data in listing.getDefinedData(True):
        try: v = data.getValue()
        except Exception: continue
        if v is None: continue
        s = unicode(v) if not isinstance(v, basestring) else v
        if not s.startswith(prefix): continue
        for ref in rm.getReferencesTo(data.getAddress()):
            f = fm.getFunctionContaining(ref.getFromAddress())
            if f is not None: found[f.getEntryPoint().getOffset()] = f
    return found.values()

result = {"recv": {}, "send": {}}

# ---- recepción
ID_RE = re.compile(r"case (0x[0-9a-f]+|\d+):|==\s*(0x[0-9a-f]+)\)")
for disp in funcs_with("get msg:"):
    dname = "%08x" % disp.getEntryPoint().getOffset()
    pending, name, handler, flags = [], None, None, []
    def flush():
        for i in pending:
            e = result["recv"].setdefault("0x%04x" % i, {})
            e.update({"name": name, "handler": handler, "dispatcher": dname, "flags": list(flags)})
    for line in ctext(disp).split("\n"):
        ids = [int(a or b, 0) for a, b in ID_RE.findall(line)]
        ids = [i for i in ids if 0x1000 <= i <= 0x9fff]
        if ids:
            if name is not None or handler is not None or flags:
                flush(); pending, name, handler, flags = [], None, None, []
            pending += ids
            continue
        if not pending: continue
        if "goto " in line and name is None and handler is None:   # case que salta a otro sitio: no es este
            pending = []; continue
        m = re.search(r"s_get_msg_\w*_([0-9a-f]{8})", line)
        if m: name = (cstring(m.group(1)) or "").replace("get msg:", ""); continue
        m = re.search(r"\(param_1 \+ (0x[0-9a-f]+)\) = (\d+);", line)
        if m: flags.append([m.group(1), int(m.group(2))])
        m = CALL.search(line)
        if m and m.group(1) != "0052c360" and handler is None: handler = m.group(1)
        if "return" in line or "break;" in line:
            flush(); pending, name, handler, flags = [], None, None, []
for k, e in result["recv"].items():
    g = real(e["handler"]) if e.get("handler") else None
    e["reads"] = ops(g, READ) if g else []
    if g is not None:
        e["handler"] = "%08x" % g.getEntryPoint().getOffset()
        e["sets"] = re.findall(r"\(param_1 \+ (0x[0-9a-f]+)\) = 2;", ctext(g))

# ---- envío
for f in funcs_with("request '"):
    c = ctext(f)
    m = re.search(r"FUN_0068a5f0\([^,]+,\s*(0x[0-9a-f]+|\d+)\)", c)
    n = re.search(r"s_request__\w*_([0-9a-f]{8})", c)
    if not m: continue
    nm = cstring(n.group(1)) if n else None
    nm = re.sub(r"^request '([^']+)'.*$", r"\1", nm or "")
    flag = re.findall(r"\(param_1(?:_00)? \+ (0x[0-9a-f]+)\) = 1;", c)
    result["send"]["0x%04x" % int(m.group(1), 0)] = {"name": nm, "func": "%08x" % f.getEntryPoint().getOffset(),
                                                    "writes": ops(f, WRITE), "waitflag": flag}

json.dump(result, open(out_path, "w"), indent=1, sort_keys=True)
print("recv %d send %d" % (len(result["recv"]), len(result["send"])))
