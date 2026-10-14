"""Mazos de prueba y política simple del jugador 0 para las pruebas de duelcore."""
from __future__ import annotations

import collections
import sys
import time

# La consola de Windows no es UTF-8 por defecto (acentos de los textos de cartas)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

# Passcodes sacados de _vendor/cards.cdb (monstruos normales + mágicas/trampas clásicas)
ALEXANDRITE, LUSTER, VORSE, WARWOLF, CELTIC, GAMMA, FERAL, SKULL, DMAGICIAN, ELF = (
    43096270, 11091375, 14898066, 69247929, 91152256, 11549357, 41392891, 70781052, 46986414, 15025844)
STONE, WITTY, DOOMED, HITOTSU, MAMMOTH, LEOTRON, MEGALO, GAIA, BLUEEYES, ACID = (
    13039848, 36304921, 49218300, 76184692, 40374923, 47226949, 81823360, 6368038, 89631139, 77568553)
DIAN_KETO, POT, HINOTAMA, SPARKS, FISSURE, RAIGEKI, REBORN, SWORDS, STORM, TRAP_HOLE = (
    84257639, 55144522, 46130346, 76103675, 66788016, 12580477, 83764718, 72302403, 19613556, 4206964)

DECK_A = ([ALEXANDRITE] * 3 + [LUSTER] * 3 + [VORSE] * 3 + [WARWOLF] * 3 + [CELTIC] * 3 + [GAMMA] * 3
          + [FERAL] * 2 + [SKULL] * 2 + [DMAGICIAN] * 2 + [ELF]
          + [DIAN_KETO] * 2 + [POT] + [HINOTAMA] * 2 + [SPARKS] * 2 + [FISSURE, RAIGEKI, REBORN, SWORDS, STORM]
          + [TRAP_HOLE] * 3)
DECK_B = ([STONE] * 3 + [ELF] * 3 + [WITTY] * 3 + [DOOMED] * 3 + [HITOTSU] * 3 + [MAMMOTH] * 3
          + [LEOTRON] * 3 + [MEGALO] * 3 + [GAIA] * 2 + [BLUEEYES] + [ACID]
          + [DIAN_KETO] * 2 + [HINOTAMA] * 2 + [SPARKS] * 2 + [FISSURE, REBORN, POT]
          + [TRAP_HOLE] * 3)
assert len(DECK_A) == 40 and len(DECK_B) == 40

DECKS = [{"main": DECK_A, "extra": []}, {"main": DECK_B, "extra": []}]


def free_places(field_mask: int, count: int):
    """SELECT_PLACE: la máscara marca las zonas NO disponibles, relativa al que elige
    (bits 0-7 sus monstruos, 8-15 sus mágicas/trampas, 16-31 lo mismo del rival)."""
    out = []
    for side, player in ((0, 0), (1, 1)):
        for base, loc in ((0, 4), (8, 8)):
            for seq in range(8):
                if len(out) >= count:
                    return out
                if not field_mask & (1 << (side * 16 + base + seq)):
                    out.append({"player": player, "location": loc, "sequence": seq})
    return out


class SimplePolicy:
    """Jugador 0 «tonto»: invoca la primera carta posible, activa la primera mágica de la mano
    (máx. 2 por turno), ataca con lo primero que pueda y pasa a la End Phase."""

    def __init__(self):
        self.turn = -1
        self.actions = 0
        self.attacks = 0
        self.bp_done = False

    def __call__(self, sel: dict) -> dict:
        if sel.get("turn") != self.turn:
            self.turn, self.actions, self.attacks, self.bp_done = sel.get("turn"), 0, 0, False
        t = sel["type"]
        if t == "SELECT_IDLECMD":
            self.actions += 1
            if self.actions <= 6:
                if sel["summons"]:
                    return {"type": t, "action": "SELECT_SUMMON", "index": 0}
                hand = [i for i, c in enumerate(sel["activates"]) if c["location"] == 2]
                if hand and self.actions <= 3:
                    return {"type": t, "action": "SELECT_ACTIVATE", "index": hand[0]}
                if sel["monster_sets"]:
                    return {"type": t, "action": "SELECT_MONSTER_SET", "index": 0}
            if sel["to_bp"] and not self.bp_done:
                self.bp_done = True
                return {"type": t, "action": "TO_BP", "index": None}
            if sel["to_ep"]:
                return {"type": t, "action": "TO_EP", "index": None}
            return {"type": t, "action": "TO_BP", "index": None}
        if t == "SELECT_BATTLECMD":
            self.attacks += 1
            if sel["attacks"] and self.attacks <= 5:
                return {"type": t, "action": "SELECT_BATTLE", "index": 0}
            return {"type": t, "action": "TO_EP" if sel["to_ep"] else "TO_M2", "index": None}
        if t == "SELECT_CHAIN":
            return {"type": t, "index": 0 if (sel["forced"] and sel["selects"]) else None}
        if t in ("SELECT_EFFECTYN", "SELECT_YESNO"):
            return {"type": t, "yes": True}
        if t == "SELECT_OPTION":
            return {"type": t, "index": 0}
        if t == "SELECT_CARD":
            n = min(max(1, sel["min"]), sel["max"], len(sel["selects"]))
            return {"type": t, "indicies": list(range(n))}
        if t == "SELECT_TRIBUTE":
            return {"type": t, "indicies": list(range(sel["min"]))}
        if t == "SELECT_UNSELECT_CARD":
            if sel["can_finish"] or not sel["select_cards"]:
                return {"type": t, "index": None}
            return {"type": t, "index": 0}
        if t in ("SELECT_PLACE", "SELECT_DISFIELD"):
            return {"type": t, "places": free_places(sel["field_mask"], sel["count"])}
        if t == "SELECT_POSITION":
            pos = next(p for p in (1, 8, 4, 2) if sel["positions"] & p)
            return {"type": t, "position": pos}
        if t in ("SORT_CARD", "SORT_CHAIN"):
            return {"type": "SORT_CARD", "order": None}
        if t == "ROCK_PAPER_SCISSORS":
            return {"type": t, "value": 1}
        if t == "ANNOUNCE_NUMBER":
            return {"type": t, "value": 0}
        return None  # sin política propia: que conteste bot.ts (cmd "auto")


def play_duel(dc, duel_id, seed, first=0, max_turns=60, log=None, timeout=30.0, rule="MR5"):
    """Juega un duelo entero: jugador 0 = SimplePolicy, jugador 1 = bot.ts. Devuelve un resumen."""
    pol = SimplePolicy()
    counts = collections.Counter()
    selects = collections.Counter()
    t0 = time.perf_counter()
    lat = []
    dc.new(duel_id, DECKS, first=first, seed=seed, bot=(False, True), rule=rule)
    first_turn_player = None
    autos = 0
    while True:
        ts = time.perf_counter()
        evs = dc.wait(duel_id, timeout=timeout)
        lat.append(time.perf_counter() - ts)
        for ev in evs:
            if log is not None:
                log.append(ev)
            if ev["ev"] == "msg":
                counts[ev["type"]] += 1
                if ev["type"] == "NEW_TURN" and first_turn_player is None:
                    first_turn_player = ev["player"]
        last = evs[-1]
        if last["ev"] == "error":
            raise RuntimeError(f"duelo {duel_id}: {last['error']}")
        if last["ev"] == "end":
            break
        selects[last["type"]] += 1
        if last["turn"] > max_turns:
            dc.end(duel_id)
            last = dc.wait(duel_id, timeout=timeout)[-1]
            break
        resp = pol(last)
        if resp is None:
            autos += 1
            dc.auto(duel_id)
        else:
            dc.respond(duel_id, resp)
    return {
        "id": duel_id, "seed": seed, "first": first, "first_turn_player": first_turn_player,
        "winner": last.get("winner"), "reason": last.get("reason"), "turns": last.get("turns"),
        "lp": last.get("lp"), "decisions": last.get("decisions"),
        "human_retries": last.get("human_retries"), "auto_passed": last.get("auto_passed"),
        "auto_responses": autos, "core_errors": last.get("core_errors"),
        "secs": time.perf_counter() - t0, "max_wait_ms": max(lat) * 1000, "avg_wait_ms": sum(lat) / len(lat) * 1000,
        "msgs": counts, "selects": selects,
    }
