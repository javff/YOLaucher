# -*- coding: utf-8 -*-
# Ghidra (Jython 2.7) - agente a1: varias consultas en un solo arranque headless.
#   argumentos: <salida.txt> <orden> [<orden> ...]
#   ordenes:
#     dis:<desde>:<hasta>   desensamblado (con referencias) entre dos direcciones hex
#     dec:<dir>             decompila la funcion que contiene <dir> (la crea si hace falta)
#     xref:<dir>            lista referencias a <dir> (desde, tipo, funcion) y decompila esas funciones
#     xrefl:<dir>           solo lista referencias (sin decompilar)
#     callers:<dir>         lista llamadores (recursivo 1 nivel) de la funcion en <dir>
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor

PROLOGS = [[0x55, 0x8b, 0xec], [0x6a, 0xff, 0x68], [0x83, 0xec], [0x81, 0xec], [0x64, 0xa1, 0x00, 0x00, 0x00, 0x00]]
mem = currentProgram.getMemory()
fm = currentProgram.getFunctionManager(); rm = currentProgram.getReferenceManager()
listing = currentProgram.getListing()
dec = DecompInterface(); dec.openProgram(currentProgram); mon = ConsoleTaskMonitor()

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

def decomp(f):
    r = dec.decompileFunction(f, 180, mon)
    return r.getDecompiledFunction().getC() if r.decompileCompleted() else "/* fallo */\n"

args = getScriptArgs()
out = open(args[0], "w")
for cmd in args[1:]:
    parts = cmd.split(":")
    op = parts[0]
    out.write("\n\n######## %s ########\n" % cmd)
    if op == "dis":
        lo = toAddr(int(parts[1], 16)); hi = toAddr(int(parts[2], 16))
        disassemble(lo)
        it = listing.getInstructions(lo, True)
        while it.hasNext():
            ins = it.next()
            if ins.getAddress().compareTo(hi) > 0: break
            refs = [str(r.getToAddress()) for r in ins.getReferencesFrom()]
            out.write("%s  %-40s %s\n" % (ins.getAddress(), ins.toString(), (" ; -> " + ",".join(refs)) if refs else ""))
    elif op == "mk":
        a = toAddr(int(parts[1], 16))
        disassemble(a)
        f = fm.getFunctionAt(a)
        if f is None:
            g = fm.getFunctionContaining(a)
            if g is not None and g.getEntryPoint() != a:
                out.write("/* %s estaba dentro de %s: se reajusta */\n" % (a, g.getName()))
            f = createFunction(a, None)
        if f is None: out.write("/* no se pudo crear */\n"); continue
        out.write("/* ---- %s @ %s ---- */\n" % (f.getName(), f.getEntryPoint()))
        out.write(decomp(f))
    elif op == "dec":
        f = func_at(toAddr(int(parts[1], 16)))
        if f is None: out.write("/* sin funcion */\n"); continue
        out.write("/* ---- %s @ %s ---- */\n" % (f.getName(), f.getEntryPoint()))
        out.write(decomp(f))
    elif op in ("xref", "xrefl"):
        a = toAddr(int(parts[1], 16)); done = set()
        for ref in rm.getReferencesTo(a):
            f = fm.getFunctionContaining(ref.getFromAddress())
            out.write("ref desde %s tipo %s func %s\n" % (ref.getFromAddress(), ref.getReferenceType(), f.getName() + "@" + str(f.getEntryPoint()) if f else "-"))
            if op == "xref" and f is not None and f.getEntryPoint() not in done:
                done.add(f.getEntryPoint())
                out.write("/* ---- %s @ %s ---- */\n" % (f.getName(), f.getEntryPoint()))
                out.write(decomp(f))
    elif op == "callers":
        f = func_at(toAddr(int(parts[1], 16)))
        if f is None: out.write("sin funcion\n"); continue
        targets = [f.getEntryPoint()] + list(f.getFunctionThunkAddresses() or [])
        for t in targets:
            for ref in rm.getReferencesTo(t):
                g = fm.getFunctionContaining(ref.getFromAddress())
                out.write("llama %s desde %s (%s)\n" % (t, ref.getFromAddress(), g.getName() + "@" + str(g.getEntryPoint()) if g else "-"))
                if g is not None and g.isThunk():
                    for r2 in rm.getReferencesTo(g.getEntryPoint()):
                        h = fm.getFunctionContaining(r2.getFromAddress())
                        out.write("   via thunk: desde %s (%s)\n" % (r2.getFromAddress(), h.getName() + "@" + str(h.getEntryPoint()) if h else "-"))
out.close()
print("a1_multi: hecho")
