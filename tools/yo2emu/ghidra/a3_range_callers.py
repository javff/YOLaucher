# -*- coding: utf-8 -*-
# Ghidra (Jython 2.7) - agente 3 (lobby). Descompila todas las funciones de un rango y guarda quién las llama.
#   argumentos: <salida.c> <salida.json> <desde_hex> <hasta_hex>
# salida.json: {"006cc250": {"callers": ["006d1840", ...], "size": N}, ...}  (los thunks se resuelven al destino)
import json
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor

args = getScriptArgs()
out_c, out_j, lo, hi = args[0], args[1], toAddr(int(args[2], 16)), toAddr(int(args[3], 16))
dec = DecompInterface(); dec.openProgram(currentProgram); mon = ConsoleTaskMonitor()
fm = currentProgram.getFunctionManager(); rm = currentProgram.getReferenceManager()
out = open(out_c, "w")
info = {}
for f in fm.getFunctions(lo, True):
    if f.getEntryPoint().compareTo(hi) > 0:
        break
    if f.isThunk():
        continue
    ep = f.getEntryPoint()
    addrs = [ep] + list(f.getFunctionThunkAddresses() or [])
    callers = set()
    for a in addrs:
        for ref in rm.getReferencesTo(a):
            g = fm.getFunctionContaining(ref.getFromAddress())
            if g is None:
                continue
            if g.isThunk():
                continue
            callers.add("%08x" % g.getEntryPoint().getOffset())
    info["%08x" % ep.getOffset()] = {"callers": sorted(callers), "size": f.getBody().getNumAddresses()}
    try:
        r = dec.decompileFunction(f, 60, mon)
        c = r.getDecompiledFunction().getC() if r.decompileCompleted() else "/* fallo */\n"
    except Exception as e:
        c = "/* excepcion %s */\n" % e
    out.write("\n/* ==== FUN_%08x @ %08x ==== */\n" % (ep.getOffset(), ep.getOffset()))
    out.write(c.encode("utf-8") if isinstance(c, unicode) else c)
out.close()
json.dump(info, open(out_j, "w"), indent=0, sort_keys=True)
print("funciones: %d" % len(info))
