"""Registro de banderas del servidor (REESTRUCTURA.md §2.6 y Anexo A). Pasos S1 (registro de solo lectura) y R2-corto (perfiles).

Qué es: un único sitio que dice, para cada bandera, cómo se llama, dónde vive HOY su valor (la `liga`), cuál es su valor
por defecto en el perfil `vanilla` (lo que hace el código sin variables de entorno), cuál en `enhanced`, de dónde viene la
idea (`origen`), qué variable de entorno la cambia y qué efecto tiene cambiarla.

Qué hace R2-corto: `Config.aplicar_perfil()` (la llama `arranque.cargar` al acabar las cargas) deja cada bandera en el valor de
su perfil escribiendo en su `liga`, con la precedencia de §2.6 (de menos a más):
    perfil (`YO2_PERFIL`)  ->  admin_banderas.json (junto a state.json; SOLO se lee, nunca se crea)  ->  variable de
    entorno `YO2_*` de la bandera  ->  cambio en caliente (panel / consola, en memoria)
Vanilla ES lo que ya hace el código sin variables (lo vigila test_vivo.py): por eso un valor del perfil que coincide con
`vanilla` no se escribe (así el arranque por defecto no puede cambiar nada, ni siquiera en instancias de prueba donde el
código se desvía a propósito, p. ej. `npc_janken_azar` con YO2_PVP_TEST=1). Solo se escribe lo que el perfil `enhanced`
pone distinto de vanilla y lo que fija admin_banderas.json. Hoy `enhanced` = `vanilla` en todas.
Qué NO hace todavía (R2 completo): no hay `set` en caliente ni «dejar fijo»; el panel sigue con su lista FLAGS.

`origen` (obligatorio en cada bandera):
    "oficial"    como el original (confianza C/P en las notas), o intento de serlo
    "inventado"  nuestro, aprobado por el owner y detrás de una bandera (no es 1:1)
    "tecnico"    sin efecto de juego: rutas, puertos, límites, logs, pruebas, seguridad
    "?"          sin decidir (se anota en BANDERAS.md)
El listado `yo2srv/BANDERAS.md` se genera con `python -m yo2srv.gen_banderas`.

Ligas (cómo se lee el valor VIVO; las mismas de `panel._flag_get`, más dos):
    ("ns", NOMBRE)                    variable del namespace de run.py (ns[NOMBRE])
    ("nsdict", DICT, clave)           clave de un dict del namespace (MUNDO_2008, NPC_RULES_2008)
    ("mod", MODULO, NOMBRE)           atributo de un módulo (MODULO = nombre en el namespace, o en sys.modules)
    ("moddict", MODULO, NOMBRE, clave) clave de un dict de un módulo (PP.NPC_MP_REWARD, LS.DUEL_CFG)
    ("trcfg", clave)                  DEFAULT_CFG del traductor (OCG.TR.DEFAULT_CFG)
    ("env", VARIABLE)                 la variable de entorno tal cual (texto o None): el código la lee en el momento de
                                      usarla y no hay un valor vivo que consultar

`vanilla` = valor por defecto con el perfil vanilla. `DERIVADA` = depende de la máquina/instancia (rutas, offset…) y no
tiene un valor fijo. Los valores de vanilla incluyen las banderas aprobadas el 07-10 (P0a; Anexo A.2).

La prueba `yo2srv/tests/test_vivo.py` arranca una instancia de prueba sin variables YO2_* de juego y comprueba por su
consola que el valor vivo de cada bandera con valor fijo es EXACTAMENTE su `vanilla`; `test_config.py` comprueba que
todas las del panel (panel.FLAGS) están aquí con la misma liga, que cada liga existe en el código fuente y que el perfil
se aplica con la precedencia de arriba.
"""
import json
import os
import sys
from typing import Any, NamedTuple, Optional


class _Marca:
    def __init__(self, nombre):
        self.nombre = nombre

    def __repr__(self):
        return self.nombre

    def __bool__(self):
        return False


DERIVADA = _Marca("DERIVADA")        # sin valor fijo (depende de la máquina o de la instancia)
NO_CARGADA = _Marca("NO_CARGADA")    # el sitio de la liga no existe ahora (módulo sin cargar, clave que falta)
SIN = _Marca("SIN")                  # `enhanced` sin definir: el perfil enhanced usa el valor de vanilla

PERFILES = ("vanilla", "enhanced")
ORIGENES = ("oficial", "inventado", "tecnico", "?")
FICHERO_FIJAS = "admin_banderas.json"   # junto a state.json (datos_dir); lo escribirá el panel en R2 completo

GRUPOS = (("lobby", "Lobby y salas"), ("mundo", "Mundo de 2008"), ("npc", "Duelos contra NPC"),
          ("hub", "Jugadores en el mapa"), ("pvp", "PvP y repeticiones"), ("torneos", "Torneos y eventos"),
          ("equipos", "Equipos"), ("intercambio", "Intercambio"), ("tienda", "Tienda y cuentas nuevas"),
          ("persistencia", "Perfiles, DP y premios"), ("duelo", "Duelo (traductor, ocgcore, lockstep)"),
          ("red", "Red y límites"), ("sistema", "Despliegue (entorno)"))


class Flag(NamedTuple):
    id: str
    liga: tuple
    vanilla: Any
    env: Optional[str]
    tipo: Any              # "bool" | "int" | "float" | "str" | "dict" | "lista" | "any" | tupla de opciones
    efecto: Optional[str]  # cuándo se nota un cambio en caliente (None = sin clasificar)
    reiniciar: bool        # solo cambia al reiniciar
    grupo: str
    desc: str
    fijo: str              # dónde se deja fija hoy
    accion: Optional[str]  # qué hace el panel después de cambiarla (mundo | npcs | watch)
    deploy: bool           # entorno de despliegue (no es una bandera de juego)
    nota: str
    origen: str            # "oficial" | "inventado" | "tecnico" | "?" (ver el docstring)
    enhanced: Any          # valor del perfil enhanced (SIN = el de vanilla)


def _tipo_de(v):
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, dict):
        return "dict"
    if isinstance(v, (list, tuple)):
        return "lista"
    return "any"


def F(id, liga, vanilla, *, origen, enhanced=SIN, env=None, grupo=None, desc="", efecto=None, reiniciar=False, fijo="",
      accion=None, tipo=None, deploy=False, nota=""):
    if origen not in ORIGENES:
        raise ValueError(f"bandera {id}: origen {origen!r} no es uno de {ORIGENES}")
    if grupo is None:
        grupo = id.split(".", 1)[0] if "." in id else "sistema"
    return Flag(id, tuple(liga), vanilla, env, tipo if tipo is not None else _tipo_de(vanilla), efecto, bool(reiniciar),
                grupo, desc, fijo, accion, bool(deploy), nota, origen, enhanced)


# =====================================================================================================================
# REGISTRO. Orden: las 40 del panel (mismos ids, liga, efecto y descripción que panel.FLAGS), luego el resto del Anexo A
# y lo que se encontró con `grep YO2_` (marcado «extra» en la nota cuando no estaba en el Anexo).
# =====================================================================================================================
REGISTRO = [
    # ---------------------------------------------------------------- las del panel (panel.FLAGS)
    F('mundo.spots', ('nsdict', 'MUNDO_2008', 'spots'), True, env='YO2_MUNDO', grupo='mundo', desc='Las 24 NPC originales de 2008 con su nombre y zona', efecto='al entrar en un mapa', fijo='run.py MUNDO_2008 (YO2_MUNDO=-spots la apaga; =0 todas)', accion='mundo', nota='YO2_MUNDO es una lista: 0/none = todas apagadas, -x = apaga x, all = todas', origen='oficial'),
    F('mundo.prizes', ('nsdict', 'MUNDO_2008', 'prizes'), True, env='YO2_MUNDO', grupo='mundo', desc='Premios de las NPC originales (carta de 5 victorias)', efecto='inmediato', fijo='run.py MUNDO_2008', accion='mundo', origen='oficial'),
    F('mundo.challenge', ('nsdict', 'MUNDO_2008', 'challenge'), True, env='YO2_MUNDO', grupo='mundo', desc='挑戦権: derecho a retar a Kaiba/Alexis tras ganar a sus NPC', efecto='inmediato', fijo='run.py MUNDO_2008', accion='mundo', origen='oficial'),
    F('mundo.pvp_titles', ('nsdict', 'MUNDO_2008', 'pvp_titles'), True, env='YO2_MUNDO', grupo='mundo', desc='Títulos por logros también tras un PvP', efecto='inmediato', fijo='run.py MUNDO_2008', accion='mundo', origen='?'),
    F('mundo.hair_titles', ('nsdict', 'MUNDO_2008', 'hair_titles'), True, env='YO2_MUNDO', grupo='mundo', desc='Títulos Kaiser Afro / Rey del Mechón', efecto='inmediato', fijo='run.py MUNDO_2008', accion='mundo', origen='oficial'),
    F('mundo.title_owned', ('nsdict', 'MUNDO_2008', 'title_owned'), True, env='YO2_MUNDO', grupo='mundo', desc='Rechazar un título que el jugador no tiene', efecto='inmediato', fijo='run.py MUNDO_2008', accion='mundo', origen='tecnico'),
    F('mundo.GM_ACCOUNTS', ('mod', 'MW', 'GM_ACCOUNTS'), True, env='YO2_GM_ACCOUNTS', grupo='mundo', desc='GM: KONAMI y GAME MASTER solo para las cuentas GM (y GAME MASTER se les da solo)', efecto='inmediato', fijo='research/mundo/proposal.py GM_ACCOUNTS (YO2_GM_ACCOUNTS=0 la apaga)', accion='mundo', origen='?'),
    F('NPC_SPOTS_2008', ('ns', 'NPC_SPOTS_2008'), True, grupo='mundo', desc='Famosos en su sitio de 2008', efecto='al entrar en un mapa', fijo='run.py NPC_SPOTS_2008', accion='npcs', origen='oficial'),
    F('npc.dp', ('nsdict', 'NPC_RULES_2008', 'dp'), True, grupo='npc', desc='Duelo contra NPC: cuesta 1 DP', efecto='próximo duelo NPC', fijo='run.py NPC_RULES_2008', origen='oficial'),
    F('npc.no_record', ('nsdict', 'NPC_RULES_2008', 'no_record'), True, grupo='npc', desc='Duelo contra NPC: no toca LV ni récord', efecto='próximo duelo NPC', fijo='run.py NPC_RULES_2008', origen='oficial'),
    F('npc.card', ('nsdict', 'NPC_RULES_2008', 'card'), True, grupo='npc', desc='Duelo contra NPC: 1 carta de premio', efecto='próximo duelo NPC', fijo='run.py NPC_RULES_2008', origen='oficial'),
    F('npc.friendship', ('nsdict', 'NPC_RULES_2008', 'friendship'), True, grupo='npc', desc='Amistad con NPC: estrellas, niveles y premios', efecto='al entrar en un mapa', fijo='run.py NPC_RULES_2008', accion='npcs', origen='oficial'),
    F('npc.famous_decks', ('nsdict', 'NPC_RULES_2008', 'famous_decks'), True, grupo='npc', desc='Mazos reales de los famosos', efecto='próximo duelo NPC', fijo='run.py NPC_RULES_2008', origen='oficial'),
    F('npc.npc_window', ('nsdict', 'NPC_RULES_2008', 'npc_window'), False, grupo='npc', desc='Ventana de resultados de NPC', efecto='próximo duelo NPC', fijo='run.py NPC_RULES_2008', nota='apagada a propósito (07-10): con u8 = 1 el cliente no deja moverse tras el duelo', origen='oficial'),
    F('MYROOM_SELF_AVATAR', ('ns', 'MYROOM_SELF_AVATAR'), True, env='YO2_MYROOM_AVATAR', grupo='lobby', desc='My Room enseña tu propio aspecto', efecto='inmediato', fijo='YO2_MYROOM_AVATAR=0 lo apaga', origen='oficial'),
    F('lobby.LIMIT_LIST', ('mod', 'LP', 'LIMIT_LIST'), True, env='YO2_LIMIT_LIST', grupo='lobby', desc='Lista de prohibidas/limitadas de 2008 (0x5600)', efecto='al elegir personaje', fijo='YO2_LIMIT_LIST=0 lo apaga', origen='oficial'),
    F('lobby.WATCH', ('mod', 'LP', 'WATCH'), True, env='YO2_WATCH', grupo='lobby', desc='Ver duelos de otros (espectador)', efecto='inmediato', fijo='YO2_WATCH=0 lo apaga', accion='watch', nota='el panel la copia también a pvp_match.PVP_WATCH (id pvp.PVP_WATCH)', origen='oficial'),
    F('lobby.ROOM_REOPEN', ('mod', 'LP', 'ROOM_REOPEN'), True, env='YO2_ROOM_REOPEN', grupo='lobby', desc='La sala sigue abierta tras el duelo', efecto='próximo duelo', fijo='YO2_ROOM_REOPEN=0 lo apaga', origen='oficial'),
    F('lobby.ROOM_LANG_MERGE', ('ns', 'ROOM_LANG_MERGE'), True, env='YO2_ROOM_LANG_MERGE', grupo='lobby', desc='Las salas de un tipo comparten bloque sea cual sea el idioma del cliente', nota='extra (09-10); YO2_ROOM_LANG_MERGE=0 vuelve a separar por idioma del cliente. Solo cuenta con lobby.ROOM_PER_LANG apagada', origen='inventado'),
    F('lobby.ROOM_PER_LANG', ('ns', 'ROOM_PER_LANG'), False, env='YO2_ROOM_PER_LANG', grupo='lobby', desc='Salas por idioma 1:1: un puerto por sala (idioma, tipo); la mundial (b1 = 6) junta idiomas', efecto='al reiniciar', reiniciar=True, fijo='YO2_ROOM_PER_LANG=1 la enciende', nota='10-10; abre 77 puertos más (5800-5876 + offset): apagada hasta que OpenServer/JoinServer (launcher/sala/tunel.go Puertos) los reenvíen. Encendida, ROOM_LANG_MERGE no cuenta (research/lobby/NOTES.md «Salas por idioma 1:1»)', origen='oficial'),
    F('lobby.PIDNAME_PLAYERS', ('mod', 'LP', 'PIDNAME_PLAYERS'), True, env='YO2_PIDNAME_PLAYERS', grupo='lobby', desc='Buscar jugador por nombre (0x4500)', efecto='inmediato', fijo='YO2_PIDNAME_PLAYERS=0 lo apaga', origen='oficial'),
    F('hub.RIVAL_CARD', ('mod', 'pvp_hub', 'RIVAL_CARD'), True, env='YO2_RIVAL_CARD', grupo='hub', desc='Ficha del rival (LV, victorias) en «en duelo»', efecto='inmediato', fijo='YO2_RIVAL_CARD=0 lo apaga', origen='oficial'),
    F('hub.NPC_DUEL_STATE', ('mod', 'pvp_hub', 'NPC_DUEL_STATE'), True, env='YO2_NPC_DUEL_STATE', grupo='hub', desc='Pose «en duelo» también contra NPC', efecto='próximo duelo NPC', fijo='YO2_NPC_DUEL_STATE=1 lo enciende', origen='oficial'),
    F('hub.AREA_BLOCKS', ('mod', 'pvp_hub', 'AREA_BLOCKS'), True, env='YO2_AREA_BLOCKS', grupo='hub', desc='Un bloque por mapa (cambiarlo con gente dentro los descoloca)', efecto='al reiniciar', reiniciar=True, fijo='YO2_AREA_BLOCKS=0 lo apaga', origen='oficial'),
    F('pvp.DUELLOG_RECORD', ('mod', 'pvp_match', 'PVP_DUELLOG_RECORD'), True, grupo='pvp', desc='Grabar repeticiones de los PvP', efecto='próximo duelo', fijo='pvp_match.py PVP_DUELLOG_RECORD', origen='oficial'),
    F('pvp.DUELLOG_FINISH', ('mod', 'pvp_match', 'PVP_DUELLOG_FINISH'), True, grupo='pvp', desc='Cierre del registro de duelo (0x6010/0x6012)', efecto='próximo duelo', fijo='pvp_match.py PVP_DUELLOG_FINISH', origen='oficial'),
    F('torneos.TOURNAMENT_ON', ('mod', 'TO', 'TOURNAMENT_ON'), True, env='YO2_TOURNAMENT', grupo='torneos', desc='Torneos CHAMPIONSHIP con datos', efecto='inmediato', fijo='research/torneos/proposal.py TOURNAMENT_ON (YO2_TOURNAMENT=0 lo apaga)', origen='oficial'),
    F('torneos.EVENTS_ON', ('mod', 'TO', 'EVENTS_ON'), True, env='YO2_EVENTS', grupo='torneos', desc='Eventos (EVENTINFO) al entrar en el mapa', efecto='al entrar en un mapa', fijo='research/torneos/proposal.py EVENTS_ON (YO2_EVENTS=0 lo apaga)', origen='oficial'),
    F('torneos.AUTO_CLOCK', ('mod', 'TO', 'AUTO_CLOCK'), True, env='YO2_AUTO_CLOCK', grupo='torneos', desc='Reloj automático del torneo (hilo que se crea al arrancar)', efecto='al reiniciar', reiniciar=True, fijo='research/torneos/proposal.py AUTO_CLOCK (YO2_AUTO_CLOCK=0 lo apaga)', origen='?'),

    # ---------------------------------------------------------------- traductor: DEFAULT_CFG (las 8 del panel llevan su efecto)
    F("duelo.lp", ("trcfg", 'lp'), 8000, env=None, grupo="duelo", desc='Vidas iniciales de cada jugador', origen='oficial'),
    F("duelo.kind_main", ("trcfg", 'kind_main'), 3, env=None, grupo="duelo", desc='Clase de la petición de fase principal (SELECT_IDLECMD)', origen='oficial'),
    F("duelo.kind_battle", ("trcfg", 'kind_battle'), 3, env=None, grupo="duelo", desc='Clase de la petición de batalla (SELECT_BATTLECMD)', origen='oficial'),
    F("duelo.kind_select", ("trcfg", 'kind_select'), 3, env=None, grupo="duelo", desc='Clase de las peticiones de elegir carta / tributo / cadena', origen='oficial'),
    F("duelo.select_bit", ("trcfg", 'select_bit'), 11, env=None, grupo="duelo", desc='Bit de las cartas elegibles en SELECT_CARD/TRIBUTE (clic directo)', origen='oficial'),
    F("duelo.target_bit", ("trcfg", 'target_bit'), 0, env=None, grupo="duelo", desc='Bit para elegir el objetivo de un ataque (menú «Atacar»)', origen='oficial'),
    F("duelo.yesno", ("trcfg", 'yesno'), 'dialog', env=None, grupo="duelo", desc='SÍ/NO: diálogo del cliente o decide el bot (dialog | auto)', origen='oficial'),
    F("duelo.hide_npc", ("trcfg", 'hide_npc'), True, env=None, grupo="duelo", desc='Tapar mano, mazo, extra y boca abajo de la NPC con cartas tapadera', origen='oficial'),
    F("duelo.token_serial_base", ("trcfg", 'token_serial_base'), None, env=None, grupo="duelo", desc='Serie del primer token de cada jugador (None = cartas del mazo + extra + 1)', origen='oficial'),
    F("duelo.finish_mode", ("trcfg", 'finish_mode'), 0, env=None, grupo="duelo", desc='P1 del 0x6202 tipo 0x4d (modo de match)', origen='oficial'),
    F("duelo.npc_phrases", ("trcfg", 'npc_phrases'), False, env=None, grupo="duelo", desc='La NPC habla (0x60/0x62/0x03; requiere el byte NPC de 0x43a0)', origen='oficial'),
    F("duelo.pile_look", ("trcfg", 'pile_look'), True, env=None, grupo="duelo", desc='«Ver detalles» en cementerio, desterradas y extra propio', origen='oficial'),
    F("duelo.stat_sync", ("trcfg", 'stat_sync'), True, env=None, grupo="duelo", desc='Imponer el ATK/DEF de ocgcore (0x36/0x37 + EFFECT_SET_STATS)', origen='oficial'),
    F("duelo.stat_sync_turn", ("trcfg", 'stat_sync_turn'), True, env='YO2_STAT_SYNC_TURN', grupo="duelo", desc='ATK/DEF de ocgcore también al cambiar de fase/turno y al cerrar un lote sin decisión propia (§32)', efecto='próximo duelo', tipo='bool', origen='oficial', nota='el cliente oficial enseñaba el ATK normal al acabar el efecto (cómo lo hacía caducar: P); quita el par EFFECT_SET_STATS de lo que caduca sin mensaje (Shrink, Rush Recklessly…) en la vista que no decide; apagado = como antes de §32'),
    F("duelo.summon_done", ("trcfg", 'summon_done'), True, env='YO2_SUMMON_DONE', grupo="duelo", desc='Aviso de invocación terminada', origen='oficial'),
    F("duelo.summon_kind_fx", ("trcfg", 'summon_kind_fx'), True, env='YO2_SUMMON_KIND_FX', grupo="duelo", desc='Efecto según el tipo de invocación', origen='oficial'),
    F("duelo.token_vanish", ("trcfg", 'token_vanish'), True, env='YO2_TOKEN_VANISH', grupo="duelo", desc='Los tokens desaparecen con su animación', origen='oficial'),
    F("duelo.yesno_card", ("trcfg", 'yesno_card'), True, env='YO2_YESNO_CARD', grupo="duelo", desc='SÍ/NO con la carta', nota='10-10: encendido otra vez (NOTES §29, carrera 0x6610/fusión arreglada); YO2_YESNO_CARD=0 lo apaga', origen='oficial'),
    F("duelo.yesno_sys_text", ("trcfg", 'yesno_sys_text'), True, env='YO2_YESNO_SYS_TEXT', grupo="duelo", desc='SÍ/NO sin carta con el texto del sistema de ocgcore (§25)', origen='oficial'),
    F("duelo.battle_damage_real", ("trcfg", 'battle_damage_real'), True, env='YO2_BATTLE_DAMAGE_REAL', grupo="duelo", desc='Daño de batalla real (de ocgcore)', origen='oficial'),
    F("duelo.lp_reconcile", ("trcfg", 'lp_reconcile'), True, env='YO2_LP_RECONCILE', grupo="duelo", desc='Reconciliación del marcador de LP del cliente con ocgcore (§24)', origen='inventado'),
    F("duelo.lp_resync", ("trcfg", 'lp_resync'), False, env='YO2_LP_RESYNC', grupo="duelo", desc='Reenviar los LP tras cada cambio', nota='apagado por defecto en master (lo sustituye lp_reconcile, §24); YO2_LP_RESYNC=1 lo enciende', origen='inventado'),
    F("duelo.lp_set_direct", ("trcfg", 'lp_set_direct'), True, env='YO2_LP_SET_DIRECT', grupo="duelo", desc='MSG_LPUPDATE de ocgcore (Duel.SetLP, reinicio) -> 0x26 con el LP absoluto en ese momento (§31)', efecto='próximo duelo', tipo='bool', origen='tecnico', nota='con 0x27 + 0x26 de los dos si el marcador aún contaba un daño/cura de ese jugador; lp_reconcile sigue como red de seguridad; apagado = como antes de §31'),
    F("duelo.yesno_text", ("trcfg", 'yesno_text'), 269, env=None, grupo="duelo", desc='Texto del SÍ/NO de cadena (id de texto)', origen='oficial'),
    F("duelo.chain_dialog", ("trcfg", 'chain_dialog'), 3, env=None, grupo="duelo", desc='Tipo del diálogo de cadena (3 = respeta «Mensaje de activación»)', origen='oficial'),
    F("duelo.chain_importance", ("trcfg", 'chain_importance'), 2, env=None, grupo="duelo", desc='P2 del diálogo de cadena tipo 3', origen='oficial'),
    F("duelo.chain_importance_auto", ("trcfg", 'chain_importance_auto'), True, env=None, grupo="duelo", desc='Importancia 2 si la cadena la abrió el rival', origen='oficial'),
    F("duelo.forced_auto_single", ("trcfg", 'forced_auto_single'), True, env=None, grupo="duelo", desc='Cadena forzada con una sola opción: se contesta sola', origen='oficial'),
    F("duelo.chain_card_name", ("trcfg", 'chain_card_name'), False, env=None, grupo="duelo", desc='Nombre de la carta en el aviso de cadena', origen='inventado'),
    F("duelo.chain_priority", ("trcfg", 'chain_priority'), 'oficial', env=None, grupo="duelo", desc='Prioridad de los avisos de cadena (oficial = la de Konami por suceso; None = apagado)', efecto='próximo duelo', tipo=('oficial', 'evento', 'spe', None), origen='oficial'),
    F("duelo.chain_event_text", ("trcfg", 'chain_event_text'), True, env='YO2_CHAIN_EVENT_TEXT', grupo="duelo", desc='Texto del suceso en el aviso de cadena (como el oficial)', efecto='próximo duelo', tipo='bool', nota='10-10: encendido otra vez (§29); YO2_CHAIN_EVENT_TEXT=0 lo apaga', origen='oficial'),
    F("duelo.chain_none_prompt", ("trcfg", 'chain_none_prompt'), False, env='YO2_CHAIN_NONE_PROMPT', grupo="duelo", desc='Aviso «no hay ninguna carta que se pueda activar» (204/205) en cada ventana vacía, como el oficial (§30)', efecto='próximo duelo', tipo='bool', origen='oficial', nota='10-10, 683fc3b0: formato C (duellogs), cuándo P; apagado hasta probarlo con el cliente real'),
    F("duelo.chain_one_name", ("trcfg", 'chain_one_name'), False, enhanced=True, env='YO2_CHAIN_ONE_NAME', grupo="duelo", desc='Aviso de cadena con el nombre de UNA sola carta activable (§24)', origen='inventado'),
    F("duelo.chain_link_detail", ("trcfg", 'chain_link_detail'), True, env='YO2_CHAIN_LINK_DETAIL', grupo="duelo", desc='El aviso de cadena 7/8 deja la carta del eslabón en el panel de detalle (§26)', origen='oficial'),
    F("duelo.chain_priority_hand", ("trcfg", 'chain_priority_hand'), True, env='YO2_CHAIN_PRIORITY_HAND', grupo="duelo", desc='Avisos de cadena también para monstruos de la mano', origen='inventado'),
    F("duelo.chain_link_text", ("trcfg", 'chain_link_text'), True, env=None, grupo="duelo", desc='Texto «… está activado» en el aviso de cadena', efecto='próximo duelo', origen='oficial'),
    F("duelo.chain_yes_direct", ("trcfg", 'chain_yes_direct'), False, env=None, grupo="duelo", desc='Tras el SÍ de la cadena, atajo directo (apagado = 1:1)', origen='oficial'),
    F("duelo.pick_panel", ("trcfg", 'pick_panel'), False, env=None, grupo="duelo", desc='Panel de elección de cartas', origen='oficial'),
    F("duelo.activate_flash", ("trcfg", 'activate_flash'), False, env=None, grupo="duelo", desc='Destello 0x30 al activar una carta del campo', origen='oficial'),
    F("duelo.attack_aim", ("trcfg", 'attack_aim'), True, env=None, grupo="duelo", desc='Espada de ataque que sigue al ratón', origen='oficial'),
    F("duelo.aim_target_bit", ("trcfg", 'aim_target_bit'), 11, env=None, grupo="duelo", desc='Bit de las cartas objetivo con la espada (11 = clic directo)', origen='oficial'),
    F("duelo.look_modes", ("trcfg", 'look_modes'), {14: 1, 15: 4, 12: 1}, env=None, grupo="duelo", desc='Zonas con «Ver detalles» (zona -> modo)', origen='oficial'),
    F("duelo.list_picks", ("trcfg", 'list_picks'), True, env=None, grupo="duelo", desc='Elecciones en lista', origen='oficial'),
    F("duelo.card_link", ("trcfg", 'card_link'), True, env=None, grupo="duelo", desc='Vínculo carta → carta (equipos, Círculo…)', efecto='próximo duelo', origen='oficial'),
    F("duelo.link_kind_equip", ("trcfg", 'link_kind_equip'), 10, env=None, grupo="duelo", desc='Tipo de vínculo para equipos', origen='oficial'),
    F("duelo.link_kind_target", ("trcfg", 'link_kind_target'), 9, env=None, grupo="duelo", desc='Tipo de vínculo que no es equipo', origen='oficial'),
    F("duelo.link_target_anim", ("trcfg", 'link_target_anim'), True, env=None, grupo="duelo", desc='Animación de objetivo (0x42) o silencioso (0x36)', origen='oficial'),
    F("duelo.target_lockon", ("trcfg", 'target_lockon'), True, env=None, grupo="duelo", desc='Marca de objetivo al activar', efecto='próximo duelo', origen='oficial'),
    F("duelo.counters", ("trcfg", 'counters'), True, env=None, grupo="duelo", desc='Contadores visibles', efecto='próximo duelo', origen='oficial'),
    F("duelo.swap_cmd", ("trcfg", 'swap_cmd'), True, env=None, grupo="duelo", desc='Intercambio de criaturas (0x3e)', efecto='próximo duelo', origen='oficial'),
    F("duelo.field_move_3d", ("trcfg", 'field_move_3d'), True, env=None, grupo="duelo", desc='Cambio de control / mover de zona (0x3d)', efecto='próximo duelo', origen='oficial'),
    F("duelo.toss_anim", ("trcfg", 'toss_anim'), True, env=None, grupo="duelo", desc='Animación de moneda y dado', efecto='próximo duelo', origen='oficial'),
    F("duelo.coin_call", ("trcfg", 'coin_call'), 1, env=None, grupo="duelo", desc='Resultado resaltado al caer la moneda', origen='oficial'),
    F("duelo.tribute_anim", ("trcfg", 'tribute_anim'), True, env=None, grupo="duelo", desc='Animación de tributo', origen='oficial'),
    F("duelo.tribute_mark", ("trcfg", 'tribute_mark'), False, env=None, grupo="duelo", desc='Marca 0x4a antes de cada 0x46 de tributo', origen='oficial'),
    F("duelo.fusion_anim", ("trcfg", 'fusion_anim'), True, env='YO2_FUSION_ANIM', grupo="duelo", desc='Animación de fusión', origen='oficial'),
    F("duelo.turn_timer", ("trcfg", 'turn_timer'), 180, env='YO2_TURN_TIMER', grupo="duelo", desc='Segundos por turno (0 = sin límite)', origen='oficial'),
    F("duelo.turn_limit", ("trcfg", 'turn_limit'), True, env='YO2_LIMITE_TIEMPO', grupo="duelo", desc='Límite de tiempo por turno activo', origen='oficial'),
    F("duelo.tiempo_gunslinger", ("trcfg", 'tiempo_gunslinger'), 60.0, env='YO2_TIEMPO_GUNSLINGER', grupo="duelo", desc='Segundos por jugada en GUNSLINGER', origen='oficial'),
    F("duelo.tiempo_respuesta", ("trcfg", 'tiempo_respuesta'), 180.0, env='YO2_TIEMPO_RESPUESTA', grupo="duelo", desc='Segundos para responder a una petición', origen='inventado'),
    F("duelo.tiempo_margen", ("trcfg", 'tiempo_margen'), 3.0, env='YO2_TIEMPO_MARGEN', grupo="duelo", desc='Margen en segundos del límite de tiempo', origen='tecnico'),
    F("duelo.retry_auto", ("trcfg", 'retry_auto'), 2, env=None, grupo="duelo", desc='Reintentos automáticos tras un error del motor', origen='tecnico'),
    F("duelo.field_change", ("trcfg", 'field_change'), True, env='YO2_FIELD_CHANGE', grupo="duelo", desc='Cambio de campo (Field Spell) animado', origen='oficial'),
    F("duelo.position_dialog", ("trcfg", 'position_dialog'), True, env=None, grupo="duelo", desc='Diálogo de posición al invocar', origen='oficial'),
    F("duelo.announce_dialogs", ("trcfg", 'announce_dialogs'), True, env=None, grupo="duelo", desc='Diálogos de anuncio', origen='oficial'),
    F("duelo.option_dialog", ("trcfg", 'option_dialog'), True, env='YO2_OPTION_DIALOG', grupo="duelo", desc='Diálogo de opciones del motor', origen='oficial'),
    F("duelo.sort_list", ("trcfg", 'sort_list'), True, env='YO2_SORT_LIST', grupo="duelo", desc='Ordenar cartas (lista)', origen='oficial'),
    F("duelo.counter_dialog", ("trcfg", 'counter_dialog'), True, env='YO2_COUNTER_DIALOG', grupo="duelo", desc='Diálogo de contadores', origen='?'),
    F("duelo.hand_limit_dialog", ("trcfg", 'hand_limit_dialog'), True, env='YO2_HAND_LIMIT_DIALOG', grupo="duelo", desc='Diálogo de descarte por límite de mano', origen='oficial'),
    F("duelo.turn_counter", ("trcfg", 'turn_counter'), True, env='YO2_TURN_COUNTER', grupo="duelo", desc='Contador de turnos de las cartas que lo usan', origen='oficial'),
    F("duelo.declare_hint", ("trcfg", 'declare_hint'), True, env='YO2_DECLARE_HINT', grupo="duelo", desc='Pista al declarar carta / tipo', origen='oficial'),
    F("duelo.ritual_sum", ("trcfg", 'ritual_sum'), True, env='YO2_RITUAL_SUM', grupo="duelo", desc='Invocación por Ritual: el jugador elige los sacrificios con las ventanas del oficial (estrellas, marcas, lista del mazo); apagada = decide bot.ts', nota='12-10 (§33): necesita el parche de SELECT_SUM de duelcore (YO2_PARCHE_SUM)', origen='oficial'),
    F("duelo.turn_counter_player", ("trcfg", 'turn_counter_player'), True, env='YO2_TURN_COUNTER_PLAYER', grupo="duelo", desc='Cuenta de Final Countdown / Crush Card Virus / Gold Sarcophagus… al cliente (0x3b con P2, como el oficial; el cliente 080912 no la pinta)', nota='12-10 (§34)', origen='oficial'),

    # ---------------------------------------------------------------- duelo: ocg_duel, lockstep, run.py
    F('ocg.USE_BRAIN', ('mod', 'OCG', 'USE_BRAIN'), True, grupo='duelo', desc='La NPC juega con el cerebro (research/ia); sin él, bot.ts', origen='inventado'),
    F('ocg.SEND_PAUSE', ('mod', 'OCG', 'SEND_PAUSE'), 0.05, env='YO2_DUEL_PAUSE', grupo='duelo', desc='Pausa en segundos entre mensajes seguidos del duelo', nota='YO2_DUEL_PAUSE solo para la instancia de prueba', origen='tecnico'),
    F('ocg.NPC_THINK', ('mod', 'OCG', 'NPC_THINK'), 0.8, grupo='duelo', desc='Segundos antes de cada jugada visible de la NPC', origen='?'),
    F('ocg.CORE_ERROR_AUTO', ('mod', 'OCG', 'CORE_ERROR_AUTO'), 1, grupo='duelo', desc='Errores de duelcore que se dejan decidir a bot.ts antes de cerrar el duelo', origen='tecnico'),
    F('duelo.npc_janken_azar', ('moddict', 'LS', 'DUEL_CFG', 'npc_janken_azar'), True, env='YO2_JANKEN_NPC_AZAR', grupo='duelo', desc='Piedra-papel-tijera contra la NPC: mano al azar', nota='extra; en instancias de prueba (YO2_PVP_TEST=1) el azar va apagado salvo que se pida', origen='inventado'),
    F('duelo.npc_janken_elige', ('moddict', 'LS', 'DUEL_CFG', 'npc_elige'), 'primero', env='YO2_NPC_JANKEN_ELIGE', grupo='duelo', desc='Qué elige la NPC cuando gana el piedra-papel-tijera (primero | segundo | azar)', tipo=('primero', 'segundo', 'azar'), nota='extra', origen='oficial'),
    F('duelo.CLOSE_OLD_DUEL_SOCK', ('ns', 'CLOSE_OLD_DUEL_SOCK'), False, grupo='duelo', desc='Cerrar desde el servidor el socket de duelo viejo al llegar uno nuevo (DUDOSO)', origen='tecnico'),
    F('duelo.DUEL_MODE', ('ns', 'DUEL_MODE'), 'ocg', grupo='duelo', desc='Motor del duelo contra NPC', origen='tecnico'),
    F('ocg.DUEL_RULE', ('mod', 'OCG', 'DUEL_RULE'), 'MR1', env='YO2_DUEL_RULE', grupo='duelo', desc='Regla de duelo de ocgcore (MR1 = reglas de 2008; MR5 = actuales)', nota='extra (08-10, §27, OK del owner)', origen='oficial'),
    F('ocg.NPC_DECKS_EXTRA', ('mod', 'OCG', 'NPC_DECKS_EXTRA'), False, env='YO2_NPC_DECKS_EXTRA', grupo='duelo', desc='Mazos PROPIOS (deducidos, no oficiales) para las 26 NPC sin receta en el cliente; apagada = esas NPC juegan con una copia del mazo del jugador', efecto='próximo duelo NPC', fijo='YO2_NPC_DECKS_EXTRA=1 la enciende', nota='13-10 (agente D): research/ia/npc_decks_extra.json, hecho por research/ia/npc_mazos_extra.py; ninguna repetición oficial trae duelos de NPC', origen='inventado'),
    F('duelo.DECK_CHECK', ('env', 'YO2_DECK_CHECK'), None, env='YO2_DECK_CHECK', grupo='duelo', desc='Comprobación del mazo que manda el cliente (off | avisar | bloquear; sin poner = avisar, con YO2_PVP_TEST = off)', nota='extra; liga de entorno: el valor vivo es el texto de la variable (seguridad_duelo/mazo.py)', origen='tecnico'),
    F('duelo.DUEL_RESERVA', ('ns', 'DUEL_RESERVA'), 'vigilar', env='YO2_DUEL_RESERVA', grupo='duelo', desc='Modo de la reserva de duelo (seguridad_duelo)', nota='extra', origen='tecnico'),

    # ---------------------------------------------------------------- lobby (research/lobby)
    F('lobby.TPPRESENT', ('mod', 'LP', 'TPPRESENT'), False, env='YO2_TPPRESENT', grupo='lobby', desc='Premio de ranking TP (0x8301/0x8302)', nota='sigue False hasta que el owner elija la carta del premio TP (con TP_PRESENT_CARD=0 el cliente puede cerrarse)', origen='oficial'),
    F('lobby.TP_PRESENT_CARD', ('mod', 'LP', 'TP_PRESENT_CARD'), 0, grupo='lobby', desc='Carta del premio de ranking TP', nota='sin decidir (D): auto_replies manda 0', origen='oficial'),
    F('lobby.STARTUP_PRESENT', ('mod', 'LP', 'STARTUP_PRESENT'), True, env='YO2_STARTUP_PRESENT', grupo='lobby', desc='Regalo de inicio (0x8401/0x8402)', origen='oficial'),
    F('lobby.NPC_CHALLENGE', ('mod', 'LP', 'CHALLENGE_DELAY'), None, env='YO2_NPC_CHALLENGE', grupo='lobby', desc='Una NPC famosa reta a tu sala si estás solo en el bloque (segundos; None = nunca)', origen='inventado', nota='10-10: el owner confirma que en el original ningún NPC te reta; apagado'),
    F('lobby.AUTODUEL', ('mod', 'LP', 'AUTODUEL'), True, env='YO2_AUTODUEL', grupo='lobby', desc='Autoduelo: sala con mstat 1 = estado 0x66 para los demás', origen='oficial'),
    F('lobby.AUTODUEL_MATCH', ('mod', 'LP', 'AUTODUEL_MATCH'), True, env='YO2_AUTODUEL_MATCH', grupo='lobby', desc='El servidor empareja a dos jugadores en autoduelo', origen='?'),
    F('lobby.RESETAUTOWAIT', ('mod', 'LP', 'RESETAUTOWAIT'), True, env='YO2_RESETAUTOWAIT', grupo='lobby', desc='0x42a0 RESETAUTOWAIT -> 0x42a2', origen='oficial'),
    F('lobby.USBDPAUTH_REFUSE', ('mod', 'LP', 'USBDPAUTH_REFUSE'), True, env='YO2_USBDPAUTH_REFUSE', grupo='lobby', desc='0x8203 REQUSBDPAUTH se rechaza (no hay servidor USB)', origen='oficial'),
    F('lobby.OFFER_TIMEOUT', ('mod', 'LP', 'OFFER_TIMEOUT'), 30.0, env='YO2_PVP_OFFER_TIMEOUT', grupo='lobby', desc='Segundos que vive un reto PvP sin respuesta', origen='?'),

    # ---------------------------------------------------------------- hub (pvp_hub)
    F('hub.LIST_SELF', ('mod', 'pvp_hub', 'LIST_SELF'), True, grupo='hub', desc='LIST2..5 con el propio jugador primero, como LIST1', origen='oficial'),
    F('hub.REANNOUNCE_ON_FIRST_POS', ('mod', 'pvp_hub', 'REANNOUNCE_ON_FIRST_POS'), False, grupo='hub', desc='Reanunciar (0x3112 op 1) al que llega al mandar su primera 0x5300', origen='oficial'),
    F('hub.LOGIN_ESTRICTO', ('mod', 'pvp_hub', 'LOGIN_ESTRICTO'), True, env='YO2_LOGIN_ESTRICTO', grupo='hub', desc='Una cuenta que no existe recibe −240 (login 1:1) en vez de crearse', origen='oficial'),
    F('hub.LOGIN_EN_USO', ('mod', 'pvp_hub', 'LOGIN_EN_USO'), True, env='YO2_LOGIN_EN_USO', grupo='hub', desc='−242 «ya está en uso» si la cuenta tiene otra conexión viva', nota='extra', origen='oficial'),
    F('hub.LOGIN_EN_USO_VIVA_S', ('mod', 'pvp_hub', 'LOGIN_EN_USO_VIVA_S'), 90.0, env='YO2_LOGIN_EN_USO_VIVA_S', grupo='hub', desc='Segundos sin mensajes tras los que una conexión ya no cuenta como viva', nota='extra', origen='tecnico'),
    F('hub.LIST1_STATE', ('mod', 'pvp_hub', 'LIST1_STATE'), True, env='YO2_LIST1_STATE', grupo='hub', desc='LIST1 lleva el estado real del jugador (azul, «Retar», pose)', nota='extra', origen='oficial'),
    F('hub.LIST2_DUEL_23', ('mod', 'pvp_hub', 'LIST2_DUEL_23'), True, env='YO2_LIST2_DUEL_23', grupo='hub', desc='LIST2 «en duelo» con 23 B (no 25)', nota='extra', origen='oficial'),
    F('hub.MAX_ACCOUNTS', ('mod', 'pvp_hub', 'MAX_ACCOUNTS'), 64, env='YO2_MAX_SESIONES', grupo='hub', desc='Tope de sesiones en memoria a la vez', nota='extra', origen='tecnico'),
    F('hub.SESSION_RELEASE_S', ('mod', 'pvp_hub', 'SESSION_RELEASE_S'), 120.0, env='YO2_SESION_LIBERAR_S', grupo='hub', desc='Segundos tras los que se libera una sesión sin conexiones', nota='extra', origen='tecnico'),

    # ---------------------------------------------------------------- pvp (pvp_match)
    F('pvp.PVP_SVRINFO', ('mod', 'pvp_match', 'PVP_SVRINFO'), True, grupo='pvp', desc='Aviso 0x649b al que se queda (rendición, desconexión)', origen='oficial'),
    F('pvp.PVP_MATCH', ('mod', 'pvp_match', 'PVP_MATCH'), '1', env='YO2_PVP_MATCH', grupo='pvp', desc='Match al mejor de 3 (1; 0 = como antes; cerrar = una partida y fin)', efecto='próximo duelo', nota='10-10, 49751d65', origen='oficial'),
    F('pvp.MATCH_WAIT', ('mod', 'pvp_match', 'MATCH_WAIT'), 300.0, env='YO2_PVP_MATCH_WAIT', grupo='pvp', desc='Segundos de espera entre partidas de un match', efecto='próximo duelo', origen='tecnico'),
    F('pvp.PVP_WATCH', ('mod', 'pvp_match', 'PVP_WATCH'), True, env='YO2_WATCH', grupo='pvp', desc='Espectador de PvP (lado pvp_match de lobby.WATCH)', origen='oficial'),
    F('pvp.WATCH_SEND_PREP', ('mod', 'pvp_match', 'WATCH_SEND_PREP'), False, env='YO2_WATCH_PREP', grupo='pvp', desc='Al espectador, un 0x600c antes del 0x6006 (vidas)', nota='NUESTRO, no 1:1', origen='inventado'),
    F('pvp.TEST_AUTO', ('mod', 'pvp_match', 'TEST_AUTO'), False, env='YO2_PVP_TEST', grupo='pvp', desc='Modo de prueba (fake_client pvp): solo instancias de prueba', deploy=True, origen='tecnico'),
    F('pvp.ATTACH_TIMEOUT', ('mod', 'pvp_match', 'ATTACH_TIMEOUT'), 90.0, env='YO2_PVP_ATTACH_TIMEOUT', grupo='pvp', desc='Segundos para que los dos jugadores conecten al duelo', origen='tecnico'),
    F('pvp.REPLAY_ARCHIVE', ('mod', 'pvp_match', 'REPLAY_ARCHIVE'), True, env='YO2_REPLAY_ARCHIVE', grupo='pvp', desc='El servidor guarda su copia de cada duelo (panel alojado)', nota='extra', origen='tecnico'),

    # ---------------------------------------------------------------- equipos, intercambio, torneos
    F('equipos.NAME_CASEFOLD', ('mod', 'EQ', 'NAME_CASEFOLD'), True, grupo='equipos', desc='«Dragones» y «dragones» son el mismo nombre de equipo', origen='oficial'),
    F('equipos.APPY_INVITER_FIRST', ('mod', 'EQ', 'APPY_INVITER_FIRST'), True, grupo='equipos', desc='Orden de los avisos de la invitación a un equipo', origen='oficial'),
    F('equipos.ANNOUNCE_4571_BLOCK', ('mod', 'EQ', 'ANNOUNCE_4571_BLOCK'), True, grupo='equipos', desc='0x4571 a los del bloque (actualiza su LIST5)', origen='oficial'),
    F('equipos.ANNOUNCE_4571_SELF', ('mod', 'EQ', 'ANNOUNCE_4571_SELF'), True, grupo='equipos', desc='0x4571 al propio jugador', origen='oficial'),
    F('equipos.JOIN_NOTICE_TO_JOINER', ('mod', 'EQ', 'JOIN_NOTICE_TO_JOINER'), True, grupo='equipos', desc='0x4561 también al que entra al equipo', origen='oficial'),
    F('equipos.PATCH_HUB_LIST5', ('mod', 'EQ', 'PATCH_HUB_LIST5'), True, grupo='equipos', desc='install() sustituye HUB.list5_body por el de equipos', reiniciar=True, origen='tecnico'),
    F('intercambio.ADDCARD_REJECT_1010', ('mod', 'TP', 'ADDCARD_REJECT_1010'), False, grupo='intercambio', desc='Rechazar con −1010 un ADDCARD que el baúl no cubre', origen='oficial'),
    F('intercambio.RESET_READY_ON_CHANGE', ('mod', 'TP', 'RESET_READY_ON_CHANGE'), False, grupo='intercambio', desc='Un cambio de cartas quita el «listo» de los dos', origen='oficial'),
    F('intercambio.CLEAR_STATUS_AFTER_NO', ('mod', 'TP', 'CLEAR_STATUS_AFTER_NO'), False, grupo='intercambio', desc='Tras un «no», apagar el «listo» del compañero', origen='oficial'),
    F('torneos.AUTO_ENTRY', ('mod', 'TO', 'AUTO_ENTRY'), True, grupo='torneos', desc='Estar en el cuadro durante la inscripción = inscrito', origen='?'),
    F('torneos.BIT80_ALIVE', ('mod', 'TO', 'BIT80_ALIVE'), True, grupo='torneos', desc='Bit 0x80 de la página = sigue vivo', origen='oficial'),
    F('torneos.NOSHOW_ADVANCE_SEAT0', ('mod', 'TO', 'NOSHOW_ADVANCE_SEAT0'), True, grupo='torneos', desc='Si no se presenta ninguno, pasa el asiento menor', origen='?'),
    F('torneos.REPAIR_ON_DRAW', ('mod', 'TO', 'REPAIR_ON_DRAW'), True, grupo='torneos', desc='Re-emparejar tras el empate del piedra-papel-tijera', origen='?'),

    # ---------------------------------------------------------------- tienda y cuentas nuevas (run.py)
    F('tienda.SHOP_SELL_PRIZE_PACKS', ('ns', 'SHOP_SELL_PRIZE_PACKS'), False, grupo='tienda', desc='Vender también los packs de premio (tipo 2)', origen='oficial'),
    F('tienda.SHOP_DECK_RECIPE', ('ns', 'SHOP_DECK_RECIPE'), True, grupo='tienda', desc='Comprar un mazo pone su receta en el perfil (PBIN bit + 1)', origen='oficial'),
    F('tienda.SHOP_DECK_ITEMGET', ('ns', 'SHOP_DECK_ITEMGET'), False, grupo='tienda', desc='Al comprar un mazo, mandar además 0x5205 con ese bit', origen='oficial'),
    F('tienda.SHOP_AVATAR_ITEMGET', ('ns', 'SHOP_AVATAR_ITEMGET'), False, grupo='tienda', desc='Al comprar una prenda, mandar además 0x5205', origen='oficial'),
    F('tienda.DEFAULT_COUNTRY', ('ns', 'DEFAULT_COUNTRY'), 'ES', env='YO2_COUNTRY', grupo='tienda', desc='País (2 letras) de los personajes nuevos', origen='?'),
    F('tienda.START_MP', ('ns', 'START_MP'), 10000, grupo='tienda', desc='Millas (MP) de un personaje nuevo', nota='también PP.START_MP (mismo valor)', origen='?'),
    F('tienda.START_DPP', ('ns', 'START_DPP'), 10, env='YO2_DP_INICIO', grupo='tienda', desc='Puntos DUELPASS de un personaje nuevo', nota='también PP.START_DPP', origen='?'),

    # ---------------------------------------------------------------- persistencia y DUELPASS (research/persistencia)
    F('persistencia.START_LEVEL', ('mod', 'PP', 'START_LV'), 1, env='YO2_START_LEVEL', grupo='persistencia', desc='Nivel (LV) de un personaje nuevo, 1-99. 1 = como el original (con LV 0-1 el cliente solo enseña las Salas de Principiantes); 2 o más = los nuevos entran en todas las salas', nota='12-10: elección del anfitrión en el lanzador («Starting level»); solo afecta a los personajes que se crean después', reiniciar=True, origen='inventado'),
    F('persistencia.COUNTRY_BY_LANG', ('mod', 'PP', 'COUNTRY_BY_LANG'), True, env='YO2_COUNTRY_BY_LANG', grupo='persistencia', desc='País (bandera) de un personaje nuevo según el idioma del cliente (0x4100): JP US DE IT FR ES KR; si no, DEFAULT_COUNTRY', nota='12-10: en el oficial era el país de la cuenta de Konami (no reproducible); los personajes de antes no cambian', origen='inventado'),
    F('persistencia.TRUNK_START', ('mod', 'PP', 'TRUNK_START'), 'all3', env='YO2_TRUNK_START', grupo='persistencia', desc='Baúl inicial de un jugador nuevo (deck | all3 | empty)', tipo=('deck', 'all3', 'empty'), nota='run.py hace os.environ.setdefault("YO2_TRUNK_START", "all3") antes de cargar PP: el default de PP (deck) NO es el de un servidor arrancado con run.py', origen='?'),
    F('persistencia.TP_RANK', ('mod', 'PP', 'TP_RANK'), False, env='YO2_TP_RANK', grupo='persistencia', desc='Puesto de ranking TP en 0x3012', nota='sigue False hasta que el owner elija la carta del premio TP', origen='oficial'),
    F('persistencia.NPC_IN_RECORD', ('mod', 'PP', 'NPC_IN_RECORD'), True, env='YO2_NPC_IN_RECORD', grupo='persistencia', desc='Los duelos contra NPC cuentan en Victorias/Derrotas', origen='?'),
    F('persistencia.NPC_MP_WIN', ('moddict', 'PP', 'NPC_MP_REWARD', 'win'), 0, env='YO2_NPC_MP_WIN', grupo='persistencia', desc='Millas por ganar a una NPC', origen='oficial'),
    F('persistencia.NPC_MP_LOSE', ('moddict', 'PP', 'NPC_MP_REWARD', 'lose'), 0, env='YO2_NPC_MP_LOSE', grupo='persistencia', desc='Millas por perder contra una NPC', origen='oficial'),
    F('persistencia.NPC_MP_DRAW', ('moddict', 'PP', 'NPC_MP_REWARD', 'draw'), 0, env='YO2_NPC_MP_DRAW', grupo='persistencia', desc='Millas por empatar con una NPC', origen='oficial'),
    F('persistencia.NPC_DP_COST', ('mod', 'PP', 'NPC_DP_COST'), 0, env='YO2_NPC_DP_COST', grupo='persistencia', desc='Coste en DP del duelo NPC (viejo; el de verdad es DUEL_DP_COST)', origen='tecnico'),
    F('persistencia.NPC_THRESHOLD', ('mod', 'PP', 'CHAIN_THRESHOLD'), 4, env='YO2_NPC_THRESHOLD', grupo='persistencia', desc='Umbral de victorias para el premio de las NPC (mínimo 2)', origen='?'),
    F('persistencia.DUEL_DP_COST', ('mod', 'PP', 'DUEL_DP_COST'), 1, env='YO2_DP_COSTE', grupo='persistencia', desc='Puntos DUELPASS que cuesta un duelo', origen='oficial'),
    F('persistencia.DP_NPC_BLOCK', ('mod', 'PP', 'DP_NPC_BLOCK'), True, env='YO2_DP_NPC_BLOQUEO', grupo='persistencia', desc='Retar a una NPC sin DP se rechaza (0x428a −612)', origen='oficial'),
    F('persistencia.DP_ROOM_CHECK', ('mod', 'PP', 'DP_ROOM_CHECK'), True, env='YO2_DP_SALA_CHECK', grupo='persistencia', desc='Comprobar los DP al crear la sala', origen='?'),
    F('persistencia.DP_START_ONCE', ('mod', 'PP', 'DP_START_ONCE'), True, env='YO2_DP_INICIO_CUENTA', grupo='persistencia', desc='Los DP de inicio se dan una sola vez por cuenta', origen='?'),
    F('persistencia.CHALLENGE_MODE', ('mod', 'PP', 'CHALLENGE_MODE'), 'free', env='YO2_CHALLENGE', grupo='persistencia', desc='Modo del derecho a retar (挑戦権)', origen='?'),
    F('persistencia.PRIZE_CARDS', ('mod', 'PP', 'PRIZE_CARDS'), False, env='YO2_PRIZE_CARDS', grupo='persistencia', desc='Cartas de premio en los sobres', origen='?'),
    F('persistencia.RIGHTS_ON', ('mod', 'PP', 'RIGHTS_ON'), False, env='YO2_RIGHTS', grupo='persistencia', desc='Derechos (PBIN) de compra', origen='oficial'),
    F('persistencia.PVP_CARD_ON', ('mod', 'PP', 'PVP_CARD_ON'), True, env='YO2_PVP_CARTA', grupo='persistencia', desc='Carta de regalo tras un PvP', origen='oficial'),
    F('persistencia.PVP_CARD_PACKS', ('mod', 'PP', 'PVP_CARD_PACKS'), tuple(range(1, 11)), env='YO2_PVP_SOBRES', grupo='persistencia', desc='Sobres que se ofrecen tras un PvP (por defecto 1-10)', origen='oficial'),
    F('persistencia.XP_ON', ('mod', 'PP', 'XP_ON'), True, env='YO2_XP', grupo='persistencia', desc='Puntuación y LV tras un PvP (+40 por ganar, tabla puntuación->LV de la guía de Duel Evolution; los duelos de NPC no cuentan)', efecto='próximo duelo', nota='confianza P: el LV sube por puntuación (C), las cifras salen de una guía de jugador (P); 36 duelos para salir del LV 1', origen='oficial'),
    F('persistencia.DUELPASS_REARM', ('env', 'YO2_DUELPASS_REARM'), None, env='YO2_DUELPASS_REARM', grupo='persistencia', desc='Rearmar el DUELPASS al instalar (se lee del entorno al usarse; sin poner = 1)', nota='liga de entorno: el valor vivo es el texto de la variable', origen='tecnico'),
    F('persistencia.DPAUTH_ON', ('mod', 'DPC', 'DPAUTH_ON'), True, env='YO2_DPAUTH', grupo='persistencia', desc='0x8101 REQDPAUTH: códigos de DUELPASS (admin_duelpass.json)', origen='oficial'),

    # ---------------------------------------------------------------- red, límites y sesiones (run.py)
    F('red.MAX_CONNS_PER_IP', ('ns', 'MAX_CONNS_PER_IP'), 32, env='YO2_MAX_CONEXIONES_IP', grupo='red', desc='Conexiones abiertas a la vez por IP', reiniciar=True, efecto='al reiniciar', nota='extra', origen='tecnico'),
    F('red.MAX_CONNS', ('ns', 'MAX_CONNS'), 256, env='YO2_MAX_CONEXIONES', grupo='red', desc='Conexiones abiertas a la vez en total', reiniciar=True, efecto='al reiniciar', nota='extra', origen='tecnico'),
    F('red.IDLE_TIMEOUT', ('ns', 'IDLE_TIMEOUT'), 600, grupo='red', desc='Segundos sin datos tras los que se cierra una conexión', nota='extra', origen='tecnico'),
    F('sistema.MAX_SAVED_ACCOUNTS', ('ns', 'MAX_SAVED_ACCOUNTS'), 64, env='YO2_MAX_CUENTAS', grupo='sistema', desc='Tope de cuentas guardadas en state', nota='extra', origen='tecnico'),

    # ---------------------------------------------------------------- despliegue (entorno; no son banderas de juego)
    F('sistema.PORT_OFFSET', ('ns', 'PORT_OFFSET'), 0, env='YO2_PORT_OFFSET', desc='Desplazamiento de TODOS los puertos (instancias de prueba)', deploy=True, reiniciar=True, efecto='al reiniciar', origen='tecnico'),
    F('sistema.BIND_IPS', ('ns', 'BIND_IPS'), ['127.0.0.1'], env='YO2_BIND', desc='IPs en las que escuchan los puertos de juego y el HTTP', deploy=True, reiniciar=True, efecto='al reiniciar', nota='la consola 5799 escucha SIEMPRE solo en 127.0.0.1', origen='tecnico'),
    F('sistema.PUBLIC_IP', ('ns', 'PUBLIC_IP'), DERIVADA, env='YO2_PUBLIC_IP', desc='IP que se anuncia en la puerta (0x2003) y en 0x4306', deploy=True, reiniciar=True, efecto='al reiniciar', tipo='str', origen='tecnico'),
    F('sistema.NO_CLIENT', ('ns', 'NO_CLIENT'), False, env='YO2_NO_CLIENT', desc='No lanzar yo2.exe', deploy=True, reiniciar=True, efecto='al reiniciar', origen='tecnico'),
    F('sistema.LOGDIR', ('ns', 'LOGDIR'), DERIVADA, env='YO2_LOGDIR', desc='Carpeta del log', deploy=True, reiniciar=True, efecto='al reiniciar', tipo='str', origen='tecnico'),
    F('sistema.STATE_PATH', ('ns', 'STATE_PATH'), DERIVADA, env='YO2_STATE', desc='state.json (y su base .sqlite)', deploy=True, reiniciar=True, efecto='al reiniciar', tipo='str', origen='tecnico'),
    F('sistema.STORAGE', ('ns', 'STORAGE'), DERIVADA, env='YO2_STORAGE', desc='Almacén: json | sqlite (por defecto sqlite si existe la base)', deploy=True, reiniciar=True, efecto='al reiniciar', tipo='str', origen='tecnico'),
    F('sistema.OWNER_ACCT', ('ns', 'OWNER_ACCT'), DERIVADA, env='YO2_OWNER_ACCT', desc='Id de la cuenta del dueño (yo2srv/cuentas/sesion.py; en modo paquete ninguno salvo YO2_OWNER_ACCT)', deploy=True, reiniciar=True, efecto='al reiniciar', tipo='str', origen='tecnico'),
    F('sistema.ADMIN_PANEL', ('ns', 'ADMIN_PANEL'), True, env='YO2_ADMIN_PANEL', desc='Panel de administración (8790)', deploy=True, reiniciar=True, efecto='al reiniciar', origen='tecnico'),
    F('sistema.ADMIN_PANEL_PORT', ('env', 'YO2_ADMIN_PANEL_PORT'), None, env='YO2_ADMIN_PANEL_PORT', desc='Puerto del panel (por defecto 8790 + offset)', deploy=True, reiniciar=True, efecto='al reiniciar', nota='liga de entorno', origen='tecnico'),
    F('sistema.ADMIN_TOKEN_FILE', ('env', 'YO2_ADMIN_TOKEN_FILE'), None, env='YO2_ADMIN_TOKEN_FILE', desc='Fichero del token del panel (pruebas)', deploy=True, reiniciar=True, efecto='al reiniciar', nota='liga de entorno', origen='tecnico'),
    F('sistema.DB', ('env', 'YO2_DB'), None, env='YO2_DB', desc='Ruta de la base SQLite (por defecto <state>.sqlite)', deploy=True, reiniciar=True, efecto='al reiniciar', nota='liga de entorno', origen='tecnico'),
    F('sistema.DB_CRYPT', ('env', 'YO2_DB_CRYPT'), None, env='YO2_DB_CRYPT', desc='Cifrado de la base (auto | off | …; sin poner = auto)', deploy=True, reiniciar=True, efecto='al reiniciar', nota='liga de entorno', origen='tecnico'),
    F('sistema.DB_KEYFILE', ('env', 'YO2_DB_KEYFILE'), None, env='YO2_DB_KEYFILE', desc='Fichero de la clave de la base (por defecto en %LOCALAPPDATA%)', deploy=True, reiniciar=True, efecto='al reiniciar', nota='liga de entorno', origen='tecnico'),
    F('sistema.PERFIL', ('env', 'YO2_PERFIL'), None, env='YO2_PERFIL', desc='Perfil de banderas: vanilla (por defecto) | enhanced', deploy=True, reiniciar=True, efecto='al reiniciar', nota='liga de entorno; lo lee Config.perfil', origen='tecnico'),
]

BY_ID = {f.id: f for f in REGISTRO}
if len(BY_ID) != len(REGISTRO):                       # un id repetido es un error de este fichero
    _vistos = set()
    _rep = [f.id for f in REGISTRO if f.id in _vistos or _vistos.add(f.id)]
    raise RuntimeError(f"ids de banderas repetidos: {_rep}")


# =====================================================================================================================
def _tipo_vale(f, v):
    """¿`v` es un valor aceptable para la bandera `f`? (comprobación del tipo declarado, no del rango)"""
    t = f.tipo
    if isinstance(t, tuple):
        return v in t
    if t == "bool":
        return isinstance(v, bool)
    if t == "int":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "float":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "str":
        return isinstance(v, str)
    if t == "dict":
        return isinstance(v, dict)
    if t == "lista":
        return isinstance(v, (list, tuple))
    return True


class Config:
    """Lectura de las banderas contra un namespace vivo y aplicación del perfil al arrancar (`aplicar_perfil`)."""

    def __init__(self, ns, *, modulos=None, entorno=None, fichero=None):
        self.ns = ns                                  # el dict vivo (globals() de run.py)
        self._modulos = sys.modules if modulos is None else modulos
        self._env = os.environ if entorno is None else entorno
        pedido = (self._env.get("YO2_PERFIL") or "vanilla").strip().lower()
        self.perfil_pedido = pedido
        self.perfil = pedido if pedido in PERFILES else "vanilla"
        self.perfil_aviso = None if pedido in PERFILES else f"YO2_PERFIL={pedido!r} no existe; se usa vanilla"
        # admin_banderas.json: junto a state.json (o donde diga `fichero`). Se lee en aplicar_perfil; nunca se crea.
        self.fichero = fichero
        self.fijas = {}                               # id -> valor leído de admin_banderas.json
        self.resumen = None                           # lo que hizo la última aplicar_perfil()

    # ------------------------------------------------------------------ registro
    @staticmethod
    def ids():
        return [f.id for f in REGISTRO]

    @staticmethod
    def flag(id):
        return BY_ID[id]

    def __contains__(self, id):
        return id in BY_ID

    def vanilla(self, id):
        return BY_ID[id].vanilla

    def valor_perfil(self, id, perfil=None):
        """Valor por defecto de la bandera en el perfil. `enhanced` usa su `enhanced=` si lo tiene (hoy ninguna) y si no, vanilla."""
        perfil = perfil or self.perfil
        if perfil not in PERFILES:
            raise ValueError(f"perfil desconocido: {perfil!r}")
        f = BY_ID[id]
        if perfil == "enhanced" and f.enhanced is not SIN:
            return f.enhanced
        return f.vanilla

    # ------------------------------------------------------------------ valor vivo
    def _modulo(self, nombre):
        m = self.ns.get(nombre)
        if m is None:
            m = self._modulos.get(nombre)
        return m

    def _tr_cfg(self):
        ocg = self.ns.get("OCG")
        tr = getattr(ocg, "TR", None) if ocg is not None else self._modulos.get("translator")
        return getattr(tr, "DEFAULT_CFG", None)

    def leer_liga(self, liga):
        """Valor vivo de una liga, o NO_CARGADA si el sitio no existe ahora."""
        k = liga[0]
        if k == "ns":
            return self.ns.get(liga[1], NO_CARGADA)
        if k == "nsdict":
            d = self.ns.get(liga[1])
            return d.get(liga[2], NO_CARGADA) if isinstance(d, dict) else NO_CARGADA
        if k == "mod":
            m = self._modulo(liga[1])
            return getattr(m, liga[2], NO_CARGADA) if m is not None else NO_CARGADA
        if k == "moddict":
            m = self._modulo(liga[1])
            d = getattr(m, liga[2], None) if m is not None else None
            return d.get(liga[3], NO_CARGADA) if isinstance(d, dict) else NO_CARGADA
        if k == "trcfg":
            d = self._tr_cfg()
            return d.get(liga[1], NO_CARGADA) if isinstance(d, dict) else NO_CARGADA
        if k == "env":
            return self._env.get(liga[1])
        raise ValueError(f"liga desconocida: {liga!r}")

    def leer(self, id):
        """Valor vivo de la bandera `id` (NO_CARGADA si su sitio no existe). KeyError si el id no está en el registro."""
        return self.leer_liga(BY_ID[id].liga)

    def get(self, id, defecto=None):
        v = self.leer(id)
        return defecto if v is NO_CARGADA else v

    def __getitem__(self, id):
        v = self.leer(id)
        if v is NO_CARGADA:
            raise LookupError(f"la bandera {id!r} no está cargada ahora ({BY_ID[id].liga!r})")
        return v

    # ------------------------------------------------------------------ vistas
    def todas(self, grupo=None, deploy=None):
        """Lista de dicts (los campos de `panel.flags()` + vanilla, env, liga y difiere)."""
        out = []
        for f in REGISTRO:
            if grupo is not None and f.grupo != grupo:
                continue
            if deploy is not None and f.deploy != deploy:
                continue
            v = self.leer_liga(f.liga)
            existe = v is not NO_CARGADA
            out.append({"id": f.id, "valor": v if existe else None, "existe": existe,
                        "tipo": "bool" if f.tipo == "bool" else "opcion" if isinstance(f.tipo, tuple) else f.tipo,
                        "opciones": list(f.tipo) if isinstance(f.tipo, tuple) else None,
                        "efecto": f.efecto, "reiniciar": f.reiniciar, "desc": f.desc, "fijo": f.fijo,
                        "grupo": f.grupo, "vanilla": None if f.vanilla is DERIVADA else f.vanilla,
                        "env": f.env, "liga": list(f.liga), "deploy": f.deploy,
                        "enhanced": None if f.enhanced is SIN or f.enhanced is DERIVADA else f.enhanced, "origen": f.origen,
                        "difiere": existe and f.vanilla is not DERIVADA and f.liga[0] != "env"
                        and v != self.valor_perfil(f.id)})
        return out

    def diferencias(self):
        """Ids cuyo valor vivo no es el de su perfil (solo banderas con valor fijo y sitio cargado)."""
        return [d["id"] for d in self.todas() if d["difiere"]]

    # ------------------------------------------------------------------ perfil (R2-corto)
    def cargar_fijas(self):
        """Lee admin_banderas.json (SOLO lectura). Formato: {"id": valor, ...} o {"banderas": {"id": valor, ...}}.
        Devuelve (valores {id: valor}, avisos [texto]). Sin fichero: ({}, []). Un fichero roto o un id/tipo que no cuadra
        no impide arrancar: se anota en los avisos y esa bandera se queda con el valor del perfil."""
        ruta = self.fichero
        if ruta is None:
            from . import rutas
            ruta = os.path.join(rutas.datos_dir(env=self._env), FICHERO_FIJAS)
        self.fichero = ruta
        if not os.path.isfile(ruta):
            return {}, []
        try:
            with open(ruta, encoding="utf-8") as fh:
                dato = json.load(fh)
        except (OSError, ValueError) as e:
            return {}, [f"{FICHERO_FIJAS}: no se pudo leer ({e!r}); se ignora"]
        if isinstance(dato, dict) and isinstance(dato.get("banderas"), dict):
            dato = dato["banderas"]
        if not isinstance(dato, dict):
            return {}, [f"{FICHERO_FIJAS}: no es un objeto JSON; se ignora"]
        valores, avisos = {}, []
        for k, v in dato.items():
            f = BY_ID.get(k)
            if f is None:
                avisos.append(f"{FICHERO_FIJAS}: {k!r} no es una bandera; se ignora")
            elif f.deploy:
                avisos.append(f"{FICHERO_FIJAS}: {k!r} es de despliegue (entorno), no se fija aquí; se ignora")
            elif not _tipo_vale(f, v):
                avisos.append(f"{FICHERO_FIJAS}: {k!r} = {v!r} no es del tipo {f.tipo!r}; se ignora")
            else:
                valores[k] = v
        return valores, avisos

    def escribir_liga(self, liga, valor):
        """Escribe `valor` en el sitio de la liga (lo que hace `panel._flag_set`). KeyError/AttributeError si no existe."""
        k = liga[0]
        if k == "ns":
            self.ns[liga[1]] = valor
        elif k == "nsdict":
            self.ns[liga[1]][liga[2]] = valor
        elif k == "mod":
            setattr(self._modulo(liga[1]), liga[2], valor)
        elif k == "moddict":
            getattr(self._modulo(liga[1]), liga[2])[liga[3]] = valor
        elif k == "trcfg":
            self._tr_cfg()[liga[1]] = valor
        elif k == "env":
            self._env[liga[1]] = str(int(valor)) if isinstance(valor, bool) else str(valor)
        else:
            raise ValueError(f"liga desconocida: {liga!r}")

    def aplicar_perfil(self):
        """Deja cada bandera en el valor de su perfil escribiendo en su liga (§2.6). La llama `arranque.cargar` al acabar las
        cargas; es idempotente. Para cada bandera (las de despliegue no se tocan):
          1. valor = el de admin_banderas.json si lo hay; si no, el del perfil;
          2. si su variable de entorno YO2_* está puesta, manda el entorno: no se escribe;
          3. un valor del perfil igual a `vanilla` no se escribe (vanilla es lo que ya hace el código);
          4. si el sitio de la liga no existe ahora (módulo sin cargar), no se escribe;
          5. se escribe solo si el valor vivo es distinto.
        Devuelve (y guarda en `self.resumen`) {"perfil", "escritas": [ids], "entorno": [ids], "fichero": [ids],
        "avisos": [texto]}. Después ejecuta las `accion` de las banderas escritas (mundo | npcs | watch), como el panel."""
        self.fijas, avisos = self.cargar_fijas()
        if self.perfil_aviso:
            avisos.insert(0, self.perfil_aviso)
        escritas, entorno, deldisco = [], [], []
        for f in REGISTRO:
            if f.deploy:
                continue
            en_fichero = f.id in self.fijas
            valor = self.fijas[f.id] if en_fichero else self.valor_perfil(f.id)
            if valor is DERIVADA:
                continue
            if f.env and self._env.get(f.env):
                if f.liga[0] != "env":
                    entorno.append(f.id)
                continue
            if f.liga[0] == "env" and valor is None:
                continue
            if not en_fichero and valor == f.vanilla and type(valor) is type(f.vanilla):
                continue
            actual = self.leer_liga(f.liga)
            if actual is NO_CARGADA:
                avisos.append(f"{f.id}: su sitio {f.liga!r} no está cargado; no se escribe")
                continue
            if actual == valor and type(actual) is type(valor):
                continue
            try:
                self.escribir_liga(f.liga, valor)
            except Exception as e:
                avisos.append(f"{f.id}: no se pudo escribir {valor!r} en {f.liga!r} ({e!r})")
                continue
            escritas.append(f.id)
            if en_fichero:
                deldisco.append(f.id)
        avisos.extend(self._acciones(escritas))
        self.resumen = {"perfil": self.perfil, "escritas": escritas, "entorno": entorno, "fichero": deldisco,
                        "avisos": avisos}
        return self.resumen

    def _acciones(self, ids):
        """Lo que el panel hace tras cambiar una bandera (`after`): mundo -> MW.install + NPCS; npcs -> NPCS; watch -> pvp_match."""
        avisos = []
        acciones = {BY_ID[i].accion for i in ids if BY_ID[i].accion}
        try:
            if "mundo" in acciones and self.ns.get("MW") is not None:
                self.ns["MW"].install(self.ns)
            if acciones & {"mundo", "npcs"} and callable(self.ns.get("load_npcs")):
                self.ns["NPCS"] = self.ns["load_npcs"]()
            if "watch" in acciones and self._modulo("pvp_match") is not None:
                setattr(self._modulo("pvp_match"), "PVP_WATCH", self.leer("lobby.WATCH"))
        except Exception as e:
            avisos.append(f"acciones tras aplicar el perfil: {e!r}")
        return avisos
