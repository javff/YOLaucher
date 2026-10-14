"""Propuesta del agente 2 (protocolo lockstep del duelo, puerto 5760) para pegar en run.py.

Todo sale de análisis estático (Ghidra, copia ghidra_a2). Ver NOTES.md para la evidencia y la confianza de cada punto.
Resumen del modelo:
  - El servidor es AUTORITATIVO. El motor del cliente solo ejecuta lo que le llega en DUEL_SETCOMMAND 0x6202
    (40 B: u32 tipo, u32 B, 8 x u32 P). Con la cola vacía el motor espera (por eso no se reparte nada).
  - Tipo 0 = comando del motor (B = u16 (jugador<<15)|id, P0..P2 = u16): robar, fase, turno, invocar...
  - Tipo 0x1a = "te toca elegir" (habilita la interfaz). Las jugadas permitidas van antes en 0x6611/0x6612.
  - El cliente contesta con 0x6350 (u32 jugador, zona, índice, comando), 0x6351 (pasar/cancelar), 0x002e (fase).

Uso en run.py (pegar el bloque "PEGAR" debajo de duel_reply y cambiar el registro del manejador):
    duel_reply_base = duel_reply
    HANDLERS[DUEL_PORT] = duel_reply_v2
Prueba por fases: DUEL_CFG["stage"] = 1 (solo DUEL START), 2 (+ robar 5 y 5), 3 (+ fases y petición de jugada),
4 (+ mazos impuestos desde el servidor y respuesta a jugadas).
"""
import os
import random
import struct
import sys
import threading
import time

try:
    import proto
except ImportError:   # fuera de run.py (prueba): tools/yo2emu
    sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")))
    import proto

# ======================================================================================= PEGAR (desde aquí) ======

# --- Tipos de entrada de 0x6202 (switch de FUN_00721020; los que no están se consumen sin efecto) ---------------
T_ENGINE = 0x00     # B = comando del motor; P0..P2 -> FUN_00709b10(B, P0, P1, P2)              CONFIRMADO
T_SYNC = 0x11       # sin efecto; único tipo que se consume aunque haya un diálogo abierto          CONFIRMADO
T_REQUEST = 0x1a    # petición de jugada: P0 = clase, P1 != 0 limpia la lista B, P2 (8->10, 9->11) CONFIRMADO (formato)
T_VIEW = 0x1b       # B = id DUEL_VIEW (0x32 diálogo / 0x33 lista), P0..P2 parámetros                PROBABLE
T_VIEW1C = 0x1c     # DUEL_VIEW 0x1c con ubicación empaquetada (P2..P6) + P0, P1                    PROBABLE
T_SETCARDID = 0x1d  # FUN_00744a50(P0 = uid, P1 = carta): identidad de un uid (revelar)              CONFIRMADO (efecto)
T_SETVAR16 = 0x1e   # u16 DAT_012cc3a8[P0] = P1                                                       CONFIRMADO (efecto)
T_SETPAIR = 0x1f    # DAT_012c872c = P0, DAT_012c8730 = P1                                            CONFIRMADO (efecto)
T_SETCARD = 0x40    # (0x46/0x4b/0x4c igual) poner carta: P0 jug, P1 zona, P2 índice, P3 carta, P4 serie, P5 dueño
T_BATTLESEL = 0x47  # FUN_00754340 (vista de selección de batalla)                                  DUDOSO
T_SHUFFLE2 = 0x48   # baraja los dos mazos con la semilla actual                                     PROBABLE
T_RESET = 0x49      # reinicia subestado de fase (DAT_012cc308 = 0x15)                                DUDOSO
T_PING = 0x4a       # el cliente responde con el mensaje 0x004a (sin datos)                           CONFIRMADO
T_FINISH = 0x4d     # fin del duelo: P0 -> FUN_00794a90, P1 -> FUN_00794bd0; sale a la escena 0x1d   PROBABLE
T_LIST_RESET = 0x60  # lista A: vacía y fija tamaño P0                                                CONFIRMADO (efecto)
T_LIST_ADD = 0x61   # lista A: añade parejas (P0,P1),(P2,P3)... hasta el tamaño                       CONFIRMADO (efecto)
T_CHAIN_CLEAR = 0x62  # vacía la tabla de cadena                                                       CONFIRMADO (efecto)
T_CHAIN_SET = 0x63  # entrada de cadena: P0 n, P1 jug, P2 zona, P3 carta, P4 índice, P5                PROBABLE

# --- Comandos del motor (tipo 0; FUN_00709cb0, id = B & 0xfff, bit 15 = jugador) ------------------------------
C_DUEL_START = 0x01     # DUEL_VIEW_DUEL_START                                                       CONFIRMADO
C_TURN_CHANGE = 0x04    # alterna el jugador del turno, turno+1, cartel TURN_CHANGE de (1 - jugador)  CONFIRMADO
C_DUEL_END = 0x05       # P0 = resultado (0/1 ganador, 2 empate), P1/P2 = motivo de cada jugador (3..10) PROBABLE
C_PHASE = (0x0c, 0x0d, 0x0e, 0x0f, 0x10)   # fase 0..4 (robo, espera, main1, batalla, main2)            PROBABLE
C_LP_FLAG = 0x09        # bit P0 de DAT_012ca498 = (P1 != 0)                                           CONFIRMADO (efecto)
C_BATTLE_INIT = 0x15    # DUEL_VIEW_BATTLE_INIT                                                        PROBABLE
C_SUMMON = 0x2d         # P0 = destino empaquetado, P1 = uid, P2 = valor de la ranura; RUN_SUMMON      PROBABLE
C_SHUFFLE = 0x54        # baraja el mazo del jugador con semilla (P1<<16)|P0                            PROBABLE
C_DRAW = 0x55           # roba P1 cartas (P0 = 0xffff sin mensaje) de la cima (índice 0) a la mano    CONFIRMADO

ZONE_HAND, ZONE_EXTRA, ZONE_DECK, ZONE_GRAVE, ZONE_BANISH = 0x0b, 0x0c, 0x0d, 0x0e, 0x0f   # 0-4 monstruos, 5-9 M/T, 10 campo

# --- Bits de las máscaras COMID (= comando que vuelve en el 4º u32 de 0x6350). CONFIRMADO (agente 9, 2026-10-06):
# el menú (FUN_0077c940) recorre los bits 0..14 de la máscara; por cada bit puesto añade la etiqueta
# DAT_00cd4a10[bit] = [8,0,12,7,6,3,4,5,2,1,10,13,9,19] y guarda el propio bit como comando (+0xfe[n]); al elegir,
# FUN_0077ce00 devuelve ese bit (+0x4d0) y FUN_007390a0 lo manda en 0x6350. Texto de la etiqueta (FUN_00a21df0):
# 0x1013+e si e<5, 0x1012+e si no, 19 -> 0x1990. Probado en vivo: bit 8 sobre una trampa = «Cambiar a Posición de
# Ataque» (4117) y bit 1 en batalla = «Ver Detalles» (4115). La tabla antigua (cursor_e.txt) iba desplazada en 1.
#   bit 0 Atacar (4122) · 1 Ver Detalles (4115) · 2 Invocación Especial (4126) · 3 Activar Efecto (4121)
#   bit 4 Invocar (4120) · 5 Invocación de Volteo (4118) · 6 Fijar (4119, monstruo) · 7 Fijar (4119, M/T)
#   bit 8 Cambiar a Posición de Ataque (4117) · 9 Cambiar a Posición de Defensa (4116) · 10 Rendirse (4124)
#   bit 11 «!» (4127, fuera de 0x27ff) · 12 Sacar = robar (4123, fuera de 0x27ff) · 13 Barajar (6544)
# La lista de acciones solo se abre si la máscara tiene bits de 0x27ff (y hay carta bajo el cursor). Con una máscara
# sin esos bits (solo 11 y/o 12) NO hay menú: un clic manda directamente el primer bit puesto (FUN_00739440 estado 0
# + rama «menú no abierto y clic»; visto en vivo con 0x800 en el mazo). Menú cerrado sin elegir -> comando -1.
# 6 = monstruo y 7 = M/T es PROBABLE (las dos dicen «Fijar»; el orden sigue al de los textos 4107/4108).
CMD_ATTACK, CMD_LOOK, CMD_SPSUMMON, CMD_ACTIVATE, CMD_SUMMON = 0, 1, 2, 3, 4
CMD_FLIP, CMD_SET, CMD_SET_ST, CMD_TO_ATK, CMD_TO_DEF, CMD_SURRENDER = 5, 6, 7, 8, 9, 10
CMD_DIRECT, CMD_DRAW, CMD_SHUFFLE = 11, 12, 13
# «Elegir esta carta» de un clic, sin menú (sacrificios, objetivos, zonas): bit 11 (0x800), fuera de 0x27ff. Con la
# máscara EXACTA 0x800, FUN_00739440 manda 0x6350 (j, zona, índice, 11) al primer clic; sobre una ranura VACÍA,
# FUN_007390a0 manda (j, zona, 0, 11) = elegir zona. NO es «rendirse» (eso es el bit 10 con menú).  C (código, audit
# duelo_menus §1-2 y agente de ataque) / sin probar en vivo.
CMD_PICK = CMD_DIRECT
CMD_GENERIC = CMD_PICK
# Cursor: cursor_e.txt NO lo carga el 080912; FUN_00786b10 escribe cmd+1 en un campo que nadie lee. La espada sobre
# los monstruos en batalla es el icono attack_new.txt que dibuja FUN_00754bb0 cuando su máscara tiene el bit 0, y la
# espada que sigue al ratón al elegir objetivo es la vista de batalla en estado 3 (tipo 0x47). C
HAND_MASK = (1 << CMD_SUMMON) | (1 << CMD_SET) | (1 << CMD_ACTIVATE) | (1 << CMD_SET_ST)   # 0xd8 (sin base de cartas)
# Botones de fase: FUN_007d5050 solo devuelve 3/4/5 si FUN_00791460(n) = u8 ND+0x6764+n (jugador 0) != 0, es decir,
# el byte n de la "cola" de 10 u8 del jugador 0 en 0x6611. CONFIRMADO (lectura del byte); 3/4/5 = batalla/main2/fin P.
PH_BATTLE, PH_MAIN2, PH_END = 3, 4, 5

def _env_on(name, default):
    """Bandera de entorno: sin poner (o vacía) = default; «0», «no», «off», «false» = apagada; lo demás = encendida."""
    v = (os.environ.get(name) or "").strip().lower()
    if not v:
        return default
    return v not in ("0", "no", "off", "false")


# Piedra-papel-tijera contra la NPC (07-10, aprobado por el owner). La NPC saca mano al azar; el cliente decide el
# resultado con (rival − mía) mod 3 y, si empatan, vuelve él solo a la elección de mano y manda otra 0x6101
# (FUN_007c76b0 estado 5 con +0x8f8 == 0 -> estado 4 -> estado 2; C). Si gana la NPC, el cliente NO manda 0x6103:
# espera la del servidor tras el eco de 0x6102 (estado 7, rama +0x8f8 == 2 lee +0x67b; C). Qué elegía la NPC del
# oficial al ganar: SIN DATOS (ni en el cliente, que solo espera el 0x6103, ni en la web); por defecto «primero» (D):
# con las reglas de 2008 el que empieza no roba en su primer turno, pero empezar seguía siendo lo habitual.
#   YO2_JANKEN_NPC_AZAR=0      -> lo de antes: gana siempre el jugador (y elige él).
#   YO2_NPC_JANKEN_ELIGE=primero|segundo|azar  -> qué elige la NPC cuando gana.
# En las instancias de PRUEBA (YO2_PVP_TEST=1) el azar va APAGADO salvo que se pida (YO2_JANKEN_NPC_AZAR=1): las
# pruebas de siempre mandan 0x6101 0 / 0x6103 0 seguidos y cuentan con empezar ellas.
NPC_ELIGE_VALIDOS = ("primero", "segundo", "azar")
_PRUEBA = os.environ.get("YO2_PVP_TEST", "") not in ("", "0")
_JANKEN_RNG = random.SystemRandom()

DUEL_CFG = {
    "stage": 4,              # 1..4, ver docstring
    "npc_janken_azar": _env_on("YO2_JANKEN_NPC_AZAR", not _PRUEBA),   # mano de la NPC al azar (si no, gana el jugador)
    "npc_elige": ((os.environ.get("YO2_NPC_JANKEN_ELIGE") or "").strip().lower() or "primero"),   # si gana la NPC
    # Sin azar (lo de antes): player_wins_jyanken = gana el jugador; si se pone a False, gana la NPC y empieza ella
    # si npc_first.
    "player_wins_jyanken": True,
    "npc_first": False,      # solo cuenta si gana la NPC el piedra-papel-tijera (y sin azar)
    "request_kind": 3,       # clase de petición para la fase principal (DUDOSO: probar 3; 2 = sin resaltar)
    "burst_pause": 0.05,     # pausa entre 0x6202 seguidos (el cliente los encola: 512 huecos)
    "player_deck": None,     # lista de ids reales (si None, se usa la del 0x6601, que es ficticia)
    "npc_deck": None,        # lista de ids para la NPC (si None, copia la del jugador)
}


def setcommand(kind, b=0, params=()):
    """DUEL_SETCOMMAND 0x6202 (FUN_00692580): u32 tipo (solo cuenta el byte bajo), u32 B, 8 x u32 P = 40 B."""
    p = list(params)[:8] + [0] * (8 - len(params[:8]))
    w = proto.W().u32(kind).u32(b)
    for v in p:
        w.u32(v)
    return w.bytes()


def engine(cmd, player=0, p0=0, p1=0, p2=0):
    """Tipo 0: comando del motor. B = (jugador<<15) | id. P0..P2 se truncan a u16 en el motor."""
    return setcommand(T_ENGINE, ((player & 1) << 15) | (cmd & 0xfff), (p0 & 0xffff, p1 & 0xffff, p2 & 0xffff))


def request_input(kind, clear_list=1, p2=0):
    """Tipo 0x1a: pone CNetDuel+0x674 bit0 (la interfaz acepta jugadas) y DAT_012cc348 = clase."""
    return setcommand(T_REQUEST, 0, (kind, clear_list, p2))


def set_card(player, zone, index, card, serial, owner=None):
    """Tipo 0x40: escribe la carta de una ranura (mano/extra/mazo/cementerio/desterradas o campo).
    Palabra = carta(14 bits) | dueño<<14 | serie<<23; uid = dueño + 2*serie (y registra uid -> carta)."""
    owner = player if owner is None else owner
    return setcommand(T_SETCARD, 0, (player & 1, zone, index, card & 0xffff, serial & 0xff, owner & 1))


def reveal(uid, card):
    """Tipo 0x1d: identidad de la carta con ese uid (uid = dueño + 2*serie)."""
    return setcommand(T_SETCARDID, 0, (uid, card))


def duel_finish(p0=0, p1=0):
    return setcommand(T_FINISH, 0, (p0, p1))


def loc(player, slot, face=0, pos=0):
    """Ubicación empaquetada (stFrom/stTo): bit0 jugador, bits1-5 zona/ranura, bits6-13 índice, bit14, bit15.
    Para C_SUMMON: slot = 0..4 (ranura de monstruo). PROBABLE."""
    return (player & 1) | ((slot & 0x1f) << 1) | ((face & 1) << 14) | ((pos & 1) << 15)


def comid_field(masks=None, extra=None, tail=None):
    """ONLINE_COMID_FIELD 0x6611 (FUN_00692f40): por jugador 0 y 1: zonas 0..15 salvo la 11 (mano) ->
    u16 máscara + u8; luego 10 x u8. 2 x (15 x 3 + 10) = 110 B. Al recibirlo el cliente BORRA todas las máscaras
    (campo y mano), así que va antes que 0x6612."""
    masks = masks or {}
    extra = extra or {}
    tail = tail or {}
    w = proto.W()
    for p in (0, 1):
        for z in range(16):
            if z == 11:
                continue
            # u8 = MOTIVO que el cliente enseña al pulsar una carta sin acciones (dlg 700+motivo, FUN_007c2270):
            # 0 = «Se necesita un tributo…» (cartel falso); 0xff = sin cartel.  C (código)
            w.u16(masks.get((p, z), 0)).u8(extra.get((p, z), 0xff))
        for k in range(10):
            w.u8(tail.get((p, k), 0))
    return w.bytes()


def comid_hand(entries, lists=((0, ()), (1, ()))):
    """ONLINE_COMID_HAND 0x6612 (FUN_00693030): u16 n; n x (u8 jugador, u16 índice de mano, u16 máscara);
    luego exactamente 2 x (u8 jugador, u8 n, n x u8). Máscara = bits 0..13 = comandos del menú (DAT_012cc358)."""
    w = proto.W().u16(len(entries))
    for p, idx, mask in entries:
        w.u8(p).u16(idx).u16(mask)
    for p, vals in lists:
        w.u8(p).u8(len(vals))
        for v in vals:
            w.u8(v)
    return w.bytes()


def timeup_warning(who, secs, paused=0, secs2=-1):
    """DUEL_TIMEUP_WARNING 0x6461: u8 quién (0/1, 3 = global), s32 segundos, u8 pausado, s32 segundo valor."""
    return proto.W().u8(who).u32(secs).u8(paused).u32(secs2).bytes()


def duelsvrinfomsg(code, flag=0):
    """DUELSVRINFOMSG 0x649b: u8 código (+0x675; 1 = cortar, 4 = error de conexión), u8 bit (+0x676 bit0)."""
    return proto.W().u8(code).u8(flag).bytes()


def duelscore(a=(0, 0, 0, 0), b=(0, 0, 0, 0), x=0):
    """DUELSCORE 0x6613: 4 x u32 de A, 4 x u32 de B, u32 -> +0x680. 36 B."""
    w = proto.W()
    for v in tuple(a) + tuple(b) + (x,):
        w.u32(v)
    return w.bytes()


def jyanken_result(mine, player_wins=True):
    """JYANKEN_RESULT 0x6101: u8 (+0x67a, no lo usa la escena) y u8 mano del rival (+0x600). El cliente decide:
    (rival - mía) % 3 == 1 -> gana el jugador y elige; == 2 -> gana el rival y espera 0x6103; 0 -> empate."""
    rival = (mine + (1 if player_wins else 2)) % 3
    return bytes([0, rival])


def selectfirst_result(first):
    """SELECTFIRST_RESULT 0x6103: u8 índice ABSOLUTO del que empieza (0 = este cliente, 1 = el rival).
    Solo hace falta si ganó el rival; si lo manda el servidor, fuerza la elección."""
    return bytes([first & 1])


def npc_elige_first(modo=None, rng=None):
    """Quién empieza (índice ABSOLUTO desde el cliente: 0 = el jugador, 1 = la NPC) cuando la NPC gana el
    piedra-papel-tijera. modo = «primero» | «segundo» | «azar» (otro valor -> «primero», D)."""
    modo = (modo or DUEL_CFG.get("npc_elige") or "primero").strip().lower()
    if modo == "segundo":
        return 0
    if modo == "azar":
        return (rng or _JANKEN_RNG).randrange(2)
    return 1


def janken_npc(mine, rng=None):
    """Una tirada contra la NPC -> (mano de la NPC, resultado visto por el cliente: 0 empate, 1 gana el jugador,
    2 gana la NPC). Con YO2_JANKEN_NPC_AZAR=0 (DUEL_CFG npc_janken_azar False), lo de antes: player_wins_jyanken."""
    mine %= 3
    if DUEL_CFG.get("npc_janken_azar"):
        rival = (rng or _JANKEN_RNG).randrange(3)
    else:
        rival = (mine + (1 if DUEL_CFG.get("player_wins_jyanken", True) else 2)) % 3
    return rival, (rival - mine) % 3


_JANKEN_KEYS = ("jwin", "jhands", "first", "first_por", "jrounds")


def janken_reset(st):
    """0x6100 GOTOJYANKEN: empieza un piedra-papel-tijera nuevo en esta conexión."""
    for k in _JANKEN_KEYS:
        st.pop(k, None)


def janken_reply(c, st, msg_id, data, log=print):
    """Piedra-papel-tijera contra la NPC (0x6101 / 0x6102 / 0x6103). Deja en st (c.duel_st, por conexión):
    jwin = None (sin decidir o empate) | 0 (ganó el jugador) | 1 (ganó la NPC); jhands = (mano jugador, mano NPC) de la
    tirada que decidió (para la repetición: duellog.replay_start(jhands, first), bloque 0 = el jugador); first = quién
    empieza (0 = el jugador, 1 = la NPC: lo lee run.py en el 0x61fe para OcgSession.start); first_por = quién eligió."""
    if msg_id == 0x6101:
        if st.get("jwin") is not None:
            log(f"[duelo] piedra-papel-tijera: 0x6101 con el resultado ya decidido ({data.hex()}): ignorado")
            return
        mine = (data[0] if data else 0) % 3
        rival, r = janken_npc(mine)
        st["jrounds"] = st.get("jrounds", 0) + 1
        if r:
            st["jhands"] = (mine, rival)
            st["jwin"] = 0 if r == 1 else 1
            if r == 2:
                if DUEL_CFG.get("npc_janken_azar"):
                    st["first"] = npc_elige_first()
                else:
                    st["first"] = 1 if DUEL_CFG.get("npc_first") else 0
                st["first_por"] = "npc"
        que = ("empate (el cliente repite)" if r == 0 else "gana el jugador (elige él)" if r == 1 else
               f"gana la NPC y elige {'empezar' if st.get('first') == 1 else 'ir segunda'}")
        log(f"[duelo] piedra-papel-tijera contra NPC, tirada {st['jrounds']}: manos [{mine}, {rival}] -> {que}"
            + ("" if DUEL_CFG.get("npc_janken_azar") else " [sin azar: YO2_JANKEN_NPC_AZAR=0]"))
        return c.send(0x6101, bytes([0, rival]))
    if msg_id == 0x6102:            # eco obligatorio (+0x20 = 2); detrás, 0x6103 solo si eligió la NPC
        c.send(0x6102)
        if st.get("jwin") == 1:
            c.send(0x6103, selectfirst_result(st.get("first", 1)))
        elif st.get("jwin") is None and DUEL_CFG.get("npc_janken_azar") and st.get("jrounds"):
            log("[duelo] piedra-papel-tijera: 0x6102 sin ganador (el cliente real no lo manda tras un empate)")
        return
    if msg_id == 0x6103:            # elección del jugador (solo si ganó él): 0 = empieza él, 1 = la NPC
        if st.get("jwin") == 1:
            log(f"[duelo] piedra-papel-tijera: 0x6103 del jugador ({data.hex()}) cuando eligió la NPC: ignorado")
            return
        st["first"] = (data[0] if data else 0) & 1
        st["first_por"] = "jugador"
        return c.send(0x6103, data[:1])


class DuelSim:
    """Estado mínimo del duelo en el servidor (vía B). Lleva mazos, manos y ranuras con uid para poder traducir
    jugadas del cliente a comandos del motor. No aplica reglas: es un esqueleto para probar el protocolo.
    field[p][0..4] = monstruos, field[p][5..9] = mágicas/trampas (mismo índice que la zona del motor)."""

    def __init__(self, player_deck, npc_deck, first=0, seed=None):
        rnd = random.Random(seed)
        self.first = first
        self.deck = [list(player_deck), list(npc_deck)]
        for d in self.deck:
            rnd.shuffle(d)
        # serie = posición + 1 (como FUN_00704d10); uid = dueño + 2*serie
        self.deck = [[(card, i + 1) for i, card in enumerate(d)] for d in self.deck]
        self.hand = [[], []]
        self.field = [[None] * 10, [None] * 10]
        self.turn_player = first
        self.turn = 0
        self.phase = 0

    def impose_decks(self):
        """0x40 por cada ranura del mazo: el cliente pasa a tener exactamente el orden del servidor."""
        out = []
        for p in (0, 1):
            for i, (card, serial) in enumerate(self.deck[p]):
                out.append(set_card(p, ZONE_DECK, i, card, serial, p))
        return out

    def draw(self, p, n):
        took = self.deck[p][:n]
        del self.deck[p][:n]
        self.hand[p] += took
        return engine(C_DRAW, p, 0xffff, n)

    def play_from_hand(self, p, idx, cmd=CMD_SUMMON):
        """Jugada desde la mano según el bit del menú. Posición en P0 de C_SUMMON (D): bit14 = boca arriba,
        bit15 = defensa. En vivo, con 0/0 salían boca abajo: por eso invocar/activar lleva bit14 = 1."""
        if cmd in (CMD_ACTIVATE, CMD_SET_ST):
            slots, face, pos = range(5, 10), int(cmd == CMD_ACTIVATE), 0
        elif cmd == CMD_SET:
            slots, face, pos = range(0, 5), 0, 1
        else:                                   # invocar, -1 (colocación automática) o desconocido
            slots, face, pos = range(0, 5), 1, 0
        free = [s for s in slots if self.field[p][s] is None]
        if idx >= len(self.hand[p]) or not free:
            return []
        card, serial = self.hand[p].pop(idx)
        self.field[p][free[0]] = (card, serial)
        return [engine(C_SUMMON, p, loc(p, free[0], face, pos), p + 2 * serial, 0)]

    def summon_from_hand(self, p, idx):        # compatibilidad
        return self.play_from_hand(p, idx, CMD_SUMMON)

    def request(self, kind):
        """6611 (botones de fase + máscaras del campo), 6612 (mano) y 0x1a. Orden obligatorio."""
        buttons = {2: (PH_BATTLE, PH_END), 3: (PH_MAIN2, PH_END), 4: (PH_END,)}.get(self.phase, (PH_END,))
        tail = {(0, b): 1 for b in buttons}
        masks = {}
        if self.phase == 3:                     # batalla: los monstruos propios pueden atacar
            masks = {(0, s): 1 << CMD_ATTACK for s in range(5) if self.field[0][s]}
        elif self.phase in (2, 4):              # main: cambiar posición
            masks = {(0, s): (1 << CMD_TO_ATK) | (1 << CMD_TO_DEF) for s in range(5) if self.field[0][s]}
        hand = [(0, i, HAND_MASK) for i in range(len(self.hand[0]))] if self.phase in (2, 4) else []
        return [(0x6611, comid_field(masks, tail=tail)), (0x6612, comid_hand(hand)),
                (0x6202, request_input(kind))]

    def start_sequence(self, stage):
        out = [engine(C_DUEL_START)]
        if stage >= 4:
            out += self.impose_decks()
        if stage >= 2:
            out += [self.draw(0, 5), self.draw(1, 5)]
        if stage >= 3:
            out += self.turn_start(self.first, first_turn=True)
        return out

    def set_phase(self, p, ph):
        self.phase = ph
        return engine(C_PHASE[ph], p)

    def turn_start(self, p, first_turn=False):
        out = []
        self.turn_player = p
        self.turn += 1
        out.append(self.set_phase(p, 0))
        if not first_turn:
            out.append(self.draw(p, 1))
        out += [self.set_phase(p, 1), self.set_phase(p, 2)]
        if p == 1:
            out += self.npc_main()
        return out

    def npc_main(self):
        """NPC tonta: invoca el primer monstruo de la mano y pasa el turno."""
        out = self.play_from_hand(1, 0) if self.hand[1] else []
        out.append(engine(C_TURN_CHANGE, 1))
        out += self.turn_start(0)
        return out


def _burst(c, msgs):
    """Manda una serie de 0x6202 (y otros (id, datos)) con poca pausa, en otro hilo para no bloquear la lectura."""
    def run():
        time.sleep(0.5)
        for m in msgs:
            mid, data = m if isinstance(m, tuple) else (0x6202, m)
            c.send(mid, data, pause=DUEL_CFG["burst_pause"])
    threading.Thread(target=run, daemon=True).start()


def _deck_from_6601(data):
    n = struct.unpack(">I", data[:4])[0]
    return list(struct.unpack(">%dH" % n, data[4:4 + 2 * n]))


def _request(sim):
    return sim.request(DUEL_CFG["request_kind"])


def _state(c):
    """Estado del duelo guardado EN LA CONEXIÓN (sobrevive a recargar este módulo)."""
    st = getattr(c, "duel_st", None)
    if st is None:
        st = {}
        try:
            c.duel_st = st
        except AttributeError:
            st = DUEL_CFG.setdefault("_st", {})
    return st


def duel_reply_v2(c, msg_id, param, data, base=None):
    """Sustituye/amplía duel_reply. Lo que no trata aquí lo pasa al duel_reply original (duel_reply_base)."""
    base = base or globals().get("duel_reply_base")
    _log = globals().get("log", print)
    st = _state(c)
    if msg_id == 0x6601:
        st["client_deck"] = _deck_from_6601(data)
    if msg_id == 0x6100:            # GOTOJYANKEN: piedra-papel-tijera nuevo; el eco lo manda duel_reply (base)
        janken_reset(st)
    if msg_id in (0x6101, 0x6102, 0x6103):   # piedra-papel-tijera contra la NPC (janken_reply)
        return janken_reply(c, st, msg_id, data, _log)
    if msg_id == 0x61fe:            # fin del init de CDuelScene (u8 = opción SetPosition). También al cambiar opciones
        if st.get("sim"):
            _log(f"[duelo] 0x61fe con duelo en marcha (opciones): {data.hex(' ')}")
            return
        deck = DUEL_CFG["player_deck"] or st.get("client_deck") or [1] * 40
        sim = DuelSim(deck, DUEL_CFG["npc_deck"] or deck, first=st.get("first", 0))
        st["sim"] = sim
        msgs = sim.start_sequence(DUEL_CFG["stage"])
        if DUEL_CFG["stage"] >= 3 and sim.turn_player == 0:
            msgs += _request(sim)
        _log(f"[duelo] arranque etapa {DUEL_CFG['stage']}: empieza {sim.first}, {len(msgs)} mensajes")
        return _burst(c, msgs)
    sim = st.get("sim")
    if msg_id == 0x6350 and sim:    # jugada: u32 jugador, zona, índice, comando (bit del menú; -1 = sin menú)
        p, zone, idx, cmd = struct.unpack(">4I", data[:16])
        cmd = -1 if cmd == 0xffffffff else cmd
        _log(f"[duelo] jugada jugador={p} zona={zone:#x} índice={idx} comando={cmd}")
        out = sim.play_from_hand(0, idx, cmd) if zone == ZONE_HAND else []
        return _burst(c, out + _request(sim))
    if msg_id == 0x6351 and sim:    # pasar / cancelar: volver a pedir
        return _burst(c, _request(sim))
    if msg_id == 0x002e and sim:    # botón de fase: u32 3 = batalla, 4 = main2, 5 = fin (PROBABLE)
        ph = struct.unpack(">I", data[:4])[0]
        _log(f"[duelo] fase pedida {ph}")
        if ph == PH_END:
            return _burst(c, [engine(C_TURN_CHANGE, 0)] + sim.turn_start(1) + _request(sim))
        return _burst(c, [sim.set_phase(0, min(ph, 4))] + _request(sim))
    if msg_id in (0x004a, 0x6423, 0x6426, 0x6432, 0x61ff, 0x61f0, 0x6495, 0x6004):
        _log(f"[duelo] 0x{msg_id:04x} {data.hex(' ')}")
        return
    if base:
        return base(c, msg_id, param, data)

# ========================================================================================= PEGAR (hasta aquí) ====


# ------------------------------------------------------------------------------------------- prueba -----------
def _sizes(reads):
    size = {"u8": 1, "u16": 2, "u32": 4, "s32": 4, "bytes16": 16, "bytes4": 4}
    return sum(size[r] for r in reads)


def selftest():
    import json
    root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))
    pj = json.load(open(os.path.join(root, "_original", "ghidra", "protocol.json"), encoding="utf-8"))["recv"]
    ok = True

    def check(mid, data, expect_len, static_reads=None):
        nonlocal ok
        r = pj.get("0x%04x" % mid, {}).get("reads")
        good = len(data) == expect_len and (static_reads is None or r == static_reads)
        ok &= good
        print("%s 0x%04x len=%d (esperado %d) lecturas protocol.json=%s" % ("OK " if good else "MAL", mid, len(data),
                                                                             expect_len, r))

    # Tamaño fijo: se compara con la suma de protocol.json
    for mid, data in ((0x6101, jyanken_result(0)), (0x6103, selectfirst_result(0)),
                      (0x6461, timeup_warning(0, 180)), (0x649b, duelsvrinfomsg(1)),
                      (0x6613, duelscore())):
        check(mid, data, _sizes(pj["0x%04x" % mid]["reads"]))
    # 0x6202: protocol.json lista u32,u32,u32 (el bucle de 8 colapsado) -> 2 + 8 u32 = 40 B
    check(0x6202, engine(C_DRAW, 1, 0xffff, 5), 40, ["u32", "u32", "u32"])
    check(0x6202, request_input(3), 40, ["u32", "u32", "u32"])
    check(0x6202, set_card(0, ZONE_DECK, 0, 1234, 1), 40, ["u32", "u32", "u32"])
    # 0x6611: lecturas estáticas u16, u8 (bucle 15) y u8 (bucle 10), 2 jugadores -> 110 B
    check(0x6611, comid_field(), 110, ["u16", "u8", "u8"])
    # 0x6612: u16 n, n x (u8,u16,u16), 2 x (u8,u8, k x u8)
    h = comid_hand([(0, i, 0x3fff) for i in range(5)], ((0, (1, 2)), (1, ())))
    check(0x6612, h, 2 + 5 * 5 + (2 + 2) + 2, ["u16", "u8", "u16", "u16", "u8", "u8", "u8"])
    # Contenido de un 0x6202 de robo: tipo 0, B = 0x8055, P0 = 0xffff, P1 = 5
    d = engine(C_DRAW, 1, 0xffff, 5)
    t, b, p0, p1 = struct.unpack(">4I", d[:16])
    good = (t, b, p0, p1) == (0, 0x8055, 0xffff, 5)
    ok &= good
    print("%s campos de DRAW: tipo=%d B=%#x P0=%#x P1=%d" % ("OK " if good else "MAL", t, b, p0, p1))
    # Secuencia completa: todo son 0x6202 de 40 B o (id, datos)
    sim = DuelSim(list(range(1, 41)), list(range(100, 140)), first=0, seed=1)
    seq = sim.start_sequence(4) + _request(sim)
    good = all((len(m) == 40) if isinstance(m, bytes) else isinstance(m, tuple) for m in seq)
    good &= len(sim.hand[0]) == 5 and len(sim.hand[1]) == 5 and len(sim.deck[0]) == 35
    ok &= good
    print("%s secuencia etapa 4: %d mensajes, manos %d/%d" % ("OK " if good else "MAL", len(seq), len(sim.hand[0]),
                                                              len(sim.hand[1])))
    good = sim.summon_from_hand(0, 0)[0][4:8] == struct.pack(">I", 0x2d)
    pm = sim.play_from_hand(0, 0, CMD_SUMMON)[0]
    good &= struct.unpack(">I", pm[8:12])[0] == loc(0, 1, 1, 0)          # 2ª ranura, boca arriba
    st_ = sim.play_from_hand(0, 0, CMD_SET_ST)[0]
    good &= (struct.unpack(">I", st_[8:12])[0] >> 1) & 0x1f == 5          # mágica/trampa a la zona 5
    rq = sim.request(3)
    f = rq[0][1]                                                          # 0x6611: cola del jugador 0
    good &= rq[0][0] == 0x6611 and len(f) == 110 and f[45 + PH_BATTLE] == 1 and f[45 + PH_END] == 1
    good &= rq[1][0] == 0x6612 and rq[2][0] == 0x6202
    class _C: pass
    good &= _state(_C()) is not None
    ok &= good
    print("%s invocar/colocar, botones de fase en 0x6611, estado en la conexión" % ("OK " if good else "MAL"))
    ok &= selftest_janken()
    print("TODO OK" if ok else "HAY FALLOS")
    return ok


def selftest_janken():
    """Piedra-papel-tijera contra la NPC sin red: manos de la NPC guionizadas (empate, gana la NPC, gana el jugador),
    qué elige la NPC (primero/segundo/azar) y el modo viejo (YO2_JANKEN_NPC_AZAR=0)."""
    g = globals()
    ok = True

    class _Rng:                     # sustituye a SystemRandom: devuelve lo guionizado
        def __init__(self, vals):
            self.vals = list(vals)

        def randrange(self, n):
            return self.vals.pop(0) % n

    class _C:
        def __init__(self):
            self.out = []

        def send(self, mid, data=b"", pause=None):
            self.out.append((mid, bytes(data)))

    def chk(name, cond, info=""):
        nonlocal ok
        ok &= bool(cond)
        print("%s janken: %s%s" % ("OK " if cond else "MAL", name, "" if cond else f" ({info})"))

    def play(c, st, seq):
        for mid, data in seq:
            if mid == 0x6100:
                janken_reset(st)
            else:
                janken_reply(c, st, mid, data, log=lambda *_: None)

    saved_cfg = {k: DUEL_CFG.get(k) for k in ("npc_janken_azar", "npc_elige", "player_wins_jyanken", "npc_first")}
    saved_rng = g["_JANKEN_RNG"]
    try:
        DUEL_CFG.update(npc_janken_azar=True, npc_elige="primero", player_wins_jyanken=True, npc_first=False)
        # 1) empate (mía 0, NPC 0) y luego gana la NPC (mía 0, NPC 2 -> (2-0)%3 = 2); elige «primero» -> 0x6103 = 1
        #    tras el eco de 0x6102
        g["_JANKEN_RNG"] = _Rng([0, 2])
        c, st = _C(), {}
        play(c, st, [(0x6100, b""), (0x6101, b"\0")])
        chk("empate: 0x6101 (0, misma mano) y sin ganador", c.out == [(0x6101, b"\0\0")] and st.get("jwin") is None,
            c.out)
        play(c, st, [(0x6101, b"\0"), (0x6102, b"")])
        chk("gana la NPC: 0x6101 (0, 2), eco 0x6102 y 0x6103 = 1 (empieza la NPC)",
            c.out[1:] == [(0x6101, b"\0\2"), (0x6102, b""), (0x6103, b"\1")], c.out)
        chk("estado: jwin 1, manos (0, 2), first 1 por la NPC, 2 tiradas",
            (st.get("jwin"), st.get("jhands"), st.get("first"), st.get("first_por"), st.get("jrounds"))
            == (1, (0, 2), 1, "npc", 2), st)
        n = len(c.out)
        play(c, st, [(0x6103, b"\0"), (0x6101, b"\1")])
        chk("0x6103 del jugador y 0x6101 tras decidir: ignorados", len(c.out) == n and st["first"] == 1, c.out[n:])
        # 2) gana el jugador: mía 1 (papel), NPC 2 -> (2-1)%3 = 1; elige él ir segundo (0x6103 1)
        g["_JANKEN_RNG"] = _Rng([2])
        c, st = _C(), {}
        play(c, st, [(0x6100, b""), (0x6101, b"\1"), (0x6102, b""), (0x6103, b"\1")])
        chk("gana el jugador: sin 0x6103 del servidor tras el eco; eco de su elección; first 1 por él",
            c.out == [(0x6101, b"\0\2"), (0x6102, b""), (0x6103, b"\1")] and st.get("jwin") == 0
            and st.get("first") == 1 and st.get("first_por") == "jugador" and st.get("jhands") == (1, 2), (c.out, st))
        # 3) la NPC elige «segundo» y «azar»
        DUEL_CFG["npc_elige"] = "segundo"
        g["_JANKEN_RNG"] = _Rng([1])                                   # mía 2, NPC 1 -> (1-2)%3 = 2: gana la NPC
        c, st = _C(), {}
        play(c, st, [(0x6100, b""), (0x6101, b"\2"), (0x6102, b"")])
        chk("NPC gana y elige segundo: 0x6103 = 0 (empieza el jugador)", c.out[-1] == (0x6103, b"\0")
            and st.get("first") == 0, c.out)
        DUEL_CFG["npc_elige"] = "azar"
        chk("azar: 0 y 1 según la tirada", npc_elige_first(rng=_Rng([0])) == 0 and npc_elige_first(rng=_Rng([1])) == 1)
        chk("valor raro -> primero", npc_elige_first("cualquiera") == 1 and npc_elige_first("Primero ") == 1)
        # 4) con SystemRandom de verdad: 300 tiradas, salen las tres y el resultado cuadra con la mano devuelta
        g["_JANKEN_RNG"] = saved_rng
        seen = set()
        for i in range(300):
            rv, r = janken_npc(i % 3)
            seen.add(r)
            if (rv - i % 3) % 3 != r or not 0 <= rv <= 2:
                seen.add("mal")
        chk("azar real: empate, gana jugador y gana NPC; resultado = (rival - mía) % 3", seen == {0, 1, 2}, seen)
        # 5) modo viejo (YO2_JANKEN_NPC_AZAR=0): gana siempre el jugador, aunque mande cualquier mano
        DUEL_CFG.update(npc_janken_azar=False)
        c, st = _C(), {}
        play(c, st, [(0x6100, b""), (0x6101, b"\0"), (0x6102, b""), (0x6103, b"\0")])
        chk("sin azar = lo de antes: 0x6101 (0, 1), eco, eco de 0x6103 0", c.out == [(0x6101, b"\0\1"), (0x6102, b""),
                                                                                   (0x6103, b"\0")]
            and st.get("first") == 0, c.out)
        c, st = _C(), {}
        play(c, st, [(0x6101, b"\x05")])                                # mano fuera de rango: módulo 3 (5 -> 2)
        chk("mano 5 = 2 (mod 3) -> NPC 0", c.out == [(0x6101, b"\0\0")], c.out)
        # 6) estado viejo sin claves (duelo empezado antes de la carga en caliente): 0x6102/0x6103 como antes
        c, st = _C(), {}
        play(c, st, [(0x6102, b""), (0x6103, b"\0")])
        chk("sin estado: eco 0x6102 y eco 0x6103 (lo de antes)", c.out == [(0x6102, b""), (0x6103, b"\0")]
            and st.get("first") == 0, c.out)
        # 7) entorno
        chk("_env_on", _env_on("YO2__NO_EXISTE__", True) is True and _env_on("YO2__NO_EXISTE__", False) is False)
    finally:
        g["_JANKEN_RNG"] = saved_rng
        DUEL_CFG.update(saved_cfg)
    return ok


if __name__ == "__main__":
    sys.exit(0 if selftest() else 1)
