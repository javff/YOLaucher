"""CLI del almacén. Desde tools/yo2emu:

  python -m storage info        [--db X]
  python -m storage importar    --json state.json [--db X]     crea la base desde el JSON (si está vacía)
  python -m storage exportar    --json SALIDA [--db X] [--forzar]
  python -m storage verificar   [--db X] [--json state.json]   cadena + fotos + tablas (+ igual al JSON)
  python -m storage copia       --a DESTINO [--db X]
  python -m storage restaurar   --desde COPIA [--db X]         SOLO con el servidor parado
  python -m storage reconstruir [--db X]                       recalcula las tablas de estado
  python -m storage historial   [--db X] [-n 20]

--db por defecto: <yo2emu>/state.json.sqlite (o YO2_DB). Exportar sobre un fichero que existe exige --forzar y deja
antes una copia <fichero>.bak-sqlite-<fecha>.
"""
import argparse
import datetime
import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from storage.backend import Store, default_db_path  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m storage")
    ap.add_argument("orden", choices=["info", "importar", "exportar", "verificar", "copia", "restaurar",
                                      "reconstruir", "historial"])
    ap.add_argument("--db")
    ap.add_argument("--json")
    ap.add_argument("--a", dest="dest")
    ap.add_argument("--desde")
    ap.add_argument("--forzar", action="store_true")
    ap.add_argument("-n", type=int, default=20)
    a = ap.parse_args(argv)
    live_json = os.path.join(HERE, "state.json")
    db = os.path.abspath(a.db) if a.db else default_db_path(os.environ.get("YO2_STATE") or live_json)

    if a.orden == "importar":
        if not a.json:
            sys.exit("importar: falta --json")
        if os.path.exists(db) and os.path.getsize(db) > 0:
            sys.exit(f"{db} ya existe: no se reimporta (bórrala a mano o usa otra --db)")
        s = Store(db, json_path=a.json, log=print)
        print(s.stats())
        return 0
    if not os.path.exists(db):
        sys.exit(f"no existe {db}")
    s = Store(db, log=print)
    if a.orden == "info":
        for k, v in s.stats().items():
            print(f"  {k}: {v}")
    elif a.orden == "exportar":
        if not a.json:
            sys.exit("exportar: falta --json")
        out = os.path.abspath(a.json)
        if os.path.exists(out):
            if not a.forzar:
                sys.exit(f"{out} ya existe: añade --forzar (se guarda antes una copia)")
            bak = out + ".bak-sqlite-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            shutil.copyfile(out, bak)
            print(f"copia previa: {bak}")
        s.export_json(out)
        print(f"exportado: {out} ({s.head[0]} eventos)")
    elif a.orden == "verificar":
        res = s.verify(a.json)
        for name, ok, det in res:
            print(f"  {'OK ' if ok else 'MAL'} {name} {det}")
        return 0 if all(ok for _, ok, _ in res) else 1
    elif a.orden == "copia":
        if not a.dest:
            sys.exit("copia: falta --a DESTINO")
        print(s.backup_to(a.dest))
    elif a.orden == "restaurar":
        if not a.desde:
            sys.exit("restaurar: falta --desde COPIA")
        print(s.restore_from(a.desde))
    elif a.orden == "reconstruir":
        print(f"filas: {s.rebuild_tables()}")
    elif a.orden == "historial":
        for r in s.ad.query("SELECT seq, ts, acct, slot, kind, length(data), substr(hash, 1, 12) FROM events "
                            "ORDER BY seq DESC LIMIT ?", (a.n,)):
            print("  ", *r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
