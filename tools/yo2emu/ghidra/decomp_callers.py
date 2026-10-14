# -*- coding: utf-8 -*-
# Ghidra (Jython 2.7) - decompila las funciones que llaman a ciertos símbolos (p. ej. importaciones).
#   argumentos: <salida.c> <simbolo1> [<simbolo2> ...]
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor

args = getScriptArgs()
out_path, names = args[0], list(args[1:])
dec = DecompInterface()
dec.openProgram(currentProgram)
mon = ConsoleTaskMonitor()
fm = currentProgram.getFunctionManager()
refman = currentProgram.getReferenceManager()
st = currentProgram.getSymbolTable()
done = set()
out = open(out_path, "w")
for name in names:
    for sym in st.getSymbols(name):
        addrs = [sym.getAddress()]
        # Las importaciones suelen llamarse a través de un thunk: incluir quienes llaman al thunk
        f = fm.getFunctionAt(sym.getAddress())
        if f is not None:
            for t in f.getFunctionThunkAddresses() or []:
                addrs.append(t)
        for a in addrs:
            for ref in refman.getReferencesTo(a):
                caller = fm.getFunctionContaining(ref.getFromAddress())
                if caller is None or caller.getEntryPoint() in done:
                    continue
                done.add(caller.getEntryPoint())
                res = dec.decompileFunction(caller, 120, mon)
                out.write("\n/* ---- %s @ %s (llama a %s desde %s) ---- */\n" % (caller.getName(), caller.getEntryPoint(), name, ref.getFromAddress()))
                out.write(res.getDecompiledFunction().getC() if res and res.decompileCompleted() else "/* fallo */\n")
out.close()
print("escritas %d funciones en %s" % (len(done), out_path))
