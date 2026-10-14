"""Rehace en el PC del anfitrión, desde SU cliente, las tablas del servidor que salen del juego (P5 11-10; PUB 12-10).

    python generar_tablas.py --datos <YO2_JUEGO_DATOS> --vendor <YO2_VENDOR> --juego <carpeta del cliente 2008>
    python generar_tablas.py ... --destino <otra carpeta tools/yo2emu>      (escribe allí en vez de junto a este script)
    python generar_tablas.py ... --comprobar <carpeta tools/yo2emu de referencia>   (además compara byte a byte)

Por qué: estas tablas llevan datos sacados del cliente de Konami (nombres de carta, recetas de NPC, títulos...). Ni el
zip del Discord ni el árbol de código abierto las llevan: se generan aquí, con la MISMA lógica con la que se hicieron
las del repo, y salen byte a byte iguales (comprobado el 12-10 con --comprobar contra el repo privado).

| Tabla (relativa a tools/yo2emu)        | De dónde sale                                                            |
|----------------------------------------|--------------------------------------------------------------------------|
| research/ia/cards_yo2.csv              | bin#/card_* (extraer_juego.py) + cards.cdb; research/ia/build_ia_tables  |
|                                        | (load_cards + link_passcodes + write_cards_csv) + MANUAL (2 filas a mano)|
| research/ia/npc_decks.json             | cardpack/100NN.ydc (receta de la NPC NN), common/yo2pbinmap.dat (bits de |
|                                        | premio), avatar/npc/fuki_index.csv (huecos de diálogo vacíos), nombres   |
|                                        | de npc_ids.json (nuestros); build_ia_tables.main                          |
| research/ia/npc_profiles.json          | npc_decks.json + cards.cdb: research/ia/brain.npc_profiles               |
| research/persistencia/valid_cards.json | filas de cards_yo2.csv cuyo dibujo existe en el cliente: data/mini/ Y    |
|                                        | data/card/<id Konami>.png (índice de merge.dat; sin --juego, las carpetas |
|                                        | mini/ y card/ de --datos). B05: el editor de mazos peta con un id sin     |
|                                        | dibujo (por eso falta la 3255, Barbaros Ür: solo tiene mini)              |
| research/persistencia/titles.json      | tabla de títulos de yo2.exe (0xc98d50, FUN_0068fe10: id, bit de PBIN,     |
|                                        | id de texto, subtipo; acaba en bit 0xffffffff) + nombre en español de     |
|                                        | common/yo2text_s (sin espacios a los lados)                               |

Lo que NO se genera aquí: npc_ids.json (nombres y sexo de las NPC: etiquetas NUESTRAS, va con el código, sin los
diálogos del juego) y npc_fuki.json (solo para investigar; el servidor no lo lee).

Idempotente: cada tabla se reescribe solo si cambia (.tmp + os.replace). Salidas: 0 bien; 2 faltan datos; 3 el
cliente no tiene lo esperado (yo2.exe sin la tabla de títulos conocida). Mensajes en inglés (los ve quien hospeda).
Stdlib pura (Python 3.12) + research/ia/build_ia_tables.py y brain.py.
"""
import argparse
import hashlib
import json
import os
import struct
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))            # tools/yo2emu/paquete
EMU = os.path.dirname(AQUI)                                    # tools/yo2emu
TOOLS = os.path.dirname(EMU)                                   # tools (kcej_dat.py)
IA = os.path.join(EMU, "research", "ia")
SALIDA = os.path.join(IA, "cards_yo2.csv")                     # (compatibilidad: la ruta de antes)
for _p in (IA, TOOLS):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import build_ia_tables as _b  # noqa: E402

# Tablas generadas, relativas a tools/yo2emu (yo2srv/rutas.py TABLAS_GENERADAS dice lo mismo: la prueba lo compara)
TABLAS = ("research/ia/cards_yo2.csv", "research/ia/npc_decks.json", "research/ia/npc_profiles.json",
          "research/persistencia/valid_cards.json", "research/persistencia/titles.json")
NPC_IDS = "npc_ids.json"                                       # nuestro (va con el código): nombres de las NPC

# Filas corregidas a mano en el cards_yo2.csv del repo (el cruce automático no las encuentra): id YO2 -> (passcode, match)
MANUAL = {
    139: (68638985, "manual+texto (1.00)"),       # Frog the Jam
    1261: (80863132, "manual+texto (1.00)"),      # Null and Void (Negación)
}
NECESITA = ("bin#/card_prop.bin", "bin#/card_name_e.bin", "bin#/card_indx_e.bin", "bin#/card_name_s.bin",
            "bin#/card_indx_s.bin", "bin#/card_max.bin", "bin#/card_pass.bin", "common/yo2pbinmap.dat",
            "avatar/npc/fuki_index.csv", "common/yo2text_s.idx", "common/yo2text_s.bin")

# Tabla de títulos de yo2.exe Ver.080912 (VA 0xc98d50): registros de 4 × u32 (id del título = registro +0x112, bit de
# PBIN, id de texto, subtipo). Se busca por sus dos primeros registros (no por la dirección: así vale aunque el exe
# tenga otra cabecera) y se comprueba con el sha256 de la tabla entera (112 registros).
FIRMA_TITULOS = struct.pack("<8I", 0, 224, 9100, 7600, 1, 225, 9150, 7601)
TITULOS_N = 112
TITULOS_SHA = "7dc720cbfa611ddd0d1b550eef1ad86ae5b6065d366686862746065230461037"
TITULOS_FUENTE = ("tabla 0xc98d50 de yo2.exe (FUN_0068fe10): [id del registro +0x112, bit de PBIN, nombre en español]; "
                  "sacada de research/audit/work_perfil_progreso/title_table.tsv y titles_es.txt")


# ------------------------------------------------------------------------------------------------------------ apoyo
def _crlf(texto):
    """Los .json del repo se escribieron en Windows en modo texto: saltos CRLF. Así sale igual en cualquier sistema."""
    return texto.replace("\n", "\r\n").encode("utf-8")


def _escribir(ruta, datos):
    """'written' o 'already up to date' (atómico: .tmp + os.replace; no toca el fichero si no cambia)."""
    try:
        with open(ruta, "rb") as f:
            if f.read() == datos:
                return "already up to date"
    except OSError:
        pass
    os.makedirs(os.path.dirname(os.path.abspath(ruta)), exist_ok=True)
    tmp = ruta + ".tmp"
    with open(tmp, "wb") as f:
        f.write(datos)
    os.replace(tmp, ruta)
    return "written"


def _cargar(datos):
    """build_ia_tables.load_cards sin los nombres japoneses (cards_yo2.csv no los lleva y no se extraen)."""
    _b.DATA, _b.BIN = datos, os.path.join(datos, "bin#")
    prop = open(os.path.join(_b.BIN, "card_prop.bin"), "rb").read()
    names_e, names_s = _b.utf16_table("e", 0), _b.utf16_table("s", 0)
    cmax = _b.u16s(os.path.join(_b.BIN, "card_max.bin"))
    cards = []
    for i in range(len(prop) // 8):
        a, b = _b.struct.unpack_from("<II", prop, i * 8)
        cards.append({
            "yo2_id": i, "konami_id": a & 0x3FFF, "atk": ((a >> 14) & 0x1FF) * 10, "def": ((a >> 23) & 0x1FF) * 10,
            "kind": b & 0x3F, "attr": (b >> 6) & 0xF, "level": (b >> 10) & 0xF, "icon": (b >> 14) & 7,
            "race": (b >> 17) & 0x1F, "rest": b >> 22,
            "name_en": names_e[i] if i < len(names_e) else "", "name_es": names_s[i] if i < len(names_s) else "",
            "limit": cmax[i] if i < len(cmax) else 0,
        })
    return cards


class _Textos:
    """common/yo2text_s.idx (u32 = offset de cada id) + .bin (cadenas cp1252 acabadas en 0): como yo2text.py."""

    def __init__(self, datos):
        c = os.path.join(datos, "common")
        idx = open(os.path.join(c, "yo2text_s.idx"), "rb").read()
        self.bin = open(os.path.join(c, "yo2text_s.bin"), "rb").read()
        self.off = struct.unpack(f"<{len(idx) // 4}I", idx)

    def get(self, tid):
        if tid <= 0 or tid >= len(self.off) or not self.off[tid]:
            return ""
        o = self.off[tid]
        fin = self.bin.find(b"\0", o)
        return self.bin[o:fin if fin >= 0 else None].decode("cp1252", "replace")


# ----------------------------------------------------------------------------------------------------------- tablas
def tabla_cartas(datos, cdb):
    """(cartas con passcode, bytes de cards_yo2.csv)."""
    _b.CDB = cdb
    cards = _cargar(datos)
    _b.link_passcodes(cards)
    for c in cards:
        if c["yo2_id"] in MANUAL and not c["passcode"]:
            c["passcode"], c["match"] = MANUAL[c["yo2_id"]]
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    try:
        _b.write_cards_csv(cards, tmp)          # csv.writer: filas con \r\n (lineterminator por defecto)
        return cards, open(tmp, "rb").read()
    finally:
        os.remove(tmp)


def tabla_npc_decks(datos, cards, npc_ids):
    """npc_decks.json: build_ia_tables.main (la parte de los mazos), con los datos de --datos."""
    _b.DATA = datos
    try:
        info = {d["id"]: d for d in json.load(open(npc_ids, encoding="utf-8"))}
    except OSError:
        info = {}
    prizes = _b.pbin_npc()
    by_id = {c["yo2_id"]: c for c in cards}
    texts, _faces = _b.load_fuki()
    decks = {}
    for nid in range(1, 99):
        d = {"npc": nid, "nombre": info.get(nid, {}).get("name", ""), "premios_pbin": prizes.get(nid, {})}
        ydc = os.path.join(datos, "cardpack", f"{10000 + nid}.ydc")
        if os.path.exists(ydc):
            main_, side, extra = _b.read_ydc(ydc)
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
    return _crlf(json.dumps(decks, ensure_ascii=False, indent=1))


def tabla_npc_profiles(decks_bytes, cdb):
    """npc_profiles.json: research/ia/brain.npc_profiles sobre el npc_decks.json recién hecho (como `brain.py perfiles`)."""
    import tempfile
    from pathlib import Path
    import brain
    fd, tmp = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            f.write(decks_bytes)
        profs = brain.npc_profiles(Path(tmp), brain.CardDB(Path(cdb)))
    finally:
        os.remove(tmp)
    return _crlf(json.dumps(profs, ensure_ascii=False, indent=1))


def arte_de_cartas(datos, juego=None):
    """(nombres de data/mini/*.png, nombres de data/card/*.png) en minúsculas: del índice de merge.dat si hay --juego
    (sin extraer nada), si no, de las carpetas mini/ y card/ de --datos."""
    if juego:
        import kcej_dat
        mini, card = set(), set()
        for nombre, _o, _t, _g in kcej_dat.read_toc(os.path.join(juego, "merge.dat")):
            n = nombre.replace("\\", "/").lower()
            if n.startswith("data/mini/") and n.count("/") == 2:
                mini.add(n[10:])
            elif n.startswith("data/card/") and n.count("/") == 2:
                card.add(n[10:])
        return mini, card
    lista = lambda d: {x.lower() for x in os.listdir(os.path.join(datos, d))} if os.path.isdir(os.path.join(datos, d)) else set()  # noqa: E731
    return lista("mini"), lista("card")


def tabla_valid_cards(cards, mini, card):
    """Ids YO2 de las filas de cards_yo2.csv (konami_id != 0) con dibujo en mini/ Y card/ (B05)."""
    v = [c["yo2_id"] for c in cards if c["konami_id"]
         and f"{c['konami_id']:04d}.png" in mini and f"{c['konami_id']:04d}.png" in card]
    return json.dumps(v).encode("ascii")


def tabla_titulos(juego, datos):
    """(código, bytes o mensaje): titles.json desde la tabla de yo2.exe y los nombres de yo2text_s."""
    exe = open(os.path.join(juego, "yo2.exe"), "rb").read()
    i = exe.find(FIRMA_TITULOS)                 # (la tabla sale ~200 veces en el exe, una por cada .obj que incluye su
    if i < 0:                                    # cabecera: todas iguales; vale la primera, el sha256 lo comprueba)
        return 3, "the title table was not found in yo2.exe (modified or different game build?)"
    regs = []
    for k in range(1000):
        tid, bit, texto, sub = struct.unpack_from("<4I", exe, i + 16 * k)
        if bit == 0xFFFFFFFF:
            break
        regs.append((tid, bit, texto, sub))
    tabla = exe[i:i + 16 * len(regs)]
    if len(regs) != TITULOS_N or hashlib.sha256(tabla).hexdigest() != TITULOS_SHA:
        return 3, f"the title table in yo2.exe is not the expected one ({len(regs)} titles; Ver.080912 has {TITULOS_N})"
    t = _Textos(datos)
    filas = [json.dumps([tid, bit, t.get(texto).strip()], ensure_ascii=False) for tid, bit, texto, _ in regs]
    txt = "{\"_fuente\": " + json.dumps(TITULOS_FUENTE, ensure_ascii=False) + ",\n \"titles\": [\n  " \
        + ",\n  ".join(filas) + "\n]}\n"
    return 0, _crlf(txt)


# ------------------------------------------------------------------------------------------------------------ todas
def generar(datos, vendor, salida=None, juego=None, destino=None):
    """Genera las 5 tablas. Devuelve (código, texto). 0 = escritas o ya iguales; 2 = faltan datos; 3 = cliente raro.
    `destino` = carpeta tools/yo2emu donde escribirlas (por defecto, la de este script). `salida` (compatibilidad) =
    ruta de cards_yo2.csv. Sin `juego` no se puede hacer titles.json (sale del exe): se avisa y se salta."""
    destino = os.path.abspath(destino or EMU)
    falta = [n for n in NECESITA if not os.path.isfile(os.path.join(datos, *n.split("/")))]
    cdb = os.path.join(vendor, "cards.cdb")
    if not os.path.isfile(cdb):
        falta.append(cdb)
    if juego and not os.path.isfile(os.path.join(juego, "yo2.exe")):
        falta.append(os.path.join(juego, "yo2.exe"))
    if juego and not os.path.isfile(os.path.join(juego, "merge.dat")):
        falta.append(os.path.join(juego, "merge.dat"))
    npc_ids = os.path.join(EMU, NPC_IDS)
    if not os.path.isfile(npc_ids):
        falta.append(npc_ids)
    if falta:
        return 2, ("ERROR: cannot build the server tables, missing: " + ", ".join(falta)
                   + ". Extract the game data again (Settings > Game folder must be the Ver.080912 client).")
    lineas = []
    ruta = lambda rel: os.path.join(destino, *rel.split("/"))  # noqa: E731

    cards, csv_bytes = tabla_cartas(datos, cdb)
    p = os.path.abspath(salida) if salida else ruta(TABLAS[0])
    n = sum(1 for c in cards if c["konami_id"])
    lineas.append(f"Card table: {n} cards -> {p} ({_escribir(p, csv_bytes)})")

    decks = tabla_npc_decks(datos, cards, npc_ids)
    con = sum(1 for d in json.loads(decks).values() if d["ydc"])
    lineas.append(f"NPC decks: {con} of 98 NPCs have a recipe -> {ruta(TABLAS[1])} ({_escribir(ruta(TABLAS[1]), decks)})")
    perf = tabla_npc_profiles(decks, cdb)
    lineas.append(f"NPC AI profiles -> {ruta(TABLAS[2])} ({_escribir(ruta(TABLAS[2]), perf)})")

    mini, card = arte_de_cartas(datos, juego)
    if not mini or not card:
        return 2, "\n".join(lineas + ["ERROR: no card pictures found in the game data (data/mini, data/card)."])
    vc = tabla_valid_cards(cards, mini, card)
    lineas.append(f"Drawable cards: {len(json.loads(vc))} -> {ruta(TABLAS[3])} ({_escribir(ruta(TABLAS[3]), vc)})")

    if not juego:
        lineas.append("Titles: skipped (needs --juego: the table is inside yo2.exe)")
        return 0, "\n".join(lineas)
    rc, tit = tabla_titulos(juego, datos)
    if rc:
        return rc, "\n".join(lineas + [f"ERROR: {tit}. Use an unmodified Ver.080912 client."])
    lineas.append(f"Titles: {TITULOS_N} -> {ruta(TABLAS[4])} ({_escribir(ruta(TABLAS[4]), tit)})")
    return 0, "\n".join(lineas)


def comprobar(destino, referencia):
    """[tablas distintas o que faltan] comparando byte a byte destino/<tabla> con referencia/<tabla>."""
    malas = []
    for rel in TABLAS:
        try:
            a = open(os.path.join(destino, *rel.split("/")), "rb").read()
            b = open(os.path.join(referencia, *rel.split("/")), "rb").read()
        except OSError as e:
            malas.append(f"{rel} ({e})")
            continue
        if a != b:
            malas.append(rel)
    return malas


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Build the server tables that come from the game client (on the host's PC).")
    ap.add_argument("--datos", required=True, help="extracted game data (YO2_JUEGO_DATOS)")
    ap.add_argument("--vendor", required=True, help="folder with cards.cdb (YO2_VENDOR)")
    ap.add_argument("--juego", help="game folder (Ver.080912 client: yo2.exe + merge.dat); needed for titles.json")
    ap.add_argument("--destino", help="tools/yo2emu folder to write the tables into (default: next to this script)")
    ap.add_argument("--comprobar", metavar="REF", help="also compare every table byte by byte with REF/<same path>")
    a = ap.parse_args()
    destino = os.path.abspath(a.destino or EMU)
    rc, texto = generar(os.path.abspath(a.datos), os.path.abspath(a.vendor), juego=a.juego and os.path.abspath(a.juego),
                        destino=destino)
    print(texto)
    if rc == 0 and a.comprobar:
        malas = comprobar(destino, os.path.abspath(a.comprobar))
        print(f"Compared {len(TABLAS)} tables with {a.comprobar}: "
              + ("all byte-identical" if not malas else f"{len(malas)} DIFFERENT: {', '.join(malas)}"))
        rc = 0 if not malas else 3
    return rc


if __name__ == "__main__":
    sys.exit(main())
