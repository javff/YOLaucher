# -*- coding: utf-8 -*-
# Ghidra (Jython 2.7) - agente 2. Busca a fuerza bruta en la memoria ejecutable los CALL/JMP rel32 (E8/E9) que
# apuntan a ciertas direcciones; si el sitio es un thunk (JMP en 0x40xxxx) busca tambien quien llama al thunk.
# Tambien busca la direccion como dato (punteros en vtables / tablas).
#   argumentos: <salida.txt> <hex1> [<hex2> ...]
import struct
args = getScriptArgs()
out = open(args[0], "w")
mem = currentProgram.getMemory(); fm = currentProgram.getFunctionManager()
blocks = [b for b in mem.getBlocks() if b.isInitialized()]
data = {}
for b in blocks:
    buf = bytearray(b.getSize())
    import jarray
    jb = jarray.zeros(b.getSize(), "b")
    b.getBytes(b.getStart(), jb)
    data[b] = (b.getStart().getOffset(), bytearray([x & 0xff for x in jb]), b.isExecute())

def scan(targets):
    res = []
    for b, (base, buf, ex) in data.items():
        n = len(buf)
        if ex:
            for i in range(n - 5):
                op = buf[i]
                if op != 0xe8 and op != 0xe9: continue
                rel = struct.unpack("<i", bytes(buf[i + 1:i + 5]))[0]
                t = (base + i + 5 + rel) & 0xffffffff
                if t in targets: res.append((base + i, "call" if op == 0xe8 else "jmp", t))
        for i in range(0, n - 4):
            v = struct.unpack("<I", bytes(buf[i:i + 4]))[0]
            if v in targets: res.append((base + i, "ptr", v))
    return res

def fname(a):
    f = fm.getFunctionContaining(toAddr(a))
    return "%s@%s" % (f.getName(), f.getEntryPoint()) if f else "?"

targets = set(int(h, 16) for h in args[1:])
level = 0
while targets and level < 3:
    hits = scan(targets)
    nxt = set()
    for site, kind, t in sorted(hits):
        out.write("nivel %d: %08x %s -> %08x  en %s\n" % (level, site, kind, t, fname(site)))
        if kind == "jmp" and site < 0x00420000:
            nxt.add(site)   # thunk: buscar quien lo llama
    targets = nxt; level += 1
out.close()
