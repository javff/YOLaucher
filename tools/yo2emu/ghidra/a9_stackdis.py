# -*- coding: utf-8 -*-
# Ghidra (Jython) - agente 9: desensambla un rango con la profundidad de pila de Ghidra en cada instruccion.
#   argumentos: <inicio_hex> <fin_hex> <salida.txt>
from ghidra.app.cmd.function import CallDepthChangeInfo
from ghidra.util.task import ConsoleTaskMonitor

args = getScriptArgs()
a = toAddr(int(args[0], 16)); b = toAddr(int(args[1], 16))
out = open(args[2], "w")
f = getFunctionContaining(a)
info = CallDepthChangeInfo(f, ConsoleTaskMonitor())
out.write("funcion %s\n" % f)
ins = getInstructionAt(a) or getInstructionAfter(a)
while ins is not None and ins.getAddress().compareTo(b) < 0:
    d = info.getDepth(ins.getAddress())
    out.write("%s  depth=%s  %s\n" % (ins.getAddress(), d, ins))
    ins = ins.getNext()
out.close()
