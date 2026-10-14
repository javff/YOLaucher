"""Protocolo TCP del cliente YO2 (2008-11), sacado de yo2.exe con Ghidra (sesión 3, 2026-10-05).

Mensaje = cabecera de 24 bytes + datos (máx. 1024), todo en XOR con la tabla de 4 bytes KEY:
    0-1   id del mensaje         u16 big-endian
    2-3   largo de los datos     u16 big-endian
    4-7   parámetro              u32 big-endian (el saludo lleva 1)
    8-23  MD5(cabecera[0:8] + datos)
Los campos de los datos también van en big-endian; las cadenas, con largo fijo y ceros al final.
"""
import hashlib
import struct

KEY = bytes.fromhex("5aa55a55")
HDR = 24


def xor(b: bytes, start: int = 0) -> bytes:
    return bytes(x ^ KEY[(i + start) % 4] for i, x in enumerate(b))


def pack(msg_id: int, data: bytes = b"", param: int = 0) -> bytes:
    head = struct.pack(">HHI", msg_id, len(data), param)
    return xor(head + hashlib.md5(head + data).digest() + data)


class Reader:
    """Va sacando mensajes completos de lo que llega por el socket."""

    def __init__(self):
        self.buf = b""

    def feed(self, chunk: bytes):
        self.buf += chunk
        out = []
        while len(self.buf) >= HDR:
            head = xor(self.buf[:HDR])
            msg_id, n, param = struct.unpack(">HHI", head[:8])
            if len(self.buf) < HDR + n:
                break
            data = xor(self.buf[HDR:HDR + n], HDR)
            ok = hashlib.md5(head[:8] + data).digest() == head[8:24]
            out.append((msg_id, param, data, ok))
            self.buf = self.buf[HDR + n:]
        return out


class W:
    """Escritor de campos (big-endian, cadenas de largo fijo)."""

    def __init__(self):
        self.b = bytearray()

    def u8(self, v):
        self.b += struct.pack(">B", v); return self

    def u16(self, v):
        self.b += struct.pack(">H", v); return self

    def u32(self, v):
        self.b += struct.pack(">I", v & 0xffffffff); return self

    def s(self, text, size):
        raw = text.encode("latin1")[:size]
        self.b += raw + b"\0" * (size - len(raw)); return self

    def bytes(self):
        return bytes(self.b)


def server_entry(sid, kind, name, ip, port, extra=0, b1=0, b2=0, b3=0):
    """Entrada de la lista de servidores (0x2003), 48 bytes. Orden de campos de FUN_0069a6a0.
    kind (2º u32): 1 = cuentas; 3 = lobby. El mundo (FUN_006d3a50) busca kind 3 con b1=0, b2=12 y b3 = área 0-7
    («MyRoom Lobby Area [%d]»); si no hay ninguna, se rinde y el cliente vuelve al título."""
    return (W().u32(sid).u32(kind).s(name, 16).s(ip, 15)
            .u16(port).u16(extra).u8(b1).u8(b2).u8(b3).u16(0).bytes())
