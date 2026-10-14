"""Parche de MSG_SELECT_SUM (parche_ocgcore.mts): ritual completo contra el motor y la causa del fallo.

    python test_parche_sum.py

1. Con el parche: Black Luster Ritual (al menos 8) con Black Luster Soldier y dos de nivel 4 en la mano: el SELECT_SUM
   trae las 4 candidatas de la mano con su nivel, `select_max` = 1 (al menos), y la respuesta con dos índices invoca a
   BLS sin RETRY. Igual con Advanced Ritual Art (suma EXACTA, `select_max` = 0, materiales del mazo) empezando el
   jugador 1 (comprueba que el primer byte es el jugador y el segundo el modo).
2. Sin el parche (YO2_PARCHE_SUM=0, proceso aparte): el lector de ocgcore-wasm 0.1.2 da `selects` vacío y
   `selects_must` con basura (lee primero las obligatorias como «selects» y cada carta con 14 B en vez de 18).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from duelcore import DuelCore  # noqa: E402
from pruebas_comun import free_places  # noqa: E402

BLR, ARA, BLS, CELTIC, ELF, SKULL = 55761792, 46052429, 5405694, 91152256, 15025844, 70781052


def ritual(dc, did, spell, first, materials_idx=(0, 1), hand=None):
    """Juega hasta el SELECT_SUM del ritual y lo contesta. -> (select_sum, eventos tras la respuesta)."""
    main = [ELF] * 35 + (hand or [spell, BLS, CELTIC, ELF, CELTIC])[::-1]   # sin barajar: lo último es la cima
    decks = [{"main": main}, {"main": [ELF] * 40}]
    dc.new(did, decks, first=first, seed=7, shuffle=False, rule="MR1")
    sumev, after = None, []
    for _ in range(60):
        evs = dc.wait(did)
        last = evs[-1]
        if sumev is not None:
            after += evs
            if last["ev"] != "select" or last["type"] == "SELECT_IDLECMD":
                return sumev, after
        elif last["ev"] != "select":
            return None, evs
        t = last["type"]
        if t == "SELECT_IDLECMD":
            i = next((k for k, a in enumerate(last["activates"]) if a["code"] == spell), None)
            if i is None:
                dc.respond(did, {"type": t, "action": "TO_EP", "index": None})
            else:
                dc.respond(did, {"type": t, "action": "SELECT_ACTIVATE", "index": i})
        elif t == "SELECT_PLACE":
            dc.respond(did, {"type": t, "places": free_places(last["field_mask"], last["count"])})
        elif t == "SELECT_CARD":
            dc.respond(did, {"type": t, "indicies": [0]})
        elif t == "SELECT_CHAIN":
            dc.respond(did, {"type": t, "index": None})
        elif t == "SELECT_POSITION":
            dc.respond(did, {"type": t, "position": 1})
        elif t == "SELECT_SUM":
            sumev = last
            if not last["selects"]:
                return sumev, []
            if materials_idx is None:
                dc.auto(did)                   # decide el bot de duelcore (elegirSuma)
            else:
                dc.respond(did, {"type": t, "indicies": list(materials_idx)})
        else:
            dc.auto(did)
    return sumev, after


def main():
    ok = True

    def check(cond, msg):
        nonlocal ok
        ok &= bool(cond)
        print(("  ok  " if cond else "  MAL ") + msg)

    with DuelCore() as dc:
        check(dc.ready.get("parche_sum") is True, f"ready con parche_sum: {dc.ready.get('parche_sum')}")
        s, after = ritual(dc, "blr", BLR, 0)
        sel = (s or {}).get("selects") or []
        check(s is not None and s["amount"] == 8 and s["select_max"] == 1 and s["selects_must"] == [],
              f"BLR: amount 8, al menos (select_max 1), sin obligatorias: {s and {k: s[k] for k in ('amount', 'select_max', 'min', 'max')}}")
        check([(c["code"], c["location"], c["amount"]) for c in sel] ==
              [(CELTIC, 2, 4), (ELF, 2, 4), (CELTIC, 2, 4), (ELF, 2, 4)],
              f"BLR: las 4 candidatas de la mano con su nivel: {[(c['code'], c['location'], c['amount']) for c in sel]}")
        check(all("position" in c for c in sel), "cada candidata con su position (loc_info entero)")
        sp = [e for e in after if e.get("type") == "SPSUMMONING" and e.get("code") == BLS]
        check(sp and not any(e.get("retry") for e in after), "BLR: la respuesta [0, 1] invoca a BLS sin RETRY")
        s2, after2 = ritual(dc, "ara", ARA, 1)
        check(s2 is not None and s2["player"] == 0 and s2["select_max"] == 0 and s2["min"] >= 1
              and all(c["location"] == 1 for c in s2["selects"]),
              f"ARA (empieza el 1): jugador 0, suma exacta (select_max 0), candidatas del mazo: "
              f"{s2 and (s2['player'], s2['select_max'], s2['min'], s2['max'], len(s2['selects']))}")
        sp2 = [e for e in after2 if e.get("type") == "SPSUMMONING" and e.get("code") == BLS]
        check(sp2, "ARA: BLS invocado")
        # «al menos 8» sin suma exacta (Summoned Skull 6 + Celtic 4): bot.ts no sabe; elegirSuma sí
        s3, after3 = ritual(dc, "auto", BLR, 0, materials_idx=None, hand=[BLR, BLS, SKULL, CELTIC, BLR])
        sp3 = [e for e in after3 if e.get("type") == "SPSUMMONING" and e.get("code") == BLS]
        err3 = [e for e in after3 if e.get("ev") == "error"]
        check(s3 is not None and sp3 and not err3 and not any(e.get("retry") for e in after3),
              f"auto (bot de duelcore) con «al menos» sin suma exacta: BLS invocado {len(sp3)}, errores {err3[:1]}")
    os.environ["YO2_PARCHE_SUM"] = "0"
    try:
        with DuelCore() as dc:
            check(dc.ready.get("parche_sum") is False, "sin parche: parche_sum false")
            s, _ = ritual(dc, "roto", BLR, 0)
            bad = (s or {}).get("selects_must") or []
            check(s is not None and s["selects"] == [] and len(bad) == 4
                  and [c["code"] for c in bad] != [CELTIC, ELF, CELTIC, ELF],
                  f"sin parche (la causa): selects vacío y selects_must con basura {[c['code'] for c in bad][:4]}")
            dc.end("roto")
    finally:
        os.environ.pop("YO2_PARCHE_SUM", None)
    print("RESULTADO:", "OK" if ok else "FALLOS")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
