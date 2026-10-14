# -*- coding: utf-8 -*-
# Ghidra (Jython 2.7) - decompila las funciones con instrucciones que usan ciertas constantes.
#   argumentos: <salida.c> <hex1> [<hex2> ...]
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor
from ghidra.program.model.scalar import Scalar

args = getScriptArgs()
out_path = args[0]
values = set(int(a, 16) & 0xffffffff for a in args[1:])
dec = DecompInterface()
dec.openProgram(currentProgram)
mon = ConsoleTaskMonitor()
fm = currentProgram.getFunctionManager()
done = {}
for ins in currentProgram.getListing().getInstructions(True):
    for i in range(ins.getNumOperands()):
        for o in ins.getOpObjects(i):
            if isinstance(o, Scalar) and (o.getUnsignedValue() & 0xffffffff) in values:
                f = fm.getFunctionContaining(ins.getAddress())
                if f is not None and f.getEntryPoint() not in done:
                    done[f.getEntryPoint()] = (f, ins.getAddress(), o.getUnsignedValue())
out = open(out_path, "w")
for ep, (f, at, v) in sorted(done.items()):
    res = dec.decompileFunction(f, 120, mon)
    out.write("\n/* ---- %s @ %s (usa 0x%x en %s) ---- */\n" % (f.getName(), ep, v, at))
    out.write(res.getDecompiledFunction().getC() if res and res.decompileCompleted() else "/* fallo */\n")
out.close()
print("escritas %d funciones en %s" % (len(done), out_path))
