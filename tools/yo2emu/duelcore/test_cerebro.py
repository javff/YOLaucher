"""Prueba 5: modo cerebro (`brain`): el jugador 1 lo decide Python y su select lleva `view`.

    python test_cerebro.py

Comprueba: con brain="python" el bot.ts del jugador 1 no juega; los select del jugador 1 llevan `view`
con la mano propia y la del rival tapada; los boca abajo del rival salen con code 0; `respond` con
`player` equivocado da error sin romper el duelo; `end` lleva `retries` por jugador; brain=[true,true]
y bot=[true,false]+brain también funcionan.
"""
from duelcore import DuelCore
from pruebas_comun import DECKS, SimplePolicy

ok = True


class Policy(SimplePolicy):
    """SimplePolicy para cualquier jugador: free_places() da las zonas con player 0 = «yo»."""

    def __init__(self, me):
        super().__init__()
        self.me = me

    def __call__(self, sel):
        r = super().__call__(sel)
        if r and "places" in r:
            for pl in r["places"]:
                pl["player"] = self.me if pl["player"] == 0 else 1 - self.me
        return r


def check(cond, what):
    global ok
    print(("  ok   " if cond else "  FALLO ") + what)
    ok &= bool(cond)


def run(dc, did, **kw):
    """Juega con SimplePolicy (o auto) por quien toque. Devuelve (end, selects por jugador, views vistos)."""
    dc.new(did, DECKS, seed=kw.pop("seed", 3), **kw)
    pols = {0: Policy(0), 1: Policy(1)}
    per = {0: 0, 1: 0}
    views = []
    while True:
        last = dc.wait(did)[-1]
        if last["ev"] != "select":
            return last, per, views
        p = last["player"]
        per[p] += 1
        if last.get("retry"):                      # no debería pasar: que conteste bot.ts y seguir
            dc.auto(did)
            continue
        if "view" in last:
            views.append(last)
        r = pols[p](last)
        dc.auto(did) if r is None else dc.respond(did, r, player=p)


with DuelCore() as dc:
    print("brain = \"python\" (jugador 1 por Python)")
    dc.new("c1", DECKS, seed=5, first=1, brain="python")
    evs = dc.wait("c1")
    created = next(e for e in evs if e["ev"] == "created")
    sel = evs[-1]
    check(created["brain"] == [False, True] and created["bot"] == [False, False], f"created: bot={created['bot']} brain={created['brain']}")
    check(sel["ev"] == "select" and sel["player"] == 1 and "view" in sel, f"primer select del jugador 1 con view ({sel.get('type')})")
    v = sel["view"]
    me, op = v["players"][1], v["players"][0]
    check(v["me"] == 1 and len(me["hand"]) == 5 and all(me["hand"]), f"mano propia visible: {me['hand']}")
    check(op["hand"] == [] and op["hand_count"] == 5, f"mano del rival tapada (hand_count={op['hand_count']})")
    check(len(me["mzone"]) == 7 and len(me["szone"]) >= 6, "zonas: mzone 7, szone >= 6")
    dc.respond("c1", {"type": "SELECT_IDLECMD", "action": "TO_EP", "index": None}, player=0)
    ev = dc.wait("c1")[-1]
    check(ev["ev"] == "error" and "jugador 1" in ev["error"], f"respond con player equivocado -> {ev.get('error')!r}")
    dc.respond("c1", {"type": "SELECT_IDLECMD", "action": "SELECT_MONSTER_SET", "index": 0}, player=1)
    evs = dc.wait("c1")
    while evs[-1]["ev"] == "select" and evs[-1]["player"] == 1 and evs[-1]["type"] != "SELECT_IDLECMD":
        r = Policy(1)(evs[-1])
        dc.respond("c1", r, player=1)
        evs = dc.wait("c1")
    dc.respond("c1", {"type": "SELECT_IDLECMD", "action": "TO_EP", "index": None}, player=1)
    sel = dc.wait("c1")[-1]
    check(sel["ev"] == "select" and sel["player"] == 0 and "view" not in sel, "el jugador 0 (humano) recibe select sin view")
    dc.end("c1")
    dc.wait("c1")

    print("brain = [true, true]: el jugador 0 ve el monstruo colocado del 1 con code 0")
    dc.new("c2", DECKS, seed=5, first=1, brain=[True, True])
    pols = {0: Policy(0), 1: Policy(1)}
    seen_fd = None
    while True:
        last = dc.wait("c2")[-1]
        if last["ev"] != "select":
            break
        if last["player"] == 0 and seen_fd is None:
            fd = [c for c in last["view"]["players"][1]["mzone"] + last["view"]["players"][1]["szone"] if c and not (c["position"] & 5)]
            if fd:
                seen_fd = fd
        r = None if last.get("retry") else pols[last["player"]](last)
        if last["player"] == 1 and last["type"] == "SELECT_IDLECMD" and last["monster_sets"]:
            r = {"type": "SELECT_IDLECMD", "action": "SELECT_MONSTER_SET", "index": 0}   # algo boca abajo
        dc.auto("c2") if r is None else dc.respond("c2", r, player=last["player"])
    check(seen_fd is not None and all(c["code"] == 0 and "attack" not in c for c in seen_fd), f"boca abajo del rival tapados: {seen_fd and seen_fd[:2]}")
    check(last["ev"] == "end" and last["retries"] == [0, 0], f"duelo cerebro-cerebro terminado: gana {last.get('winner')} retries={last.get('retries')}")

    print("bot = [true, false] + brain: bot.ts (J0) contra el cerebro (J1)")
    end, per, views = run(dc, "c3", bot=[True, False], brain=[False, True], seed=9)
    check(end["ev"] == "end" and per[0] == 0 and per[1] > 0 and len(views) == per[1], f"solo decide el 1 ({per[1]} select con view); gana {end.get('winner')}")

    print("sin brain: el protocolo de siempre")
    end, per, views = run(dc, "c4", seed=9)
    check(end["ev"] == "end" and per[1] == 0 and not views and "retries" in end, f"bot.ts juega el 1; sin view; end.retries={end.get('retries')}")
    dc.new("c5", DECKS, brain="otra cosa rara")
    ev = dc.wait("c5")[-1]
    check(ev["ev"] == "select", "brain como cadena cualquiera = jugador 1")
    dc.end("c5"); dc.wait("c5")
    dc.new("c6", DECKS, brain=[1, 2, 3])
    ev = dc.wait("c6")[-1]
    check(ev["ev"] == "error" and "brain" in ev["error"], f"brain mal formado -> {ev.get('error')!r}")
    check(dc.ping()["duels"] == 0, "no quedan duelos abiertos")

print("RESULTADO:", "OK" if ok else "FALLOS")
