"""Prueba 3 (bucle verificador): N duelos con semillas distintas, alternando quién empieza.
Falla si hay un error, un cuelgue (sin evento en 30 s), respuestas rechazadas o el orden
de turnos no respeta `first`. También comprueba que la misma semilla da el mismo duelo.

    python test_bucle.py [n=20] [paralelos=1] [regla=MR5]
"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from duelcore import DuelCore
from pruebas_comun import play_duel


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    par = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    rule = sys.argv[3] if len(sys.argv) > 3 else "MR5"
    fails = []
    results = []
    t0 = time.perf_counter()
    with DuelCore() as dc:
        def one(i):
            seed = 1000 + i * 7919
            first = i % 2
            try:
                r = play_duel(dc, f"b{i}", seed, first=first, max_turns=60, rule=rule)
            except Exception as e:  # error del core o cuelgue
                return i, None, f"{type(e).__name__}: {e}"
            problems = []
            if r["human_retries"]:
                problems.append(f"{r['human_retries']} respuestas rechazadas")
            if r["first_turn_player"] != first:
                problems.append(f"empezó {r['first_turn_player']} en vez de {first}")
            if r["reason"] in ("error",):
                problems.append("terminó por error")
            return i, r, "; ".join(problems)

        with ThreadPoolExecutor(max_workers=par) as ex:
            for i, r, prob in ex.map(one, range(n)):
                if r:
                    results.append(r)
                    print(f"#{i:2} seed={r['seed']:6} first={r['first']} gana={r['winner']} ({r['reason']}) "
                          f"turnos={r['turns']:2} LP={r['lp']} dec={r['decisions']:3} "
                          f"{r['secs']*1000:6.0f} ms máx.espera={r['max_wait_ms']:.0f} ms {('ERROR: ' + prob) if prob else ''}")
                else:
                    print(f"#{i:2} FALLO: {prob}")
                if prob:
                    fails.append((i, prob))

        # Reproducibilidad: misma semilla y mismas respuestas -> mismo resultado
        a = play_duel(dc, "rep-a", 4242, first=0)
        b = play_duel(dc, "rep-b", 4242, first=0)
        same = (a["winner"], a["turns"], a["lp"], a["decisions"]) == (b["winner"], b["turns"], b["lp"], b["decisions"])
        print(f"reproducible con la misma semilla: {same} ({a['winner']}/{a['turns']}/{a['lp']} vs {b['winner']}/{b['turns']}/{b['lp']})")
        if not same:
            fails.append(("rep", "la misma semilla dio duelos distintos"))
        ping = dc.ping()

    total = time.perf_counter() - t0
    wins = [r["winner"] for r in results]
    print(f"\n{len(results)}/{n} duelos completos en {total:.1f}s; ganados J0={wins.count(0)} J1={wins.count(1)} "
          f"cortados={wins.count(None)}; duelos abiertos al final: {ping['duels']}")
    if results:
        print(f"media {sum(r['secs'] for r in results)/len(results)*1000:.0f} ms/duelo, "
              f"espera máx. por decisión {max(r['max_wait_ms'] for r in results):.0f} ms, "
              f"turnos medios {sum(r['turns'] for r in results)/len(results):.1f}")
    print("RESULTADO:", "OK" if not fails and ping["duels"] == 0 else f"FALLOS {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
