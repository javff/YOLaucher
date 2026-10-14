# -*- coding: utf-8 -*-
# Ghidra (Jython 2.7) - decompila todas las funciones entre dos direcciones (hex) a un fichero.
#   argumentos: <salida.c> <desde> <hasta>
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor

args = getScriptArgs()
out_path, lo, hi = args[0], toAddr(int(args[1], 16)), toAddr(int(args[2], 16))
dec = DecompInterface()
dec.openProgram(currentProgram)
mon = ConsoleTaskMonitor()
out = open(out_path, "w")
n = 0
for f in currentProgram.getFunctionManager().getFunctions(lo, True):
    if f.getEntryPoint().compareTo(hi) > 0:
        break
    res = dec.decompileFunction(f, 120, mon)
    out.write("\n/* ---- %s @ %s ---- */\n" % (f.getName(), f.getEntryPoint()))
    out.write(res.getDecompiledFunction().getC() if res and res.decompileCompleted() else "/* fallo */\n")
    n += 1
out.close()
print("escritas %d funciones en %s" % (n, out_path))
