"""CardMap: id de carta de YO2 <-> passcode de ocgcore.

Tres numeraciones (comprobado en el exe, ver NOTES.md §3):
  - id YO2 (1..0xcb7): el que viaja por la red (mazo 0x6601, 0x6202 tipo 0x40 P3, 0x2f P0, .ydc). Es el ÍNDICE
    en card_prop.bin. FUN_0073bc50(id) = card_prop[id*8] & 0x3fff.
  - id interno (3000..7790, p. ej. 4007 = Blue-Eyes): el que guarda el motor en la palabra de cada carta y el que
    comparan sus reglas (0x1740, 0x1dd9...). Lo esperan 0x48 (ficha) P1, 0x25/0x24 P2, 0x30 P0 y la lista de
    diálogo con clase 4. FUN_0073bbe0(interno) -> id YO2.
  - passcode de ocgcore (89631139 = Blue-Eyes).

Fuente del nombre inglés: research/ia/cards_yo2.csv (lo genera otro agente) si existe; si no, card_name_e.bin, que va
en el mismo orden que card_prop.bin. El cruce con _vendor/cards.cdb reutiliza tools/build_cards.py (norm() y RENAMES).
NO se usa card_pass.bin (solo acierta 562 de 1.884; ver build_cards.py).
"""
from __future__ import annotations

import csv
import os
import sqlite3
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", "..", ".."))       # .../DuelCards
CSV_DEFAULT = os.path.join(ROOT, "tools", "yo2emu", "research", "ia", "cards_yo2.csv")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import build_cards as _bc                                                     # noqa: E402  (reutilizado, no copiado)

TYPE_MONSTER, TYPE_SPELL, TYPE_TRAP, TYPE_NORMAL, TYPE_TOKEN = 0x1, 0x2, 0x4, 0x10, 0x4000


def _read_csv(path, with_passcodes=False):
    """CSV del otro agente (research/ia/cards_yo2.csv: yo2_id, konami_id, name_en, ..., passcode, match).
    Acepta cabeceras flexibles: id|yo2_id y name|name_en. Con with_passcodes devuelve también
    {id: (passcode, confianza)}: 'nombre' = 1.0; 'parecido+stats (0.88)' = 0.88."""
    out, pcs = {}, {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return out
    keys = {k.lower().strip(): k for k in rows[0].keys() if k}
    kid = next((keys[k] for k in ("yo2_id", "id_yo2", "id", "yo2", "index") if k in keys), None)
    kname = next((keys[k] for k in ("name_en", "name_e", "nombre_en", "english", "name", "nombre") if k in keys), None)
    if not kid or not kname:
        raise ValueError(f"{path}: no encuentro columnas de id y nombre en {list(keys)}")
    for r in rows:
        try:
            i = int(str(r[kid]).strip(), 0)
        except ValueError:
            continue
        out[i] = (r[kname] or "").strip()
        pc = str(r.get("passcode") or "").strip()
        if pc.isdigit() and int(pc):
            m = str(r.get("match") or "")
            conf = 1.0 if m == "nombre" else float(m.rsplit("(", 1)[1].rstrip(")")) if "(" in m else 0.0
            pcs[i] = (int(pc), conf)
    return (out, pcs) if with_passcodes else out


class CardMap:
    def __init__(self, csv_path: str | None = CSV_DEFAULT, cdb_path: str | None = None, min_conf: float = 0.85):
        """min_conf: confianza mínima para aceptar un passcode del CSV que no salió por nombre exacto
        (sus 'card_pass+stats' van de 0.53 a 0.80: por debajo de 0.85 se ignoran)."""
        prop = open(os.path.join(_bc.BIN, "card_prop.bin"), "rb").read()
        self.internal = [struct.unpack_from("<I", prop, i * 8)[0] & 0x3FFF for i in range(len(prop) // 8)]
        self.source = "card_name_e.bin"
        names = _bc.utf16_table("card_name_e.bin", "card_indx_e.bin", 0)
        self.names = {i: names[i] for i in range(1, min(len(names), len(self.internal)))}
        csv_pcs = {}
        if csv_path and os.path.exists(csv_path):
            from_csv, csv_pcs = _read_csv(csv_path, with_passcodes=True)
            if from_csv:
                self.names.update({i: n for i, n in from_csv.items() if n})
                self.source = os.path.relpath(csv_path, ROOT)
        db = sqlite3.connect(cdb_path or _bc.CDB)
        by_name = {}
        self.cdb_type, self.cdb_alias, self.cdb_stats = {}, {}, {}
        for code, name, alias, typ, atk, def_ in db.execute(
                "select d.id, t.name, d.alias, d.type, d.atk, d.def from datas d join texts t on t.id = d.id "
                "order by d.alias != 0"):
            by_name.setdefault(_bc.norm(name), code)
            self.cdb_type[code], self.cdb_alias[code] = typ, alias
            self.cdb_stats[code] = (atk, def_)          # ATK/DEF impresos (para no reenviar lo que el cliente ya pinta)
        for old, new in _bc.RENAMES.items():
            c = by_name.get(_bc.norm(new))
            if c:
                by_name[_bc.norm(old)] = c
        self._y2c, self._c2y, self.missing, self.fuzzy = {}, {}, [], []
        for i, name in self.names.items():
            if not name or not self.internal[i]:
                continue
            code = by_name.get(_bc.norm(name))
            if not code and i in csv_pcs and csv_pcs[i][1] >= min_conf and csv_pcs[i][0] in self.cdb_type:
                code = csv_pcs[i][0]
                self.fuzzy.append((i, name, code, csv_pcs[i][1]))
            if not code:
                self.missing.append((i, name))
                continue
            self._y2c[i] = code
            self._c2y.setdefault(code, i)            # varias ids YO2 con el mismo nombre: la primera
        self._int2y = {}
        for i, v in enumerate(self.internal):
            if v and i:
                self._int2y.setdefault(v, i)
        self.placeholders = self._pick_placeholders()

    # -- conversiones ---------------------------------------------------------------------------------------
    def yo2_to_code(self, yo2_id: int) -> int | None:
        return self._y2c.get(yo2_id)

    def code_to_yo2(self, code: int) -> int | None:
        """passcode -> id YO2. Si el passcode es un arte alternativo, prueba con su alias."""
        y = self._c2y.get(code)
        if y is None and self.cdb_alias.get(code):
            y = self._c2y.get(self.cdb_alias[code])
        return y

    def yo2_to_internal(self, yo2_id: int) -> int:
        return self.internal[yo2_id] if 0 <= yo2_id < len(self.internal) else 0

    def internal_to_yo2(self, internal: int) -> int | None:
        return self._int2y.get(internal)

    def stats(self, code: int):
        """(ATK, DEF) impresos según cards.cdb, o None."""
        return self.cdb_stats.get(code) or self.cdb_stats.get(self.cdb_alias.get(code, 0))

    def kind(self, code: int) -> str:
        """'monster' | 'spell' | 'trap' según cards.cdb (para elegir carta tapadera)."""
        t = self.cdb_type.get(code) or self.cdb_type.get(self.cdb_alias.get(code, 0), 0)
        return "trap" if t & TYPE_TRAP else "spell" if t & TYPE_SPELL else "monster"

    def _pick_placeholders(self):
        """Cartas tapadera para lo que el cliente no debe ver (mano/mazo/boca abajo de la NPC):
        un monstruo normal (sin reglas propias en el motor del cliente) y una mágica y una trampa cualquiera."""
        ph = {}
        for i in sorted(self._y2c):
            t = self.cdb_type.get(self._y2c[i], 0)
            if "monster" not in ph and t == TYPE_MONSTER | TYPE_NORMAL:
                ph["monster"] = i
            elif "spell" not in ph and t == TYPE_SPELL:
                ph["spell"] = i
            elif "trap" not in ph and t == TYPE_TRAP:
                ph["trap"] = i
        return ph

    def coverage(self):
        total = sum(1 for i, n in self.names.items() if n and self.internal[i])
        return {"fuente": self.source, "ids_con_nombre": total, "enlazadas": len(self._y2c),
                "por_nombre_exacto": len(self._y2c) - len(self.fuzzy), "por_parecido_csv": len(self.fuzzy),
                "sin_enlazar": len(self.missing), "passcodes_distintos": len(self._c2y)}


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    cm = CardMap()
    print(cm.coverage())
    print("tapaderas:", {k: (v, cm.names[v]) for k, v in cm.placeholders.items()})
    for code in (89631139, 46986414, 83764718, 4206964):
        y = cm.code_to_yo2(code)
        print(code, "->", y, cm.names.get(y), "interno", cm.yo2_to_internal(y or 0))
    print("ejemplos sin enlazar:", cm.missing[:15])
