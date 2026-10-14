"""Comparación de la paridad de bytes (REESTRUCTURA.md §3.3).

    python paridad/comparar.py --base              # (lo llama grabar.py --base) máscaras desde las pasadas de la base
    python paridad/comparar.py --base --escenarios kill   # rehace SOLO esos escenarios; los demás se quedan como estaban
    python paridad/comparar.py E1a                 # compara _out/grab/E1a/* con la base -> IGUAL o la 1.ª diferencia
    python paridad/comparar.py --mascara           # % de bytes enmascarados por id (tabla)

Base = N pasadas (3) de los mismos escenarios con el mismo código. Por conexión (escenario, cliente, puerto sin
offset, nº de conexión, rx/tx):
- prefijo estable = la parte más larga de la secuencia de ids que es IGUAL en las N pasadas. Si las N son enteras
  iguales, la conexión es «estable» y se exige también la misma longitud total; si no (duelos con azar, temporizadores),
  solo se compara el prefijo y lo de detrás cuenta como enmascarado.
- dentro del prefijo, cada mensaje: si su largo varía entre pasadas, se enmascara entero (no se compara el largo); si no,
  se enmascaran los bytes que varían (rangos [desde, hasta)). Más las máscaras a mano de mascaras.json (con motivo).
- una conexión que no sale en todas las pasadas se enmascara entera.
- «repetidos» de mascaras.json: en esas conexiones, los mensajes SEGUIDOS con ese id y los mismos bytes cuentan como uno
  (al cargar, antes de todo lo demás): sondeos cuyo número depende del reloj (0x6702 del espectador).
Comparación: nivel 1 = ids del prefijo (y longitud si es estable), nivel 2 = largos, nivel 3 = bytes fuera de máscara.
El orden ENTRE conexiones no se compara (hilos); dentro de una conexión, sí. El campo «t» (hora) y «param» no cuentan.
"""
import argparse
import glob
import json
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comun  # noqa: E402

BASE_DIR = os.path.join(comun.OUT, "paridad-base")
BASE_JSON = os.path.join(BASE_DIR, "paridad-base.json")
MASC_JSON = os.path.join(BASE_DIR, "mascara_por_id.json")


def _k(esc, r):
    return f"{esc}|{r['cli']}|{r['puerto']}|{r['con']}|{r['dir']}"


def cargar(pasada_dir, solo=None):
    """{clave: [(i, id, hex)]} de una pasada (todas sus carpetas de escenario, o solo las de la lista `solo`)."""
    out = defaultdict(list)
    for esc_dir in sorted(glob.glob(os.path.join(pasada_dir, "*"))):
        if not os.path.isdir(esc_dir):
            continue
        esc = os.path.basename(esc_dir)
        if solo and esc not in solo:
            continue
        for f in glob.glob(os.path.join(esc_dir, "tap", "*.jsonl")):
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        r = json.loads(line)
                        out[_k(esc, r)].append((r["i"], r["id"], r["hex"]))
    reps = repetidos()
    for k in out:
        out[k].sort()
        ids = _reps_para(k, reps)
        if ids:
            col = []
            for m in out[k]:
                if col and m[1] in ids and col[-1][1] == m[1] and col[-1][2] == m[2]:
                    continue
                col.append(m)
            out[k] = col
    return out


def repetidos():
    """Reglas «repetidos» de mascaras.json (ver el docstring del módulo)."""
    m = comun.leer_json(os.path.join(comun.AQUI, "mascaras.json"), {}) or {}
    return {"_reglas": m.get("repetidos") or []}


def _reps_para(clave, reps):
    """{ids} que se colapsan en esta conexión (escenario|cli|puerto|con|dir)."""
    esc, cli, puerto, con, way = clave.split("|")
    out = set()
    for r in reps.get("_reglas", []):
        if r.get("escenario") not in (None, esc) or r.get("cli") not in (None, cli) or                 r.get("puerto") not in (None, int(puerto)) or r.get("dir") not in (None, way):
            continue
        out.add(int(r["id"], 16))
    return out


def pasadas(etiqueta):
    d = os.path.join(comun.OUT, "grab", etiqueta)
    ps = sorted((p for p in glob.glob(os.path.join(d, "*")) if os.path.isdir(p)), key=lambda p: int(os.path.basename(p)))
    if not ps:
        sys.exit(f"no hay grabaciones en {d} (python paridad/grabar.py --etiqueta {etiqueta})")
    return ps


def _rangos(pos):
    out = []
    for p in sorted(pos):
        if out and out[-1][1] == p:
            out[-1][1] = p + 1
        else:
            out.append([p, p + 1])
    return out


def mascaras_mano():
    m = comun.leer_json(os.path.join(comun.AQUI, "mascaras.json"), {}) or {}
    return m.get("mascaras", []), {int(k, 16): v for k, v in (m.get("ids_azar") or {}).items()}


def _mano_para(clave, mid, largo, manos):
    """Posiciones enmascaradas a mano para este mensaje (None = ninguna; 'todo' = entero)."""
    esc, cli, puerto, con, way = clave.split("|")
    pos = set()
    for m in manos:
        if int(m["id"], 16) != mid:
            continue
        if m.get("escenario") not in (None, esc) or m.get("dir") not in (None, way) or \
                m.get("puerto") not in (None, int(puerto)):
            continue
        if m.get("todo"):
            return "todo"
        a, b = m["bytes"]
        pos |= set(range(a, min(b, largo)))
    return pos or None


# ------------------------------------------------------------------------------------------------ base
def construir_base(etiqueta="base", solo=None):
    """Base desde las pasadas de `etiqueta`. solo = lista de escenarios: se rehacen SOLO sus conexiones y las de los
    demás escenarios se quedan como estaban en la base (p. ej. regrabar `kill` sin tocar `pvp`)."""
    ps = pasadas(etiqueta)
    runs = [cargar(p, solo) for p in ps]
    claves = sorted(set().union(*runs))
    base = {"pasadas": len(runs), "conexiones": {}}
    if solo:
        vieja = comun.leer_json(BASE_JSON)
        if not vieja:
            sys.exit(f"--escenarios sin base previa ({BASE_JSON}): hace falta la base entera primero")
        base["conexiones"] = {k: v for k, v in vieja["conexiones"].items() if k.split("|")[0] not in solo}
        base["rehechos"] = dict(vieja.get("rehechos") or {}, **{e: time.strftime("%Y-%m-%d %H:%M") for e in solo})
    for k in claves:
        if not all(k in r for r in runs):
            base["conexiones"][k] = {"presencia": "variable", "n": [len(r.get(k, [])) for r in runs],
                                     "ids": [m[1] for m in (next(r[k] for r in runs if k in r))],
                                     "largos": [len(m[2]) // 2 for m in (next(r[k] for r in runs if k in r))]}
            continue
        seqs = [[m[1] for m in r[k]] for r in runs]
        p = 0
        while all(len(s) > p for s in seqs) and len({s[p] for s in seqs}) == 1:
            p += 1
        estable = all(s == seqs[0] for s in seqs)
        msgs = []
        for j in range(p):
            hx = [r[k][j][2] for r in runs]
            if len({len(h) for h in hx}) != 1:
                msgs.append({"largo": None, "mascara": "todo"})
                continue
            bs = [bytes.fromhex(h) for h in hx]
            pos = [i for i in range(len(bs[0])) if len({b[i] for b in bs}) != 1]
            msgs.append({"largo": len(bs[0]), "mascara": _rangos(pos)})
        cola = runs[0][k][p:]
        base["conexiones"][k] = {"estable": estable, "prefijo": p, "ids": seqs[0][:p],
                                 "hex": [m[2] for m in runs[0][k][:p]], "msgs": msgs,
                                 "n": [len(s) for s in seqs],
                                 "cola": [[m[1], len(m[2]) // 2] for m in cola]}
    comun.escribir_json(BASE_JSON, base)
    tabla = porcentaje_mascara(base)
    inest = [k for k, c in base["conexiones"].items() if c.get("presencia") or not c["estable"]]
    print(f"== base: {len(runs)} pasadas, {len(claves)} conexiones ({len(inest)} con parte inestable) -> {BASE_JSON}")
    for k in inest:
        c = base["conexiones"][k]
        print(f"   inestable {k}: " + ("no sale en todas las pasadas" if c.get("presencia") else
                                     f"prefijo {c['prefijo']} de {c['n']} mensajes"))
    imprimir_tabla(tabla)
    # la base ya debe ser IGUAL contra cada una de sus propias pasadas
    ok = all(comparar_pasada(r, base, solo)[0] for r in runs)
    print("== cada pasada de la base contra la base:", "IGUAL" if ok else "DISTINTO (¿máscara mal hecha?)")
    return 0 if ok else 1


def porcentaje_mascara(base):
    """{'rx 0x4101': {bytes, enmascarados, pct}} con lo enmascarado por la base y por mascaras.json."""
    manos, _ = mascaras_mano()
    tot = defaultdict(lambda: [0, 0, 0])               # bytes, enmascarados, mensajes
    for k, c in base["conexiones"].items():
        way = k.split("|")[4]
        if c.get("presencia"):
            for mid, n in zip(c["ids"], c["largos"]):
                t = tot[(way, mid)]; t[0] += n; t[1] += n; t[2] += 1
            continue
        for mid, hx, m in zip(c["ids"], c["hex"], c["msgs"]):
            n = len(hx) // 2
            t = tot[(way, mid)]; t[0] += n; t[2] += 1
            mano = _mano_para(k, mid, n, manos)
            if m["mascara"] == "todo" or mano == "todo":
                t[1] += n
                continue
            pos = {i for a, b in m["mascara"] for i in range(a, b)} | (mano or set())
            t[1] += len(pos)
        for mid, n in c["cola"]:
            t = tot[(way, mid)]; t[0] += n; t[1] += n; t[2] += 1
    out = {}
    for (way, mid), (b, e, n) in sorted(tot.items(), key=lambda x: (x[0][1], x[0][0])):
        out[f"{way} 0x{mid:04x}"] = {"mensajes": n, "bytes": b, "enmascarados": e,
                                    "pct": round(100.0 * e / b, 1) if b else 0.0}
    comun.escribir_json(MASC_JSON, out)
    return out


def imprimir_tabla(tabla, umbral=5.0):
    _, azar = mascaras_mano()
    print(f"   % enmascarado por id (> {umbral}% fuera de duelo 0x6xxx y sin motivo en mascaras.json = MIRAR):")
    b = e = 0
    for k, v in tabla.items():
        b += v["bytes"]; e += v["enmascarados"]
        if v["pct"] == 0:
            continue
        mid = int(k.split()[1], 16)
        nota = ""
        if v["pct"] > umbral:
            nota = ("duelo" if 0x6000 <= mid < 0x7000 else (f"azar: {azar[mid]}" if mid in azar else "MIRAR"))
        print(f"     {k}: {v['pct']:5.1f}% de {v['bytes']} B en {v['mensajes']} mensajes  {nota}")
    print(f"   total: {round(100.0 * e / b, 1) if b else 0}% de {b} B enmascarado; ids sin nada enmascarado: "
          f"{sum(1 for v in tabla.values() if v['pct'] == 0)} de {len(tabla)}")


# ------------------------------------------------------------------------------------------------ comparar
def _ctx(ids, j):
    return " ".join(f"0x{x:04x}" + ("*" if i == j else "") for i, x in list(enumerate(ids))[max(0, j - 2):j + 3])


def comparar_pasada(run, base, solo=None):
    """(igual, [diferencias]) de una pasada contra la base (solo = comparar únicamente esos escenarios)."""
    manos, _ = mascaras_mano()
    difs = []
    for k, c in base["conexiones"].items():
        if c.get("presencia") or (solo and k.split("|")[0] not in solo):
            continue
        got = run.get(k)
        if got is None:
            difs.append({"clave": k, "nivel": 1, "que": "falta la conexión"})
            continue
        ids = [m[1] for m in got]
        if ids[:c["prefijo"]] != c["ids"] or (c["estable"] and len(ids) != len(c["ids"])):
            j = next((j for j in range(min(len(ids), len(c["ids"]))) if ids[j] != c["ids"][j]),
                     min(len(ids), len(c["ids"])))
            difs.append({"clave": k, "nivel": 1, "indice": j,
                         "que": f"ids: base [{_ctx(c['ids'], j)}] / ahora [{_ctx(ids, j)}] "
                                f"(base {len(c['ids'])}{'' if c['estable'] else '+cola'}, ahora {len(ids)})"})
            continue
        for j, (m, hb) in enumerate(zip(c["msgs"], c["hex"])):
            ha = got[j][2]
            mid = c["ids"][j]
            if m["mascara"] == "todo":
                continue
            if len(ha) != len(hb):
                difs.append({"clave": k, "nivel": 2, "indice": j, "id": f"0x{mid:04x}",
                             "que": f"largo base {len(hb) // 2} / ahora {len(ha) // 2}", "base": hb[:200], "ahora": ha[:200]})
                break
            mano = _mano_para(k, mid, len(hb) // 2, manos)
            if mano == "todo":
                continue
            pos = {i for a, b in m["mascara"] for i in range(a, b)} | (mano or set())
            ba, bb = bytes.fromhex(ha), bytes.fromhex(hb)
            mal = [i for i in range(len(bb)) if i not in pos and ba[i] != bb[i]]
            if mal:
                a0 = max(0, mal[0] - 24)                  # ventana alrededor del primer byte distinto
                difs.append({"clave": k, "nivel": 3, "indice": j, "id": f"0x{mid:04x}",
                             "que": f"bytes {_rangos(mal)[:6]} distintos (máscara {m['mascara'][:6]}); hex desde el byte {a0}",
                             "base": hb[2 * a0:2 * a0 + 128], "ahora": ha[2 * a0:2 * a0 + 128]})
                break
    conocidas = set(base["conexiones"])
    for k in run:
        if k not in conocidas:
            difs.append({"clave": k, "nivel": 1, "que": f"conexión nueva ({len(run[k])} mensajes)"})
    return not difs, difs


def comparar(etiqueta):
    base = comun.leer_json(BASE_JSON)
    if not base:
        sys.exit(f"no hay base ({BASE_JSON}): python paridad/grabar.py --base")
    res = []
    for p in pasadas(etiqueta):
        igual, difs = comparar_pasada(cargar(p), base)
        res.append({"pasada": os.path.basename(p), "igual": igual, "diferencias": difs})
        print(f"== paridad {etiqueta}/{os.path.basename(p)}: " + ("IGUAL" if igual else f"DISTINTO ({len(difs)} conexiones)"))
        for d in difs[:8]:
            esc, cli, puerto, con, way = d["clave"].split("|")
            print(f"   [{esc}] cliente {cli} puerto {puerto} conexión {con} {way}: nivel {d['nivel']}"
                  + (f", mensaje #{d['indice']}" if "indice" in d else "") + (f" {d['id']}" if "id" in d else "")
                  + f": {d['que']}")
            if "base" in d:
                print(f"      base : {d['base']}\n      ahora: {d['ahora']}")
    comun.escribir_json(os.path.join(comun.OUT, f"comparar-{etiqueta}.json"), res)
    return 0 if all(r["igual"] for r in res) else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("etiqueta", nargs="?")
    ap.add_argument("--base", action="store_true", help="rehace la base (máscaras) desde _out/grab/base/*")
    ap.add_argument("--mascara", action="store_true", help="tabla de %% enmascarado por id")
    ap.add_argument("--escenarios", default="", help="con --base: rehace solo estos escenarios (separados por comas)")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.base:
        return construir_base(solo=[x for x in a.escenarios.split(",") if x] or None)
    if a.mascara:
        base = comun.leer_json(BASE_JSON)
        if not base:
            sys.exit("no hay base")
        imprimir_tabla(porcentaje_mascara(base))
        return 0
    if not a.etiqueta:
        ap.print_help()
        return 2
    return comparar(a.etiqueta)


if __name__ == "__main__":
    sys.exit(main())
