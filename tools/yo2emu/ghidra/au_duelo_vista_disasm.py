# -*- coding: utf-8 -*-
# Ghidra (Jython 2.7) - auditoria duelo_vista: copia de a2_disasm.py (desensambla N instrucciones desde cada direccion)
# y, si la primera es un jmp/call directo, dice a donde va.   argumentos: <salida.txt> <n> <hex1> [<hex2> ...]
args = getScriptArgs()
out = open(args[0], "w"); n = int(args[1])
listing = currentProgram.getListing()
for h in args[2:]:
    a = toAddr(int(h, 16))
    if listing.getInstructionAt(a) is None:
        disassemble(a)
    out.write("== %s\n" % h)
    ins = listing.getInstructionAt(a)
    k = 0
    while ins is not None and k < n:
        flows = ",".join(str(x) for x in ins.getFlows())
        out.write("  %s  %-40s %s\n" % (ins.getAddress(), ins.toString(), ("-> " + flows) if flows else ""))
        ins = ins.getNext(); k += 1
out.close()
