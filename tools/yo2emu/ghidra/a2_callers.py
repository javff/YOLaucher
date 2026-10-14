# -*- coding: utf-8 -*-
# Ghidra (Jython 2.7) - agente 2. Decompila quienes llaman a una funcion, siguiendo thunks (jmp) y
# opcionalmente subiendo N niveles.   argumentos: <salida.c> <niveles> <hex1> [<hex2> ...]
# Ademas de la descompilacion, escribe la lista "llamante <- llamado @ sitio".
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor
args = getScriptArgs()
out = open(args[0], "w"); depth = int(args[1])
dec = DecompInterface(); dec.openProgram(currentProgram); mon = ConsoleTaskMonitor()
fm = currentProgram.getFunctionManager(); rm = currentProgram.getReferenceManager()

def targets(a):
    """la funcion en a + todos sus thunks"""
    res = [a]
    f = fm.getFunctionAt(a)
    if f is not None:
        for t in f.getFunctionThunkAddresses() or []:
            res.append(t)
    # thunks creados como funciones 'thunk_FUN_x' que hacen jmp a a
    for ref in rm.getReferencesTo(a):
        g = fm.getFunctionContaining(ref.getFromAddress())
        if g is not None and g.isThunk() and g.getEntryPoint() not in res:
            res.append(g.getEntryPoint())
    return res

done = set(); edges = []; todo = [(toAddr(int(h, 16)), 0) for h in args[2:]]
decomp = []
while todo:
    a, lvl = todo.pop(0)
    for t in targets(a):
        for ref in rm.getReferencesTo(t):
            g = fm.getFunctionContaining(ref.getFromAddress())
            if g is None:
                edges.append("?? <- %s @ %s" % (a, ref.getFromAddress())); continue
            if g.isThunk():
                continue
            edges.append("%s <- %s @ %s (%s)" % (g.getEntryPoint(), a, ref.getFromAddress(), ref.getReferenceType()))
            if g.getEntryPoint() in done: continue
            done.add(g.getEntryPoint()); decomp.append((g, a, lvl))
            if lvl + 1 < depth: todo.append((g.getEntryPoint(), lvl + 1))
out.write("/* aristas:\n" + "\n".join(edges) + "\n*/\n")
for g, a, lvl in decomp:
    r = dec.decompileFunction(g, 120, mon)
    out.write("\n/* ---- %s @ %s (llama a %s, nivel %d) ---- */\n" % (g.getName(), g.getEntryPoint(), a, lvl))
    out.write(r.getDecompiledFunction().getC() if r.decompileCompleted() else "/* fallo */\n")
out.close()
