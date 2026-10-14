# -*- coding: utf-8 -*-
# Ghidra (Jython 2.7) - agente 5 (persistencia): varios trabajos en UNA sola pasada headless.
#   argumentos: <jobs.json> <dir_salida>
# jobs.json = lista de trabajos {"op": ..., "out": "fichero.c", ...}:
#   at      {"addrs": [hex...]}                         función que contiene cada dirección
#   xref    {"addrs": [hex...], "filter": "regex"?}     funciones que referencian la dirección o sus thunks
#   scalar  {"values": [hex...], "filter": "regex"?, "list_only": bool}
#   strings {"needles": [...], "decomp": bool}
#   range   {"from": hex, "to": hex}
# "filter": solo se escriben las funciones cuyo C contiene la regex.
import json, re
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor
from ghidra.program.model.scalar import Scalar

args = getScriptArgs()
jobs = json.load(open(args[0]))
outdir = args[1]
dec = DecompInterface(); dec.openProgram(currentProgram); mon = ConsoleTaskMonitor()
fm = currentProgram.getFunctionManager(); rm = currentProgram.getReferenceManager()
listing = currentProgram.getListing()
mem = currentProgram.getMemory()
cache = {}

PROLOGS = [[0x55, 0x8b, 0xec], [0x6a, 0xff, 0x68], [0x83, 0xec], [0x81, 0xec], [0x64, 0xa1, 0x00, 0x00, 0x00, 0x00]]
def byte(a): return mem.getByte(a) & 0xff
def find_start(a):
    for back in range(0, 0x3000):
        p = a.subtract(back)
        prev = byte(p.subtract(1))
        if prev not in (0xcc, 0x90, 0xc3): continue
        for pat in PROLOGS:
            if all(byte(p.add(i)) == b for i, b in enumerate(pat)): return p
    return None

def func_at(a):
    f = fm.getFunctionContaining(a)
    if f is None:
        s = find_start(a)
        if s is not None:
            disassemble(s); f = createFunction(s, None)
    return f

def C(f):
    ep = str(f.getEntryPoint())
    if ep not in cache:
        r = dec.decompileFunction(f, 180, mon)
        cache[ep] = r.getDecompiledFunction().getC() if r and r.decompileCompleted() else "/* fallo */\n"
    return cache[ep]

def emit(out, f, note, flt):
    c = C(f)
    if flt and not re.search(flt, c):
        return False
    out.write("\n/* ---- %s @ %s %s ---- */\n" % (f.getName(), f.getEntryPoint(), note))
    out.write(c)
    return True

# Pasada única por las instrucciones si hay trabajos scalar
scal_jobs = [j for j in jobs if j["op"] == "scalar"]
hits = {}
if scal_jobs:
    want = set()
    for j in scal_jobs:
        for v in j["values"]: want.add(int(v, 16) & 0xffffffff)
    for ins in listing.getInstructions(True):
        for i in range(ins.getNumOperands()):
            for o in ins.getOpObjects(i):
                if isinstance(o, Scalar):
                    v = o.getUnsignedValue() & 0xffffffff
                    if v in want:
                        hits.setdefault(v, []).append(ins.getAddress())

for j in jobs:
    out = open(outdir + "/" + j["out"], "w")
    op = j["op"]; flt = j.get("filter"); n = 0
    if op == "disasm":   # {"ranges": [[hex_desde, n_instrucciones], ...]}
        for h, cnt in j["ranges"]:
            ins = listing.getInstructionAt(toAddr(int(h, 16))) or listing.getInstructionAfter(toAddr(int(h, 16)))
            out.write("\n; ---- %s ----\n" % h)
            for k in range(cnt):
                if ins is None: break
                out.write("%s  %s\n" % (ins.getAddress(), ins))
                ins = ins.getNext(); n += 1
    elif op == "callargs":   # {"addrs": [hex...], "before": n}: n instrucciones antes de cada llamada
        for h in j["addrs"]:
            a = toAddr(int(h, 16)); targets = [a]
            f0 = fm.getFunctionAt(a)
            if f0 is not None:
                for t in f0.getFunctionThunkAddresses() or []: targets.append(t)
            for t in targets:
                for ref in rm.getReferencesTo(t):
                    fa = ref.getFromAddress(); f = fm.getFunctionContaining(fa)
                    ins = listing.getInstructionAt(fa)
                    if ins is None: continue
                    seq = []
                    p = ins
                    for k in range(j.get("before", 4)):
                        p = p.getPrevious()
                        if p is None: break
                        seq.insert(0, str(p))
                    out.write("%s %s %s | %s | %s\n" % (h, fa, f.getName() if f else "?", " ; ".join(seq), ins))
                    n += 1
    elif op == "at":
        for h in j["addrs"]:
            f = func_at(toAddr(int(h, 16)))
            if f is None:
                out.write("/* sin funcion en %s */\n" % h); continue
            n += emit(out, f, "(contiene %s)" % h, flt)
    elif op == "range":
        lo, hi = toAddr(int(j["from"], 16)), toAddr(int(j["to"], 16))
        for f in fm.getFunctions(lo, True):
            if f.getEntryPoint().compareTo(hi) > 0: break
            n += emit(out, f, "", flt)
    elif op == "xref":
        done = set()
        for h in j["addrs"]:
            a = toAddr(int(h, 16))
            targets = [a]
            f0 = fm.getFunctionAt(a)
            if f0 is not None:
                for t in f0.getFunctionThunkAddresses() or []: targets.append(t)
            for t in targets:
                for ref in rm.getReferencesTo(t):
                    f = fm.getFunctionContaining(ref.getFromAddress())
                    if f is None:
                        out.write("/* ref sin funcion desde %s */\n" % ref.getFromAddress()); continue
                    if f.getEntryPoint() in done or f.isThunk(): continue
                    done.add(f.getEntryPoint())
                    n += emit(out, f, "(ref a %s desde %s)" % (h, ref.getFromAddress()), flt)
    elif op == "scalar":
        seen = {}
        for v in j["values"]:
            for at in hits.get(int(v, 16) & 0xffffffff, []):
                f = fm.getFunctionContaining(at)
                key = str(f.getEntryPoint()) if f else "nofunc"
                seen.setdefault(key, (f, []))[1].append("%s=%s" % (v, at))
        for key in sorted(seen):
            f, where = seen[key]
            if j.get("list_only") or f is None:
                out.write("%s %s %s\n" % (key, f.getName() if f else "?", " ".join(where)))
                n += 1
            else:
                n += emit(out, f, "(usa %s)" % " ".join(where[:6]), flt)
    elif op == "strings":
        done = set()
        for data in listing.getDefinedData(True):
            try: val = data.getValue()
            except Exception: continue
            if val is None: continue
            s = unicode(val) if not isinstance(val, basestring) else val
            if not any(nd in s for nd in j["needles"]): continue
            out.write("\n/* ==== cadena %s @ %s ==== */\n" % (repr(s)[:160], data.getAddress()))
            for ref in rm.getReferencesTo(data.getAddress()):
                f = fm.getFunctionContaining(ref.getFromAddress())
                if f is None:
                    out.write("/* ref sin funcion en %s */\n" % ref.getFromAddress()); continue
                out.write("/* ref desde %s en %s @ %s */\n" % (ref.getFromAddress(), f.getName(), f.getEntryPoint()))
                if j.get("decomp") and f.getEntryPoint() not in done:
                    done.add(f.getEntryPoint())
                    n += emit(out, f, "(cadena)", flt)
    out.close()
    print("a5_jobs: %s -> %s (%d)" % (op, j["out"], n))
