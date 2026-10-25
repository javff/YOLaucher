"""Utilidades para los duelos dirigidos de test_errata_2008.py: una partida con mazos SIN barajar (la mano son las 5
primeras cartas de la lista) y una política escrita a mano para el jugador 0 (el humano del protocolo).

Los mazos se dan «de arriba abajo»: main[0:5] es la mano inicial, main[5] el primer robo, etc.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from pruebas_comun import free_places  # noqa: E402

ELF = 15025844          # Mystical Elf (relleno: monstruo de 800/2000)
LOC = {"mano": 2, "mazo": 1, "mzone": 4, "szone": 8, "tumba": 16, "desterradas": 32}
PARAR = "PARAR"


class Partida:
    def __init__(self, dc, did, main0, main1=None, first=0, seed=5, rule="MR5", relleno=ELF):
        self.dc, self.did = dc, did
        self.eventos: list[dict] = []        # todos los eventos (sin los select), en orden
        self.selects: list[dict] = []
        self.fin: dict | None = None
        relleno0 = [relleno] * (40 - len(main0))
        main1 = main1 if main1 is not None else [relleno] * 40
        # sin barajar el último de la lista es la cima: se da la vuelta
        dc.new(did, [{"main": (list(main0) + relleno0)[::-1]}, {"main": list(main1)[::-1]}],
               first=first, seed=seed, shuffle=False, rule=rule)

    def jugar(self, politica, max_pasos=400, hasta=None):
        """Bucle: politica(sel, partida) -> respuesta (dict) | 'auto' | PARAR. Termina con PARAR o al acabar el duelo."""
        for _ in range(max_pasos):
            evs = self.dc.wait(self.did)
            for e in evs:
                if e["ev"] == "select":
                    self.selects.append(e)
                else:
                    self.eventos.append(e)
            last = evs[-1]
            if last["ev"] != "select":
                self.fin = last
                return last
            r = politica(last, self)
            if r == PARAR:
                return last
            if r is None or r == "auto":
                self.dc.auto(self.did)
            else:
                self.dc.respond(self.did, r)
        raise AssertionError("la política no paró en %d pasos" % max_pasos)

    # -- consultas sobre lo ocurrido -----------------------------------------------------------------------------
    def msgs(self, tipo, desde=0):
        return [e for e in self.eventos[desde:] if e.get("type") == tipo]

    def moves(self, code, desde=0):
        return [(e["from"]["controller"], e["from"]["location"], e["to"]["location"], e.get("reason"))
                for e in self.eventos[desde:] if e.get("type") == "MOVE" and e.get("card") == code]

    def chainings(self, code, desde=0):
        return [e for e in self.eventos[desde:] if e.get("type") == "CHAINING" and e.get("code") == code]

    def por_turno(self):
        """{n.º de turno: [eventos]} (el n.º lo cuentan los NEW_TURN; lo anterior al primero va en el 0)."""
        out, t = {}, 0
        for e in self.eventos:
            if e.get("type") == "NEW_TURN":
                t += 1
            out.setdefault(t, []).append(e)
        return out

    def marca(self):
        return len(self.eventos)

    def cerrar(self):
        try:
            self.dc.end(self.did)
            for _ in range(8):
                if any(e["ev"] == "end" for e in self.dc.wait(self.did)):
                    break
        except Exception:                                             # noqa: BLE001
            pass


# -- respuestas de uso común ------------------------------------------------------------------------------------
def idle(sel, accion, code=None, lista=None, loc=None):
    """Respuesta de SELECT_IDLECMD: accion = SELECT_SUMMON | SELECT_MONSTER_SET | SELECT_ACTIVATE | SELECT_POS_CHANGE |
    TO_BP | TO_EP...; code (y loc) eligen la carta de la lista correspondiente."""
    if accion in ("TO_BP", "TO_EP", "SHUFFLE"):
        return {"type": "SELECT_IDLECMD", "action": accion, "index": None}
    nombre = lista or {"SELECT_SUMMON": "summons", "SELECT_MONSTER_SET": "monster_sets", "SELECT_ACTIVATE": "activates",
                       "SELECT_POS_CHANGE": "pos_changes", "SELECT_SPECIAL_SUMMON": "special_summons",
                       "SELECT_SPELL_SET": "spell_sets"}[accion]
    for i, c in enumerate(sel[nombre]):
        if (code is None or c["code"] == code) and (loc is None or c["location"] == loc):
            return {"type": "SELECT_IDLECMD", "action": accion, "index": i}
    return None


def por_defecto(sel):
    """Respuesta para los select «de trámite»: sitios libres, posición de ataque, no encadenar..."""
    t = sel["type"]
    if t in ("SELECT_PLACE", "SELECT_DISFIELD"):
        return {"type": t, "places": free_places(sel["field_mask"], sel["count"])}
    if t == "SELECT_POSITION":
        pos = next(p for p in (1, 8, 4, 2) if sel["positions"] & p)
        return {"type": t, "position": pos}
    if t == "SELECT_CHAIN":
        return {"type": t, "index": 0 if (sel.get("forced") and sel.get("selects")) else None}
    if t in ("SORT_CHAIN", "SORT_CARD"):
        return {"type": "SORT_CARD", "order": None}
    if t == "SELECT_EFFECTYN" or t == "SELECT_YESNO":
        return {"type": t, "yes": True}
    if t == "SELECT_CARD":
        n = min(max(1, sel["min"]), sel["max"], len(sel["selects"]))
        return {"type": t, "indicies": list(range(n))}
    if t == "SELECT_IDLECMD":
        return {"type": t, "action": "TO_EP", "index": None}
    if t == "SELECT_BATTLECMD":
        return {"type": t, "action": "TO_EP" if sel["to_ep"] else "TO_M2", "index": None}
    return None


def elegir_cartas(sel, codes):
    """SELECT_CARD: los índices de las cartas cuyo código está en codes (en ese orden, sin repetir cartas)."""
    usados, idx = set(), []
    for code in codes:
        for i, c in enumerate(sel["selects"]):
            if c["code"] == code and i not in usados:
                usados.add(i)
                idx.append(i)
                break
    # si no hay ninguna de las pedidas (p. ej. con el script moderno), las primeras que valgan: que el duelo siga
    n = max(1, sel.get("min") or 1)
    for i in range(len(sel["selects"])):
        if len(idx) >= max(n, len(codes)):
            break
        if i not in idx and not idx:
            idx.append(i)
    return {"type": sel["type"], "indicies": idx}


def volcar(partida, desde=0, tipos=None):
    """Para depurar: una línea por evento."""
    for e in partida.eventos[desde:]:
        if tipos and e.get("type") not in tipos:
            continue
        d = {k: v for k, v in e.items() if k not in ("ev", "id", "type_id", "ms")}
        print("   ", json.dumps(d, ensure_ascii=False)[:260])
