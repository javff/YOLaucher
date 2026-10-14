# -*- coding: utf-8 -*-
# Ghidra (Jython 2.7) - agente 4: varios trabajos en una sola pasada headless.
#   argumentos: <fichero_trabajos.txt> <salida.c>
# Cada línea del fichero de trabajos:
#   at <hex>            decompila la función que contiene la dirección (la crea si no existe)
#   xref <hex>          decompila las funciones que referencian la dirección
#   callers <hex>       lista (sin decompilar) quién llama/referencia la dirección
#   str <subcadena>     cadenas definidas que contienen la subcadena + funciones que las usan (decompiladas)
#   strlist <subcadena> solo lista cadenas y direcciones de quien las usa
#   dis <hex> <n>       desensambla n instrucciones
#   bytes <hex> <n>     vuelca n bytes
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor

PROLOGS = [[0x55, 0x8b, 0xec], [0x6a, 0xff, 0x68], [0x83, 0xec], [0x81, 0xec], [0x64, 0xa1, 0x00, 0x00, 0x00, 0x00]]
mem = currentProgram.getMemory()
fm = currentProgram.getFunctionManager(); rm = currentProgram.getReferenceManager()
listing = currentProgram.getListing()
dec = DecompInterface(); dec.openProgram(currentProgram); mon = ConsoleTaskMonitor()

def byte(a): return mem.getByte(a) & 0xff
def find_start(a):
    for back in range(0, 0x3000):
        p = a.subtract(back)
        prev = byte(p.subtract(1))
        if prev not in (0xcc, 0x90, 0xc3): continue
        for pat in PROLOGS:
            if all(byte(p.add(i)) == b for i, b in enumerate(pat)): return p
    return None

def func_at(a):
    f = getFunctionContaining(a)
    if f is None:
        s = find_start(a)
        if s is not None:
            disassemble(s); f = createFunction(s, None)
    return f

def decomp(f):
    r = dec.decompileFunction(f, 180, mon)
    return r.getDecompiledFunction().getC() if r.decompileCompleted() else "/* fallo */\n"

args = getScriptArgs()
jobs = [l.strip() for l in open(args[0]).read().splitlines() if l.strip() and not l.startswith("#")]
_raw = open(args[1], "w")
class _O:
    def write(self, t):
        if isinstance(t, unicode): t = t.encode("utf-8", "replace")
        _raw.write(t)
    def flush(self): _raw.flush()
    def close(self): _raw.close()
out = _O()
done = set()
strs = None
def all_strings():
    global strs
    if strs is None:
        strs = []
        for data in listing.getDefinedData(True):
            try: val = data.getValue()
            except Exception: continue
            if val is None: continue
            try:
                s = unicode(val) if not isinstance(val, basestring) else val
            except Exception: continue
            if isinstance(s, basestring): strs.append((data.getAddress(), s))
    return strs

for job in jobs:
    p = job.split(None, 1); kind = p[0]; arg = p[1] if len(p) > 1 else ""
    out.write("\n/* ######## %s ######## */\n" % job)
    try:
        if kind in ("at", "atj"):
            a = toAddr(int(arg, 16))
            if kind == "atj":   # sigue el jmp de un thunk 0x40xxxx
                i0 = listing.getInstructionAt(a)
                if i0 is None:
                    disassemble(a); i0 = listing.getInstructionAt(a)
                if i0 is not None and i0.getMnemonicString() == "JMP" and i0.getFlows():
                    a = i0.getFlows()[0]
                    out.write("/* thunk %s -> %s */\n" % (arg, a))
            f = func_at(a)
            if f is None: out.write("/* sin función */\n"); continue
            out.write("/* ---- %s @ %s ---- */\n" % (f.getName(), f.getEntryPoint()))
            out.write(decomp(f))
        elif kind in ("xrefj", "callersj"):
            # como xref/callers, pero también a través de los thunks (jmp) que apuntan a la dirección
            a = toAddr(int(arg, 16)); targets = [a]
            for ref in rm.getReferencesTo(a):
                i0 = listing.getInstructionAt(ref.getFromAddress())
                if i0 is not None and i0.getMnemonicString() == "JMP": targets.append(ref.getFromAddress())
            for t in targets:
                for ref in rm.getReferencesTo(t):
                    i0 = listing.getInstructionAt(ref.getFromAddress())
                    if i0 is not None and i0.getMnemonicString() == "JMP" and t == a: continue
                    f = fm.getFunctionContaining(ref.getFromAddress())
                    if f is None:
                        out.write("/* ref sin función desde %s */\n" % ref.getFromAddress()); continue
                    if kind == "callersj" or f.getEntryPoint() in done:
                        out.write("/* %s @ %s desde %s (via %s) */\n" % (f.getName(), f.getEntryPoint(), ref.getFromAddress(), t)); continue
                    done.add(f.getEntryPoint())
                    out.write("\n/* ---- %s @ %s (desde %s via %s) ---- */\n" % (f.getName(), f.getEntryPoint(), ref.getFromAddress(), t))
                    out.write(decomp(f))
        elif kind in ("xref", "callers"):
            a = toAddr(int(arg, 16))
            for ref in rm.getReferencesTo(a):
                f = fm.getFunctionContaining(ref.getFromAddress())
                if f is None:
                    out.write("/* ref sin función desde %s */\n" % ref.getFromAddress()); continue
                if kind == "callers":
                    out.write("/* %s @ %s desde %s */\n" % (f.getName(), f.getEntryPoint(), ref.getFromAddress())); continue
                if f.getEntryPoint() in done:
                    out.write("/* (ya escrita) %s @ %s desde %s */\n" % (f.getName(), f.getEntryPoint(), ref.getFromAddress())); continue
                done.add(f.getEntryPoint())
                out.write("\n/* ---- %s @ %s (desde %s) ---- */\n" % (f.getName(), f.getEntryPoint(), ref.getFromAddress()))
                out.write(decomp(f))
        elif kind in ("str", "strlist"):
            for addr, s in all_strings():
                if arg not in s: continue
                out.write("\n/* == cadena %r @ %s == */\n" % (s[:100], addr))
                for ref in rm.getReferencesTo(addr):
                    f = fm.getFunctionContaining(ref.getFromAddress())
                    if f is None:
                        out.write("/* ref sin función %s */\n" % ref.getFromAddress()); continue
                    if kind == "strlist" or f.getEntryPoint() in done:
                        out.write("/* usada en %s @ %s desde %s */\n" % (f.getName(), f.getEntryPoint(), ref.getFromAddress())); continue
                    done.add(f.getEntryPoint())
                    out.write("\n/* ---- %s @ %s (desde %s) ---- */\n" % (f.getName(), f.getEntryPoint(), ref.getFromAddress()))
                    out.write(decomp(f))
        elif kind == "scalar":
            from ghidra.program.model.scalar import Scalar
            v = int(arg, 16) & 0xffffffff; seen = set()
            for ins in listing.getInstructions(True):
                for i in range(ins.getNumOperands()):
                    for o in ins.getOpObjects(i):
                        if isinstance(o, Scalar) and (o.getUnsignedValue() & 0xffffffff) == v:
                            f = fm.getFunctionContaining(ins.getAddress())
                            out.write("/* %s en %s: %s */\n" % (f.getName() if f else "?", ins.getAddress(), ins))
        elif kind == "rtti":
            # arg = nombre de clase, p.ej. CShopAvatarWindow -> vtable(s) y sus métodos
            import struct as _st
            name = ".?AV%s@@" % arg
            for addr, s in all_strings():
                if s != name: continue
                td = addr.subtract(8)
                out.write("/* TypeDescriptor %s */\n" % td)
                pat = _st.pack("<I", td.getOffset())
                a = mem.findBytes(currentProgram.getMinAddress(), pat, None, True, mon)
                while a is not None:
                    col = a.subtract(0xc)
                    pat2 = _st.pack("<I", col.getOffset())
                    b = mem.findBytes(currentProgram.getMinAddress(), pat2, None, True, mon)
                    while b is not None:
                        vt = b.add(4)
                        out.write("/* COL %s -> vtable %s */\n" % (col, vt))
                        for i in range(80):
                            p = mem.getInt(vt.add(4 * i)) & 0xffffffff
                            f = fm.getFunctionAt(toAddr(p))
                            if f is None:
                                blk = mem.getBlock(toAddr(p))
                                if blk is None or not blk.isExecute(): break
                            tgt = ""
                            ins0 = listing.getInstructionAt(toAddr(p))
                            if ins0 is None:
                                disassemble(toAddr(p)); ins0 = listing.getInstructionAt(toAddr(p))
                            if ins0 is not None and ins0.getMnemonicString() == "JMP":
                                fl = ins0.getFlows()
                                if fl: tgt = " => %s" % fl[0]
                            out.write("   [%d] +0x%x -> %08x %s%s\n" % (i, 4 * i, p, f.getName() if f else "?", tgt))
                        b = mem.findBytes(b.add(1), pat2, None, True, mon)
                    a = mem.findBytes(a.add(1), pat, None, True, mon)
        elif kind == "dis":
            h, n = arg.split(); a = toAddr(int(h, 16))
            ins = listing.getInstructionAt(a)
            if ins is None:
                disassemble(a); ins = listing.getInstructionAt(a)
            for i in range(int(n)):
                if ins is None: break
                out.write("%s  %s\n" % (ins.getAddress(), ins))
                ins = ins.getNext()
        elif kind == "bytes":
            h, n = arg.split(); a = toAddr(int(h, 16)); n = int(n)
            row = []
            for i in range(n):
                row.append("%02x" % byte(a.add(i)))
                if len(row) == 16:
                    out.write("%s  %s\n" % (a.add(i - 15), " ".join(row))); row = []
            if row: out.write("%s  %s\n" % (a.add(n - len(row)), " ".join(row)))
    except Exception as e:
        out.write("/* error: %s */\n" % e)
    out.flush()
out.close()
print("a4_multi hecho")
