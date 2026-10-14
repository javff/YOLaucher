# -*- coding: utf-8 -*-
# Ghidra (Jython 2.7) - agente 2. Lee una tabla de punteros (hasta N o hasta el primer 0) y decompila cada
# destino (creando la funcion si hace falta; si el destino es un JMP-thunk, sigue el salto).
#   argumentos: <salida.c> <dir_tabla_hex> <n>
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor
from ghidra.app.cmd.function import CreateFunctionCmd
args = getScriptArgs()
out = open(args[0], "w"); base = int(args[1], 16); n = int(args[2])
dec = DecompInterface(); dec.openProgram(currentProgram); mon = ConsoleTaskMonitor()
fm = currentProgram.getFunctionManager(); mem = currentProgram.getMemory(); listing = currentProgram.getListing()
def follow(a):
    for _ in range(3):
        if listing.getInstructionAt(a) is None: disassemble(a)
        ins = listing.getInstructionAt(a)
        if ins is not None and ins.getMnemonicString() == "JMP" and len(ins.getFlows()) == 1:
            a = ins.getFlows()[0]
        else:
            break
    return a
def ensure(a):
    f = fm.getFunctionAt(a)
    if f is None:
        old = fm.getFunctionContaining(a)
        olde = old.getEntryPoint() if old else None
        if old is not None: fm.removeFunction(olde)
        CreateFunctionCmd(a).applyTo(currentProgram, mon)
        if olde is not None: CreateFunctionCmd(olde).applyTo(currentProgram, mon)
        f = fm.getFunctionAt(a)
    return f
seen = {}
for i in range(n):
    p = mem.getInt(toAddr(base + 4 * i)) & 0xffffffff
    if p == 0:
        out.write("\n/* ==== [%d] = 0 (fin) ==== */\n" % i); break
    t = follow(toAddr(p))
    out.write("\n/* ==== [%d] %08x -> %s ==== */\n" % (i, p, t))
    if str(t) in seen:
        out.write("/* igual que [%d] */\n" % seen[str(t)]); continue
    seen[str(t)] = i
    f = ensure(t)
    if f is None: out.write("/* sin funcion */\n"); continue
    r = dec.decompileFunction(f, 120, mon)
    out.write(r.getDecompiledFunction().getC() if r.decompileCompleted() else "/* fallo */\n")
out.close()
