# -*- coding: utf-8 -*-
# Ghidra (Jython 2.7) - postScript de analyzeHeadless.
# Decompila las funciones que usan ciertas cadenas y las escribe en un fichero.
#   argumentos: <salida.c> <cadena1> [<cadena2> ...]
# Ejemplo: decomp_refs.py out.c root.txt gateServer
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor

args = getScriptArgs()
out_path = args[0]
needles = list(args[1:])

listing = currentProgram.getListing()
refman = currentProgram.getReferenceManager()
fm = currentProgram.getFunctionManager()

dec = DecompInterface()
dec.openProgram(currentProgram)
mon = ConsoleTaskMonitor()

done = set()
out = open(out_path, "w")
for data in listing.getDefinedData(True):
    try:
        val = data.getValue()
    except Exception:
        continue
    if val is None:
        continue
    s = unicode(val) if not isinstance(val, basestring) else val
    if not any(n in s for n in needles):
        continue
    out.write("\n/* ==== cadena %s @ %s ==== */\n" % (repr(s)[:120], data.getAddress()))
    for ref in refman.getReferencesTo(data.getAddress()):
        f = fm.getFunctionContaining(ref.getFromAddress())
        if f is None:
            out.write("/* ref sin funcion en %s */\n" % ref.getFromAddress())
            continue
        if f.getEntryPoint() in done:
            out.write("/* (ya escrita) %s @ %s */\n" % (f.getName(), f.getEntryPoint()))
            continue
        done.add(f.getEntryPoint())
        res = dec.decompileFunction(f, 120, mon)
        out.write("\n/* ---- %s @ %s (ref desde %s) ---- */\n" % (f.getName(), f.getEntryPoint(), ref.getFromAddress()))
        if res and res.decompileCompleted():
            out.write(res.getDecompiledFunction().getC())
        else:
            out.write("/* no se pudo decompilar */\n")
out.close()
print("escrito " + out_path)
