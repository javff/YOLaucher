"""Lector/extractor de los contenedores KCEJYUGI (.dat) de Yu-Gi-Oh! Online 2: Duel Evolution.

Formato (deducido a mano, 2026-10-05):
  0x00  char[8]  magia "KCEJYUGI"
  0x08  u32 LE   numero de entradas
  0x0C  entradas de 268 bytes cada una:
          char[256] nombre con ruta, cada byte con los nibbles intercambiados (0x26 -> 0x62 'b')
          u32 offset absoluto en el fichero
          u32 tamano ORIGINAL (descomprimido)
          u32 tamano GUARDADO en el .dat. Si difiere del anterior, va comprimido con
              LZSS de Okumura (anillo 4096, r inicial 0xFEE, relleno a ceros, flags LSB
              primero con 1 = literal, ref = 2 bytes: off = b1 | (b2 & 0xF0) << 4,
              len = (b2 & 0x0F) + 3).
"""
import struct, sys, os, collections

MAGIC = b"KCEJYUGI"
REC = 268

# Tabla de nibbles intercambiados: bytes.translate es ~20 veces mas rapido que el bucle byte a byte (TOC de merge.dat:
# 19.445 nombres de 256 bytes; 1 s -> 0,05 s). Mismo resultado.
_SWAP = bytes(((x & 15) << 4) | (x >> 4) for x in range(256))

def swap(b: bytes) -> bytes:
    return bytes(b).translate(_SWAP)

def lzss(src: bytes, out_size: int) -> bytes:
    ring = bytearray(4096)
    r = 0xFEE
    out = bytearray()
    i = 0
    n = len(src)
    while i < n and len(out) < out_size:
        flags = src[i]; i += 1
        for bit in range(8):
            if i >= n or len(out) >= out_size:
                break
            if flags & (1 << bit):
                c = src[i]; i += 1
                out.append(c); ring[r] = c; r = (r + 1) & 0xFFF
            else:
                b1, b2 = src[i], src[i + 1]; i += 2
                off = b1 | ((b2 & 0xF0) << 4)
                for k in range((b2 & 0x0F) + 3):
                    c = ring[(off + k) & 0xFFF]
                    out.append(c); ring[r] = c; r = (r + 1) & 0xFFF
    return bytes(out[:out_size])

def read_entry(f, off, size, stored):
    f.seek(off)
    data = f.read(stored)
    return data if size == stored else lzss(data, size)

def read_toc(path):
    with open(path, "rb") as f:
        head = f.read(12)
        assert head[:8] == MAGIC, f"{path}: no es KCEJYUGI"
        n = struct.unpack_from("<I", head, 8)[0]
        raw = f.read(n * REC)
    out = []
    for i in range(n):
        r = raw[i * REC:(i + 1) * REC]
        name = swap(r[:256]).split(b"\0", 1)[0].decode("cp932", "replace")
        off, size, size2 = struct.unpack_from("<III", r, 256)
        out.append((name, off, size, size2))
    return out

def extract(path, dest, only=None):
    toc = read_toc(path)
    with open(path, "rb") as f:
        for name, off, size, size2 in toc:
            if only and not any(name.lower().startswith(o.lower()) for o in only):
                continue
            p = os.path.join(dest, name.replace("\\", os.sep))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as o:
                o.write(read_entry(f, off, size, size2))

if __name__ == "__main__":
    cmd, path = sys.argv[1], sys.argv[2]
    if cmd == "list":
        for name, off, size, size2 in read_toc(path):
            print(f"{off:>10} {size:>10} {'' if size == size2 else size2} {name}")
    elif cmd == "extract":
        extract(path, sys.argv[3], sys.argv[4:] or None)
