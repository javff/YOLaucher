"""Tablas para la IA de NPC del emulador YO2 (agente 6, solo lectura de _original/).

    python build_ia_tables.py            # escribe cards_yo2.csv, npc_decks.json y npc_fuki.json aquí al lado

Fuentes (cliente 2008, _original/extracted/merge2008/data):
  bin#/card_prop.bin      8 B por carta; índice = id YO2 (el de los .ydc, 0x6600/0x6601 y 0x6202 tipo 0x40)
                          u32 a: bits 0-13 id Konami (4007 = Blue-Eyes; nombre del PNG), 14-22 ATK/10, 23-31 DEF/10
                          u32 b: bits 0-5 clase, 6-9 atributo, 10-13 nivel, 14-16 icono de mágica/trampa, 17-21 tipo
  bin#/card_indx_<l>.bin  2 × u32 por carta (offset nombre, offset texto) en card_name_<l>/card_desc_<l> (UTF-16LE)
  bin#/card_max.bin       u16 por carta: copias permitidas (lista de prohibidas: 0/1/2/3)
  bin#/card_intid.bin     u16[id Konami - 3000] = id YO2 (inversa de card_prop; 58 duplicados de arte)
  bin#/card_pass.bin      u32 passcodes de UNA lista vieja ordenada por id Konami desde 4005, con huecos:
                          solo sirve como CANDIDATO (se valida con estadísticas)
  cardpack/100NN.ydc      receta del mazo de la NPC NN (yo2pbinmap.dat: «コンプ景品 GET済み (レシピ: …)», columna 6 = 100NN)
  avatar/npc/fuki_index.csv  2 filas por NPC: 84 ids de texto (yo2text) y 84 «caras»
  common/yo2pbinmap.dat   bits PBIN de premios por NPC

Cruce con ocgcore/ProjectIgnis (_vendor/cards.cdb, solo inglés):
  1. nombre inglés normalizado (con RENAMES de nombres YO2 que el TCG cambió)
  2. candidato de card_pass.bin si el cdb tiene ese passcode con las mismas estadísticas
  3. nombre parecido (difflib >= 0.85) entre las cartas del cdb con las mismas estadísticas
No se copian textos de cartas ni diálogos: solo ids, nombres y números.
"""
import csv
import difflib
import json
import os
import re
import sqlite3
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", "..", ".."))
DATA = os.path.join(ROOT, "_original", "extracted", "merge2008", "data")
BIN = os.path.join(DATA, "bin#")
CDB = os.path.join(ROOT, "_vendor", "cards.cdb")
NPC_IDS = os.path.join(HERE, "..", "..", "npc_ids.json")

KIND = {1: "normal", 3: "efecto", 5: "fusión", 7: "fusión-efecto", 9: "ritual", 11: "ritual-efecto",
        13: "toon", 15: "espíritu", 17: "unión", 19: "géminis", 20: "token", 23: "token-efecto?",
        27: "mágica", 29: "trampa", 0: "-"}
ATTR = {1: "LUZ", 2: "OSCURIDAD", 3: "AGUA", 4: "FUEGO", 5: "TIERRA", 6: "VIENTO", 7: "DIVINO",
        8: "(mágica)", 9: "(trampa)", 0: "-"}
RACE = {1: "Dragón", 2: "Zombi", 3: "Demonio", 4: "Piro", 5: "Serpiente Marina", 6: "Roca", 7: "Máquina",
        8: "Pez", 9: "Dinosaurio", 10: "Insecto", 11: "Bestia", 12: "Guerrero-Bestia", 13: "Planta", 14: "Aqua",
        15: "Guerrero", 16: "Bestia Alada", 17: "Hada", 18: "Lanzador de Conjuros", 19: "Trueno", 20: "Reptil",
        21: "Bestia Divina", 22: "(mágica)", 23: "(trampa)", 0: "-"}
ICON = {0: "normal", 1: "contraefecto", 2: "campo", 3: "equipo", 4: "continua", 5: "rápida", 6: "ritual"}
# Equivalencias con cards.cdb (comprobadas con ~2000 cartas enlazadas por nombre)
CDB_ATTR = {1: 0x10, 2: 0x20, 3: 0x02, 4: 0x04, 5: 0x01, 6: 0x08, 7: 0x40}
CDB_RACE = {1: 0x2000, 2: 0x10, 3: 0x8, 4: 0x80, 5: 0x40000, 6: 0x100, 7: 0x20, 8: 0x20000, 9: 0x10000,
            10: 0x800, 11: 0x4000, 12: 0x8000, 13: 0x400, 14: 0x40, 15: 0x1, 16: 0x200, 17: 0x4, 18: 0x2,
            19: 0x1000, 20: 0x80000, 21: 0x200000}

RENAMES = {   # nombre YO2 (inglés) -> nombre actual en cards.cdb
    "Red-Eyes B. Dragon": "Red-Eyes Black Dragon", "B. Skull Dragon": "Black Skull Dragon",
    "Meteor B. Dragon": "Meteor Black Dragon", "Red-Eyes B. Chick": "Red-Eyes Black Chick",
    "Mystical Beast Serket": "Mystical Beast of Serket", "F.G.D.": "Five-Headed Dragon",
    "Amazon Archer": "Amazoness Archer", "Marie the Fallen One": "Darklord Marie",
    "Cliff the Trap Remover": "Dark Scorpion - Cliff the Trap Remover", "Big Core": "B.E.S. Big Core",
    "Oscillo Hero #2": "Wattkid", "Oscillo Hero": "Oscillo Hero",
    # segunda pasada (nombres TCG actuales; se ignoran si no están en el cdb)
    "Eternal Draught": "Eternal Drought", "Kinetic Soldier": "Cipher Soldier", "Bright Castle": "Shine Palace",
    "Harpie's Brother": "Sky Scout", "Red-Moon Baby": "Vampire Baby", "Makiu": "Makiu, the Magical Mist",
    "Pigeonholing Books of Spell": "Spellbook Organization", "Hidden Book of Spell": "Hidden Spellbook",
    "Judgment of Pharaoh": "Judgment of the Pharaoh", "Red-Eyes B. Chick": "Black Dragon's Chick",
    "Summon Priest": "Summoner Monk", "Machiners Force": "Machina Force",
    "Nurse Reficule the Fallen One": "Darklord Nurse Reficule", "Arms Hole": "Hidden Armory",
    "Earth Giant Gaia Plate": "Gaia Plate the Earth Giant", "Saurobeast Brachion": "Sauropod Brachion",
    "Tethys the Light Divine": "Tethys, Goddess of Light",
}


def norm(s):
    return "".join(ch for ch in s.lower() if ch.isalnum())


def u16s(path):
    b = open(path, "rb").read()
    return struct.unpack(f"<{len(b) // 2}H", b)


def u32s(path):
    b = open(path, "rb").read()
    return struct.unpack(f"<{len(b) // 4}I", b)


def utf16_table(lang, which):
    """Nombres (which=0) o textos (which=1) de card_name_<l>/card_desc_<l>."""
    idx = open(os.path.join(BIN, f"card_indx_{lang}.bin"), "rb").read()
    blob = open(os.path.join(BIN, ("card_name_" if which == 0 else "card_desc_") + f"{lang}.bin"), "rb").read()
    out = []
    for i in range(len(idx) // 8):
        o = struct.unpack_from("<II", idx, i * 8)[which]
        j = o
        while j + 1 < len(blob) and blob[j:j + 2] != b"\0\0":
            j += 2
        out.append(blob[o:j].decode("utf-16le", "replace").strip())
    return out


def load_cards():
    prop = open(os.path.join(BIN, "card_prop.bin"), "rb").read()
    names_e = utf16_table("e", 0)
    names_s = utf16_table("s", 0)
    names_j = utf16_table("j", 0)
    cmax = u16s(os.path.join(BIN, "card_max.bin"))
    cards = []
    for i in range(len(prop) // 8):
        a, b = struct.unpack_from("<II", prop, i * 8)
        cards.append({
            "yo2_id": i, "konami_id": a & 0x3FFF, "atk": ((a >> 14) & 0x1FF) * 10, "def": ((a >> 23) & 0x1FF) * 10,
            "kind": b & 0x3F, "attr": (b >> 6) & 0xF, "level": (b >> 10) & 0xF, "icon": (b >> 14) & 7,
            "race": (b >> 17) & 0x1F, "rest": b >> 22,
            "name_en": names_e[i] if i < len(names_e) else "", "name_es": names_s[i] if i < len(names_s) else "",
            "name_ja": names_j[i] if i < len(names_j) else "",
            "limit": cmax[i] if i < len(cmax) else 0,
        })
    return cards


def stats_match(c, row):
    """row = (type, atk, def, level, race, attribute) de cards.cdb."""
    typ, atk, dfn, lvl, race, attr = row
    if c["kind"] == 27:
        return bool(typ & 2)
    if c["kind"] == 29:
        return bool(typ & 4)
    if not typ & 1:
        return False
    return (atk == c["atk"] and dfn == c["def"] and (lvl & 0xFF) == c["level"]
            and CDB_ATTR.get(c["attr"]) == attr and CDB_RACE.get(c["race"]) == race)


def link_passcodes(cards):
    db = sqlite3.connect(CDB)
    rows = {}
    by_name = {}
    for code, name, alias, typ, atk, dfn, lvl, race, attr in db.execute(
            "select d.id, t.name, d.alias, d.type, d.atk, d.def, d.level, d.race, d.attribute "
            "from datas d join texts t on t.id = d.id order by d.alias != 0, d.id"):
        rows[code] = (name, alias, (typ, atk, dfn, lvl, race, attr))
        by_name.setdefault(norm(name), code)
    for old, new in RENAMES.items():
        if norm(new) in by_name:
            by_name.setdefault(norm(old), by_name[norm(new)])
    by_stats = {}
    for code, (name, alias, st) in rows.items():
        if alias == 0:
            by_stats.setdefault(st[1:] if st[0] & 1 else ("st", st[0] & 6), []).append(code)
    pw = u32s(os.path.join(BIN, "card_pass.bin"))
    for c in cards:
        c["passcode"], c["match"] = 0, ""
        if not c["konami_id"]:
            continue
        code = by_name.get(norm(c["name_en"]))
        if code:
            c["passcode"], c["match"] = code, "nombre"
            continue
        k = c["konami_id"] - 4005
        cand = pw[k] if 0 <= k < len(pw) else 0
        if cand in rows and stats_match(c, rows[cand][2]):
            ratio = difflib.SequenceMatcher(None, norm(c["name_en"]), norm(rows[cand][0])).ratio()
            if ratio >= 0.5:
                c["passcode"], c["match"] = cand, f"card_pass+stats ({ratio:.2f})"
                continue
        best, best_r = 0, 0.0
        pool = [code for code, (n, al, st) in rows.items() if al == 0 and stats_match(c, st)] \
            if c["kind"] not in (27, 29) else []
        for code in pool:
            r = difflib.SequenceMatcher(None, norm(c["name_en"]), norm(rows[code][0])).ratio()
            if r > best_r:
                best, best_r = code, r
        if best and best_r >= 0.85:
            c["passcode"], c["match"] = best, f"parecido+stats ({best_r:.2f})"
    return rows


def write_cards_csv(cards, path):
    cols = ["yo2_id", "konami_id", "name_en", "name_es", "kind", "kind_txt", "attr", "attr_txt", "level",
            "race", "race_txt", "spell_icon", "atk", "def", "limit", "passcode", "match"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for c in cards:
            if not c["konami_id"]:
                continue
            w.writerow([c["yo2_id"], c["konami_id"], c["name_en"], c["name_es"], c["kind"],
                        KIND.get(c["kind"], "?"), c["attr"], ATTR.get(c["attr"], "?"), c["level"], c["race"],
                        RACE.get(c["race"], "?"), ICON.get(c["icon"], "?") if c["kind"] in (27, 29) else "",
                        c["atk"], c["def"], c["limit"], c["passcode"] or "", c["match"]])


def read_ydc(path):
    v = struct.unpack("<125H", open(path, "rb").read()[:250])
    return [x for x in v[:80] if x], [x for x in v[80:95] if x], [x for x in v[95:] if x]


def pbin_npc():
    """bits de premios por NPC sacados de yo2pbinmap.dat."""
    out = {}
    txt = open(os.path.join(DATA, "common", "yo2pbinmap.dat"), "rb").read().decode("cp932", "replace")
    for line in txt.splitlines():
        p = line.split(",")
        if len(p) < 7:
            continue
        m = re.search(r"NPCID_(\d+)\s*(途中景品|コンプ景品)\s*(GET権利|GET済み)\s*(?:\(\s*レシピ：(.*?)\s*\))?", p[6])
        if not m:
            continue
        n = int(m.group(1))
        key = ("intermedio" if m.group(2) == "途中景品" else "completa") + ("_derecho" if m.group(3) == "GET権利" else "_recogido")
        d = out.setdefault(n, {})
        d[key + "_bit"] = int(p[0])
        if m.group(4):
            d["receta"] = m.group(4)
            d["receta_id"] = int(p[5])
    return out


def load_fuki():
    rows = [l.split(",") for l in open(os.path.join(DATA, "avatar", "npc", "fuki_index.csv"), "rb")
            .read().decode("cp932").splitlines() if l.strip()]
    texts, faces = {}, {}
    for i in range(0, len(rows), 2):
        nid = int(rows[i][0])
        texts[nid] = [int(x) for x in rows[i][1:85]]
        faces[nid] = [int(x) for x in rows[i + 1][1:85]]
    return texts, faces


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cards = load_cards()
    link_passcodes(cards)
    write_cards_csv(cards, os.path.join(HERE, "cards_yo2.csv"))
    real = [c for c in cards if c["konami_id"]]
    linked = [c for c in real if c["passcode"]]
    print(f"cards_yo2.csv: {len(real)} cartas, {len(linked)} con passcode "
          f"({sum(1 for c in linked if c['match'] == 'nombre')} por nombre, "
          f"{sum(1 for c in linked if c['match'].startswith('card_pass'))} por card_pass, "
          f"{sum(1 for c in linked if c['match'].startswith('parecido'))} por parecido)")

    npc_info = {}
    try:
        npc_info = {d["id"]: d for d in json.load(open(NPC_IDS, encoding="utf-8"))}
    except OSError:
        pass
    prizes = pbin_npc()
    by_id = {c["yo2_id"]: c for c in cards}
    texts, faces = load_fuki()
    decks = {}
    for nid in range(1, 99):
        d = {"npc": nid, "nombre": npc_info.get(nid, {}).get("name", ""), "premios_pbin": prizes.get(nid, {})}
        ydc = os.path.join(DATA, "cardpack", f"{10000 + nid}.ydc")
        if os.path.exists(ydc):
            main_, side, extra = read_ydc(ydc)
            d["ydc"] = f"cardpack/{10000 + nid}.ydc"
            for k, lst in (("main", main_), ("side", side), ("extra", extra)):
                d[k] = lst
                d[k + "_passcodes"] = [by_id[x]["passcode"] for x in lst]
                d[k + "_nombres"] = [by_id[x]["name_en"] for x in lst]
            d["sin_passcode"] = sorted({by_id[x]["name_en"] for x in main_ + extra if not by_id[x]["passcode"]})
        else:
            d["ydc"] = None
        d["fuki_vacios"] = sum(1 for t in texts.get(nid, []) if not t)
        decks[str(nid)] = d
    json.dump(decks, open(os.path.join(HERE, "npc_decks.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    have = [d for d in decks.values() if d["ydc"]]
    print(f"npc_decks.json: {len(have)} NPC con mazo (.ydc), {98 - len(have)} sin él; "
          f"cartas sin passcode en mazos: {sorted({n for d in have for n in d['sin_passcode']})}")
    json.dump({"slots": 84, "texts": {str(k): v for k, v in texts.items()},
               "faces": {str(k): v for k, v in faces.items()}},
              open(os.path.join(HERE, "npc_fuki.json"), "w", encoding="utf-8"), separators=(",", ":"))
    print(f"npc_fuki.json: {len(texts)} NPC × 84 huecos (ids de yo2text; los textos se quedan en el cliente)")


if __name__ == "__main__":
    main()
