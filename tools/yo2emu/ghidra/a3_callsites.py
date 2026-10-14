# -*- coding: utf-8 -*-
# Ghidra (Jython 2.7) - agente 3. Para cada llamada a las funciones dadas (incluidos sus thunks), escribe la
# función que llama, la dirección y las N instrucciones anteriores y M posteriores (para ver los argumentos
# empujados con push y qué se hace con el resultado).
#   argumentos: <salida.txt> <antes> <despues> <hex1> [<hex2> ...]
args = getScriptArgs()
out_path, nb, na = args[0], int(args[1]), int(args[2])
fm = currentProgram.getFunctionManager(); rm = currentProgram.getReferenceManager(); listing = currentProgram.getListing()
out = open(out_path, "w")
for h in args[3:]:
    tgt = toAddr(int(h, 16))
    f = fm.getFunctionAt(tgt)
    addrs = [tgt]
    if f is not None:
        addrs += list(f.getFunctionThunkAddresses() or [])
    for a in addrs:
        for ref in rm.getReferencesTo(a):
            if not ref.getReferenceType().isCall() and not ref.getReferenceType().isJump():
                continue
            src = ref.getFromAddress()
            g = fm.getFunctionContaining(src)
            if g is not None and g.isThunk():
                continue
            out.write("== %s llamada desde %s en %s\n" % (h, g.getEntryPoint() if g else "?", src))
            ins = listing.getInstructionAt(src)
            prev = []
            p = ins
            for i in range(nb):
                p = p.getPrevious() if p is not None else None
                if p is None:
                    break
                prev.append(p)
            for p in reversed(prev):
                out.write("   %s  %s\n" % (p.getAddress(), p))
            out.write(" > %s  %s\n" % (ins.getAddress(), ins))
            n = ins
            for i in range(na):
                n = n.getNext() if n is not None else None
                if n is None:
                    break
                out.write("   %s  %s\n" % (n.getAddress(), n))
out.close()
print("hecho")
