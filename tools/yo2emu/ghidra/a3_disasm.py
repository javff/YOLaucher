# -*- coding: utf-8 -*-
# Ghidra (Jython 2.7) - agente 3. Desensambla rangos de direcciones (instrucción por línea, con referencias).
#   argumentos: <salida.txt> <desde_hex>:<hasta_hex> [<desde>:<hasta> ...]
args = getScriptArgs()
listing = currentProgram.getListing()
out = open(args[0], "w")
for rg in args[1:]:
    lo, hi = [toAddr(int(x, 16)) for x in rg.split(":")]
    out.write("==== %s\n" % rg)
    a = lo
    while a.compareTo(hi) < 0:
        ins = listing.getInstructionAt(a)
        if ins is None:
            d = listing.getDataAt(a)
            if d is not None:
                out.write("%s  DATA %s\n" % (a, d))
                a = a.add(max(1, d.getLength()))
            else:
                a = a.add(1)
            continue
        refs = ",".join("%s" % r.getToAddress() for r in ins.getReferencesFrom())
        out.write("%s  %-40s %s\n" % (a, ins, refs))
        a = a.add(ins.getLength())
out.close()
print("hecho")
