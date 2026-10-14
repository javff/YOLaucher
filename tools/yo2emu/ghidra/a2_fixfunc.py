# -*- coding: utf-8 -*-
# Ghidra (Jython 2.7) - agente 2. Para cada direccion: busca hacia atras el inicio real de la funcion
# (relleno 0xcc/0x90/ret seguido de prologo), si la funcion que Ghidra cree que la contiene empieza en
# otro sitio la crea de nuevo en el inicio real (sin guardar el proyecto: -noanalysis + no se llama a save,
# pero analyzeHeadless guarda al terminar; solo se crean funciones, no se borra nada) y la decompila.
#   argumentos: <salida.c> <hex1> [<hex2> ...]
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor
from ghidra.program.model.address import AddressSet
PROLOGS = [[0x55, 0x8b, 0xec], [0x6a, 0xff, 0x68], [0x83, 0xec], [0x81, 0xec], [0x64, 0xa1, 0, 0, 0, 0],
           [0x51], [0x53], [0x56], [0x57], [0x8b, 0x44, 0x24], [0xa1], [0x8b, 0x0d], [0x33, 0xc0]]
mem = currentProgram.getMemory(); fm = currentProgram.getFunctionManager()
def byte(a): return mem.getByte(a) & 0xff
def find_start(a):
    for back in range(0, 0x8000):
        p = a.subtract(back)
        if byte(p.subtract(1)) != 0xcc: continue
        if byte(p) == 0xcc: continue
        for pat in PROLOGS:
            if all(byte(p.add(i)) == b for i, b in enumerate(pat)): return p
    return None
args = getScriptArgs()
dec = DecompInterface(); dec.openProgram(currentProgram); mon = ConsoleTaskMonitor()
out = open(args[0], "w")
for h in args[1:]:
    force = h.startswith("!")   # !hex = la direccion ES el inicio de la funcion
    a = toAddr(int(h.lstrip("!"), 16))
    s = a if force else find_start(a)
    f = fm.getFunctionAt(s) if s else None
    if s is not None and f is None and force:
        old = fm.getFunctionContaining(s)
        if old is not None:
            try:
                old.setBody(old.getBody().subtract(AddressSet(s, old.getBody().getMaxAddress())))
            except Exception as e:
                out.write("/* recorte %s: %s */\n" % (old.getName(), e))
    if s is not None and f is None:
        old = fm.getFunctionContaining(s)
        disassemble(s)
        f = createFunction(s, None)
        if f is None and old is not None and old.getEntryPoint() != s:
            # el cuerpo de 'old' se come esta zona: recortarlo hasta s y reintentar
            try:
                body = old.getBody()
                newbody = body.subtract(AddressSet(s, old.getBody().getMaxAddress()))
                old.setBody(newbody)
                f = createFunction(s, None)
            except Exception as e:
                out.write("/* no se pudo recortar %s: %s */\n" % (old.getName(), e))
    if f is None:
        out.write("\n/* ---- %s: sin funcion (inicio %s) ---- */\n" % (h, s)); continue
    r = dec.decompileFunction(f, 180, mon)
    out.write("\n/* ---- %s @ %s (contiene %s) ---- */\n" % (f.getName(), f.getEntryPoint(), h))
    out.write(r.getDecompiledFunction().getC() if r.decompileCompleted() else "/* fallo */\n")
out.close()
