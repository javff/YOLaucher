"""Prueba 1: un duelo completo. Jugador 0 = política simple (Python), jugador 1 = bot.ts.

    python test_duelo.py [semilla] [first]
"""
import sys
import time

from duelcore import DuelCore
from pruebas_comun import play_duel

PHASES = {1: "DP", 2: "SP", 4: "M1", 8: "BP", 16: "BS", 32: "DS", 64: "DC", 128: "BE", 256: "M2", 512: "EP"}


def main():
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 1234
    first = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    t0 = time.perf_counter()
    with DuelCore() as dc:
        print(f"duelcore listo en {time.perf_counter() - t0:.2f}s ({dc.ready['cards']} cartas, carga del motor {dc.ready['load_ms']} ms)")
        names = {c["code"]: c.get("name", "?") for c in dc.cardinfo(
            {43096270, 11091375, 14898066, 69247929, 91152256, 11549357, 41392891, 70781052, 46986414, 15025844,
             13039848, 36304921, 49218300, 76184692, 40374923, 47226949, 81823360, 6368038, 89631139, 77568553,
             84257639, 55144522, 46130346, 76103675, 66788016, 12580477, 83764718, 72302403, 19613556, 4206964})}
        log = []
        r = play_duel(dc, "duelo-1", seed, first=first, max_turns=60, log=log)

    # Relato corto: turnos, invocaciones, ataques, daño
    for ev in log:
        if ev["ev"] != "msg":
            continue
        t = ev["type"]
        if t == "NEW_TURN":
            print(f"-- turno del jugador {ev['player']}")
        elif t in ("SUMMONING", "SPSUMMONING"):
            print(f"   J{ev['controller']} invoca {names.get(ev['code'], ev['code'])}")
        elif t == "CHAINING":
            print(f"   J{ev['triggering_controller']} activa {names.get(ev['code'], ev['code'])}")
        elif t == "ATTACK":
            tgt = "directo" if ev["target"] is None else f"zona {ev['target']['sequence']}"
            print(f"   J{ev['card']['controller']} ataca ({tgt})")
        elif t == "DAMAGE":
            print(f"   J{ev['player']} recibe {ev['amount']} de daño")
        elif t == "WIN":
            print(f"   WIN jugador {ev['player']} (razón {ev['reason']})")

    print("\n== resumen ==")
    for k in ("seed", "first", "first_turn_player", "winner", "reason", "turns", "lp", "decisions",
              "human_retries", "auto_passed", "auto_responses", "core_errors"):
        print(f"{k:18} {r[k]}")
    print(f"{'tiempo':18} {r['secs']:.2f}s (espera media por decisión {r['avg_wait_ms']:.1f} ms, máx {r['max_wait_ms']:.1f} ms)")
    print("mensajes:", dict(r["msgs"].most_common()))
    print("decisiones del J0:", dict(r["selects"].most_common()))
    ok = r["human_retries"] == 0 and r["winner"] is not None
    print("OK" if ok else "AVISO: el duelo no acabó con ganador o hubo respuestas rechazadas")


if __name__ == "__main__":
    main()
