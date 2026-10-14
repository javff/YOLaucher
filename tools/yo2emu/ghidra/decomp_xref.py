# -*- coding: utf-8 -*-
# Ghidra (Jython 2.7) - decompila las funciones que referencian ciertas direcciones (llamadas, saltos, datos).
#   argumentos: <salida.c> <hex1> [<hex2> ...]   (sirve para thunks: pasar la dirección del jmp)
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor
args = getScriptArgs()
dec = DecompInterface(); dec.openProgram(currentProgram); mon = ConsoleTaskMonitor()
fm = currentProgram.getFunctionManager(); rm = currentProgram.getReferenceManager()
out = open(args[0], "w"); done = set()
for h in args[1:]:
    a = toAddr(int(h, 16))
    for ref in rm.getReferencesTo(a):
        f = fm.getFunctionContaining(ref.getFromAddress())
        if f is None:
            out.write("/* ref sin función desde %s */\n" % ref.getFromAddress()); continue
        if f.getEntryPoint() in done: continue
        done.add(f.getEntryPoint())
        r = dec.decompileFunction(f, 120, mon)
        out.write("\n/* ---- %s @ %s (ref a %s desde %s) ---- */\n" % (f.getName(), f.getEntryPoint(), a, ref.getFromAddress()))
        out.write(r.getDecompiledFunction().getC() if r.decompileCompleted() else "/* fallo */\n")
out.close()
