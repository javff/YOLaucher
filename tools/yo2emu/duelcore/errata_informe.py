"""Informe de erratas posibles: compara el texto de cada carta en el CLIENTE 2008 con el de cards.cdb (ProjectIgnis, de HOY)
y lista las que difieren de verdad. Solo informa: no arregla nada (los arreglos son scripts_2008/cNNNNN.lua).

    python duelcore/errata_informe.py                      # las 60 mas distintas (tabla, de mas a menos distinta)
    python duelcore/errata_informe.py --todas --salida informe.md
    python duelcore/errata_informe.py --carta "Ryko"       # texto del cliente, texto de hoy y si ya tiene script de 2008
    python duelcore/errata_informe.py --umbral 0.6         # solo con parecido (0-1) por debajo de 0.6
    python duelcore/errata_informe.py --pistas --todas     # las que ademas llevan pistas de errata moderna (lo mas util)

Fuentes: el texto en ingles del cliente sale de los bin#/card_desc_e.bin + card_indx_e.bin del merge.dat (los extrae
paquete/extraer_juego.py; YO2_JUEGO_DATOS), la correspondencia carta-passcode de research/ia/cards_yo2.csv (la rehace
paquete/generar_tablas.py) y el texto de hoy de YO2_VENDOR/cards.cdb. Stdlib pura.

Como se compara: las dos versiones se pasan a minusculas y a un vocabulario comun (Graveyard = GY, "remove from play" =
banish, "Flip Effect Monster" = Flip monster, "select" = target...), sin puntuacion ni palabras vacias; el parecido es el
ratio de difflib sobre esas palabras. Las diferencias de plantilla puro (que son la mayoria) se quedan por encima del
umbral; lo que baja suele ser texto distinto de verdad (condiciones nuevas, "once per Duel", "cannot"...). Las
"pistas" son patrones que solo estan en el texto de hoy (once per Duel, except, "the turn this card was sent"...). La lista es
un punto de partida para mirar a mano, no un veredicto: mirar siempre el texto de las dos columnas.
"""
import argparse
import csv
import difflib
import os
import re
import sqlite3
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EMU = os.path.normpath(os.path.join(HERE, ".."))
if EMU not in sys.path:
    sys.path.insert(0, EMU)
from yo2srv import rutas  # noqa: E402

CSV = os.path.join(EMU, "research", "ia", "cards_yo2.csv")
SCRIPTS_2008 = os.path.join(HERE, "scripts_2008")

# vocabulario comun: (patron, sustituto), en este orden
SINONIMOS = (
    (r"\bgraveyard\b", "gy"), (r"\bremoved? from play\b", "banish"), (r"\bremoving from play\b", "banishing"),
    (r"\bremoved from play\b", "banished"), (r"\bflip effect monsters?\b", "flip monster"), (r"\bselect\b", "target"),
    (r"\bsent to the gy\b", "sent to gy"), (r"\bspecial summoned\b", "special summoned"),
    (r"\bdestroys?\b", "destroy"), (r"\bdestroyed\b", "destroy"), (r"\bsends?\b", "send"), (r"\bsent\b", "send"),
    (r"\bmonsters\b", "monster"), (r"\bcards\b", "card"), (r"\bplayer s\b", "player"), (r"\bopponent s\b", "opponent"),
    (r"\bcontrols?\b", "control"), (r"\bcontrolled\b", "control"), (r"\byour opponent\b", "opponent"),
)
VACIAS = {"the", "a", "an", "of", "this", "that", "to", "and", "or", "it", "its", "is", "are", "be", "by", "in", "on",
          "you", "can", "your", "from", "for", "as", "if", "when", "while", "with", "other", "than", "also", "then",
          "only", "will", "up", "any", "all", "each", "both", "same", "turn", "once", "per", "use", "used", "effect"}
# Pistas de texto cambiado: (nombre, patron que debe estar en el texto de HOY, patron que NO debe estar en el del cliente)
PISTAS = (
    ("una-vez-por-Duelo", r"once per duel", r"once per duel"),
    ("excepto", r"\bexcept\b", r"\bexcept\b"),
    ("no-el-turno-que-cae", r"the turn (this card was|it was) sent|the turn this card is sent", r"the turn"),
    ("simultaneo", r"simultaneously|apply these effects", r"simultaneously"),
    ("Quick-Effect", r"quick effect", r"quick effect"),
    ("dos-efectos-en-uno", r"\n\s*\n|\u25cf", r"\u25cf"),
    ("cannot", r"\bcannot\b", r"\bcannot\b|can not"),
)


def pistas(viejo, nuevo):
    return [n for n, hoy, ant in PISTAS if re.search(hoy, nuevo, re.I) and not re.search(ant, viejo, re.I)]


def tabla(nombre_blob, nombre_idx, cual):
    """card_name_* / card_desc_*: card_indx_* trae (offset del nombre, offset del texto) por entrada."""
    idx = open(rutas.juego("bin#", nombre_idx), "rb").read()
    blob = open(rutas.juego("bin#", nombre_blob), "rb").read()
    out = []
    for i in range(len(idx) // 8):
        o = struct.unpack_from("<II", idx, i * 8)[cual]
        j = o
        while j + 1 < len(blob) and blob[j:j + 2] != b"\0\0":
            j += 2
        out.append(blob[o:j].decode("utf-16le", "replace"))
    return out


def normalizar(txt):
    t = (txt or "").lower().replace("\r", " ").replace("\n", " ")
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    for pat, sub in SINONIMOS:
        t = re.sub(pat, sub, t)
    return [w for w in t.split() if w not in VACIAS]


def cargar():
    nombres = tabla("card_name_e.bin", "card_indx_e.bin", 0)
    textos = tabla("card_desc_e.bin", "card_indx_e.bin", 1)
    cdb = sqlite3.connect(os.path.join(rutas.vendor(), "cards.cdb"))
    hoy = {i: (n, d) for i, n, d in cdb.execute("select id, name, desc from texts")}
    scripts = set()
    if os.path.isdir(SCRIPTS_2008):
        scripts = {int(f[1:-4]) for f in os.listdir(SCRIPTS_2008) if re.fullmatch(r"c\d+\.lua", f)}
    filas, vistos = [], set()
    for r in csv.DictReader(open(CSV, encoding="utf-8")):
        if not r["passcode"].isdigit() or int(r["passcode"]) == 0:
            continue
        code, y = int(r["passcode"]), int(r["yo2_id"])
        if code in vistos or code not in hoy or y >= len(textos):
            continue
        vistos.add(code)
        viejo, nuevo = textos[y], hoy[code][1]
        # monstruos normales: el texto es solo ambientacion, sin efecto que pueda tener errata
        if r["kind_txt"] == "normal" or not viejo.strip():
            continue
        # fusiones: el cliente guarda los materiales como "%c4030" + "%c4106" (ids de carta), no como texto
        if re.search(r"%c\d+", viejo):
            continue
        a, b = normalizar(viejo), normalizar(nuevo)
        ratio = difflib.SequenceMatcher(None, a, b).ratio() if (a or b) else 1.0
        solo_hoy = pistas(viejo, nuevo)
        filas.append({"code": code, "yo2": y, "nombre": hoy[code][0], "ratio": ratio, "viejo": viejo.strip(),
                      "nuevo": nuevo.strip().replace("\r\n", " / "), "pistas": solo_hoy, "script2008": code in scripts})
    return filas


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--carta", help="nombre (trozo) de la carta: muestra las dos versiones")
    ap.add_argument("--top", type=int, default=60)
    ap.add_argument("--todas", action="store_true", help="sin limite de filas")
    ap.add_argument("--umbral", type=float, default=0.75, help="solo las de parecido por debajo (0-1; defecto 0.75)")
    ap.add_argument("--pistas", action="store_true", help="solo las que ademas tienen alguna pista de texto cambiado")
    ap.add_argument("--salida", help="escribe el informe (Markdown) en este fichero")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    filas = cargar()
    if a.carta:
        for f in filas:
            if a.carta.lower() in f["nombre"].lower():
                print(f"{f['nombre']} ({f['code']}, id YO2 {f['yo2']})  parecido {f['ratio']:.2f}  "
                      f"script de 2008: {'SI' if f['script2008'] else 'no'}")
                print("  cliente 2008:", f["viejo"])
                print("  cards.cdb   :", f["nuevo"])
        return
    cand = sorted((f for f in filas if f["ratio"] < a.umbral and (f["pistas"] or not a.pistas)), key=lambda f: f["ratio"])
    total = len(cand)
    if not a.todas:
        cand = cand[:a.top]
    out = [f"# Erratas posibles: cliente 2008 frente a cards.cdb\n",
           f"{len(filas)} cartas con efecto comparadas; {total} con parecido < {a.umbral}; "
           f"{sum(1 for f in filas if f['script2008'])} ya tienen script de 2008 (columna 2008).\n",
           "| Parecido | 2008 | Carta | Texto del cliente 2008 | Texto de hoy | Pistas |", "|---:|:---:|---|---|---|---|"]
    for f in cand:
        c = lambda s: s.replace("|", "\\|").replace("\n", " ")  # noqa: E731
        out.append(f"| {f['ratio']:.2f} | {'si' if f['script2008'] else ''} | {c(f['nombre'])} ({f['code']}) | "
                   f"{c(f['viejo'])} | {c(f['nuevo'])} | {', '.join(f['pistas'])} |")
    texto = "\n".join(out) + "\n"
    if a.salida:
        with open(a.salida, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(texto)
        print(f"{a.salida}: {len(cand)} filas de {total}")
    else:
        print(texto)


if __name__ == "__main__":
    main()
