"""Cartas del juego original -> assets/cards (nombre y texto en español + imágenes WebP).

  python tools/build_cards.py           # tabla + imágenes (las imágenes ya hechas se saltan)

Enlaza el id interno de YO2 (el nombre del PNG: 4007 = Blue-Eyes) con el passcode que usan
ocgcore y los scripts de ProjectIgnis (89631139) POR NOMBRE EN INGLÉS contra _vendor/cards.cdb.
Los nombres salen de card_name_e, que va en el mismo orden que card_prop (id interno en sus
14 bits bajos).

⚠️ NO usar bin#/card_pass.bin: parece "passcode por id - 4005" porque acierta con las primeras
cartas (Blue-Eyes, Mystical Elf...), pero se desplaza enseguida: medido el 2026-10-05, solo 562
de 1.884 coinciden. Daba arte equivocado en cientos de cartas.

Salida:
  cards/cards.json   { "<passcode>": { "id": <id YO2>, "name": "...", "desc": "...",
                       "t": tipo, "a": atributo, "r": raza, "l": nivel, "atk": .., "def": .. } }
  cards/<passcode>.webp, cards/mini/<passcode>.webp, cards/back.webp
"""
import json
import os
import sqlite3
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Rutas de los datos: yo2srv/rutas.py (P1, empaquetado). Sin YO2_JUEGO_DATOS/YO2_VENDOR son las de siempre
# (_original/extracted/merge2008/data y _vendor/cards.cdb). Pillow (PIL) SOLO lo usa main() (imágenes): cardmap.py importa
# este fichero desde el servidor, y el servidor no lleva Pillow (sin él, antes, el motor de reglas no cargaba).
try:
    if os.path.join(ROOT, "tools", "yo2emu") not in sys.path:
        sys.path.append(os.path.join(ROOT, "tools", "yo2emu"))
    from yo2srv import rutas as _rutas
except ImportError:                    # servidor viejo sin yo2srv/rutas.py (cargas en caliente sobre commits antiguos): la ruta de siempre
    _rutas = None
DATA = _rutas.juego() if _rutas else os.path.join(ROOT, "_original", "extracted", "merge2008", "data")
BIN = os.path.join(DATA, "bin#")
CDB = os.path.join(_rutas.vendor() if _rutas else os.path.join(ROOT, "_vendor"), "cards.cdb")
OUT = os.path.join(ROOT, "apps", "client", "public", "assets", "cards")


def utf16_table(name_file, idx_file, which):
    """card_name_* / card_desc_*: card_indx_* trae (offset nombre, offset texto) por entrada."""
    idx = open(os.path.join(BIN, idx_file), "rb").read()
    blob = open(os.path.join(BIN, name_file), "rb").read()
    out = []
    for i in range(len(idx) // 8):
        o = struct.unpack_from("<II", idx, i * 8)[which]
        j = o
        while j + 1 < len(blob) and blob[j:j + 2] != b"\0\0":
            j += 2
        out.append(blob[o:j].decode("utf-16le", "replace"))
    return out


# Nombres de YO2 que el TCG cambió después (nombre YO2 -> nombre actual en cards.cdb)
RENAMES = {
    "Red-Eyes B. Dragon": "Red-Eyes Black Dragon",
    "B. Skull Dragon": "Black Skull Dragon",
    "Meteor B. Dragon": "Meteor Black Dragon",
    "Red-Eyes B. Chick": "Red-Eyes Black Chick",
    "Mystical Beast Serket": "Mystical Beast of Serket",
    "F.G.D.": "Five-Headed Dragon",
    "Amazon Archer": "Amazoness Archer",
    "Marie the Fallen One": "Darklord Marie",
    "Harpie Lady 1": "Harpie Lady 1",
    "Cliff the Trap Remover": "Dark Scorpion - Cliff the Trap Remover",
    "Frog the Jam": "Frog The Jam",
    "Rope of Spirit": "Rope of Spirit",
    "Big Core": "B.E.S. Big Core",
    "Crush D. Gandra": "Crush D. Gandra",
}


def norm(s):
    return "".join(ch for ch in s.lower() if ch.isalnum())


def main():
    from PIL import Image                     # solo aquí (ver arriba)

    prop = open(os.path.join(BIN, "card_prop.bin"), "rb").read()
    ids = [struct.unpack_from("<I", prop, i * 8)[0] & 0x3FFF for i in range(len(prop) // 8)]
    names_e = utf16_table("card_name_e.bin", "card_indx_e.bin", 0)
    names_s = utf16_table("card_name_s.bin", "card_indx_s.bin", 0)
    descs_s = utf16_table("card_desc_s.bin", "card_indx_s.bin", 1)

    db = sqlite3.connect(CDB)
    by_name = {}
    # Primero las cartas "originales" (alias = 0); las de arte alternativo solo si no hay otra
    for code, name in db.execute("select d.id, t.name from datas d join texts t on t.id = d.id order by d.alias != 0"):
        by_name.setdefault(norm(name), code)
    for old, new in RENAMES.items():
        code = by_name.get(norm(new))
        if code:
            by_name[norm(old)] = code

    cards, missing = {}, []
    seen = set()
    for i, cid in enumerate(ids):
        if not cid or cid in seen or i >= len(names_e):
            continue
        seen.add(cid)
        img = os.path.join(DATA, "card", f"{cid}.png")
        if not os.path.exists(img):
            continue
        code = by_name.get(norm(names_e[i]), 0)
        if not code:
            missing.append((cid, names_e[i]))
            continue
        if str(code) in cards:
            continue
        typ, attr, race, level, atk, dfn = db.execute(
            "select type, attribute, race, level, atk, def from datas where id = ?", (code,)).fetchone()
        # t/a/r/l/atk/def: para el detalle de carta (iconos de atributo, tipo y nivel del original)
        cards[str(code)] = {"id": cid, "name": names_s[i].strip() or names_e[i].strip(),
                            "desc": descs_s[i].strip() if i < len(descs_s) else "",
                            "t": typ, "a": attr, "r": race, "l": level & 0xFF, "atk": atk, "def": dfn}

    os.makedirs(os.path.join(OUT, "mini"), exist_ok=True)
    json.dump(cards, open(os.path.join(OUT, "cards.json"), "w", encoding="utf-8"), ensure_ascii=False,
              separators=(",", ":"))
    print(f"  {len(cards)} cartas enlazadas, {len(missing)} sin enlazar: {missing}")

    if "--no-images" in sys.argv:
        return
    done = 0
    jobs = [(0, "back")] + [(c["id"], code) for code, c in cards.items()]
    for cid, out_name in jobs:
        for sub, folder in (("card", ""), ("mini", "mini")):
            dst = os.path.join(OUT, folder, f"{out_name}.webp")
            if os.path.exists(dst):
                continue
            Image.open(os.path.join(DATA, sub, f"{cid:04d}.png")).convert("RGBA").save(dst, "WEBP", quality=85, method=4)
            done += 1
    size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(OUT) for f in fs)
    print(f"  {done} imágenes nuevas, {size / 1e6:.1f} MB en total")


if __name__ == "__main__":
    main()
