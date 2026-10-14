"""NPC del mapa (paso E5 de la reestructura, REESTRUCTURA.md §3.4): los puntos y reglas de 2008 (`SPOTS_*`,
`NPC_SPOTS_2008`, `NPC_RULES_2008`), `npc_rule`, `npc_parts`, `load_npcs` (la lista de NPCINFOLIST), `npc_list_parts` y
`npc_entry` (el registro de 61 B de 0x4232).

Salido de run.py SIN cambiar la lógica. La lista `NPCS` NO se guarda aquí: vive en el namespace (el panel la REASIGNA con
`NS["NPCS"] = load_npcs()`), así que todo lo que la lee dentro de este módulo la pide como `srv.NPCS`; run.py la crea al
final de las cargas (`NPCS = load_npcs()`, cuando ya existen `NR`, `MW` y `AV`). Igual se leen como `srv.X` las banderas
(`NPC_SPOTS_2008` la escribe el panel), `NR` (research/npcreglas), `MW` (research/mundo), `AV`, `NPC_SEX`, `cur_world`
y las funciones de este módulo que un hot_*.py pueda sustituir. `NR` y `MW` los carga run.py (paso E8 los moverá).
"""
import json
import os

from yo2srv import rutas
from yo2srv.red import proto

srv = None      # el Server; lo fija setup(). Hasta entonces nada de este módulo se puede llamar.


def setup(servidor):
    """Enlaza el módulo con el Server (idempotente). run.py lo llama justo antes de importar los nombres de aquí."""
    global srv
    srv = servidor
    return servidor


# NPC del mapa. El mapa las crea él solo a partir de NPCINFOLIST (FUN_009ff2d0 -> FUN_009d2c90 -> CNpcChibiAvatar):
# cada registro lleva el id de un punto del mapa, y los puntos están fijos en el exe (tablas en 0xd0f688: 14 puntos
# especiales 201-214, y 0xd0f7a0: 64 puntos 1-64, 8 por área; entrada = (área, punto, x, y, flags)). Solo se crean
# las del área en la que está el jugador (escena +0x274).
# Ids 1-98 (fuki_index.csv de 2008: 2 filas por NPC). 51-94 = personajes famosos con muñeco y nombre propios del
# cliente (ignora nombre y piezas); el resto se dibuja con las 16 piezas que mandamos. Identidades: npc_ids.json
# (sacado de los diálogos, yo2text.py). Registro (FUN_006c30f0, 61 B): u32 id, nombre16, piezas16, u8,
# 6×u32 = (+0x28, +0x30, +0x34 nivel mínimo, +0x38 punto del mapa, +0x3c, +0x40 NPC previa en la cadena).
SPOTS_A = [(4, 209), (4, 213), (5, 201), (6, 202), (6, 208), (7, 203), (7, 207), (0, 204), (0, 212), (1, 205),
           (2, 214), (2, 210), (2, 211), (9, 206)]
FAMOUS = [51, 54, 57, 60, 63, 66, 69, 72, 75, 78, 81, 84]          # nivel 0 de cada personaje
# NPC_SPOTS_2008: reparto de la web de 2008 (research/npcs_web/NOTES.md, «Reparto aplicado»). Áreas del exe (tabla
# 0xd2d4d0): 0 Clock Square, 1 Game Shop, 2 Pier, 3 Kaiba Corp., 9 R&D Lab · 4 Duel Academy, 5 Store, 6 Duel Space,
# 7 Slifer Red. Famoso -> punto especial (Yugipedia); Marik (75) sin fuente: hueco libre del Pier (D). Originales:
# solo las confirmadas (DC 1-2-2008): Abigail 43 y NiaNia 26 -> área 4 (Robert 17 ya está en el área 0). False = como antes.
NPC_SPOTS_2008 = True   # 06-10: activado con el OK del owner (research/npcs_web/NOTES.md §10)
SPOTS_2008 = {51: 204, 54: 206, 57: 205, 60: 203, 63: 202, 66: 201, 69: 210, 72: 207, 75: 214, 78: 208, 81: 212,
              84: 209}
NORMAL_SPOTS_2008 = {43: 2, 26: 4}                                  # puntos libres del área 4 (los impares están ocupados)
# NPC_RULES_2008: duelo contra NPC con las reglas oficiales (research/npcreglas/proposal.py; research/npcs_web/NOTES.md
# «Duelo contra NPC: reglas aplicadas»). Todo apagado = como antes. dp: 1 DP por duelo · no_record: no toca LV/récord ·
# card: 1 carta (0x4800 con 1 sobre -> 0x4802 -> 1 carta) · friendship: cadena de 3 niveles, estrellas, premios, 0x4237 ·
# famous_decks: receta del nivel en curso · npc_window: 0x43a0 u8 = 1 (ventana de NPC; match cerrado tras 1 partida).
NPC_RULES_2008 = {"dp": True, "no_record": True, "card": True, "friendship": True, "famous_decks": True,
                  "npc_window": False}  # 06-10: todo activado con el OK del owner (efectivo al reiniciar)
# 07-10 npc_window APAGADO (bug «no me puedo mover tras el duelo de NPC», 2 veces). Con u8 = 1 el cliente pone
# DAT_012bb6b0+0x9c = 1 (0x6bad5c) y nadie lo vuelve a 0; al volver al mapa FUN_008603d0 (0x8623e2) solo pone +0xa4 = 1
# si +0x9c == 0, y sin +0xa4 FUN_00863ec0 (0x869bfe) no llama a FUN_0089fc70 (despedida de la NPC). Sin despedida la
# charla no acaba en FUN_0089cc50, que es lo único que suelta CTRL_NPC_DUEL (0x14, prioridad 3000: ni 0xc ni 0x18 la
# pisan), y FUN_009c5560 solo mueve y manda 0x5300 con DAT_012bc618 == 0. Log: sin «NPC[...]» de despedida tras
# 01:02:52 y 01:27:35 (sí a las 19:37:52 del 06-10, u8 = 0). El oficial mandaba duelo simple (u8 = 0, npcs_web §NPC). C

def npc_rule(k):
    return bool(srv.NR is not None and srv.NPC_RULES_2008.get(k))

def npc_parts(nid, sex):
    """16 piezas (formato del exe): [0] sexo, [1] pelo delantero, [2] accesorio 0x20=no, [3] 0x30, [4] ojos 0x40+,
    [5] boca 0x60+, [6] ropa (0x70+ Domino, 0x80+ Academia), [7] pelo trasero 0x90+, [8] disco 0xb8, [9] 0x20,
    [13] piel, [14] color de pelo, [15] color de ojos. Variantes pequeñas para no pedir ficheros que no existan."""
    r = (nid * 2654435761) & 0xffffffff
    pick = lambda k, n: (r >> (k * 3)) % n
    clothes = (0x80 if pick(0, 2) else 0x70) + pick(1, 4)
    return bytes([1 if sex == "F" else 0, pick(2, 6), 0x20, 0x30, 0x40 + pick(3, 4), 0x60 + pick(4, 4), clothes,
                  0x90 + pick(5, 4), 0xb8, 0x20, 0, 0, 0, pick(6, 3), pick(7, 6), pick(8, 4)])

def load_npcs():
    try:
        info = {d["id"]: d for d in json.load(open(os.path.join(rutas.EMU, "npc_ids.json"), encoding="utf-8"))}
    except OSError:
        info = {}
    npcs = []
    # OJO: `srv.mundo` es el alias de MW (compat.ALIAS["mundo"]), no la función mundo() de run.py: de ahí srv.ns["mundo"]
    for (area, spot), nid in zip(srv.SPOTS_A, srv.FAMOUS):
        spot = srv.SPOTS_2008.get(nid, spot) if srv.NPC_SPOTS_2008 else spot
        npcs.append((nid, info.get(nid, {}).get("name", f"NPC {nid}"), spot, bytes(16)))
    if srv.ns["mundo"]("spots"):                                  # MUNDO_2008 spots: originales de DC08 con su nombre
        for nid, name, spot, parts, sex in srv.MW.orig_entries(info, srv.npc_parts):
            npcs.append((nid, name, spot, parts)); srv.NPC_SEX[nid] = sex
        return srv.NR.npc_list(npcs) if srv.npc_rule("friendship") else npcs
    normals = [i for i in range(1, 51)]
    extra = [n for n in srv.NORMAL_SPOTS_2008 if n not in normals[:32]] if srv.NPC_SPOTS_2008 else []   # Abigail (43)
    for k, nid in enumerate(normals[:32] + extra):            # 4 por área en los puntos 1-64 (8 por área 0-7)
        area = k % 8; spot = {4: 1, 5: 9, 6: 17, 7: 25, 0: 33, 1: 41, 2: 49, 3: 57}[area] + k // 8 * 2
        spot = srv.NORMAL_SPOTS_2008.get(nid, spot) if srv.NPC_SPOTS_2008 else spot
        d = info.get(nid, {})
        npcs.append((nid, d.get("name", f"NPC {nid}")[:15], spot, srv.npc_parts(nid, d.get("sex"))))
        srv.NPC_SEX[nid] = d.get("sex")
    return srv.NR.npc_list(npcs) if srv.npc_rule("friendship") else npcs   # + niveles 2 y 3 de los famosos

NPC_SEX = {}

def npc_list_parts(n):
    """Piezas de una NPC para NPCINFOLIST: las normales, con las del agente 4 (research/avatar), válidas en el mundo
    actual; el cliente cambia por el chico por defecto cualquier pieza que no exista o sea del otro mundo."""
    nid, name, spot, parts = n
    if srv.AV is not None and parts != bytes(16):
        parts = srv.AV.npc_parts(nid, srv.NPC_SEX.get(nid), srv.cur_world())   # V09: el mundo de QUIEN pide la lista
    return nid, name, spot, parts

def npc_entry(nid, name, spot, parts=bytes(16)):
    thr, prev = srv.NR.chain_fields(nid, {n[0] for n in srv.NPCS}) if srv.npc_rule("friendship") else (0, 0)   # +0x34, +0x40
    return proto.W().u32(nid).s(name, 16).bytes() + parts[:16].ljust(16, bytes(1)) + \
        proto.W().u8(0).u32(0).u32(0).u32(thr).u32(spot).u32(0).u32(prev).bytes()
