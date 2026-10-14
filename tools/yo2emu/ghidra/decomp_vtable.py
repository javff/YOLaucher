# -*- coding: utf-8 -*-
# Ghidra (Jython 2.7) - decompila los métodos de una tabla virtual.
#   argumentos: <salida.c> <direccion_vtable_hex> <numero_de_entradas>
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor

args = getScriptArgs()
out_path, base, n = args[0], int(args[1], 16), int(args[2])
dec = DecompInterface()
dec.openProgram(currentProgram)
mon = ConsoleTaskMonitor()
fm = currentProgram.getFunctionManager()
mem = currentProgram.getMemory()
out = open(out_path, "w")
for i in range(n):
    p = mem.getInt(toAddr(base + 4 * i)) & 0xffffffff
    f = fm.getFunctionAt(toAddr(p))
    if f is None:
        f = createFunction(toAddr(p), None)
    out.write("\n/* ==== vtable[%d] (+0x%x) -> %08x %s ==== */\n" % (i, 4 * i, p, f.getName() if f else "?"))
    if f is None:
        continue
    # Si es un thunk (jmp), seguirlo
    if f.isThunk():
        f = f.getThunkedFunction(True)
        out.write("/* thunk -> %s @ %s */\n" % (f.getName(), f.getEntryPoint()))
    res = dec.decompileFunction(f, 120, mon)
    out.write(res.getDecompiledFunction().getC() if res and res.decompileCompleted() else "/* fallo */\n")
out.close()
print("vtable escrita en " + out_path)
