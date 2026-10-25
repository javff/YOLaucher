"""Traductor ocgcore (duelcore) <-> cliente YO2 (yo2.exe Ver.080912). Ver NOTES.md para la evidencia de cada comando.

    duel = YO2Duel(decks, first=0, cardmap=CardMap())
    out = duel.start()                       # [(0x6202, 40 B), ...]: DUEL_START + mazos impuestos
    out += duel.feed_many(eventos)           # eventos JSON de duelcore ("msg"/"select"/"end") -> mensajes al cliente
    cmd, out = duel.on_client(0x6350, datos) # jugada del cliente -> {"cmd":"respond","response":{...}} | {"cmd":"auto"} | None

Todo lo que sale hacia el cliente es una lista de (id_mensaje, bytes). Hacia duelcore, un dict sin "id" (lo pone run.py).
El jugador 0 es siempre el humano (= jugador 0 del cliente; duelcore ya lo traduce así).

Modelo:
  - El cliente identifica cada carta por uid = dueño + 2*serie. Aquí se lleva un ESPEJO del tablero del cliente:
    por jugador mazo (índice 0 = cima, como el cliente), mano, cementerio, desterradas, extra, 5 zonas de monstruo y
    6 de M/T (la 5 = campo). Cada hueco guarda un uid. ocgcore identifica por (controlador, location, sequence): el
    espejo traduce una cosa a la otra.
  - Identidad perezosa: las cartas del mazo y las ocultas de la NPC llevan una carta tapadera; justo antes de que una
    carta se haga visible se escribe su id real en el hueco con 0x6202 tipo 0x40 (y se registra uid -> carta).
  - El cliente nunca baraja por su cuenta sin que después se reimponga el orden con 0x40.
"""
from __future__ import annotations

import os
import random
import re
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_EMU = os.path.normpath(os.path.join(HERE, "..", ".."))        # tools/yo2emu (para importar yo2srv fuera del servidor)
if _EMU not in sys.path:
    sys.path.append(_EMU)
from yo2srv import compat as _compat  # noqa: E402
# U1: lockstep es UN solo módulo por proceso (sys.modules["research_lockstep"]): el del servidor (srv.LS) si ya está
# cargado y, si no (pruebas y herramientas que cargan este fichero solas), el que se cargue aquí la primera vez. Antes se
# hacía `sys.path.insert(lockstep); from proposal import …`: una tercera copia con el nombre pelado «proposal».
_compat.cargar("lockstep")
from research_lockstep import (  # noqa: E402,F401  (se importan, no se copian)
    C_DRAW, C_DUEL_END, C_PHASE, C_SHUFFLE, C_SUMMON, C_TURN_CHANGE, CMD_ACTIVATE, CMD_ATTACK, CMD_FLIP, CMD_LOOK,
    CMD_GENERIC, CMD_SET, CMD_SET_ST, CMD_SPSUMMON, CMD_SUMMON, CMD_TO_ATK, CMD_TO_DEF, PH_BATTLE, PH_END, PH_MAIN2,
    T_LIST_ADD, T_LIST_RESET, T_VIEW, T_VIEW1C, ZONE_BANISH, ZONE_DECK, ZONE_EXTRA, ZONE_GRAVE, ZONE_HAND, comid_field,
    comid_hand, duel_finish, engine, loc, request_input, set_card, setcommand, timeup_warning,
)
from cardmap import CardMap  # noqa: E402

# --- Comandos del motor que no están en proposal.py (FUN_00709cb0; ver NOTES.md §2) -------------------------------
C_LP_GAIN = 0x24        # P0 = cantidad, P1 = tipo, P2 = carta origen (id interno)                     PROBABLE
C_LP_DAMAGE = 0x25      # P0 = cantidad, P1 = tipo (0 batalla, 1 efecto, 2 coste), P2 = carta interna   PROBABLE
C_LP_SET = 0x26         # P0 = LP nuevos (LIFE_SET)                                                       PROBABLE
C_LP_RESET = 0x27       # LIFE_RESET (FUN_00716ba0): vista 0x10 -> FUN_007cf050 en los DOS marcadores de LP: pendiente
                        # y espera a 0, el número no se toca (§24)                                       C
C_CHAIN_SET = 0x12      # P0 = zona, P1 = índice en la mano, P2 = n.º de eslabón (CHAIN_SET)              PROBABLE
C_CHAIN_RUN = 0x13      # P0 = zona, P2 = n.º de eslabón (CHAIN_RUN)                                      PROBABLE
C_BATTLE_RUN = 0x17     # P0 = daño J0, P1 = daño J1, P2 = flags (cifras del cálculo de daño)             PROBABLE
C_ATTACK = 0x19         # P0 = ranura atacante, P1 = (ranura objetivo << 8) | jugador defensor            PROBABLE
C_DIRECT = 0x1a         # P0 = ranura atacante (ataque directo)                                           PROBABLE
C_ATTACK_END = 0x1c     # cierra el ataque en curso (P0 = 0, P1 = fase a la que saltar, 0 = ninguna)      DUDOSO
C_HAPPEN = 0x2f         # P0 = id YO2: muestra la carta que activa (DUEL_VIEW 0x43 + 0x21 CARD_HAPPEN)    PROBABLE
C_FIELD_CHANGE = 0x14   # FUN_00713240: P0 = id INTERNO de la magia de campo (0 = ninguna) -> DAT_012ca4c0 + DUEL_VIEW 7
#                         FIELD_CHANGE (fondo/BGM por FUN_00704100) y, al acabar la vista, FUN_006e4050  C (B23)
C_REVEAL = 0x2e         # P0 = zona | bits<<8, P1 = id INTERNO, P2 = uid: LA activación del oficial (DUEL_VIEW 0x13 de la mano
#                         + 0x43 + 0x21 CARD_HAPPEN: destello/sonido/panel). 1356 de 1356 eslabones de los duellogs oficiales
#                         la llevan justo antes de su 0x12, con el mismo jugador, zona y uid (NOTES §35)    C (oficial)
C_FLASH = 0x30          # P0 = id INTERNO: destello 0x21 sobre esa carta boca arriba del campo (FUN_00412981) PROBABLE
C_TO_GRAVE = 0x31       # P0 = zona | índice<<8, P1 = zona destino, P2 = uid; con animación de destrucción PROBABLE
C_POSITION = 0x34       # P0 = ranura, P1 = 1 voltea la cara; SIEMPRE alterna ATK/DEF; P2 = animación    PROBABLE
C_FLIP = 0x35           # P0 = ranura, P1 = 1: alterna la cara sin tocar la posición; P2 = flags          PROBABLE
C_MOVE = 0x3f           # P0 = origen (j | zona<<1 | uid<<6), P1 = destino (j | zona<<1 | índice<<6)      PROBABLE
C_MOVE_FIELD = 0x41     # P0 = origen (como 0x3f), P1 = destino (j | ranura<<1), P2 = def<<8 | boca arriba PROBABLE
C_TOKEN = 0x48          # P0 = destino empaquetado (como C_SUMMON), P1 = id INTERNO, P2 = valor           PROBABLE
C_HAND_SHUFFLE = 0x4e   # baraja la mano (semilla P1<<16 | P0)                                           PROBABLE
C_MONST_SHUFFLE = 0x49  # FUN_00714ea0: baraja las ranuras de monstruo de P2 (máscara, bits 0-4); semilla P1<<16 | P0 (NOTES MIS-1)  C
C_SET_MONSTER = 0x4f    # P1 = ranura<<1 | cara<<14 | def<<15, P2 = uid<<1 | gasta invocación normal   PROBABLE
C_SET_SPELL = 0x50      # P1 = ranura<<1 | cara<<14, P2 = uid (de la mano a M/T; boca arriba = activar)   PROBABLE
# «Invocación terminada» (NOTES §19). 0x2d (siempre), 0x4f (P2 bit0), 0x34 (P1 y P2 != 0) y 0x35 (P2 bit1) ponen el
# bit 3 de los flags de la ranura (+0x10) = «invocándose»; SOLO 0x45 lo quita (FUN_00714620 -> FUN_00707f20(j, r, 3,
# 0)). Con el bit puesto FUN_006fae40 devuelve el ATK/DEF IMPRESO sin mirar la lista de efectos (equipos 0x42, par
# EFFECT_SET_STATS 0x36, campo) y FUN_006ebe10 da el efecto por inactivo. El cliente es tonto (la cola del motor solo
# la llena el servidor, research/lockstep §0.2): el oficial TENÍA que mandar 0x45 tras cada invocación.  C (código +
# volcado en vivo del 06-10: flags 0x00200408 + emulación unicorn de FUN_006fae40 y FUN_00714620)
C_SUMMON_DONE = 0x45    # FUN_00714620: P0 = ranura, P1 = bits, P2 != 0 -> flag 0x20 (D: no se manda). P1: bit1 -> flag
#                         0x20000 (especial), bit2 -> 0x40040 (colocada), bit3 -> 0x4000 (volteo), ninguno y boca
#                         arriba -> 0x10000 (normal); bit0 = «invocada como toca» (palabra | 0x8000, solo si
#                         FUN_0071b470) y bit4 = la cuenta el rival: no se usan (D). Cuenta la invocación del turno
#                         (DAT_012c88a0), FUN_006e4050 y vista 0x25.  C (efecto) / P (qué bit corresponde a qué)
SUMMON_DONE_BITS = {"SUMMONED": 0, "SPSUMMONED": 2, "FLIPSUMMONED": 8}   # mensaje de ocgcore -> P1 de 0x45 (P)
SUMMON_DONE_SET = 4                                                      # P1 tras 0x4f con P2 bit0 (P)
# Invocación NORMAL (y por sacrificio) = 0x4f boca arriba + 0x2c (NOTES §23). 0x2d es la ESPECIAL: su vista 0x19 va
# con wEffect 2 (sin sonido) y lanza la vista 0x35 RUN_SUMMON (duel/effect/sp_summon.txt + reborn.wav, 0x1000000e,
# FUN_007a3100). 0x4f boca arriba: vista 0x19 wEffect 1 -> card_move.wav (0x1000000f) y pone el bit 3. 0x2c
# (FUN_00715230) es el ÚNICO que lanza la vista 0x34 = efecto de invocación normal (duel/effect/summon.txt: arco iris
# y estrellas, FUN_007a28a0, sin sonido propio). C (código + emulación unicorn); el orden 0x4f -> 0x2c: P (0x2c lee la
# carta de la ranura: tiene que estar ya)
C_SUMMON_FX = 0x2c      # P0 sin uso, P1 = ranura, P2 = tipo (byte bajo; 2 -> flags |= 0x200008) | gasta la invocación
#                         normal<<8 (FUN_006e5f10; NO: ya lo hace el bit0 de 0x4f). Vista 0x34 (j, ranura|cara, uid)  C
SUMMON_FX_KIND = 2      # qué tipo mandaba el oficial: D (solo cambia flags que 0x4f ya puso)
C_HAND_SHOW = 0x51      # P0 = uid de una carta de la mano: la enseña                                    PROBABLE
C_HAND_OPEN = 0x52      # P0 = 1/0: FUN_00713dc0 pone/quita el bit 1 de las banderas del jugador de B (FUN_006fa050) y, con 1, el
#                         bit 2; la vista 0x13 que lanza no hace nada (devuelve 0). Con el bit 1, duel_opp_hand_visible
#                         (FUN_006fa0e0) da por visible TODA la mano de ese jugador: se dibuja boca arriba. Así enseñaba el oficial
#                         la mano del rival en Trap Dustshoot / Crush Card Virus / Royal Tribute…: 0x52 [1] antes, y 0x52 [0]
#                         después (tras el 0x29 de la carta elegida), sin ningún 0x51 (que sí manda al sacar UNA carta de la
#                         mano y abre su panel de detalle)  C (código + 34 usos en los duellogs 2009) / P (cómo se ve)
C_DICE = 0x5d           # RUN_DICE: P0 = zona, P1 = resultado                                            DUDOSO
# --- «Motor: lo que faltaba» (NOTES §14). Manejadores leídos en work/handlers.c + desensamblado -------------------
C_COIN = 0x5c           # FUN_007135d0 -> DUEL_VIEW 0x38 (CCoin, «duel_effect_coin_new.txt», ctor FUN_007ab150):
#                         P0 = j | zona<<1 | n.º de monedas<<6 | «cantada»<<14, P1 sin uso, P2 = bit i = moneda i
#                         (1 = cara del ojo, frame 6; 0 = cara del puzle, frame 13). La moneda cuyo resultado == bit14
#                         se resalta al caer (sprites 3/4).  C (formato) / D (qué cara es «cara» y qué se cantaba)
C_COUNTER = 0x3c        # FUN_0070e3e0: P0 = ranura, P1 = delta (s16), P2 = tipo de contador del cliente. Tipo 0 =
#                         contador de magia (+0xc, tope FUN_0071c210 por carta: 0 si la carta no lleva), 1..14 en +0xc
#                         (se dibujan solo si la carta casa con la tabla 0xbeb748), >= 15 = entrada de efecto con id de
#                         la tabla (cualquier carta). Vista 0x26 (animación +/-) y FUN_0075b5c0 los dibuja.  C
C_FIELD_MOVE = 0x3d     # FUN_0070cd80: ranura -> ranura copiando el registro entero (efectos, contadores, vínculos con
#                         FUN_006e4290); si cambia el jugador marca 0x400 (cambio de control). P0 = j | ranura<<8, P1 =
#                         j | ranura<<8 (destino), P2 = boca arriba (va tal cual a +8). Vista 0x19 CARD_MOVE.  C
C_SWAP = 0x3e           # FUN_0070d0f0: intercambia dos ranuras ocupadas (Intercambio de Criaturas): P0 = j | ranura<<8,
#                         P1 = j | ranura<<8. Vista 0x1a CARD_SWAP. Si alguna está vacía, no hace nada.  C
# contador de ocgcore (counter_type sin los bits 0x1000/0x2000) -> tipo del cliente. Fuente: tabla 0xbeb748 (tipo ->
# id interno de la carta) + card.txt (sprites 14+: 魔力/ただの/…/アイス/ヴェノム/フォッグ) + el AddCounter del script
# de esa carta. El tipo 1 («ただのカウンター», cartas de FUN_0071c420) no se mapea: depende de la escena.  C/P
COUNTER_MAP = {0x01: 0,    # Spell Counter (Breaker, Royal Magical Library…)
               0x1b: 2,    # Clock Tower Prison
               0x11: 3,    # Vennominaga (Hyper Venom)
               0x06: 4,    # Crystal Tree
               0x25: 5,    # Spell Chronicle
               0x03: 6,    # Bushido (Six Samurai United)
               0x1c: 7,    # D - Formation
               0x05: 8,    # Realm of Light
               0x2a: 16,   # Magic Reflector
               0x0e: 17,   # A-Counter (Alien Grey)
               0x15: 18,   # Ice Counter (Ice Master)
               0x09: 19,   # Venom Counter (Venom Swamp)
               0x19: 20}   # Fog Counter (Cloudian - Ghost Fog)
# --- «Contador de turnos» y valor declarado de una carta (NOTES §22, 07-10). MSG_CARD_HINT de ocgcore -> +0xc ----------
C_SLOT_VALUE = 0x3a     # FUN_0070e300: +0xc de la ranura (byte bajo de P0) = P1 | P2<<16 si el byte alto de P0 es 0
#                         (si no, se le SUMA). Sin vista (0x25 «hecho»). Emulado con unicorn sobre el exe.  C
C_TURN_COUNT = 0x3b     # FUN_00711b50 (antes «+1 contador de magia»): CONTADOR DE TURNOS de la ranura P0. Solo si
#                         FUN_0071bfa0(carta) >= 1 (TURN_CARDS); si no, no hace nada. P2 = 0: P1 = 0 -> +0xc = 0 sin
#                         vista; P1 != 0 -> +0xc += 1 y vista 0x24 (reloj de arena 砂時計 + número, sprites 6/7 de
#                         card.txt; FUN_0075ac50 lo pinta siempre que 1 <= +0xc < tope: Espadas = 1, 2 y el 3 solo en la
#                         animación). P2 != 0: número P2 sobre el jugador (ranura 0x10)  C (emulado) / D (P2)
CHINT_TURN, CHINT_CARD, CHINT_RACE, CHINT_ATTRIBUTE = 1, 2, 3, 4      # constant.lua (CHINT_NUMBER 5, DESC_ADD 6/7)
# FUN_0071bfa0: id interno -> tope del contador de turnos. Solo los > 0 viven en la ranura (los negativos: Final
# Countdown -20, Crush Card Virus -3, Gold Sarcophagus -2…, se cuentan fuera: P2 de 0x3b, D). Tabla sacada emulando
# la función para todos los ids 0..0x23ff (C). 0xffff = sin tope (Stim-Pack, Castle of Dark Illusions, Blockman…)
TURN_CARDS = {0xfee: 0xffff, 0xff9: 0xffff, 0x1009: 0xffff, 0x1102: 3, 0x130d: 0xffff, 0x131a: 0xffff, 0x1400: 2,
              0x149d: 2, 0x14ac: 3, 0x1529: 0xffff, 0x159c: 2, 0x15a0: 0xffff, 0x15ee: 0xffff, 0x17a1: 2,
              0x180f: 0xffff, 0x1810: 0xffff, 0x187c: 2, 0x1909: 0xffff, 0x1a7e: 2, 0x1aa3: 3, 0x1aa9: 3, 0x1c91: 2,
              0x1dd1: 2, 0x1de2: 2, 0x1ee4: 2}
# §34: cartas con tope NEGATIVO en FUN_0071bfa0 (se cuentan fuera de la ranura). El oficial mandaba 0x3b con P0 = id
# interno, P1 = 1, P2 = cuenta (1, 2, 3…) al jugador del turno que se cuenta (duellogs 2009: Crush Card Virus 86 veces,
# Gold Sarcophagus 106; nada de Final Countdown). Valor = |tope|. 0xecc no tiene carta en YO2.  C (tabla y formato)
TURN_CARDS_NEG = {0xecc: 3, 0x123b: 3, 0x12c8: 4, 0x169c: 20, 0x1811: 2, 0x188c: 3, 0x18d5: 2, 0x1bb3: 2, 0x1bf1: 3}
FINAL_COUNTDOWN = 0x169c   # cuenta TODOS los turnos (EVENT_TURN_END): el número es del que la activó (su dueño)  P
# FUN_0075a030: cartas cuyo +0xc se pinta como lo declarado (icono de raza 1..20 / atributo 1..6 de card.txt
# sprites 5/4, o la imagen de la carta por id interno). Cada una lleva su SetHint(CHINT_…) en el script de ocgcore,
# salvo Tualatin (0x197d), que no lo manda.  C (dibujo) / P (que el oficial lo escribiera con 0x3a)
DECL_RACE = frozenset({0x1357, 0x1358, 0x14d1, 0x14cc})   # DNA Surgery, Regulation of Tribe, Array of Revealing
#                                                           Light, The Hunter with 7 Weapons
DECL_ATTR = frozenset({0x145b, 0x171f, 0x1a19, 0x197d})   # Scroll of Bewitchment, DNA Transplant, Elemental Absorber
DECL_ATTR_BITS = frozenset({0x19c2})                      # Disciple of the Forbidden Spell: bit i = atributo YO2 i
DECL_CARD = frozenset({0x1368, 0x1b67})                   # Prohibition, Cloak and Dagger: +0xc = id interno
# Frases de la NPC (research/ia/LOCAL.md §2.3; solo hablan si AUTODUELSTART 0x43a0 lleva el último byte = 1):
C_NPC_ANNOUNCE = 0x60   # DUEL_VIEW 0x40: P0 = id interno o 0x8000|uid -> frase 0x0d/0x1c (invoca)      PROBABLE
C_NPC_CHAIN = 0x62      # DUEL_VIEW 0x42 -> frase 0x1e (encadena)                                         PROBABLE
C_NPC_TURN_END = 0x03   # DUEL_VIEW 0x48 + marca cementerio/desterradas -> frase 0x17 (fin de turno)       PROBABLE

T_REVEAL = 0x1d         # 0x6202 tipo 0x1d: uid -> carta (solo la tabla de uids, no la palabra del hueco)
# ATK/DEF: el cliente los CALCULA (FUN_006fae10 -> 006fae40: card_prop + lista de efectos de la ranura, con valores
# fijos por carta); ocgcore no se los manda nunca. Para imponer el valor de ocgcore (agente 9, análisis estático):
C_EFFECT_ADD = 0x36     # P0 = ranura | tipo<<8 (3 = hasta que deja el campo o se pone boca abajo), P1 = id, P2 = valor
C_EFFECT_DEL = 0x37     # P0 = ranura, P1 = id, P2 = 1 borra todas las entradas con ese id
EFFECT_SET_STATS = 0x1c5f   # id cuyo par de entradas FIJA ATK (1.ª) y DEF (2.ª) y anula lo anterior  C (código) / P (vivo)
# Vínculo carta -> carta (equipo, Círculo Atahechizos, Call of the Haunted…). NOTES «Vínculo carta-objetivo / equipo».
# En la lista de efectos de una ranura, las entradas de TIPO 6..11 son vínculos: id = jugador | ranura<<8 de la carta
# ORIGEN (FUN_006e4ca0, FUN_006e2870, FUN_006e4290); tipos >= 10 = equipo (FUN_006e4a30 / FUN_006e4f60 los cuentan
# como equipos; FUN_006fae40 caso 10/0xb suma el bono de cada carta de equipo conocida).  C
C_LINK = 0x42           # FUN_007119f0: P0 = origen (j | ranura<<8), P1 = destino (j | ranura<<8), P2 = tipo.
#                         DUEL_VIEW 0x23 (CEquipEffect: la imagen de la carta origen vuela hasta el destino, 24
#                         fotogramas + sonido 0x1000000a) y al acabar FUN_006e2930(destino, tipo, id = P0, 0).  C
C_LOCKON = 0x29         # FUN_0070e920: P0 = zona (campo: ranura 0..10; 0xb mano), P1 = uid, P2 = 0 -> DUEL_VIEW
#                         0x18 CARD_LOCKON (animación de estado 1 del objeto de la carta). Zonas 0xe/0xf MUEVEN la
#                         carta a la cima de su pila (FUN_006f7c10/7d20) y abren la vista 0x17 (cementerio): así señalaba el
#                         oficial una carta objetivo del cementerio (66 en los duellogs; NOTES §35, lockon_pile)  C
# --- Sacrificio y fusión (NOTES §15, agente «duelo2»). Cadenas de depuración del exe (SJIS) junto a cada vista:
# 0x29 «生贄モンスターセット», 0x2b «生贄召喚開始», 0x2c «融合召喚モンスターセット», 0x2e «融合召喚開始_DoMaterial»,
# 0x2a/0x2d «融合＆生贄召喚キャンセル», 0x36 «DUEL_VIEW_RUN_FUSION».  C
C_TRIBUTE = 0x46        # FUN_007102b0: P0 = ranura, P1 = zona destino (0xe), P2 = uid. Vista 0x2b (si no hay marca de
#                         0x4a la crea ya «en marcha», FUN_007eff20 modo 1) y luego mueve la carta a la pila del DUEÑO
#                         con CARD_MOVE | 0x80000 (como 0x31, pero sin la animación de destrucción).  C
C_TRIBUTE_MARK = 0x4a   # FUN_00714970: P0 = ranura -> vista 0x29: marca de sacrificio sobre la carta (FUN_007efd20,
#                         modo 0). La «arranca» el 0x46 siguiente.  C (código) / D (que el oficial la mandara)
C_FUSION_MARK = 0x4b    # FUN_00714a00: P0 = zona, P1 = índice del material, P2 = uid. Guarda la palabra de la carta en
#                         la tabla DAT_012ccb34[P1] y vista 0x2c: marca de material en su sitio (campo o mano).  C
C_FUSION_MAT = 0x4c     # FUN_00714a90: igual que 0x4b + vista 0x2e, que SOLO arranca una marca ya puesta (FUN_007f0330:
#                         sin 0x4b antes no se ve nada en el campo). P0 = 0xe: sube la carta a la cima del cementerio
#                         (FUN_006f7c10 + vista 0x17); P0 = 0x10: el material ya se fue (solo apunta, sin vista).  C
C_FUSION_RUN = 0x4d     # FUN_00714c90: P1 = uid del monstruo de fusión, P2 = n | flag<<8. Vista 0x41 (vacía en
#                         080912), cuelga al uid n efectos tipo 3 id 0x12e5 = uid de cada material (flag 0) y vista 0x36
#                         RUN_FUSION(jugador, carta del uid, n). No mueve nada: la invocación sigue siendo 0x2d.  C
HINTMSG_RELEASE, HINTMSG_FMATERIAL = 500, 511   # HINT tipo 3 de ocgcore antes de SELECT_TRIBUTE / de los materiales
# --- §33 Invocación por Ritual (SELECT_SUM). Lo que mandaba Konami (duellogs oficiales 2009 de research/poc_modtools,
# 13 rituales de Contract with the Abyss y Advanced Ritual Art, mismas secuencias en todos):  C
#   1) monstruo de ritual de la mano: lista A [(286, 2)] + 0x1b B=0x32 (0xff, 286, 1) + 0x1a (8, 0, 0)
#      «Selecciona monstruo(s) desde la mano para Invocación Especial.»
#   2) sacrificios de la mano/campo: 0x1f (estrellas, 0xff) + lista A [(207, 2)] + 0x1b B=0x32 (0xe, 207, estrellas)
#      (CURitualDialog: pinta tantas estrellas como P2 y enciende las que ya se han cubierto, FUN_00779f90) + 0x1a
#      (8, 0, 0). Por cada carta elegida: 0x1b B=0x29 (jugador, zona/ranura, uid) = DUEL_VIEW 0x29 «生贄モンスターセッ
#      ト_SetMaterial» (marca sobre la carta) + 0x1f (estrellas que faltan, nivel de esa carta) y, si faltan, otra 0x1a.
#      0x1f = tipo 0x1f del ejecutor: DAT_012c872c = P0 (lo que falta; el diálogo se cierra con 0, FUN_00779f20, y con
#      0xff no se pinta, FUN_0077a290), DAT_012c8730 = P1.
#   3) materiales del MAZO (Advanced Ritual Art): lista A [(289, 2)] + 0x1b B=0x32 (0xfe, 289, 0) + lista 0x6610 modo 6
#      (elegir 1) + 0x1b B=0x33 (0, 6, 0), una carta cada vez «De la lista, selecciona carta(s) en tu Deck…».
#   4) después: 0x46 (zona: ranura o 0xb = mano, 0xe, uid) por cada sacrificio de la mano/campo; los del mazo, 0x3f.
T_STARS = 0x1f              # 0x6202 tipo 0x1f: DAT_012c872c = P0, DAT_012c8730 = P1 (T_SETPAIR de proposal.py)
VIEW_SET_MATERIAL = 0x29    # DUEL_VIEW 0x29 (P0 jugador, P1 zona YO2 / ranura, P2 uid): marca de material
DLGT_RITUAL = 0xe           # 0x1b B=0x32 P0=0xe -> CURitualDialog (vtable 0xbec6d0)
DLG_RITUAL_MONSTER, DLG_RITUAL_STARS, DLG_RITUAL_DECK = 286, 207, 289
KIND_RITUAL = 8             # clase de 0x1a de las dos peticiones del ritual (oficial)
KIND_OPP_HAND, DLG_PICK_OPP_HAND = 8, 244   # §40: clase de 0x1a y panel «Selecciona 1 carta desde la mano de tu oponente.» (oficial)
HINTMSG_SPSUMMON = 509      # HINT tipo 3 de ocgcore antes del SELECT_CARD del monstruo de ritual
STARS_NONE = 0xff
LINK_STALE = (-1, -1)   # stat_sent de un monstruo al que se le colgó un vínculo DESPUÉS de su par EFFECT_SET_STATS
# Ola 4 (2026-10-06), comprobado EMULANDO el exe (unicorn): 0x36 -> FUN_0070df30 -> FUN_006e2930 añade al final de la
# lista de la ranura; FUN_006fae40 caso 0x1c5f toma ATK = valor de esa entrada, DEF = valor de la SIGUIENTE y se la
# salta. Que el DEF vaya en un 2.º 0x36 es lo correcto (0x37 = FUN_0070e0b0 -> FUN_006e2c10 = BORRAR). Emulado:
# 500/2000 + 0x36(1200) + 0x36(2000) -> 1200/2000; + 0x37(P2=1) -> 500/2000. El número del campo lo pinta
# FUN_00759430 en CADA fotograma (interpola +0x4c/+0x50 hacia FUN_006fae10): no hace falta DUEL_VIEW de repintado.
# El panel de detalle no llama a FUN_006fae10: enseña el ATK/DEF IMPRESO.  C
T_VIEW_LIST = 0x33      # 0x6202 tipo 0x1b B=0x33: DUEL_VIEW 0x33 RUN_LIST -> CWindowDeckList (P0 j, P1 = MODO, no zona)
# §26: DUEL_VIEW 0x37 RUN_DETAIL (0x73dd80 caso 0x37 = 0x7416c6): id = DAT_012ca4ca[P0*5] y el panel de detalle de
# la izquierda (CCardDetailViewDlg, escena+0x138) pasa a esa carta (FUN_00742eb0: +0x438 id, +0x440 uid). Nada más.  C
T_VIEW_DETAIL = 0x37
# Espada de apuntar (vista de batalla DAT_012a6790: +0x14 estado, +0x1c/+0x20 jugador/ranura del atacante). Ejecutor
# de 0x6202 (FUN_00721020, desensamblado): tipo 0x1b -> FUN_0073dd80(B, P0, P1, P2) con u32 completos.  C
T_AIM = 0x47            # 0x6202 tipo 0x47 -> FUN_00754340 sin parámetros (= DUEL_VIEW 0xb). No se usa: 0x1b B=0xb
VIEW_BATTLE_INIT = 0x0a  # DUEL_VIEW 0xa P0 = jugador -> FUN_00754040: estado 1 (como al entrar en batalla), atacante -1
VIEW_BATTLE_SELECT = 0x0b  # DUEL_VIEW 0xb -> FUN_00754340: estado 3 = espada desde el atacante hacia el ratón
#                             (FUN_00754bb0: FUN_007bea20(+0x1c, 0, +0x20) + posición del ratón; el mismo sprite +0x6c
#                             que la flecha del estado 4). FUN_00754600 («ocupada») solo es 4/6/7: el 3 no bloquea nada
VIEW_ATTACKER = 0x0c    # DUEL_VIEW 0xc P0 = j | ranura<<8, P2 < 0 -> solo FUN_007542b0 (estado 2, fija el atacante;
#                         lee DAT_012c88b4[ranura], global). Con P2 >= 0 lanzaría la flecha (FUN_00754360): P2 = -1
M_LIST = 0x6610         # SELDUELLIST_DATA (FUN_006929b0): u8 0, u32 modo, u32 0, u32 n, u32 0, n x (u16 id YO2, u16
                        # serie, u16 flags (bit0 = dueño), u16 0); ítem = palabra de carta. Audit duelo_menus §7  C
                        # El id del ítem es el id YO2 (el de la red, como 0x40 P3), NO el interno: la ventana lo pasa
                        # por FUN_0073bc50 (YO2 -> interno) antes de pintar/nombrar (FUN_00768c50 -> 0073bc50/0073bcc0)
                        # y al anunciar (FUN_0076a3f0). P0 de B=0x33 = 0 (el valor probado en vivo con «ver pilas»)
LIST_ONE, LIST_EXACT0, LIST_UPTO0 = 6, 6, 0x27   # modo 6 = elegir 1; 6+n = exactamente n (n<=0x20); 0x27+max (<=0x21)
LIST_ZONES = (ZONE_DECK, ZONE_GRAVE, ZONE_BANISH, ZONE_EXTRA)
LIST_FUSION = 0xfe        # modo de 0x6610 que va a 0x12be180 (lo lee CFusionEffectTask, FUN_007b4690), sin ventana  C
# §23: cartas que pinta el CUYesNoDialog (DUEL_VIEW 0x32 tipo 2 y 3). Su dibujo (0x774650, desensamblado) solo pinta
# cartas si el modo de la última lista 0x6610 (DAT_012ccb24) es 0xff: las DAT_012ccb2c primeras de DAT_012ccb34
# (FUN_007202a0: id & 0x3fff). El tipo 3 que se cierra solo vuelve el modo a 0. 0x6610 no abre ventana (FUN_006929b0
# solo copia a DAT_012ccb20). Sin esta lista el diálogo enseñaba la carta de la última lista que hubiera (la
# «miniatura rara», pvp1010 22:34:08: Command Knight en el aviso de Trap Hole).  C (código) / P (que el oficial la
# mandase así)
LIST_DIALOG = 0xff
# §23: SELECT_EFFECTYN -> lista A [(id interno, 4), (dlg, 0)] con dlg 250 «¿Activar el efecto de "%s"?» (329 si la
# carta está en el cementerio, 328 en la mano), el mismo formato clase 4 + plantilla clase 0 que ya va en vivo con
# DLG 7/8. Antes [(id interno, 4)] sola: sin plantilla, el texto no decía de qué carta era.  P (textos del cliente)
DLG_EFFECT_YN, DLG_EFFECT_YN_HAND, DLG_EFFECT_YN_GRAVE = 250, 328, 329
DLG_PICK_FIELD, DLG_PICK_TRIBUTE = 274, 18   # «Selecciona una carta objetivo en el campo.» / «… como Sacrificio.»
DLG_LINK_EFFECT, DLG_LINK_CARD = 7, 8   # «El efecto de "%s" está activado. ¿…Cadena?» / «%s está activado. ¿…?»   C
DLG_CHAIN = 269         # dlg_text 269 «¿activar el efecto de la carta?» (clase 0 de la lista A = dlg_text)          C
# §25: SELECT_YESNO SIN carta cuyo `description` es un texto del sistema de ocgcore (!system N de strings.conf) ->
# el texto del cliente con el mismo significado (dlg_text es/ja), en lista A [(dlg, 2)] como el genérico 269. Lo que no
# está aquí sigue con 269. 30: C (llega en las trazas); 31: C (texto) / P (que llegue); 32/94/210: P. NOTES §25
YESNO_SYS_DLG = {30: 223,    # «A replay occurred, continue the attack?» -> «Reproducir batalla. ¿Continuar para atacar?»
                 31: 225,    # «Attack directly?» -> «¿Atacarás directamente al jugador?»
                 32: 221,    # «Conduct another Battle Phase?» -> «¿Comenzarás la 2ª Battle Phase?»
                 94: 270,    # «Activate this card's effect now?» -> «¿Usar el efecto ahora?»
                 210: 93}    # «Continue selecting cards?» -> «¿Seleccionar otra carta?»
# §41 (OPT2): SELECT_YESNO / SELECT_EFFECTYN de UNA carta -> su texto del cliente. ocgcore (wasm 0.1.2) manda aux.Stringid(código,
# n) como código << 20 | n (antes se leía como código << 4 | n y NINGUNA carta con description llegaba a su texto: Ryko, etc.).
# (passcode, n) -> (dlg, carta que va con su nombre (clase 4): None = ninguna, "self" = la propia, o un passcode, número
# (clase 1) que va detrás de la plantilla o None). Lista A del oficial (duellogs 2009): texto sin argumentos = [(dlg, 2)];
# con la carta = [(id interno, 4), (dlg, 0)]; con un número = [(id, 4), (dlg, 0), (cifra, 1)]. Confianza: C = el
# duellog oficial trae ESE texto para ESA carta; P = emparejado por el texto del cliente (dlg_text_e vs cards.cdb)
YESNO_DLG = {
    (21502796, 1): (353, None, None),      # Ryko, Lightsworn Hunter «Destroy this Card?»                      C (2 duellogs)
    (24140059, 0): (305, None, None),      # A Cat of Ill Omen «Will you add the designated card to your hand?» C (2 duellogs)
    (23927567, 0): (305, None, None),      # An Owl of Luck (mismo "Add to hand?")                             P
    (55256016, 0): (104, None, None),      # Judgment of Anubis «Do you wish to destroy a monster?»            C (duellog)
    (28553439, 1): (104, None, None),      # Magical Dimension "Destroy 1 monster?"                            P
    (69270537, 0): (89, None, None),       # Contact Out «Special Summon a Fusion-Material Monster to the Field?» P (texto casi idéntico)
    (44656491, 0): (232, "self", 100),     # Messenger of Peace «To maintain "%s" requires %dLP. Pay?»           C (duellog)
    (82432018, 0): (232, "self", 1000),    # Mask of Brutality                                                 C (duellog)
    (22359980, 0): (232, "self", 2000),    # Mirror Wall                                                       P (mismo mantenimiento)
    (21598948, 1): (232, "self", 500),     # Fairy Box                                                         P
    (24874630, 0): (232, "self", 1000),    # Fiend's Sanctuary                                                 P
    (92394653, 1): (232, "self", 500),     # Spirit's Invitation                                               P
    (81332143, 2): (299, 14731897, None),  # Yu-Jo Friendship «Will you show "Unity" to coerce ... handshake?»    P (299 es el único texto con "Unity")
}
# SELECT_EFFECTYN con la description de SISTEMA + la carta (Duel.SelectEffectYesNo(tp, c, 96) de los Six Samurai, Great
# Shogun Shien, Colosseum…): «¿Destruir otra carta en lugar de "%s"?» = dlg 333 con el nombre. C (duellogs: Shien y Zanji)
YESNO_SYS_CARD = {96: 333}
REASON_NONE = 0xff      # u8 de motivo de 0x6611/0x6612 (solo se lee con máscara 0): texto 700+u8 de dlg_text; 0 = «Se
                        # necesita un tributo…» (lo que salía en el cementerio); 0xff = sin texto (FUN_007c2270)  C
# --- B42: diálogos del cliente para las decisiones que antes tomaba bot.ts (NOTES §18) -----------------------------
# DUEL_VIEW 0x32 (0x6202 tipo 0x1b B=0x32, P0 = tipo, P2 = parámetro) -> FUN_0073dd80 caso 0x32; cada diálogo contesta
# con 0x6423 u32 (FUN_0071f8c0 -> FUN_00791670 -> FUN_00692190). Texto = lista A (0x60/0x61; clase 2 = texto dlg).
DLGT_SELECT = 4      # CUSelectDialog (FUN_00774ee0): opciones = textos dlg de los ítems de 0x6610 (solo los 4 primeros,
#                      FUN_0073dbf0(…, 4)); se ENSEÑA el ítem i si su bit 15 (flags bit 1) = 1 (máscara [0x392],
#                      0x776100); 0x6423 = índice ORIGINAL del ítem ([0x38b + k] = i)  C
DLGT_RACE = 6        # CUSelectTypeDialog (FUN_0077a320/0077a400): P2 = máscara, bit i = raza YO2 i (1..20); elige 1;
#                      0x6423 = i (0x77acb9)  C
DLGT_ATTR = 7        # CUSelectAttributeDialog (FUN_0077ad20/0077ae10): P2 = cuántos | máscara<<16 (bit i = atributo
#                      1..6); OK solo con exactamente «cuántos»; 0x6423 = OR de 1<<i (0x77b40f)  C
DLGT_STAND = 8       # CUSelectStandDialog (FUN_00776510): P2 = uid (imagen ca4ca[uid]) | (cara del DEF)<<16. Dos
#                      cartas: 0 = boca arriba en ATK, 1 = DEF (boca arriba si P2>>16, boca abajo si no): palabra
#                      0x20 | cara<<14 | i<<15 (0x776ea9-0x776ec2); 0x6423 = 0/1 (FUN_00776a30)  C
DLGT_NUM = 0xb       # CUSelectNumDialog (FUN_00778160/00778280): P2 = dlg de la etiqueta (formato con %d, FUN_00743ae0)
#                      | mín<<16 (4 bits) | máx<<24; empieza en mín; 0x6423 = el número (FUN_00778ba0)  C
# Descarte por límite de mano (NOTES §20). DUEL_VIEW 0x32 tipo 0xd (salto 0x73fe9b, tabla 0x741ccc) -> CUDiscardDialog
# (FUN_00778dd0, ctor FUN_00778ee0, vtable 0xbec570; cadena de depuración «CUDiscardDialog 手札を捨ててください»,
# 0xccc6e0). No se crea si hay una ventana modal (CWindowTaskModel en DAT_012a9070+0x108). Panel NO modal de 314 px
# (FUN_00779520) con el texto de la lista A y debajo una carta pequeña (data/mini_list) por cada carta que FALTA por
# descartar = u16 DAT_012cc3aa (FUN_00722670, leído en cada fotograma por FUN_00779660). Con 0 la tarea acaba (el
# panel se cierra solo). Sin botones: NO contesta 0x6423; las cartas se eligen con las máscaras de la mano. El tipo
# 0xc no hace nada en 080912 (va al final del switch).  C (código) / P (que el oficial lo usara en la End Phase)
DLGT_DISCARD = 0xd
T_SETVAR16 = 0x1e    # 0x6202 tipo 0x1e: u16 DAT_012cc3a8[P0] = P1 (ejecutor, 0x7217af-0x7217c2): el ÚNICO escritor de
#                      DAT_012cc3aa (aparte de ponerlo a 0 la vista de fusión, FUN_0072af90). La versión de tutorial de
#                      DUEL_VIEW (FUN_0074bfa0) lo pone justo antes de crear CUDiscardDialog  C
VAR_DISCARD = 1      # índice 1 de esa tabla = DAT_012cc3aa = cartas que quedan por descartar  C
DLG_DISCARD = 235    # dlg 235 «Descarta de tu mano.» (j «手札を捨ててください。» = el texto de la cadena de CUDiscardDialog)  P
HINTMSG_DISCARD = 501   # HINT tipo 3 de ocgcore antes del SELECT_CARD de descarte (HINTMSG_DISCARD)
OCG_PHASE_END = 0x200
HAND_LIMIT = 6       # ocgcore: en la End Phase, SELECT_CARD min = max = mano - 6 sobre la mano entera
LIST_ANNOUNCE = 0x4b  # CWindowDeckList modo 0x4b: el cliente pone TODAS las cartas (FUN_007689d0, 0x6610 no hace falta);
#                       al pulsar: 0x6423 id INTERNO, 0x6432 i 1 y al cerrarse 0x6423 1 (FUN_0076a3f0). No se cancela  C
# raza / atributo del cliente (dlg 500+i / 530+i, FUN_0073be50 / 0073bec0) -> bit de ocgcore  C (nombres = tablas)
YO2_RACE = {1: 0x2000, 2: 0x10, 3: 0x8, 4: 0x80, 5: 0x40000, 6: 0x100, 7: 0x20, 8: 0x20000, 9: 0x10000, 10: 0x800,
            11: 0x4000, 12: 0x8000, 13: 0x400, 14: 0x40, 15: 0x1, 16: 0x200, 17: 0x4, 18: 0x2, 19: 0x1000, 20: 0x80000}
YO2_ATTR = {1: 0x10, 2: 0x20, 3: 0x2, 4: 0x4, 5: 0x1, 6: 0x8}
DLG_SEL_RACE, DLG_SEL_ATTR, DLG_SEL_POS, DLG_SEL_EFFECT, DLG_SEL_CARDTYPE = 241, 242, 326, 259, 260
DLG_NUM_LEVEL, DLG_NUM_LP, DLG_NUM_QTY, DLG_NUM_COUNTERS = 280, 301, 594, 317   # textos del diálogo de número
LBL_NUM, LBL_LEVEL, LBL_LP1000, LBL_LP100, LBL_COUNTERS = 283, 304, 302, 303, 318   # «%d», «Nivel %d», «%d000LP»…
# §41: texto y etiqueta del diálogo de número que usaba cada carta (passcode del último eslabón -> (dlg del título, dlg de la
# etiqueta)). Duellogs oficiales 2009: Card Trooper 0x1b 0x32 0xb con lista A [(330, 2)] y P2 = 331 | 1<<16 | 3<<24 (C: 5 de 5);
# Reasoning 280 + «Nivel %d» (la heurística de _num_plan ya lo daba). Sixth Sense: 296 «Elige dos números del 1 al 6» (P: es el único
# texto del cliente de números 1-6; el 297 es el aviso de lo que eligió el rival)
NUMBER_TEXTS = {85087012: (330, 331),     # Card Trooper «Decide cuántas cartas mandas al Cementerio» / «%d carta(s)»
                3280747: (296, LBL_NUM)}  # Sixth Sense
# SELECT_OPTION: descripción de ocgcore (sistema o aux.Stringid(código, n) = código<<4 | n) -> texto dlg del cliente.
# Los dlg 406-480 son, uno a uno, los efectos a elegir de cartas concretas de la época (Fuh-Rin-Ka-Zan, Kunai with
# Chain, Don Zaloog…): el servidor oficial mandaba esos ítems. Emparejado por el texto (dlg_text_e vs cards.cdb)  P
def _sid(code, n):
    return code << 4 | n


OPTION_DLG = {70: 440, 71: 441, 72: 442,                                  # DECLTYPE: monstruo / mágica / trampa
              _sid(967928, 0): 465, _sid(967928, 1): 466,                 # Penalty Game!
              _sid(1781310, 0): 458, _sid(1781310, 1): 459, _sid(1781310, 2): 421, _sid(1781310, 3): 449,  # Fuh-Rin-Ka-Zan
              _sid(1804528, 1): 426, _sid(1804528, 2): 427,               # Dark Coffin
              _sid(6967870, 1): 431, _sid(6967870, 2): 423,               # Dark Scorpion - Cliff
              _sid(8842266, 0): 406, _sid(8842266, 1): 419,               # Poison of the Old Man
              _sid(10032958, 1): 462, _sid(10032958, 2): 463, _sid(10032958, 3): 464,   # Divine Dragon - Excelion
              _sid(12183332, 1): 424, _sid(12183332, 2): 425,             # Card Shuffle
              _sid(20065322, 0): 436, _sid(20065322, 1): 478,             # The Flute of Summoning Kuriboh
              _sid(26931058, 1): 452, _sid(26931058, 2): 453,             # Formation Union
              _sid(29424328, 1): 438, _sid(29424328, 2): 439,             # Diabolos (cima / fondo)
              _sid(30461781, 0): 448, _sid(30461781, 1): 449,             # Legacy of Yata-Garasu
              _sid(37390589, 1): 416, _sid(37390589, 2): 417, _sid(37390589, 3): 418,   # Kunai with Chain
              _sid(39978267, 1): 454, _sid(39978267, 2): 455,             # Cyber Raider
              _sid(40044918, 1): 470, _sid(40044918, 2): 471,             # Elemental HERO Stratos
              _sid(40695128, 2): 438, _sid(40695128, 3): 439,             # Maharaghi
              _sid(48576971, 1): 448, _sid(48576971, 2): 422,             # Necklace of Command
              _sid(48768179, 1): 434, _sid(48768179, 2): 435,             # Dark Scorpion - Gorg
              _sid(53839837, 1): 440, _sid(53839837, 2): 441, _sid(53839837, 3): 442,   # Vampire Lord
              _sid(58074572, 0): 406, _sid(58074572, 1): 407,             # Mooyan Curry
              _sid(61587183, 1): 432, _sid(61587183, 2): 433, _sid(61587183, 3): 438, _sid(61587183, 4): 439,  # Chick
              _sid(74153887, 1): 436, _sid(74153887, 2): 437,             # Dark Scorpion - Meanae
              _sid(76754619, 0): 408, _sid(76754619, 1): 409,             # Pyramid Energy (ATK / DEF)
              _sid(45653036, 0): 479, _sid(45653036, 1): 480,             # Rain Storm (§41: destruir 1 Mágica/Trampa / 2 cartas del rival)
              _sid(76922029, 1): 422, _sid(76922029, 2): 423,             # Don Zaloog
              _sid(80161395, 1): 408, _sid(80161395, 2): 409,             # Mystik Wok (ATK / DEF)
              _sid(86198326, 0): 408, _sid(86198326, 1): 409,             # 7 Completed
              _sid(93130021, 1): 467, _sid(93130021, 2): 468, _sid(93130021, 3): 469,   # Victory Viper / Power Capsule
              _sid(98045062, 1): 446, _sid(98045062, 2): 447}             # Enemy Controller
# §41 (OPT2): SELECT_OPTION de DOS opciones que en el cliente 2008 eran una pregunta SÍ/NO (no una ventana de textos): (passcode,
# n0, n1) -> (dlg, carta del nombre, número). SÍ = la opción de índice 0. Textos del cliente: 233 «Para mantener "%s" en el Campo
# hace falta 1 monstruo como Tributo. ¿Quieres ofrecer un monstruo?» (Yubel: Tributo de otro monstruo o se destruye; P),
# 298 «¿Darás la mano a tu oponente?» (Yu-Jo Friendship: el rival acepta o rechaza el apretón; 299/300 son su pareja; P)
OPTION_YN = {(78371393, 3, 4): (233, "self", None),      # Yubel
             (81332143, 0, 1): (298, None, None)}        # Yu-Jo Friendship


def _option_key(desc):
    """Descripción de SELECT_OPTION -> clave de OPTION_DLG. Este ocgcore (wasm 0.1.2) manda aux.Stringid(código, n) como
    código << 20 | n (verificado con Enemy Controller / Stratos en duelcore); la tabla se emparejó con código << 4 | n.
    Los textos del sistema (< 2**20: 70-72, 1213, 1214…) se quedan tal cual."""
    desc = int(desc)
    if desc >> 20 and (desc & 0xfffff) < 16:
        return _sid(desc >> 20, desc & 0xfffff)
    return desc


def _desc_card(desc):
    """description de ocgcore -> (passcode de la carta, n) si es aux.Stringid(código, n) = código << 20 | n; si es un texto del
    sistema (!system N de strings.conf) -> (0, desc). §41."""
    desc = int(desc or 0)
    if desc >> 20 and (desc & 0xfffff) < 16:
        return desc >> 20, desc & 0xfffff
    return 0, desc


OPT_YES, OPT_NO = 1213, 1214    # Duel.SelectOption(tp, 1213, 1214) (Magic Drain, Call of the Earthbound) = SÍ / NO
DLG_MAGIC_DRAIN = 11            # «¿Deseas descartar una Carta Mágica de tu mano?» (Magic Drain, la del rival)

OCG_DECK, OCG_HAND, OCG_MZONE, OCG_SZONE, OCG_GRAVE, OCG_REMOVED, OCG_EXTRA, OCG_OVERLAY = 1, 2, 4, 8, 16, 32, 64, 128
POS_FU_ATK, POS_FD_ATK, POS_FU_DEF, POS_FD_DEF = 1, 2, 4, 8
PILE_ZONE = {"hand": ZONE_HAND, "deck": ZONE_DECK, "grave": ZONE_GRAVE, "banish": ZONE_BANISH, "extra": ZONE_EXTRA}
OCG_PILE = {OCG_DECK: "deck", OCG_HAND: "hand", OCG_GRAVE: "grave", OCG_REMOVED: "banish", OCG_EXTRA: "extra"}
# fase de ocgcore -> fase del cliente (0 robo, 1 espera, 2 main1, 3 batalla, 4 main2). End Phase no tiene comando.
PHASE_MAP = {1: 0, 2: 1, 4: 2, 8: 3, 16: 3, 128: 3, 256: 4}

# --- barajar la mano igual que el cliente («mano», duelo5 del 07-10) ---------------------------------------------------
# Motor 0x4e (FUN_00713b10): semilla DAT_012cc2c8 = P1<<16 | P0 y FUN_006fa2d0 baraja las palabras de la mano con
# Fisher-Yates: para u = n..2, j = (rand()*u) >> 15 (FUN_00720890) e intercambia [j] y [u-1]. rand() = FUN_00720850,
# el LCG de MSVC (s = s*0x343fd + 0x269ec3; (s>>16) & 0x7fff). La vista 0x11 refresca la imagen de cada objeto con la
# palabra de su índice A MITAD de la animación; los 0x40 que van detrás cambian la palabra y NO el objeto. Por eso,
# con una semilla al azar, la imagen del índice i dejaba de ser la carta del índice i (se pulsaba «Beckoning Light» y
# se activaba «Mystical Space Typhoon»). Ahora se elige la semilla con la que el cliente deja la mano en el orden de
# ocgcore (los 0x40 de después ya no cambian nada). CONFIRMADO (código + traza real).
_LCG_A, _LCG_C = 0x343fd, 0x269ec3
_LCG_AINV = pow(_LCG_A, -1, 1 << 32)
HAND_SEED_TRIES = 120000   # n <= 8: ~(n-1)! intentos, sobra; más grande -> reimposición + refresco de la imagen
HAND_SEED_MAX_N = 9


def client_hand_perm(seed, n):
    """Orden que deja el 0x4e del cliente con esta semilla: mano_nueva[k] = mano_vieja[perm[k]]."""
    s, arr, u = seed & 0xffffffff, list(range(n)), n
    while u > 1:
        s = (s * _LCG_A + _LCG_C) & 0xffffffff
        j = (((s >> 16) & 0x7fff) * u) >> 15
        arr[j], arr[u - 1] = arr[u - 1], arr[j]
        u -= 1
    return arr


def hand_seed_for(old, want, rng=random, tries=HAND_SEED_TRIES):
    """Semilla de 32 bits con la que el 0x4e del cliente pasa `old` (claves de la mano actual) a `want` (claves en el
    orden de ocgcore; 0/None = cualquiera). -> (semilla, perm) o None. El primer paso se fuerza eligiendo el estado
    del LCG tras la 1.ª llamada (el LCG es biyectivo: semilla = (s1 - c) * a^-1); el resto se prueba al azar."""
    n = len(old)
    ok = lambda k, w: not w or k == w  # noqa: E731
    if n < 2:
        return rng.getrandbits(32), list(range(n))
    if n > HAND_SEED_MAX_N:
        return None
    first = [j for j in range(n) if ok(old[j], want[n - 1])]
    if not first:
        return None
    for _ in range(tries):
        j = rng.choice(first)
        r = rng.randrange(-(-j * 32768 // n), -(-(j + 1) * 32768 // n))
        s1 = rng.getrandbits(1) << 31 | r << 16 | rng.getrandbits(16)
        seed = ((s1 - _LCG_C) * _LCG_AINV) & 0xffffffff
        s, arr, u = seed, list(range(n)), n
        while u > 1:
            s = (s * _LCG_A + _LCG_C) & 0xffffffff
            jj = (((s >> 16) & 0x7fff) * u) >> 15
            arr[jj], arr[u - 1] = arr[u - 1], arr[jj]
            if not ok(old[arr[u - 1]], want[u - 1]):
                break
            u -= 1
        else:
            if ok(old[arr[0]], want[0]):
                return seed, arr
    return None

def client_monst_perm(seed, n):
    """Reparto que deja el 0x49 del cliente (FUN_00714ea0) con esta semilla: la carta de la ranura S[i] (S = ranuras de
    la máscara en orden) pasa a la S[perm[i]]. Es el mismo Fisher-Yates + LCG que la mano."""
    return client_hand_perm(seed, n)


def monst_seed_for(want, rng=random, tries=200000):
    """Semilla de 32 bits con la que el 0x49 del cliente reparte las cartas como `want` (want[i] = índice, dentro de las
    ranuras de la máscara, al que va la carta de la i-ésima). Con n <= 5 hay <= 120 repartos: se prueba al azar."""
    n = len(want)
    want = list(want)
    for _ in range(tries):
        seed = rng.getrandbits(32)
        if client_monst_perm(seed, n) == want:
            return seed
    return rng.getrandbits(32)                               # inalcanzable con n <= 5; el cliente barajaría distinto


# 07-10 (P0a): las banderas del traductor aprobadas por el owner van encendidas por defecto; cada una tiene su
# variable YO2_* (=0 la apaga al arrancar) para las pruebas que suponían el valor viejo.
_TRON = lambda k, d="1": os.environ.get(k, d) not in ("", "0")

DEFAULT_CFG = {
    "lp": 8000,
    "kind_main": 3,          # clase de 0x1a para SELECT_IDLECMD (DUDOSO; la 3 es la que se probó en vivo)
    "kind_battle": 3,        # clase para SELECT_BATTLECMD (DUDOSO)
    "kind_select": 3,        # clase para SELECT_CARD/TRIBUTE/CHAIN con máscaras (DUDOSO)
    # bit para las cartas elegibles de SELECT_CARD/TRIBUTE: 12 = sin menú, un clic la elige (lockstep CMD_PICK) (PROBABLE)
    "select_bit": CMD_GENERIC,
    # bit para elegir el objetivo de un ataque: 0 = menú «Atacar» sobre el monstruo rival (CONFIRMADO la etiqueta)
    "target_bit": CMD_ATTACK,
    "yesno": "dialog",       # "dialog" = diálogo SÍ/NO del cliente (0x1b 0x32 tipo 2) | "auto" = bot.ts decide
    "hide_npc": True,        # tapar mano/mazo/extra/boca abajo de la NPC con cartas tapadera
    "token_serial_base": None,  # serie del 1.er token de cada jugador (None = nº de cartas del mazo+extra + 1)
    "finish_mode": 0,        # P1 de 0x6202 tipo 0x4d (modo de match; DUDOSO)
    "npc_phrases": False,    # emitir 0x60/0x62/0x03 para que la NPC hable (requiere el byte NPC de 0x43a0 = 1)
    "pile_look": True,       # «Ver Detalles» (bit 1) en cementerio/desterradas de los dos y en el extra propio -> lista
    "stat_sync": True,       # sync_stats(): imponer el ATK/DEF de ocgcore con 0x36/0x37 + EFFECT_SET_STATS
    # NOTES §32: ATK/DEF también al cambiar de fase/turno (NEW_PHASE, NEW_TURN) y al cerrar un lote que no acaba en una
    # decisión de ESTA vista (la vista que espera en PvP, la NPC pensando). Sin esto, lo que ocgcore deshace sin mensaje
    # (Shrink / Rush Recklessly «hasta el final del turno», equipos o continuas que se van fuera de una cadena) seguía
    # pintado en la vista que no decidía hasta su siguiente decisión, cadena o ataque (PvP 12-10: 925 en vez de 1850
    # todo el turno del rival). YO2_STAT_SYNC_TURN=0 lo apaga (= como antes)
    "stat_sync_turn": _TRON("YO2_STAT_SYNC_TURN"),
    # NOTES §19: 0x45 «invocación terminada» al llegar SUMMONED/SPSUMMONED/FLIPSUMMONED (y justo tras 0x4f al colocar
    # un monstruo). Sin él el cliente deja el monstruo «invocándose» para siempre y pinta el ATK/DEF impreso: ni
    # equipos, ni campo, ni el par de stat_sync (el +700 de Nuzzler no se veía). Que el oficial lo mandaba: C (código);
    # los bits de P1 por tipo de invocación: P. YO2_SUMMON_DONE=0 lo apaga (= como antes)
    "summon_done": _TRON("YO2_SUMMON_DONE"),
    # NOTES §23: invocación NORMAL/por sacrificio desde la mano -> 0x4f boca arriba (card_move.wav) + 0x2c (efecto de
    # invocación normal, vista 0x34) en vez de 0x2d, que es la ESPECIAL (vista 0x35 sp_summon + reborn.wav: el
    # Guardián Celta sonaba a especial). C. YO2_SUMMON_KIND_FX=0 lo apaga (= 0x2d como antes)
    "summon_kind_fx": _TRON("YO2_SUMMON_KIND_FX"),
    # NOTES §23: ficha que deja el campo (MOVE a location 0). El cliente la borra SOLO: card_prop la marca como tipo 10
    # (FUN_007179e0), FUN_006f7960/FUN_006f7a00 no la meten en cementerio/desterradas y la vista CARD_MOVE
    # (FUN_00761970) borra su objeto (FUN_007615e0) en vez de volar a la pila. Así que va como cualquier monstruo que
    # se va: sacrificio de invocación -> 0x46 (antes 0x31, animación de destrucción); si no, 0x31. Sin aviso. C.
    # YO2_TOKEN_VANISH=0 lo apaga (= siempre 0x31 y el aviso «se manda al cementerio»)
    "token_vanish": _TRON("YO2_TOKEN_VANISH"),
    # NOTES §23: SÍ/NO con la carta. EFFECTYN -> «¿Activar el efecto de "X"?» (dlg 250/328/329) y, en EFFECTYN, YESNO
    # y el aviso de cadena, lista 0x6610 modo 0xff con la(s) carta(s) para la miniatura del diálogo. Antes: texto sin
    # la carta y miniatura de la última lista que hubiera. C (miniatura) / P (texto). YO2_YESNO_CARD=0 lo apaga
    "yesno_card": _TRON("YO2_YESNO_CARD", "1"),   # 10-10: ENCENDIDO otra vez (§29: los 2 cierres del 09-10 eran la carrera 0x6610/0x4b-0x4c, arreglada con list_dirty)
    # NOTES §25: SÍ/NO SIN carta con un texto del sistema de ocgcore (description 30 «¿seguir atacando?» tras la
    # repetición de batalla, 31 «¿atacar directamente?»…) -> su texto del cliente (YESNO_SYS_DLG: 223, 225…) en vez del
    # genérico 269 «¿activar el efecto de la carta?». P (texto). YO2_YESNO_SYS_TEXT=0 lo apaga (= 269 siempre)
    "yesno_sys_text": _TRON("YO2_YESNO_SYS_TEXT"),
    # §41 (OPT2): SÍ/NO de una carta (Ryko «¿Destruir esta carta?», Judgment of Anubis, Messenger of Peace…) con SU texto del cliente
    # (YESNO_DLG: C = lo manda así el duellog oficial, P = emparejado por el texto) y las Six Samurai con «¿Destruir otra
    # carta en lugar de "X"?». La description de ocgcore es código << 20 | n (se leía como código << 4: nunca coincidía).
    # YO2_YESNO_TEXTS=0 = como antes (genérico 269)
    "yesno_texts": _TRON("YO2_YESNO_TEXTS"),
    # §41: Yubel y Yu-Jo Friendship (SELECT_OPTION de dos efectos que el cliente 2008 preguntaba con un SÍ/NO) -> OPTION_YN.
    # P (texto 233 / 298). YO2_OPTION_YN=0 = decide bot.ts (índice 0), como antes
    "option_yn": _TRON("YO2_OPTION_YN"),
    # §41: CONFIRM_CARDS de la mano ENTERA de un jugador (Trap Dustshoot, Crush Card Virus, Royal Tribute, The Forceful Sentry…) ->
    # 0x52 [1] / 0x52 [0] como el oficial (34 usos en los duellogs 2009) en vez de un 0x51 por carta (que abre su panel de
    # detalle una y otra vez). Al elegir en esa mano (opp_hand_pick) la carta elegida lleva su 0x29 (zona 0xb) y luego el
    # 0x52 [0]. C (secuencia) / P (cómo lo pinta el cliente). YO2_HAND_OPEN=0 = los 0x51 de antes
    "hand_open": _TRON("YO2_HAND_OPEN"),
    # §41: ANNOUNCE_NUMBER con el título y la etiqueta de la carta que lo pide (Card Trooper 330/331 del oficial; Sixth Sense 296).
    # YO2_NUMBER_TEXTS=0 = los genéricos de antes (594 «Elige una cantidad» / «%d»)
    "number_texts": _TRON("YO2_NUMBER_TEXTS"),
    # NOTES §23 (LP): 0x17 BATTLE_RUN con el daño REAL (los DAMAGE que siguen al BATTLE), no el calculado con ATK/DEF.
    # Un daño anunciado y no hecho dejaba el marcador de LP del cliente con -N en espera y el siguiente igual lo
    # restaba dos veces (pvp1010 22:44:28 -> Player1 con 0 LP y 600 de verdad). C. YO2_BATTLE_DAMAGE_REAL=0 lo apaga
    "battle_damage_real": _TRON("YO2_BATTLE_DAMAGE_REAL"),
    # B67 (jugadores, Ojama Trio: «muestra el daño total, luego el de batalla y luego el del efecto de la ficha»).
    # ocgcore manda BATTLE, DAMAGE (batalla), MOVE (el destruido), DAMAGE (efecto: la ficha de Ojama hace 300 al irse
    # del campo) y DAMAGE_STEP_END: el 0x17 sumaba los dos DAMAGE y el marcador de LP del cliente enseñaba el total
    # antes de contar cada cifra. En los duellogs oficiales de 2009 el 0x17 lleva SOLO el daño de batalla (la cifra de
    # su 0x25 siguiente, 18 de 18) y el daño de efecto sale aparte. Con esto, el daño de batalla es el que llega
    # antes del primer MOVE tras el BATTLE; lo demás es de efecto.  C. YO2_BATTLE_DAMAGE_SPLIT=0 lo apaga
    "battle_damage_split": _TRON("YO2_BATTLE_DAMAGE_SPLIT"),
    # B67: P1 de 0x25 (tipo de daño). Duellogs oficiales (304 partidas, 0x6202 de 2009): 1 = daño de batalla (339 veces,
    # y siempre es el que sigue a un 0x17 con la misma cifra), 0 = daño de efecto (156), 2 = coste de LP (100); nuestro
    # traductor mandaba al revés (0 batalla / 1 efecto). El cliente solo lo usa para sus estadísticas del duelo
    # (FUN_00716440: duel_stats_empty), no cambia lo que se ve.  C. YO2_LP_TYPE_OFICIAL=0 lo apaga (= el orden de antes)
    "lp_type_oficial": _TRON("YO2_LP_TYPE_OFICIAL"),
    # Red de seguridad (regla NUESTRA, no del oficial, pedida el 07-10): al empezar cada turno, 0x26 LIFE_SET con los
    # LP del espejo para los dos (marcador y LP del cliente = ocgcore). §24: la sustituye lp_reconcile y queda APAGADA
    # por defecto (en el vivo ya estaba a False a mano). YO2_LP_RESYNC=1 la enciende
    "lp_resync": _TRON("YO2_LP_RESYNC", "0"),
    # NOTES §24 (regla NUESTRA aprobada por el owner el 07-10): reconciliación de LP «tipo pro». El traductor lleva
    # el modelo del marcador de LP que ve el cliente (DAT_012a9070+0xa9c+j*0x44: número, pendiente, espera; 0x25/0x24
    # = FUN_00716440, 0x17 = vista 0xd, 0x26 = FUN_007cef90, 0x27 = FUN_007cf050 en los dos) y lo compara con los LP
    # de ocgcore tras cada DAMAGE/RECOVER/PAY_LPCOST/LPUPDATE (ese jugador) y al cerrar cada lote o antes de cada
    # petición (los dos). Si cuadran, nada (1:1). Si no: 0x27 LIFE_RESET (quita lo pendiente y la espera de los dos
    # marcadores: 0x26 solo pone el número y un -N colgado del 0x17 se aplicaría después) + 0x26 de los dos, aviso
    # «[lp] desfase …» en el log y apunte en lp_desync / lp_desync_n del traductor (lo lee el panel). C (cliente) /
    # D (el oficial no lo hacía). YO2_LP_RECONCILE=0 lo apaga
    "lp_reconcile": _TRON("YO2_LP_RECONCILE"),
    # NOTES §31: MSG_LPUPDATE de ocgcore (LP ABSOLUTO: Duel.SetLP y reinicio de jugador) -> en ese momento 0x26 LIFE_SET
    # con el valor (>= 0) del jugador, y el modelo del marcador queda con ese valor para que lp_reconcile no lo corrija
    # otra vez. Antes solo salía el 0x26 si el valor difería del espejo y NO se miraba el marcador: con un -N en espera
    # (o un daño/cura animándose en el mismo lote) el 0x26 quedaba pisado por lo que faltaba contar y lo arreglaba
    # lp_reconcile con el aviso «[lp] desfase». Ahora, en ese caso, 0x27 + 0x26 de los dos YA en el LPUPDATE (sin aviso).
    # Es el patrón de EDOPro (LPUPDATE = fijar absoluto). YO2_LP_SET_DIRECT=0 lo apaga (= como antes de §31)
    "lp_set_direct": _TRON("YO2_LP_SET_DIRECT"),
    # NOTES §24: aviso de cadena SIN cadena abierta con UNA sola carta activable -> «¿Activar el efecto de "X"?» (dlg
    # 250; 328 en la mano, 329 en el cementerio; lista A [(id interno, 4), (dlg, 0)] como el EFFECTYN de §23) y su
    # miniatura; con varias, el genérico 269 con sus miniaturas. Con cadena abierta manda chain_link_text (dlg 7/8).
    # P (texto). YO2_CHAIN_ONE_NAME=0 lo apaga (= 269 siempre)
    # 10-10 (owner): APAGADO en vanilla (el oficial pregunta 269 + miniatura); encendido solo en el perfil enhanced
    "chain_one_name": _TRON("YO2_CHAIN_ONE_NAME", "0"),
    # NOTES §26: con el aviso de cadena 7/8 abierto («"X" está activado. ¿Encadenar?») el cliente NO actualiza el panel
    # de detalle al pasar el ratón (CUYesNoDialog es modal: FUN_00799030 no hace el picking, C). Antes del 0x32 va
    # 0x1b B=0x37 RUN_DETAIL con el uid de X (si sigue en el campo; desde la mano/cementerio ya lo puso el 0x2f):
    # el panel enseña X mientras se decide. C (cliente) / P (el oficial lo dejaba con el 0x2f/0x30 de la
    # activación). YO2_CHAIN_LINK_DETAIL=0 lo apaga
    "chain_link_detail": _TRON("YO2_CHAIN_LINK_DETAIL"),
    # texto del SÍ/NO (EFFECTYN/YESNO): lista A [(dlg, clase 0)]. Antes [(id interno, clase 4)] (clase 4 DUDOSA)
    "yesno_text": DLG_CHAIN,
    # SELECT_CHAIN opcional (ventana de cadena del jugador, p. ej. en el turno de la NPC): antes solo se mandaban las
    # máscaras, sin aviso, y el duelo esperaba a que pulsara su carta. 3 = diálogo SÍ/NO «de cadena» (DUEL_VIEW 0x32
    # tipo 3: el cliente contesta solo NO si se mantiene la tecla de saltar o, con importancia < 2, según su opción
    # «Mensaje de activación» DAT_012bb6b0+0x437; FUN_0073dd80 caso 0x32/3); 2 = SÍ/NO normal (siempre); "pass" = no
    # encadenar nunca (lo decide el servidor); 0 = como antes (solo máscaras). Con SÍ se mandan las máscaras.
    # 2026-10-06: con 3 el cliente PETÓ en vivo (access violation en FUN_007437d0 al formatear la lista A [(269, 0)]:
    # la clase 0 no es «un texto dlg»). Vuelve a 0 hasta saber qué lista lleva el diálogo de cadena oficial.
    "chain_dialog": 3,      # 06-10 18:2x: tipo 3 (respeta «Mensaje de activación») a petición del owner; antes 2. 2026-10-06: SÍ/NO normal (tipo 2) con texto (269, clase 2): el tipo 2 + clase 2 ya se vio en vivo con la rendición
    "chain_importance": 2,   # P2 del tipo 3: >= 2 se enseña salvo con la tecla de saltar; < 2 obedece a la opción  C/D
    # Con chain_dialog=3: P2 por ventana (regla PROPIA, apagada): 2 si responde al rival, 1 si no. El cliente
    # (FUN_0073dd80 tipo 3) con P2 < 2 contesta solo 0x6423 0 salvo opción «Manual» (DAT_012bb6b0+0x437 = 2)  C/D
    "chain_importance_auto": True,   # 06-10: regla NUESTRA (P2=2 si lo abrió el rival), aprobada por el owner para probar
    # SELECT_CHAIN forced con UNA sola opción (molienda de Lumina/Jain en la End Phase…): el servidor contesta el
    # índice 0 sin preguntar. Con varias (p. ej. [Lumina, MST]) se sigue preguntando: ocgcore no marca qué entrada
    # es obligatoria (MSG_SELECT_CHAIN solo trae code/loc/description/client_mode) y elegir por el jugador el orden
    # o activar MST sin querer no sería 1:1. Lo que hacía el servidor oficial: desconocido (D)
    "forced_auto_single": True,   # 06-10: forzada con 1 sola opción -> se contesta sola (aprobado por el owner)
    # Ola 6. Texto del «¿encadenar?» con el NOMBRE de la carta: lista A [(id interno, 4), (0xffff, 3), (dlg, 3)] ->
    # «Call of the Haunted\n¿activar el efecto de la carta?». FUN_00744670 (desensamblado): clase 4 = strcpy(nombre)
    # (FUN_0073bc80, como el SÍ/NO que ya se ve en vivo), clase 3 = strcat(texto dlg P) o «\n» con P = 0xffff
    # (0xc85cc8). Ninguna lee la pareja SIGUIENTE (la clase 0 sí: era el access violation de FUN_007437d0).  C
    "chain_card_name": False,
    # PRIORIDAD del aviso de cadena (P2 de DUEL_VIEW 0x32 tipo 3; solo con chain_dialog=3). Si está puesto, manda
    # sobre chain_importance_auto. El cliente cancela solo (0x6423 0) los P2 < 2 en «Cancelación automática» y
    # «semi-automática» (las dos se tratan IGUAL en FUN_0073dd80), los P2 < 3 con el botón derecho pulsado, y enseña
    # todo en «Manual»  C. Qué P2 mandaba el servidor oficial a cada aviso: desconocido (no está en el cliente ni en
    # sus datos)  D. Reglas NUESTRAS (NOTES «Prioridad de avisos»):
    #   None     = apagado (como antes: chain_importance_auto / chain_importance);
    #   "evento" = reglamento: P2=2 si (a) el último eslabón de la cadena abierta es del RIVAL, (b) alguna opción es
    #              un efecto con evento (SetCode ≠ EVENT_FREE_CHAIN en su script: Mirror Force, Torrential, Magic
    #              Jammer…; ocgcore solo lo ofrece si el suceso acaba de pasar) o (c) el rival acaba de declarar un
    #              ataque y una opción libre lleva TIMING_ATTACK en su SetHintTiming (Necro Gardna, Raigeki Break);
    #              si no, 1 (activación libre: Coro de Aqua, Tifón, Call of the Haunted…);
    #   "spe"    = la prioridad que calcula ocgcore (spe_count > 0: efectos con evento + libres cuyo SetHintTiming
    #              casa con el momento, lo que usa EDOPro para su «auto-cadena»), salvo eslabón propio -> 1. Más
    #              avisos que "evento" (p. ej. Tifón en cada invocación del rival, por TIMINGS_CHECK_MONSTER_E)
    #   "oficial" = NOTES §28 (08-10): la P2 que mandaba Konami, sacada de 18.933 avisos de 100 duellogs de 2009 (C): una
    #              por SUCESO (CHAIN_EVENTS / CHAIN_SUMMONS: 2 = ataque propio y del rival, Damage Step, antes del cálculo
    #              del daño, fin del turno del rival, tu invocación con éxito...; 1 = fases, robos, colocar, destruir...;
    #              sin suceso conocido, 1). Con la cadena abierta: 2 si el último eslabón es del rival, 1 si es propio
    #              (también C: 627 contra 140) y siguen las reglas (e) de la mano. Es el modo por defecto
    "chain_priority": "oficial",   # 08-10 (§28): 1:1 con el servidor oficial; "evento" era regla NUESTRA (06-10, OK del owner)
    # NOTES §28: el aviso de cadena SIN cadena abierta lleva el TEXTO DEL SUCESO + la pregunta, como el oficial: lista A
    # [(suceso, 2), (0xffff, 3), (269, 3)] (p. ej. «Ésta es la Standby Phase.» y debajo la
    # pregunta 269). El suceso
    # sale de los TIMING_* del SELECT_CHAIN (CHAIN_EVENTS); sin suceso conocido, el 269 solo. Con UNA carta candidata y
    # suceso gana la forma oficial (suceso + 269, miniatura de la carta): suceso + «¿Activar el efecto de "X"?» no cabe
    # (clase 0 con texto ya empezado usa ese texto como plantilla, no el dlg; y son 5 parejas, el 0x61 admite 4). Con la
    # cadena abierta el 7/8 lleva la 3.ª pareja (id interno, 0) del oficial (se salta; inocua). C (desensamblado + 100
    # duellogs oficiales). YO2_CHAIN_EVENT_TEXT=0 lo apaga (= como antes de §28)
    "chain_event_text": os.environ.get("YO2_CHAIN_EVENT_TEXT", "1") not in ("", "0"),   # 10-10: ENCENDIDO otra vez (§29: el cierre no era del texto); YO2_CHAIN_EVENT_TEXT=0 lo apaga
    # NOTES §30: ventana de cadena SIN nada activable (SELECT_CHAIN no obligatorio con `selects` vacío) -> aviso oficial
    # «No hay carta que pueda activarse durante este momento oportuno / mediante una Cadena.» + «¿Revisarás el Campo?»
    # (CHAIN_NONE, P2 0; solo con chain_dialog=3). El cliente lo contesta solo (NO) en «automática»/«semi» y lo enseña
    # en «Manual»; SÍ y NO = no encadenar. Formato y P2: C (14.063 avisos oficiales iguales); que Konami lo mandase en
    # CADA ventana a cada jugador: P (11,8 avisos por turno y lado en el oficial, 11,4 ventanas por turno en ocgcore).
    # APAGADO por defecto: necesita que duelcore entregue las ventanas vacías (ocg_duel pasa auto_chain=False con la
    # clave encendida, solo en duelos nuevos), espera la respuesta del cliente en ~11 ventanas por turno (cambia el
    # ritmo) y lo que hacía el oficial con el SÍ es D. YO2_CHAIN_NONE_PROMPT=1 lo enciende
    "chain_none_prompt": os.environ.get("YO2_CHAIN_NONE_PROMPT", "0") not in ("", "0"),
    # 07-10 (NOTES §21): monstruos con efecto DESDE LA MANO en "evento"/"spe". Sin esto, dos casos salían con P2 = 1
    # y el cliente (opción automática/semi) contestaba NO solo, sin enseñar nada:
    #   (d) efecto rápido libre de un monstruo de la mano con momento de BATALLA en su SetHintTiming (ATTACK,
    #       DAMAGE_STEP, DAMAGE_CAL, BATTLE_PHASE) durante un ataque -> 2. Honest (solo en el paso de daño, antes del
    #       cálculo), Cyber Ogre, Destiny Hero - Blade Master. Su condición en el script ya limita la ventana;
    #   (e) disparado opcional (TRIGGER_O) de un monstruo de la mano con la cadena abierta y el último eslabón PROPIO
    #       -> 2 (antes la regla (a) daba 1). Gorz recibe daño, el jugador encadena D.D. Crow y Gorz se volvía a
    #       ofrecer con 1: se perdía.
    # Kuriboh / Arcana Force XIV (EVENT_PRE_DAMAGE_CALCULATE), Herald of Green Light / Gravekeeper's Watcher
    # (EVENT_CHAINING) y la 1.ª ventana de Gorz (EVENT_DAMAGE) ya daban 2 por (b). Regla NUESTRA (D).
    # YO2_CHAIN_PRIORITY_HAND=0 lo apaga (= como antes)
    "chain_priority_hand": _TRON("YO2_CHAIN_PRIORITY_HAND"),
    # Texto del aviso cuando hay cadena abierta: DLG 7 «El efecto de "%s" está activado. ¿Resolverlo como parte de
    # una Cadena?» (monstruo) / DLG 8 «%s está activado…» (Mágica/Trampa), %s = carta del ÚLTIMO eslabón. Lista A
    # [(id interno, 4), (7|8, 0)]: la clase 4 deja el nombre en el búfer y la clase 0 usa el dlg como plantilla con
    # %s = ese búfer (desensamblado 0x744670 -> 0x7437d0). La clase 0 SOLA petaba porque su 2.º argumento salía de
    # la pareja siguiente, inexistente (basura de pila). P por código, SIN probar en vivo
    "chain_link_text": True,   # 06-10: activado con el OK del owner (probar en vivo)
    # SÍ con UNA sola carta encadenable -> se activa esa (antes: había que pulsarla después y en vivo parecía colgado,
    # con el «Pensando…» azul = tiene permiso, FUN_007e0880). Con varias, máscaras como antes.
    "chain_yes_direct": False,  # 2026-10-06 owner: 1:1 con el original (tras el SÍ se pulsa la carta), nada de atajos
    # aviso «Selecciona una carta objetivo en el campo» (dlg 274; sacrificio: 18) en un panel CULockOnDialog antes
    # de las máscaras de SELECT_CARD/TRIBUTE en campo o mano. Formato comprobado (clase 2, no la 0) y no es modal; lo
    # que NO está comprobado es cuándo se cierra el panel (puede quedarse puesto): probar en vivo antes de dejarlo  D
    "pick_panel": False,
    "activate_flash": False, # destello 0x30 al activar una carta del campo (trampas/magias de la NPC no se veían)
    # NOTES §35 (bug #2 «Effect activation visuals & sfx not displayed»): CADA activación (CHAINING de ocgcore) manda, como
    # el oficial, 0x2e (revelar la carta: vista 0x13 si viene de la mano + 0x43 + 0x21 CARD_HAPPEN = destello, sonido y
    # panel) justo antes del 0x12, y el 0x12 lleva el UID en P1 (antes: índice en la mano / 0). Las 1356 activaciones de
    # los 100 duellogs oficiales de 2009 son así. Sustituye al 0x2f (hand/cementerio) y a activate_flash/0x30 (campo).
    # C (oficial: 1356/1356) / C (vistas) / D (los bits altos de P0, que el cliente no lee). YO2_CHAIN_REVEAL=0 lo apaga
    "chain_reveal": _TRON("YO2_CHAIN_REVEAL"),
    # objetivo de ataque tras «Atacar»: 0x1b B=0xc (atacante, P2 = -1) + 0x1b B=0xb (estado 3: la espada sale del
    # atacante y sigue al ratón). Clic derecho (0x6351) cancelable -> 0x1b B=0xa (estado 1). C (código), sin probar vivo
    "attack_aim": True,
    "aim_target_bit": CMD_GENERIC,  # (= CMD_PICK) con la espada: bit de las cartas objetivo (11 = clic directo; 0 = menú «Atacar»)
    # «Ver Detalles» en una pila -> lista 0x6610 con este modo (1..5 = solo ver; el 4 tapa las desterradas boca abajo
    # del rival, FUN_007202a0). Qué número es cada pila está sin probar (D)
    "look_modes": {ZONE_GRAVE: 1, ZONE_BANISH: 4, ZONE_EXTRA: 1},
    # SELECT_CARD con alguna candidata en mazo/cementerio/desterradas/extra -> lista 0x6610 + 0x1b B=0x33 (modo 6 =
    # elegir 1, 6+n = exactamente n, 0x27+max = de 1 a max); respuestas 0x6426/0x6432 + 0x6423. Antes: máscaras en la
    # pila, el cliente manda siempre índice 0 y el duelo se paraba (Reinforcement of the Army). Audit duelo_menus §7  C
    "list_picks": True,
    # Opción del cliente «Auto Card Placing» (Opciones; texto 6523, OFF = «You decide which position the card will go»,
    # 6533). El cliente manda su valor en el u8 de 0x61fe (FUN_00692060, que lleva DAT_012bb6b0+0x436 = NO(casilla
    # 0x191); por defecto la casilla está ON -> 0 = automática, 1 = manual; en los logs vivos salen los dos). Con la
    # colocación MANUAL el SELECT_PLACE de ocgcore (invocar/colocar un monstruo, colocar una M/T) se le pregunta al
    # jugador: 0x800 en las zonas vacías permitidas (un clic en una zona vacía manda 0x6350 (j, zona, i, 0xb), duelo_menus
    # §1 / H13). Sin esta bandera (o con la automática) el servidor elige la primera zona libre, como hasta ahora. C
    # (el cliente: mapeo del u8 y clic en la zona) / D (que el servidor oficial preguntase así). YO2_COLOCACION_MANUAL=0
    # la apaga (= siempre automática)
    "place_manual": _TRON("YO2_COLOCACION_MANUAL"),
    # Vínculo carta -> carta (NOTES «Vínculo carta-objetivo / equipo»). EQUIP de ocgcore -> motor 0x42 (animación
    # CEquipEffect de la carta de equipo al monstruo + entrada de tipo link_kind_equip en la lista del monstruo); re-
    # equipar a otro monstruo / UNEQUIP -> 0x37 (borra el vínculo). CARD_TARGET (Círculo Atahechizos, Call of the
    # Haunted…) -> 0x42 con link_kind_target (o 0x36 sin animación si link_target_anim=False); CANCEL_TARGET -> 0x37.
    # El cliente original NO dibuja nada fijo (ni línea ni «+»): la relación se ve con la animación al crearse y en el
    # ATK/DEF que él mismo calcula. Con esto activo, los monstruos con vínculo llevan SIEMPRE el par EFFECT_SET_STATS
    # (si no, el cliente sumaría su propio bono de equipo al ATK impreso).  C (comando) / P (tipo de equipo) / D (tipo
    # de CARD_TARGET y que el oficial mandase 0x42 para él). Sin probar en vivo
    "card_link": True,   # 06-10: activado con el OK del owner (tipo del Círculo = 9, D)
    "link_kind_equip": 10,     # 10/11 = equipo para el cliente (los dos iguales en FUN_006fae40); 10 = P
    "link_kind_target": 9,     # 6..9 = vínculo que no es equipo; el 9 no lo usa el motor del cliente (6/7/8 sí)  D
    "link_target_anim": True,  # CARD_TARGET con 0x42 (animación) o con 0x36 tipo link_kind_target (silencioso)  D
    # BECOME_TARGET (objetivos al activar) -> 0x29 CARD_LOCKON sobre cada carta del campo o de la mano  C/P
    "target_lockon": True,   # 06-10: activado con el OK del owner
    # NOTES §35 (bug #8 «cartas que señalan el Cementerio sin indicar cuál»): BECOME_TARGET sobre una carta del
    # CEMENTERIO / DESTERRADAS -> 0x29 con zona 0xe / 0xf, como el oficial (66 en los duellogs). El cliente sube esa carta a
    # la cima de su pila y abre la vista 0x17 (cementerio) antes del 0x18 CARD_LOCKON. C. YO2_LOCKON_PILE=0 lo apaga
    # (= solo campo y mano, como hasta ahora)
    "lockon_pile": _TRON("YO2_LOCKON_PILE"),
    # --- «Motor: lo que faltaba» (NOTES §14) -------------------------------------------------------------------------
    # ADD_COUNTER / REMOVE_COUNTER de una carta del campo -> 0x3c (contador visible + el cliente recalcula su ATK:
    # Breaker +300 por contador de magia, FUN_006fae40 caso 0x1617). Antes se ignoraban.  C
    "counters": True,
    # SWAP (Intercambio de Criaturas) -> 0x3e. Antes se ignoraba y el cliente se desincronizaba.  C
    "swap_cmd": True,
    # SHUFFLE_SET_CARD (Magical Hats) -> 0x49 MONST_SHUFFLE como el oficial (1 caso en los 360 duellogs oficiales: B = 0x49,
    # P0/P1 = semilla, P2 = máscara de ranuras). La semilla se ELIGE para que el RNG del cliente deje las cartas donde
    # las puso ocgcore (NOTES MIS-1).  C (comando) / P (que el oficial lo usara para todos los barajes de colocadas)
    # YO2_MONST_SHUFFLE=0 lo apaga (= ignorar el mensaje, como antes: el espejo y el cliente se desincronizan).
    "monst_shuffle": _TRON("YO2_MONST_SHUFFLE"),
    # MOVE campo -> campo (cambio de control, mover de zona) -> 0x3d (conserva efectos/contadores/vínculos de la
    # ranura, como el oficial) en vez de 0x41 por uid. False = 0x41 como antes.  C (manejador) / P (que el oficial lo
    # usara para el cambio de control: marca 0x400 solo si cambia el jugador)
    "field_move_3d": True,
    # TOSS_COIN -> 0x5c (animación de moneda, antes nada); y moneda/dado salen sobre la carta del eslabón que se
    # resuelve (antes el dado salía siempre en la ranura 0).  C (comando) / D (cara y «cantada»)
    "toss_anim": True,
    "coin_call": 1,          # bit14 de 0x5c: el resultado que se resalta al caer (1 = la cara «1» de ocgcore)  D
    # --- «duelo2» (NOTES §15) ----------------------------------------------------------------------------------------
    # Sacrificio de una Invocación por Sacrificio (o Colocación) -> 0x46 «生贄召喚開始» en vez de 0x31 (que es la
    # animación de DESTRUCCIÓN). Se reconoce por el HINT 500 de ocgcore + que, en el mismo lote, detrás de los MOVE al
    # cementerio viene la invocación (MOVE mano -> campo o el HINT de zona con el código de una carta de la mano). Un
    # sacrificio como COSTE (sigue CHAINING) se queda en 0x31.  C (comando) / P (reconocimiento)
    "tribute_anim": True,
    "tribute_mark": False,   # 0x4a (marca) antes de cada 0x46. El cliente acepta las dos formas; cuál usaba el oficial: D
    # Fusión: por material [0x4b marca, 0x4c arranque] justo antes de que el material salga de su sitio (tras el
    # HINT 511 «materiales de fusión»), y 0x4d RUN_FUSION justo antes del 0x2d del monstruo de fusión. Formatos y que
    # 0x4b/0x4c vayan antes que 0x4d: C. Que 0x4d vaya entre los materiales y el 0x2d (y no después): P/D -> APAGADO
    # hasta verlo en vivo
    "fusion_anim": _TRON("YO2_FUSION_ANIM"),   # 07-10: activado por defecto con el OK del owner (=0 lo apaga)
    # B67 (jugadores: «la Invocación por Fusión deja un efecto visual residual»). Orden del oficial, sacado de los duellogs
    # de 2009 (28 fusiones con materiales, todas iguales): 0x4b de TODOS los materiales primero (P1 = 0, 1…), luego por material 0x4c y
    # su movimiento SIN destrucción (mano -> cementerio 0x3f, campo -> cementerio 0x40, desterrados 0x33), después 0x4d y
    # el 0x2d. El traductor mandaba 0x4b, 0x4c y 0x31 (CARD_BREAK = la animación de DESTRUCCIÓN) por material, uno tras otro,
    # encima de la marca de material que ya vuela. C (orden, formato del 0x4b/0x4c/0x4d) / D (qué parte se veía como
    # residuo: no se pudo probar con el cliente). YO2_FUSION_OFICIAL=0 vuelve al orden anterior
    "fusion_official": _TRON("YO2_FUSION_OFICIAL"),
    # Barra TIME (mensaje de red 0x6461 DUEL_TIMEUP_WARNING, FUN_00692bf0: u8 lado, s32 segundos, u8 pausado, s32 -1).
    # El cliente la pinta con máximo FIJO 0xb4 = 180 s (FUN_00799d80) y descuenta solo con time(); nada más escribe ese
    # reloj y al llegar a 0 NO manda nada (C). Solo se ve con la opción de tiempo de la sala (DAT_012bb6b0+0xc2) o en
    # salas de tipo 8 (C). Cuándo y con qué valores lo mandaba el oficial: D. Con N > 0 (regla NUESTRA): al empezar el
    # turno del jugador 0 su reloj vuelve a N y cada petición (0x1a) lleva lo que le queda; en el turno del rival el
    # reloj del rival se enseña PAUSADO a N y las ventanas de cadena del jugador 0 llevan N. Visual: nadie pierde por
    # tiempo. 0 = apagado (como antes)
    "turn_timer": int(os.environ.get("YO2_TURN_TIMER", "180") or 0),   # 07-10: 180 por defecto con el OK del owner (=0 lo apaga)
    # Límite de tiempo REAL en PvP (research/traductor/TIEMPO.md «Implementado», 07-10, decisión del owner). Lo cuenta
    # el SERVIDOR (ocg_duel.PvpSession + pvp_match.PvpMatch), no este traductor: con el límite activo la PvpSession
    # crea sus vistas con turn_timer = 0 y manda ella los 0x6461 con lo que queda de verdad. Contra la NPC no hay
    # límite (C: la barra no existe en los modos 0/1). Se leen al EMPEZAR cada PvP: cambiarlos en caliente
    # (OCG.TR.DEFAULT_CFG[...] = ...) solo afecta a los duelos nuevos.
    # - turn_limit: encendido = quien se queda sin tiempo PIERDE (0x649b código 3 + fin como la rendición). Apagado =
    #   barra solo visual como antes. La sala con «Límite de Tiempo OFF» (0x4288, +0xa6) también lo apaga.
    # - turn_timer (arriba) = presupuesto de TU turno para todas tus decisiones (180, C: texto 7006). 0 = sin límite.
    # - tiempo_gunslinger: el mismo presupuesto en salas GUNSLINGER (b2 7/15): 60 (C: texto 7006). 0 = como las demás.
    # - tiempo_respuesta: presupuesto para contestar en el turno del RIVAL (cadena, Sí/No, objetivos…): se repone al
    #   empezar cada turno del rival y se gasta entre todas tus respuestas de ese turno. Valor INVENTADO (I, estilo
    #   Master Duel): el cliente solo dice que hay un reloj activo a la vez.
    # - tiempo_margen: segundos de gracia sobre el reloj antes de dar la derrota (red, última animación). I.
    "turn_limit": _TRON("YO2_LIMITE_TIEMPO"),
    "tiempo_gunslinger": float(os.environ.get("YO2_TIEMPO_GUNSLINGER", "60") or 0),
    "tiempo_respuesta": float(os.environ.get("YO2_TIEMPO_RESPUESTA", "180") or 0),   # 10-10: 30 -> 180 (owner; el cliente solo tiene UN reloj: el que contesta gasta su barra de 180, P)
    "tiempo_margen": float(os.environ.get("YO2_TIEMPO_MARGEN", "3") or 0),
    # --- triage 07-10 (research/audit/BACKLOG_ESTADO_0710.md) -------------------------------------------------------
    # B45: tope de RETRY (respuesta ilegal). N = a partir del RETRY N-ésimo SEGUIDO de una decisión, decide bot.ts; y
    # una respuesta automática del servidor (zona/posición) que ya falló no se repite. Robustez del servidor: el
    # cliente no ve nada distinto salvo que el duelo ya no se queda girando. 0 = sin tope (como antes)
    "retry_auto": 2,
    # B23: magia de campo -> motor 0x14 FIELD_CHANGE (FUN_00713240: P0 = id INTERNO -> DAT_012ca4c0 y DUEL_VIEW 7,
    # FUN_00704100 lo convierte en el fondo/BGM; P0 = 0 = sin campo). Es el ÚNICO escritor de DAT_012ca4c0, así que
    # el oficial tenía que mandarlo (C por código); cuándo exactamente (al quedar boca arriba / al resolverse) y si el
    # cliente suma además su bono de campo al ATK que impone sync_stats: P/D -> APAGADO hasta verlo en vivo (T11)
    "field_change": _TRON("YO2_FIELD_CHANGE"),   # 07-10: activado por defecto con el OK del owner (=0 lo apaga)
    # --- B42 (NOTES §18): el jugador decide con las ventanas del propio cliente en vez de bot.ts ------------------------
    # SELECT_POSITION (ATK / DEF de una Invocación Especial) -> CUSelectStandDialog (0x32 tipo 8): ventana y respuesta C;
    # la imagen sale del uid de una carta con ese código (P, solo estética). Sin uid (fichas) o con FD_ATK -> como antes
    "position_dialog": True,
    # ANNOUNCE_RACE / ANNOUNCE_ATTRIB -> 0x32 tipo 6 / 7; ANNOUNCE_NUMBER -> 0x32 tipo 0xb (número entre mín y máx; la
    # etiqueta «Nivel %d» / «%d000LP» / «%d» se elige por los valores: P, solo estética); ANNOUNCE_CARD -> lista modo
    # 0x4b (todas las cartas; 0x6423 = id interno). Ventanas y respuestas: C
    "announce_dialogs": True,
    # SELECT_OPTION -> CUSelectDialog (0x6610 con un ítem = texto dlg por opción + 0x32 tipo 4; 0x6423 = índice). La
    # ventana es C; el TEXTO de cada opción sale de OPTION_DLG (emparejado a mano, P). Si alguna opción no tiene texto
    # o hay más de 4, decide bot.ts como antes. Las de SÍ/NO (1213/1214) -> CUYesNoDialog. APAGADO hasta verlo en vivo
    "option_dialog": _TRON("YO2_OPTION_DIALOG"),   # 07-10: activado por defecto con el OK del owner (=0 lo apaga)
    # SORT_CARD / SORT_CHAIN -> lista «elegir exactamente n» (modo 6+n): el orden en que se marcan = orden de ocgcore
    # (order[i] = puesto de la carta i, 0 = la primera marcada = arriba). Lista C; que ocgcore lea así `order`: P
    "sort_list": _TRON("YO2_SORT_LIST"),   # 07-10: activado por defecto con el OK del owner (=0 lo apaga)
    # SELECT_COUNTER -> un diálogo de número por carta («%d contadores.», mín/máx según lo que queda); si el reparto es
    # obligado se contesta solo. Regla NUESTRA (P): el oficial pudo hacerlo de otra forma (dlg 317/344)
    "counter_dialog": _TRON("YO2_COUNTER_DIALOG"),   # 07-10: activado por defecto con el OK del owner (=0 lo apaga)
    # NOTES §20: descarte por límite de mano en la End Phase (HINT 501 + SELECT_CARD min = max = mano - 6 sobre toda
    # la mano, sin cadena) -> panel CUDiscardDialog del cliente (0x6202 tipo 0x1e P0 = 1 P1 = cuántas faltan, lista A
    # [(235, 2)] «Descarta de tu mano.», 0x1b B=0x32 P0=0xd) + máscaras de la mano como siempre; tras cada carta
    # elegida se baja la cuenta y al acabar se pone a 0 (el panel se cierra). Antes: solo las máscaras, sin texto.
    # Panel y cuenta: C (código); que el oficial lo mandara aquí: P. YO2_HAND_LIMIT_DIALOG=0 lo apaga (= como antes)
    "hand_limit_dialog": _TRON("YO2_HAND_LIMIT_DIALOG"),
    # NOTES §22: MSG_CARD_HINT CHINT_TURN (SetTurnCounter: Espadas de la Luz Reveladora, Nightmare's Steelcage,
    # Viser Des…) -> 0x3b sobre la ranura: el reloj de arena con los turnos que van (1, 2…) y la animación al subir.
    # Antes se ignoraba: la carta no enseñaba nada. Solo las cartas de TURN_CARDS. Comando y dibujo: C; que el oficial
    # lo mandara así: P. YO2_TURN_COUNTER=0 lo apaga (= como antes)
    "turn_counter": _TRON("YO2_TURN_COUNTER"),
    # NOTES §22: CHINT_RACE / CHINT_ATTRIBUTE / CHINT_CARD (lo declarado por DNA Surgery, Prohibition…) -> 0x3a con el
    # valor en +0xc: el cliente pinta el icono de la raza/atributo o la carta. Solo las cartas DECL_*. C (dibujo) / P.
    # YO2_DECLARE_HINT=0 lo apaga
    "declare_hint": _TRON("YO2_DECLARE_HINT"),
    # §33: SELECT_SUM (Invocación por Ritual) con las ventanas del oficial (CURitualDialog + marcas 0x29 + 0x1f; del
    # mazo, lista de 1 en 1) y 0x46 en cada sacrificio. Antes decidía bot.ts (y con ocgcore-wasm 0.1.2 sin parche el
    # duelo se colgaba). Secuencia: C (duellogs oficiales). YO2_RITUAL_SUM=0 lo apaga (= decide bot.ts)
    "ritual_sum": _TRON("YO2_RITUAL_SUM"),
    # §34: CHINT_TURN de una carta con tope NEGATIVO en el cliente (FUN_0071bfa0: Final Countdown, Crush Card Virus,
    # Gold Sarcophagus, Lightforce Sword…) -> 0x3b con P0 = id interno, P1 = 1, P2 = cuenta, como el oficial (duellogs
    # 2009: CCV 1-2-3 y Gold Sarcophagus 1-2 al jugador del turno que se cuenta). El 080912 no pinta nada con P2 != 0
    # (vista 0x24 en la «ranura» 0x10, que FUN_00760f40 no tiene), así que solo es fidelidad de red. YO2_TURN_COUNTER_
    # PLAYER=0 lo apaga
    "turn_counter_player": _TRON("YO2_TURN_COUNTER_PLAYER"),
    # NOTES §40: SELECT_CARD cuyas candidatas están en la MANO DEL RIVAL (Trap Dustshoot, The Forceful Sentry, Confiscation,
    # Lightforce Sword…) -> el jugador elige él (antes decidía bot.ts al azar). Secuencia del oficial (duellogs 2009,
    # Dustshoot): lista A [(244, 2)] + 0x1b B=0x32 (0xff, 244, 1), identidad de la mano (CONFIRM_CARDS, ya hecho) y
    # petición de clase 8 con las máscaras sobre la mano del rival. C (secuencia) / P (que el cliente deje pulsar ahí).
    # YO2_OPP_HAND_PICK=0 lo apaga (= decide bot.ts, como antes)
    "opp_hand_pick": _TRON("YO2_OPP_HAND_PICK"),
}


# --- scripts Lua de ocgcore (para chain_priority="evento") ------------------------------------------------------------
# Solo se leen: SetType / SetCode / SetHintTiming / SetDescription de cada efecto (Effect.CreateEffect o :Clone()).
from yo2srv import rutas as _rutas  # noqa: E402
SCRIPTS_DIR = os.path.join(_rutas.vendor(), "CardScripts")   # YO2_VENDOR (paquete) o <root>/_vendor, como antes
EVENT_FREE_CHAIN = 1002
TIMING_ATTACK = 0x1000
TIMING_DAMAGE_STEP, TIMING_DAMAGE_CAL, TIMING_BATTLE_PHASE = 0x2000, 0x4000, 0x1000000      # constant.lua
TIMINGS_BATTLE = TIMING_ATTACK | TIMING_DAMAGE_STEP | TIMING_DAMAGE_CAL | TIMING_BATTLE_PHASE   # §21 (d)
# NOTES §28 (chain_event_text / chain_priority="oficial"): el suceso al que responde la ventana de cadena y la P2 que
# le daba el servidor de Konami (duellogs oficiales de 2009: lista A [(suceso, 2), (0xffff, 3), (269, 3)]). Sale de los
# TIMING_* de ocgcore que trae el SELECT_CHAIN (hint_timing = causados por quien elige, hint_timing_other = por el
# rival). Filas (bit TIMING_*, dlg «mío», dlg «del rival», P2 «mío», P2 «del rival»), por orden de prioridad; dlg 0 =
# sin texto propio (-> 269 solo). P2 = la mayoritaria del suceso en los 18.933 avisos de dubina (NOTES §28.1)
CHAIN_EVENTS = (
    (0x1000, 182, 183, 2, 2),        # ATTACK: «Has declarado la batalla» / «Tu oponente ha declarado la batalla»
    (0x4000, 266, 266, 2, 2),        # DAMAGE_CAL: «Estamos antes del cálculo del daño»
    (0x2000, 177, 177, 2, 2),        # DAMAGE_STEP: «Se entró en el Damage Step»
    (0x200, 175, 176, 1, 1),         # MSET: «Has Colocado un monstruo» / «Tu oponente Colocó un monstruo»
    (0x400, 159, 160, 1, 1),         # SSET: «Tú Colocas una carta Mágica o de Trampa» / «Tu oponente Coloca…»
    (0x800, 178, 178, 1, 1),         # POS_CHANGE: «La posición del monstruo ha sido cambiada»
    (0x10000, 197, 198, 1, 1),       # DRAW: «Has robado una(s) carta(s)» / «Tu oponente robó…»
    (0x80000, 267, 267, 1, 1),       # DESTROY: «La(s) carta(s) en el Campo ha/han sido destruida(s)»
    (0x2000000, 196, 196, 1, 1),     # EQUIP: «Un monstruo ha sido provisto con una Carta de Equipo»
    (0x20, 67, 251, 1, 2),           # END_PHASE: «Ésta es la End Phase» (turno propio) / «El turno de tu oponente está a punto de finalizar»
    (0x10, 0, 272, 1, 1),            # BATTLE_END: «La Battle Phase de tu oponente terminará pronto» (la propia: sin texto)
    (0x4, 64, 64, 1, 1),             # MAIN_END: «Esto es antes del final de la Main Phase»
    (0x2, 63, 63, 1, 1),             # STANDBY_PHASE: «Ésta es la Standby Phase»
    (0x1, 62, 62, 1, 1),             # DRAW_PHASE: «Ésta es la Draw Phase»
)
# invocaciones, por el último mensaje de ocgcore: *SUMMONING = ventana al ANUNCIARLA (Juicio Solemne…), *SUMMONED = al
# CERRARLA («con éxito»). Mismas columnas que CHAIN_EVENTS; quién invoca = el controlador de ese mensaje
CHAIN_SUMMONS = {
    "SUMMONING": (0x40, 169, 170, 1, 2), "SUMMONED": (0x40, 252, 253, 2, 1),
    "SPSUMMONING": (0x80, 173, 174, 1, 2), "SPSUMMONED": (0x80, 256, 257, 2, 1),
    "FLIPSUMMONING": (0x100, 171, 172, 1, 2), "FLIPSUMMONED": (0x100, 254, 255, 1, 1),
}
CHAIN_DAMAGE = (189, 190, 1)         # DAMAGE: «Has sufrido daños» (me dañaron) / «Has dañado a tu oponente»; P2 1
CHAIN_SEP = 0xffff                   # clase 3 con 0xffff = «\n» entre el suceso y la pregunta (FUN_00744670 caso 3)
# NOTES §30 (chain_none_prompt): aviso oficial «No hay carta que pueda activarse…» + «¿Revisarás el Campo?» en las
# ventanas de cadena SIN nada activable. Lista A [(204 | 205, 2), (0xffff, 3), (206, 3)] + 0x32 tipo 3 con P2 0: los
# 14.063 avisos 204/205 de los 100 duellogs de dubina llevan EXACTAMENTE eso (C). 204 = sin cadena abierta, 205 = con
# eslabones (1.740 de 1.778 van detrás de un 0x12 de cadena, C). El suceso no va en el texto (C)
CHAIN_NONE = (204, 205, 206)         # «…durante este momento oportuno.» / «…mediante una Cadena.» / «¿Revisarás el Campo?»
CHAIN_NONE_P2 = 0                    # P2 del oficial (C): el cliente lo contesta solo (NO) salvo en «Manual»
LOCATION_HAND = 2
_ET_ACTIVATE, _ET_TRIGGER_O, _ET_QUICK_O, _ET_TRIGGER_F, _ET_QUICK_F = 0x10, 0x80, 0x100, 0x200, 0x400
_ET_CHAIN = _ET_ACTIVATE | _ET_TRIGGER_O | _ET_QUICK_O | _ET_TRIGGER_F | _ET_QUICK_F   # lo que entra en SELECT_CHAIN
_LUA = {"index": None, "consts": None, "info": {}}


def _lua_consts():
    if _LUA["consts"] is None:
        consts = {}
        try:
            txt = open(os.path.join(SCRIPTS_DIR, "constant.lua"), encoding="utf-8", errors="replace").read()
        except OSError:
            txt = ""
        for name, val in re.findall(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(0x[0-9a-fA-F]+|\d+)\b", txt, re.M):
            consts[name] = int(val, 0)
        _LUA["consts"] = consts
    return _LUA["consts"]


def _lua_eval(expr):
    """'TIMING_ATTACK|TIMINGS_CHECK_MONSTER_E' / 'EVENT_FREE_CHAIN' / '0x10+0x100' -> int, o None si no se sabe."""
    consts, total = _lua_consts(), 0
    for tok in re.split(r"[|+]", expr.replace(" ", "")):
        if re.fullmatch(r"0x[0-9a-fA-F]+|\d+", tok):
            total |= int(tok, 0)
        elif tok in consts:
            total |= consts[tok]
        else:
            return None
    return total


def script_info(code):
    """passcode -> {"effects": [{"type", "code", "hint": (propio, rival), "desc"}...]} de su c<code>.lua (caché), o
    None si no hay script. Los efectos sin tipo/código evaluable quedan con None en ese campo."""
    if code in _LUA["info"]:
        return _LUA["info"][code]
    if _LUA["index"] is None:
        idx = {}
        for dp, _dn, fns in os.walk(SCRIPTS_DIR):
            for fn in fns:
                if fn.startswith("c") and fn.endswith(".lua") and fn[1:-4].isdigit():
                    idx.setdefault(int(fn[1:-4]), os.path.join(dp, fn))
        _LUA["index"] = idx
    path = _LUA["index"].get(code)
    if not path:
        _LUA["info"][code] = None
        return None
    txt = open(path, encoding="utf-8", errors="replace").read()
    blocks, cur = [], {}
    for line in txt.splitlines():
        m = re.search(r"\b(\w+)\s*=\s*Effect\.CreateEffect\(", line)
        if m:
            cur[m.group(1)] = {"type": None, "code": None, "hint": (0, 0), "desc": None}
            blocks.append(cur[m.group(1)])
            continue
        m = re.search(r"\b(\w+)\s*=\s*(\w+):Clone\(\)", line)
        if m and m.group(2) in cur:
            cur[m.group(1)] = dict(cur[m.group(2)])
            blocks.append(cur[m.group(1)])
            continue
        m = re.search(r"\b(\w+):Set(Type|Code|HintTiming|Description)\((.*)\)", line)
        if not m or m.group(1) not in cur:
            continue
        b, what, arg = cur[m.group(1)], m.group(2), m.group(3)
        if what == "Type":
            b["type"] = _lua_eval(arg)
        elif what == "Code":
            b["code"] = _lua_eval(arg)
        elif what == "HintTiming":
            parts = [_lua_eval(a) or 0 for a in arg.split(",")]
            b["hint"] = (parts[0], parts[1] if len(parts) > 1 else parts[0])   # 1 argumento = los dos (ocgcore)
        else:
            d = re.search(r"Stringid\(\s*(?:id|\d+)\s*,\s*(\d+)\s*\)", arg)
            b["desc"] = int(d.group(1)) if d else None
    info = {"effects": [b for b in blocks if b["type"] is not None and b["type"] & _ET_CHAIN]}
    _LUA["info"][code] = info
    return info


class Card:
    __slots__ = ("uid", "owner", "code", "face", "defense", "known")

    def __init__(self, uid, owner, code):
        self.uid, self.owner, self.code = uid, owner, code
        self.face, self.defense = False, False
        self.known = None            # id YO2 que el cliente tiene ahora mismo para este uid


class Side:
    def __init__(self):
        self.deck, self.hand, self.grave, self.banish, self.extra = [], [], [], [], []
        self.mz = [None] * 5
        self.sz = [None] * 6         # 0..4 M/T, 5 = magia de campo

    def pile(self, name):
        return getattr(self, name)


class YO2Duel:
    attacking = viewing = False      # valores de clase: un duelo creado antes de una recarga en caliente los hereda
    attacker = None                  # (jugador, ranura) del último «Atacar» (espada de apuntar)
    ritual_mats = frozenset()        # §33 (valor de clase: un duelo de antes de una recarga en caliente no lo tiene)
    trigger = None                   # jugador de la última acción que abre ventana (CHAINING/invocación/ataque)
    chain_codes = ()                 # passcode de cada eslabón de la cadena abierta (chain_link_text / chain_priority)
    links = ()                       # card_link: vínculos colgados (tupla nueva en cada cambio, como chain_codes)
    solving = None                   # n.º del eslabón que se está resolviendo (moneda/dado salen de esa carta)
    _batch = None                    # lote de feed_many (para mirar hacia delante) y posición del evento en curso
    _bi = None
    tribute_armed = False            # HINT 500 visto: los MOVE campo -> cementerio pueden ser sacrificios
    retry_n = 0                      # B45: RETRY seguidos de la decisión en curso del jugador 0
    field_shown = None               # B23: (jugador, uid) de la magia de campo que el cliente tiene puesta (0x14)
    _field_seen = {}                 # B23: uid boca arriba en la SZONE 5 de cada jugador, visto en la última mirada
    fusion_armed = False             # HINT 511 visto: los MOVE de monstruos a la pila pueden ser materiales de fusión
    fusion_mats = ()                 # uids de los materiales ya anunciados con 0x4b/0x4c (índice = posición)
    fusion_started = ()              # B67 (fusion_official): los que ya tienen su 0x4c (los 0x4b salen todos al principio)
    battle_open = False              # B67: tras un BATTLE y antes del primer MOVE: los DAMAGE son de batalla
    list_dirty = False               # CIERRE_MINIATURA: hay 0x4b/0x4c en la cola del cliente sin ejecutar (escriben
                                     # DAT_012ccb34[i] con el id INTERNO; 0x6610 se aplica al llegar, el 0x32 en cola)
    timer_deadline = None            # turn_timer: instante (clock()) en que se acaba el turno del jugador 0
    ocg_phase = None                 # §20: última fase de ocgcore (NEW_PHASE; 0x200 = End Phase, que PHASE_MAP no tiene)
    discard_open = False             # §20: el cliente tiene el panel de descarte (DAT_012cc3aa != 0)
    hand_open = None                 # §41: jugador (de esta vista) cuya mano entera se enseña con 0x52 [1] (hand_open); None = ninguna
    # §24 (lp_reconcile). Valores de clase: un duelo creado antes de la carga en caliente los hereda (hud None = el
    # modelo se crea en su próximo evento con los LP del espejo de ese momento: se supone que entonces cuadraba)
    hud = None                       # marcador de LP del cliente, por jugador de esta vista: [número, pendiente, espera]
    lp_tag = None                    # (id del duelo, vista, sesión) para el aviso; lo saca _lp_tag de la sesión
    lp_ctx = ""                      # último cambio de LP visto (evento, jugador, cifra, carta) para el aviso
    lp_card = None                   # passcode de la última carta que encadena / ataca (CHAINING, ATTACK, BATTLE)
    turn_no = 0                      # turno según los eventos (select/NEW_TURN), si la sesión no lo da
    lp_desync_n = 0                  # PANEL: n.º de desfases corregidos en esta vista
    lp_desync = ()                   # PANEL: los últimos _LP_KEEP desfases (dicts; lista NUEVA en cada uno)
    lp_anim = frozenset()            # §31: jugadores con un 0x24/0x25/0x17 en este lote/decisión (el marcador aún cuenta)
    _LP_EVENTS = ("DAMAGE", "RECOVER", "PAY_LPCOST", "LPUPDATE")
    _HAND_KEEP = ("CONFIRM_CARDS", "CONFIRM_DECKTOP", "HINT", "CARD_HINT", "RETRY")   # §41: no cierran la mano abierta (hand_open)
    _LP_KEEP = 50
    clock = staticmethod(time.monotonic)
    _DISARM = ("CHAINING", "CHAIN_SOLVED", "CHAIN_END", "NEW_TURN", "NEW_PHASE", "SUMMONING", "SPSUMMONING", "SET")

    _TRIGGERS = ("CHAINING", "SUMMONING", "SPSUMMONING", "FLIPSUMMONING")
    # §28 (chain_event_text): último suceso que abre una ventana de cadena, para el texto del aviso. (tipo, jugador)
    # con jugador = quien lo hizo (o el que cobra el DAMAGE); None si ya pasó otra cosa. Valor de clase: un duelo de antes
    # de la carga en caliente lo hereda (y lo rellena desde su próximo mensaje)
    ev_last = None
    dmg_player = None                # jugador que cobró el último DAMAGE (189 «has sufrido» / 190 «has dañado»)
    _EV_KEEP = ("SUMMONING", "SUMMONED", "SPSUMMONING", "SPSUMMONED", "FLIPSUMMONING", "FLIPSUMMONED", "ATTACK",
                "DAMAGE_STEP_START", "DRAW", "NEW_PHASE")
    _EV_DROP = ("NEW_TURN", "NEW_PHASE", "CHAIN_END", "CHAINING", "ATTACK", "DRAW", "SET", "DAMAGE_STEP_START", "BATTLE",
                "DAMAGE", "SUMMONING", "SUMMONED", "SPSUMMONING", "SPSUMMONED", "FLIPSUMMONING", "FLIPSUMMONED")

    def _track_trigger(self, ev):
        """Para chain_importance_auto: quién hizo lo último a lo que responde la ventana de cadena. §28: y qué fue."""
        t = ev.get("type")
        prev = self.ev_last
        if t in self._EV_DROP:                               # §28: lo nuevo pisa lo anterior
            self.ev_last = None
        if t in self._EV_KEEP:
            p = ev.get("player") if t in ("DRAW", "NEW_PHASE") else ev.get("controller")
            if p is None and isinstance(ev.get("card"), dict):
                p = ev["card"].get("controller")
            if p is None and t.endswith("SUMMONED"):         # *SUMMONED no trae carta: el que anunció la invocación
                p = prev[1] if prev and prev[0] == t[:-2] + "ING" else self.trigger
            self.ev_last = (t, p, ev.get("phase"))
        elif t == "DAMAGE":
            self.dmg_player = ev.get("player")
        if t in self._TRIGGERS:
            p = ev.get("controller")
            if p is None and isinstance(ev.get("card"), dict):
                p = ev["card"].get("controller")
            self.trigger = p
        elif t == "ATTACK" and isinstance(ev.get("card"), dict):
            self.trigger = ev["card"].get("controller")
        elif t in ("NEW_TURN", "NEW_PHASE", "CHAIN_END"):
            self.trigger = None

    def __init__(self, decks, first=0, cardmap: CardMap | None = None, cfg: dict | None = None, log=None):
        """decks = [{"main": [passcodes], "extra": [...]}, {...}] en índices del protocolo (0 = humano), en el orden
        en que se imponen al cliente. Sus tamaños TIENEN que coincidir con los que el cliente conoce (0x6601 y
        0x6005): 0x40 solo sobrescribe huecos, no cambia cuántas cartas hay."""
        self.cm = cardmap or CardMap()
        self.cfg = dict(DEFAULT_CFG, **(cfg or {}))
        self.log = log or (lambda s: None)
        self.first = first
        self.cards = {}
        self.side = [Side(), Side()]
        self.lp = [self.cfg["lp"], self.cfg["lp"]]
        self.next_serial = [0, 0]
        for p in (0, 1):
            serial = 0
            for code in decks[p].get("main", []):
                serial += 1
                c = self._new_card(p, serial, code)
                self.side[p].deck.append(c.uid)
            for code in decks[p].get("extra", []) or []:
                serial += 1
                c = self._new_card(p, serial, code)
                self.side[p].extra.append(c.uid)
            base = self.cfg["token_serial_base"]
            self.next_serial[p] = (base - 1) if base else serial
        self.turn_player = None
        self.phase = None
        self.in_battle = False
        self.in_damage = False
        self.chain = []              # [(jugador, zona)] por eslabón
        self.pending = None          # MOVE al campo esperando a ver si es invocación/colocación/activación
        self.waiting = None          # select pendiente del jugador 0
        self.state = None            # cómo interpretar la próxima jugada del cliente
        self.finished = False
        self.stat_sent = {}          # uid -> (ATK, DEF) impuesto con EFFECT_SET_STATS (None = lo calcula el cliente)
        self.query = None            # callable -> query de duelcore; si está, cada petición va precedida de sync_board
        self._syncing = False
        self.attacking = False       # el jugador acaba de declarar un ataque: el SELECT_CARD siguiente es el objetivo
        self.viewing = False         # lista de solo-ver abierta: su 0x6423 0 al cerrarla obliga a reenviar la petición
        self.unknown = []            # avisos (mensajes no traducidos, ids sin carta, etc.)
        self.links = ()              # card_link: ((uid origen, uid destino, tipo), …) colgados en el cliente
        self.summoning = []          # summon_done: [(uid, P1 de 0x45)] invocándose (bit 3 puesto en el cliente)
        self.ritual_mats = set()     # §33: uids elegidos como sacrificio de un ritual (mano/campo): su MOVE -> 0x46
        self.place_manual = False    # opción «Auto Card Placing» OFF del cliente (u8 de 0x61fe != 0); lo pone la sesión

    # ================================================================================== utilidades de estado ======
    def _new_card(self, owner, serial, code):
        uid = owner + 2 * serial
        c = Card(uid, owner, code)
        self.cards[uid] = c
        return c

    def _warn(self, s):
        self.unknown.append(s)
        self.log("[traductor] " + s)

    def _yo2(self, code):
        y = self.cm.code_to_yo2(code) if code else None
        if y is None:
            if code:
                self._warn(f"passcode {code} sin id YO2: uso tapadera")
            y = self.cm.placeholders["monster"]
        return y

    def _visible(self, c: Card, where: str, p: int) -> int:
        """Id YO2 que el cliente debe tener para la carta c en `where` (vista del jugador 0): las cartas ocultas de la
        NPC (mazo, mano, extra, boca abajo en el campo) llevan una tapadera; el resto, la real."""
        if self.cfg["hide_npc"] and p == 1 and (where in ("deck", "hand", "extra") or
                                                 (where in ("mz", "sz") and not c.face)):
            if where == "sz":
                return self.cm.placeholders["spell" if self.cm.kind(c.code) == "spell" else "trap"]
            return self.cm.placeholders["monster"]
        return self._yo2(c.code)

    def find(self, uid):
        """uid -> (jugador, contenedor, índice)."""
        for p in (0, 1):
            s = self.side[p]
            for name in ("hand", "deck", "grave", "banish", "extra", "mz", "sz"):
                lst = s.pile(name)
                if uid in lst:
                    return p, name, lst.index(uid)
        return None

    @staticmethod
    def yo2_zone(where, i):
        """(contenedor, índice) -> (zona YO2, índice YO2). Campo: zona = ranura (0-4 monstruos, 5-9 M/T, 10 campo)."""
        if where == "mz":
            return i, 0
        if where == "sz":
            return (5 + i if i < 5 else 10), 0
        return PILE_ZONE[where], i

    def ocg_slot(self, controller, location, sequence):
        """(controlador, location, sequence) de ocgcore -> (jugador, contenedor, índice del espejo)."""
        p = controller
        if location == OCG_MZONE:
            return p, "mz", sequence
        if location == OCG_SZONE:
            if sequence > 5:
                self._warn(f"zona M/T {sequence} (péndulo) no existe en YO2")
            return p, "sz", min(sequence, 5)
        if location == OCG_DECK:
            # ocgcore: la cima es la sequence más alta; el cliente: índice 0 = cima
            return p, "deck", len(self.side[p].deck) - 1 - sequence
        if location in OCG_PILE:
            return p, OCG_PILE[location], sequence
        return p, None, sequence

    def uid_at(self, controller, location, sequence):
        p, where, i = self.ocg_slot(controller, location, sequence)
        if where is None:
            return None
        lst = self.side[p].pile(where)
        return lst[i] if 0 <= i < len(lst) else None

    def _packed_src(self, uid):
        p, where, i = self.find(uid)
        z, _ = self.yo2_zone(where, i)
        return (p & 1) | ((z & 0x1f) << 1) | ((uid & 0xff) << 6)

    def _identity(self, uid, real=False):
        """Si el cliente no tiene la identidad correcta del uid, la escribe con 0x40 en su hueco actual.
        En el campo se usa zona 0 + índice = ranura: el caso por defecto de tipo 0x40 escribe la palabra en
        zona+índice pero el bit de dueño en el índice (NOTES §2.3), así que zona 0 es lo único que no pisa otra ranura."""
        c = self.cards[uid]
        loc_ = self.find(uid)
        if not loc_:
            return []
        p, where, i = loc_
        want = self._yo2(c.code) if real else self._visible(c, where, p)
        if c.known == want:
            return []
        c.known = want
        serial = uid >> 1
        if where in ("mz", "sz"):
            slot, _ = self.yo2_zone(where, i)
            return [set_card(p, 0, slot, want, serial, c.owner), self._refresh_view(uid, p, slot, 0)]
        out = [set_card(p, PILE_ZONE[where], i, want, serial, c.owner)]
        if where == "hand":
            out.append(self._refresh_view(uid, p, ZONE_HAND, i))
        return out

    @staticmethod
    def _refresh_view(uid, p, zone, idx):
        """0x6202 tipo 0x1c: el objeto de vista de la carta (mano/campo) guarda su imagen al crearse (al salir del
        mazo) y 0x40 no la cambia: por eso las cartas de la NPC salían con el DIBUJO de la tapadera y los datos
        reales. Tipo 0x1c -> DUEL_VIEW(0x1c, P2 | P3<<1 | P4<<6 | P5<<14 | P6<<15, P0, P1 = uid): busca el objeto
        por (jugador, zona, índice) y le pasa ca4ca[uid] por +0x10 (recarga imagen e id del panel). Campo: zona =
        ranura e índice 0; mano: zona 0xb e índice = posición. Ejecutor 0x7211fa / vista 0x1c (work/view.c).
        CONFIRMADO (código) / sin probar en vivo. Ojo: con (zona, índice) mal crea un objeto nuevo (fantasma)."""
        return setcommand(T_VIEW1C, 0, (0, uid & 0xffff, p & 1, zone, idx, 0, 0))

    # ======================================================================================= arranque ============
    def start(self):
        """DUEL_START + imposición de los dos mazos y extras con 0x40 (orden del espejo, identidades visibles)."""
        out = [engine(0x01)]
        for p in (0, 1):
            for where in ("deck", "extra"):
                for i, uid in enumerate(self.side[p].pile(where)):
                    c = self.cards[uid]
                    c.known = self._visible(c, where, p)
                    out.append(set_card(p, PILE_ZONE[where], i, c.known, uid >> 1, c.owner))
        return [(0x6202, m) for m in out]

    # ==================================================================================== eventos duelcore =======
    def feed_many(self, events):
        """Lote de eventos hasta la siguiente decisión. Guarda el lote para que el sacrificio pueda mirar hacia
        delante (con feed() suelto no hay lote: se comporta como antes)."""
        out = []
        self._batch = events
        self.lp_anim = frozenset()                       # §31: lote nuevo
        try:
            for i, ev in enumerate(events):
                self._bi = i
                out += self.feed(ev)
        finally:
            self._batch = self._bi = None
        if events:
            last = events[-1]
            if not (last.get("ev") == "select" and last.get("player", 0) == 0):
                out += self._stat_boundary()             # §32: lote sin decisión propia (la de esta vista ya sincroniza)
            out += self._lp_watch([], (0, 1))            # §24: al cerrar el lote (BATTLE, cadena…), los dos jugadores
        return out

    def feed(self, ev: dict):
        """Un evento de duelcore -> [(id, bytes)] para el cliente."""
        kind = ev.get("ev")
        out = []
        if self.hud is None:
            self._hud_init()                              # §24: antes de que el evento cambie los LP del espejo
        if kind == "msg":
            t = ev.get("type")
            if self.pending is not None:
                out += self._flush_pending(ev)
            self._track_trigger(ev)
            if t in self._BATTLE_CLOSE:
                self.battle_open = False       # B67: lo que llega después ya no es el daño de batalla (_battle_damage_ahead)
            self._lp_note(ev, t)
            fn = getattr(self, "_m_" + str(t), None)
            if self.hand_open is not None and t not in self._HAND_KEEP:
                out += self._hand_close()        # §41: la mano enseñada se cierra (0x52 [0]) con lo primero que pasa después
            if fn:
                out += fn(ev) or []
            elif t not in IGNORED:
                self._warn(f"mensaje {t} sin traducción")
            if t in ("CHAIN_SOLVED", "CHAIN_END", "NEW_PHASE", "NEW_TURN"):
                if self.ritual_mats:
                    self.ritual_mats = set()                # §33: los sacrificios del ritual ya se movieron
            if t in self._DISARM:
                self.tribute_armed = self.fusion_armed = False
                self.fusion_mats = self.fusion_started = ()
            if self.pending is None:
                out += self._field_check()
            # §24: tras cada cambio de LP del motor, el marcador de ESE jugador
            out += self._lp_watch(out, (ev.get("player", 0) & 1,) if t in self._LP_EVENTS else None)
        elif kind == "select":
            out += self._flush_pending(None)
            out += self._field_check()
            out += self._lp_watch(out, (0, 1), ev.get("lp"))     # §24: antes de pedir la jugada, los dos
            self.lp_anim = frozenset()                           # §31: el jugador decide: el marcador ya habrá contado
            sel = self._on_select(ev)
            if self.hand_open is not None and (self.state or {}).get("kind") != KIND_OPP_HAND:
                out += self._hand_close()        # §41: se queda abierta solo mientras se elige en ella (Dustshoot…)
            self._hud_track(sel)
            return out + sel
        elif kind == "end":
            out += self._flush_pending(None)
            if not self.finished:
                w = ev.get("winner")
                out += self._finish(2 if w is None else w, ev.get("reason"))
            self._hud_track(out)
        return [(0x6202, m) if isinstance(m, bytes) else m for m in out]

    # B67: mensajes que cierran el «daño de batalla» abierto por BATTLE (los mismos que corta _battle_damage_ahead)
    _BATTLE_CLOSE = ("MOVE", "CHAINING", "ATTACK", "ATTACK_DISABLED", "DAMAGE_STEP_END", "NEW_PHASE", "NEW_TURN", "WIN")

    # --- §24: marcador de LP del cliente y reconciliación (lp_reconcile) ------------------------------------------
    def _hud_init(self):
        """Modelo del marcador con los LP del espejo (duelo nuevo: los iniciales; duelo de antes de la carga en
        caliente: los de ahora, que se suponen bien) y (duelo, vista) para el aviso."""
        self.hud = [[min(max(v, 0), 0xffff), 0, 0] for v in self.lp]
        self.lp_desync = []
        try:
            self.lp_tag = self._lp_tag()
        except Exception:                                 # noqa: BLE001  solo es para el aviso
            self.lp_tag = None

    def _lp_tag(self):
        """(id del duelo, vista, sesión) sacados de la sesión que creó este traductor (la lambda de query de
        OcgSession/PvpSession la lleva en su cierre); si no hay (pruebas), ("?", 0, None). Atributo duel_id = id."""
        did = getattr(self, "duel_id", None)
        for c in getattr(self.query, "__closure__", None) or ():
            try:
                o = c.cell_contents
            except ValueError:
                continue
            views = getattr(o, "views", None)
            if hasattr(o, "id") and (getattr(o, "d", None) is self or (isinstance(views, list) and
                                                                          any(v is self for v in views))):
                vi = next((i for i, v in enumerate(views) if v is self), 0) if isinstance(views, list) else 0
                return (str(did if did is not None else o.id), vi, o)
        return (str(did) if did is not None else "?", 0, None)

    def _lp_note(self, ev, t):
        """Contexto del último cambio de LP (para el aviso): evento, jugador, cifra y la última carta que encadenó o
        atacó; y el turno."""
        if t in ("CHAINING", "ATTACK", "BATTLE"):
            card = ev.get("card")
            self.lp_card = ev.get("code") or (card.get("code") if isinstance(card, dict) else None) or self.lp_card
        elif t == "NEW_TURN":
            self.turn_no += 1
        elif t in self._LP_EVENTS:
            n = ev.get("lp") if t == "LPUPDATE" else ev.get("amount")
            self.lp_ctx = f"{t} j{ev.get('player')} {n}" + (f" (carta {self.lp_card})" if self.lp_card else "")

    def _hud_track(self, msgs):
        """Aplica al modelo del marcador los comandos de LP de msgs, como el cliente (NOTES §23.4 / §24):
        0x24/0x25 (FUN_00716440): paso 0 vista 0xf CON espera solo si ningún marcador espera; paso 3 SIN espera.
        0x17 (vista 0xd): cada daño != 0 CON espera. 0x26 (FUN_007cef90): número = P0 (no toca lo pendiente).
        0x27 (vista 0x10, FUN_007cf050 x 2): pendiente y espera a 0 en los dos."""
        h = self.hud
        if h is None:
            return
        for m in msgs:
            if isinstance(m, tuple):
                if m[0] != 0x6202:
                    continue
                m = m[1]
            if not isinstance(m, (bytes, bytearray)) or len(m) < 20:
                continue
            t, b, p0, p1, p2 = struct.unpack_from(">5I", m)
            if t & 0xff:
                continue
            cmd, j = b & 0xfff, b >> 15 & 1
            if cmd in (C_LP_DAMAGE, C_LP_GAIN):
                self.lp_anim = self.lp_anim | {j}         # §31: el cliente cuenta hasta ~1 s; un 0x26 ahora se pisaría
                d = -p0 if cmd == C_LP_DAMAGE else p0
                if not (h[0][2] or h[1][2]):
                    self._hud_call(j, d, 1)
                self._hud_call(j, d, 0)
            elif cmd == C_LP_SET:
                h[j][0] = p0
            elif cmd == C_LP_RESET:
                self.lp_anim = frozenset()
                for x in h:
                    x[1] = x[2] = 0
            elif cmd == C_BATTLE_RUN:
                for k, dmg in enumerate((p0, p1)):
                    if dmg:
                        self.lp_anim = self.lp_anim | {k ^ 1 if (p2 >> (8 * k)) & 8 else k}
                        self._hud_call(k ^ 1 if (p2 >> (8 * k)) & 8 else k, -dmg, 1)

    def _hud_call(self, j, d, w):
        """FUN_007cefc0 sobre el marcador j: si espera con la MISMA cifra, la suelta; si no, la pisa (lo que esperaba
        se pierde). Sin espera, el número va hacia lo pendiente (0x7cf070) y se queda en 0 si baja; en 0 no sube."""
        x = self.hud[j]
        if x[2] and x[1] == d:
            x[2] = 0
        else:
            x[1], x[2] = d, w
        if not x[2] and x[1]:
            if x[1] < 0 or x[0]:
                x[0] = max(0, x[0] + x[1])
            x[1] = 0

    def _lp_watch(self, msgs, players=None, engine_lp=None):
        """§24: apunta msgs en el modelo del marcador y, si toca (players), lo compara con los LP del motor (los del
        select de duelcore si vienen; si no, el espejo). Cuadra -> []. No cuadra -> 0x27 + 0x26 de los dos, aviso
        «[lp] desfase …» y apunte para el panel (lp_desync / lp_desync_n)."""
        self._hud_track(msgs)
        if not players or self.finished or self.hud is None or \
                not self.cfg.get("lp_reconcile", DEFAULT_CFG.get("lp_reconcile")):
            return []
        if isinstance(engine_lp, (list, tuple)) and len(engine_lp) == 2 and \
                all(isinstance(v, int) and not isinstance(v, bool) for v in engine_lp):
            for j in (0, 1):
                if engine_lp[j] != self.lp[j]:
                    self._warn(f"LP del espejo {self.lp[j]} != motor {engine_lp[j]} (jugador {j}): se toma el motor")
                    self.lp[j] = engine_lp[j]
        want = [min(max(v, 0), 0xffff) for v in self.lp]
        h = self.hud
        bad = [j for j in players if h[j][2] or h[j][0] != want[j]]
        if not bad:
            return []
        tag = self.lp_tag or ("?", 0, None)
        sess = tag[2]
        turn = getattr(sess, "turn", None) or self.turn_no
        now = time.time()
        recs = []
        for j in bad:
            x = h[j]
            recs.append({"hora": time.strftime("%H:%M:%S", time.localtime(now)), "ts": round(now, 3),
                         "duelo": tag[0], "vista": tag[1], "turno": turn, "jugador": j, "jugador_abs": j ^ (tag[1] & 1),
                         "cliente": x[0], "pendiente": x[1], "espera": bool(x[2]), "motor": want[j],
                         "evento": self.lp_ctx})
            self.log(f"[lp] desfase duelo {tag[0]} vista {tag[1]} turno {turn} jugador {j}: cliente {x[0]}"
                     + (f" ({x[1]:+d} {'en espera' if x[2] else 'pendiente'})" if x[1] else "")
                     + f" / motor {want[j]}; último: {self.lp_ctx or '-'} -> 0x27 + 0x26")
        self.lp_desync_n += len(recs)
        self.lp_desync = (list(self.lp_desync) + recs)[-self._LP_KEEP:]
        out = [(0x6202, engine(C_LP_RESET, bad[0])),
               (0x6202, engine(C_LP_SET, 0, want[0])), (0x6202, engine(C_LP_SET, 1, want[1]))]
        self._hud_track(out)
        return out

    # --- turno y fase ------------------------------------------------------------------------------------------
    def _m_NEW_TURN(self, ev):
        p = ev["player"]
        out = []
        if self.turn_player is not None:
            out += self._stat_boundary()                            # §32: lo que caducó al final del turno, antes del 0x0c
            out.append(engine(C_TURN_CHANGE, self.turn_player))     # bit = el que termina (CONFIRMADO en vivo)
            out += self._lp_resync()                                # §23: marcador de LP = espejo (flag lp_resync)
        elif p != self.first:
            self._warn(f"el 1.er turno es de {p} pero el cliente cree que empieza {self.first} (0x6103)")
        self.turn_player = p
        self.phase = None
        self.in_battle = self.in_damage = False
        n = int(self.cfg.get("turn_timer") or 0)
        if n > 0:
            if p == 0:
                self.timer_deadline = self.clock() + n
            else:
                self.timer_deadline = None
                out.append((0x6461, timeup_warning(1, n, 1)))      # reloj del rival: lleno y quieto
        return out

    def _timer_msg(self):
        """0x6461 para el jugador 0 antes de cada petición (turn_timer)."""
        n = int(self.cfg.get("turn_timer") or 0)
        if n <= 0:
            return []
        left = n
        if self.turn_player == 0 and self.timer_deadline is not None:
            left = max(0, int(round(self.timer_deadline - self.clock())))
        return [(0x6461, timeup_warning(0, left, 0))]

    def _m_NEW_PHASE(self, ev):
        self.ocg_phase = ev["phase"]
        # §20: un panel de descarte que siguiera puesto (la decisión la tomó bot.ts sin pasar por _answer) se cierra
        out = [self._discard_var(0)] if self.discard_open else []
        if self.turn_player is not None:
            out += self._stat_boundary()                 # §32: lo que caducó al acabar la fase anterior (batalla…)
        if ev["phase"] == 512 and self.turn_player == 1 and self.cfg["npc_phrases"]:
            return out + [engine(C_NPC_TURN_END, 1)]                     # frase «termino mi turno»
        ph = PHASE_MAP.get(ev["phase"])
        if ph is None or ph == self.phase:
            return out
        self.phase = ph
        return out + [engine(C_PHASE[ph], self.turn_player or 0)]

    # --- robar ---------------------------------------------------------------------------------------------------
    def _m_DRAW(self, ev):
        p = ev["player"]
        s = self.side[p]
        n = len(ev["drawn"])
        out = []
        for k, d in enumerate(ev["drawn"]):
            if k >= len(s.deck):
                self._warn("robo con el mazo del espejo vacío")
                break
            uid = s.deck[k]
            c = self.cards[uid]
            if d.get("code"):
                c.code = d["code"]
            # identidad que verá en la mano: la real para el humano, tapadera para la NPC
            want = self._yo2(c.code) if p == 0 or not self.cfg["hide_npc"] else self.cm.placeholders["monster"]
            if c.known != want:
                c.known = want
                out.append(set_card(p, ZONE_DECK, k, want, uid >> 1, c.owner))
        took = s.deck[:n]
        del s.deck[:n]
        s.hand += took
        out.append(engine(C_DRAW, p, 0xffff, n))
        return out

    # --- movimientos ---------------------------------------------------------------------------------------------
    def _m_MOVE(self, ev):
        fr, to = ev["from"], ev["to"]
        if fr["location"] & OCG_OVERLAY or to["location"] & OCG_OVERLAY:
            self._warn("MOVE con materiales XYZ (no existe en YO2): ignorado")
            return []
        if fr["location"] == 0:                                   # carta nueva (ficha)
            return self._token(ev)
        uid = self.uid_at(fr["controller"], fr["location"], fr["sequence"])
        if uid is None:
            self._warn(f"MOVE sin carta en el espejo: {fr}")
            return []
        c = self.cards[uid]
        if ev.get("card"):
            c.code = ev["card"]
        if to["location"] == 0:
            return self._leave(uid)
        if (fr["controller"], fr["location"], fr["sequence"]) == (to["controller"], to["location"], to["sequence"]):
            return []                                             # mismo hueco (solo cambia la posición)
        if to["location"] in (OCG_MZONE, OCG_SZONE) and fr["location"] not in (OCG_MZONE, OCG_SZONE):
            self.pending = (uid, ev)                              # se decide con el mensaje siguiente
            return []
        if to["location"] in (OCG_MZONE, OCG_SZONE):
            if self.cfg.get("field_move_3d"):
                return self._field_move(uid, ev)
            return self._to_field(uid, ev, how="move")
        return self._to_pile(uid, ev)

    def _field_move(self, uid, ev):
        """MOVE campo -> campo con 0x3d (FUN_0070cd80): el registro de la ranura viaja entero (efectos tipo 3 como el
        par EFFECT_SET_STATS, contadores en +0xc, vínculos). La posición (+6) se copia del origen: si ocgcore la cambia
        a la vez se corrige con 0x34 detrás. Identidad: la que toque en el destino (la NPC tapa lo boca abajo)."""
        to = ev["to"]
        c = self.cards[uid]
        p_from, w_from, i_from = self.find(uid)
        p, where, slot_i = self.ocg_slot(to["controller"], to["location"], to["sequence"])
        src, _ = self.yo2_zone(w_from, i_from)
        dst, _ = self.yo2_zone(where, slot_i)
        pos = to.get("position") or POS_FU_ATK
        face = bool(pos & (POS_FU_ATK | POS_FU_DEF))
        defense = bool(pos & (POS_FU_DEF | POS_FD_DEF)) if where == "mz" else False
        out = []
        if face and not c.face:
            out += self._identity(uid, real=True)          # se descubre al moverse: identidad antes
        out.append(engine(C_FIELD_MOVE, p_from, (p_from & 1) | ((src & 0xff) << 8), (p & 1) | ((dst & 0xff) << 8),
                          int(face)))
        sent = self.stat_sent.pop(uid, None)
        self._remove(uid)
        if sent is not None:
            self.stat_sent[uid] = sent                     # el par EFFECT_SET_STATS va con el registro
        self.side[p].pile(where)[slot_i] = uid
        was_def = c.defense if w_from == "mz" else False
        c.face = face
        if where == "mz" and defense != was_def:
            out.append(engine(C_POSITION, p, dst, 0, 0))   # 0x34 alterna ATK/DEF (sin tocar la cara)
        c.defense = defense
        out += self._identity(uid)                         # tapadera o real según el nuevo controlador
        return out

    def _flush_pending(self, nxt):
        """Decide cómo mostrar un MOVE al campo según lo que venga detrás (orden real: MOVE y luego
        SUMMONING/SPSUMMONING/SET/CHAINING de la misma carta)."""
        if self.pending is None:
            return []
        uid, ev = self.pending
        self.pending = None
        to = ev["to"]
        how = "move"
        if nxt is not None and nxt.get("location") == to["location"] and nxt.get("sequence") == to["sequence"] \
                and nxt.get("controller") == to["controller"]:
            how = {"SUMMONING": "summon", "SPSUMMONING": "spsummon", "SET": "set", "CHAINING": "activate"}.get(
                nxt.get("type"), "move")
            if how != "move" and nxt.get("position"):
                # la posición buena es la del SUMMONING/SPSUMMONING/SET (en vivo, un monstruo de la NPC invocado
                # en DEFENSA salió en ATAQUE en el cliente: el MOVE puede traer la posición previa)
                ev = dict(ev, to=dict(to, position=nxt["position"]))
        return self._to_field(uid, ev, how)

    def _to_field(self, uid, ev, how):
        fr, to = ev["from"], ev["to"]
        c = self.cards[uid]
        p_from, w_from, i_from = self.find(uid)
        p, where, slot_i = self.ocg_slot(to["controller"], to["location"], to["sequence"])
        pos = to.get("position") or POS_FU_ATK
        face = bool(pos & (POS_FU_ATK | POS_FU_DEF))
        defense = bool(pos & (POS_FU_DEF | POS_FD_DEF))
        slot, _ = self.yo2_zone(where, slot_i)
        out = []
        mset = False                     # 0x4f con P2 bit0: el cliente pone el bit 3 (summon_done)
        # identidad antes de mover: real si queda boca arriba; tapadera de su tipo si queda boca abajo (NPC)
        c.face = face
        if face:
            out += self._identity(uid, real=True)
        elif p_from == 1 and self.cfg["hide_npc"]:
            c.known = None
            want = self.cm.placeholders["monster" if where == "mz" else
                                        "spell" if self.cm.kind(c.code) == "spell" else "trap"]
            out.append(self._set_known(uid, want))
        from_hand = w_from == "hand" and p_from == p
        fx = None                        # §23: 0x2c (efecto de invocación normal) detrás del 0x4f
        if how in ("summon", "spsummon"):
            if how == "summon" and p == 1 and self.cfg["npc_phrases"]:
                out.append(engine(C_NPC_ANNOUNCE, 1, 0x8000 | uid))       # frase «invoco» antes de invocar
            # §23 (un duelo empezado antes de la carga en caliente no lleva la clave en su cfg: manda DEFAULT_CFG)
            if how == "summon" and from_hand and where == "mz" and face \
                    and self.cfg.get("summon_kind_fx", DEFAULT_CFG.get("summon_kind_fx")):
                # normal / por sacrificio: 0x4f boca arriba (P2 bit0 = gasta la invocación normal, como al colocar)
                cmd = engine(C_SET_MONSTER, p, i_from, loc(p, slot, int(face), int(defense)), (uid << 1) | 1)
                fx = engine(C_SUMMON_FX, p, 0, slot & 0x1f, SUMMON_FX_KIND)
            else:
                cmd = engine(C_SUMMON, p, loc(p, slot, int(face), int(defense)), uid, 0)
        elif how == "set" and from_hand and where == "mz":
            cmd = engine(C_SET_MONSTER, p, i_from, loc(p, slot, int(face), int(defense)), (uid << 1) | 1)
            mset = True
        elif how in ("set", "activate") and from_hand and where == "sz":
            cmd = engine(C_SET_SPELL, p, i_from, ((slot & 0x1f) << 1) | (int(face) << 14), uid)
        else:
            cmd = engine(C_MOVE_FIELD, p_from, self._packed_src(uid), (p & 1) | ((slot & 0x1f) << 1),
                         (int(defense) << 8) | int(face))
        if how == "spsummon" and w_from == "extra" and self.fusion_mats and self.cfg.get("fusion_anim"):
            # RUN_FUSION con la identidad ya puesta (la vista 0x36 lee la carta del uid) y antes de que aparezca.
            # CFusionEffectTask (FUN_007b4690) copia los n ids de material de la tabla de la lista 0x6610 MODO 0xfe
            # (0x12be194; FUN_006929b0 solo la escribe con ese modo y no abre ventana): se manda antes  C
            mats = self.fusion_mats
            if self.cfg.get("fusion_official", DEFAULT_CFG.get("fusion_official")):
                # B67: un 0x4b sin su 0x4c deja la marca de material sin arrancar (solo la quitan las vistas 0x2a/0x2d de
                # cancelar): los que no llegaron a moverse en este lote se arrancan aquí
                for m in mats:
                    if m not in self.fusion_started:
                        pm, wm, im = self.find(m)
                        out.append(engine(C_FUSION_MAT, pm, self.yo2_zone(wm, im)[0] if wm == "mz" else PILE_ZONE[wm],
                                          mats.index(m), m))
            body = b"".join(struct.pack(">4H", self._yo2(self.cards[m].code) & 0x3fff, (m >> 1) & 0xff,
                                        self.cards[m].owner & 1, 0) for m in mats)
            out.append((M_LIST, struct.pack(">BIIII", 0, LIST_FUSION, 0, len(mats), 0) + body))
            out.append(engine(C_FUSION_RUN, p, 0, uid, len(mats) & 0xff))
            self.fusion_mats = self.fusion_started = ()
            self.fusion_armed = False
        out.append(cmd)
        if fx is not None:
            out.append(fx)
        if mset and self.cfg.get("summon_done"):
            # 0x4f con P2 bit0 (gasta la invocación normal) pone el bit 3; ocgcore no manda «colocada» después de SET,
            # así que se cierra ya (si no, al voltearla en batalla seguiría «invocándose» y sin efectos)  P
            out.append(engine(C_SUMMON_DONE, p, slot, SUMMON_DONE_SET, 0))
        self._remove(uid)
        self.side[p].pile(where)[slot_i] = uid
        c.defense = defense
        return out

    def _set_known(self, uid, want):
        c = self.cards[uid]
        c.known = want
        p, where, i = self.find(uid)
        if where in ("mz", "sz"):
            return set_card(p, 0, self.yo2_zone(where, i)[0], want, uid >> 1, c.owner)
        return set_card(p, PILE_ZONE[where], i, want, uid >> 1, c.owner)

    def _to_pile(self, uid, ev):
        to = ev["to"]
        c = self.cards[uid]
        p_from, w_from, i_from = self.find(uid)
        p, where, _ = self.ocg_slot(to["controller"], to["location"], 0)
        public = where in ("grave", "banish") or (where == "hand" and p == 0)
        out = self._identity(uid, real=True) if public else []
        dst_pile = self.side[p].pile(where)
        if where == "deck":
            top = to["sequence"] >= len(dst_pile)              # ocgcore: sequence = n-1 tras insertar = cima
            idx = 0 if top else 1
        elif where == "hand":
            idx = len(dst_pile)
        else:
            idx = len(dst_pile)
        fmat = self._fusion_material(uid, p_from, w_from, i_from, where)
        out += fmat
        # B67: un material de fusión que sale del campo NO se destruye (el oficial: 0x40, un movimiento sin CARD_BREAK)
        plain = bool(fmat) and bool(self.cfg.get("fusion_official", DEFAULT_CFG.get("fusion_official")))
        if uid in self.ritual_mats and where == "grave" and w_from in ("mz", "hand"):
            # §33: sacrificio de un ritual, como el oficial: 0x46 (ranura o 0xb = mano, 0xe, uid)  C
            self.ritual_mats = set(self.ritual_mats) - {uid}
            z = self.yo2_zone(w_from, i_from)[0] if w_from == "mz" else ZONE_HAND
            out.append(engine(C_TRIBUTE, p_from, z & 0xff, ZONE_GRAVE, uid))
        elif w_from == "mz" and where == "grave" and self.cfg.get("tribute_anim") and self.tribute_armed \
                and self._tribute_ahead():
            # sacrificio de una invocación: 0x46 «生贄召喚開始» (marca + a la pila del dueño, sin destrucción)
            slot, _ = self.yo2_zone(w_from, i_from)
            if self.cfg.get("tribute_mark"):
                out.append(engine(C_TRIBUTE_MARK, p_from, slot & 0xff, 0, 0))
            out.append(engine(C_TRIBUTE, p_from, slot & 0xff, ZONE_GRAVE, uid))
        elif w_from == "mz" and where == "grave" and not plain:
            # al cementerio desde una zona de monstruo: con animación de destrucción (0x31)
            slot, _ = self.yo2_zone(w_from, i_from)
            out.append(engine(C_TO_GRAVE, p_from, slot & 0xff, ZONE_GRAVE, uid))
        else:
            # P2 = 0x20 en los materiales de fusión que van al cementerio (oficial: 14 de 14 tras el 0x4c; ninguna otra
            # 0x3f/0x40 lo lleva): el cliente pone el valor 2 en los bits 16-17 de la palabra de la carta  (B67)
            out.append(engine(C_MOVE, p_from, self._packed_src(uid),
                              (p & 1) | ((PILE_ZONE[where] & 0x1f) << 1) | ((idx & 0xff) << 6),
                              0x20 if plain and where == "grave" else 0))
        self._remove(uid)
        c.face = where in ("grave", "banish") or (where == "hand" and p == 0)
        c.defense = False
        if where == "deck":
            if top:
                dst_pile.insert(0, uid)
            else:
                dst_pile.append(uid)
        else:
            dst_pile.append(uid)
        if not public and where in ("deck", "extra", "hand"):
            # vuelve a estar oculta: tapadera (NPC) — se reescribe ya en su hueco nuevo
            want = self._visible(c, where, p)
            if want != c.known:
                out.append(self._set_known(uid, want))
        return out

    def _remove(self, uid):
        self.stat_sent.pop(uid, None)            # el efecto tipo 3 se va con la carta (y no sigue a la carta)
        if self.summoning:
            self.summoning = [x for x in self.summoning if x[0] != uid]   # se fue antes del *SUMMONED (negada)
        p, where, i = self.find(uid)
        lst = self.side[p].pile(where)
        if where in ("mz", "sz"):
            lst[i] = None
        else:
            del lst[i]

    def _leave(self, uid):
        """Carta que sale del juego (ficha que deja el campo). No hay comando de «borrar»: va al cementerio y el
        cliente, al ver una carta de tipo 10 (ficha) en card_prop, no la mete en la pila y borra su objeto (§23)."""
        vanish = self.cfg.get("token_vanish", DEFAULT_CFG.get("token_vanish"))
        if not vanish:
            self._warn("carta a location 0 (ficha): se manda al cementerio del cliente")
        p, where, i = self.find(uid)
        slot, _ = self.yo2_zone(where, i)
        out = []
        if vanish and where == "mz" and self.cfg.get("tribute_anim") and self.tribute_armed and self._tribute_ahead():
            # sacrificio de una invocación (la ficha de Gorz por Cyber Dragon, pvp1008): 0x46 como los demás
            if self.cfg.get("tribute_mark"):
                out.append(engine(C_TRIBUTE_MARK, p, slot & 0xff, 0, 0))
            out.append(engine(C_TRIBUTE, p, slot & 0xff, ZONE_GRAVE, uid))
        elif where in ("mz", "sz"):
            out.append(engine(C_TO_GRAVE, p, slot & 0xff, ZONE_GRAVE, uid))
        self._remove(uid)
        del self.cards[uid]
        return out

    def _token(self, ev):
        to = ev["to"]
        p, where, slot_i = self.ocg_slot(to["controller"], to["location"], to["sequence"])
        if where != "mz":
            self._warn("carta nueva fuera de la zona de monstruos: ignorada")
            return []
        self.next_serial[p] += 1
        c = self._new_card(p, self.next_serial[p], ev.get("card", 0))
        pos = to.get("position") or POS_FU_ATK
        c.face = bool(pos & 5)
        c.defense = bool(pos & 12)
        y = self._yo2(c.code)
        c.known = y
        self.side[p].mz[slot_i] = c.uid
        return [engine(C_TOKEN, p, loc(p, slot_i, int(c.face), int(c.defense)), self.cm.yo2_to_internal(y), 0)]

    def _m_HINT(self, ev):
        """HINT tipo 3 (HINT_SELECTMSG): 500 = va a elegir sacrificios, 511 = materiales de fusión. No se traduce."""
        if ev.get("hint_type") == 3:
            if ev.get("hint") == HINTMSG_RELEASE:
                self.tribute_armed = True
            elif ev.get("hint") == HINTMSG_FMATERIAL and not self.fusion_armed:
                self.fusion_armed = True
                self.fusion_mats = self.fusion_started = ()
        return []

    def _tribute_ahead(self):
        """¿Los MOVE campo -> cementerio en curso son los sacrificios de una invocación? Mira el resto del lote: tras
        los MOVE (sacrificios, equipos que caen) tiene que venir la invocación -- MOVE mano -> zona de monstruos, o el
        HINT tipo 3 de «elige zona» con el código de una carta de la mano (va antes de SELECT_PLACE). CHAINING, otra
        cosa o el final del lote = no (coste de un efecto: se queda en 0x31)."""
        if not self._batch or self._bi is None:
            return False
        for ev in self._batch[self._bi + 1:]:
            t = ev.get("type")
            if ev.get("ev") != "msg":
                return False
            if t == "MOVE":
                if ev["from"]["location"] == OCG_HAND and ev["to"]["location"] == OCG_MZONE:
                    return True
                continue
            if t == "HINT":
                h = ev.get("hint")
                if ev.get("hint_type") == 3 and h and any(self.cards[u].code == h for s in self.side for u in s.hand):
                    return True
                continue
            if t in ("SUMMONING", "SET"):
                return True
            if t in self._TRIBUTE_STOP:
                return False
            # el resto (MISSED_EFFECT, FIELD_DISABLED…: visto en la traza 181824 entre los MOVE y el HINT) no corta
        return False

    _TRIBUTE_STOP = ("CHAINING", "CHAINED", "CHAIN_SOLVING", "CHAIN_SOLVED", "CHAIN_END", "CHAIN_NEGATED",
                     "CHAIN_DISABLED", "NEW_TURN", "NEW_PHASE", "SPSUMMONING", "FLIPSUMMONING", "ATTACK", "BATTLE",
                     "DAMAGE", "RECOVER", "PAY_LPCOST", "LPUPDATE", "DRAW", "WIN")

    def _fusion_material(self, uid, p_from, w_from, i_from, where):
        """0x4b + 0x4c de un material que está a punto de salir de su sitio (fusion_anim, tras el HINT 511)."""
        c = self.cards[uid]
        if not (self.cfg.get("fusion_anim") and self.fusion_armed) or self.cm.kind(c.code) != "monster" \
                or w_from not in ("mz", "hand", "grave", "deck") or where not in ("grave", "banish", "deck"):
            return []
        zone = self.yo2_zone(w_from, i_from)[0] if w_from == "mz" else PILE_ZONE[w_from]
        if self.cfg.get("fusion_official", DEFAULT_CFG.get("fusion_official")):
            return self._fusion_material_oficial(uid, p_from, zone)
        i = len(self.fusion_mats)
        self.fusion_mats = tuple(self.fusion_mats) + (uid,)
        self.list_dirty = True
        return [engine(C_FUSION_MARK, p_from, zone, i, uid), engine(C_FUSION_MAT, p_from, zone, i, uid)]

    def _fusion_material_oficial(self, uid, p_from, zone):
        """B67: el orden del oficial. Con el primer material salen los 0x4b de TODOS los que se van a mover en este lote
        (P1 = 0, 1…); cada material lleva luego solo su 0x4c (el cliente busca la marca por la posición REAL de la carta
        en ese momento: mano = su sitio en la mano, campo = ranura, y al arrancar una de la mano baja el índice de las
        demás, por eso no hay que repetir nada). Si no hay lote (feed suelto) o el material no estaba en la vista previa,
        se marca ahora, como antes."""
        out = []
        if not self.fusion_mats:
            ahead = self._fusion_ahead()
            uids = [u for u, _, _, _ in ahead]
            if uid not in uids:
                uids.insert(0, uid)
            self.fusion_mats = tuple(uids)
            self.fusion_started = ()
            for k, u in enumerate(uids):
                pu, wu, iu = self.find(u)
                out.append(engine(C_FUSION_MARK, pu, self.yo2_zone(wu, iu)[0] if wu == "mz" else PILE_ZONE[wu], k, u))
        elif uid not in self.fusion_mats:
            out.append(engine(C_FUSION_MARK, p_from, zone, len(self.fusion_mats), uid))
            self.fusion_mats = tuple(self.fusion_mats) + (uid,)
        out.append(engine(C_FUSION_MAT, p_from, zone, self.fusion_mats.index(uid), uid))
        self.fusion_started = tuple(self.fusion_started) + (uid,)
        self.list_dirty = True
        return out

    _FUSION_STOP = ("SPSUMMONING", "SUMMONING", "SET", "CHAINING", "CHAIN_SOLVED", "CHAIN_END", "NEW_PHASE", "NEW_TURN")

    def _fusion_ahead(self):
        """B67: los materiales de la fusión que traen los MOVE del resto del lote (desde el MOVE en curso): monstruos de
        campo, mano, cementerio o mazo que van al cementerio, a desterradas o al mazo, hasta que llega el monstruo de
        fusión (MOVE desde el extra) o algo que no es de la fusión. [(uid, jugador, contenedor, índice)] en orden."""
        if not self._batch or self._bi is None:
            return []
        out, used = [], set()
        for ev in self._batch[self._bi:]:
            if ev.get("ev") != "msg":
                break
            t = ev.get("type")
            if t == "MOVE":
                f, to = ev["from"], ev["to"]
                if f["location"] == OCG_EXTRA:
                    break
                if f["location"] not in (OCG_MZONE, OCG_HAND, OCG_GRAVE, OCG_DECK) \
                        or to["location"] not in (OCG_GRAVE, OCG_REMOVED, OCG_DECK):
                    continue
                p, where, i = self.ocg_slot(f["controller"], f["location"], f["sequence"])
                lst = self.side[p].pile(where) if where else []
                cand = [u for u in lst if u is not None and u not in used and self.cards[u].code == ev.get("card")]
                uid = lst[i] if 0 <= i < len(lst) and lst[i] in cand else (cand[0] if cand else None)
                if uid is not None and self.cm.kind(self.cards[uid].code) == "monster":
                    used.add(uid)
                    pu, wu, iu = self.find(uid)
                    out.append((uid, pu, wu, iu))
            elif t in self._FUSION_STOP:
                break
        return out

    def _m_SUMMONING(self, ev):
        self._summon_start(ev, SUMMON_DONE_BITS["SUMMONED"])
        return []        # ya lo cubre el MOVE pendiente (0x2d)

    def _m_SPSUMMONING(self, ev):
        self._summon_start(ev, SUMMON_DONE_BITS["SPSUMMONED"])   # también fichas (0x48 ya salió con su MOVE)
        return []

    def _m_SET(self, ev):
        return []        # 0x4f (+ su 0x45) salen del MOVE pendiente

    def _m_FLIPSUMMONING(self, ev):
        """La invocación por volteo NO trae POS_CHANGE (visto en duelcore): solo FLIPSUMMONING con la posición nueva.
        0x34 con P1 = P2 = 1 pone el bit 3 «invocándose» (FUN_0070d550): lo quita el 0x45 de FLIPSUMMONED."""
        out = self._m_POS_CHANGE(dict(ev, prev_position=None), anim=1)
        self._summon_start(ev, SUMMON_DONE_BITS["FLIPSUMMONED"])
        return out

    # --- invocación terminada (summon_done, NOTES §19) ------------------------------------------------------------
    def _summon_start(self, ev, bits):
        """SUMMONING/SPSUMMONING/FLIPSUMMONING: apunta la carta (ya en su ranura del espejo) hasta el *SUMMONED."""
        if not self.cfg.get("summon_done") or ev.get("location") != OCG_MZONE:
            return
        uid = self.uid_at(ev["controller"], ev["location"], ev["sequence"])
        if uid is not None and all(u != uid for u, _ in self.summoning):
            self.summoning.append((uid, bits))

    def _summon_done(self, ev):
        """SUMMONED/SPSUMMONED/FLIPSUMMONED (sin datos de carta en ocgcore): 0x45 por cada carta de ese tipo que sigue
        en una zona de monstruo del espejo. Una invocación negada (Juicio Solemne) no trae *SUMMONED y su carta ya se
        fue del campo (_remove la quita de la lista)."""
        bits = SUMMON_DONE_BITS[ev["type"]]
        out, keep = [], []
        for uid, b in self.summoning:
            if b != bits:
                keep.append((uid, b))
                continue
            w = self.find(uid)
            if w and w[1] == "mz":
                out.append(engine(C_SUMMON_DONE, w[0], self.yo2_zone(w[1], w[2])[0], b, 0))
        self.summoning = keep
        return out

    _m_SUMMONED = _m_SPSUMMONED = _m_FLIPSUMMONED = _summon_done

    def _m_POS_CHANGE(self, ev, anim=0):
        uid = self.uid_at(ev["controller"], ev["location"], ev["sequence"])
        if uid is None:
            self._warn(f"POS_CHANGE sin carta: {ev}")
            return []
        c = self.cards[uid]
        p, where, i = self.find(uid)
        slot, _ = self.yo2_zone(where, i)
        pos = ev["position"]
        face0, face1 = c.face, bool(pos & 5)                   # el «antes» es el del espejo (= lo que tiene el cliente)
        def0, def1 = c.defense, bool(pos & 12)
        if face0 == face1 and def0 == def1:
            return []
        out = []
        if face1 and not face0:
            c.face = True
            out += self._identity(uid, real=True)
        if where == "sz" or def0 == def1:
            if face0 != face1:
                out.append(engine(C_FLIP, p, slot, 1, 0))     # solo la cara (activar colocada, voltear atacada)
        else:
            # 0x34 alterna SIEMPRE ATK/DEF; P1 = 1 voltea además la cara (invocación por volteo, Book of Moon)
            out.append(engine(C_POSITION, p, slot, int(face0 != face1), anim))
        c.face, c.defense = face1, def1
        if not face1:
            self.stat_sent.pop(uid, None)        # boca abajo: el cliente borra los efectos tipo 3
        if not face1 and p == 1 and self.cfg["hide_npc"]:
            want = self._visible(c, where, p)
            if want != c.known:
                out.append(self._set_known(uid, want))
        return out

    # --- cadena ----------------------------------------------------------------------------------------------------
    def _chain_reveal(self, pp, zone, where, uid, c):
        """§35: 0x2e como el oficial. P0 = zona YO2 | bits<<8 (6 = magia/trampa y campo, 8 = monstruo del campo, 0xa = mano /
        cementerio / desterradas: los tres valores que mandaba Konami; el cliente solo lee el bit 0, que aquí es 0), P1 = id
        INTERNO de la carta, P2 = su uid. El 0xc de los efectos de monstruo que se disparan solos no se distingue (D): el
        cliente no lee esos bits."""
        bits = {"sz": 6, "mz": 8}.get(where, 0xa)
        return engine(C_REVEAL, pp, zone | bits << 8, self.cm.yo2_to_internal(self._yo2(c.code)) or 0, uid)

    def _m_CHAINING(self, ev):
        out = []
        reveal = self.cfg.get("chain_reveal", DEFAULT_CFG.get("chain_reveal"))
        p = ev["controller"]
        if p == 1 and self.cfg["npc_phrases"]:
            out.append(engine(C_NPC_CHAIN, 1, 0))                         # frase «encadeno»
        uid = self.uid_at(p, ev["location"], ev["sequence"])
        zone, hidx = 0, 0
        if uid is not None:
            c = self.cards[uid]
            if ev.get("code"):
                c.code = ev["code"]
            pp, where, i = self.find(uid)
            zone, hidx = self.yo2_zone(where, i)
            if where in ("mz", "sz"):
                if not c.face:
                    c.face = True
                    out += self._identity(uid, real=True)
                    out.append(engine(C_FLIP, pp, zone, 1, 0))
                if reveal:
                    out.append(self._chain_reveal(pp, zone, where, uid, c))     # §35: como el oficial
                elif self.cfg.get("activate_flash"):
                    # destello «se activa» sobre la carta del campo: motor 0x30 (FUN_00412981) busca en el campo la
                    # carta boca arriba con ese id INTERNO (FUN_006ec290) y le hace DUEL_VIEW 0x21; si no la encuentra
                    # enseña la carta grande (FUN_0073bbe0, el mismo id que 0x48, que va bien en vivo).  P
                    out.append(engine(C_FLASH, pp, self.cm.yo2_to_internal(self._yo2(c.code)) or 0, 0))
            elif where != "sz":
                out += self._identity(uid, real=True)
                # efecto de monstruo / desde la mano o el cementerio: enseñar la carta (0x2e del oficial, §35; 0x2f antes)
                if reveal:
                    out.append(self._chain_reveal(pp, zone, where, uid, c))
                else:
                    out.append(engine(C_HAPPEN, pp, self._yo2(c.code)))
        n = ev.get("chain_size", len(self.chain) + 1)
        # §35: el oficial pone el UID de la carta en P1 del 0x12 (1356/1356); el manejador lo usa para cerrar la marca que
        # dejó el 0x2e en la mano (FUN_006e5910/006e2d00 + vista 0x13). Sin 0x2e (o sin carta) queda como antes
        p1 = uid if (reveal and uid is not None) else (hidx if zone == ZONE_HAND else 0)
        out.append(engine(C_CHAIN_SET, p, zone, p1, n))
        self.chain.append((p, zone))
        code = ev.get("code") or (self.cards[uid].code if uid is not None else None)
        self.chain_codes = tuple(self.chain_codes) + (code,)       # tupla nueva: el valor de clase no se comparte
        return out

    def _m_CHAIN_SOLVING(self, ev):
        n = ev.get("chain_size", len(self.chain))
        if 0 < n <= len(self.chain):
            p, zone = self.chain[n - 1]
            self.solving = n
            return [engine(C_CHAIN_RUN, p, zone, 0, n)]
        return []

    def _m_CHAIN_END(self, ev):
        self.chain = []
        self.solving = None
        self.chain_codes = ()
        return self._live_stats()                # equipos/efectos resueltos: repintar ATK/DEF ya

    # --- vínculos carta -> carta (card_link / target_lockon; NOTES «Vínculo carta-objetivo / equipo») -----------
    def _field_ref(self, d):
        """dict de ocgcore {controller, location, sequence} -> (uid, jugador, ranura YO2) si está en el campo."""
        if not isinstance(d, dict) or d.get("location") not in (OCG_MZONE, OCG_SZONE):
            return None
        uid = self.uid_at(d.get("controller", 0), d["location"], d.get("sequence", 0))
        if uid is None:
            return None
        p, where, i = self.find(uid)
        return uid, p, self.yo2_zone(where, i)[0]

    def _uid_ref(self, uid):
        """uid -> jugador | ranura<<8 (id del vínculo en el cliente) si sigue en el campo; si no, None."""
        f = self.find(uid) if uid in self.cards else None
        if not f or f[1] not in ("mz", "sz"):
            return None
        return (f[0] & 1) | (self.yo2_zone(f[1], f[2])[0] << 8)

    def _add_link(self, src, dst, kind, anim=True):
        """Cuelga el vínculo src -> dst en el cliente: 0x42 (animación + entrada) o 0x36 (solo entrada)."""
        a, b = self._uid_ref(src), self._uid_ref(dst)
        if a is None or b is None:
            return []
        self.links = tuple(lk for lk in self.links if lk[:2] != (src, dst)) + ((src, dst, kind),)
        if self.stat_sent.get(dst) is not None:
            # la entrada nueva va DESPUÉS del par EFFECT_SET_STATS y el cliente sumaría su bono: repetir el par
            self.stat_sent[dst] = LINK_STALE
        if anim:
            return [engine(C_LINK, a & 1, a, b, kind)]
        return [engine(C_EFFECT_ADD, b & 1, (b >> 8) | (kind << 8), a, 0)]

    def _del_link(self, lk):
        """Quita un vínculo: 0x37 P0 = ranura destino | tipo<<8, P1 = id origen, P2 = 0 (FUN_0070e0b0: tipo > 5 ->
        FUN_006e2ba0 borra la 1.ª entrada de vínculo con ese id). Si una de las dos cartas ya no está en el campo,
        el cliente ya lo borró al quitarla (FUN_006f0600 -> FUN_006e2870 / lista de la ranura)."""
        self.links = tuple(x for x in self.links if x != lk)
        a, b = self._uid_ref(lk[0]), self._uid_ref(lk[1])
        if a is None or b is None:
            return []
        return [engine(C_EFFECT_DEL, b & 1, (b >> 8) | (lk[2] << 8), a, 0)]

    def _prune_links(self):
        self.links = tuple(lk for lk in self.links if self._uid_ref(lk[0]) is not None
                           and self._uid_ref(lk[1]) is not None)

    def _m_EQUIP(self, ev):
        if not self.cfg.get("card_link"):
            return []
        self._prune_links()
        s, t = self._field_ref(ev.get("card")), self._field_ref(ev.get("target"))
        if not s or not t:
            self._warn(f"EQUIP fuera del campo: {ev.get('card')} -> {ev.get('target')}")
            return []
        kind = self.cfg.get("link_kind_equip", 10)
        out = []
        for lk in self.links:                    # re-equipar: el vínculo viejo se quita (ocgcore no manda UNEQUIP)
            if lk[0] == s[0] and lk[2] >= 10 and lk[1] != t[0]:
                out += self._del_link(lk)
        if any(lk[:2] == (s[0], t[0]) for lk in self.links):
            return out
        return out + self._add_link(s[0], t[0], kind)

    def _m_UNEQUIP(self, ev):
        if not self.cfg.get("card_link"):
            self._warn("mensaje UNEQUIP sin traducción")         # lo de siempre con el flag apagado
            return []
        self._prune_links()
        s = self._field_ref(ev.get("card"))
        out = []
        for lk in self.links:
            if s and lk[0] == s[0] and lk[2] >= 10:
                out += self._del_link(lk)
        return out

    def _m_CARD_TARGET(self, ev):
        if not self.cfg.get("card_link"):
            return []
        self._prune_links()
        s, t = self._field_ref(ev.get("card")), self._field_ref(ev.get("target"))
        if not s or not t or any(lk[:2] == (s[0], t[0]) for lk in self.links):
            return []
        return self._add_link(s[0], t[0], self.cfg.get("link_kind_target", 9), self.cfg.get("link_target_anim", True))

    def _m_CANCEL_TARGET(self, ev):
        if not self.cfg.get("card_link"):
            return []
        self._prune_links()
        s, t = self._field_ref(ev.get("card")), self._field_ref(ev.get("target"))
        out = []
        for lk in self.links:
            if s and t and lk[:2] == (s[0], t[0]) and lk[2] < 10:
                out += self._del_link(lk)
        return out

    def _m_BECOME_TARGET(self, ev):
        """Objetivos de una activación -> 0x29 CARD_LOCKON (B = jugador de la carta, P0 = ranura, 0xb, 0xe o 0xf, P1 = uid).
        Cementerio / desterradas (§35, lockon_pile): el cliente sube la carta a la cima de su pila y abre la vista 0x17,
        que es como señalaba el oficial un objetivo del cementerio. Solo cambia el orden de la pila DEL CLIENTE (la imagen
        de la cima); el espejo sigue el orden de ocgcore porque todo lo demás va por uid."""
        if not self.cfg.get("target_lockon"):
            return []
        out = []
        for d in ev.get("cards") or []:
            if not isinstance(d, dict):
                continue
            uid = self.uid_at(d.get("controller", 0), d.get("location", 0), d.get("sequence", 0))
            f = self.find(uid) if uid is not None else None
            pile = bool(f) and f[1] in ("grave", "banish") and self.cfg.get("lockon_pile", DEFAULT_CFG.get("lockon_pile"))
            if not f or (f[1] not in ("mz", "sz", "hand") and not pile):
                continue
            z = self.yo2_zone(f[1], f[2])[0]
            out.append(engine(C_LOCKON, f[0], z, uid, 0))
        return out

    # --- LP --------------------------------------------------------------------------------------------------------
    def _lp(self, p, delta):
        self.lp[p] = max(0, self.lp[p] + delta)

    def _m_DAMAGE(self, ev):
        p, a = ev["player"], ev["amount"]
        self._lp(p, -a)
        if self.cfg.get("lp_type_oficial", DEFAULT_CFG.get("lp_type_oficial")):
            # oficial: 1 = batalla, 0 = efecto (B67). Con battle_damage_split el daño de batalla es solo el que llega
            # antes del primer MOVE tras el BATTLE (la ficha de Ojama hace su 300 DESPUÉS de irse del campo)
            if self.cfg.get("battle_damage_split", DEFAULT_CFG.get("battle_damage_split")):
                kind = 1 if self.battle_open else 0
            else:
                kind = 1 if self.in_damage else 0
        else:
            kind = 0 if self.in_damage else 1
        return [engine(C_LP_DAMAGE, p, min(a, 0xffff), kind, 0)]

    def _m_PAY_LPCOST(self, ev):
        p, a = ev["player"], ev["amount"]
        self._lp(p, -a)
        return [engine(C_LP_DAMAGE, p, min(a, 0xffff), 2, 0)]

    def _m_RECOVER(self, ev):
        p, a = ev["player"], ev["amount"]
        self._lp(p, a)
        return [engine(C_LP_GAIN, p, min(a, 0xffff), 1, 0)]

    def _m_LPUPDATE(self, ev):
        p, lp = ev["player"], ev["lp"]
        if self.cfg.get("lp_set_direct", DEFAULT_CFG.get("lp_set_direct")) and self.hud is not None:
            return self._lp_set_direct(p, lp)
        if lp == self.lp[p]:
            return []
        self.lp[p] = lp
        return [engine(C_LP_SET, p, min(lp, 0xffff))]

    def _lp_set_direct(self, p, lp):
        """§31 (lp_set_direct): MSG_LPUPDATE = LP absoluto (Duel.SetLP, reinicio) -> 0x26 LIFE_SET del jugador YA, y el
        modelo del marcador (hud) lo recoge al salir (_hud_track). Cuadra (número = valor y nada en espera) -> nada.
        Si el marcador espera un -N o hay un 0x24/0x25/0x17 de ESTE jugador en el lote, el 0x26 solo se pisaría (NOTES §24.2): va
        0x27 (quita lo pendiente) + 0x26 de los dos, como la corrección de lp_reconcile pero sin aviso de desfase."""
        lp = max(0, lp)
        self.lp[p] = lp
        want = min(lp, 0xffff)
        x = self.hud[p]
        if x[0] == want and not x[1] and not x[2]:
            return []
        if x[1] or x[2] or p in self.lp_anim:
            q = p ^ 1
            return [engine(C_LP_RESET, p), engine(C_LP_SET, p, want),
                    engine(C_LP_SET, q, min(max(self.lp[q], 0), 0xffff))]
        return [engine(C_LP_SET, p, want)]

    # --- batalla ---------------------------------------------------------------------------------------------------
    def _m_ATTACK(self, ev):
        a, t = ev["card"], ev.get("target")
        self.in_battle = True
        self.attacking = False
        pre = self._live_stats()                 # el ATK con el que ataca (equipos recién puestos) antes de la flecha
        if not t or not t.get("location"):
            return pre + [engine(C_DIRECT, a["controller"], a["sequence"])]
        return pre + [engine(C_ATTACK, a["controller"], a["sequence"],
                             ((t["sequence"] & 0xff) << 8) | (t["controller"] & 1))]

    def _m_DAMAGE_STEP_START(self, ev):
        self.in_damage = True
        return []

    def _m_BATTLE(self, ev):
        self.battle_open = True
        a, t = ev["card"], ev.get("target") or {}
        dmg = [0, 0]
        flags = 0
        if not t.get("location"):
            dmg[1 - a["controller"]] = a["attack"]
        elif t["position"] & 3:                                   # objetivo en ataque
            d = a["attack"] - t["attack"]
            if d > 0:
                dmg[t["controller"]] = d
            elif d < 0:
                dmg[a["controller"]] = -d
        elif a["attack"] < t["defense"]:
            dmg[a["controller"]] = t["defense"] - a["attack"]
        # banderas por jugador (byte 0 = J0, byte 1 = J1) -> DUEL_VIEW 0xd -> FUN_00754790(+0x3c/+0x40):
        #   bit 1 = destruida: estado 7 con golpe y sonido (FUN_00754980); sin él y sin daño, estado 6 = «rebote»  C
        #   bit 0 = en defensa: el impacto se dibuja en horizontal (FUN_00754bb0, audit duelo_vista hueco 3)      P
        # Antes se ponía el bit 0 por «destruida»: el defensor en DEF se pintaba vertical (como en ataque) y rebotaba.
        for side in (a, t):
            if not side.get("location"):
                continue
            b = 0
            if side.get("position", 0) & (POS_FU_DEF | POS_FD_DEF):
                b |= 1
            if side.get("destroyed"):
                b |= 2
            flags |= b << (8 * (side["controller"] & 1))
        ahead = self._battle_damage_ahead() if self.cfg.get("battle_damage_real",
                                                                 DEFAULT_CFG.get("battle_damage_real")) else None
        if ahead is not None:
            dmg = ahead
        return [engine(C_BATTLE_RUN, a["controller"], min(dmg[0], 0xffff), min(dmg[1], 0xffff), flags)]

    def _battle_damage_ahead(self):
        """§23 (LP): daño de batalla REAL de cada jugador = los DAMAGE que siguen al BATTLE en el lote (hasta el fin del
        paso de daño, otra batalla, una cadena o un select). La vista 0xd de 0x17 deja en el marcador de LP del cliente
        (DAT_012a9070+0xa9c+j*0x44, FUN_007cefc0 con espera) el daño que se le dice, y solo lo suelta el 0x25 con la
        MISMA cifra (o el siguiente 0x17 igual, que lo aplica de más). Un 0x17 con daño que ocgcore no hace (pvp1010
        22:44:28: ataque directo de 600 sin DAMAGE) dejaba -600 colgado; el 0x17 siguiente de 600 (22:45:16) lo soltaba
        y su 0x25 restaba otros 600: el cliente pintó 0 LP con 600 de verdad. None = sin lote (feed suelto)."""
        if not self._batch or self._bi is None:
            return None
        dmg = [0, 0]
        # B67: ocgcore manda BATTLE, DAMAGE (batalla), MOVE (el destruido) y, después, los DAMAGE de efecto al irse la carta
        # (ficha de Ojama: 300). Solo cuenta el de antes del primer MOVE: el oficial lleva ese solo en el 0x17
        stop = ("DAMAGE_STEP_END", "BATTLE", "ATTACK", "ATTACK_DISABLED", "CHAINING", "NEW_PHASE", "NEW_TURN", "WIN")
        if self.cfg.get("battle_damage_split", DEFAULT_CFG.get("battle_damage_split")):
            stop += ("MOVE",)
        for ev in self._batch[self._bi + 1:]:
            if ev.get("ev") != "msg":
                break
            t = ev.get("type")
            if t == "DAMAGE":
                dmg[ev["player"] & 1] += ev["amount"]
            elif t in stop:
                break
        return dmg

    def _lp_resync(self):
        """§23 (LP, red de seguridad NUESTRA, flag lp_resync): 0x26 LIFE_SET con los LP del espejo para los dos
        jugadores. La vista 0xe pone el número del marcador (FUN_007cef90) y 0x26 el LP del motor: arregla un marcador
        desfasado (no se ve nada si ya coincide). Va al empezar cada turno, cuando no queda ninguna animación de LP."""
        if not self.cfg.get("lp_resync", DEFAULT_CFG.get("lp_resync")):
            return []
        return [engine(C_LP_SET, p, min(max(self.lp[p], 0), 0xffff)) for p in (0, 1)]

    def _end_attack(self, ev):
        if ev.get("type") == "ATTACK_DISABLED":
            self.log("[traductor] ocgcore: ATTACK_DISABLED (el ataque se anula; no hay cálculo de daño)")
        if not self.in_battle:
            return []
        self.in_battle = self.in_damage = False
        return [engine(C_ATTACK_END, self.turn_player or 0, 0, 0)]

    _m_DAMAGE_STEP_END = _m_ATTACK_DISABLED = _end_attack

    # --- barajar / enseñar -----------------------------------------------------------------------------------------
    def _reimpose(self, p, where):
        out = []
        for i, uid in enumerate(self.side[p].pile(where)):
            c = self.cards[uid]
            c.known = self._visible(c, where, p)
            out.append(set_card(p, PILE_ZONE[where], i, c.known, uid >> 1, c.owner))
            if where == "hand":
                # 0x40 no toca el objeto de vista: sin esto, la imagen del índice i se queda con la carta que puso
                # ahí el 0x4e del cliente (research/audit/duelo_vista.md, hueco 1)
                out.append(self._refresh_view(uid, p, ZONE_HAND, i))
        return out

    def _m_SHUFFLE_DECK(self, ev):
        p = ev["player"]
        random.shuffle(self.side[p].deck)
        seed = random.getrandbits(32)
        # animación del cliente (baraja con su RNG) y luego se reimpone el orden del espejo
        return [engine(C_SHUFFLE, p, seed & 0xffff, seed >> 16)] + self._reimpose(p, "deck")

    def _m_SHUFFLE_HAND(self, ev):
        p = ev["player"]
        s = self.side[p]
        codes = list(ev.get("cards") or [])
        old = list(s.hand)
        found = None
        if codes and len(codes) == len(old):
            # semilla con la que el cliente baraja SU mano (= la del espejo) justo al orden de ocgcore (ver
            # client_hand_perm): así la imagen que deja la vista 0x11 es la de la carta de cada índice
            found = hand_seed_for([self.cards[u].code for u in old], codes)
            if found:
                s.hand[:] = [old[k] for k in found[1]]
            else:
                pool, new = list(old), []
                for code in codes:
                    j = next((k for k, u in enumerate(pool) if self.cards[u].code == code), 0)
                    new.append(pool.pop(j))
                s.hand[:] = new
        if found:
            seed = found[0]
        else:
            seed = random.getrandbits(32)
            if len(old) > 1:
                self.log(f"[traductor] SHUFFLE_HAND J{p}: sin semilla exacta para {len(old)} cartas; se reimpone "
                         f"la mano y se refresca la imagen de cada índice")
        return [engine(C_HAND_SHUFFLE, p, seed & 0xffff, seed >> 16)] + self._reimpose(p, "hand")

    def _m_SHUFFLE_SET_CARD(self, ev):
        """Barajar monstruos colocados (Magical Hats) -> 0x49 MONST_SHUFFLE (FUN_00714ea0, NOTES MIS-1). El cliente elige la
        permutación él solo: toma las ranuras de P2 (máscara) en orden, las baraja con Fisher-Yates y el LCG de MSVC
        sembrado con P1<<16 | P0 (el mismo de la mano) y mueve el REGISTRO ENTERO de cada ranura (carta, cara,
        posición, valor): la carta de la ranura S[i] pasa a la ranura S[perm[i]]. Como con la mano, se ELIGE la semilla
        con la que ese reparto coincide con el de ocgcore. Los uid viajan con la carta: no hace falta 0x40 ni 0x1d.
        Qué ranuras entran lo dice el mensaje; DÓNDE acaba cada carta, no (el core de EDOPro de ocgcore-wasm escribe
        la 2.ª lista a ceros): ver _shuffle_dest."""
        if not self.cfg.get("monst_shuffle"):
            return []
        groups = {}
        for c in ev.get("cards") or []:
            f = c.get("from") or {}
            if f.get("location") != OCG_MZONE:
                self._warn(f"SHUFFLE_SET_CARD fuera de la zona de monstruos {f}: se ignora")
                return []
            groups.setdefault(f["controller"] & 1, []).append(c)
        out = []
        for p, cs in sorted(groups.items()):
            slots = sorted(c["from"]["sequence"] for c in cs)
            mz = self.side[p].mz
            if len(set(slots)) != len(slots) or any(not 0 <= s < 5 or mz[s] is None for s in slots):
                self._warn(f"SHUFFLE_SET_CARD J{p} {slots}: ranuras repetidas o vacías en el espejo; se ignora")
                continue
            if len(slots) < 2:
                continue                                      # el cliente no hace nada con menos de 2 ranuras (return)
            mv = self._shuffle_dest(p, cs, slots)
            if mv is None:
                continue
            want = [slots.index(mv[s]) for s in slots]        # índice destino de cada ranura de origen
            seed = monst_seed_for(want)
            old = [mz[s] for s in slots]
            for s, u in zip(slots, old):
                mz[mv[s]] = u
            mask = sum(1 << s for s in slots)
            out.append(engine(C_MONST_SHUFFLE, p, seed & 0xffff, seed >> 16, mask))
        return out

    def _shuffle_dest(self, p, cs, slots):
        """-> {ranura origen: ranura destino} de un SHUFFLE_SET_CARD, o None (y aviso) si no se puede saber.
        1) Si el mensaje trae la 2.ª lista bien (`to` en la zona de monstruos del mismo jugador y con las mismas
           ranuras), es el reparto.
        2) Si no (así sale del ocgcore-wasm 0.1.2: la 2.ª lista son ceros, medido con 75 mensajes de Magical Hats), se
           saca de lo que ocgcore dice que hay AHORA en cada ranura: si una carta de ese hueco sale después en este
           mismo lote (MOVE desde esa ranura: las de Magical Hats se destruyen al acabar la Battle Phase), su código;
           si no, el de la query de duelcore. Las cartas se emparejan por código (las iguales son intercambiables) y
           las que ya están donde toca se quedan quietas."""
        mz = self.side[p].mz
        to = [c.get("to") or {} for c in cs]
        if all(t.get("location") == OCG_MZONE and (t.get("controller", p) & 1) == p for t in to):
            mv = {c["from"]["sequence"]: t["sequence"] for c, t in zip(cs, to)}
            if sorted(mv.values()) == slots:
                return mv
        now = {s: self.cards[mz[s]].code for s in slots}
        target = {}
        state = None
        for s in slots:
            code = None
            for e in (self._batch[self._bi + 1:] if self._batch and self._bi is not None else ()):
                f = e.get("from") or {}
                if (e.get("ev") == "msg" and e.get("type") == "MOVE" and f.get("controller") == p
                        and f.get("location") == OCG_MZONE and f.get("sequence") == s):
                    code = e.get("card")
                    break
            if not code:
                if state is None and self.query is not None:
                    try:
                        state = self.query() or {}
                    except Exception as ex:                   # noqa: BLE001
                        self._warn(f"query falló: {ex!r}")
                        state = {}
                pl = (state or {}).get("players") or []
                zone = (pl[p].get("mzone") or []) if p < len(pl) else []
                cd = zone[s] if s < len(zone) else None
                code = cd.get("code") if cd else None
            target[s] = code
        if sorted(target.values(), key=str) != sorted(now.values(), key=str):
            self._warn(f"SHUFFLE_SET_CARD J{p} {slots}: no sé dónde ha quedado cada carta (espejo {list(now.values())}, "
                       f"ocgcore {list(target.values())}); no se manda 0x49")
            return None
        mv, used = {}, set()
        for s in slots:                                       # las que ya están donde toca
            if target[s] == now[s]:
                mv[s] = s
                used.add(s)
        for s in slots:
            if s not in mv:
                t = next(t for t in slots if t not in used and target[t] == now[s])
                mv[s] = t
                used.add(t)
        return mv

    def _m_CONFIRM_CARDS(self, ev):
        out = []
        cards = ev.get("cards", [])
        whole = self._whole_hand(cards)
        for cd in cards:
            uid = self.uid_at(cd["controller"], cd["location"], cd["sequence"])
            if uid is None:
                continue
            self.cards[uid].code = cd.get("code") or self.cards[uid].code
            out += self._identity(uid, real=True)
            if cd["location"] == OCG_HAND and whole is None:
                out.append(engine(C_HAND_SHOW, cd["controller"], uid))
        if whole is not None:
            # §41: la mano ENTERA de un jugador (Dustshoot, Crush Card Virus, Royal Tribute…): 0x52 [1] como el oficial en vez de
            # un 0x51 (y su panel de detalle) por carta; se cierra con 0x52 [0] (_hand_close)
            out += self._hand_close()
            out.append(engine(C_HAND_OPEN, whole, 1))
            self.hand_open = whole
        return out

    def _whole_hand(self, cards):
        """§41: jugador cuya mano ENTERA (2 o más cartas) enseña este CONFIRM_CARDS, o None (flag hand_open apagado, cartas
        sueltas, pilas, dos jugadores, o solo algunas de la mano: las demás seguirían ocultas)."""
        if not self.cfg.get("hand_open", DEFAULT_CFG.get("hand_open")) or len(cards) < 2:
            return None
        who = {cd["controller"] for cd in cards if cd["location"] == OCG_HAND}
        if len(who) != 1 or any(cd["location"] != OCG_HAND for cd in cards):
            return None
        p = who.pop()
        if p not in (0, 1) or len(self.side[p].hand) != len(cards) or len({cd["sequence"] for cd in cards}) != len(cards):
            return None
        return p

    def _opp_hand_done(self, st):
        """§41: acabada la elección en la mano abierta: 0x29 sobre cada carta elegida (zona 0xb, como el oficial) y 0x52 [0]."""
        out = []
        for p, zone, idx, i in st["cands"]:
            if i in st["chosen"] and zone == ZONE_HAND and 0 <= idx < len(self.side[p & 1].hand):
                uid = self.side[p & 1].hand[idx]
                if uid is not None:
                    out.append((0x6202, engine(C_LOCKON, p & 1, ZONE_HAND, uid, 0)))
        return out + [(0x6202, m) for m in self._hand_close()]      # lo que va al cliente: (id de mensaje, bytes)

    def _hand_close(self):
        """§41: cierra la mano abierta con 0x52 [0] (lo que el oficial manda al acabar de verla)."""
        p, self.hand_open = self.hand_open, None
        return [] if p is None else [engine(C_HAND_OPEN, p, 0)]

    _m_CONFIRM_DECKTOP = _m_CONFIRM_CARDS

    def _m_TOSS_DICE(self, ev):
        # 0x5d -> DUEL_VIEW 0x39 (CDice, ctor FUN_007aa630): P0 = zona de donde sale (FUN_007bea20), P1 = 1..6 (las
        # tablas del paso FUN_007aa990 se indexan 1..6; sprite 6+P1). El jugador va en B.  C
        p = ev["player"]
        zone = self._toss_zone(p)[1] if self.cfg.get("toss_anim") else 0
        return [engine(C_DICE, p, zone, r) for r in ev.get("results", [])[:2]]

    def _toss_zone(self, p):
        """(jugador, zona) de la carta que lanza: el eslabón que se resuelve (o el último abierto); si no hay cadena,
        la ranura 0 del jugador (D: posición de arranque de la animación; luego va al centro)."""
        n = self.solving or len(self.chain)
        if 0 < n <= len(self.chain):
            return self.chain[n - 1]
        return p, 0

    def _m_TOSS_COIN(self, ev):
        if not self.cfg.get("toss_anim"):
            return []
        res = [bool(r) for r in ev.get("results", [])][:16]
        if not res:
            return []
        j, zone = self._toss_zone(ev["player"])
        bits = sum(1 << i for i, r in enumerate(res) if r)
        p0 = (j & 1) | ((zone & 0x1f) << 1) | ((len(res) & 0xff) << 6) | ((self.cfg.get("coin_call", 1) & 1) << 14)
        return [engine(C_COIN, ev["player"], p0, 0, bits)]

    # --- contadores / intercambio ------------------------------------------------------------------------------
    def _counter(self, ev, sign):
        if not self.cfg.get("counters"):
            return []
        if ev.get("location") not in (OCG_MZONE, OCG_SZONE):
            return []                                     # el cliente solo guarda contadores en las ranuras
        t = ev.get("counter_type", 0) & 0xfff
        kind = COUNTER_MAP.get(t)
        if kind is None:
            self._warn(f"contador 0x{ev.get('counter_type', 0):x} sin tipo en el cliente: no se ve")
            return []
        p, where, i = self.ocg_slot(ev["controller"], ev["location"], ev["sequence"])
        if self.side[p].pile(where)[i] is None:
            return []
        slot, _ = self.yo2_zone(where, i)
        n = min(int(ev.get("count", 1)), 0x7fff)
        return [engine(C_COUNTER, p, slot, (sign * n) & 0xffff, kind)]

    def _m_ADD_COUNTER(self, ev):
        return self._counter(ev, 1)

    def _m_REMOVE_COUNTER(self, ev):
        return self._counter(ev, -1)

    # --- MSG_CARD_HINT (NOTES §22) ----------------------------------------------------------------------------------
    def _m_CARD_HINT(self, ev):
        """CHINT_TURN -> contador de turnos (0x3b); CHINT_RACE/ATTRIBUTE/CARD -> lo declarado en +0xc (0x3a). Los
        demás (CHINT_NUMBER, DESC_ADD/REMOVE) no tienen dónde pintarse en el cliente: nada, como antes. Un duelo
        empezado antes de la carga en caliente no lleva las claves en su cfg: manda DEFAULT_CFG."""
        kind = ev.get("card_hint")
        if kind == CHINT_TURN:
            key = "turn_counter"
        elif kind in (CHINT_RACE, CHINT_ATTRIBUTE, CHINT_CARD):
            key = "declare_hint"
        else:
            return []
        if not self.cfg.get(key, DEFAULT_CFG.get(key)):
            return []
        if kind == CHINT_TURN:
            neg = self._hint_player_count(ev)
            if neg is not None:
                return neg
        at = self._hint_slot(ev)
        if at is None:
            return []
        p, slot, internal = at
        v = int(ev.get("description") or 0) & 0xffffffff
        if kind == CHINT_TURN:
            if internal not in TURN_CARDS:
                return []                                 # el cliente no lo pintaría (0x3b no hace nada)
            if v == 0:
                return [engine(C_TURN_COUNT, p, slot, 0, 0)]          # SetTurnCounter(0): a 0, sin vista
            # 0x3b SUMA 1 (con la animación del reloj): antes se fija +0xc = v-1 con 0x3a (sin vista) para que el
            # número sea el de ocgcore aunque el cliente no lo tuviera (duelo de antes de la carga, salto de valor)
            v = min(v, 0xffff)
            return [engine(C_SLOT_VALUE, p, slot, v - 1, 0), engine(C_TURN_COUNT, p, slot, 1, 0)]
        if kind == CHINT_RACE and internal in DECL_RACE:
            val = next((i for i, b in sorted(YO2_RACE.items()) if v & b), 0)
        elif kind == CHINT_ATTRIBUTE and internal in DECL_ATTR:
            val = next((i for i, b in sorted(YO2_ATTR.items()) if v & b), 0)
        elif kind == CHINT_ATTRIBUTE and internal in DECL_ATTR_BITS:
            val = sum(1 << i for i, b in YO2_ATTR.items() if v & b)
        elif kind == CHINT_CARD and internal in DECL_CARD:
            y = self.cm.code_to_yo2(v) if v else None
            val = (self.cm.yo2_to_internal(y) or 0) if y else 0
        else:
            return []
        if not val:
            self._warn(f"CARD_HINT {kind} = {v:#x} sin equivalente en el cliente: no se pinta")
            return []
        return [engine(C_SLOT_VALUE, p, slot, val & 0xffff, val >> 16)]

    def _hint_player_count(self, ev):
        """§34: CHINT_TURN de una carta de TURN_CARDS_NEG (esté donde esté) -> [0x3b (jugador, id interno, 1, n)], o
        None si no es de esas. Jugador = el del turno que se cuenta (CCV: la víctima; Gold Sarcophagus: el que la
        activó, en su Standby), salvo Final Countdown (cuenta todos los turnos: el dueño). n = 0 no se manda: con P2 = 0
        el cliente tomaría P0 como RANURA (camino del reloj de arena de §22)."""
        uid = self.uid_at(ev.get("controller", 0), ev.get("location", 0), ev.get("sequence", 0))
        c = self.cards.get(uid) if uid is not None else None
        if c is None:
            return None
        y = self.cm.code_to_yo2(c.code)
        internal = self.cm.yo2_to_internal(y) if y else None
        if internal not in TURN_CARDS_NEG:
            return None
        if not self.cfg.get("turn_counter_player", DEFAULT_CFG.get("turn_counter_player")):
            return []
        v = int(ev.get("description") or 0) & 0xffffffff
        if v <= 0:
            return []
        p = c.owner if internal == FINAL_COUNTDOWN else (self.turn_player or 0)
        return [engine(C_TURN_COUNT, p, internal, 1, min(v, 0xffff))]

    def _hint_slot(self, ev):
        """(jugador, ranura YO2, id interno) de la carta boca arriba del campo del CARD_HINT, o None."""
        if ev.get("location") not in (OCG_MZONE, OCG_SZONE):
            return None                                   # +0xc solo existe en las ranuras
        p, where, i = self.ocg_slot(ev.get("controller", 0), ev["location"], ev.get("sequence", 0))
        lst = self.side[p].pile(where)
        u = lst[i] if 0 <= i < len(lst) else None
        if u is None or not self.cards[u].face:
            return None
        y = self.cm.code_to_yo2(self.cards[u].code)
        internal = self.cm.yo2_to_internal(y) if y else None
        if not internal:
            return None
        slot, _ = self.yo2_zone(where, i)
        return p, slot, internal

    def _m_SWAP(self, ev):
        if not self.cfg.get("swap_cmd"):
            return []
        a, b = ev.get("card1") or {}, ev.get("card2") or {}
        ua = self.uid_at(a.get("controller", 0), a.get("location", 0), a.get("sequence", 0))
        ub = self.uid_at(b.get("controller", 0), b.get("location", 0), b.get("sequence", 0))
        if ua is None or ub is None:
            self._warn(f"SWAP sin carta en el espejo: {a} / {b}")
            return []
        pa, wa, ia = self.find(ua)
        pb, wb, ib = self.find(ub)
        if wa not in ("mz", "sz") or wb not in ("mz", "sz"):
            return []
        sa, _ = self.yo2_zone(wa, ia)
        sb, _ = self.yo2_zone(wb, ib)
        self.side[pa].pile(wa)[ia], self.side[pb].pile(wb)[ib] = ub, ua
        out = [engine(C_SWAP, pa, (pa & 1) | ((sa & 0xff) << 8), (pb & 1) | ((sb & 0xff) << 8), 0)]
        for u in (ua, ub):                                # la tapadera depende del controlador nuevo
            out += self._identity(u)
        return out

    def _m_WIN(self, ev):
        return self._finish(ev["player"], ev.get("reason"))

    def _field_check(self):
        """B23 (flag field_change): si la magia de campo boca arriba ha cambiado, motor 0x14 FIELD_CHANGE (P0 = id
        interno; 0 = ninguna). El cliente guarda UNA sola (DAT_012ca4c0): manda la última que se ha puesto boca
        arriba; si esa se va y queda la del otro jugador (reglas con campo por jugador), la del otro. Se mira con el
        espejo ya actualizado (tras cada mensaje sin MOVE retenido y antes de cada petición)."""
        if not self.cfg.get("field_change") or self.finished:
            return []
        cur = {}
        for p in (0, 1):
            uid = self.side[p].sz[5]
            c = self.cards.get(uid) if uid is not None else None
            if c is not None and c.face:
                cur[p] = uid
        new = [p for p, uid in cur.items() if self._field_seen.get(p) != uid]
        self._field_seen = dict(cur)
        shown = self.field_shown
        if new:
            target = (new[-1], cur[new[-1]])
        elif shown is not None and cur.get(shown[0]) == shown[1]:
            target = shown
        elif cur:
            p = next(iter(cur))
            target = (p, cur[p])
        else:
            target = None
        if target == shown:
            return []
        self.field_shown = target
        if target is None:
            return [engine(C_FIELD_CHANGE, 0, 0)]
        internal = self.cm.yo2_to_internal(self._yo2(self.cards[target[1]].code)) or 0
        if not internal:
            self._warn(f"magia de campo sin id interno: {self.cards[target[1]].code}")
        return [engine(C_FIELD_CHANGE, target[0], internal)]

    def _finish(self, winner, reason):
        """DUEL_END (0x05) + fin de escena (tipo 0x4d). Resultado: 1 = gana el jugador 0, 2 = gana el 1, 0 = empate."""
        if self.finished:
            return []
        self.finished = True
        res = {0: 1, 1: 2}.get(winner, 0)
        return [engine(C_DUEL_END, 0, res, 0, 0), duel_finish(res or 3, self.cfg["finish_mode"])]

    # ================================================================================== ATK/DEF ==================
    def sync_board(self, state, resend=True):
        """Corrige el cliente con el tablero REAL de ocgcore (query de duelcore): cara/posición de cada carta del
        campo (0x34/0x35 vía _m_POS_CHANGE, con identidad/tapadera) y ATK/DEF (sync_stats). Si ya se había mandado
        una petición de jugada y hay algo que corregir, la vuelve a mandar DETRÁS (las correcciones deben llegar
        antes que 0x6611/0x6612/0x1a). Con self.query (callable -> state) puesto, _on_select lo hace solo y antes."""
        if not state or self._syncing:
            return []
        out = []
        for p, pl in enumerate((state.get("players") or [])[:2]):
            for key, loc_ in (("mzone", OCG_MZONE), ("szone", OCG_SZONE)):
                for i, cd in enumerate((pl.get(key) or [])[:5]):
                    uid = self.uid_at(p, loc_, i)
                    if uid is None or not cd or not cd.get("position"):
                        continue
                    c = self.cards[uid]
                    pos = cd["position"]
                    if c.face != bool(pos & 5) or (key == "mzone" and c.defense != bool(pos & 12)):
                        self._warn(f"desfase de posición j{p} {key}[{i}]: cliente cara={c.face} def={c.defense}, "
                                   f"ocgcore pos={pos}: se corrige")
                        if cd.get("code"):
                            c.code = cd["code"]
                        out += [m if isinstance(m, tuple) else (0x6202, m) for m in
                                self._m_POS_CHANGE({"controller": p, "location": loc_, "sequence": i, "position": pos})]
        out += self._stat_cmds(state)
        if out and resend and self.waiting is not None and (self.state or {}).get("mode") not in (None, "auto"):
            self._syncing = True
            try:
                out += self._resend()
            finally:
                self._syncing = False
        return out

    def sync_stats(self, state):
        """Compatibilidad con la integración actual (ocg_duel llama a sync_stats tras feed_many): = sync_board."""
        return self.sync_board(state)

    def _live_stats(self):
        """ATK/DEF de ocgcore EN MEDIO de un lote (antes de un ataque, al cerrar una cadena), si hay self.query.
        Formato de 0x36 comprobado en el exe: la cola del motor (FUN_00709b10) guarda u16 B, P0, P1, P2 y
        FUN_0070df30 llama a FUN_006e2930(jugador = B>>15, ranura = P0 & 0xff, tipo = P0>>8, id = P1, valor = P2),
        que cuelga la entrada de la lista de la RANURA (DAT_012c88be + ranura*0x14 + j*0xe0c), la misma que lee
        FUN_006fae40 al pintar (FUN_00759430). Sin uid: P1 & 0x8000 iría a la lista por uid (FUN_006e2a80), que el
        cálculo de ATK no lee. Lo que fallaba es CUÁNDO: solo se mandaba tras la petición de jugada (0x1a) y nunca
        entre el equipo de la NPC y su ataque (audit duelo_vista, hueco 7).  P"""
        if self.query is None or not self.cfg.get("stat_sync") or self._syncing:
            return []
        try:
            return self._stat_cmds(self.query())
        except Exception as e:                                   # noqa: BLE001
            self._warn(f"query falló: {e!r}")
            return []

    def _stat_boundary(self):
        """§32: ATK/DEF de ocgcore en un límite de fase/turno o al cerrar un lote que no acaba en una decisión de esta
        vista. ocgcore deshace sin mensaje lo «hasta el final del turno/fase» (RESET_PHASE: Shrink, Rush Recklessly…) y
        el par EFFECT_SET_STATS se quedaba colgado en el cliente: lo pinta tal cual, el cliente no lo hace caducar
        (tipo 3). Solo sale algo si el valor cambió respecto a lo mandado (_stat_cmds compara con stat_sent)."""
        if not self.cfg.get("stat_sync_turn"):
            return []
        return self._live_stats()

    def _stat_cmds(self, state):
        """state = query de duelcore ({"players": [{"mzone": [{code, position, attack, defense}|None]*5}, ...]}).
        Devuelve [(0x6202, bytes)] para que el cliente pinte el ATK/DEF de ocgcore en cada monstruo boca arriba:
        0x37 (quitar el par anterior) + 0x36 tipo 3 ×2 (ATK, DEF) con EFFECT_SET_STATS. Solo si cambia respecto a
        lo último mandado; con los valores impresos (cards.cdb) se deja al cliente (solo se quita el par).
        Llamarlo antes de cada decisión del jugador (tras cadenas, fases, equipos…) y en el cambio de turno.  P"""
        if not self.cfg.get("stat_sync") or not state:
            return []
        out = []
        for p, pl in enumerate((state.get("players") or [])[:2]):
            for i, cd in enumerate((pl.get("mzone") or [])[:5]):
                uid = self.side[p].mz[i]
                if uid is None or not cd or not (cd.get("position", 0) & 5):
                    continue
                atk, df = cd.get("attack"), cd.get("defense")
                if not isinstance(atk, int) or not isinstance(df, int) or atk < 0 or df < 0:
                    continue
                c = self.cards[uid]
                if cd.get("code") and c.code and cd["code"] != c.code:
                    continue        # la query es de más tarde (fin del lote) y en esa ranura ya hay otra carta
                base = self.cm.stats(c.code) if hasattr(self.cm, "stats") else None
                want = None if base and tuple(base) == (atk, df) else (atk, df)
                if want is None and self.links and self.cfg.get("card_link") \
                        and any(lk[1] == uid for lk in self.links):
                    want = (atk, df)    # con vínculo el cliente sumaría su propio bono: fijar SIEMPRE (card_link)
                prev = self.stat_sent.get(uid)
                if want == prev:
                    continue
                if prev is not None:
                    out.append(engine(C_EFFECT_DEL, p, i, EFFECT_SET_STATS, 1))
                if want is not None:
                    out.append(engine(C_EFFECT_ADD, p, i | 0x300, EFFECT_SET_STATS, min(atk, 0xffff)))
                    out.append(engine(C_EFFECT_ADD, p, i | 0x300, EFFECT_SET_STATS, min(df, 0xffff)))
                self.stat_sent[uid] = want
        return [(0x6202, m) for m in out]

    # ===================================================================================== decisiones ===========
    def _loc_yo2(self, d):
        """{controller, location, sequence} -> (jugador, zona YO2, índice YO2) o None."""
        p, where, i = self.ocg_slot(d["controller"], d["location"], d["sequence"])
        if where is None:
            return None
        z, idx = self.yo2_zone(where, i)
        return p, z, idx

    def _request(self, entries, tail_buttons=(), kind=3, clear=1):
        """entries: [(jugador, zona, índice, bit)] -> 0x6611 + 0x6612 + 0x6202 tipo 0x1a (P1 = clear: 0 no vacía la
        lista B; es lo que mandaba el oficial en las peticiones del ritual, §33)."""
        masks, hand = {}, {}
        for p, z, idx, bit in entries:
            if z == ZONE_HAND:
                hand[(p, idx)] = hand.get((p, idx), 0) | (1 << bit)
            else:
                masks[(p, z)] = masks.get((p, z), 0) | (1 << bit)
        if self.cfg.get("pile_look"):
            for p in (0, 1):
                s = self.side[p]
                for name in ("grave", "banish") + (("extra",) if p == 0 else ()):
                    if s.pile(name):
                        z = PILE_ZONE[name]
                        masks[(p, z)] = masks.get((p, z), 0) | (1 << CMD_LOOK)
        tail = {(0, b): 1 for b in tail_buttons}
        hand_list = [(p, i, m) for (p, i), m in sorted(hand.items())]
        extra = {(p, z): REASON_NONE for p in (0, 1) for z in range(16) if z != ZONE_HAND}
        lists = tuple((p, (REASON_NONE,) * len(self.side[p].hand)) for p in (0, 1))
        return self._timer_msg() + [(0x6611, comid_field(masks, extra=extra, tail=tail)),
                                    (0x6612, comid_hand(hand_list, lists=lists)), (0x6202, request_input(kind, clear))]

    def _on_select(self, ev):
        if ev.get("player", 0) != 0:
            return []
        # B45: RETRY = la respuesta anterior no era legal. Antes no había tope: si la respuesta la generaba el propio
        # servidor (SELECT_PLACE/POSITION automáticas…) se repetía la misma sin fin y el duelo se quedaba girando. Con
        # retry_auto = N (> 0): a partir del RETRY N-ésimo seguido decide bot.ts ({"cmd": "auto"}); y una respuesta
        # automática del servidor que ya falló una vez no se repite (auto_response). 0 = como antes (sin tope).
        fresh = ev is not self.waiting           # _resend() vuelve a pasar el MISMO evento: no cuenta como RETRY
        if fresh:
            self.retry_n = self.retry_n + 1 if ev.get("retry") else 0
            if ev.get("retry"):
                self._warn(f"RETRY de {ev.get('type')}: la respuesta anterior no era legal (seguidos: {self.retry_n})")
        self.waiting = ev
        t = ev["type"]
        cap = self.cfg.get("retry_auto") or 0
        if fresh and cap and self.retry_n >= cap:
            self._warn(f"RETRY n.º {self.retry_n} de {t}: decide bot.ts")
            self.state = {"mode": "auto"}
            return []
        fn = getattr(self, "_s_" + t, None)
        if fn is None:
            self.state = {"mode": "auto"}
            return []
        pre = []
        if self.query is not None and not self._syncing:
            try:
                pre = self.sync_board(self.query(), resend=False)
            except Exception as e:                               # noqa: BLE001
                self._warn(f"query falló: {e!r}")
        return pre + fn(ev)

    def needs_auto(self):
        """True si la decisión pendiente no se pregunta al cliente: run.py debe mandar auto_response()."""
        return self.waiting is not None and (self.state or {}).get("mode") == "auto"

    def auto_response(self):
        """Respuesta automática para la decisión pendiente (o {"cmd":"auto"} para que decida bot.ts)."""
        ev = self.waiting
        if ev is None:
            return None
        t = ev["type"]
        self.waiting = None
        if self.retry_n and (self.cfg.get("retry_auto") or 0):
            return {"cmd": "auto"}                     # B45: la automática del servidor ya falló: que decida bot.ts
        if t in ("SELECT_PLACE", "SELECT_DISFIELD"):
            return {"cmd": "respond", "response": _respond(t, places=_free_places(ev["field_mask"], ev.get("count", 1)))}
        if t == "SELECT_POSITION":
            m = ev["positions"]
            pos = next(b for b in (POS_FU_ATK, POS_FU_DEF, POS_FD_DEF, POS_FD_ATK) if m & b) if m else POS_FU_ATK
            return {"cmd": "respond", "response": _respond(t, position=pos)}
        if (t == "SELECT_CHAIN" and ev.get("forced") and self.cfg.get("forced_auto_single")
                and len(ev.get("selects", [])) == 1):
            return {"cmd": "respond", "response": _respond(t, index=0)}       # obligatoria y única
        if t == "SELECT_CHAIN" and not ev.get("forced") and self.cfg.get("chain_dialog") == "pass":
            return {"cmd": "respond", "response": _respond(t, index=None)}   # «pass»: no encadenar nunca
        if t == "SELECT_CHAIN" and not ev.get("forced") and not ev.get("selects"):
            return {"cmd": "respond", "response": _respond(t, index=None)}   # §30: ventana vacía (auto_chain=False)
        if t == "SELECT_OPTION" and len(ev.get("options", [])) == 1:
            return {"cmd": "respond", "response": _respond(t, index=0)}      # B42: una sola opción, nada que elegir
        st = self.state or {}
        if t == "SELECT_COUNTER" and st.get("counters") is not None:
            return {"cmd": "respond", "response": _respond(t, counters=list(st["counters"]))}   # B42: reparto obligado
        return {"cmd": "auto"}

    # --- SELECT_IDLECMD / SELECT_BATTLECMD -------------------------------------------------------------------------
    IDLE_LISTS = (("summons", "SELECT_SUMMON", CMD_SUMMON), ("special_summons", "SELECT_SPECIAL_SUMMON", CMD_SPSUMMON),
                  ("pos_changes", "SELECT_POS_CHANGE", None), ("monster_sets", "SELECT_MONSTER_SET", CMD_SET),
                  ("spell_sets", "SELECT_SPELL_SET", CMD_SET_ST), ("activates", "SELECT_ACTIVATE", CMD_ACTIVATE))

    def _s_SELECT_IDLECMD(self, ev):
        options = []                                   # (jugador, zona, índice, bit, acción, i)
        for key, action, bit in self.IDLE_LISTS:
            for i, d in enumerate(ev.get(key, [])):
                l3 = self._loc_yo2(d)
                if not l3:
                    continue
                b = bit
                if b is None:                          # cambio de posición: según cómo está ahora
                    c = self.cards.get(self.uid_at(d["controller"], d["location"], d["sequence"]))
                    b = CMD_FLIP if c and not c.face else CMD_TO_DEF if c and not c.defense else CMD_TO_ATK
                options.append((*l3, b, action, i))
        buttons = [PH_BATTLE] * bool(ev.get("to_bp")) + [PH_END] * bool(ev.get("to_ep"))
        self.state = {"mode": "idle", "options": options, "buttons": {PH_BATTLE: "TO_BP", PH_END: "TO_EP"}}
        self.state["buttons"] = {b: a for b, a in self.state["buttons"].items() if b in buttons}
        return self._request([o[:4] for o in options], buttons, self.cfg["kind_main"])

    def _s_SELECT_BATTLECMD(self, ev):
        options = []
        for i, d in enumerate(ev.get("attacks", [])):
            l3 = self._loc_yo2(d)
            if l3:
                options.append((*l3, CMD_ATTACK, "SELECT_BATTLE", i))
        for i, d in enumerate(ev.get("chains", [])):
            l3 = self._loc_yo2(d)
            if l3:
                options.append((*l3, CMD_ACTIVATE, "SELECT_CHAIN", i))
        buttons = {}
        if ev.get("to_m2"):
            buttons[PH_MAIN2] = "TO_M2"
        if ev.get("to_ep"):
            buttons[PH_END] = "TO_EP"
        self.state = {"mode": "battle", "options": options, "buttons": buttons}
        return self._request([o[:4] for o in options], list(buttons), self.cfg["kind_battle"])

    # --- SELECT_CHAIN ----------------------------------------------------------------------------------------------
    def _s_SELECT_CHAIN(self, ev):
        options = []
        for i, d in enumerate(ev.get("selects", [])):
            l3 = self._loc_yo2(d)
            if l3:
                options.append((*l3, CMD_ACTIVATE, None, i))
        if not options and not ev.get("forced"):
            if not ev.get("selects") and self.cfg.get("chain_dialog") == 3 and \
                    self.cfg.get("chain_none_prompt", DEFAULT_CFG.get("chain_none_prompt")):
                return self._chain_none()                    # §30: «No hay carta que pueda activarse…»
            self.state = {"mode": "auto"}
            return []
        if ev.get("forced") and self.cfg.get("forced_auto_single") and len(ev.get("selects", [])) == 1:
            # cadena OBLIGATORIA con una sola opción: la única respuesta legal es el índice 0 (no hay decisión que
            # preguntar). auto_response() la manda sin pasar por el cliente; el cliente ve CHAIN_SET/RUN igual
            self.state = {"mode": "auto"}
            return []
        self.state = {"mode": "chain", "options": options, "forced": ev.get("forced")}
        how = self.cfg.get("chain_dialog")
        if ev.get("forced") or not options or not how:
            return self._request([o[:4] for o in options], (), self.cfg["kind_select"])
        if how == "pass":
            self.state = {"mode": "auto"}                        # auto_response -> bot.ts (no lo pregunta)
            return []
        # Ventana de cadena OPCIONAL: primero el SÍ/NO (lista A [(dlg 269, 0)] + DUEL_VIEW 0x32 tipo 3 o 2). Antes
        # solo iban las máscaras y, en el turno de la NPC, el duelo esperaba sin avisar a que pulsara su carta.
        self.state["mode"] = "chain_ask"
        p2 = self.cfg.get("chain_importance", 2) if how == 3 else 0
        if how == 3 and self.cfg.get("chain_priority"):
            p2 = self._chain_priority(ev, self.cfg["chain_priority"])
        elif how == 3 and self.cfg.get("chain_importance_auto"):
            # regla PROPIA (la del oficial es del servidor y no se conoce, D): 2 = pregunta siempre si la ventana
            # responde al RIVAL (último CHAINING/invocación/ataque suyo); 1 = el cliente pregunta solo en «Manual»
            p2 = 2 if self.trigger == 1 else 1
        pairs = [(DLG_CHAIN, 2)]
        sel = ev.get("selects", [])
        # §24: UNA sola carta activable -> «¿Activar el efecto de "X"?» (dlg 250/328/329) con su miniatura (una vez
        # aunque ofrezca dos efectos). Un duelo de antes de la carga en caliente no lleva la clave: DEFAULT_CFG
        one = self._chain_one_pairs(sel) if self.cfg.get("chain_one_name", DEFAULT_CFG.get("chain_one_name")) else None
        # §28: texto del suceso + la pregunta, como el oficial (solo sin cadena abierta). Gana al nombre de §24: «suceso
        # + ¿Activar el efecto de "X"?» no cabe (ver DEFAULT_CFG). `one` sigue sirviendo para la miniatura única
        text = self.cfg.get("chain_event_text", DEFAULT_CFG.get("chain_event_text"))
        evt = self._chain_event(ev)[0] if text and not self.chain else None
        if evt:
            pairs = [(evt, 2), (CHAIN_SEP, 3), (DLG_CHAIN, 3)]
        elif one:
            pairs = one
        elif self.cfg.get("chain_card_name") and sel:
            # solo la 1.ª carta: una 2.ª clase 4 iría a un búfer que ya no se copia (con texto escrito, FUN_00744670
            # solo copia la salida). Con varias, el nombre es el de la primera y las máscaras dicen el resto
            d = sel[0]
            uid = self.uid_at(d["controller"], d["location"], d["sequence"])
            code = d.get("code") or (self.cards[uid].code if uid is not None else None)   # ocgcore manda el código
            internal = self.cm.yo2_to_internal(self._yo2(code)) if code else None
            if internal:
                pairs = [(internal, 4), (0xffff, 3), (DLG_CHAIN, 3)]
        link = self._chain_link_pairs() if self.cfg.get("chain_link_text") else None
        detail = []
        if link:
            # §28: el oficial manda 3 parejas, [(id, 4), (7|8, 0), (id, 0)]: la clase 0 salta la pareja siguiente (inc esi
            # en 0x744795) y con la clase 4 delante usa el nombre, no su valor: la 3.ª es inocua. C
            pairs = link + [(link[0][0], 0)] if text else link
            one = None
            # §26: el panel de detalle enseña la carta X del texto (con el diálogo abierto no hay hover). Un duelo de
            # antes de la carga en caliente no lleva la clave: DEFAULT_CFG
            if self.cfg.get("chain_link_detail", DEFAULT_CFG.get("chain_link_detail")):
                detail = self._chain_link_detail()
        # TODAS las parejas en UN 0x61 (P0..P7, hasta 4). El ejecutor (0x721920, desensamblado) añade (P0,P1), (P2,P3)…
        # de ESE comando hasta llenar el tamaño de 0x60: con 3 comandos de una pareja, el 1.º metía (id,4),(0,0),(0,0)
        # y los demás no cabían -> (0, clase 0) = plantilla con dlg 0 «Para Invocar… 1 Sacrificio» (vivo 06-10).  C
        flat = [v for pr in pairs for v in pr]
        out = [setcommand(T_LIST_RESET, 0, (len(pairs),)), setcommand(T_LIST_ADD, 0, tuple(flat))]
        out.append(setcommand(T_VIEW, 0x32, (3 if how == 3 else 2, 0, p2)))
        pic = sel[:1] if one else sel
        return self._dialog_cards([(d.get("code"), d.get("controller", 0)) for d in pic]) + detail + \
            [(0x6202, m) for m in out]

    def _chain_none(self):
        """§30 (chain_none_prompt): aviso oficial de ventana de cadena SIN nada activable. Lista A [(204 sin cadena
        abierta | 205 con eslabones, 2), (0xffff, 3), (206 «¿Revisarás el Campo?», 3)] + DUEL_VIEW 0x32 tipo 3 con P2 0
        (C: los 14.063 avisos oficiales). El cliente (duel_view_dispatch 0x73dd80, caso 3) contesta solo 0x6423 0 si
        P2 < 2 y su opción no es «Manual» (C); el modo "chain_none" espera ese 0x6423 y con SÍ o NO no encadena."""
        self.state = {"mode": "chain_none"}
        pairs = [(CHAIN_NONE[1] if self.chain else CHAIN_NONE[0], 2), (CHAIN_SEP, 3), (CHAIN_NONE[2], 3)]
        out = [setcommand(T_LIST_RESET, 0, (len(pairs),)), setcommand(T_LIST_ADD, 0, tuple(v for pr in pairs for v in pr)),
               setcommand(T_VIEW, 0x32, (3, 0, CHAIN_NONE_P2))]
        # sin cartas: con yesno_card, 0x6610 modo 0 n = 0 para no heredar el 0xff de un aviso anterior (§29)
        return self._dialog_cards([]) + [(0x6202, m) for m in out]

    def _chain_link_detail(self):
        """§26 (chain_link_detail): [0x6202 tipo 0x1b B=0x37 RUN_DETAIL (P0 = uid)] con la carta del último eslabón si
        sigue en el campo, boca arriba y con ese código; si no, []. Desde la mano/cementerio/desterradas el 0x2f de
        _m_CHAINING ya dejó la carta en el panel (vista 0x21, C)."""
        if not self.chain or not self.chain_codes:
            return []
        p, zone = self.chain[-1]
        if not 0 <= zone <= 10:
            return []
        uid = self.side[p].mz[zone] if zone < 5 else self.side[p].sz[zone - 5 if zone < 10 else 5]
        c = self.cards.get(uid) if uid is not None else None
        if c is None or not c.face or c.code != self.chain_codes[-1] or not self._yo2(c.code):
            return []
        return [(0x6202, setcommand(T_VIEW, T_VIEW_DETAIL, (uid, 0, 0)))]

    def _chain_one_pairs(self, sel):
        """§24 (chain_one_name): lista A del aviso de cadena cuando las candidatas son UNA sola carta (mismo sitio,
        aunque traiga varios efectos): [(id interno, 4), (dlg, 0)] con dlg 250 «¿Activar el efecto de "%s"?», 328 si
        está en la mano o 329 en el cementerio (el formato del EFFECTYN de §23, que ya va en vivo). None con varias
        cartas o si la carta no tiene id YO2 (-> el genérico 269)."""
        if not sel or len({(d.get("controller", 0), d.get("location"), d.get("sequence")) for d in sel}) != 1:
            return None
        d = sel[0]
        code = d.get("code")
        if not code:
            uid = self.uid_at(d.get("controller", 0), d.get("location"), d.get("sequence"))
            code = self.cards[uid].code if uid is not None else None
        y = self.cm.code_to_yo2(code) if code else None
        internal = self.cm.yo2_to_internal(y) if y else 0
        if not internal:
            return None
        return [(internal, 4),
                ({OCG_HAND: DLG_EFFECT_YN_HAND, OCG_GRAVE: DLG_EFFECT_YN_GRAVE}.get(d.get("location"), DLG_EFFECT_YN), 0)]

    def _dialog_cards(self, cards):
        """§23: lista 0x6610 modo 0xff con las cartas [(código, jugador)] que pinta el CUYesNoDialog (antes que su
        0x32). Sin código o con yesno_card apagado: nada (un duelo de antes de la carga en caliente mira
        DEFAULT_CFG)."""
        if not self.cfg.get("yesno_card", DEFAULT_CFG.get("yesno_card")):
            return []
        items = [struct.pack(">4H", self._yo2(c) & 0x3fff, 0, p & 1, 0) for c, p in cards if c][:5]
        if not items or self.list_dirty:
            # CIERRE_MINIATURA: lista de modo 0 con n = 0: el CUYesNoDialog no pinta nada (ni hereda el 0xff de un aviso
            # anterior, que no se borra al contestar) y no lee los ids internos que un 0x4b/0x4c aún en cola deja en
            # DAT_012ccb34
            return [(M_LIST, struct.pack(">BIIII", 0, 0, 0, 0, 0))]
        return [(M_LIST, struct.pack(">BIIII", 0, LIST_DIALOG, 0, len(items), 0) + b"".join(items))]

    def _chain_priority(self, ev, mode):
        """P2 del aviso de cadena según chain_priority (regla NUESTRA, D; ver DEFAULT_CFG y NOTES)."""
        me = ev.get("player", 0)
        last = self.chain[-1][0] if self.chain else None             # quién puso el último eslabón (None = sin cadena)
        # §21: monstruos de la mano (d/e). Un duelo de antes de la carga en caliente no lleva la clave: DEFAULT_CFG
        hand = self.cfg.get("chain_priority_hand", DEFAULT_CFG.get("chain_priority_hand")) \
            and self._hand_priority(ev, last == me)
        if mode == "oficial":
            # §28: con cadena abierta, 2 si el último eslabón es del rival y 1 si es propio (oficial: 627 / 140); sin
            # ella, la P2 de su suceso (CHAIN_EVENTS) y 1 si no hay ninguno conocido
            if last is not None:
                return 2 if last != me or hand else 1
            # sin cadena: la de su suceso, salvo un disparado opcional de un monstruo de la mano (Gorz tras un daño: con
            # P2 1 el cliente en «automática» lo cancelaba solo y se perdía; §21, chain_priority_hand). Regla NUESTRA (D)
            if self.cfg.get("chain_priority_hand", DEFAULT_CFG.get("chain_priority_hand")) and self._hand_priority(ev, True):
                return 2
            p2 = self._chain_event(ev)[1]
            # 09-10: el oficial varía la P2 de un mismo suceso (253, 257…: D); con la mayoritaria (1) el cliente en
            # «semi» cancelaba solo el Agujero Trampa Sin Fondo tras la invocación del rival. Si alguna opción es un
            # efecto CON EVENTO (la regla (b) de "evento": responde a lo que acaba de pasar) va con 2.
            # chain_event_p2=False deja la tabla pura.
            if p2 < 2 and self.cfg.get("chain_event_p2", DEFAULT_CFG.get("chain_event_p2", True)):
                for d in ev.get("selects", []):
                    code, effs = self._opt_effects(d)
                    if effs and all(e["code"] not in (None, EVENT_FREE_CHAIN) for e in effs):
                        return 2
            return p2
        if mode == "spe":
            return 2 if (ev.get("spe_count", 0) > 0 and last != me) or hand else 1
        if last is not None:
            # (a) cadena abierta: responde a una activación del rival -> 2; encadenar a la PROPIA -> 1 (Magic Jammer
            # sale también sobre tus Mágicas: ocgcore lo ofrece porque la regla lo permite, pero no es «responder»)
            # salvo (e): un disparado de un monstruo de la mano que sigue pendiente (Gorz tras tu D.D. Crow)
            return 2 if last != me or hand else 1
        if hand:
            return 2                                                 # (d) efecto de batalla desde la mano
        attack = ev.get("hint_timing_other", 0) & TIMING_ATTACK      # el rival acaba de declarar un ataque
        for d in ev.get("selects", []):
            code, effs = self._opt_effects(d)
            if effs and all(e["code"] not in (None, EVENT_FREE_CHAIN) for e in effs):
                return 2                                             # (b) efecto con evento: el suceso acaba de pasar
            if attack and any(e["hint"][1] & TIMING_ATTACK for e in effs):
                return 2                                             # (c) libre pensada para responder al ataque
        return 1

    def _chain_event(self, ev):
        """§28 (chain_event_text / chain_priority="oficial"): suceso al que responde la ventana de cadena SIN cadena
        abierta -> (dlg del texto del suceso o None, P2 del oficial). Sale de los TIMING_* del SELECT_CHAIN (hint_timing =
        los que causó quien elige, hint_timing_other = los del rival; CHAIN_EVENTS por prioridad) y, para las
        invocaciones, de si ocgcore acaba de anunciarla (Juicio Solemne) o de cerrarla (CHAIN_SUMMONS, por ev_last). Sin
        TIMING_* (pruebas), de lo último que rastreó _track_trigger. Ninguno encaja -> (None, 1): el 269 solo, como el
        oficial."""
        me = ev.get("player", 0)
        mine, opp = ev.get("hint_timing") or 0, ev.get("hint_timing_other") or 0
        last = self.ev_last
        # 09-10: en el paso de daño ocgcore abre DOS ventanas: al empezar (sin TIMING_*: 177 «Se entró en el Damage
        # Step») y, tras el HINT 41, otra con TIMING_DAMAGE_STEP que es la de ANTES DEL CÁLCULO: la segunda salía
        # también con 177 (el owner vio el mismo aviso dos veces). El oficial manda 177 y luego 266, los dos con P2 2.
        if (mine | opp) & 0x2000 and not (mine | opp) & 0x4000 and last and last[0] == "DAMAGE_STEP_START":
            return 266, 2
        if not (mine or opp) and last:
            t, p, ph = last
            bit = {"ATTACK": 0x1000, "DAMAGE_STEP_START": 0x2000, "DRAW": 0x10000}.get(t, 0)
            if t == "NEW_PHASE":
                bit, p = {1: 0x1, 2: 0x2, 0x200: 0x20}.get(ph, 0), self.turn_player
            elif t == "DAMAGE_STEP_START":
                p = self.turn_player
            elif t in CHAIN_SUMMONS:
                bit = CHAIN_SUMMONS[t][0]
            if p == me:
                mine = bit
            else:
                opp = bit
        both = mine | opp
        if last and last[0] in CHAIN_SUMMONS:
            bit, dm, do, pm, po = CHAIN_SUMMONS[last[0]]
            if both & bit or not both:
                own = last[1] == me
                return (dm if own else do), (pm if own else po)
        for bit, dm, do, pm, po in CHAIN_EVENTS:
            if both & bit:
                own = bool(mine & bit)
                return ((dm if own else do) or None), (pm if own else po)
        if both & 0x20000 and self.dmg_player is not None:           # TIMING_DAMAGE: quién cobró
            return CHAIN_DAMAGE[0 if self.dmg_player == me else 1], CHAIN_DAMAGE[2]
        return None, 1

    def _opt_effects(self, d):
        """Opción de SELECT_CHAIN -> (passcode, efectos encadenables de su script; solo el de la opción si su
        description = aux.Stringid lo dice)."""
        code = d.get("code")
        if not code:
            uid = self.uid_at(d["controller"], d["location"], d["sequence"])
            code = self.cards[uid].code if uid is not None else None
        info = script_info(code) if code else None
        alias = self.cm.cdb_alias.get(code) if code else None
        if info is None and alias:
            info = script_info(alias)                                # arte alternativo sin script propio
        effs = (info or {}).get("effects", [])
        desc = d.get("description") or 0
        # aux.Stringid: code << 20 | n en este ocgcore (las trazas); code * 16 + n en el formato viejo
        n = desc & 0xfffff if desc >> 20 and desc >> 20 in (code, alias) else \
            desc & 0xf if desc >> 4 and desc >> 4 in (code, alias) else None
        if n is not None:
            effs = [e for e in effs if e["desc"] == n] or effs
        return code, effs

    def _hand_priority(self, ev, own_last):
        """§21 (d)/(e): ¿alguna opción es el efecto de un MONSTRUO desde la mano que el cliente no debe cancelar solo?
        (d) rápido libre con momento de batalla en su HintTiming, durante un ataque (Honest, Cyber Ogre);
        (e) con el último eslabón propio, un disparado opcional (Gorz sigue pendiente tras tu D.D. Crow)."""
        battle = self.in_battle or self.in_damage or \
            (ev.get("hint_timing", 0) | ev.get("hint_timing_other", 0)) & TIMINGS_BATTLE
        for d in ev.get("selects", []):
            if d.get("location") != LOCATION_HAND:
                continue
            code, effs = self._opt_effects(d)
            if not code or self.cm.kind(code) != "monster":
                continue
            for e in effs:
                if battle and e["code"] == EVENT_FREE_CHAIN and (e["hint"][0] | e["hint"][1]) & TIMINGS_BATTLE:
                    return True
                if own_last and (e["type"] or 0) & _ET_TRIGGER_O:
                    return True
        return False

    def _chain_link_pairs(self):
        """Lista A del aviso «X está activado. ¿Encadenar?» (DLG 7 monstruo / 8 Mágica-Trampa) con X = último eslabón,
        o None si no hay cadena abierta o la carta no tiene id YO2."""
        codes = tuple(self.chain_codes)
        if not self.chain or len(codes) != len(self.chain) or not codes[-1]:
            return None
        y = self.cm.code_to_yo2(codes[-1])
        internal = self.cm.yo2_to_internal(y) if y else 0
        if not internal:
            return None
        return [(internal, 4), (DLG_LINK_EFFECT if self.cm.kind(codes[-1]) == "monster" else DLG_LINK_CARD, 0)]

    # --- SÍ/NO -----------------------------------------------------------------------------------------------------
    def _yesno(self, ev):
        if self.cfg["yesno"] != "dialog":
            self.state = {"mode": "auto"}
            return []
        self.state = {"mode": "yesno"}
        # lista A: 1 pareja (texto, clase 0 = dlg_text). Antes iba (id interno, clase 4 = nombre de carta), clase sin
        # confirmar (audit duelo_menus §5/§9); dlg 269 «¿activar el efecto de la carta?» vale para EFFECTYN.  C
        # 2026-10-06: la clase 0 tumba el cliente (FUN_00744670 -> FUN_007437d0, access violation). Se vuelve a la
        # lista que ya se veía sin petar: (id interno de la carta, clase 4) o lista vacía.
        code = ev.get("code")
        # SELECT_YESNO (Duel.SelectYesNo del script: «¿destruir 1 carta?» de Ryko al voltearse...) no trae code, solo
        # description = aux.Stringid(passcode, n). Sin esto la lista iba vacía y el cliente pintaba basura (06-10).
        desc = ev.get("description") or 0
        texts = self.cfg.get("yesno_texts", DEFAULT_CFG.get("yesno_texts"))   # §41 (duelo de antes de la carga: DEFAULT)
        card = self.cfg.get("yesno_card", DEFAULT_CFG.get("yesno_card"))     # §23 (duelo de antes de la carga: DEFAULT)
        spec = None
        if texts:
            # §41: la description de ocgcore es aux.Stringid(código, n) = código << 20 | n. Solo las cartas de YESNO_DLG
            # tienen el texto de SU pregunta; las demás siguen con el genérico (269) como hasta ahora
            dcode, dn = _desc_card(desc)
            if ev.get("type") == "SELECT_EFFECTYN" and code and desc in YESNO_SYS_CARD:
                spec = (YESNO_SYS_CARD[desc], "self", None)           # «¿Destruir otra carta en lugar de "%s"?» (C)
            elif not code and dcode:
                spec = YESNO_DLG.get((dcode, dn))
                if spec and self.cm.code_to_yo2(dcode):
                    code = dcode
        elif not code and desc >= 1 << 20 and self.cm.code_to_yo2(desc >> 4):
            code = desc >> 4                                          # yesno_texts apagado: la lectura de antes (código << 4)
        y = self.cm.code_to_yo2(code) if code else None          # sin pareja YO2 -> texto genérico, no la tapadera
        if not (y and self.cm.yo2_to_internal(y)):
            code = None
        pairs = self._yesno_pairs(spec, code) if spec else None
        if pairs:
            # §41: el texto de ESA carta; el nombre (clase 4) y el número (clase 1) como en el oficial. La miniatura
            # de la carta de la description va igual que en las demás (yesno_card)
            out = [setcommand(T_LIST_RESET, 0, (len(pairs),))]
            flat = [v for pr in pairs for v in pr]
            for k in range(0, len(flat), 8):
                out.append(setcommand(T_LIST_ADD, 0, tuple(flat[k:k + 8])))
        elif not code:
            # §25: texto del sistema de ocgcore con pareja en el cliente (30 -> 223 «Reproducir batalla. ¿Continuar
            # para atacar?»…); si no, el genérico. Un duelo de antes de la carga en caliente no lleva la clave: DEFAULT
            text = DLG_CHAIN
            if self.cfg.get("yesno_sys_text", DEFAULT_CFG.get("yesno_sys_text")):
                text = YESNO_SYS_DLG.get(desc, DLG_CHAIN)
            out = [setcommand(T_LIST_RESET, 0, (1,)), setcommand(T_LIST_ADD, 0, (text, 2))]
        elif card and ev.get("type") == "SELECT_EFFECTYN":
            # §23: «¿Activar el efecto de "X"?» (mano / cementerio con su texto), clase 4 + plantilla clase 0 en UN 0x61
            dlg = {OCG_HAND: DLG_EFFECT_YN_HAND, OCG_GRAVE: DLG_EFFECT_YN_GRAVE}.get(ev.get("location"), DLG_EFFECT_YN)
            out = [setcommand(T_LIST_RESET, 0, (2,)),
                   setcommand(T_LIST_ADD, 0, (self.cm.yo2_to_internal(self._yo2(code)), 4, dlg, 0))]
        else:
            out = [setcommand(T_LIST_RESET, 0, (1,)),
                   setcommand(T_LIST_ADD, 0, (self.cm.yo2_to_internal(self._yo2(code)), 4))]
        out.append(setcommand(T_VIEW, 0x32, (2, 0, 0)))          # DUEL_VIEW 0x32 tipo 2 = CUYesNoDialog -> 0x6423 1/0
        pic = self._dialog_cards([(code, ev.get("controller", ev.get("player", 0)))] if code else [])
        return pic + [(0x6202, m) for m in out]

    _s_SELECT_EFFECTYN = _s_SELECT_YESNO = _yesno

    def _yesno_pairs(self, spec, own_code):
        """§41: (dlg, carta, número) de YESNO_DLG -> pares de la lista A [(texto, clase)…] como el oficial, o None si falta
        la carta del nombre (el %s quedaría sin rellenar: mejor el genérico)."""
        dlg, who, num = spec
        if who is None and num is None:
            return [(dlg, 2)]
        pairs = []
        if who is not None:
            code = own_code if who == "self" else who
            y = self.cm.code_to_yo2(code) if code else None
            internal = self.cm.yo2_to_internal(y) if y else 0
            if not internal:
                return None
            pairs.append((internal, 4))
        pairs.append((dlg, 0))
        if num is not None:
            pairs.append((int(num), 1))
        return pairs

    # --- elegir cartas ---------------------------------------------------------------------------------------------
    def _pick(self, ev, cands_key, mode):
        cands = []
        opp_hand = mode == "card" and self.cfg.get("opp_hand_pick", DEFAULT_CFG.get("opp_hand_pick"))   # §40
        for i, d in enumerate(ev.get(cands_key, [])):
            l3 = self._loc_yo2(d)
            if l3 is None or (l3[1] == ZONE_HAND and l3[0] != 0 and not opp_hand):
                self.state = {"mode": "auto"}                    # mano del rival / sitio raro: decide el bot
                return []
            cands.append((*l3, i))
        if mode == "card" and self.cfg.get("list_picks") and any(c[1] in LIST_ZONES for c in cands):
            return self._list_pick(ev, cands)
        # Objetivo de ataque: el SELECT_CARD que sigue a «Atacar» (self.attacking), no solo «estamos en fase 3» (si la
        # fase del espejo no era 3, salía el bit 11 y duelfin lo tomaba por rendición: diálogo y bucle).
        target = (self.attacking or self.phase == 3) and all(c[0] == 1 and c[1] < 5 for c in cands)
        att = self.attacker
        aim = bool(target and self.attacking and self.cfg.get("attack_aim") and att and 0 <= att[1] < 5)
        self.attacking = False
        bit = self.cfg["target_bit"] if target else self.cfg["select_bit"]
        if aim:
            bit = self.cfg.get("aim_target_bit", bit)
        self.state = {"mode": mode, "cands": cands, "chosen": [], "bit": bit, "aim": aim,
                      "min": ev.get("min", 1), "max": ev.get("max", 1), "can_cancel": ev.get("can_cancel")}
        if not aim and not target and self._ritual_monster_pick(ev, cands_key, mode, cands):
            # §33: el monstruo de ritual (HINT 509 + solo monstruos de ritual de la mano propia), como el oficial:
            # lista A [(286, 2)] + 0x1b B=0x32 (0xff, 286, 1) + petición de clase 8 sin vaciar la lista B  C
            self.state.update(kind=KIND_RITUAL, clear=0)
            return [(0x6202, setcommand(T_LIST_RESET, 0, (1,))),
                    (0x6202, setcommand(T_LIST_ADD, 0, (DLG_RITUAL_MONSTER, 2))),
                    (0x6202, setcommand(T_VIEW, 0x32, (0xff, DLG_RITUAL_MONSTER, 1)))] + self._pick_request()
        if not aim and cands and all(c[0] == 1 and c[1] == ZONE_HAND for c in cands) and opp_hand:
            # §40: elegir en la mano del rival (Dustshoot…). Aviso 244 + clase 8 como el oficial; la identidad de la
            # mano ya la mandó CONFIRM_CARDS (si el script la enseñó). Con varias a elegir (max > 1) sin aviso: el 244
            # dice «1 carta»
            self.state.update(kind=KIND_OPP_HAND, clear=1)
            panel = []
            if self.state["max"] == 1:
                panel = [(0x6202, setcommand(T_LIST_RESET, 0, (1,))),
                         (0x6202, setcommand(T_LIST_ADD, 0, (DLG_PICK_OPP_HAND, 2))),
                         (0x6202, setcommand(T_VIEW, 0x32, (0xff, DLG_PICK_OPP_HAND, 1)))]
            return panel + self._pick_request()
        if not aim:
            panel = []
            if self.cfg.get("pick_panel") and not target and mode in ("card", "tribute"):
                # aviso «Selecciona…»: lista A [(dlg, 2)] (clase 2 sola = strcpy del texto, FUN_00744670) + 0x1b B=0x32
                # P0=0xff (ejecutor: copia A -> B y DUEL_VIEW 0x32 0xff -> caso por defecto -> CULockOnDialog,
                # FUN_0077e3a0, hijo de DAT_012a9070+0x78, no de +0x108: no es modal para FUN_00743130(0x32))  P
                dlg = DLG_PICK_TRIBUTE if mode == "tribute" else DLG_PICK_FIELD
                panel = [(0x6202, setcommand(T_LIST_RESET, 0, (1,))), (0x6202, setcommand(T_LIST_ADD, 0, (dlg, 2))),
                         (0x6202, setcommand(T_VIEW, 0x32, (0xff, 0, 0)))]
            return panel + self._pick_request()
        # espada de apuntar: fijar el atacante (DUEL_VIEW 0xc, P2 = -1: sin flecha) y pasar al estado 3 (DUEL_VIEW 0xb).
        # Al elegir, 0x19 ATTACK -> DUEL_VIEW 0xc con objetivo -> estado 4 (la flecha de siempre).  C
        return [(0x6202, setcommand(T_VIEW, VIEW_ATTACKER, ((att[0] & 1) | (att[1] & 0xff) << 8, 1, 0xffffffff))),
                (0x6202, setcommand(T_VIEW, VIEW_BATTLE_SELECT, ()))] + self._pick_request()

    def _pick_request(self):
        st = self.state
        left = [c for c in st["cands"] if c[3] not in st["chosen"]]
        # §20: con el panel de descarte, su cuenta (cartas que faltan) va delante de cada petición
        pre = [self._discard_var(st["discard"] - len(st["chosen"]))] if st.get("discard") else []
        return pre + self._request([(p, z, idx, st["bit"]) for p, z, idx, _ in left], (),
                                   st.get("kind", self.cfg["kind_select"]), st.get("clear", 1))

    def _ritual_monster_pick(self, ev, cands_key, mode, cands):
        """§33: ¿es el SELECT_CARD del monstruo de ritual? (HINT 509 delante, candidatas = monstruos de ritual en la
        mano del jugador 0)."""
        if mode != "card" or not cands or not self.cfg.get("ritual_sum", DEFAULT_CFG.get("ritual_sum")):
            return False
        if self._last_hint3() != HINTMSG_SPSUMMON or any(c[0] != 0 or c[1] != ZONE_HAND for c in cands):
            return False
        return all(self.cm.cdb_type.get(d.get("code"), 0) & 0x81 == 0x81 for d in ev.get(cands_key, []))

    # --- §20: descarte por límite de mano (CUDiscardDialog) ------------------------------------------------------------
    def _discard_var(self, n):
        """0x6202 tipo 0x1e: DAT_012cc3aa = n = cartas que le quedan por descartar (0 = el panel se cierra)."""
        self.discard_open = n > 0
        return (0x6202, setcommand(T_SETVAR16, 0, (VAR_DISCARD, max(0, n))))

    def _last_hint3(self):
        """HINT tipo 3 (HINT_SELECTMSG) que va justo delante de la decisión en curso, en el mismo lote (o None)."""
        if not self._batch or self._bi is None:
            return None
        for e in reversed(self._batch[:self._bi]):
            if e.get("ev") != "msg":
                return None
            if e.get("type") == "HINT" and e.get("hint_type") == 3:
                return e.get("hint")
            if e.get("type") not in ("HINT", "RETRY"):
                return None
        return None

    def _hand_limit(self, ev):
        """¿Es el descarte por límite de mano? ocgcore, en la End Phase del jugador del turno: HINT 3 501 y
        SELECT_CARD sobre la mano ENTERA con min = max = mano - 6, sin cadena abierta y sin poder cancelar. Un descarte
        por efecto (Graceful Charity, coste de Lightning Vortex…) va dentro de una cadena o fuera de la End Phase."""
        sel, hand, n = ev.get("selects") or [], self.side[0].hand, ev.get("min")
        if self.ocg_phase != OCG_PHASE_END or self.turn_player != 0 or self.chain or ev.get("can_cancel"):
            return False
        if not n or ev.get("max") != n or len(sel) != len(hand) or n != len(hand) - HAND_LIMIT:
            return False
        if any(d.get("controller") != 0 or d.get("location") != OCG_HAND for d in sel):
            return False
        return self._last_hint3() == HINTMSG_DISCARD

    def _discard_pick(self, ev):
        """Lista A [(235, 2)] + cuenta (tipo 0x1e) + DUEL_VIEW 0x32 tipo 0xd y las máscaras de la mano (clic directo,
        como cualquier SELECT_CARD en la mano). La cuenta va ANTES de la vista: el panel la lee en su primer fotograma
        y con 0 se cierra. Cada 0x6350 elige una carta (se baja la cuenta); con la última se contesta a ocgcore."""
        cands = []
        for i, d in enumerate(ev.get("selects", [])):
            l3 = self._loc_yo2(d)
            if l3 is None or l3[0] != 0 or l3[1] != ZONE_HAND:
                return self._pick(ev, "selects", "card")
            cands.append((*l3, i))
        n = ev["min"]
        self.attacking = False
        self.state = {"mode": "card", "cands": cands, "chosen": [], "bit": self.cfg["select_bit"], "aim": False,
                      "min": n, "max": n, "can_cancel": False, "discard": n}
        return [(0x6202, setcommand(T_LIST_RESET, 0, (1,))), (0x6202, setcommand(T_LIST_ADD, 0, (DLG_DISCARD, 2))),
                self._discard_var(n), (0x6202, setcommand(T_VIEW, 0x32, (DLGT_DISCARD, 0, 0)))] + self._pick_request()

    # --- elegir de una lista (mazo, cementerio, desterradas, extra; mezclas con el campo) -----------------------------
    def _list_pick(self, ev, cands):
        """SELECT_CARD -> 0x6610 con TODAS las candidatas (índice de la lista = índice de ocgcore) + 0x1b B=0x33."""
        self.attacking = False
        items = []
        for d in ev.get("selects", []):
            uid = self.uid_at(d["controller"], d["location"], d["sequence"])
            found = self.find(uid) if uid is not None else None
            if found is None:
                self._warn(f"lista: candidata sin carta en el espejo {d}")
                self.state = {"mode": "auto"}
                return []
            p, where, _ = found
            c = self.cards[uid]
            # Ola 7: el id sale del CÓDIGO que manda ocgcore en la candidata, no del espejo. En el mazo el espejo solo
            # sabe la POSICIÓN: su carta en cada hueco es la del orden inicial barajado al azar (_m_SHUFFLE_DECK) y la
            # identidad real llega con el MOVE. En vivo (log 143047 #632/#1044) la lista enseñó Solar Recharge, MST,
            # Brain Control… (lo que el espejo creía en esos huecos) y el índice elegido era otra carta.
            code = d.get("code") or c.code
            hidden = self.cfg["hide_npc"] and p == 1 and where in ("deck", "hand", "extra")
            y = self._visible(c, where, p) if hidden else self._yo2(code)
            items.append((y & 0x3fff, (uid >> 1) & 0xff, c.owner & 1))
        n = len(items)
        mx = max(1, min(ev.get("max", 1), n))
        mn = max(0, min(ev.get("min", 1), mx))
        if mx == 1:
            lmode = LIST_ONE
        elif mn == mx and mx <= 0x20:
            lmode = LIST_EXACT0 + mx
        else:
            mx = min(mx, 0x21)
            lmode = LIST_UPTO0 + mx
        self.state = {"mode": "list", "cands": cands, "items": items, "lmode": lmode, "min": mn, "max": mx,
                      "marks": {}, "single": None, "can_cancel": ev.get("can_cancel")}
        return self._list_request()

    def _list_request(self):
        st = self.state
        body = b"".join(struct.pack(">4H", i, s, f, 0) for i, s, f in st["items"])
        st["marks"], st["single"] = {}, None
        return [(M_LIST, struct.pack(">BIIII", 0, st["lmode"], 0, len(st["items"]), 0) + body),
                (0x6202, setcommand(T_VIEW, T_VIEW_LIST, (0, st["lmode"], 0)))]

    def _list_msg(self, msg_id, data):
        """0x6426 u32 i (elegir 1) / 0x6432 u32 i, u32 orden (0 = desmarcar) / 0x6423 u32 cuántas = cerrar."""
        st = self.state
        n = len(st["items"])
        if msg_id == 0x6426 and len(data) >= 4:
            i = struct.unpack(">I", data[:4])[0]
            if i < n:
                st["single"] = i
            return None, []
        if msg_id == 0x6432 and len(data) >= 8:
            i, order = struct.unpack(">II", data[:8])
            if i < n:
                if order:
                    st["marks"][i] = order
                else:
                    st["marks"].pop(i, None)
            return None, []
        # 0x6423: la lista se cerró
        chosen = sorted(st["marks"], key=st["marks"].get)
        if st.get("sort"):
            # B42 SORT_CARD/SORT_CHAIN: order[i] = puesto de la carta i (0 = la marcada primero = arriba)
            if len(chosen) == len(st["items"]):
                return self._answer(_respond("SORT_CARD", order=[chosen.index(i) for i in range(len(chosen))]))
            self.log(f"[traductor] ordenar: lista cerrada con {chosen}: se vuelve a abrir")
            return None, self._list_request()
        if not chosen and st["single"] is not None:
            chosen = [st["single"]]
        if chosen and st["min"] <= len(chosen) <= st["max"]:
            return self._answer(_respond(self.waiting["type"], indicies=chosen))
        if not chosen and st.get("can_cancel"):
            return self._answer(_respond(self.waiting["type"], indicies=None))
        self.log(f"[traductor] lista cerrada con {chosen} (min {st['min']}, max {st['max']}): se vuelve a abrir")
        return None, self._list_request()

    def _s_SELECT_CARD(self, ev):
        # §20 (un duelo empezado antes de la carga en caliente no lleva la clave en su cfg: manda DEFAULT_CFG)
        if self.cfg.get("hand_limit_dialog", DEFAULT_CFG.get("hand_limit_dialog")) and self._hand_limit(ev):
            return self._discard_pick(ev)                      # §20: descarte por límite de mano
        return self._pick(ev, "selects", "card")

    def _s_SELECT_TRIBUTE(self, ev):
        return self._pick(ev, "selects", "tribute")

    def _s_SELECT_UNSELECT_CARD(self, ev):
        ev2 = dict(ev, min=1, max=1)
        out = self._pick(dict(ev2, selects=ev.get("select_cards", [])), "selects", "unselect")
        if self.state.get("mode") == "unselect":
            self.state["can_cancel"] = ev.get("can_finish") or ev.get("can_cancel")
        return out

    # --- §33: SELECT_SUM (Invocación por Ritual) ----------------------------------------------------------------------
    def _s_SELECT_SUM(self, ev):
        """Sacrificios por suma de niveles. Mano/campo: CURitualDialog con las estrellas + máscaras (clase 8) y, por cada
        clic, la marca 0x29 y la cuenta 0x1f. Con alguna candidata fuera (mazo, cementerio…): lista de 1 en 1 (modo 6)
        con el texto 289. Se contesta a ocgcore en cuanto la selección es válida (como el oficial: el diálogo se cierra
        al cubrir las estrellas). Candidatas que no llevan a una selección válida no se ofrecen (si no, RETRY)."""
        if not self.cfg.get("ritual_sum", DEFAULT_CFG.get("ritual_sum")):
            return self._auto()
        must, sel = ev.get("selects_must") or [], ev.get("selects") or []
        target = int(ev.get("amount") or 0) & 0xffff
        exact = not ev.get("select_max")
        lo, hi = int(ev.get("min") or 0), int(ev.get("max") or 0)
        cands = []
        for i, d in enumerate(sel):
            l3 = self._loc_yo2(d)
            uid = self.uid_at(d["controller"], d["location"], d["sequence"])
            if l3 is None or uid is None or (l3[1] == ZONE_HAND and l3[0] != 0):
                self._warn(f"SELECT_SUM: candidata rara {d}: decide bot.ts")
                return self._auto()
            cands.append({"i": i, "loc": l3, "uid": uid, "vals": sum_vals(int(d.get("amount") or 0)),
                          "code": d.get("code")})
        mvals = [sum_vals(int(d.get("amount") or 0)) for d in must]
        if not target or not cands and not must:
            return self._auto()
        listing = any(c["loc"][1] in LIST_ZONES for c in cands)
        self.state = {"mode": "sum_list" if listing else "sum", "cands": cands, "chosen": [], "must": mvals,
                      "nmust": len(must), "target": target, "exact": exact, "lo": lo, "hi": hi,
                      "kind": KIND_RITUAL, "clear": 0}
        if not self._sum_open():
            self._warn("SELECT_SUM sin ninguna selección válida posible: decide bot.ts")
            return self._auto()
        if listing:
            return self._sum_list_request()
        left = self._sum_left()
        return [(0x6202, setcommand(T_STARS, 0, (left, STARS_NONE))),
                (0x6202, setcommand(T_LIST_RESET, 0, (1,))), (0x6202, setcommand(T_LIST_ADD, 0, (DLG_RITUAL_STARS, 2))),
                (0x6202, setcommand(T_VIEW, 0x32, (DLGT_RITUAL, DLG_RITUAL_STARS, target)))] + self._sum_request()

    def _sum_chosen_vals(self):
        st = self.state
        return list(st["must"]) + [c["vals"] for c in st["cands"] if c["i"] in st["chosen"]]

    def _sum_open(self):
        """Candidatas que todavía caben en una selección válida (índices de ocgcore)."""
        st = self.state
        chosen = self._sum_chosen_vals()
        free = [c for c in st["cands"] if c["i"] not in st["chosen"]]
        out = []
        for c in free:
            rest = [o["vals"] for o in free if o is not c]
            if sum_completable(chosen + [c["vals"]], rest, st["target"], st["exact"], st["lo"], st["hi"]):
                out.append(c["i"])
        return out

    def _sum_left(self):
        """Estrellas que faltan (lo que pinta CURitualDialog): objetivo - lo elegido (valor bajo de cada carta)."""
        st = self.state
        got = sum(v[0] for v in self._sum_chosen_vals())
        return max(0, min(0xfe, st["target"] - got))

    def _sum_done(self):
        st = self.state
        return sum_valido(self._sum_chosen_vals(), st["target"], st["exact"], st["lo"], st["hi"])

    def _sum_answer(self):
        st = self.state
        n = st["nmust"]
        # índices: las obligatorias primero y las elegidas desplazadas n (como bot.ts / brain.py; con n = 0, lo único
        # visto en los rituales probados)
        return self._answer(_respond("SELECT_SUM", indicies=list(range(n)) + [i + n for i in st["chosen"]]))

    def _sum_request(self):
        st = self.state
        ok = set(self._sum_open())
        return self._request([(*c["loc"], self.cfg["select_bit"]) for c in st["cands"] if c["i"] in ok], (),
                             KIND_RITUAL, 0)

    def _sum_pick(self, c):
        """Una carta más (clic o lista): marca 0x29 + cuenta 0x1f; si ya vale, se contesta a ocgcore."""
        st = self.state
        st["chosen"].append(c["i"])
        out = []
        if st["mode"] == "sum":
            p, z, idx = c["loc"]
            out = [(0x6202, setcommand(T_VIEW, VIEW_SET_MATERIAL, (p, z, c["uid"]))),
                   (0x6202, setcommand(T_STARS, 0, (self._sum_left(), c["vals"][0] & 0xffff)))]
        if c["loc"][1] == ZONE_HAND or c["loc"][1] < 5:
            self.ritual_mats = set(self.ritual_mats) | {c["uid"]}  # su MOVE al cementerio sale con 0x46
        if self._sum_done():
            cmd, back = self._sum_answer()
            return cmd, out + back
        return None, out + (self._sum_request() if st["mode"] == "sum" else self._sum_list_request())

    def _sum_click(self, p, zone, idx):
        st = self.state
        ok = set(self._sum_open())
        c = next((c for c in st["cands"] if c["i"] in ok and c["loc"][0] == p and c["loc"][1] == zone
                  and (zone >= 0xc or c["loc"][2] == idx)), None)
        if c is None:
            return None, self._sum_request()
        return self._sum_pick(c)

    def _sum_list_request(self):
        """Lista de 1 carta (modo 6) con las candidatas que aún valen, como el oficial con Advanced Ritual Art: lista A
        [(289, 2)] + 0x1b B=0x32 (0xfe, 289, 0) (texto de la ventana) + 0x6610 + 0x1b B=0x33 (0, 6, 0)."""
        st = self.state
        ok = set(self._sum_open())
        items, order = [], []
        for c in st["cands"]:
            if c["i"] not in ok:
                continue
            u = c["uid"]
            card = self.cards[u]
            p, where, _ = self.find(u)
            hidden = self.cfg["hide_npc"] and p == 1 and where in ("deck", "hand", "extra")
            y = self._visible(card, where, p) if hidden else self._yo2(c["code"] or card.code)
            items.append((y & 0x3fff, (u >> 1) & 0xff, card.owner & 1))
            order.append(c["i"])
        st["list_order"], st["single"], st["marks"] = order, None, {}
        body = b"".join(struct.pack(">4H", i, s, f, 0) for i, s, f in items)
        return [(0x6202, setcommand(T_LIST_RESET, 0, (1,))), (0x6202, setcommand(T_LIST_ADD, 0, (DLG_RITUAL_DECK, 2))),
                (0x6202, setcommand(T_VIEW, 0x32, (0xfe, DLG_RITUAL_DECK, 0))),
                (M_LIST, struct.pack(">BIIII", 0, LIST_ONE, 0, len(items), 0) + body),
                (0x6202, setcommand(T_VIEW, T_VIEW_LIST, (0, LIST_ONE, 0)))]

    def _sum_list_msg(self, msg_id, data):
        """0x6426 / 0x6432 marcan; 0x6423 cierra: la marcada se suma (sin marca: se vuelve a abrir)."""
        st = self.state
        n = len(st.get("list_order") or ())
        if msg_id == 0x6426 and len(data) >= 4:
            i = struct.unpack(">I", data[:4])[0]
            if i < n:
                st["single"] = i
            return None, []
        if msg_id == 0x6432 and len(data) >= 8:
            i, order = struct.unpack(">II", data[:8])
            if i < n:
                if order:
                    st["marks"][i] = order
                else:
                    st["marks"].pop(i, None)
            return None, []
        marked = sorted(st["marks"], key=st["marks"].get) or ([st["single"]] if st["single"] is not None else [])
        if not marked:
            self.log("[traductor] SELECT_SUM: lista cerrada sin carta: se vuelve a abrir")
            return None, self._sum_list_request()
        i = st["list_order"][marked[0]]
        c = next(c for c in st["cands"] if c["i"] == i)
        return self._sum_pick(c)

    def _place_cands(self, ev):
        """SELECT_PLACE de UNA zona -> {(jugador, ranura YO2): lugar de la respuesta} de las zonas libres que el cliente
        puede pulsar (ranuras 0-9 de monstruos y M/T; la zona de campo y las extra de monstruos no brillan en el cliente,
        duelo_menus §0.6). ocgcore marca con 1 las NO disponibles, relativa al que elige (0-7 sus monstruos, 8-15 sus
        M/T, +16 las del rival)."""
        mask = ev.get("field_mask", 0xffffffff)
        out = {}
        for side in (0, 1):
            for base, location, first in ((0, OCG_MZONE, 0), (8, OCG_SZONE, 5)):
                for seq in range(5):
                    if not mask & (1 << (side * 16 + base + seq)):
                        out[(side, first + seq)] = {"player": side, "location": location, "sequence": seq}
        return out

    def _s_SELECT_PLACE(self, ev):
        # Colocación manual (opción «Auto Card Placing» OFF del cliente): se pregunta la zona. Con una sola zona posible
        # (o varias a la vez) no hay nada que elegir: automática, como siempre
        if self.cfg.get("place_manual") and self.place_manual and ev.get("type") == "SELECT_PLACE" \
                and ev.get("count", 1) == 1:
            cands = self._place_cands(ev)
            if len(cands) > 1:
                self.state = {"mode": "place", "cands": cands}
                return self._request([(p, z, 0, CMD_GENERIC) for p, z in sorted(cands)], (), self.cfg["kind_select"])
        self.state = {"mode": "auto"}                            # el cliente coloca solo (opción SetPosition)
        return []

    _s_SELECT_DISFIELD = _s_SELECT_PLACE

    def _place_click(self, p, zone, idx, cmd):
        """Clic en una zona vacía (0x6350 (j, zona, i, 0xb); el cliente NO normaliza esta: zona + índice = ranura)."""
        slot = zone + idx if zone < 0xb else zone
        c = self.state["cands"].get((p, slot)) if cmd == CMD_GENERIC else None
        if c is None:
            self.log(f"[traductor] colocación: clic fuera de las zonas libres j={p} zona={zone:#x} índice={idx} cmd={cmd}: se vuelve a pedir")
            return None, self._resend()
        return self._answer(_respond(self.waiting["type"], places=[dict(c)]))

    # --- B42: ventanas del cliente (NOTES §18) ---------------------------------------------------------------------
    def _dialog(self, kind, text, p2=0, mode=None, **st):
        """Lista A [(dlg, clase 2)] (la clase 0 sola tumba el cliente, §12.1) + 0x1b B=0x32 P0=kind P2=p2."""
        self.state = dict(st, mode=mode)
        return [(0x6202, setcommand(T_LIST_RESET, 0, (1,))), (0x6202, setcommand(T_LIST_ADD, 0, (text, 2))),
                (0x6202, setcommand(T_VIEW, 0x32, (kind, 0, p2 & 0xffffffff)))]

    def _dialog_pairs(self, kind, pairs, p2=0, mode=None, **st):
        """Como _dialog, con la lista A completa [(valor, clase)…] (nombre de carta clase 4 + plantilla clase 0 + número
        clase 1): §41."""
        self.state = dict(st, mode=mode)
        out = [(0x6202, setcommand(T_LIST_RESET, 0, (len(pairs),)))]
        flat = [v for pr in pairs for v in pr]
        for k in range(0, len(flat), 8):
            out.append((0x6202, setcommand(T_LIST_ADD, 0, tuple(flat[k:k + 8]))))
        out.append((0x6202, setcommand(T_VIEW, 0x32, (kind, 0, p2 & 0xffffffff))))
        return out

    def _auto(self):
        self.state = {"mode": "auto"}
        return []

    def _uid_for_code(self, code):
        """uid de una carta con ese código fuera del campo (la que se va a invocar), para la imagen del diálogo de
        posición. Prefiere las del jugador 0 y las pilas a la vista. -> (uid, salida para el cliente) o (None, [])."""
        alias = self.cm.cdb_alias.get(code)
        for p in (0, 1):
            for name in ("hand", "grave", "banish", "extra", "deck", "mz", "sz"):
                for uid in self.side[p].pile(name):
                    c = self.cards.get(uid) if uid is not None else None
                    if c is not None and (c.code == code or (alias and c.code == alias)):
                        pre = []
                        if p == 0 and name in ("deck", "extra"):
                            pre = self._identity(uid, real=True)     # su hueco y ca4ca[uid] con la carta real
                        return uid, pre
        return None, []

    def _s_SELECT_POSITION(self, ev):
        m = ev.get("positions", 0)
        if not self.cfg.get("position_dialog") or not m & POS_FU_ATK or not m & (POS_FU_DEF | POS_FD_DEF) \
                or m & POS_FD_ATK:
            return self._auto()                          # una sola posición o con boca abajo en ATK: como antes
        uid, pre = self._uid_for_code(ev.get("code"))
        if uid is None:
            return self._auto()                          # ficha (aún sin uid): sin imagen no se abre la ventana
        face_def = bool(m & POS_FU_DEF)                  # el cliente ofrece 2: ATK boca arriba y UN DEF
        return [(0x6202, m_) if isinstance(m_, bytes) else m_ for m_ in pre] + self._dialog(
            DLGT_STAND, DLG_SEL_POS, (uid & 0xffff) | (int(face_def) << 16), "position",
            def_pos=POS_FU_DEF if face_def else POS_FD_DEF)

    def _s_ANNOUNCE_RACE(self, ev):
        avail, n = int(ev.get("available", 0)), ev.get("count", 1)
        mask = sum(1 << i for i, b in YO2_RACE.items() if avail & b)
        if not self.cfg.get("announce_dialogs") or n != 1 or not mask:
            return self._auto()                          # el cliente solo deja elegir 1 raza (FUN_0077a320: +0x3b0 = 1)
        return self._dialog(DLGT_RACE, DLG_SEL_RACE, mask, "race")

    def _s_ANNOUNCE_ATTRIB(self, ev):
        avail, n = int(ev.get("available", 0)), ev.get("count", 1)
        mask = sum(1 << i for i, b in YO2_ATTR.items() if avail & b)
        if not self.cfg.get("announce_dialogs") or not 1 <= n <= bin(mask).count("1") or n > 0xffff:
            return self._auto()
        return self._dialog(DLGT_ATTR, DLG_SEL_ATTR, n | mask << 16, "attrib", count=n)

    @staticmethod
    def _num_plan(opts):
        """Valores de ANNOUNCE_NUMBER -> (texto, etiqueta, escala, mín, máx) para CUSelectNumDialog, o None si no caben
        (mín de 4 bits, máx de 8). Etiqueta por los valores (P): múltiplos de 1000/100 = LP, 1..12 = nivel, si no «%d»."""
        if not opts or min(opts) < 0:
            return None
        if all(v and v % 1000 == 0 for v in opts):
            text, label, k = DLG_NUM_LP, LBL_LP1000, 1000
        elif all(v and v % 100 == 0 for v in opts):
            text, label, k = DLG_NUM_LP, LBL_LP100, 100
        elif max(opts) <= 12 and min(opts) >= 1 and len(opts) >= 5:
            text, label, k = DLG_NUM_LEVEL, LBL_LEVEL, 1
        else:
            text, label, k = DLG_NUM_QTY, LBL_NUM, 1
        lo, hi = min(opts) // k, max(opts) // k
        if lo > 0xf or hi > 0xff:
            return None
        return text, label, k, lo, hi

    def _s_ANNOUNCE_NUMBER(self, ev):
        opts = [int(x) for x in ev.get("options", [])]
        plan = self._num_plan(opts) if self.cfg.get("announce_dialogs") else None
        if plan is None:
            return self._auto()
        text, label, k, lo, hi = plan
        if k == 1 and self.chain_codes and self.cfg.get("number_texts", DEFAULT_CFG.get("number_texts")):
            text, label = NUMBER_TEXTS.get(self.chain_codes[-1], (text, label))      # §41: el texto de ESA carta
        return self._dialog(DLGT_NUM, text, label | lo << 16 | hi << 24, "number", opts=opts, scale=k)

    def _s_ANNOUNCE_CARD(self, ev):
        if not self.cfg.get("announce_dialogs"):
            return self._auto()
        self.state = {"mode": "announce", "card": None}
        return self._announce_request()

    def _announce_request(self):
        self.state["card"] = None
        return [(0x6202, setcommand(T_VIEW, T_VIEW_LIST, (0, LIST_ANNOUNCE, 0)))]

    def _announce_msg(self, msg_id, data):
        """0x6423 id interno (la carta pulsada) -> 0x6432 i 1 -> 0x6423 1 (cierre): se contesta en el cierre."""
        st = self.state
        if msg_id != 0x6423 or len(data) < 4:
            return None, []
        v = struct.unpack(">I", data[:4])[0]
        if v > 1:
            st["card"] = v
            return None, []
        if st.get("card") is None:
            return None, self._announce_request()
        y = self.cm.internal_to_yo2(st["card"])
        code = self.cm.yo2_to_code(y) if y else None
        if not code or not _declarable(code, self.waiting.get("opcodes", [])):
            self.log(f"[traductor] anunciar: interno {st['card']} -> {code} no vale para {self.waiting.get('opcodes')}")
            return None, self._announce_request()
        return self._answer(_respond("ANNOUNCE_CARD", card=code))

    def _s_SELECT_OPTION(self, ev):
        opts = [int(x) for x in ev.get("options", [])]
        if len(opts) <= 1 or not self.cfg.get("option_dialog"):
            return self._auto()                          # 1 opción = no hay nada que elegir (auto_response: índice 0)
        if len(opts) == 2 and self.cfg.get("option_yn", DEFAULT_CFG.get("option_yn")):
            # §41: Yubel / Yu-Jo Friendship: la elección de dos efectos era un SÍ/NO del cliente (OPTION_YN)
            (c0, n0), (c1, n1) = _desc_card(opts[0]), _desc_card(opts[1])
            spec = OPTION_YN.get((c0, n0, n1)) if c0 and c0 == c1 else None
            pairs = self._yesno_pairs(spec, c0) if spec else None
            if pairs:
                return self._dialog_cards([]) + self._dialog_pairs(2, pairs, 0, "option_yn", yes=0, no=1)
        if sorted(opts) == [OPT_YES, OPT_NO]:
            text = DLG_MAGIC_DRAIN if 59344077 in tuple(self.chain_codes) else DLG_CHAIN
            return self._dialog_cards([]) + \
                self._dialog(2, text, 0, "option_yn", yes=opts.index(OPT_YES), no=opts.index(OPT_NO))
        over = self.cfg.get("option_texts") or {}
        ids = [over.get(o) or OPTION_DLG.get(o) or OPTION_DLG.get(_option_key(o)) for o in opts]
        if len(opts) > 4 or not all(ids):
            self.log(f"[traductor] SELECT_OPTION {opts} sin texto del cliente para todas: decide bot.ts")
            return self._auto()
        body = b"".join(struct.pack(">4H", i & 0x3fff, 0, 2, 0) for i in ids)   # flags bit 1 -> bit 15 = se enseña
        text = DLG_SEL_CARDTYPE if set(opts) <= {70, 71, 72} else DLG_SEL_EFFECT
        return [(M_LIST, struct.pack(">BIIII", 0, 0, 0, len(ids), 0) + body)] + self._dialog(
            DLGT_SELECT, text, 0, "option", n=len(opts))

    def _s_SORT_CARD(self, ev):
        cards = ev.get("cards", [])
        if not self.cfg.get("sort_list") or not 2 <= len(cards) <= 0x20:
            return self._auto()
        items = []
        for d in cards:
            uid = self.uid_at(d["controller"], d["location"], d["sequence"])
            code = d.get("code") or (self.cards[uid].code if uid is not None else 0)
            y = self._yo2(code) if code else self.cm.placeholders["monster"]
            items.append((y & 0x3fff, (uid >> 1) & 0xff if uid is not None else 0, (d["controller"] & 1)))
        n = len(items)
        self.state = {"mode": "list", "sort": True, "cands": [], "items": items, "lmode": LIST_EXACT0 + n,
                      "min": n, "max": n, "marks": {}, "single": None, "can_cancel": False}
        return self._list_request()

    _s_SORT_CHAIN = _s_SORT_CARD

    def _s_SELECT_COUNTER(self, ev):
        caps = [int(c.get("count", 0)) for c in ev.get("cards", [])]
        need = int(ev.get("count", 0))
        if not self.cfg.get("counter_dialog") or not caps or sum(caps) < need:
            return self._auto()
        self.state = {"mode": "counter", "caps": caps, "need": need, "got": [], "cards": ev.get("cards", [])}
        out = self._counter_next()
        if self.state.get("done"):
            self.state = {"mode": "auto", "counters": list(self.state["got"])}   # reparto obligado: sin pregunta
        return out

    def _counter_next(self):
        """Reparte los contadores carta a carta; lo obligado se pone solo. -> mensajes o una respuesta lista."""
        st = self.state
        while len(st["got"]) < len(st["caps"]):
            k = len(st["got"])
            left = st["need"] - sum(st["got"])
            lo, hi = max(0, left - sum(st["caps"][k + 1:])), min(st["caps"][k], left)
            if lo == hi:
                st["got"].append(lo)
                continue
            if lo > 0xf or hi > 0xff:
                self.state = {"mode": "auto"}           # no cabe en el diálogo: decide bot.ts
                return []
            d = st["cards"][k]
            uid = self.uid_at(d["controller"], d["location"], d["sequence"])
            code = d.get("code") or (self.cards[uid].code if uid is not None else 0)
            internal = self.cm.yo2_to_internal(self.cm.code_to_yo2(code)) if code and self.cm.code_to_yo2(code) else 0
            st["lo"], st["hi"] = lo, hi
            out = [(0x6202, setcommand(T_LIST_RESET, 0, (1,))),
                   (0x6202, setcommand(T_LIST_ADD, 0, (internal, 4) if internal else (DLG_NUM_COUNTERS, 2))),
                   (0x6202, setcommand(T_VIEW, 0x32, (DLGT_NUM, 0, LBL_COUNTERS | lo << 16 | hi << 24)))]
            return out
        st["done"] = True
        return []

    DIALOG_MODES = ("position", "race", "attrib", "number", "option", "option_yn", "counter")

    def _dialog_msg(self, v):
        """0x6423 de una ventana de B42 -> respuesta para ocgcore. Un valor imposible (p. ej. 0 si el cliente cierra
        solo con DAT_012a90a4) no se inventa: decide bot.ts."""
        st, t = self.state, self.waiting["type"]
        mode = st["mode"]
        ev = self.waiting
        if mode == "position":
            return self._answer(_respond(t, position=POS_FU_ATK if v == 0 else st["def_pos"]))
        if mode == "race":
            bit = YO2_RACE.get(v, 0)
            if bit & int(ev.get("available", 0)):
                return self._answer(_respond(t, races=[bit]))
        elif mode == "attrib":
            bits = [b for i, b in sorted(YO2_ATTR.items()) if v >> i & 1 and b & int(ev.get("available", 0))]
            if len(bits) == st["count"]:
                return self._answer(_respond(t, attributes=bits))
        elif mode == "number":
            want = v * st["scale"]
            if want in st["opts"]:
                return self._answer(_respond(t, value=st["opts"].index(want)))
            self.log(f"[traductor] número {want} fuera de {st['opts']}: se vuelve a preguntar")
            return None, self._resend()
        elif mode == "option":
            if v < st["n"]:
                return self._answer(_respond(t, index=v))
        elif mode == "option_yn":
            return self._answer(_respond(t, index=st["yes"] if v == 1 else st["no"]))
        elif mode == "counter":
            if st["lo"] <= v <= st["hi"]:
                st["got"].append(v)
                out = self._counter_next()
                if st.get("done"):
                    return self._answer(_respond(t, counters=list(st["got"])))
                if self.state.get("mode") == "auto":
                    self.waiting, self.state = None, None
                    return {"cmd": "auto"}, []
                return None, out
            return None, self._resend()
        self.log(f"[traductor] 0x6423 {v} no vale en el diálogo {mode}: decide bot.ts")
        self.waiting = None
        self.state = None
        return {"cmd": "auto"}, []

    # ======================================================================================= cliente -> core =====
    def on_client(self, msg_id, data=b""):
        """Mensaje del cliente -> (orden para duelcore o None, [(id, bytes)] para el cliente)."""
        st = self.state or {}
        mode = st.get("mode")
        if self.waiting is None or mode in (None, "auto"):
            self.log(f"[traductor] 0x{msg_id:04x} sin decisión pendiente: ignorado")
            return None, []
        # contesta a una petición que iba DETRÁS en su cola: los 0x4b/0x4c anteriores ya se ejecutaron
        self.list_dirty = False
        if mode == "chain_none":
            # §30: «¿Revisarás el Campo?»: con SÍ (1) o NO (0) no se encadena (no hay nada activable). El oficial nunca
            # mandó petición (0x1a) tras un 204/205 (0 de 14.063, C); qué hacía con el SÍ: D. El cliente no tiene
            # permiso de jugada (no hay 0x1a): cualquier otro mensaje se ignora
            if msg_id == 0x6423 and len(data) >= 4:
                return self._answer(_respond(self.waiting["type"], index=None))
            self.log(f"[traductor] 0x{msg_id:04x} {bytes(data).hex(' ')} no esperado en modo chain_none")
            return None, []
        if mode == "list" and msg_id in (0x6426, 0x6432, 0x6423):
            return self._list_msg(msg_id, data)
        if mode == "sum_list" and msg_id in (0x6426, 0x6432, 0x6423):
            return self._sum_list_msg(msg_id, data)
        if mode == "announce" and msg_id in (0x6426, 0x6432, 0x6423):
            return self._announce_msg(msg_id, data)
        if mode in self.DIALOG_MODES and msg_id == 0x6423 and len(data) >= 4:
            return self._dialog_msg(struct.unpack(">I", data[:4])[0])
        if msg_id == 0x6350 and len(data) >= 16:
            p, zone, idx, cmd = struct.unpack(">4I", data[:16])
            cmd = -1 if cmd == 0xffffffff else cmd
            return self._click(p, zone, idx, cmd)
        if msg_id == 0x002e and len(data) >= 4:
            b = struct.unpack(">I", data[:4])[0]
            action = st.get("buttons", {}).get(b)
            if action:
                return self._answer(_respond(self.waiting["type"], action=action, index=None))
            return None, self._resend()
        if msg_id == 0x6351:
            return self._cancel()
        if msg_id == 0x6423 and self.viewing:
            # se cerró la lista de solo-ver (0x6423 0): el cliente ya no tiene permiso -> petición otra vez
            self.viewing = False
            return None, self._resend()
        if msg_id == 0x6423 and len(data) >= 4 and mode in ("yesno", "chain_ask"):
            # CUYesNoDialog (vtable 0xbec058, método 0x774650): botón 4000 -> 1 = SÍ, 0xfa1 -> 0 = NO. El 5/7 de antes
            # era de CUPhaseYesNoDialog (FUN_00774e70), que nunca se crea. En vivo llega 1 al pulsar SÍ (B01).  C
            yes = struct.unpack(">I", data[:4])[0] == 1
            t = self.waiting["type"]
            if mode == "chain_ask":
                if not yes:
                    return self._answer(_respond(t, index=None))       # no encadenar
                if self.cfg.get("chain_yes_direct") and len(st["options"]) == 1:
                    return self._answer(_respond(t, index=st["options"][0][5]))   # SÍ = la única carta posible
                self.state = dict(st, mode="chain", asked=True)       # SÍ: ahora elige la carta con sus máscaras
                return None, self._request([o[:4] for o in st["options"]], (), self.cfg["kind_select"])
            return self._answer(_respond(t, yes=yes))
        self.log(f"[traductor] 0x{msg_id:04x} {bytes(data).hex(' ')} no esperado en modo {mode}")
        return None, []

    def _answer(self, resp):
        out = [self._discard_var(0)] if self.discard_open else []     # §20: cierra el panel de descarte
        self.waiting = None
        self.state = None
        return {"cmd": "respond", "response": resp}, out

    def _resend(self):
        ev = self.waiting
        if ev is None:
            return []
        mode = (self.state or {}).get("mode")
        if mode in ("card", "tribute", "unselect"):
            return self._pick_request()
        if mode == "list":
            return self._list_request()
        if mode == "sum":
            return self._sum_request()
        if mode == "sum_list":
            return self._sum_list_request()
        if mode == "chain" and self.state.get("asked"):
            # ya dijo SÍ al diálogo de cadena: solo las máscaras (no volver a preguntar)
            return self._request([o[:4] for o in self.state["options"]], (), self.cfg["kind_select"])
        return self._on_select(ev)

    def _click(self, p, zone, idx, cmd):
        st = self.state
        mode = st["mode"]
        if cmd == CMD_LOOK and zone in (ZONE_GRAVE, ZONE_BANISH, ZONE_EXTRA) and self.cfg.get("pile_look"):
            return None, self._look(p & 1, zone)
        if cmd == -1:
            # -1 = el jugador cerró el menú sin elegir (FUN_0077ce00 deja +0x4d0 = -1): NO es una jugada. Antes se
            # tomaba como «la primera opción de la carta» y en vivo cambió la posición de un monstruo sin querer.
            return None, self._resend()
        if mode == "place":
            return self._place_click(p, zone, idx, cmd)
        if mode in ("idle", "battle", "chain"):
            same = [o for o in st["options"] if o[0] == p and o[1] == zone and (zone >= 0xc or o[2] == idx)]
            pick = next((o for o in same if o[3] == cmd), None)
            if pick is None:
                self.log(f"[traductor] jugada sin opción: j={p} zona={zone:#x} índice={idx} cmd={cmd}")
                return None, self._resend()
            t = self.waiting["type"]
            if mode == "chain":
                return self._answer(_respond(t, index=pick[5]))
            self.attacking = mode == "battle" and pick[4] == "SELECT_BATTLE"
            self.attacker = (pick[0], pick[1]) if self.attacking and pick[1] < 5 else None   # (jugador, ranura)
            return self._answer(_respond(t, action=pick[4], index=pick[5]))
        if mode == "sum":
            return self._sum_click(p, zone, idx)
        if mode in ("card", "tribute", "unselect"):
            c = next((c for c in st["cands"] if c[3] not in st["chosen"] and c[0] == p and c[1] == zone
                      and (zone >= 0xc or c[2] == idx)), None)
            if c is None:
                return None, self._pick_request()
            if mode == "unselect":
                return self._answer(_respond("SELECT_UNSELECT_CARD", index=c[3]))
            st["chosen"].append(c[3])
            if len(st["chosen"]) >= st["max"]:
                cmd, out = self._answer(_respond(self.waiting["type"], indicies=list(st["chosen"])))
                if st.get("kind") == KIND_OPP_HAND and self.hand_open is not None:
                    out = out + self._opp_hand_done(st)
                return cmd, out
            return None, self._pick_request()
        return None, []

    def _look(self, p, zone):
        """«Ver Detalles» sobre una pila: el cliente no tiene visor local (FUN_00790e50 sin llamadores); la lista la
        abre el servidor: 0x6610 con las cartas + 0x6202 tipo 0x1b B=0x33 (P0 = jugador, P1 = MODO de CWindowDeckList,
        FUN_007674a0). Antes P1 iba con la ZONA (0xe = 14 = modo «elegir exactamente 8») y sin 0x6610: ventana en
        blanco que no se cerraba. Con un modo de solo ver (1..5) el cliente contesta 0x6423 0 al cerrar y ENTONCES
        se reenvía la petición (todo mensaje del cliente borra máscaras y permiso, FUN_00692300).  C (formato) / D (modo)"""
        name = {ZONE_GRAVE: "grave", ZONE_BANISH: "banish", ZONE_EXTRA: "extra"}[zone]
        if p == 1 and name == "extra":
            return self._resend()                      # el extra de la NPC es secreto
        mode = self.cfg["look_modes"].get(zone, 1)
        pile = self.side[p].pile(name)
        # ítem = (id & 0x3fff) | flags << 14 | serie << 23 = la misma palabra que la de la carta en su hueco
        body = b"".join(struct.pack(">4H", self._visible(self.cards[u], name, p) & 0x3fff, (u >> 1) & 0xff,
                                    self.cards[u].owner & 1, 0) for u in pile)
        self.viewing = True
        return [(M_LIST, struct.pack(">BIIII", 0, mode, 0, len(pile), 0) + body),
                (0x6202, setcommand(T_VIEW, T_VIEW_LIST, (p, mode, 0)))]

    def _cancel(self):
        st = self.state
        mode = st["mode"]
        t = self.waiting["type"]
        if mode == "chain":
            if not st.get("forced"):
                return self._answer(_respond(t, index=None))
        elif mode in ("card", "tribute"):
            if len(st["chosen"]) >= st["min"] and st["chosen"]:
                cmd, out = self._answer(_respond(t, indicies=list(st["chosen"])))
                if st.get("kind") == KIND_OPP_HAND and self.hand_open is not None:
                    out = out + self._opp_hand_done(st)
                return cmd, out
            if st.get("can_cancel"):
                cmd, out = self._answer(_respond(t, indicies=None))
                if st.get("aim"):
                    # ataque cancelado: la espada seguiría al ratón. DUEL_VIEW 0xa (FUN_00754040, lo mismo que al
                    # entrar en batalla) vuelve al estado 1. No el motor 0x15: su FUN_006e3d30 borra efectos tipo 4  C
                    out = out + [(0x6202, setcommand(T_VIEW, VIEW_BATTLE_INIT, (self.turn_player or 0,)))]
                return cmd, out
        elif mode == "sum_list":
            return None, []                                      # §33: la lista se cierra con 0x6423
        elif mode == "list":
            # la lista sigue abierta y se cierra SIEMPRE con 0x6423 (ahí se cancela si se puede): ni contestar aquí (su
            # 0x6423 llegaría luego a la decisión siguiente, p. ej. un SÍ/NO) ni abrir otra encima
            return None, []
        elif mode == "unselect":
            if st.get("can_cancel"):
                return self._answer(_respond(t, index=None))
        elif mode == "yesno":
            return self._answer(_respond(t, yes=False))
        elif mode == "chain_ask":
            return self._answer(_respond(t, index=None))         # clic derecho con el diálogo = no encadenar
        elif mode in self.DIALOG_MODES or mode == "announce":
            return None, []                                      # B42: la ventana sigue abierta y no se cancela
        return None, self._resend()


IGNORED = {"HINT", "CHAINED", "CHAIN_SOLVED", "CHAIN_NEGATED",
           "CHAIN_DISABLED", "CARD_SELECTED", "RANDOM_SELECTED", "BECOME_TARGET", "CARD_HINT", "PLAYER_HINT",
           "FIELD_DISABLED", "UPDATE_DATA", "UPDATE_CARD", "WAITING", "START", "MISSED_EFFECT", "CARD_TARGET",
           "CANCEL_TARGET", "EQUIP", "ADD_COUNTER", "REMOVE_COUNTER", "SHOW_HINT", "BE_CHAIN_TARGET",
           "CREATE_RELATION", "RELEASE_RELATION", "RETRY", "HAND_RES", "TOSS_COIN", "SWAP", "DECK_TOP",
           "REFRESH_DECK", "SHUFFLE_SET_CARD", "SHUFFLE_EXTRA", "AI_NAME", "MATCH_KILL", "REMOVE_CARDS"}


def _respond(t, **kw):
    return {"type": t, **kw}


_DECL = {}


def _declarable(code, opcodes):
    """B42 ANNOUNCE_CARD: ¿cumple la carta los opcodes de ocgcore? (brain.declarable con cards.cdb). Si no se puede
    comprobar (sin brain/cdb) dice que sí: ocgcore contestará RETRY y se vuelve a preguntar (B45)."""
    if not opcodes:
        return True
    try:
        if "db" not in _DECL:
            sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "ia")))
            import brain                                                   # noqa: E402
            _DECL["db"], _DECL["fn"] = brain.CardDB.shared(), brain.declarable
        c = _DECL["db"].get(code)
        return True if c is None else bool(_DECL["fn"](c, [int(x) for x in opcodes]))
    except Exception:                                                      # noqa: BLE001
        _DECL["db"], _DECL["fn"] = None, None
        return True


def sum_vals(amount):
    """sum_param de ocgcore -> valores posibles de la carta: el u16 bajo y, si no es 0, el alternativo (u16 alto)."""
    v1, v2 = amount & 0xffff, (amount >> 16) & 0xffff
    return (v1, v2) if v2 and v2 != v1 else (v1,)


def _sum_states(vals, target, exact, start=None):
    """Estados (suma, menor valor, n.º de cartas) alcanzables eligiendo un valor por carta. Con «exacta» se poda lo que
    pasa de target; con «al menos», lo que ya llega (añadir otra carta la haría sobrante)."""
    states = set(start) if start is not None else {(0, 1 << 30, 0)}
    for opts in vals:
        nxt = set(states)
        for s, m, c in states:
            if not exact and s >= target:
                continue
            for v in opts:
                if exact and s + v > target:
                    continue
                nxt.add((s + v, min(m, v), c + 1))
        states = nxt
    return states


def _sum_state_ok(s, m, c, target, exact, lo, hi):
    if c < max(lo, 1) or (hi and c > hi):
        return False
    return s == target if exact else (s >= target and s - m < target)


def sum_valido(vals, target, exact, lo=0, hi=0):
    """¿Las cartas elegidas (vals = [(v1[, v2]), …], TODAS) cumplen la suma como la comprueba ocgcore? Exacta: alguna
    elección de valores suma target. Al menos (select_max = 1): suma >= target sin carta sobrante (quitando la menor
    ya no llega: check_with_sum_greater_limit). lo/hi = n.º de cartas (hi 0 = sin tope)."""
    states = {(0, 1 << 30, 0)}
    for opts in vals:
        states = {(s + v, min(m, v), c + 1) for s, m, c in states for v in opts}
    return any(_sum_state_ok(s, m, c, target, exact, lo, hi) for s, m, c in states)


def sum_completable(chosen, rest, target, exact, lo=0, hi=0):
    """¿Se puede completar `chosen` (todas cuentan) con algún subconjunto de `rest` hasta una selección válida?"""
    start = {(0, 1 << 30, 0)}
    for opts in chosen:
        start = {(s + v, min(m, v), c + 1) for s, m, c in start for v in opts}
    if exact:
        start = {x for x in start if x[0] <= target}
    states = _sum_states(rest, target, exact, start)
    return any(_sum_state_ok(s, m, c, target, exact, lo, hi) for s, m, c in states)


def _free_places(mask, count):
    """SELECT_PLACE: bits a 1 = zonas NO disponibles; relativa al que elige (0-7 sus monstruos, 8-15 sus M/T,
    16-31 lo mismo del rival). Elige las primeras libres propias."""
    out = []
    for side, player in ((0, 0), (1, 1)):
        for base, location in ((0, OCG_MZONE), (8, OCG_SZONE)):
            for seq in range(5):
                if len(out) >= count:
                    return out
                if not mask & (1 << (side * 16 + base + seq)):
                    out.append({"player": player, "location": location, "sequence": seq})
    return out
