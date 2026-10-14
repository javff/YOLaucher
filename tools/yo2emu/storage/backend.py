"""Backend SQLite del guardado del emulador, con la MISMA interfaz de dict que state.json:

    store = open_store(STATE_PATH, log=log, lock=STATE_LOCK)
    st = store.load()          # dict nuevo (como json.load)
    store.save(st)             # diff con lo último guardado -> 1 evento + tablas, en UNA transacción

Fuente de verdad = tabla events (solo crece, hash encadenado). Las tablas de estado y las fotos (snapshots) se
derivan de ella. Ver NOTES.md.
"""
import datetime
import glob
import json
import os
import threading

from . import chain, project, schema
from .adapter import SqliteAdapter

SNAPSHOT_EVERY = 200


class ChainError(RuntimeError):
    pass


def default_db_path(state_path):
    """state.json -> state.json.sqlite (así las pruebas que borran «state-prueba.json.*» la limpian también)."""
    return os.path.abspath(os.environ.get("YO2_DB") or (state_path + ".sqlite"))


class Store:
    def __init__(self, db_path, *, json_path=None, log=None, lock=None, crypt=None, keyfile=None,
                 snapshot_every=SNAPSHOT_EVERY):
        self.db_path = os.path.abspath(db_path)
        self.json_path = os.path.abspath(json_path) if json_path else None
        self.log = log or (lambda *a: None)
        self.lock = lock if lock is not None else threading.RLock()
        self.snapshot_every = snapshot_every
        self.crypt = crypt or os.environ.get("YO2_DB_CRYPT", "auto")
        self.keyfile = keyfile
        with self.lock:
            self.ad = SqliteAdapter(self.db_path, crypt=self.crypt, keyfile=keyfile, log=self.log)
            rebuild = schema.migrate(self.ad, self.log)
            if self._count_events() == 0:
                self._initial_import()
            self._reload_locked()
            if rebuild:
                self.rebuild_tables()

    # ---------------------------------------------------------------- interfaz de dict
    def load(self):
        with self.lock:
            return json.loads(self.text)

    def save(self, st):
        """Guarda st. Devuelve el seq del evento escrito (0 si no había cambios)."""
        text = json.dumps(st, ensure_ascii=False)     # antes del cerrojo: si no es serializable no toca nada
        new = json.loads(text)                        # copia propia, con los tipos que daría state.json
        with self.lock:
            self.ad.begin_write()
            try:
                head = self._head_db()
                if head != self.head:                  # otro proceso escribió: diff contra lo que hay en disco
                    self.log(f"[almacén] la base cambió por fuera (cabeza {head[0]} != {self.head[0]}): recargo")
                    self._reload_locked(in_tx=True)
                ops = chain.diff(self.state, new)
                if not ops:
                    self.ad.rollback()
                    return 0
                acct, slot, kind = chain.describe(ops)
                seq, h = self._append_event(kind, {"ops": ops}, acct, slot)
                scopes = []
                for op in ops:
                    for sc in project.scopes_for(op[1]):
                        if sc not in scopes:
                            scopes.append(sc)
                if ("all",) in scopes:
                    scopes = [("all",)]
                self._apply_scopes(scopes, new)
                if seq - self.snap_seq >= self.snapshot_every:
                    self._snapshot(seq, h, new)
                self.ad.commit()
            except BaseException:
                self.ad.rollback()
                raise
            self.state, self.text, self.head = new, text, (seq, h)
            return seq

    # ---------------------------------------------------------------- historial
    def _count_events(self):
        return int(self.ad.query_one("SELECT count(*) FROM events")[0])

    def _head_db(self):
        r = self.ad.query_one("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1")
        return (int(r[0]), r[1]) if r else (0, chain.GENESIS)

    def _append_event(self, kind, data, acct=None, slot=None):
        seq0, prev = self._head_db()
        seq = seq0 + 1
        ts = chain.now_ts()
        data_text = chain.dumps(data)
        h = chain.event_hash(prev, seq, ts, acct, slot, kind, data_text)
        self.ad.execute("INSERT INTO events (seq, ts, acct, slot, kind, data, prev_hash, hash, sig) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)", (seq, ts, acct, slot, kind, data_text, prev, h))
        return seq, h

    def _snapshot(self, seq, h, state):
        self.ad.execute("INSERT INTO snapshots (seq, event_hash, ts, state) VALUES (?, ?, ?, ?)",
                        (seq, h, chain.now_ts(), chain.dumps(state)))
        self.snap_seq = seq

    def _initial_import(self):
        src = self.json_path
        state, origen = None, None
        if src and os.path.exists(src):
            with open(src, encoding="utf-8") as f:
                state = json.load(f)
            origen = os.path.basename(src)
        elif src and os.path.exists(src + ".ok"):
            with open(src + ".ok", encoding="utf-8") as f:
                state = json.load(f)
            origen = os.path.basename(src + ".ok")
        if state is None:
            state, origen = {"players": {}}, None          # como load_state_raw sin fichero
        self.ad.begin_write()
        try:
            seq, h = self._append_event("importacion_inicial", {"state": state, "origen": origen})
            self._apply_scopes([("all",)], state)
            self._snapshot(seq, h, state)
            self.ad.commit()
        except BaseException:
            self.ad.rollback()
            raise
        self.log(f"[almacén] importación inicial desde {origen or '(vacío)'} -> {os.path.basename(self.db_path)}"
                 f" (el JSON no se toca)")

    def _reload_locked(self, in_tx=False):
        """Estado = última foto válida + eventos posteriores (verificando su cadena)."""
        r = self.ad.query_one("SELECT seq, event_hash, state FROM snapshots ORDER BY seq DESC LIMIT 1")
        if r is None:
            state, seq, prev = None, 0, chain.GENESIS
        else:
            seq, prev = int(r[0]), r[1]
            ev = self.ad.query_one("SELECT hash FROM events WHERE seq = ?", (seq,))
            if ev is None or ev[0] != prev:
                raise ChainError(f"la foto del seq {seq} no coincide con el historial")
            state = json.loads(r[2])
        self.snap_seq = seq
        for ev in self.ad.query("SELECT seq, ts, acct, slot, kind, data, prev_hash, hash FROM events "
                                "WHERE seq > ? ORDER BY seq", (seq,)):
            state = self._check_and_apply(ev, state, seq, prev)
            seq, prev = int(ev[0]), ev[7]
        if state is None:
            state = {"players": {}}
        self.state = state
        self.text = json.dumps(state, ensure_ascii=False)
        self.head = (seq, prev)

    @staticmethod
    def _check_and_apply(ev, state, last_seq, last_hash):
        seq, ts, acct, slot, kind, data, prev_hash, h = ev
        if int(seq) != last_seq + 1:
            raise ChainError(f"hueco en el historial: tras {last_seq} viene {seq}")
        if prev_hash != last_hash:
            raise ChainError(f"evento {seq}: prev_hash no enlaza con el anterior")
        if chain.event_hash(prev_hash, seq, ts, acct, slot, kind, data) != h:
            raise ChainError(f"evento {seq}: el hash no cuadra (evento modificado)")
        d = json.loads(data)
        if kind == "importacion_inicial":
            return d["state"]
        return chain.apply_ops(state, d["ops"])

    # ---------------------------------------------------------------- tablas de estado
    def _apply_scopes(self, scopes, state):
        for sc in scopes:
            dels, rows = project.refresh(sc, state)
            for sql, params in dels:
                self.ad.execute(sql, params)
            by = {}
            for t, row in rows:
                by.setdefault(t, []).append(row)
            for t, rs in by.items():
                self.ad.executemany(project.INSERT[t], rs)

    def rebuild_tables(self):
        """Borra y recalcula todas las tablas de estado desde el historial (no toca events)."""
        with self.lock:
            st = self.replay()
            self.ad.begin_write()
            try:
                self._apply_scopes([("all",)], st)
                self.ad.commit()
            except BaseException:
                self.ad.rollback()
                raise
            return len(project.project(st))

    def replay(self, upto=None):
        """Estado reproduciendo TODO el historial desde el seq 1 (verifica la cadena). Lanza ChainError."""
        state, seq, prev = None, 0, chain.GENESIS
        sql = "SELECT seq, ts, acct, slot, kind, data, prev_hash, hash FROM events"
        params = ()
        if upto is not None:
            sql += " WHERE seq <= ?"
            params = (upto,)
        for ev in self.ad.iterate(sql + " ORDER BY seq", params):
            state = self._check_and_apply(ev, state, seq, prev)
            seq, prev = int(ev[0]), ev[7]
        return state

    # ---------------------------------------------------------------- verificación
    def verify_chain(self):
        """(ok, nº eventos, mensaje). Recorre todo el historial."""
        seq, prev = 0, chain.GENESIS
        try:
            for ev in self.ad.iterate("SELECT seq, ts, acct, slot, kind, data, prev_hash, hash FROM events "
                                      "ORDER BY seq"):
                s, ts, acct, slot, kind, data, ph, h = ev
                if int(s) != seq + 1:
                    return False, seq, f"hueco en el historial: tras {seq} viene {s}"
                if ph != prev:
                    return False, seq, f"evento {s}: prev_hash no enlaza"
                if chain.event_hash(ph, s, ts, acct, slot, kind, data) != h:
                    return False, seq, f"evento {s}: hash no cuadra (modificado)"
                seq, prev = int(s), h
        except Exception as e:
            return False, seq, f"error leyendo el historial: {e!r}"
        return True, seq, "cadena válida"

    def verify(self, json_path=None):
        """Comprobación completa. Devuelve lista de (nombre, ok, detalle)."""
        res = []
        with self.lock:
            ok, n, msg = self.verify_chain()
            res.append(("cadena de hashes", ok, f"{n} eventos; {msg}"))
            if not ok:
                return res
            st = self.replay()
            res.append(("reproducir historial == estado en memoria", chain.same(st, self.state), ""))
            bad = []
            for seq, h, s in self.ad.query("SELECT seq, event_hash, state FROM snapshots ORDER BY seq"):
                ev = self.ad.query_one("SELECT hash FROM events WHERE seq = ?", (seq,))
                if ev is None or ev[0] != h or not chain.same(json.loads(s), self.replay(upto=seq)):
                    bad.append(seq)
            res.append(("fotos (snapshots) == historial", not bad, f"mal: {bad}" if bad else ""))
            try:
                tb = project.from_tables(self.ad)
                res.append(("tablas de estado == historial", tb == st, ""))
            except Exception as e:
                res.append(("tablas de estado == historial", False, repr(e)))
            if json_path:
                try:
                    with open(json_path, encoding="utf-8") as f:
                        js = json.load(f)
                    det = ("idénticos, mismo orden de claves" if chain.same(js, st) else
                           "mismo contenido, otro orden de claves" if js == st else "contenido DISTINTO")
                    res.append((f"{os.path.basename(json_path)} == base", js == st, det))
                except Exception as e:
                    res.append((f"{os.path.basename(json_path)} == base", False, repr(e)))
        return res

    # ---------------------------------------------------------------- exportar / copias
    def export_json(self, path, indent=1):
        """Escribe el estado como lo escribiría run.py (indent=1, UTF-8 sin BOM), atómico."""
        with self.lock:
            text = json.dumps(self.state, ensure_ascii=False, indent=indent)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
        return path

    def backup_to(self, path):
        """Copia en caliente consistente: nadie escribe mientras se copia (cerrojo); misma clave."""
        with self.lock:
            self.ad.backup_to(path)
            return {"fichero": os.path.abspath(path), "eventos": self.head[0], "bytes": os.path.getsize(path)}

    def restore_from(self, path):
        """Sustituye la base por una copia. Antes valida la copia (cadena) y guarda la actual en
        <base>.antes-de-restaurar-<fecha>. Para usar con el servidor parado o desde su propio proceso."""
        path = os.path.abspath(path)
        other = self.ad.open_other(path)
        try:
            v = other.get_schema_version()
            if v > schema.LATEST:
                raise RuntimeError(f"la copia está en el esquema {v}; este código llega al {schema.LATEST}")
            tmp = Store.__new__(Store)
            tmp.ad = other
            ok, n, msg = tmp.verify_chain()
            if not ok or n == 0:
                raise ChainError(f"copia no válida: {msg}")
            with self.lock:
                pre = self.db_path + ".antes-de-restaurar-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                self.ad.backup_to(pre)
                self.ad.restore_pages_from(other)
        finally:
            other.close()
        with self.lock:
            rebuild = schema.migrate(self.ad, self.log)
            self._reload_locked()
            if rebuild:
                self.rebuild_tables()
        self.log(f"[almacén] restaurada desde {os.path.basename(path)} ({n} eventos); la anterior en "
                 f"{os.path.basename(pre)}")
        return {"eventos": n, "copia_previa": pre}

    def boot_backup(self, keep=10):
        """Copia al arrancar (<base>.bak-arranque-<fecha>); se conservan las `keep` últimas."""
        dst = self.db_path + ".bak-arranque-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.backup_to(dst)
        for f in sorted(glob.glob(self.db_path + ".bak-arranque-*"))[:-keep]:
            try:
                os.remove(f)
            except OSError:
                pass
        return dst

    def stats(self):
        with self.lock:
            out = dict(self.ad.info())
            out["esquema"] = self.ad.get_schema_version()
            out["eventos"] = self.head[0]
            out["fotos"] = int(self.ad.query_one("SELECT count(*) FROM snapshots")[0])
            for t in schema.PROJECTION_TABLES:
                out["filas_" + t] = int(self.ad.query_one("SELECT count(*) FROM %s" % t)[0])
            out["bytes"] = sum(os.path.getsize(f) for f in (self.db_path, self.db_path + "-wal") if os.path.exists(f))
            return out

    def close(self):
        with self.lock:
            self.ad.close()


def open_store(state_path, log=None, lock=None, **kw):
    """Abre (o crea e importa state.json la primera vez) la base que va con state_path."""
    return Store(default_db_path(state_path), json_path=state_path, log=log, lock=lock, **kw)
