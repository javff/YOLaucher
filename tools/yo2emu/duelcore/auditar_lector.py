"""Busca más fallos del lector de mensajes de ocgcore-wasm 0.1.2 (como el de SELECT_SUM).

    python auditar_lector.py [duelos=200] [regla=MR1]

Arranca duelcore con YO2_AUDITAR_LECTOR=1 (parche_ocgcore.mts): por cada mensaje del core, la librería tiene que leer
EXACTAMENTE los bytes que escribió el core. Si un tipo lee menos o más, su lector no casa con el formato de EDOPro y
duelcore lo apunta en stderr («[lector] tipo=… largo=… leido=…»). Juega duelos bot contra bot con los mazos de las NPC
(research/ia/npc_decks.json, todos contra todos al azar) y resume los tipos con discrepancias. Sin red ni ficheros.
"""
import collections
import json
import os
import random
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from duelcore import DuelCore  # noqa: E402

# tipos que parche_ocgcore.mts ya corrige (la librería los sigue leyendo mal, pero el mensaje que sale es el bueno)
PARCHEADOS = {23: "SELECT_SUM", 36: "SHUFFLE_SET_CARD", 50: "MOVE (reason)"}
NPC_DECKS = os.path.join(HERE, "..", "research", "ia", "npc_decks.json")


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    rule = sys.argv[2] if len(sys.argv) > 2 else "MR1"
    decks = json.load(open(NPC_DECKS, encoding="utf-8"))
    pool = [[c for c in d.get("main_passcodes") or [] if c] for d in decks.values()]
    pool = [d for d in pool if len(d) >= 20]
    rnd = random.Random(1)
    os.environ["YO2_AUDITAR_LECTOR"] = "1"
    tipos = collections.Counter()
    t0 = time.time()
    ends = collections.Counter()
    errores = collections.Counter()
    try:
        with DuelCore() as dc:
            names = {}
            for k in range(n):
                a, b = rnd.choice(pool), rnd.choice(pool)
                try:
                    dc.new(k, [{"main": a}, {"main": b}], first=k % 2, seed=1000 + k, bot=[True, True], rule=rule)
                    evs = dc.wait(k, timeout=60)
                except Exception as e:                       # noqa: BLE001
                    ends["excepción"] += 1
                    print("duelo", k, "excepción:", e)
                    continue
                for e in evs:
                    if e.get("ev") == "msg":
                        tipos[e.get("type_id")] += 1
                        names[e.get("type_id")] = e.get("type")
                last = evs[-1]
                ends[last.get("reason") if last.get("ev") == "end" else last.get("ev")] += 1
                if last.get("ev") == "error" or last.get("reason") == "error":
                    errores[str(last.get("error"))[:120]] += 1
            time.sleep(0.5)
            lines = [x for x in dc.stderr_tail if x.startswith("[lector")]
    finally:
        os.environ.pop("YO2_AUDITAR_LECTOR", None)
    print(f"{n} duelos ({rule}) en {time.time() - t0:.0f} s; fines: {dict(ends)}")
    print(f"tipos de mensaje vistos: {len(tipos)}: {sorted(names.get(t, t) for t in tipos)}")
    for e, c in errores.most_common(5):
        print(f"  error de duelcore ({c}x): {e}")
    malos = collections.defaultdict(list)
    parcheados = collections.Counter()
    leidos = sorted(int(x.split("=")[1]) for x in lines if x.startswith("[lector-visto]"))
    print(f"tipos que ha leído la librería (también las decisiones de los bots): {len(leidos)}: {leidos}")
    for ln in lines:
        m = re.match(r"\[lector\] tipo=(\d+) largo=(\d+) leido=(-?\d+)", ln)
        if m:
            t, ln_, rd = map(int, m.groups())
            if t in PARCHEADOS:
                parcheados[t] += 1
                continue          # la librería lee la versión neutralizada/incompleta; el bueno lo pone el parche
            malos[t].append((ln_, rd))
    if parcheados:
        print("ya corregidos por el parche:", ", ".join(PARCHEADOS[t] for t in sorted(parcheados)))
    if not malos:
        print("RESULTADO: ningún otro tipo lee distinto de lo que escribe el core")
        return 0
    for t, xs in sorted(malos.items()):
        print(f"  tipo {t} ({names.get(t, '?')}): largo/leído {xs[:6]}")
    print("RESULTADO: hay tipos con el lector mal (ver arriba)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
