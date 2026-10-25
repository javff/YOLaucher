"""Scripts de 2008 (duelcore/scripts_2008): duelos dirigidos contra el motor que demuestran, carta a carta, el
comportamiento del TEXTO DEL CLIENTE 2008 frente al de CardScripts (errata moderna).

    python test_errata_2008.py            # todo (arranca 3 duelcore: 2008, moderno y sin compat CHAININFO)
    YO2_TEST_VERBOSO=1 python test_errata_2008.py     # ademas vuelca los eventos de cada escenario

1. Night Assailant: devuelve OTRO Night Assailant (sin 'except "Night Assailant"').
2. Ryko (el cliente lo llama "Raiko"): el objetivo se elige al ACTIVAR el efecto (antes de resolver) y despues de
   destruir se mandan 3 cartas del Deck al Cementerio; el efecto "when" de la carta destruida (Poison Draw Frog) NO
   pierde el momento.
3. Destiny Hero - Disk Commander: se puede revivir el mismo turno que va al Cementerio y roba 2 SIEMPRE (2 veces seguidas).
4. Strike Ninja: vuelve al campo en la End Phase del turno en que se destierra (fallo real: chain.lua moderno vs
   ocgcore-wasm 0.1.2, arreglado con la compat CHAININFO de duelcore.mts) y su coste solo sale del Cementerio.
5. Crush Card Virus: destruye (1500+ ATK) la mano y los monstruos del rival al resolver y cada carta que robe durante
   sus 3 turnos siguientes; al 4.o turno ya no mira.
Cada comprobacion del 2008 va emparejada con la del motor moderno (YO2_SCRIPTS_2008=0) para ver que la diferencia es real.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from duelcore import DuelCore  # noqa: E402
from pruebas_errata import ELF, PARAR, Partida, elegir_cartas, idle, por_defecto, volcar  # noqa: E402

CHARITY, REBORN, ORION, FROG = 79571449, 83764718, 2971090, 56840658
NA, RYKO, DC, NINJA, CCV = 16226786, 21502796, 56570271, 41006930, 57728570
DM, SKULL, BEWD = 46986414, 70781052, 89631139
VERBOSO = bool(os.environ.get("YO2_TEST_VERBOSO"))
OK = True


def check(cond, msg):
    global OK
    OK &= bool(cond)
    print(("  ok  " if cond else "  MAL ") + msg)


def descarta_de_mano(sel):
    """SELECT_CARD de Graceful Charity (descartar 2 de la mano)."""
    return sel["min"] == 2 and all(c["location"] == 2 for c in sel["selects"])


# ---------------------------------------------------------------------------------------------------------------------
# 1. Night Assailant
# ---------------------------------------------------------------------------------------------------------------------
def esc_night_assailant(dc):
    """Graceful Charity descarta los dos Night Assailant de la mano (mano -> Cementerio directo)."""
    st = {"charity": False}

    def pol(sel, p):
        t = sel["type"]
        if t == "SELECT_IDLECMD":
            if not st["charity"]:
                st["charity"] = True
                return idle(sel, "SELECT_ACTIVATE", CHARITY)
            return PARAR
        if t == "SELECT_CARD" and descarta_de_mano(sel):
            return elegir_cartas(sel, [NA, NA])
        if t == "SELECT_CARD":
            return elegir_cartas(sel, [NA])               # objetivo de "devolver un monstruo de Volteo"
        return por_defecto(sel)

    p = Partida(dc, "na", [CHARITY, NA, NA, ELF, ELF])
    p.jugar(pol)
    return p


# ---------------------------------------------------------------------------------------------------------------------
# 2. Ryko
# ---------------------------------------------------------------------------------------------------------------------
def esc_ryko(dc):
    """T1 pongo a Ryko boca abajo; el rival (mazo de ranas) ataca y lo voltea: elijo como objetivo la rana."""
    st = {"hecho": False, "objetivo_antes_de_resolver": None}

    def pol(sel, p):
        t = sel["type"]
        if t == "SELECT_IDLECMD":
            if sel["turn"] == 1 and not st["hecho"]:
                st["hecho"] = True
                return idle(sel, "SELECT_MONSTER_SET", RYKO)
            if sel["turn"] >= 3:
                return PARAR
            return idle(sel, "TO_EP")
        if t == "SELECT_YESNO" and "Destroy" in (sel.get("desc_text") or ""):
            return {"type": t, "yes": True}
        if t == "SELECT_CARD" and any(c["code"] == FROG for c in sel["selects"]):
            st["objetivo_antes_de_resolver"] = len(p.msgs("CHAIN_SOLVING")) == 0
            return elegir_cartas(sel, [FROG])
        return por_defecto(sel)

    p = Partida(dc, "ry", [RYKO, ELF, ELF, ELF, ELF], [FROG] * 40)
    p.jugar(pol)
    p.st = st
    return p


# ---------------------------------------------------------------------------------------------------------------------
# 3. Destiny HERO - Disk Commander
# ---------------------------------------------------------------------------------------------------------------------
def esc_disk_commander(dc):
    """Charity manda a DC al Cementerio; Monster Reborn lo revive el MISMO turno (roba 2); lo tributo para invocar a
    Orion y otra Monster Reborn lo revive otra vez (roba 2 otra vez: no hay "una vez por Duelo")."""
    st = {"paso": 0}

    def pol(sel, p):
        t = sel["type"]
        if t == "SELECT_IDLECMD":
            n = st["paso"]
            if n == 0:
                st["paso"] = 1
                return idle(sel, "SELECT_ACTIVATE", CHARITY)
            if n == 1:
                st["paso"] = 2
                r = idle(sel, "SELECT_ACTIVATE", REBORN, loc=2)
                return r if r is not None else PARAR      # con el script moderno DC no es objetivo valido
            if n == 2:
                st["paso"] = 3
                return idle(sel, "SELECT_SUMMON", ORION)
            if n == 3:
                st["paso"] = 4
                r = idle(sel, "SELECT_ACTIVATE", REBORN, loc=2)
                return r if r is not None else PARAR
            return PARAR
        if t == "SELECT_CARD" and descarta_de_mano(sel):
            return elegir_cartas(sel, [DC, ELF])
        if t == "SELECT_CARD":
            return elegir_cartas(sel, [DC])
        if t == "SELECT_TRIBUTE":
            return elegir_cartas(sel, [DC])
        return por_defecto(sel)

    p = Partida(dc, "dc", [CHARITY, DC, REBORN, REBORN, ORION])
    p.jugar(pol)
    p.st = st
    return p


# ---------------------------------------------------------------------------------------------------------------------
# 4. Strike Ninja
# ---------------------------------------------------------------------------------------------------------------------
def esc_strike_ninja(dc):
    """Charity manda dos monstruos OSCURIDAD al Cementerio, invoco a Strike Ninja, lo uso y paso a la End Phase."""
    st = {"paso": 0, "coste": None}

    def pol(sel, p):
        t = sel["type"]
        if t == "SELECT_IDLECMD":
            n = st["paso"]
            if sel["turn"] > 1:
                return PARAR
            if n == 0:
                st["paso"] = 1
                return idle(sel, "SELECT_ACTIVATE", CHARITY)
            if n == 1:
                st["paso"] = 2
                return idle(sel, "SELECT_SUMMON", NINJA)
            if n == 2:
                st["paso"] = 3
                return idle(sel, "SELECT_ACTIVATE", NINJA)
            return idle(sel, "TO_EP")
        if t == "SELECT_CARD" and descarta_de_mano(sel):
            return elegir_cartas(sel, [DM, SKULL])
        if t == "SELECT_CARD":                              # coste de Strike Ninja
            st["coste"] = sorted({c["location"] for c in sel["selects"]})
            return elegir_cartas(sel, [DM, SKULL])
        return por_defecto(sel)

    p = Partida(dc, "sn", [CHARITY, NINJA, DM, SKULL, ELF])
    p.jugar(pol)
    p.st = st
    return p


def esc_ninja_coste_corto(dc):
    """Solo UN monstruo OSCURIDAD en el Cementerio: el coste (2 del Cementerio) no se puede pagar."""
    st = {"paso": 0, "sale": None}

    def pol(sel, p):
        t = sel["type"]
        if t == "SELECT_IDLECMD":
            n = st["paso"]
            if n == 0:
                st["paso"] = 1
                return idle(sel, "SELECT_ACTIVATE", CHARITY)
            if n == 1:
                st["paso"] = 2
                return idle(sel, "SELECT_SUMMON", NINJA)
            st["sale"] = any(a["code"] == NINJA for a in sel["activates"])
            return PARAR
        if t == "SELECT_CARD" and descarta_de_mano(sel):
            return elegir_cartas(sel, [DM, ELF])
        return por_defecto(sel)

    p = Partida(dc, "sn2", [CHARITY, NINJA, DM, ELF, ELF])
    p.jugar(pol)
    p.st = st
    return p


# ---------------------------------------------------------------------------------------------------------------------
# 5. Crush Card Virus
# ---------------------------------------------------------------------------------------------------------------------
def esc_ccv(dc):
    """T1 invoco Night Assailant (OSCURIDAD 200) y pongo Crush Card Virus; T3 lo activo (tributo a NA). El rival tiene
    BEWD en la mano (no puede invocarlos: 8 estrellas) y roba, por orden: BEWD (T2), ELF (T4), BEWD (T6), BEWD (T8),
    BEWD (T10). Con 2008: T3 destruye los BEWD de su mano; T4 el ELF vive; T6 y T8 el BEWD robado muere; T10 vive."""
    st = {"paso": 0}

    def pol(sel, p):
        t = sel["type"]
        if t == "SELECT_IDLECMD":
            tn = sel["turn"]
            if tn == 1:
                n = st["paso"]
                if n == 0:
                    st["paso"] = 1
                    return idle(sel, "SELECT_SUMMON", NA)
                if n == 1:
                    st["paso"] = 2
                    return idle(sel, "SELECT_SPELL_SET", CCV)
                return idle(sel, "TO_EP")
            if tn == 3 and st["paso"] == 2:
                st["paso"] = 3
                r = idle(sel, "SELECT_ACTIVATE", CCV)
                return r if r is not None else PARAR
            if tn >= 11:
                return PARAR
            return idle(sel, "TO_EP")
        return por_defecto(sel)

    bot = [BEWD] * 6 + [ELF, BEWD, BEWD, BEWD] + [ELF] * 30
    p = Partida(dc, "vi", [NA, CCV, ELF, ELF, ELF], bot)
    p.jugar(pol, max_pasos=600)
    p.st = st
    return p


def esc_ccv_turno_rival(dc):
    """Crush Card Virus activado en el turno del RIVAL (T2, al invocar su Mystical Elf): ese turno no cuenta; los 3
    siguientes del rival (T4, T6, T8) revisan lo que roba (BEWD cada vez) y en el 4.o (T10) ya no."""
    st = {"paso": 0}

    def pol(sel, p):
        t = sel["type"]
        if t == "SELECT_CHAIN" and sel["turn"] == 2 and st["paso"] == 2:
            for i, c in enumerate(sel["selects"]):
                if c["code"] == CCV:
                    st["paso"] = 3
                    return {"type": t, "index": i}
        if t == "SELECT_IDLECMD":
            tn = sel["turn"]
            if tn == 1:
                n = st["paso"]
                if n == 0:
                    st["paso"] = 1
                    return idle(sel, "SELECT_SUMMON", NA)
                if n == 1:
                    st["paso"] = 2
                    return idle(sel, "SELECT_SPELL_SET", CCV)
                return idle(sel, "TO_EP")
            if tn >= 11:
                return PARAR
            return idle(sel, "TO_EP")
        return por_defecto(sel)

    bot = [BEWD] * 4 + [ELF, BEWD, BEWD, BEWD, BEWD, BEWD] + [ELF] * 30
    p = Partida(dc, "vi2", [NA, CCV, ELF, ELF, ELF], bot)
    p.jugar(pol, max_pasos=600)
    p.st = st
    return p




# ---------------------------------------------------------------------------------------------------------------------
# Tanda ERR2 (24-10): mas cartas cuyo texto de 2008 difiere del de hoy
# ---------------------------------------------------------------------------------------------------------------------
SANGAN, WITCH, DARKHOLE, KOS, POLY = 26202165, 78010363, 53129443, 79109599, 24094653
RESCUE_CAT, KITTY, SERPENT, MAKYURA, COTH = 14878871, 96501677, 8131171, 21593977, 97077563
AXE, OJAMA_GREEN = 40619825, 12482652          # relleno inerte del rival / monstruo de ATK 0 que no ataca


def elige_cadena(sel, *codes):
    """SELECT_CHAIN: el indice de la primera opcion cuyo codigo esta en codes (o no encadenar)."""
    for i, c in enumerate(sel["selects"]):
        if c["code"] in codes:
            return {"type": sel["type"], "index": i}
    return {"type": sel["type"], "index": 0 if (sel.get("forced") and sel.get("selects")) else None}


def mueve(p, code, de, a, desde=0):
    """MOVE de `code` de la ubicacion `de` a la `a` (codigos LOC: mano 2, mazo 1, mzone 4, szone 8, tumba 16, desterradas 32)."""
    return [e for e in p.eventos[desde:] if e.get("type") == "MOVE" and e.get("card") == code
            and e["from"]["location"] == de and e["to"]["location"] == a]


# -- 6. Sangan / Witch of the Black Forest ---------------------------------------------------------------------------
def esc_sangan_doble(dc, carta=SANGAN, relleno=ELF):
    """T1 pongo un Sangan boca abajo; T3 invoco otro y Dark Hole los destruye A LA VEZ: cada uno busca (sin 'once per
    turn') y baraja el Deck."""
    st = {"paso": 0}

    def pol(sel, p):
        t = sel["type"]
        if t == "SELECT_IDLECMD":
            tn = sel["turn"]
            if tn == 1:
                if st["paso"] == 0:
                    st["paso"] = 1
                    return idle(sel, "SELECT_MONSTER_SET", carta)
                return idle(sel, "TO_EP")
            if tn == 3:
                if st["paso"] == 1:
                    st["paso"] = 2
                    return idle(sel, "SELECT_SUMMON", carta)
                if st["paso"] == 2:
                    st["paso"] = 3
                    return idle(sel, "SELECT_ACTIVATE", DARKHOLE)
                return PARAR
            return idle(sel, "TO_EP")
        return por_defecto(sel)

    p = Partida(dc, "sg", [carta, carta, DARKHOLE, ELF, ELF], [AXE] * 40, relleno=relleno)
    p.jugar(pol, max_pasos=300)
    p.st = st
    return p


def esc_sangan_bloqueo(dc):
    """T1 invoco Sangan; T3 Dark Hole lo destruye y busca King of the Swamp (efecto de MANO, ignicion): sin el bloqueo
    por nombre de hoy se puede activar en el mismo turno."""
    st = {"paso": 0, "kos": None}

    def pol(sel, p):
        t = sel["type"]
        if t == "SELECT_IDLECMD":
            tn = sel["turn"]
            if tn == 1:
                if st["paso"] == 0:
                    st["paso"] = 1
                    return idle(sel, "SELECT_SUMMON", SANGAN)
                return idle(sel, "TO_EP")
            if tn == 3:
                if st["paso"] == 1:
                    st["paso"] = 2
                    return idle(sel, "SELECT_ACTIVATE", DARKHOLE)
                st["kos"] = any(a["code"] == KOS and a["location"] == 2 for a in sel["activates"])
                return PARAR
            return idle(sel, "TO_EP")
        if t == "SELECT_CARD":
            return elegir_cartas(sel, [KOS])
        return por_defecto(sel)

    p = Partida(dc, "sg2", [SANGAN, DARKHOLE, ELF, ELF, ELF, ELF, KOS, POLY], [AXE] * 40)
    p.jugar(pol, max_pasos=300)
    p.st = st
    return p


# -- 7. Rescue Cat ---------------------------------------------------------------------------------------------------
def esc_rescue_cat(dc):
    """T3 invoco Rescue Cat y la mando al Cementerio: invoca 2 Catnipped Kitty del Deck (Bestias de nivel 2 con efecto de
    ignicion). Sin el 'effects are negated' de hoy, los Kitty pueden usar su efecto (el rival tiene un Ojama Green)."""
    st = {"paso": 0, "kitty": None}

    def pol(sel, p):
        t = sel["type"]
        if t == "SELECT_IDLECMD":
            tn = sel["turn"]
            if tn == 3:
                if st["paso"] == 0:
                    st["paso"] = 1
                    return idle(sel, "SELECT_SUMMON", RESCUE_CAT)
                if st["paso"] == 1:
                    st["paso"] = 2
                    return idle(sel, "SELECT_ACTIVATE", RESCUE_CAT, loc=4)
                if st["paso"] == 2:
                    st["paso"] = 3
                    st["kitty"] = any(a["code"] == KITTY and a["location"] == 4 for a in sel["activates"])
                    return idle(sel, "SELECT_ACTIVATE", KITTY, loc=4)
                return PARAR
            return idle(sel, "TO_EP")
        if t == "SELECT_CARD":
            return elegir_cartas(sel, [KITTY, KITTY])
        return por_defecto(sel)

    p = Partida(dc, "rc", [RESCUE_CAT, ELF, ELF, ELF, ELF, ELF, KITTY, KITTY], [OJAMA_GREEN] * 40)
    p.jugar(pol, max_pasos=300)
    p.st = st
    return p


# -- 8. Sinister Serpent ---------------------------------------------------------------------------------------------
def esc_serpent(dc):
    """Graceful Charity manda 2 Sinister Serpent al Cementerio (T1); en mi Standby del T3 vuelven los dos (cada una con su
    efecto, sin 'once per turn') y no hay destierro en la End Phase del rival (T4)."""
    st = {"paso": 0}

    def pol(sel, p):
        t = sel["type"]
        if t == "SELECT_IDLECMD":
            tn = sel["turn"]
            if tn == 1 and st["paso"] == 0:
                st["paso"] = 1
                return idle(sel, "SELECT_ACTIVATE", CHARITY)
            if tn >= 5:
                return PARAR
            return idle(sel, "TO_EP")
        if t == "SELECT_CHAIN":
            return elige_cadena(sel, SERPENT)
        if t == "SELECT_CARD" and descarta_de_mano(sel):
            return elegir_cartas(sel, [SERPENT, SERPENT])
        return por_defecto(sel)

    p = Partida(dc, "sp", [CHARITY, SERPENT, SERPENT, ELF, ELF], [AXE] * 40)
    p.jugar(pol, max_pasos=400)
    p.st = st
    return p


# -- 9. Makyura the Destructor ---------------------------------------------------------------------------------------
def esc_makyura(dc):
    """T1 pongo un Elf; T3 lo tributo para invocar a Makyura y Dark Hole la destruye: con 2008 se pueden activar DOS
    Call of the Haunted desde la mano (sin limite), con el script de hoy solo 1."""
    st = {"paso": 0, "coth": 0}

    def pol(sel, p):
        t = sel["type"]
        if t == "SELECT_IDLECMD":
            tn = sel["turn"]
            if tn == 1:
                if st["paso"] == 0:
                    st["paso"] = 1
                    return idle(sel, "SELECT_MONSTER_SET", ELF)
                return idle(sel, "TO_EP")
            if tn == 3:
                if st["paso"] == 1:
                    st["paso"] = 2
                    return idle(sel, "SELECT_SUMMON", MAKYURA)
                if st["paso"] == 2:
                    st["paso"] = 3
                    return idle(sel, "SELECT_ACTIVATE", DARKHOLE)
                r = idle(sel, "SELECT_ACTIVATE", COTH, loc=2)
                if r is None:
                    return PARAR
                st["coth"] += 1
                return r
            return idle(sel, "TO_EP")
        if t == "SELECT_CHAIN":
            return elige_cadena(sel, MAKYURA)
        if t == "SELECT_TRIBUTE":
            return elegir_cartas(sel, [ELF])
        if t == "SELECT_CARD":                              # objetivo de cada Call of the Haunted (1.o Makyura, 2.o el Elf)
            return elegir_cartas(sel, [MAKYURA] if st["coth"] <= 1 else [ELF])
        return por_defecto(sel)

    p = Partida(dc, "mk", [ELF, MAKYURA, DARKHOLE, COTH, COTH, ELF], [AXE] * 40)
    p.jugar(pol, max_pasos=300)
    p.st = st
    return p


def esc_makyura_mano(dc):
    """Graceful Charity descarta a Makyura de la MANO: en 2008 vale (el texto dice 'sent to the Graveyard', venga de donde
    venga) y se puede activar una Call of the Haunted desde la mano; hoy solo vale si viene de la Zona de Monstruos."""
    st = {"paso": 0, "coth": None}

    def pol(sel, p):
        t = sel["type"]
        if t == "SELECT_IDLECMD":
            if sel["turn"] == 1:
                if st["paso"] == 0:
                    st["paso"] = 1
                    return idle(sel, "SELECT_ACTIVATE", CHARITY)
                st["coth"] = any(a["code"] == COTH and a["location"] == 2 for a in sel["activates"])
                return PARAR
            return idle(sel, "TO_EP")
        if t == "SELECT_CHAIN":
            return elige_cadena(sel, MAKYURA)
        if t == "SELECT_CARD" and descarta_de_mano(sel):
            return elegir_cartas(sel, [MAKYURA, ELF])
        return por_defecto(sel)

    p = Partida(dc, "mk2", [CHARITY, MAKYURA, COTH, ELF, ELF], [AXE] * 40)
    p.jugar(pol, max_pasos=200)
    p.st = st
    return p


# -- 10. Dark Magician of Chaos --------------------------------------------------------------------------------------
DMOC, REBORN = 40737112, 83764718


def esc_dmoc(dc):
    """T1: Graceful Charity manda a DMoC y a un Elf al Cementerio y Monster Reborn revive a DMoC (T1: recupera una Magia
    del Cementerio AL INVOCARLA en 2008; hoy, en la End Phase). T2: el Elf del rival ataca a DMoC y muere en combate:
    desterrado EN LUGAR de ir al Cementerio en 2008; hoy va al Cementerio y luego se destierra."""
    st = {"paso": 0}

    def pol(sel, p):
        t = sel["type"]
        if t == "SELECT_IDLECMD":
            tn = sel["turn"]
            if tn == 1:
                n = st["paso"]
                if n == 0:
                    st["paso"] = 1
                    return idle(sel, "SELECT_ACTIVATE", CHARITY)
                if n == 1:
                    st["paso"] = 2
                    return idle(sel, "SELECT_ACTIVATE", REBORN, loc=2)
                return idle(sel, "TO_EP")
            if tn >= 3:
                return PARAR
            return idle(sel, "TO_EP")
        if t == "SELECT_CHAIN":
            return elige_cadena(sel, DMOC)
        if t == "SELECT_CARD" and descarta_de_mano(sel):
            return elegir_cartas(sel, [DMOC, ELF])
        if t == "SELECT_CARD":
            # objetivo de Monster Reborn (DMoC) o de DMoC (una Magia: Charity o Monster Reborn)
            return elegir_cartas(sel, [CHARITY] if any(c["code"] == CHARITY for c in sel["selects"]) else [DMOC])
        return por_defecto(sel)

    p = Partida(dc, "dm", [CHARITY, DMOC, REBORN, ELF, ELF])
    p.jugar(pol, max_pasos=300)
    p.st = st
    return p


# -- 11. Gateway to Dark World ---------------------------------------------------------------------------------------
GATEWAY, BROWW = 93431518, 79126789


def esc_gateway(dc):
    """T1: Graceful Charity manda a Broww al Cementerio, INVOCO un Elf y SOLO ENTONCES miro si puedo activar Gateway to Dark
    World (2008: si, el texto no exige no haber invocado; hoy: no, es un juramento previo)."""
    st = {"paso": 0, "gateway": None}

    def pol(sel, p):
        t = sel["type"]
        if t == "SELECT_IDLECMD":
            n = st["paso"]
            if n == 0:
                st["paso"] = 1
                return idle(sel, "SELECT_ACTIVATE", CHARITY)
            if n == 1:
                st["paso"] = 2
                return idle(sel, "SELECT_SUMMON", ELF)
            if n == 2:
                st["paso"] = 3
                st["gateway"] = any(a["code"] == GATEWAY for a in sel["activates"])
                r = idle(sel, "SELECT_ACTIVATE", GATEWAY)
                return r if r is not None else PARAR
            return PARAR
        if t == "SELECT_CARD" and descarta_de_mano(sel):
            return elegir_cartas(sel, [BROWW, ELF])
        if t == "SELECT_CARD":
            return elegir_cartas(sel, [BROWW])
        return por_defecto(sel)

    p = Partida(dc, "gw", [CHARITY, BROWW, GATEWAY, ELF, ELF], [AXE] * 40)
    p.jugar(pol, max_pasos=300)
    p.st = st
    return p


# -- 12. Catapult Turtle ---------------------------------------------------------------------------------------------
TURTLE = 95727991


def esc_turtle(dc):
    """T1: Graceful Charity manda a Catapult Turtle y a un Elf al Cementerio; Monster Reborn revive a la tortuga, invoco un
    Elf y la tortuga se activa DOS veces el mismo turno (tributa al Elf y luego a si misma): sin el 'once per turn'
    de hoy, 400 + 500 de dano."""
    st = {"paso": 0, "usos": 0}

    def pol(sel, p):
        t = sel["type"]
        if t == "SELECT_IDLECMD":
            n = st["paso"]
            if n == 0:
                st["paso"] = 1
                return idle(sel, "SELECT_ACTIVATE", CHARITY)
            if n == 1:
                st["paso"] = 2
                return idle(sel, "SELECT_ACTIVATE", REBORN, loc=2)
            if n == 2:
                st["paso"] = 3
                return idle(sel, "SELECT_SUMMON", ELF)
            r = idle(sel, "SELECT_ACTIVATE", TURTLE, loc=4)
            if r is None:
                return PARAR
            st["usos"] += 1
            return r
        if t == "SELECT_CARD" and descarta_de_mano(sel):
            return elegir_cartas(sel, [TURTLE, ELF])
        if t == "SELECT_UNSELECT_CARD":                                         # coste: tributo (1.o el Elf, 2.o la tortuga)
            if sel["can_finish"]:
                return {"type": t, "index": None}
            quiero = ELF if st["usos"] <= 1 else TURTLE
            return {"type": t, "index": next((i for i, c in enumerate(sel["select_cards"]) if c["code"] == quiero), 0)}
        if t == "SELECT_CARD":                                                  # objetivo de Monster Reborn
            return elegir_cartas(sel, [TURTLE])
        return por_defecto(sel)

    p = Partida(dc, "tt", [CHARITY, TURTLE, REBORN, ELF, ELF], [AXE] * 40)
    p.jugar(pol, max_pasos=300)
    p.st = st
    return p


# -- 13. Necro Gardna ------------------------------------------------------------------------------------------------
NECRO = 4906301


def esc_necro_gardna(dc):
    """T1: Graceful Charity manda a Necro Gardna al Cementerio. T2: el rival invoca un Elf y me ataca directo. Anoto CUANDO
    se me ofrece Necro Gardna (despues de que acaba de invocar, antes del combate, o con el ataque ya declarado) y lo
    activo solo con el ataque declarado."""
    st = {"paso": 0, "ofertas": [], "usado": False}

    def pol(sel, p):
        t = sel["type"]
        if t == "SELECT_IDLECMD":
            tn = sel["turn"]
            if tn == 1 and st["paso"] == 0:
                st["paso"] = 1
                return idle(sel, "SELECT_ACTIVATE", CHARITY)
            if tn >= 3:
                return PARAR
            return idle(sel, "TO_EP")
        if t == "SELECT_CHAIN" and any(c["code"] == NECRO for c in sel["selects"]):
            ult = [e.get("type") for e in p.eventos if e.get("type") not in ("CHAINING", "CHAIN_SOLVING", "CHAIN_SOLVED",
                                                                                 "CHAIN_END")][-1:]
            ataque = bool(p.msgs("ATTACK")) and sel["turn"] == 2
            st["ofertas"].append("ataque" if ataque else (ult[0] if ult else "?"))
            if ataque and not st["usado"]:
                st["usado"] = True
                return elige_cadena(sel, NECRO)
            return {"type": sel["type"], "index": None}
        if t == "SELECT_CARD" and descarta_de_mano(sel):
            return elegir_cartas(sel, [NECRO, ELF])
        return por_defecto(sel)

    p = Partida(dc, "ng", [CHARITY, NECRO, ELF, ELF, ELF])
    p.jugar(pol, max_pasos=300)
    p.st = st
    return p


# -- 14. Soul Rope ---------------------------------------------------------------------------------------------------
SOUL_ROPE = 37383714


def esc_soul_rope(dc):
    """T1: invoco un Elf (800) y pongo Soul Rope; T2 el Elf del rival (800) lo ataca y los dos se destruyen en combate:
    en 2008 Soul Rope se puede activar (cualquier destruccion), con el script de hoy solo si la destruye un efecto."""
    st = {"paso": 0}

    def pol(sel, p):
        t = sel["type"]
        if t == "SELECT_IDLECMD":
            tn = sel["turn"]
            if tn == 1:
                if st["paso"] == 0:
                    st["paso"] = 1
                    return idle(sel, "SELECT_SUMMON", ELF)
                if st["paso"] == 1:
                    st["paso"] = 2
                    return idle(sel, "SELECT_SPELL_SET", SOUL_ROPE)
                return idle(sel, "TO_EP")
            if tn >= 3:
                return PARAR
            return idle(sel, "TO_EP")
        if t == "SELECT_CHAIN":
            return elige_cadena(sel, SOUL_ROPE)
        return por_defecto(sel)

    p = Partida(dc, "sr", [ELF, SOUL_ROPE, ELF, ELF, ELF])
    p.jugar(pol, max_pasos=300)
    p.st = st
    return p


# -- 15. Light and Darkness Dragon -----------------------------------------------------------------------------------
LADD, RUSH = 47297616, 70046172


def esc_ladd(dc):
    """T1 y T3 pongo un Elf; T5 invoco a LaDD tributandolos y activo Rush Recklessly sobre ella: LaDD anula la activacion y
    sobre esa anulacion encadeno la 2.a Rush Recklessly: en 2008 LaDD la anula tambien (2 veces en la misma cadena); hoy solo
    una vez por cadena."""
    st = {"paso": 0, "enc": 0}

    def pol(sel, p):
        t = sel["type"]
        if t == "SELECT_IDLECMD":
            tn = sel["turn"]
            if tn in (1, 3):
                if st["paso"] == (0 if tn == 1 else 1):
                    st["paso"] += 1
                    return idle(sel, "SELECT_MONSTER_SET", ELF)
                return idle(sel, "TO_EP")
            if tn == 5:
                if st["paso"] == 2:
                    st["paso"] = 3
                    return idle(sel, "SELECT_SUMMON", LADD)
                if st["paso"] == 3:
                    st["paso"] = 4
                    return idle(sel, "SELECT_ACTIVATE", RUSH, loc=2)
                return PARAR
            return idle(sel, "TO_EP")
        if t == "SELECT_UNSELECT_CARD":                  # los dos tributos
            if sel["can_finish"]:
                return {"type": t, "index": None}
            return {"type": t, "index": 0}
        if t == "SELECT_TRIBUTE":
            return {"type": t, "indicies": list(range(sel["min"]))}
        if t == "SELECT_CHAIN":
            if any(c["code"] == LADD for c in sel["selects"]):
                return elige_cadena(sel, LADD)
            if st["paso"] == 4 and st["enc"] == 0 and any(c["code"] == RUSH for c in sel["selects"]):
                st["enc"] = 1
                return elige_cadena(sel, RUSH)
            return {"type": t, "index": None}
        if t == "SELECT_CARD":
            return elegir_cartas(sel, [LADD])
        return por_defecto(sel)

    p = Partida(dc, "ld", [ELF, ELF, LADD, RUSH, RUSH, ELF, ELF], [AXE] * 40)
    p.jugar(pol, max_pasos=400)
    p.st = st
    return p


def destruidos(eventos, code):
    """MOVE de `code` de la mano al Cementerio por efecto (razon con REASON_DESTROY 0x1 y REASON_EFFECT 0x40)."""
    return [e for e in eventos if e.get("type") == "MOVE" and e.get("card") == code
            and e["from"]["location"] == 2 and e["to"]["location"] == 16 and (e.get("reason", 0) & 0x41) == 0x41]


# ---------------------------------------------------------------------------------------------------------------------
# Comprobaciones de la tanda ERR2 (2008 y, al lado, el motor moderno)
# ---------------------------------------------------------------------------------------------------------------------
TANDA_ERR2 = {SANGAN, WITCH, RESCUE_CAT, SERPENT, MAKYURA, DMOC, SOUL_ROPE, GATEWAY, TURTLE, NECRO, LADD}


def busquedas(p, code, reason=64):
    """MOVE Deck -> mano de `code` por efecto (la tanda de Sangan / Witch)."""
    return [e for e in p.eventos if e.get("type") == "MOVE" and e.get("card") == code and e["from"]["location"] == 1
            and e["to"]["location"] == 2 and e.get("reason") == reason]


def checks_2008_tanda2(dc):
    print("-- 6. Sangan y Witch of the Black Forest")
    p = esc_sangan_doble(dc)
    check(len(busquedas(p, ELF)) == 2, "2008: dos Sangan destruidos a la vez buscan los DOS (sin 'once per turn'): %d" % len(busquedas(p, ELF)))
    check(len(p.msgs("SHUFFLE_DECK")) == 2, "2008: cada busqueda baraja el Deck ('Then shuffle your Deck'): %d" % len(p.msgs("SHUFFLE_DECK")))
    p.cerrar()
    p = esc_sangan_bloqueo(dc)
    check(p.st["kos"] is True, "2008: tras buscar King of the Swamp con Sangan se puede activar SU efecto de mano el mismo turno (sin bloqueo por nombre)")
    p.cerrar()
    p = esc_sangan_doble(dc, WITCH, OJAMA_GREEN)
    check(len(busquedas(p, OJAMA_GREEN)) == 2, "2008: dos Witch of the Black Forest buscan los DOS (DEF 1500 o menos): %d" % len(busquedas(p, OJAMA_GREEN)))
    p.cerrar()

    print("-- 7. Rescue Cat")
    p = esc_rescue_cat(dc)
    check(p.st["kitty"] is True and not p.msgs("CHAIN_DISABLED"),
          "2008: los Catnipped Kitty que invoca conservan su efecto (la activacion NO se anula)")
    p.cerrar()

    print("-- 8. Sinister Serpent")
    p = esc_serpent(dc)
    t3 = p.por_turno().get(3, [])
    vuelven = [e for e in t3 if e.get("type") == "MOVE" and e.get("card") == SERPENT and e["from"]["location"] == 16 and e["to"]["location"] == 2]
    check(len(vuelven) == 2, "2008: en mi Standby vuelven las DOS Sinister Serpent del Cementerio (sin 'once per turn'): %d" % len(vuelven))
    check(not mueve(p, SERPENT, 16, 32) and not mueve(p, SERPENT, 4, 32), "2008: no hay destierro de Sinister Serpent en la End Phase del rival")
    p.cerrar()

    print("-- 9. Makyura the Destructor")
    p = esc_makyura(dc)
    check(p.st["coth"] == 2 and len(p.chainings(COTH)) == 2,
          "2008: tras ir Makyura al Cementerio se activan DOS Call of the Haunted desde la mano (%d)" % len(p.chainings(COTH)))
    check(not p.chainings(MAKYURA), "2008: es continuo: Makyura no abre cadena ni pregunta")
    p.cerrar()
    p = esc_makyura_mano(dc)
    check(p.st["coth"] is True, "2008: tambien vale si Makyura va al Cementerio DESDE LA MANO (descartada)")
    p.cerrar()

    print("-- 10. Dark Magician of Chaos")
    p = esc_dmoc(dc)
    i_ep = next(i for i, e in enumerate(p.eventos) if e.get("type") == "NEW_PHASE" and e.get("phase") == 512)
    i_ret = next((i for i, e in enumerate(p.eventos) if e.get("type") == "MOVE" and e.get("card") == CHARITY
                  and e["from"]["location"] == 16 and e["to"]["location"] == 2), None)
    check(i_ret is not None and i_ret < i_ep, "2008: recupera la Magia AL INVOCARLA (antes de la End Phase del turno)")
    sale = [e for e in p.eventos if e.get("type") == "MOVE" and e.get("card") == ELF and e["from"]["controller"] == 1
            and e["from"]["location"] == 4 and e["to"]["location"] == 32]
    check(len(sale) == 1 and (sale[0]["reason"] & 0x20) and not mueve(p, ELF, 16, 32),
          "2008: el Elf que muere en combate se destierra EN LUGAR de ir al Cementerio (razon de combate): %s" % [s["reason"] for s in sale])
    check(len(p.chainings(DMOC)) == 1, "2008: el unico efecto activado de DMoC es el de la Invocacion (el destierro no abre cadena)")
    p.cerrar()

    print("-- 11. Gateway to Dark World")
    p = esc_gateway(dc)
    check(p.st["gateway"] is True, "2008: se puede activar DESPUES de haber invocado este turno")
    check(mueve(p, BROWW, 16, 4), "2008: invoca a Broww del Cementerio")
    p.cerrar()

    print("-- 12. Catapult Turtle")
    p = esc_turtle(dc)
    danos = [e["amount"] for e in p.msgs("DAMAGE") if e["player"] == 1]
    check(p.st["usos"] == 2 and danos == [400, 500], "2008: se usa DOS veces el mismo turno (sin 'once per turn'): dano %s" % danos)
    p.cerrar()

    print("-- 13. Necro Gardna")
    p = esc_necro_gardna(dc)
    check(p.st["ofertas"] == ["ataque"], "2008: solo se ofrece con un ataque declarado: %s" % p.st["ofertas"])
    check(not p.msgs("DAMAGE") and mueve(p, NECRO, 16, 32), "2008: anula ese ataque (sin dano) y se destierra")
    p.cerrar()

    print("-- 14. Soul Rope")
    p = esc_soul_rope(dc)
    muere = [e for e in p.eventos if e.get("type") == "MOVE" and e.get("card") == ELF and e["from"]["controller"] == 0
             and e["from"]["location"] == 4 and e["to"]["location"] == 16 and (e.get("reason", 0) & 0x20)]
    check(len(muere) == 1, "(prueba) mi Elf muere en combate")
    check(len(p.chainings(SOUL_ROPE)) == 1, "2008: Soul Rope se activa tras la destruccion en COMBATE")
    check(any(e["to"]["location"] == 4 and e["from"]["location"] == 1 for e in p.eventos
              if e.get("type") == "MOVE" and e.get("card") == ELF and e["from"]["controller"] == 0),
          "2008: y invoca un monstruo de nivel 4 del Deck")
    p.cerrar()

    print("-- 15. Light and Darkness Dragon")
    p = esc_ladd(dc)
    check(len(p.chainings(LADD)) == 2, "2008: LaDD anula las DOS Rush Recklessly de la misma cadena (%d activaciones)" % len(p.chainings(LADD)))
    check(len(mueve(p, RUSH, 8, 16)) == 2, "2008: las dos Rush Recklessly acaban anuladas en el Cementerio")
    p.cerrar()


def checks_moderno_tanda2(dc):
    p = esc_sangan_doble(dc)
    check(len(busquedas(p, ELF)) == 1, "moderno: solo UN Sangan busca ('once per turn'): %d" % len(busquedas(p, ELF)))
    p.cerrar()
    p = esc_sangan_bloqueo(dc)
    check(p.st["kos"] is False, "moderno: tras buscar King of the Swamp NO se puede activar su efecto (bloqueo por nombre)")
    p.cerrar()
    p = esc_rescue_cat(dc)
    check(bool(p.msgs("CHAIN_DISABLED")), "moderno: los monstruos de Rescue Cat tienen el efecto anulado (la activacion se anula)")
    p.cerrar()
    p = esc_serpent(dc)
    t3 = p.por_turno().get(3, [])
    vuelven = [e for e in t3 if e.get("type") == "MOVE" and e.get("card") == SERPENT and e["from"]["location"] == 16 and e["to"]["location"] == 2]
    check(len(vuelven) == 1 and mueve(p, SERPENT, 16, 32), "moderno: vuelve UNA y se destierra otra en la End Phase del rival")
    p.cerrar()
    p = esc_makyura(dc)
    check(p.st["coth"] == 1, "moderno: solo UNA Trampa desde la mano ('activate 1 Trap Card'): %d" % p.st["coth"])
    p.cerrar()
    p = esc_makyura_mano(dc)
    check(p.st["coth"] is False, "moderno: descartada desde la mano NO cuenta (solo 'from the Monster Zone')")
    p.cerrar()
    p = esc_dmoc(dc)
    i_ep = next(i for i, e in enumerate(p.eventos) if e.get("type") == "NEW_PHASE" and e.get("phase") == 512)
    i_ret = next((i for i, e in enumerate(p.eventos) if e.get("type") == "MOVE" and e.get("card") == CHARITY
                  and e["from"]["location"] == 16 and e["to"]["location"] == 2), None)
    check(i_ret is not None and i_ret > i_ep, "moderno: DMoC recupera la Magia en la End Phase")
    check(len(p.chainings(DMOC)) == 2, "moderno: el destierro es un efecto que se activa (2 cadenas de DMoC)")
    p.cerrar()
    p = esc_gateway(dc)
    check(p.st["gateway"] is False, "moderno: Gateway no se puede activar tras haber invocado")
    p.cerrar()
    p = esc_turtle(dc)
    check(p.st["usos"] == 1, "moderno: Catapult Turtle solo una vez por turno")
    p.cerrar()
    p = esc_necro_gardna(dc)
    check(len(p.st["ofertas"]) > 1 and p.st["ofertas"][0] != "ataque", "moderno: Necro Gardna se ofrece ANTES del ataque: %s" % p.st["ofertas"])
    p.cerrar()
    p = esc_soul_rope(dc)
    check(not p.chainings(SOUL_ROPE), "moderno: Soul Rope no se ofrece tras una destruccion en combate")
    p.cerrar()
    p = esc_ladd(dc)
    check(len(p.chainings(LADD)) == 1, "moderno: LaDD solo anula una vez por cadena (%d activaciones)" % len(p.chainings(LADD)))
    p.cerrar()


def main():
    os.environ.pop("YO2_SCRIPTS_2008", None)
    os.environ.pop("YO2_COMPAT_CHAININFO", None)
    with DuelCore() as dc:
        print("== motor 2008 (por defecto):", {k: v for k, v in dc.ready.items() if k in ("scripts_2008", "compat_chaininfo")})
        check(set(dc.ready.get("scripts_2008", [])) >= {NA, RYKO, DC, NINJA, CCV} | TANDA_ERR2,
              "ready lista los scripts de 2008 cargados: %s" % dc.ready.get("scripts_2008"))
        check(dc.ready.get("compat_chaininfo") is True, "compat CHAININFO encendida")

        print("-- 1. Night Assailant")
        p = esc_night_assailant(dc)
        vuelven = [m for m in p.moves(NA) if m[1] == 16 and m[2] == 2]
        check(len(vuelven) >= 1, "2008: Night Assailant devuelve OTRO Night Assailant del Cementerio a la mano (%d)" % len(vuelven))
        p.cerrar()

        print("-- 2. Ryko")
        p = esc_ryko(dc)
        if VERBOSO:
            volcar(p, 0, ("MOVE", "CHAINING", "CHAIN_SOLVING", "CHAIN_SOLVED", "CHAIN_END", "POS_CHANGE"))
        check(p.st["objetivo_antes_de_resolver"] is True, "2008: el objetivo se elige al ACTIVAR (aun sin CHAIN_SOLVING)")
        orden = [e["card"] for e in p.eventos if e.get("type") == "MOVE" and e["card"] in (FROG, ELF) and e["to"]["location"] == 16]
        check(orden[:4] == [FROG, ELF, ELF, ELF], "2008: primero se destruye el objetivo y LUEGO caen 3 cartas del Deck: %s" % orden)
        check(len(p.chainings(FROG)) >= 1, "2008: el 'when' de Poison Draw Frog (carta destruida) NO pierde el momento")
        p.cerrar()

        print("-- 3. Destiny HERO - Disk Commander")
        p = esc_disk_commander(dc)
        if VERBOSO:
            volcar(p, 0, ("MOVE", "DRAW", "CHAINING"))
        sp = [m for m in p.moves(DC) if m[1] == 16 and m[2] == 4]
        robos2 = [e for e in p.msgs("DRAW") if e["player"] == 0 and len(e["drawn"]) == 2]
        check(len(sp) == 2, "2008: DC sale 2 veces del Cementerio al campo el mismo turno que cayo (%d)" % len(sp))
        check(len(robos2) == 2, "2008: y las 2 veces roba 2 cartas (sin 'una vez por Duelo'): %d robos de 2" % len(robos2))
        p.cerrar()

        print("-- 4. Strike Ninja")
        p = esc_strike_ninja(dc)
        if VERBOSO:
            volcar(p, 0, ("MOVE", "NEW_PHASE", "NEW_TURN"))
        mv = p.moves(NINJA)
        check((0, 4, 32, 68) in mv, "2008: se destierra con su efecto (REASON_EFFECT|TEMPORARY): %s" % mv)
        vuelve = [m for m in mv if m[1] == 32 and m[2] == 4]
        check(len(vuelve) == 1, "2008: vuelve al campo en la End Phase del mismo turno (%s)" % vuelve)
        i_ep = next(i for i, e in enumerate(p.eventos) if e.get("type") == "NEW_PHASE" and e.get("phase") == 512)
        i_back = next(i for i, e in enumerate(p.eventos) if e.get("type") == "MOVE" and e.get("card") == NINJA
                      and e["from"]["location"] == 32)
        check(i_back > i_ep and not any(e.get("type") == "NEW_TURN" for e in p.eventos[i_ep:i_back]),
              "2008: vuelve DESPUES de empezar la End Phase y ANTES del turno siguiente")
        check(p.st["coste"] == [16], "2008: el coste solo ofrece cartas del Cementerio: %s" % p.st["coste"])
        p.cerrar()
        p = esc_ninja_coste_corto(dc)
        check(p.st["sale"] is False, "2008: con solo 1 OSCURIDAD en el Cementerio el efecto no se puede activar")
        p.cerrar()

        print("-- 5. Crush Card Virus")
        p = esc_ccv(dc)
        pt = p.por_turno()
        if VERBOSO:
            volcar(p, 0, ("MOVE", "DRAW", "CARD_HINT", "NEW_TURN", "CONFIRM_CARDS"))
        check(len(destruidos(pt.get(3, []), BEWD)) == 6, "2008 T3: al resolver destruye los 6 BEWD de la mano del rival (%d)" % len(destruidos(pt.get(3, []), BEWD)))
        check(not destruidos(pt.get(4, []), ELF), "2008 T4: el ELF (800) que roba NO muere")
        check(len(destruidos(pt.get(6, []), BEWD)) == 1, "2008 T6: el BEWD que roba muere nada mas robarlo")
        check(len(destruidos(pt.get(8, []), BEWD)) == 1, "2008 T8 (3.er turno del rival): el BEWD que roba muere")
        check(not destruidos(pt.get(10, []), BEWD), "2008 T10 (4.o turno): el virus ya acabo, el BEWD vive")
        check([e for e in pt.get(4, []) if e.get("type") == "CONFIRM_CARDS"], "2008 T4: la carta robada se revela al dueño del virus (CONFIRM_CARDS)")
        cont = [e["description"] for e in p.msgs("CARD_HINT") if e.get("card_hint") == 1]
        check(cont == [1, 2, 3], "2008: cuenta de turnos (CHINT_TURN, la que el traductor manda como 0x3b con P2): %s" % cont)
        p.cerrar()
        p = esc_ccv_turno_rival(dc)
        pt = p.por_turno()
        check(p.st["paso"] == 3, "2008: se puede activar en el turno del rival (al invocar el Elf)")
        check(len(destruidos(pt.get(2, []), BEWD)) == 5, "2008 T2: destruye los 5 BEWD de la mano (%d)" % len(destruidos(pt.get(2, []), BEWD)))
        check(all(len(destruidos(pt.get(t, []), BEWD)) == 1 for t in (4, 6, 8)), "2008 T4, T6, T8: el BEWD que roba muere (3 turnos DESPUES de la activacion)")
        check(not destruidos(pt.get(10, []), BEWD), "2008 T10: ya no mira")
        p.cerrar()

        checks_2008_tanda2(dc)

    print("== motor MODERNO (YO2_SCRIPTS_2008=0): las diferencias de verdad")
    os.environ["YO2_SCRIPTS_2008"] = "0"
    try:
        with DuelCore() as dc:
            check(dc.ready.get("scripts_2008") == [], "ready sin scripts de 2008")
            p = esc_night_assailant(dc)
            check(not [m for m in p.moves(NA) if m[1] == 16 and m[2] == 2], "moderno: Night Assailant NO puede devolver otro Night Assailant")
            p.cerrar()
            p = esc_ryko(dc)
            check(p.st["objetivo_antes_de_resolver"] is not True, "moderno: Ryko no elige objetivo al activar (decide al resolver, sin objetivo)")
            p.cerrar()
            p = esc_disk_commander(dc)
            check(not [m for m in p.moves(DC) if m[1] == 16 and m[2] == 4], "moderno: DC no se puede revivir el turno que cayo al Cementerio")
            p.cerrar()
            p = esc_ccv(dc)
            pt = p.por_turno()
            check(not destruidos(pt.get(6, []), BEWD), "moderno: el CCV de hoy NO mira lo que roba el rival (T6)")
            p.cerrar()
            checks_moderno_tanda2(dc)
    finally:
        os.environ.pop("YO2_SCRIPTS_2008", None)

    print("== sin compat CHAININFO (YO2_COMPAT_CHAININFO=0): el fallo original de Strike Ninja")
    os.environ["YO2_COMPAT_CHAININFO"] = "0"
    try:
        with DuelCore() as dc:
            check(dc.ready.get("compat_chaininfo") is False, "ready sin compat")
            p = esc_strike_ninja(dc)
            check(not [m for m in p.moves(NINJA) if m[1] == 32 and m[2] == 4],
                  "sin compat: Strike Ninja se destierra y NO vuelve (chain.lua: Passed invalid CHAININFO flag)")
            p.cerrar()
    finally:
        os.environ.pop("YO2_COMPAT_CHAININFO", None)

    print("\n" + ("TODO OK" if OK else "HAY FALLOS"))
    sys.exit(0 if OK else 1)


if __name__ == "__main__":
    main()
