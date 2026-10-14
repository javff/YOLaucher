# -*- coding: utf-8 -*-
# Ghidra (Jython 2.7) - decompila la función que contiene cada dirección; si Ghidra no la tiene
# creada (el análisis completo no llegó ahí), busca hacia atrás el prólogo y la crea.
#   argumentos: <salida.c> <dir_hex> [<dir_hex> ...]
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor

PROLOGS = [[0x55, 0x8b, 0xec], [0x6a, 0xff, 0x68], [0x83, 0xec], [0x81, 0xec], [0x64, 0xa1, 0x00, 0x00, 0x00, 0x00]]
mem = currentProgram.getMemory()
def byte(a): return mem.getByte(a) & 0xff
def find_start(a):
    for back in range(0, 0x3000):
        p = a.subtract(back)
        prev = byte(p.subtract(1))
        if prev not in (0xcc, 0x90, 0xc3): continue
        for pat in PROLOGS:
            if all(byte(p.add(i)) == b for i, b in enumerate(pat)): return p
    return None

args = getScriptArgs()
dec = DecompInterface(); dec.openProgram(currentProgram); mon = ConsoleTaskMonitor()
out = open(args[0], "w")
for h in args[1:]:
    a = toAddr(int(h, 16))
    f = getFunctionContaining(a)
    if f is None:
        s = find_start(a)
        if s is not None:
            disassemble(s); f = createFunction(s, None)
    if f is None:
        out.write("/* %s: sin función */\n" % h); continue
    r = dec.decompileFunction(f, 120, mon)
    out.write("\n/* ---- %s @ %s (contiene %s) ---- */\n" % (f.getName(), f.getEntryPoint(), h))
    out.write(r.getDecompiledFunction().getC() if r.decompileCompleted() else "/* falló */\n")
out.close()
print("hecho")
