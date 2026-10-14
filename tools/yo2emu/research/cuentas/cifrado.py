"""Los 32 B de 0x3003 (YO2 Ver.080912): construir, descifrar y derivar el id de cuenta. Python puro.

Cómo los hace el cliente (research/cuentas/NOTES.md; C = visto en el código, P = probable):
  - FUN_0068efd0 (cuentas, 5731) / FUN_006cb990 (lobby, 5732)                                           C
      usuario  = CNet+0x1224 (17 B: 16 + NUL), clave = CNet+0x1235 (17 B)   (copiados con strcpy, sin mayúsculas
                 ni recorte: FUN_00642120 / FUN_006421a0)
      reto     = 16 B de 0x3002 CHALLENGE (CNet+0x534 / +0x7cc). Nuestro 0x3002 va vacío -> 16 ceros (búfer de
                 recepción recién creado y a cero en cada conexión, FUN_0068b630)                              C/P
      resumen  = MD5(usuario[:strlen] + reto + clave[:strlen])                                     (FUN_0068bbb0)
      cuerpo   = los 16 B del campo usuario TAL CUAL (con lo que quedara tras el NUL) + resumen
  - FUN_0068b1b0(pkt, 0): Blowfish estándar (16 rondas, tablas de pi) en ECB, bloques de 8 B big-endian.     C
  - Clave Blowfish: 56 B que FUN_0068b3e0 (arranque de red, WSAStartup) monta byte a byte en la pila y pasa
    a FUN_0068b180 -> FUN_00583ac0 (key schedule).                                                          C
=> el usuario se puede descifrar; la contraseña NO (va dentro de un MD5): solo se puede comprobar.

Ojo: si en la misma sesión del cliente se escribió antes un usuario más largo, los bytes tras el NUL son restos
de ese usuario (visto en los logs). El mismo usuario/clave puede dar 2 blobs distintos -> usar account_id()
(forma canónica, restos a cero), no el sha256 de los bytes crudos.
"""
import hashlib
import struct

__all__ = ["encrypt", "encrypt_partes", "decrypt", "canonical", "account_id", "legacy_id", "verify",
           "Login", "Blowfish", "KEY", "MAX_USUARIO", "MAX_CLAVE", "RETO_VACIO"]

ENCODING = "cp1252"          # cliente español (ANSI)
MAX_USUARIO = 16             # 0x3003 lleva 16 B del campo (C); con 16 caracteres no va NUL
MAX_CLAVE = 16               # campo de 17 B (C); el límite real de la caja de texto no se ha visto (P)
RETO_VACIO = bytes(16)       # 0x3002 sin cuerpo

# FUN_0068b3e0 (0x68b3e0): local_1c8..local_191, en orden.
KEY = bytes.fromhex(
    "017f5642869a6b827519efd00273145f"
    "2bc992ac239391da8627881e72940d37"
    "63fc079a14089196843bbc76280a1f39"
    "655cdd217b98183c")
assert len(KEY) == 56


# ---------------------------------------------------------------- Blowfish
def _pi_words(n):
    """n palabras de 32 bits de la parte fraccionaria de pi (las tablas iniciales de Blowfish; las del exe en
    0xc814c4 / 0xc8150c son exactamente estas). Machin con enteros: ~50 ms para 1042 palabras."""
    bits = 32 * n + 64
    one = 1 << (bits + 32)

    def atan_inv(x):
        s, t, k, x2, sign = 0, one // x, 1, x * x, 1
        while t:
            s += sign * (t // k)
            t //= x2
            k += 2
            sign = -sign
        return s
    pi = (16 * atan_inv(5) - 4 * atan_inv(239)) >> 32
    frac = pi - (3 << bits)
    return [(frac >> (bits - 32 * (i + 1))) & 0xFFFFFFFF for i in range(n)]


_INIT = None


def _init_tables():
    global _INIT
    if _INIT is None:
        w = _pi_words(18 + 1024)
        _INIT = (tuple(w[:18]), tuple(tuple(w[18 + 256 * i:18 + 256 * (i + 1)]) for i in range(4)))
    return _INIT


class Blowfish:
    def __init__(self, key):
        if not 1 <= len(key) <= 72:
            raise ValueError("clave Blowfish de 1 a 72 B")
        p0, s0 = _init_tables()
        self.P = list(p0)
        self.S = [list(s) for s in s0]
        j = 0
        for i in range(18):
            v = 0
            for _ in range(4):
                v = (v << 8) | key[j % len(key)]
                j += 1
            self.P[i] ^= v
        l = r = 0
        for i in range(0, 18, 2):
            l, r = self._enc(l, r)
            self.P[i], self.P[i + 1] = l, r
        for s in self.S:
            for i in range(0, 256, 2):
                l, r = self._enc(l, r)
                s[i], s[i + 1] = l, r

    def _f(self, x):
        s0, s1, s2, s3 = self.S
        return ((((s0[x >> 24] + s1[(x >> 16) & 0xFF]) & 0xFFFFFFFF) ^ s2[(x >> 8) & 0xFF]) + s3[x & 0xFF]) & 0xFFFFFFFF

    def _enc(self, l, r):
        P = self.P
        for i in range(16):
            l ^= P[i]
            r ^= self._f(l)
            l, r = r, l
        l, r = r, l
        return l ^ P[17], r ^ P[16]

    def _dec(self, l, r):
        P = self.P
        for i in range(17, 1, -1):
            l ^= P[i]
            r ^= self._f(l)
            l, r = r, l
        l, r = r, l
        return l ^ P[0], r ^ P[1]

    def _ecb(self, data, fn):
        if len(data) % 8:
            raise ValueError("ECB: múltiplo de 8 B")
        out = bytearray()
        for k in range(0, len(data), 8):
            out += struct.pack(">II", *fn(*struct.unpack(">II", data[k:k + 8])))
        return bytes(out)

    def encrypt_ecb(self, data):
        return self._ecb(bytes(data), self._enc)

    def decrypt_ecb(self, data):
        return self._ecb(bytes(data), self._dec)


_BF = None


def _bf():
    global _BF
    if _BF is None:
        _BF = Blowfish(KEY)
    return _BF


# ---------------------------------------------------------------- 0x3003
class Login(tuple):
    """Resultado de decrypt(): (usuario, resumen) + el campo usuario crudo (16 B, con restos tras el NUL)."""
    __slots__ = ()

    def __new__(cls, usuario, resumen, usuario_raw):
        return tuple.__new__(cls, (usuario, resumen, usuario_raw))

    usuario = property(lambda s: s[0])      # str (cp1252)
    resumen = property(lambda s: s[1])      # 16 B: MD5(usuario + reto + clave)
    usuario_raw = property(lambda s: s[2])  # 16 B tal como vinieron


def _bytes(s, nombre, maximo):
    b = s.encode(ENCODING) if isinstance(s, str) else bytes(s)
    if not b or b"\0" in b:
        raise ValueError(f"{nombre} vacío o con NUL")
    if len(b) > maximo:
        raise ValueError(f"{nombre}: máximo {maximo} B")
    return b


def resumen(usuario, clave, reto=RETO_VACIO):
    """MD5(usuario + reto + clave), como FUN_0068bbb0 sobre el búfer de FUN_0068efd0."""
    u = _bytes(usuario, "usuario", MAX_USUARIO)
    c = _bytes(clave, "clave", MAX_CLAVE)
    if len(reto) != 16:
        raise ValueError("reto de 16 B")
    return hashlib.md5(u + bytes(reto) + c).digest()


def encrypt_partes(usuario_raw, resumen_):
    """Cifra un cuerpo ya montado: 16 B de campo usuario + 16 B de resumen -> 32 B (Blowfish-ECB)."""
    usuario_raw, resumen_ = bytes(usuario_raw), bytes(resumen_)
    if len(usuario_raw) != 16 or len(resumen_) != 16:
        raise ValueError("16 B + 16 B")
    return _bf().encrypt_ecb(usuario_raw + resumen_)


def encrypt(usuario, clave, reto=RETO_VACIO):
    """Los 32 B que mandaría el cliente con este usuario y clave (forma canónica: ceros tras el NUL)."""
    u = _bytes(usuario, "usuario", MAX_USUARIO)
    return encrypt_partes(u.ljust(16, b"\0"), resumen(usuario, clave, reto))


def decrypt(blob):
    """32 B de 0x3003 -> Login(usuario, resumen, usuario_raw). La clave no se puede sacar (va en un MD5)."""
    blob = bytes(blob)
    if len(blob) < 32:
        raise ValueError("0x3003 de 32 B")
    plano = _bf().decrypt_ecb(blob[:32])
    raw = plano[:16]
    u = raw.split(b"\0", 1)[0]
    return Login(u.decode(ENCODING, errors="replace"), plano[16:], raw)


def canonical(blob):
    """El mismo blob con los restos tras el NUL del usuario a cero (lo que daría un cliente recién abierto)."""
    plano = _bf().decrypt_ecb(bytes(blob)[:32])
    u = plano[:16].split(b"\0", 1)[0]
    return encrypt_partes(u.ljust(16, b"\0"), plano[16:])


def legacy_id(blob):
    """Id de cuenta de hoy (pvp_hub.acct_key): sha256 de los bytes crudos, 16 hex."""
    return hashlib.sha256(bytes(blob)[:32]).hexdigest()[:16]


def account_id(blob):
    """Id de cuenta estable: sha256 de la forma canónica, 16 hex. Coincide con legacy_id si no hay restos."""
    return legacy_id(canonical(blob))


def verify(blob, usuario, clave, reto=RETO_VACIO):
    """¿Este 0x3003 es de este usuario y clave? (ignora los restos tras el NUL)."""
    try:
        return canonical(blob) == encrypt(usuario, clave, reto)
    except ValueError:
        return False
