# -*- coding: utf-8 -*-
# Ghidra (Jython 2.7) - agente 2. Para cada direccion (que ES un inicio de funcion conocido por un CALL):
# si otra funcion la "contiene", la borra y vuelve a crear las dos (la vieja y la nueva) y decompila la nueva.
# Solo toca la copia ghidra_a2.   argumentos: <salida.c> <hex1> [<hex2> ...]
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor
from ghidra.app.cmd.function import CreateFunctionCmd
args = getScriptArgs()
dec = DecompInterface(); dec.openProgram(currentProgram); mon = ConsoleTaskMonitor()
fm = currentProgram.getFunctionManager(); listing = currentProgram.getListing()
out = open(args[0], "w")
for h in args[1:]:
    a = toAddr(int(h, 16))
    f = fm.getFunctionAt(a)
    if f is None:
        old = fm.getFunctionContaining(a)
        olde = old.getEntryPoint() if old else None
        if old is not None:
            fm.removeFunction(olde)
        if listing.getInstructionAt(a) is None:
            disassemble(a)
        CreateFunctionCmd(a).applyTo(currentProgram, mon)
        if olde is not None:
            CreateFunctionCmd(olde).applyTo(currentProgram, mon)
        f = fm.getFunctionAt(a)
    if f is None:
        out.write("\n/* ---- %s: no se pudo crear ---- */\n" % h); continue
    r = dec.decompileFunction(f, 180, mon)
    out.write("\n/* ---- %s @ %s ---- */\n" % (f.getName(), f.getEntryPoint()))
    out.write(r.getDecompiledFunction().getC() if r.decompileCompleted() else "/* fallo */\n")
out.close()
