# -*- coding: utf-8 -*-
# Ghidra (Jython 2.7) - agente a1: contexto de instrucciones alrededor de cada referencia a unas direcciones.
#   argumentos: <salida.txt> <antes> <despues> <hex1> [<hex2> ...]
args = getScriptArgs()
out = open(args[0], "w")
nb = int(args[1]); na = int(args[2])
rm = currentProgram.getReferenceManager(); fm = currentProgram.getFunctionManager()
listing = currentProgram.getListing()
for h in args[3:]:
    a = toAddr(int(h, 16))
    out.write("\n######## refs a %s ########\n" % a)
    for ref in rm.getReferencesTo(a):
        frm = ref.getFromAddress()
        f = fm.getFunctionContaining(frm)
        out.write("--- %s (%s) %s\n" % (frm, f.getName() if f else "-", ref.getReferenceType()))
        ins = listing.getInstructionAt(frm)
        if ins is None:
            ins = listing.getInstructionContaining(frm)
        if ins is None:
            out.write("   (sin instruccion)\n"); continue
        seq = []
        p = ins
        for i in range(nb):
            p = p.getPrevious()
            if p is None: break
            seq.insert(0, p)
        seq.append(ins)
        n = ins
        for i in range(na):
            n = n.getNext()
            if n is None: break
            seq.append(n)
        for s in seq:
            out.write("   %s%s  %s\n" % ("=>" if s == ins else "  ", s.getAddress(), s))
out.close()
print("a1_refctx: hecho")
