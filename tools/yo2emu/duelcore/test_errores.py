"""Prueba 4: errores sin morir, respuestas inválidas (RETRY), surrender, query, auto_chain=false."""
import collections

from duelcore import DuelCore
from pruebas_comun import DECKS, SimplePolicy

ok = True


def check(cond, what):
    global ok
    print(("  ok   " if cond else "  FALLO ") + what)
    ok &= bool(cond)


def global_error(dc):
    ev = dc._global.get(timeout=10)
    return ev


with DuelCore() as dc:
    print("errores de protocolo")
    with dc._lock:
        dc.proc.stdin.write("esto no es json\n")
        dc.proc.stdin.flush()
    ev = global_error(dc)
    check(ev["ev"] == "error", f"línea no JSON -> {ev.get('error')!r}")
    dc.send({"cmd": "volar"})
    ev = global_error(dc)
    check(ev["ev"] == "error", f"comando desconocido -> {ev.get('error')!r}")

    dc.new("e1", [{"main": [1, 2, 3]}, DECKS[1]])
    ev = dc.wait("e1")[-1]
    check(ev["ev"] == "error" and "cards.cdb" in ev["error"], f"passcodes inexistentes -> {ev.get('error')!r}")

    dc.respond("nadie", {"type": "SELECT_IDLECMD", "action": "TO_EP", "index": None})
    ev = dc.wait("nadie")[-1]
    check(ev["ev"] == "error", f"responder a un duelo que no existe -> {ev.get('error')!r}")

    print("respuestas inválidas")
    dc.new("e2", DECKS, seed=5)
    sel = dc.wait("e2")[-1]
    check(sel["ev"] == "select" and sel["type"] == "SELECT_IDLECMD", "primer select = SELECT_IDLECMD")
    dc.new("e2", DECKS, seed=5)
    ev = dc.wait("e2")[-1]
    check(ev["ev"] == "error" and "ya existe" in ev["error"], f"id repetido -> {ev.get('error')!r}")

    dc.respond("e2", {"type": "SELECT_CHAIN", "index": None})
    ev = dc.wait("e2")[-1]
    check(ev["ev"] == "error" and "se espera" in ev["error"], f"tipo de respuesta equivocado -> {ev.get('error')!r}")

    dc.respond("e2", {"type": "SELECT_IDLECMD", "action": "SELECT_SUMMON", "index": 99})
    ev = dc.wait("e2")[-1]
    check(ev["ev"] in ("select", "error"), f"índice fuera de rango -> {ev['ev']} retry={ev.get('retry')} {ev.get('error', '')}")
    if ev["ev"] == "select":
        check(ev["retry"] and ev["type"] == "SELECT_IDLECMD", "el core pide lo mismo otra vez (retry=true)")

    st = dc.query("e2")
    check(len(st["players"][0]["hand"]) == 5 and st["players"][1]["deck_count"] == 35, f"query: mano J0={len(st['players'][0]['hand'])}, mazo J1={st['players'][1]['deck_count']}")

    dc.respond("e2", {"type": 1, "action": 7, "index": None})  # numérico: SELECT_IDLECMD / TO_EP
    evs = dc.wait("e2")
    check(evs[-1]["ev"] == "select", f"respuesta numérica aceptada; sigue con {evs[-1].get('type')}")

    dc.surrender("e2", 0)
    ev = dc.wait("e2")[-1]
    check(ev["ev"] == "end" and ev["winner"] == 1 and ev["reason"] == "surrender", "surrender del J0 -> gana J1")

    print("auto_chain = false (el J0 ve todas las ventanas de cadena)")
    dc.new("e3", DECKS, seed=11, auto_chain=False)
    pol = SimplePolicy()
    sels = collections.Counter()
    chain_with_options = None
    while True:
        last = dc.wait("e3")[-1]
        if last["ev"] != "select":
            break
        sels[last["type"]] += 1
        if last["type"] == "SELECT_CHAIN" and last["selects"] and not chain_with_options:
            chain_with_options = last
        r = pol(last)
        dc.auto("e3") if r is None else dc.respond("e3", r)
    check(last["ev"] == "end" and last["human_retries"] == 0, f"duelo terminado: {last.get('winner')} en {last.get('turns')} turnos")
    check(sels["SELECT_CHAIN"] > 0, f"selects vistos: {dict(sels)}")
    if chain_with_options:
        c = chain_with_options["selects"][0]
        print(f"     ejemplo de cadena con opciones: code={c['code']} loc={c['location']} desc={c.get('desc_text')!r}")

    print("auto (bot.ts juega por el J0) en un duelo entero")
    dc.new("e4", DECKS, seed=77)
    n = 0
    while True:
        last = dc.wait("e4")[-1]
        if last["ev"] != "select":
            break
        n += 1
        dc.auto("e4")
    check(last["ev"] == "end" and last["winner"] in (0, 1), f"bot contra bot: gana {last.get('winner')} tras {n} decisiones auto")
    check(dc.ping()["duels"] == 0, "no quedan duelos abiertos")

print("RESULTADO:", "OK" if ok else "FALLOS")
