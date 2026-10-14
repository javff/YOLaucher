"""Pruebas del almacén. Trabaja SOLO con copias en logs/prueba_sqlite/ (la state.json viva solo se lee).

    python storage/test_storage.py            pruebas
    python storage/test_storage.py --bench    + rendimiento (escrituras por segundo: sqlcipher, sqlite y json)
"""
import copy
import json
import os
import random
import shutil
import sys
import threading
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from storage import chain  # noqa: E402
from storage.backend import Store, ChainError  # noqa: E402

LIVE = os.path.join(HERE, "state.json")
WORK = os.path.join(HERE, "logs", "prueba_sqlite", "test_" + time.strftime("%Y%m%d-%H%M%S"))
RES = []


def _estado_sintetico(ruta):
    """Estado INVENTADO con la forma de un state.json (raíz del dueño + cuentas, baúl, mazos, correo, cartera) para un
    árbol sin estado vivo (el público, código abierto). Determinista (semilla fija). Devuelve la ruta escrita."""
    rnd = random.Random(20261012)

    def perfil(nombre):
        return {"name": nombre,
                "rec": {"lv": rnd.randrange(1, 30), "mp": rnd.randrange(0, 5000), "tp": 0, "win": rnd.randrange(50),
                        "lose": rnd.randrange(50)},
                "trunk": {str(c): rnd.randrange(1, 4) for c in sorted(rnd.sample(range(1, 3300), 400))},
                "pbin": "00" * 64, "npcbin": {str(n): rnd.randrange(4) for n in range(1, 30)},
                "decks": {"principal": {"main": [rnd.randrange(1, 3300) for _ in range(40)], "extra": [], "side_count": 0}},
                "friends": [], "claims": {}, "pending": []}
    st = {"schema": 1,
          "lobby": {"mail": [{"id": i, "from": "Sistema", "text": f"Mensaje {i}"} for i in range(5)], "next_mail": 5,
                    "friends": {}, "black": {}, "team": {}},
          "players": {"0": "Jugador0"}, "profiles": {"0": perfil("Jugador0")}, "active_slot": 0,
          "wallet": {"mp": 1000, "dpp": 10},
          "accounts": {f"{i:06x}": {"idx": i, "players": {"0": f"Jugador{i}"}, "profiles": {"0": perfil(f"Jugador{i}")},
                                    "active_slot": 0} for i in range(1, 6)},
          "owner_acct": None}
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:                 # como Store.export_json (fin de línea del sistema)
        f.write(json.dumps(st, ensure_ascii=False, indent=1))
    return ruta


if not os.path.isfile(LIVE):                           # árbol público: no hay estado vivo que copiar
    LIVE = _estado_sintetico(os.path.join(WORK, "estado-sintetico.json"))


def check(name, ok, det=""):
    RES.append((name, bool(ok)))
    print(f"  {'OK ' if ok else 'MAL'} {name}" + (f"  [{det}]" if det else ""))


def fresh(name, crypt="auto"):
    d = os.path.join(WORK, name)
    os.makedirs(d, exist_ok=True)
    js = os.path.join(d, "state.json")
    shutil.copyfile(LIVE, js)
    return Store(js + ".sqlite", json_path=js, crypt=crypt), js


def ver_ok(s, js=None):
    return all(ok for _, ok, _ in s.verify(js))


# ---------------------------------------------------------------- 1. ida y vuelta
def t_ida_vuelta():
    print("== migración ida y vuelta")
    s, js = fresh("idavuelta")
    check("base cifrada con SQLCipher", s.ad.encrypted, s.ad.info().get("sqlcipher"))
    with open(js, "rb") as f:
        b0 = f.read()
    check("state.json intacto tras importar", open(js, "rb").read() == b0)
    out = js + ".export"
    s.export_json(out)
    check("exportar SQLite -> JSON idéntico byte a byte", open(out, "rb").read() == b0)
    res = s.verify(js)
    check("verificar (cadena, fotos, tablas, == JSON)", all(ok for _, ok, _ in res),
          "; ".join(f"{n}={ok}" for n, ok, _ in res))
    # cambios variados, cerrar, reabrir: mismo estado y mismo orden
    st = s.load()
    p = st["profiles"]["0"]
    p["trunk"]["4007"] = p["trunk"].get("4007", 0) + 3
    p["rec"]["lv"] = p["rec"].get("lv", 0) + 1
    p.setdefault("decks", {})["prueba"] = {"main": [1, 2, 3], "extra": [], "side_count": 0}
    st["equipos"] = {"next_id": 2, "teams": {"1": {"name": "Ñandú", "members": [1, 2]}}}
    st.setdefault("accounts", {})["abc123"] = {"idx": 9, "players": {"0": "X"},
                                               "profiles": {"0": {"name": "X", "trunk": {"1": 1}}}, "active_slot": 0}
    del st["wallet"]
    s.save(st)
    st2 = s.load()
    st2["lobby"]["mail"] = []
    st2["equipos"]["teams"]["1"]["members"].append(3)
    st2["accounts"]["abc123"]["profiles"]["0"]["trunk"]["1"] = 0
    s.save(st2)
    expect = s.load()
    s.close()
    s2 = Store(s.db_path)
    check("reabrir: mismo estado y mismo orden de claves", chain.same(s2.load(), expect))
    check("verificar tras cambios", ver_ok(s2))
    s2.export_json(out)
    with open(out, encoding="utf-8") as f:
        check("exportar tras cambios == estado", chain.same(json.load(f), expect))
    s2.rebuild_tables()
    check("reconstruir tablas desde el historial", ver_ok(s2))
    s2.close()


# ---------------------------------------------------------------- 2. diff/apply con cambios al azar
def _mutate(x, rnd, depth=0):
    if type(x) is dict and x and depth < 6:
        k = rnd.choice(list(x))
        r = rnd.random()
        if r < 0.15:
            del x[k]
        elif r < 0.3:
            x["n%d" % rnd.randrange(10 ** 6)] = rnd.choice([1, "a", True, None, [1, 2], {"z": 1}])
        elif r < 0.35:                                  # reordenar
            items = list(x.items()); rnd.shuffle(items); x.clear(); x.update(items)
        elif r < 0.45:
            x[k] = rnd.choice([0, 1, True, False, 1.0, "1", [], {}])
        else:
            _mutate(x[k], rnd, depth + 1)
    elif type(x) is list and x:
        x[rnd.randrange(len(x))] = rnd.randrange(100)


def t_diff_fuzz():
    print("== diff/apply (500 cambios al azar, orden de claves exacto)")
    with open(LIVE, encoding="utf-8") as f:
        base = json.load(f)
    rnd = random.Random(7)
    bad = 0
    cur = base
    for _ in range(500):
        new = copy.deepcopy(cur)
        for _ in range(rnd.randrange(1, 4)):
            _mutate(new, rnd)
        ops = json.loads(chain.dumps(chain.diff(cur, new)))
        got = chain.apply_ops(copy.deepcopy(cur), ops)
        if not chain.same(got, new):
            bad += 1
        cur = new
    check("apply(diff(a, b), a) == b con el mismo orden", bad == 0, f"{bad} fallos")


# ---------------------------------------------------------------- 3. cadena y manipulación
def _falsear(data):
    """El mismo evento con otra cantidad de carta (lo que haría alguien regalándose cartas)."""
    d = json.loads(data)
    d["ops"][0][2] = 99
    out = chain.dumps(d)
    assert out != data
    return out


def t_cadena():
    print("== cadena de hashes y manipulación")
    s, js = fresh("cadena")
    for i in range(30):
        st = s.load()
        st["profiles"]["0"]["trunk"][str(9000 + i)] = i + 1
        s.save(st)
    ok, n, msg = s.verify_chain()
    check("cadena válida (31 eventos)", ok and n == 31, msg)
    db = s.db_path
    s.close()

    def tamper(sql, params=()):
        t = Store(db)
        t.ad.execute(sql, params)
        r = t.verify_chain()
        t.close()
        return r

    # a) cambiar el contenido de un evento
    work = os.path.join(WORK, "cadena", "copia.sqlite")
    s0 = Store(db); s0.backup_to(work); s0.close()
    ev = Store(db).ad.query_one("SELECT data FROM events WHERE seq = 10")[0]
    ok, n, msg = tamper("UPDATE events SET data = ? WHERE seq = 10", (_falsear(ev),))
    check("cambiar un evento rompe la verificación", not ok and "10" in msg, msg)
    try:
        Store(db).close()
        check("y el arranque lo rechaza", False, "abrió sin quejarse")
    except ChainError as e:
        check("y el arranque lo rechaza", True, str(e))
    # b) cambiarlo Y recalcular su hash: lo delata el siguiente
    shutil.copyfile(work, db)
    for f in (db + "-wal", db + "-shm"):
        if os.path.exists(f):
            os.remove(f)
    t = Store(db)
    r = t.ad.query_one("SELECT seq, ts, acct, slot, kind, data, prev_hash FROM events WHERE seq = 10")
    data2 = _falsear(r[5])
    h2 = chain.event_hash(r[6], r[0], r[1], r[2], r[3], r[4], data2)
    t.ad.execute("UPDATE events SET data = ?, hash = ? WHERE seq = 10", (data2, h2))
    ok, n, msg = t.verify_chain()
    t.close()
    check("cambiar un evento y su hash: lo delata el siguiente", not ok and "11" in msg, msg)
    # c) borrar un evento del medio
    shutil.copyfile(work, db)
    for f in (db + "-wal", db + "-shm"):
        if os.path.exists(f):
            os.remove(f)
    ok, n, msg = tamper("DELETE FROM events WHERE seq = 5")
    check("borrar un evento rompe la verificación", not ok, msg)
    # d) tocar la foto
    shutil.copyfile(work, db)
    for f in (db + "-wal", db + "-shm"):
        if os.path.exists(f):
            os.remove(f)
    t = Store(db)
    t.ad.execute("UPDATE snapshots SET state = replace(state, '\"lv\":', '\"lv\":1') WHERE seq = 1")
    res = dict((n_, ok_) for n_, ok_, _ in t.verify())
    t.close()
    check("tocar una foto lo detecta verificar", res.get("fotos (snapshots) == historial") is False)
    # e) tocar una tabla de estado
    shutil.copyfile(work, db)
    for f in (db + "-wal", db + "-shm"):
        if os.path.exists(f):
            os.remove(f)
    t = Store(db)
    t.ad.execute("UPDATE trunk SET qty = qty + 50 WHERE card = '9005'")
    res = dict((n_, ok_) for n_, ok_, _ in t.verify())
    check("tocar una tabla de estado lo detecta verificar", res.get("tablas de estado == historial") is False)
    t.rebuild_tables()
    check("... y reconstruir lo arregla", ver_ok(t))
    t.close()


# ---------------------------------------------------------------- 4. copia y restauración
def t_copias():
    print("== copia en caliente y restauración")
    s, js = fresh("copias")
    st = s.load(); st["profiles"]["0"]["rec"]["mp"] = 111; s.save(st)
    good = s.load()
    bk = os.path.join(WORK, "copias", "copia.sqlite")
    stop = threading.Event()
    errs = []

    def writer():                                # escribe mientras se copia
        i = 0
        while not stop.is_set():
            try:
                with s.lock:
                    x = s.load(); x["profiles"]["0"]["trunk"]["777"] = i; s.save(x)
                i += 1
            except Exception as e:
                errs.append(e)
                return
    th = threading.Thread(target=writer); th.start()
    time.sleep(0.05)
    info = s.backup_to(bk)
    stop.set(); th.join()
    check("copia en caliente con escrituras a la vez", not errs and os.path.exists(bk), str(info))
    c = Store(bk)
    check("la copia abre, cadena válida y coherente", ver_ok(c), f"{c.head[0]} eventos")
    copied = c.load()
    c.close()
    st = s.load(); st["profiles"]["0"]["rec"]["mp"] = 999999; st["players"]["0"] = "ROTO"; s.save(st)
    r = s.restore_from(bk)
    check("restaurar devuelve el estado de la copia", chain.same(s.load(), copied), str(r["eventos"]))
    check("restaurar deja copia previa", os.path.exists(r["copia_previa"]))
    check("tras restaurar: verificar OK", ver_ok(s))
    st = s.load(); st["profiles"]["0"]["rec"]["mp"] = 5; s.save(st)
    s.close()
    s2 = Store(s.db_path)
    check("tras restaurar se sigue escribiendo y reabre bien", s2.load()["profiles"]["0"]["rec"]["mp"] == 5
          and ver_ok(s2))
    # una copia manipulada no se restaura
    t = Store(bk); t.ad.execute("UPDATE events SET ts = 'x' WHERE seq = 1"); t.close()
    try:
        s2.restore_from(bk)
        check("una copia manipulada NO se restaura", False)
    except ChainError as e:
        check("una copia manipulada NO se restaura", True, str(e))
    check("la base sigue bien tras el intento", ver_ok(s2))
    s2.close()
    # boot_backup
    s3 = Store(s.db_path)
    for _ in range(3):
        s3.boot_backup(keep=2)
        time.sleep(1.05)
    n = len([f for f in os.listdir(os.path.dirname(s.db_path)) if ".bak-arranque-" in f])
    check("copias de arranque: se conservan las últimas", n == 2, f"{n}")
    s3.close()


# ---------------------------------------------------------------- 5. varios hilos
def t_hilos():
    print("== escrituras desde varios hilos")
    s, js = fresh("hilos")
    N, K = 8, 40
    errs = []
    base = s.load()["profiles"]["0"]["trunk"]

    def worker(i):
        try:
            for j in range(K):
                if j % 2 == 0:                       # patrón de run.py: cerrojo alrededor de leer-modificar-guardar
                    with s.lock:
                        st = s.load()
                        st["profiles"]["0"]["trunk"][f"h{i}"] = st["profiles"]["0"]["trunk"].get(f"h{i}", 0) + 1
                        st.setdefault("contadores", {})[str(i)] = j
                        s.save(st)
                else:                                # cuenta propia por hilo
                    with s.lock:
                        st = s.load()
                        a = st.setdefault("accounts", {}).setdefault(f"acct{i}", {"idx": 10 + i, "profiles": {}})
                        pr = a["profiles"].setdefault("0", {"name": f"P{i}", "trunk": {}})
                        pr["trunk"]["1"] = pr["trunk"].get("1", 0) + 1
                        s.save(st)
        except Exception as e:
            errs.append(repr(e))
    ths = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    t0 = time.time()
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    dt = time.time() - t0
    st = s.load()
    tr = st["profiles"]["0"]["trunk"]
    good = all(tr.get(f"h{i}") == K // 2 for i in range(N)) and \
        all(st["accounts"][f"acct{i}"]["profiles"]["0"]["trunk"]["1"] == K // 2 for i in range(N))
    check(f"{N} hilos x {K} escrituras sin errores", not errs, "; ".join(errs[:2]))
    check("no se pierde ninguna escritura", good)
    check("cadena + tablas coherentes tras la concurrencia", ver_ok(s), f"{s.head[0]} eventos en {dt:.2f}s")
    s.close()
    # dos procesos/conexiones: el segundo ve los cambios del primero al guardar (diff contra el disco)
    a = Store(s.db_path); b = Store(s.db_path)
    x = a.load(); x["profiles"]["0"]["rec"]["mp"] = 4242; a.save(x)
    y = b.load(); y["profiles"]["0"]["rec"]["tp"] = 7; b.save(y)
    a.close(); b.close()
    c = Store(s.db_path)
    check("dos conexiones a la vez: cadena válida", ver_ok(c), f"{c.head[0]} eventos")
    c.close()
    _ = base


# ---------------------------------------------------------------- 6. rendimiento
def _json_save(path, st):
    """Lo mismo que save_state_raw de run.py (indent=1, .tmp + fsync + replace, y state.json.ok)."""
    text = json.dumps(st, ensure_ascii=False, indent=1)
    for p, sync in ((path, True), (path + ".ok", False)):
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text); f.flush()
            if sync:
                os.fsync(f.fileno())
        os.replace(tmp, p)


def bench(n=300):
    print(f"== rendimiento ({n} guardados de un cambio pequeño en el baúl, como save_state)")
    out = {}
    for crypt in ("on", "off"):
        s, js = fresh("bench_" + crypt, crypt=crypt)
        t0 = time.perf_counter()
        for i in range(n):
            st = s.load()                                   # run.py: save_state relee antes de guardar
            st["profiles"]["0"]["trunk"]["4007"] = i
            s.save(st)
        dt = time.perf_counter() - t0
        out["sqlcipher" if crypt == "on" else "sqlite"] = n / dt
        t0 = time.perf_counter()
        for _ in range(n):
            s.load()
        out[("sqlcipher" if crypt == "on" else "sqlite") + "_lecturas"] = n / (time.perf_counter() - t0)
        s.close()
    d = os.path.join(WORK, "bench_json"); os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "state.json"); shutil.copyfile(LIVE, p)
    t0 = time.perf_counter()
    for i in range(n):
        with open(p, encoding="utf-8") as f:
            st = json.load(f)
        st["profiles"]["0"]["trunk"]["4007"] = i
        _json_save(p, st)
    out["json"] = n / (time.perf_counter() - t0)
    for k, v in out.items():
        print(f"  {k}: {v:.0f}/s")
    return out


if __name__ == "__main__":
    os.makedirs(WORK, exist_ok=True)
    print(f"carpeta de prueba: {WORK}")
    t_ida_vuelta()
    t_diff_fuzz()
    t_cadena()
    t_copias()
    t_hilos()
    if "--bench" in sys.argv:
        bench()
    bad = [n for n, ok in RES if not ok]
    print(f"== RESULTADO almacén: {len(RES) - len(bad)}/{len(RES)} bien" + (f"; mal: {bad}" if bad else ""))
    sys.exit(1 if bad else 0)
