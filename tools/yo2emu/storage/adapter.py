"""Conector de base de datos. TODO lo específico de SQLite / SQLCipher vive aquí (PRAGMA, BEGIN IMMEDIATE, API de
copia en caliente, user_version, cifrado). El resto del módulo solo usa SQL estándar con marcadores «?» a través de
execute/executemany/query. Para pasar a PostgreSQL basta otra clase con los mismos métodos (ver NOTES.md).
"""
import os
import sqlite3

from . import keys

SQLITE_HEADER = b"SQLite format 3\x00"


def _cipher_module():
    try:
        from sqlcipher3 import dbapi2 as mod       # paquete sqlcipher3-wheels
        return mod
    except Exception:
        return None


def is_plain_sqlite(path):
    try:
        with open(path, "rb") as f:
            return f.read(16) == SQLITE_HEADER
    except FileNotFoundError:
        return None


class SqliteAdapter:
    """Una conexión (compartida entre hilos; el que llama serializa con su cerrojo)."""
    name = "sqlite"
    paramstyle = "qmark"

    def __init__(self, path, *, crypt="auto", keyfile=None, create=True, log=None):
        self.path = os.path.abspath(path)
        self.log = log or (lambda *a: None)
        self.keyfile = keyfile or keys.default_keyfile()
        self.key = None
        exists = os.path.exists(self.path) and os.path.getsize(self.path) > 0
        if not exists and not create:
            raise FileNotFoundError(self.path)
        mod = _cipher_module()
        if exists:
            encrypted = not is_plain_sqlite(self.path)
            if encrypted:
                if mod is None:
                    raise RuntimeError(f"{self.path} está cifrada y falta el paquete sqlcipher3-wheels")
                self.key = keys.read_key(self.keyfile)
                if self.key is None:       # nunca se crea una clave nueva para una base ya cifrada
                    raise RuntimeError(f"{self.path} está cifrada y no encuentro la clave ({self.keyfile}). "
                                       "No se crea otra: con otra clave la base no se abre.")
        else:
            want = {"on": True, "off": False}.get(crypt, None)
            if want is None:               # auto: cifrar si hay SQLCipher y DPAPI
                want = mod is not None and keys.DPAPI
            if want:
                if mod is None:
                    raise RuntimeError("YO2_DB_CRYPT=on pero falta el paquete sqlcipher3-wheels")
                self.key = keys.read_key(self.keyfile) or keys.create_key(self.keyfile)
            encrypted = bool(want)
        self.encrypted = encrypted
        self.mod = mod if encrypted else sqlite3
        self.conn = self._connect(self.path, self.key)
        self.execute("PRAGMA journal_mode=WAL")
        self.execute("PRAGMA synchronous=FULL")      # cada COMMIT llega al disco (como el fsync de state.json)

    # ---------------------------------------------------------------- conexión
    def _connect(self, path, key):
        conn = self.mod.connect(path, isolation_level=None, check_same_thread=False, timeout=15)
        if key is not None:
            conn.execute("PRAGMA key = \"x'%s'\"" % key.hex())
        try:
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()   # clave mala = «file is not a database»
        except Exception as e:
            conn.close()
            raise RuntimeError(f"no se puede abrir {path}: {e}") from e
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def info(self):
        v = self.query_one("SELECT sqlite_version()")[0]
        c = None
        if self.encrypted:
            r = self.query_one("PRAGMA cipher_version")
            c = r[0] if r else None
        return {"motor": "sqlcipher" if self.encrypted else "sqlite", "sqlite": v, "sqlcipher": c,
                "cifrada": self.encrypted, "clave": self.keyfile if self.encrypted else None}

    # ---------------------------------------------------------------- SQL estándar
    def execute(self, sql, params=()):
        return self.conn.execute(sql, params)

    def executemany(self, sql, rows):
        return self.conn.executemany(sql, rows)

    def query(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()

    def query_one(self, sql, params=()):
        return self.conn.execute(sql, params).fetchone()

    def iterate(self, sql, params=()):
        cur = self.conn.execute(sql, params)
        while True:
            rows = cur.fetchmany(500)
            if not rows:
                return
            yield from rows

    # ---------------------------------------------------------------- transacciones
    def begin_write(self):
        self.conn.execute("BEGIN IMMEDIATE")          # coge el cerrojo de escritura ya (PostgreSQL: BEGIN)

    def commit(self):
        self.conn.execute("COMMIT")

    def rollback(self):
        try:
            self.conn.execute("ROLLBACK")
        except Exception:
            pass

    # ---------------------------------------------------------------- versión de esquema
    def get_schema_version(self):
        return int(self.query_one("PRAGMA user_version")[0])

    def set_schema_version(self, n):
        self.conn.execute("PRAGMA user_version = %d" % int(n))

    # ---------------------------------------------------------------- copias
    def backup_to(self, dest):
        """Copia en caliente consistente (API de backup de SQLite) a dest, con la misma clave. Atómica: .tmp + replace."""
        dest = os.path.abspath(dest)
        tmp = dest + ".tmp"
        for f in (tmp, tmp + "-wal", tmp + "-shm"):
            if os.path.exists(f):
                os.remove(f)
        out = self._connect(tmp, self.key)
        try:
            self.conn.backup(out)
            out.execute("PRAGMA journal_mode=DELETE")    # la copia queda en un solo fichero
        finally:
            out.close()
        os.replace(tmp, dest)
        return dest

    def open_other(self, path):
        """Conexión de solo lectura lógica a otra base con la misma clave (para validar una copia antes de restaurar)."""
        other = object.__new__(SqliteAdapter)
        other.path, other.log, other.keyfile = os.path.abspath(path), self.log, self.keyfile
        enc = not is_plain_sqlite(path)
        other.encrypted, other.key = enc, (self.key if enc else None)
        if enc and other.key is None:
            other.key = keys.read_key(self.keyfile)
        other.mod = _cipher_module() if enc else sqlite3
        other.conn = other._connect(path, other.key)
        return other

    def restore_pages_from(self, other):
        """Sustituye el contenido de esta base por el de other (API de backup en sentido contrario)."""
        if other.encrypted != self.encrypted:
            raise RuntimeError("la copia y la base no tienen el mismo cifrado")
        other.conn.backup(self.conn)
        self.execute("PRAGMA journal_mode=WAL")      # la copia venía en modo DELETE
