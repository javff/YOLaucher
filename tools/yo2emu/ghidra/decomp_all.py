# -*- coding: utf-8 -*-
# Ghidra (Jython 2.7) - descompila TODAS las funciones del programa.
#   argumentos: <carpeta_salida>
# Escribe <salida>/c/<0xNN0000>.c (una por cada 64 KB de direcciones) y <salida>/index.jsonl con una línea por
# función: {"a": "006ac020", "n": "FUN_006ac020", "size": N, "calls": [...], "strs": [...], "file": "..."}.
# Pensado para grep: cada función empieza con "/* ==== FUN_xxxxxxxx @ xxxxxxxx ==== */".
import os, json
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor

out = getScriptArgs()[0]
cdir = os.path.join(out, "c")
if not os.path.isdir(cdir):
    os.makedirs(cdir)
dec = DecompInterface(); dec.openProgram(currentProgram); mon = ConsoleTaskMonitor()
fm = currentProgram.getFunctionManager(); rm = currentProgram.getReferenceManager(); listing = currentProgram.getListing()
idx = open(os.path.join(out, "index.jsonl"), "w")
files = {}
n = 0
for f in fm.getFunctions(True):
    if f.isThunk() or f.isExternal():
        continue
    a = f.getEntryPoint().getOffset()
    key = "%08x" % (a & 0xffff0000)
    if key not in files:
        files[key] = open(os.path.join(cdir, key + ".c"), "w")
    try:
        r = dec.decompileFunction(f, 60, mon)
        c = r.getDecompiledFunction().getC() if r.decompileCompleted() else "/* fallo de descompilación */\n"
    except Exception as e:
        c = "/* excepción: %s */\n" % e
    files[key].write("\n/* ==== %s @ %08x ==== */\n" % (f.getName(), a))
    files[key].write(c.encode("utf-8") if isinstance(c, unicode) else c)
    calls = sorted(set("%08x" % g.getEntryPoint().getOffset() for g in f.getCalledFunctions(mon)))
    strs = []
    for ins in listing.getInstructions(f.getBody(), True):
        for ref in ins.getReferencesFrom():
            d = listing.getDataAt(ref.getToAddress())
            if d is not None and d.hasStringValue():
                try:
                    strs.append(unicode(d.getValue())[:120])
                except Exception:
                    pass
    idx.write(json.dumps({"a": "%08x" % a, "n": f.getName(), "size": f.getBody().getNumAddresses(),
                          "calls": calls, "strs": strs[:40], "file": "c/%s.c" % key}) + "\n")
    n += 1
    if n % 500 == 0:
        print("decomp_all: %d funciones" % n)
for fh in files.values():
    fh.close()
idx.close()
print("decomp_all: hecho, %d funciones" % n)
