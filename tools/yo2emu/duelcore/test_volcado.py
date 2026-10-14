"""Prueba 2: duelo de 3 turnos que vuelca TODOS los eventos para documentar sus campos.

    python test_volcado.py [semilla]

Escribe en out/ (generado; lleva textos de cards.cdb/strings.conf, no subir a un repo):
  volcado_3turnos.jsonl   todos los eventos en orden
  ejemplos_por_tipo.json  el primer ejemplo de cada tipo de mensaje/selección + sus campos
"""
import json
import sys
from pathlib import Path

from duelcore import DuelCore
from pruebas_comun import DECKS, SimplePolicy

OUT = Path(__file__).resolve().parent / "out"


def main():
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    OUT.mkdir(exist_ok=True)
    events = []
    with DuelCore() as dc:
        pol = SimplePolicy()
        dc.new("dump", DECKS, first=0, seed=seed)
        turns = 0
        while True:
            evs = dc.wait("dump")
            events += evs
            turns += sum(1 for e in evs if e.get("type") == "NEW_TURN" and e["ev"] == "msg")
            last = evs[-1]
            if last["ev"] in ("end", "error"):
                break
            if turns > 3:  # ya empezó el 4º turno: se corta
                dc.end("dump")
                events += dc.wait("dump")
                break
            resp = pol(last)
            if resp is None:
                dc.auto("dump")
            else:
                dc.respond("dump", resp)
        state = None

    with open(OUT / "volcado_3turnos.jsonl", "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    ejemplos = {}
    for e in events:
        k = f"{e['ev']}:{e.get('type', '')}"
        if k not in ejemplos:
            ejemplos[k] = {"campos": sorted(e.keys()), "ejemplo": e}
    with open(OUT / "ejemplos_por_tipo.json", "w", encoding="utf-8") as f:
        json.dump(ejemplos, f, ensure_ascii=False, indent=1)

    print(f"{len(events)} eventos, {len(ejemplos)} tipos distintos -> {OUT}")
    for k, v in ejemplos.items():
        print(f"{k:32} {', '.join(c for c in v['campos'] if c not in ('ev', 'id', 'type'))}")


if __name__ == "__main__":
    main()
