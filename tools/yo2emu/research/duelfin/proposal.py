"""Fin de duelo para el emulador de YO2 (cliente Ver.080912) - propuesta del agente 7 (duelfin).

NO se importa desde run.py todavía: el integrador pega/enchufa (ver NOTES.md §7).
Todo sale de análisis estático (decomp en _original/decomp + Ghidra ghidra_a4, scripts a7_*). Confianza en NOTES.md.

    python proposal.py      # autoprueba: tamaños contra protocol.json, ast.parse, secuencias

Resumen del modelo (detalle en NOTES.md):
  Puerto de duelo (5760), en la cola 0x6202 y en este orden:
    [0x6202 t0 cmd 0x23 (perdedor: LP a 0)]   solo rendición / tiempo / desconexión (opcional)
    0x6202 t0 cmd 0x05 C_DUEL_END   P0 = resultado VISTO POR EL CLIENTE (1 gana, 2 pierde, 3 empate), P1/P2 = motivo
    0x6613 DUELSCORE                (lo lee la escena de resultados al crearse; mandarlo antes de 0x4d)
    0x6202 tipo 0x4d T_FINISH       P0 = mismo resultado, P1 = cierre del match (0 normal, 3 rellena, 5 cierra ya)
  -> el cliente acaba el paso del motor, carga CDuelEndScene (0x1d) y luego CResultScene (0x20).
  Lobby (la conexión de sala donde llegó 0x4288):
    0x4140 PLAYERDATAUPDATE (estadísticas + NPCBIN[npc]; antes de que se cree CResultScene)
    0x4237 NPCPRESENTTYPE   (s32; lo consume la NPC al volver a hablarle)
    0x4800 CARDPACKLISTNOTICE (n x u16 sobres; vacío vale) -> pone la bandera 0x39 que CResultScene ESPERA
           para mostrar «Siguiente». Sin él el cliente se queda en la pantalla de resultados para siempre.
  El cliente, al poder pulsar «Siguiente», manda 0x0003 por el puerto de duelo (cierra la sesión) y vuelve
  al mapa pasando por CCardPackCheckScene (0x19) -> CAreaMapScene (0xc), que repide listas (0x4210.., 0x4230).
"""
import ast
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EMU = os.path.normpath(os.path.join(HERE, "..", ".."))            # tools/yo2emu
ROOT = os.path.normpath(os.path.join(EMU, "..", ".."))           # DuelCards
if EMU not in sys.path:
    sys.path.insert(0, EMU)
import proto  # noqa: E402
from yo2srv import compat  # noqa: E402

# U1: lockstep y persistencia son UN solo módulo por proceso (sys.modules["research_lockstep"] / ["research_persistencia"]):
# en el servidor, los mismos objetos que srv.LS y srv.PP (arranque los carga antes que este fichero); fuera de él (pruebas
# y herramientas que cargan este fichero solo) se cargan aquí la primera vez. Antes cada uno era una copia propia
# («lockstep_proposal», «persistencia_proposal») con su estado de módulo aparte.
LS = compat.cargar("lockstep")           # setcommand / engine / comid_* (agente 2)
PS = compat.cargar("persistencia")       # perfiles, PLAYERDATAUPDATE, on_npc_duel_end (agente 5)

# ============================================================================== PEGAR (desde aquí) ==========

# --- Resultado, SIEMPRE desde el punto de vista del cliente (jugador 0) -------------------------------------
# CDuelEndEffectTask (FUN_007afcb0): +0x8c == 1 -> duel_ef_win, == 2 -> duel_ef_lose, otro -> duel_ef_drow.  C
# CResultScene (FUN_008c1a70) con DAT_00cd82d0: 1 -> ventana 0 ganadora, 2 -> perdedora, otro -> empate.     C
# Motor cmd 0x23 (FUN_007162f0): DAT_012cc2e4 = (perdedor == jugador local) + 1 -> mismo convenio.           C
RESULT_WIN, RESULT_LOSE, RESULT_DRAW = 1, 2, 3

# --- Motivo (P1/P2 de C_DUEL_END = +0x30 de cada jugador; FUN_00720cc0 = comprobador de victoria del cliente) --
# Con motivo < 3 no hay animación especial. 3..10 lanzan DUEL_VIEW 0x3b (3/9 RUN_EXODIA, 7/10 RUN_FINALCOUNT)
# y con 4 resalta las letras de Destiny Board (tabla 0xbeb7e8): NO usarlos sin esas cartas en la mesa.
REASON_NONE = 0          # rendición, tiempo, desconexión (sin animación)                                    P
REASON_LP = 1            # LP del rival a 0                                                                  P
REASON_DECKOUT = 2       # el rival no puede robar (+0x24 del jugador)                                       P
REASON_EXODIA = 3        # 5 piezas de Exodia en la mano (0xfb7..0xfbb)                                     C (cartas)
REASON_DESTINY = 4       # Destiny Board + 4 letras (0x1468, 0x1497..0x149a)                                 C (cartas)
REASON_FINALCOUNT = 6    # carta 0x151e en el campo (FUN_00720cc0)                                           P
REASON_CARD_169C = 7     # carta 0x169c en la mano                                                           P
REASON_TURNLIMIT = 0x0b  # límite de turnos: gana el de más LP                                               P

# Razones lógicas del servidor (no van al cliente tal cual)
WHY_LP, WHY_DECKOUT, WHY_SURRENDER, WHY_TIMEOUT, WHY_DISCONNECT, WHY_DRAW, WHY_ABORT = (
    "lp", "deckout", "surrender", "timeout", "disconnect", "draw", "abort")
WHY_EXODIA, WHY_FINALCOUNT, WHY_DESTINY = "exodia", "finalcount", "destiny"     # B50: victorias especiales
_MOTIVE = {WHY_LP: REASON_LP, WHY_DECKOUT: REASON_DECKOUT, WHY_SURRENDER: REASON_NONE,
           WHY_TIMEOUT: REASON_NONE, WHY_DISCONNECT: REASON_NONE, WHY_DRAW: REASON_NONE,
           WHY_EXODIA: REASON_EXODIA, WHY_FINALCOUNT: REASON_CARD_169C, WHY_DESTINY: REASON_DESTINY}

# B50: motivo NUMÉRICO del MSG_WIN de ocgcore (duelcore lo pasa tal cual en «end».reason) -> WHY_*. Antes run.py
# hacía `"deck" in str(reason)` sobre un número: todo salía «LP» (incluido el deck-out). Constantes WIN_REASON_* de
# ocgcore (common.h): 0x1 LP, 0x2 mazo vacío, 0x10 Exodia, 0x11 Final Countdown, 0x15 Destiny Board.  C
# Lado cliente (FUN_00712840 + DUEL_VIEW 0x3b): 3 = RUN_EXODIA, 7 = RUN_FINALCOUNT (carta 0x169c = Final Countdown,
# passcode 95308449), 4 = letras de Destiny Board (0x1468 = Destiny Board, 94212438).  C (cartas)
# Lo demás (0x12 Vennominaga, 0x16 Last Turn…, que en YO2 no tienen animación conocida) = LP, sin animación.
_OCG_WIN_REASON = {0x1: WHY_LP, 0x2: WHY_DECKOUT, 0x10: WHY_EXODIA, 0x11: WHY_FINALCOUNT, 0x15: WHY_DESTINY}


def why_from_ocg(reason):
    """Motivo de fin de duelcore («end».reason: número de ocgcore, o texto «surrender»/«ended»/«error»/«loop»)
    -> WHY_*. Texto o número desconocido -> WHY_LP (como antes)."""
    if isinstance(reason, bool):
        return WHY_LP
    if isinstance(reason, int):
        return _OCG_WIN_REASON.get(reason, WHY_LP)
    r = str(reason or "").lower()
    if r == "surrender":
        return WHY_SURRENDER
    if r in ("error", "ended"):
        return WHY_ABORT
    return WHY_DECKOUT if "deck" in r else WHY_LP

# --- 0x6202 tipo 0x4d, P1 = DAT_012a9090 (FUN_00794bd0); efecto en FUN_00794a90 / caso 0x4d de FUN_00721020 --
FINISH_NORMAL = 0        # solo apunta la partida; en un match decide al llegar a 2 victorias / 3 partidas    C
FINISH_FILL = 3          # en un match rellena las partidas que faltan hasta 3 con el mismo resultado          C
FINISH_CLOSE = 5         # DAT_00cd82d0 = P0 directamente: el match se acaba YA con ese resultado             C

C_LOSE_LP0 = 0x23        # motor: LP del jugador del bit 15 a 0 + LIFE_SET; P0 = motivo (al +0x30 del ganador) C
# Rendición (agente 9). El menú de acciones pinta la etiqueta DAT_00cd4a10[bit]: «Rendirse» (4124) es el BIT 10, no
# el 11 (CONFIRMADO: tabla del exe + en vivo el bit 8 salió como «Cambiar a Posición de Ataque»). El bit 11 (0x800)
# está fuera de 0x27ff: no abre menú y un clic manda 11 al instante (FUN_007390a0 / FUN_00739440), que es lo que hizo
# perder al owner de un clic. Ahora: bit 10 en el mazo propio -> al pasar por el mazo sale el menú con «Rendirse»; al
# elegirlo llega 0x6350 (0, 0xd, 0, 10) y el servidor pide confirmación con el diálogo SÍ/NO del cliente
# (0x6202 tipo 0x60/0x61 lista A + tipo 0x1b B=0x32 P0=2 -> CUYesNoDialog; respuesta 0x6423 u32 1 = SÍ, 0 = NO).
CMD_SURRENDER = LS.CMD_SURRENDER        # 10 = «Rendirse» en el menú                                       C
CMD_SURRENDER_DIRECT = 11               # 0x800 sin menú (clic directo); ya NO cuenta como rendición         C
SURRENDER_BIT = 1 << CMD_SURRENDER
# Texto del diálogo: la clase 0 de la lista A es un id de dlg_text (no de yo2text): dlg 6 = «¿Te rindes?». El 4124
# (yo2text «Rendirse») no existe en dlg_text y el cliente enseñaba otro cartel («…se requiere 1 Sacrificio…»).  C
SURRENDER_TEXT = 6       # dlg 6 = «¿Te rindes?» (dlg_text; audit duelo_menus §3)  C
# Clase del ítem de la lista A (FUN_00744670): 0 = plantilla «%s%s%s» que mezcla DOS textos (FUN_007437d0); con un solo
# ítem lee basura -> texto equivocado («…1 Sacrificio…») o access violation (2026-10-06, dos cierres en vivo). 2 = copiar
# el texto tal cual (FUN_00743750 -> 0073be10).  C (código) / P (en vivo)
SURRENDER_CLASS = 2
# 0x6423 de CUYesNoDialog (vtable 0xbec058, método 0x774650): botón 4000 -> 1 = SÍ, 0xfa1 -> 0 = NO. El 5/7 era de
# CUPhaseYesNoDialog (FUN_00774e70), que nunca se crea (su creador FUN_00774a60 no tiene llamadas). En vivo llega 1.  C
YES, NO = 1, 0

# --- ids ----------------------------------------------------------------------------------------------------
M_SETCOMMAND, M_DUELSCORE, M_SVRINFO, M_TIMEUP = 0x6202, 0x6613, 0x649b, 0x6461
M_PDU, M_NPCPRESENT, M_CARDPACKLIST, M_DUELABORT = 0x4140, 0x4237, 0x4800, 0x4310


def result_for_client(winner):
    """winner: 0 = jugador (cliente), 1 = NPC/rival, None = empate."""
    return RESULT_DRAW if winner is None else (RESULT_WIN if winner == 0 else RESULT_LOSE)


def duelscore(me=(0, 0, 0, 0), rival=(0, 0, 0, 0), x=0):
    """0x6613 DUELSCORE (FUN_00692350), 9 x u32 = 36 B: A0..A3 -> ND+0x5a8.., B0..B3 -> ND+0x63c.., X -> +0x680.
    CResultScene (ventanas CWindowResult, solo si NO es duelo de NPC) pinta «%s WIN LOSE SCORE» con A0, A1, A3
    (jugador) y B0, B1, B3 (rival). A2/B2/X: sin lector encontrado.  Formato C, sentido P."""
    return LS.duelscore(tuple(me), tuple(rival), x)


def end_duel_msgs(winner, reason=WHY_LP, *, finish=FINISH_NORMAL, lp_zero=None, score=None):
    """Mensajes del PUERTO DE DUELO para acabar la partida. Devuelve [(id, datos), ...] en orden.

    winner: 0 = gana el cliente, 1 = gana la NPC, None = empate.
    reason: WHY_* (lp, deckout, surrender, timeout, disconnect, draw).
    finish: P1 de 0x4d (FINISH_NORMAL en duelo simple o partida de match; FINISH_CLOSE para cerrar un match).
    lp_zero: mandar antes el cmd 0x23 (LP del perdedor a 0 con animación). Por defecto sí en rendición/tiempo/
             desconexión (el LP no ha llegado a 0 por combate).
    score: ((A0,A1,A2,A3), (B0,B1,B2,B3)) para DUELSCORE; por defecto victorias/derrotas de esta partida."""
    res = result_for_client(winner)
    motive = _MOTIVE.get(reason, REASON_NONE)
    out = []
    if lp_zero is None:
        lp_zero = reason in (WHY_SURRENDER, WHY_TIMEOUT, WHY_DISCONNECT)
    if lp_zero and winner is not None:
        loser = 1 - winner
        out.append((M_SETCOMMAND, LS.engine(C_LOSE_LP0, loser, motive)))
    # C_DUEL_END: B = jugador 0; P0 = resultado; P1 = motivo del jugador 0, P2 = del jugador 1. El cliente usa
    # P2 si P0 == 2 y P1 si no (FUN_00712840): ponemos el mismo en los dos.
    out.append((M_SETCOMMAND, LS.engine(LS.C_DUEL_END, 0, res, motive, motive)))
    if score is None:
        me = (1 if res == RESULT_WIN else 0, 1 if res == RESULT_LOSE else 0, 1 if res == RESULT_DRAW else 0, 0)
        rv = (me[1], me[0], me[2], 0)
        score = (me, rv)
    out.append((M_DUELSCORE, duelscore(score[0], score[1])))
    out.append((M_SETCOMMAND, LS.setcommand(LS.T_FINISH, 0, (res, finish))))
    return out


def surrender_confirm_msgs(text=SURRENDER_TEXT):
    """Diálogo SÍ/NO del cliente para confirmar la rendición: lista A = 1 pareja (texto, clase 0 = mensaje de
    dlg_text, FUN_007437d0 -> FUN_0073be10) + DUEL_VIEW 0x32 tipo 2 (CUYesNoDialog). Respuesta: 0x6423 u32 1/0."""
    return [(M_SETCOMMAND, LS.setcommand(LS.T_LIST_RESET, 0, (1,))),
            (M_SETCOMMAND, LS.setcommand(LS.T_LIST_ADD, 0, (int(text), SURRENDER_CLASS))),
            (M_SETCOMMAND, LS.setcommand(LS.T_VIEW, 0x32, (2, 0, 0)))]


def is_surrender(data):
    """0x6350 de rendición: SOLO comando 10 (menú «Rendirse») sobre el mazo PROPIO (0, 0xd). El 11 ya no: es el clic
    directo (máscara 0x800) con el que el traductor pide elegir cartas/objetivos; tomarlo como rendición abría el
    diálogo SÍ/NO sobre un objetivo de equipo y, con SÍ = 1 leído como NO, reenviaba la petición en bucle (log
    20261006-132548 l.2620-2830: 0x6350 (0,0,0,0xb) -> diálogo -> 0x6423 1 -> reenvío).  C"""
    m = parse_6350(data)
    return bool(m) and m[0] == 0 and m[1] == LS.ZONE_DECK and m[3] == CMD_SURRENDER


def surrender_masks(field_masks=None, on_deck=True):
    """Añade «Rendirse» a las máscaras de 0x6611 del jugador 0: bit 10 en el MAZO (zona 0xd). El bit 10 está en
    0x27ff, así que el mazo abre la lista de acciones con «Rendirse» (etiqueta 10 -> texto 4124, C); elegirla manda
    0x6350 (0, 0xd, 0, 10) y el servidor pide confirmación (make_handlers(confirm_surrender=True)).
    NO usar 0x800: sin menú, un clic manda 11 y se pierde al instante (C, visto en vivo)."""
    m = dict(field_masks or {})
    if on_deck:
        m[(0, LS.ZONE_DECK)] = m.get((0, LS.ZONE_DECK), 0) | SURRENDER_BIT
    return m


def parse_6350(data):
    """0x6350 = 4 x u32 BE: jugador, zona, índice, comando (-1 = menú cerrado sin elegir)."""
    if len(data) < 16:
        return None
    p, z, i, c = struct.unpack(">4I", data[:16])
    return p, z, i, (c if c != 0xffffffff else -1)


def on_client_surrender(data, *, in_match=False):
    """Si el 0x6350 es «Rendirse» devuelve la secuencia de fin (gana la NPC); si no, None.
    En un match, rendirse cierra el match entero (FINISH_CLOSE)."""
    if not is_surrender(data):
        return None
    return end_duel_msgs(1, WHY_SURRENDER, finish=FINISH_CLOSE if in_match else FINISH_NORMAL)


def cardpacklist_notice(pack_ids=()):
    """0x4800 YG_MSG_CARDPACKLISTNOTICE (FUN_006bbe20; id C por desensamblado 0x6ac955 CMP EAX,0x4800):
    lee u16 mientras queden datos; cada id != 0 se añade a la lista de sobres pendientes (lobby+0x1204, 0x6c B
    por sobre, FUN_0084d8a0); al final lobby+0x39 = 2. Vacío = solo libera «Siguiente» en CResultScene.  C
    Con sobres, CCardPackCheckScene (0x19) abre el elector (FUN_008444c0) -> 0x4802 SELECTCARDPACK u16 ->
    el servidor contesta 0x4803 (s32 0, n x (u16 carta, u8)).  P"""
    w = proto.W()
    for pid in pack_ids:
        w.u16(int(pid) & 0xffff)
    return w.bytes()


def npc_present_type(kind):
    """0x4237 NPCPRESENTTYPE (FUN_006c3190): s32 -> lobby+0xdbd4. Al volver a hablar con la NPC (FUN_0089d590):
    1 -> diálogo 8 si DAT_00cd82d0 == 1 (ganaste) o 10 si no; 2 -> diálogo 12; otro -> diálogo normal (4).
    Se consume (vuelve a 0) al leerlo.  Formato C, sentido de 1/2 P (regalo / sin regalo)."""
    return proto.W().u32(int(kind)).bytes()


def duel_abort(code=0):
    """0x4310 YG_MSG_DUELABORT (FUN_006bb4b0): s32 -> lobby+0x834, borra los datos de AUTODUELSTART y pone
    lobby+0x31 = 2. Lo consume el primero que lo vea: la UI del duelo (FUN_0078c140: diálogo 4516 «Se acabó el
    tiempo. Este duelo será considerado sin resultado») o CDuelEndScene estado 0 (mismo diálogo; al aceptar manda
    0x0003 y vuelve al mapa SIN pasar por resultados).  C (efectos) / D (cuándo lo usaba el servidor)"""
    return proto.W().u32(int(code)).bytes()


# NPCBIN[npc] = 4 bytes b0..b3. CWindowResultNpc (CResultScene init 0x8c1a70 -> FUN_008c3c30) los lee como
# u32 y los reparte: b0 -> +0x420 (FUN_008ccad0), b1 -> +0x424 (FUN_008ccb20), b2 -> +0x428 (FUN_008ccb70); el
# repintado FUN_008ccbc0 los pinta en ese orden = WIN, LOSE, DRAW. Evidencia en vivo (agente 9): con la derrota
# apuntada en b2 (convenio de persistencia) la ventana mostró «WIN 0, LOSE 0, DRAW 1» -> b2 = EMPATES y b1 = DERROTAS.
# Desensamblado 0x8c30c1-0x8c32e2 (mismo patrón de pila en la llamada y en los setters).  PROBABLE (alto)
NPCBIN_WIN, NPCBIN_LOSE, NPCBIN_DRAW, NPCBIN_PROGRESS = 0, 1, 2, 3


def npc_duel_end(p, npc, result, turns=0, lp_left=0):
    """PS.on_npc_duel_end con el reparto CORRECTO de NPCBIN (persistencia apunta derrotas en b2 y empates en b1:
    se corrige aquí sin tocar su fichero). Devuelve los datos de 0x4140."""
    before = PS.npcbin_get(p, npc)
    PS.on_npc_duel_end(p, npc, result, turns=turns, lp_left=lp_left)
    e = PS.npcbin_get(p, npc)
    if result != "win":
        want = NPCBIN_LOSE if result == "lose" else NPCBIN_DRAW
        for k in (1, 2):                       # el contador que haya subido persistencia, si no es el bueno, se mueve
            if k != want and e[k] > before[k]:
                e[k] -= 1
                e[want] = min(e[want] + 1, 255)
    PS.npcbin_set(p, npc, e)
    return PS.player_data_update(p, npc=npc)


def fix_npcbin_swapped(p):
    """Una vez: los perfiles guardados con el convenio viejo tienen derrotas en b2 y empates en b1 -> intercambia.
    (Llamar a mano desde la consola sobre el estado y guardar; no se llama solo.)"""
    for k, v in list(p.get("npcbin", {}).items()):
        v = (list(v) + [0, 0, 0, 0])[:4]
        v[1], v[2] = v[2], v[1]
        p["npcbin"][k] = v
    return p


def lobby_after_duel_msgs(p, npc, result, *, turns=0, lp_left=0, present=None, packs=()):
    """Mensajes del LOBBY tras un duelo contra NPC. p = perfil de persistencia (se modifica: guardar después).
    result: RESULT_* (punto de vista del cliente). Devuelve [(id, datos)] en el orden recomendado."""
    r = {RESULT_WIN: "win", RESULT_LOSE: "lose"}.get(result, "draw")
    out = [(M_PDU, npc_duel_end(p, npc, r, turns=turns, lp_left=lp_left))]
    if present is not None:
        out.append((M_NPCPRESENT, npc_present_type(present)))
    out.append((M_CARDPACKLIST, cardpacklist_notice(packs)))
    return out


class DuelEnd:
    """Estado de fin de duelo/match en el servidor. mode = último u8 de AUTODUELSTART 0x43a0:
    0 -> duelo simple (DAT_012a9084 = 0); 1 -> MATCH al mejor de 3 (DAT_012a9084 = 1) y duelo «de NPC»
    (DAT_012bb6b0+0x9c = 1 -> ventana CWindowResultNpc y DAT_00cd82dc = id de la NPC).  C"""

    def __init__(self, mode=0, single_game_match=True):
        self.mode = mode
        self.games = []          # resultados de cada partida (punto de vista del cliente)
        self.over = False
        # Recomendado mientras no se pruebe CCardSwapScene (0x25): en modo 1 cerrar el match tras la 1ª partida.
        self.single_game_match = single_game_match

    def _decided(self):
        w = self.games.count(RESULT_WIN); l_ = self.games.count(RESULT_LOSE)
        if w - l_ >= 2 or l_ - w >= 2:
            return True
        return len(self.games) >= 3

    def game_over(self, winner, reason):
        """Devuelve (mensajes del duelo, el match ha terminado)."""
        res = result_for_client(winner)
        self.games.append(res)
        if self.mode != 1:
            self.over = True
            return end_duel_msgs(winner, reason, finish=FINISH_NORMAL), True
        if reason in (WHY_SURRENDER, WHY_DISCONNECT) or self.single_game_match:
            self.over = True
            return end_duel_msgs(winner, reason, finish=FINISH_CLOSE), True
        self.over = self._decided()
        # Con FINISH_NORMAL el cliente decide igual que _decided() (FUN_00794a90). Si no está decidido,
        # CDuelEndScene manda a CCardSwapScene (0x25) y de ahí a piedra-papel-tijera (0x1e) - ver NOTES §5.
        return end_duel_msgs(winner, reason, finish=FINISH_NORMAL), self.over

    def match_result(self):
        w = self.games.count(RESULT_WIN); l_ = self.games.count(RESULT_LOSE)
        return RESULT_WIN if w > l_ else RESULT_LOSE if l_ > w else RESULT_DRAW


# ---------------------------------------------------------------- manejadores para run.py
def make_handlers(load_state, save_state, lobby_conn_getter, send_burst=None, confirm_surrender=False):
    """lobby_conn_getter() -> conexión de sala (la que recibió 0x4288). send_burst(c, [(id, datos)]) manda
    con pausa (la cola del cliente tiene 512 huecos; por defecto c.send uno a uno).
    confirm_surrender=True: «Rendirse» pide SÍ/NO (h_6350 devuelve "ask"; h_6423 devuelve "end"/"no"/None).
    Con False (run.py antiguo) se rinde al instante y h_6350 devuelve True, como antes."""
    sess = {"end": None, "npc": 0, "turn": 0, "ask": False}

    def burst(c, msgs):
        if send_burst:
            return send_burst(c, msgs)
        for mid, d in msgs:
            c.send(mid, d)

    def h_autoduelstart(npc, mode):            # llamar al mandar 0x43a0 (lobby_reply 0x4288)
        sess.update(end=DuelEnd(mode), npc=int(npc), turn=0)

    def finish(c, winner, reason, turns=0, lp_left=0, present=None):
        """Acaba la partida: cola de duelo + lobby. Devuelve True si el match terminó."""
        de = sess["end"] or DuelEnd(0)
        msgs, over = de.game_over(winner, reason)
        lob = lobby_conn_getter()
        if over and lob is not None:
            st = load_state(); p = PS.profile(st, PS.active_slot(st))
            res = de.match_result() if de.mode == 1 else result_for_client(winner)
            # PDU antes que 0x4d: CResultScene lee NPCBIN al crearse
            pdu = lobby_after_duel_msgs(p, sess["npc"], res, turns=turns, lp_left=lp_left, present=present)
            save_state(st)
            burst(lob, pdu[:1])
            burst(c, msgs)
            burst(lob, pdu[1:])
        else:
            burst(c, msgs)
        return over

    def h_6350(c, data):                       # en duel_reply, ANTES de traducir la jugada
        """False = no es rendición (sigue el traductor). Con confirmación: "ask" (diálogo mandado; no hacer nada
        más). Sin confirmación: True (duelo acabado: run.py llama a s.surrender())."""
        if not is_surrender(data):
            return False
        if confirm_surrender:
            sess["ask"] = True
            burst(c, surrender_confirm_msgs())
            return "ask"
        finish(c, 1, WHY_SURRENDER)
        return True

    def h_6423(c, data):                       # en duel_reply, ANTES de pasarlo al traductor
        """None = no es nuestro (sigue el traductor). "end" = SÍ: duelo acabado (run.py llama a s.surrender()).
        "no" = NO: run.py debe volver a mandar la petición de jugada pendiente (s.d._resend())."""
        if not sess.get("ask"):
            return None
        sess["ask"] = False
        v = struct.unpack(">I", data[:4])[0] if len(data) >= 4 else NO
        if v == YES:
            finish(c, 1, WHY_SURRENDER)
            return "end"
        return "no"

    def h_0003(c, data):                       # el cliente cierra la sesión de duelo (CResultScene / abortos)
        sess["end"] = None
        return True

    def h_disconnect(c):                       # el socket de duelo se cae sin fin: cuenta como derrota
        de = sess["end"]
        if de is not None and not de.over:
            st = load_state(); p = PS.profile(st, PS.active_slot(st))
            npc_duel_end(p, sess["npc"], "lose")
            p["rec"]["duels_void"] = p["rec"].get("duels_void", 0) + 1
            save_state(st)
        sess["end"] = None

    return dict(h_autoduelstart=h_autoduelstart, finish=finish, h_6350=h_6350, h_6423=h_6423, h_0003=h_0003,
                h_disconnect=h_disconnect, sess=sess)

# ============================================================================== PEGAR (hasta aquí) ==========


# ---------------------------------------------------------------- autoprueba
def _proto():
    return json.load(open(os.path.join(ROOT, "_original", "ghidra", "protocol.json"), encoding="utf-8"))


_SZ = {"u8": 1, "u16": 2, "u32": 4, "s32": 4}


def _size(reads):
    n = 0
    for r in reads:
        if r in _SZ:
            n += _SZ[r]
        elif r.startswith("bytes") and r[5:].isdigit():
            n += int(r[5:])
        else:
            return None
    return n


def selftest():
    ok = True
    P = _proto()["recv"]

    def check(name, got, want):
        nonlocal ok
        good = got == want
        ok &= good
        print(("OK  " if good else "MAL ") + "%s: %s (esperado %s)" % (name, got, want))

    check("0x6613 DUELSCORE", len(duelscore()), _size(P["0x6613"]["reads"]))
    check("0x649b DUELSVRINFOMSG", len(LS.duelsvrinfomsg(2)), _size(P["0x649b"]["reads"]))
    check("0x6461 TIMEUP_WARNING", len(LS.timeup_warning(0, 60)), _size(P["0x6461"]["reads"]))
    check("0x4237 NPCPRESENTTYPE", len(npc_present_type(1)), _size(P["0x4237"]["reads"]))
    check("0x4310 DUELABORT", len(duel_abort()), _size(P["0x4310"]["reads"]))
    check("0x6202 = 40 B (u32,u32,8xu32; protocol.json colapsa el bucle)", len(LS.engine(5)), 40)
    st = {"players": {"0": "Player1"}, "wallet": {"mp": 9800, "dpp": 1000}}
    p = PS.profile(st, 0)
    pdu = PS.on_npc_duel_end(p, 51, "win")
    check("0x4140 PLAYERDATAUPDATE", len(pdu), _size(P["0x4140"]["reads"]))
    check("0x4800 no está en protocol.json (manejador por default del switch)", "0x4800" in P, False)
    check("0x4800 con 3 sobres", len(cardpacklist_notice([1, 2, 3])), 6)
    # secuencias
    seq = end_duel_msgs(0, WHY_LP)
    check("victoria por LP: ids", [m for m, _ in seq], [0x6202, 0x6613, 0x6202])
    d = seq[0][1]
    check("C_DUEL_END B/P0/P1/P2", struct.unpack(">5I", d[:20]), (0, 5, 1, 1, 1))
    d = seq[-1][1]
    check("0x4d tipo/P0/P1", (struct.unpack(">I", d[:4])[0], struct.unpack(">2I", d[8:16])), (0x4d, (1, 0)))
    check("0x6350 (0,0xd,0,10) es rendición", is_surrender(struct.pack(">4I", 0, 0xd, 0, 10)), True)
    check("comando 10 fuera del mazo no es rendición", is_surrender(struct.pack(">4I", 0, 0xb, 0, 10)), False)
    # B01 / bucle del equipo: el clic directo 11 (elegir carta u objetivo) NO es rendición, ni en el campo ni en el mazo
    check("clic directo 11 en un monstruo no es rendición", is_surrender(struct.pack(">4I", 0, 0, 0, 11)), False)
    check("clic directo 11 en el mazo no es rendición", is_surrender(struct.pack(">4I", 0, 0xd, 0, 11)), False)
    check("SÍ/NO de CUYesNoDialog = 1/0", (YES, NO), (1, 0))
    dlg = surrender_confirm_msgs()
    check("confirmación: lista A (dlg 6, clase 2) + vista 0x32 tipo 2",
          [struct.unpack(">4I", d[:16]) for _, d in dlg],
          [(0x60, 0, 1, 0), (0x61, 0, SURRENDER_TEXT, SURRENDER_CLASS), (0x1b, 0x32, 2, 0)])
    sent = []

    class _C:
        def send(self, mid, d):
            sent.append(mid)
    H = make_handlers(lambda: st, lambda s_: None, lambda: None, confirm_surrender=True)
    r1 = H["h_6350"](_C(), struct.pack(">4I", 0, 0xd, 0, 10))
    r2 = H["h_6423"](_C(), struct.pack(">I", NO))
    r3 = H["h_6423"](_C(), struct.pack(">I", YES))
    H["h_6350"](_C(), struct.pack(">4I", 0, 0xd, 0, 10))
    r4 = H["h_6423"](_C(), struct.pack(">I", YES))
    check("confirmación: ask / no / (no es nuestro) / end", (r1, r2, r3, r4), ("ask", "no", None, "end"))
    q = PS.profile(st, 0)
    e0 = PS.npcbin_get(q, 77)
    npc_duel_end(q, 77, "lose")
    e1 = PS.npcbin_get(q, 77)
    check("NPCBIN: derrota en b1 (LOSE), no en b2 (DRAW)", (e1[1] - e0[1], e1[2] - e0[2]), (1, 0))
    seq = on_client_surrender(struct.pack(">4I", 0, 0xd, 0, 10))
    check("rendición: ids", [m for m, _ in seq], [0x6202, 0x6202, 0x6613, 0x6202])
    check("rendición: cmd 0x23 del jugador 0", struct.unpack(">2I", seq[0][1][:8]), (0, 0x23))
    check("rendición: resultado 2", struct.unpack(">I", seq[1][1][8:12])[0], 2)
    check("0x6350 normal no es rendición", on_client_surrender(struct.pack(">4I", 0, 0xb, 0, 5)), None)
    de = DuelEnd(1, single_game_match=False)
    r1 = de.game_over(0, WHY_LP)[1]; r2 = de.game_over(0, WHY_LP)[1]
    check("match 2-0 decidido en la 2ª", (r1, r2), (False, True))
    de = DuelEnd(1)
    check("match de NPC cerrado tras 1 (P1 = 5)",
          struct.unpack(">2I", de.game_over(1, WHY_LP)[0][-1][1][8:16]), (2, FINISH_CLOSE))
    lob = lobby_after_duel_msgs(p, 51, RESULT_WIN, present=1)
    check("lobby: ids", [m for m, _ in lob], [0x4140, 0x4237, 0x4800])
    m = surrender_masks({(0, 0): 0x20})
    check("máscara del mazo con Rendirse (bit 10)", m[(0, 0xd)], 0x400)
    check("0x6611 con Rendirse = 110 B", len(LS.comid_field(m)), 110)
    # B50: motivo numérico de ocgcore -> WHY_* -> motivo del cliente (antes todo salía «LP»)
    check("B50 why_from_ocg 1/2/0x10/0x11/0x15/0x16/'error'/'loop'",
          [why_from_ocg(r) for r in (1, 2, 0x10, 0x11, 0x15, 0x16, "error", "loop")],
          [WHY_LP, WHY_DECKOUT, WHY_EXODIA, WHY_FINALCOUNT, WHY_DESTINY, WHY_LP, WHY_ABORT, WHY_LP])
    _e = end_duel_msgs(0, WHY_DECKOUT)[0][1]
    check("B50 deck-out -> 0x05 con motivo 2", struct.unpack(">3I", _e[8:20]), (1, 2, 2))
    _e = end_duel_msgs(None, WHY_ABORT)
    check("B50 empate/error -> 0x05 resultado 3 (drow) y 0x4d P0 = 3", (struct.unpack(">I", _e[0][1][8:12])[0],
          struct.unpack(">I", _e[-1][1][8:12])[0]), (RESULT_DRAW, RESULT_DRAW))
    try:
        ast.parse(open(os.path.abspath(__file__), encoding="utf-8").read())
        print("OK  ast.parse")
    except SyntaxError as e:
        ok = False
        print("MAL ast.parse", e)
    if b"\0" in open(os.path.abspath(__file__), "rb").read():
        ok = False
        print("MAL bytes nulos en el fichero")
    print("RESULTADO:", "todo bien" if ok else "HAY FALLOS")
    return ok


if __name__ == "__main__":
    sys.exit(0 if selftest() else 1)
