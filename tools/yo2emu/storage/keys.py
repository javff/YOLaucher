"""Clave de cifrado de la base (SQLCipher) protegida con la DPAPI de Windows.

La clave son 32 bytes aleatorios. En disco solo queda el blob de CryptProtectData: solo el mismo usuario de Windows
en el mismo PC puede descifrarlo. Ruta por defecto: %LOCALAPPDATA%\\YO2Online\\db.key (YO2_DB_KEYFILE la cambia).

OJO (MSIX): los procesos lanzados desde una app de escritorio empaquetada escriben AppData en una caché privada del
paquete (AppData\\Local\\Packages\\<app>_*\\LocalCache\\Local\\...). Por eso, al LEER, si la ruta normal no existe se
mira también esa caché; al CREAR nunca se inventa una clave nueva si la base ya existe cifrada.
"""
import ctypes
import glob
import os
import secrets
import sys

MAGIC = b"YO2DBKEY1\n"
ENTROPY = b"YO2Online-db-v1"
CRYPTPROTECT_UI_FORBIDDEN = 0x1


class KeyError_(RuntimeError):
    pass


def default_keyfile():
    if os.environ.get("YO2_DB_KEYFILE"):
        return os.path.abspath(os.environ["YO2_DB_KEYFILE"])
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "YO2Online", "db.key")


def candidate_keyfiles(path=None):
    """La ruta pedida y, si es la de por defecto, su copia en la caché virtualizada de un paquete MSIX."""
    path = path or default_keyfile()
    out = [path]
    base = os.environ.get("LOCALAPPDATA")
    if base and os.path.normcase(os.path.dirname(os.path.dirname(path))) == os.path.normcase(base):
        rel = os.path.relpath(path, base)
        out += sorted(glob.glob(os.path.join(base, "Packages", "*", "LocalCache", "Local", rel)))
    return out


# ---------------------------------------------------------------- DPAPI por ctypes
if sys.platform == "win32":
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _P = ctypes.POINTER(DATA_BLOB)
    _crypt32.CryptProtectData.argtypes = [_P, wintypes.LPCWSTR, _P, ctypes.c_void_p, ctypes.c_void_p,
                                          wintypes.DWORD, _P]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [_P, ctypes.POINTER(wintypes.LPWSTR), _P, ctypes.c_void_p,
                                            ctypes.c_void_p, wintypes.DWORD, _P]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p

    def _blob(data):
        buf = ctypes.create_string_buffer(data, len(data))
        return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    def _take(out):
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))

    def dpapi_protect(data):
        inb, _b1 = _blob(data)
        ent, _b2 = _blob(ENTROPY)
        out = DATA_BLOB()
        if not _crypt32.CryptProtectData(ctypes.byref(inb), "YO2Online db", ctypes.byref(ent), None, None,
                                         CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
            raise ctypes.WinError(ctypes.get_last_error())
        return _take(out)

    def dpapi_unprotect(blob):
        inb, _b1 = _blob(blob)
        ent, _b2 = _blob(ENTROPY)
        out = DATA_BLOB()
        if not _crypt32.CryptUnprotectData(ctypes.byref(inb), None, ctypes.byref(ent), None, None,
                                           CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
            raise ctypes.WinError(ctypes.get_last_error())
        return _take(out)

    DPAPI = True
else:                                       # fuera de Windows no hay DPAPI: sin cifrado (ver NOTES.md)
    DPAPI = False

    def dpapi_protect(data):
        raise KeyError_("DPAPI solo existe en Windows")

    dpapi_unprotect = dpapi_protect


# ---------------------------------------------------------------- fichero de clave
def read_key(path=None):
    """32 bytes de la clave, o None si no hay fichero. Lanza si existe pero no se puede descifrar."""
    for p in candidate_keyfiles(path):
        if os.path.exists(p):
            with open(p, "rb") as f:
                raw = f.read()
            if not raw.startswith(MAGIC):
                raise KeyError_(f"{p}: no es un fichero de clave YO2")
            try:
                key = dpapi_unprotect(raw[len(MAGIC):])
            except OSError as e:
                raise KeyError_(f"{p}: la DPAPI no lo descifra (¿otro usuario u otro PC?): {e}") from e
            if len(key) != 32:
                raise KeyError_(f"{p}: clave de {len(key)} bytes")
            return key
    return None


def create_key(path=None):
    """Clave nueva protegida con DPAPI; escritura atómica. Lanza si ya existe (nunca pisa una clave)."""
    path = path or default_keyfile()
    if os.path.exists(path):
        raise KeyError_(f"{path} ya existe: no se pisa")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    key = secrets.token_bytes(32)
    blob = MAGIC + dpapi_protect(key)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(blob); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)
    return key
