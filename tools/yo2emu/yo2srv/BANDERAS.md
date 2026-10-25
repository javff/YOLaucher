# Banderas del servidor (generado)

> Generado por `python -m yo2srv.gen_banderas` desde `yo2srv/config.py`. **No se edita a mano**: cambia el registro y regenera.

222 banderas. Perfil por defecto: `vanilla` (`YO2_PERFIL=vanilla|enhanced`). Hoy `enhanced` = `vanilla` en todas (`enhanced` aparece como «= vanilla»). Precedencia al arrancar: perfil → `admin_banderas.json` → variable de entorno `YO2_*` → cambio en caliente.

| Origen | Banderas | Qué significa |
|---|---:|---|
| `oficial` | 146 | como el original (C/P en las notas) o intento de serlo |
| `inventado` | 16 | nuestro, aprobado y detrás de una bandera (no es 1:1) |
| `tecnico` | 40 | sin efecto de juego: rutas, puertos, límites, logs, pruebas, seguridad |
| `?` | 20 | sin decidir (ver más abajo) |

## Inventadas encendidas en vanilla (candidatas a pasar a enhanced)

Banderas de origen `inventado` cuyo valor en vanilla NO es «apagado» (y por tanto el Discord de restauración las vería).

| id | vanilla | desc | nota |
|---|---|---|---|
| `lobby.ROOM_LANG_MERGE` | `True` | Las salas de un tipo comparten bloque sea cual sea el idioma del cliente | extra (09-10); YO2_ROOM_LANG_MERGE=0 vuelve a separar por idioma del cliente. Solo cuenta con lobby.ROOM_PER_LANG apagada |
| `duelo.lp_reconcile` | `True` | Reconciliación del marcador de LP del cliente con ocgcore (§24) |  |
| `duelo.chain_priority_hand` | `True` | Avisos de cadena también para monstruos de la mano |  |
| `duelo.tiempo_respuesta` | `180.0` | Segundos para responder a una petición |  |
| `ocg.USE_BRAIN` | `True` | La NPC juega con el cerebro (research/ia); sin él, bot.ts |  |
| `duelo.npc_janken_azar` | `True` | Piedra-papel-tijera contra la NPC: mano al azar | extra; en instancias de prueba (YO2_PVP_TEST=1) el azar va apagado salvo que se pida |
| `pvp.WATCH_SIN_DECISIONES` | `True` | El espectador no recibe peticiones de jugada ni ventanas de decisión (SÍ/NO, opciones, listas, avisos de aceptar) del jugador 0 | 25-10, rx/ESP; el cliente abre esas ventanas también en modo espectador (C) y el visionado se quedaba parado; qué mandaba Konami a un espectador: D |
| `persistencia.START_LEVEL` | `1` | Nivel (LV) de un personaje nuevo, 1-99. 1 = como el original (con LV 0-1 el cliente solo enseña las Salas de Principiantes); 2 o más = los nuevos entran en todas las salas | 12-10: elección del anfitrión en el lanzador («Starting level»); solo afecta a los personajes que se crean después |
| `persistencia.COUNTRY_BY_LANG` | `True` | País (bandera) de un personaje nuevo según el idioma del cliente (0x4100): JP US DE IT FR ES KR; si no, DEFAULT_COUNTRY | 12-10: en el oficial era el país de la cuenta de Konami (no reproducible); los personajes de antes no cambian |

### Inventadas que no son una bandera del registro

| qué | dónde vive | estado en vanilla |
|---|---|---|
| chain_event_p2 | research/traductor/translator.py `_chain_priority`: `cfg.get("chain_event_p2", True)`; la clave NO está en DEFAULT_CFG (por eso no es una bandera del registro) | ENCENDIDA en vanilla (P2 2 si alguna opción es un efecto con evento); `cfg["chain_event_p2"] = False` deja la tabla pura |
| Aviso «Mover» (texto 5092) | lado del cliente (Frida): `launcher/aviso_sala_hook.js` y copia en `net_hook.js` (research/lobby/NOTES.md «Aviso al pulsar MOVE con sala abierta») | APAGADO en vanilla: solo con el fichero `yo2-aviso-sala.txt` junto a yo2.exe o `YO2_AVISO_SALA=1` |
| Tamaño de ventana del cliente (Ajustes → Display → «Window size») | lado del cliente (DLL propia): `launcher/juego/dll/yo2net.c` (ventana ampliada, cuentas en `yo2win.h`), valor en `lanzador.json` («ventana») y `YO2ONLINE_WINDOW` (rama rx/PAN) | APAGADO en vanilla: «Original (1024x768)»; 1.5x / 2x / «Fit screen» solo si el jugador lo elige en el lanzador |
| «Mirar» un duelo contra una NPC | `npc_watch.py` (research/lobby/NOTES.md «C2»): cuelga de `hub.NPC_DUEL_STATE` y `lobby.WATCH`, sin bandera propia | ENCENDIDO en vanilla mientras esas dos lo estén |
| Torpeza de la IA de las NPC | `research/ia/brain.py` (`NpcProfile.torpeza`, por nivel de la NPC; research/ia/BRAIN.md): parte de `ocg.USE_BRAIN`, sin bandera propia | ENCENDIDA en vanilla mientras `ocg.USE_BRAIN` lo esté |

### Inventadas apagadas en vanilla (no molestan)

`duelo.lp_resync` (False), `duelo.chain_card_name` (False), `duelo.chain_one_name` (False), `ocg.NPC_DECKS_EXTRA` (False), `lobby.NPC_CHALLENGE` (None), `pvp.WATCH_SEND_PREP` (False), `persistencia.STAT_COMEBACK_LP` (0)

## Origen dudoso (`?`)

Sin evidencia clara en las notas (`research/**/NOTES.md`, `HANDOFF.md`, `TIEMPO.md`); decisión pendiente.

| id | vanilla | desc |
|---|---|---|
| `mundo.pvp_titles` | `True` | Títulos por logros también tras un PvP |
| `mundo.GM_ACCOUNTS` | `True` | GM: KONAMI y GAME MASTER solo para las cuentas GM (y GAME MASTER se les da solo) |
| `torneos.AUTO_CLOCK` | `True` | Reloj automático del torneo (hilo que se crea al arrancar) |
| `duelo.counter_dialog` | `True` | Diálogo de contadores |
| `ocg.NPC_THINK` | `0.8` | Segundos antes de cada jugada visible de la NPC |
| `lobby.AUTODUEL_MATCH` | `True` | El servidor empareja a dos jugadores en autoduelo |
| `lobby.OFFER_TIMEOUT` | `30.0` | Segundos que vive un reto PvP sin respuesta |
| `torneos.AUTO_ENTRY` | `True` | Estar en el cuadro durante la inscripción = inscrito |
| `torneos.NOSHOW_ADVANCE_SEAT0` | `True` | Si no se presenta ninguno, pasa el asiento menor |
| `torneos.REPAIR_ON_DRAW` | `True` | Re-emparejar tras el empate del piedra-papel-tijera |
| `tienda.DEFAULT_COUNTRY` | `'ES'` | País (2 letras) de los personajes nuevos |
| `tienda.START_MP` | `10000` | Millas (MP) de un personaje nuevo |
| `tienda.START_DPP` | `10` | Puntos DUELPASS de un personaje nuevo |
| `persistencia.TRUNK_START` | `'all3'` | Baúl inicial de un jugador nuevo (deck \| all3 \| empty) |
| `persistencia.NPC_IN_RECORD` | `True` | Los duelos contra NPC cuentan en Victorias/Derrotas |
| `persistencia.NPC_THRESHOLD` | `4` | Umbral de victorias para el premio de las NPC (mínimo 2) |
| `persistencia.DP_ROOM_CHECK` | `True` | Comprobar los DP al crear la sala |
| `persistencia.DP_START_ONCE` | `True` | Los DP de inicio se dan una sola vez por cuenta |
| `persistencia.CHALLENGE_MODE` | `'free'` | Modo del derecho a retar (挑戦権) |
| `persistencia.PRIZE_CARDS` | `False` | Cartas de premio en los sobres |

## Todas las banderas

### Lobby y salas (`lobby`)

| id | vanilla | enhanced | origen | desc |
|---|---|---|---|---|
| `MYROOM_SELF_AVATAR` | `True` | = vanilla | oficial | My Room enseña tu propio aspecto |
| `lobby.LIMIT_LIST` | `True` | = vanilla | oficial | Lista de prohibidas/limitadas de 2008 (0x5600) |
| `lobby.WATCH` | `True` | = vanilla | oficial | Ver duelos de otros (espectador) |
| `lobby.ROOM_REOPEN` | `True` | = vanilla | oficial | La sala sigue abierta tras el duelo |
| `lobby.ROOM_LANG_MERGE` | `True` | = vanilla | inventado | Las salas de un tipo comparten bloque sea cual sea el idioma del cliente |
| `lobby.ROOM_PER_LANG` | `False` | = vanilla | oficial | Salas por idioma 1:1: un puerto por sala (idioma, tipo); la mundial (b1 = 6) junta idiomas |
| `lobby.PIDNAME_PLAYERS` | `True` | = vanilla | oficial | Buscar jugador por nombre (0x4500) |
| `lobby.TPPRESENT` | `False` | = vanilla | oficial | Premio de ranking TP (0x8301/0x8302) |
| `lobby.TP_PRESENT_CARD` | `0` | = vanilla | oficial | Carta del premio de ranking TP |
| `lobby.STARTUP_PRESENT` | `True` | = vanilla | oficial | Regalo de inicio (0x8401/0x8402) |
| `lobby.NPC_CHALLENGE` | `None` | = vanilla | inventado | Una NPC famosa reta a tu sala si estás solo en el bloque (segundos; None = nunca) |
| `lobby.AUTODUEL` | `True` | = vanilla | oficial | Autoduelo: sala con mstat 1 = estado 0x66 para los demás |
| `lobby.AUTODUEL_MATCH` | `True` | = vanilla | ? | El servidor empareja a dos jugadores en autoduelo |
| `lobby.RESETAUTOWAIT` | `True` | = vanilla | oficial | 0x42a0 RESETAUTOWAIT -> 0x42a2 |
| `lobby.USBDPAUTH_REFUSE` | `True` | = vanilla | oficial | 0x8203 REQUSBDPAUTH se rechaza (no hay servidor USB) |
| `lobby.OFFER_TIMEOUT` | `30.0` | = vanilla | ? | Segundos que vive un reto PvP sin respuesta |

### Mundo de 2008 (`mundo`)

| id | vanilla | enhanced | origen | desc |
|---|---|---|---|---|
| `mundo.spots` | `True` | = vanilla | oficial | Las 24 NPC originales de 2008 con su nombre y zona |
| `mundo.prizes` | `True` | = vanilla | oficial | Premios de las NPC originales (carta de 5 victorias) |
| `mundo.challenge` | `True` | = vanilla | oficial | 挑戦権: derecho a retar a Kaiba/Alexis tras ganar a sus NPC |
| `mundo.pvp_titles` | `True` | = vanilla | ? | Títulos por logros también tras un PvP |
| `mundo.hair_titles` | `True` | = vanilla | oficial | Títulos Kaiser Afro / Rey del Mechón |
| `mundo.title_owned` | `True` | = vanilla | tecnico | Rechazar un título que el jugador no tiene |
| `mundo.GM_ACCOUNTS` | `True` | = vanilla | ? | GM: KONAMI y GAME MASTER solo para las cuentas GM (y GAME MASTER se les da solo) |
| `NPC_SPOTS_2008` | `True` | = vanilla | oficial | Famosos en su sitio de 2008 |

### Duelos contra NPC (`npc`)

| id | vanilla | enhanced | origen | desc |
|---|---|---|---|---|
| `npc.dp` | `True` | = vanilla | oficial | Duelo contra NPC: cuesta 1 DP |
| `npc.no_record` | `True` | = vanilla | oficial | Duelo contra NPC: no toca LV ni récord |
| `npc.card` | `True` | = vanilla | oficial | Duelo contra NPC: 1 carta de premio |
| `npc.friendship` | `True` | = vanilla | oficial | Amistad con NPC: estrellas, niveles y premios |
| `npc.famous_decks` | `True` | = vanilla | oficial | Mazos reales de los famosos |
| `npc.npc_window` | `False` | = vanilla | oficial | Ventana de resultados de NPC |

### Jugadores en el mapa (`hub`)

| id | vanilla | enhanced | origen | desc |
|---|---|---|---|---|
| `hub.RIVAL_CARD` | `True` | = vanilla | oficial | Ficha del rival (LV, victorias) en «en duelo» |
| `hub.NPC_DUEL_STATE` | `True` | = vanilla | oficial | Pose «en duelo» también contra NPC |
| `hub.AREA_BLOCKS` | `True` | = vanilla | oficial | Un bloque por mapa (cambiarlo con gente dentro los descoloca) |
| `hub.LIST_SELF` | `True` | = vanilla | oficial | LIST2..5 con el propio jugador primero, como LIST1 |
| `hub.REANNOUNCE_ON_FIRST_POS` | `False` | = vanilla | oficial | Reanunciar (0x3112 op 1) al que llega al mandar su primera 0x5300 |
| `hub.LOGIN_ESTRICTO` | `True` | = vanilla | oficial | Una cuenta que no existe recibe −240 (login 1:1) en vez de crearse |
| `hub.LOGIN_EN_USO` | `True` | = vanilla | oficial | −242 «ya está en uso» si la cuenta tiene otra conexión viva |
| `hub.LOGIN_EN_USO_VIVA_S` | `90.0` | = vanilla | tecnico | Segundos sin mensajes tras los que una conexión ya no cuenta como viva |
| `hub.LIST1_STATE` | `True` | = vanilla | oficial | LIST1 lleva el estado real del jugador (azul, «Retar», pose) |
| `hub.LIST2_DUEL_23` | `True` | = vanilla | oficial | LIST2 «en duelo» con 23 B (no 25) |
| `hub.MAX_ACCOUNTS` | `64` | = vanilla | tecnico | Tope de sesiones en memoria a la vez |
| `hub.SESSION_RELEASE_S` | `120.0` | = vanilla | tecnico | Segundos tras los que se libera una sesión sin conexiones |

### PvP y repeticiones (`pvp`)

| id | vanilla | enhanced | origen | desc |
|---|---|---|---|---|
| `pvp.DUELLOG_RECORD` | `True` | = vanilla | oficial | Grabar repeticiones de los PvP |
| `pvp.DUELLOG_FINISH` | `True` | = vanilla | oficial | Cierre del registro de duelo (0x6010/0x6012) |
| `pvp.PVP_SVRINFO` | `True` | = vanilla | oficial | Aviso 0x649b al que se queda (rendición, desconexión) |
| `pvp.PVP_MATCH` | `'1'` | = vanilla | oficial | Match al mejor de 3 (1; 0 = como antes; cerrar = una partida y fin) |
| `pvp.MATCH_WAIT` | `300.0` | = vanilla | tecnico | Segundos de espera entre partidas de un match |
| `pvp.PVP_WATCH` | `True` | = vanilla | oficial | Espectador de PvP (lado pvp_match de lobby.WATCH) |
| `pvp.WATCH_SIN_DECISIONES` | `True` | = vanilla | inventado | El espectador no recibe peticiones de jugada ni ventanas de decisión (SÍ/NO, opciones, listas, avisos de aceptar) del jugador 0 |
| `pvp.SURRENDER_MIN_TURN` | `10` | = vanilla | oficial | Turno global a partir del cual se puede rendir (dlg 82 «a partir del 10º turno»; 0 = siempre) |
| `pvp.SURRENDER_ROOM_OPTION` | `True` | = vanilla | oficial | Respetar la opción de sala «Rendirse OFF» (byte 21 de 0x4288): sin rendición en esa sala |
| `pvp.SURRENDER_ENDS_MATCH` | `False` | = vanilla | oficial | Rendirse en un match lo cierra entero (0x4d P1 = 5); apagada = solo se pierde la partida en curso y el match sigue (como el oficial) |
| `pvp.SURRENDER_FIN_OFICIAL` | `True` | = vanilla | oficial | Fin por rendición como el oficial: sin 0x23, 0x05 con motivo 12, 0x4d P1 = 2 (apagada = 0x23 + motivo 0 + P1 de partida) |
| `pvp.WATCH_SEND_PREP` | `False` | = vanilla | inventado | Al espectador, un 0x600c antes del 0x6006 (vidas) |
| `pvp.TEST_AUTO` | `False` | = vanilla | tecnico | Modo de prueba (fake_client pvp): solo instancias de prueba |
| `pvp.ATTACH_TIMEOUT` | `90.0` | = vanilla | tecnico | Segundos para que los dos jugadores conecten al duelo |
| `pvp.REPLAY_ARCHIVE` | `True` | = vanilla | tecnico | El servidor guarda su copia de cada duelo (panel alojado) |

### Torneos y eventos (`torneos`)

| id | vanilla | enhanced | origen | desc |
|---|---|---|---|---|
| `torneos.TOURNAMENT_ON` | `True` | = vanilla | oficial | Torneos CHAMPIONSHIP con datos |
| `torneos.EVENTS_ON` | `True` | = vanilla | oficial | Eventos (EVENTINFO) al entrar en el mapa |
| `torneos.AUTO_CLOCK` | `True` | = vanilla | ? | Reloj automático del torneo (hilo que se crea al arrancar) |
| `torneos.AUTO_ENTRY` | `True` | = vanilla | ? | Estar en el cuadro durante la inscripción = inscrito |
| `torneos.BIT80_ALIVE` | `True` | = vanilla | oficial | Bit 0x80 de la página = sigue vivo |
| `torneos.NOSHOW_ADVANCE_SEAT0` | `True` | = vanilla | ? | Si no se presenta ninguno, pasa el asiento menor |
| `torneos.REPAIR_ON_DRAW` | `True` | = vanilla | ? | Re-emparejar tras el empate del piedra-papel-tijera |

### Equipos (`equipos`)

| id | vanilla | enhanced | origen | desc |
|---|---|---|---|---|
| `equipos.NAME_CASEFOLD` | `True` | = vanilla | oficial | «Dragones» y «dragones» son el mismo nombre de equipo |
| `equipos.APPY_INVITER_FIRST` | `True` | = vanilla | oficial | Orden de los avisos de la invitación a un equipo |
| `equipos.ANNOUNCE_4571_BLOCK` | `True` | = vanilla | oficial | 0x4571 a los del bloque (actualiza su LIST5) |
| `equipos.ANNOUNCE_4571_SELF` | `True` | = vanilla | oficial | 0x4571 al propio jugador |
| `equipos.JOIN_NOTICE_TO_JOINER` | `True` | = vanilla | oficial | 0x4561 también al que entra al equipo |
| `equipos.PATCH_HUB_LIST5` | `True` | = vanilla | tecnico | install() sustituye HUB.list5_body por el de equipos |

### Intercambio (`intercambio`)

| id | vanilla | enhanced | origen | desc |
|---|---|---|---|---|
| `intercambio.ADDCARD_REJECT_1010` | `False` | = vanilla | oficial | Rechazar con −1010 un ADDCARD que el baúl no cubre |
| `intercambio.RESET_READY_ON_CHANGE` | `False` | = vanilla | oficial | Un cambio de cartas quita el «listo» de los dos |
| `intercambio.CLEAR_STATUS_AFTER_NO` | `False` | = vanilla | oficial | Tras un «no», apagar el «listo» del compañero |

### Tienda y cuentas nuevas (`tienda`)

| id | vanilla | enhanced | origen | desc |
|---|---|---|---|---|
| `tienda.SHOP_SELL_PRIZE_PACKS` | `False` | = vanilla | oficial | Vender también los packs de premio (tipo 2) |
| `tienda.SHOP_DECK_RECIPE` | `True` | = vanilla | oficial | Comprar un mazo pone su receta en el perfil (PBIN bit + 1) |
| `tienda.SHOP_DECK_ITEMGET` | `False` | = vanilla | oficial | Al comprar un mazo, mandar además 0x5205 con ese bit |
| `tienda.SHOP_AVATAR_ITEMGET` | `False` | = vanilla | oficial | Al comprar una prenda, mandar además 0x5205 |
| `tienda.DEFAULT_COUNTRY` | `'ES'` | = vanilla | ? | País (2 letras) de los personajes nuevos |
| `tienda.START_MP` | `10000` | = vanilla | ? | Millas (MP) de un personaje nuevo |
| `tienda.START_DPP` | `10` | = vanilla | ? | Puntos DUELPASS de un personaje nuevo |

### Perfiles, DP y premios (`persistencia`)

| id | vanilla | enhanced | origen | desc |
|---|---|---|---|---|
| `persistencia.START_LEVEL` | `1` | = vanilla | inventado | Nivel (LV) de un personaje nuevo, 1-99. 1 = como el original (con LV 0-1 el cliente solo enseña las Salas de Principiantes); 2 o más = los nuevos entran en todas las salas |
| `persistencia.COUNTRY_BY_LANG` | `True` | = vanilla | inventado | País (bandera) de un personaje nuevo según el idioma del cliente (0x4100): JP US DE IT FR ES KR; si no, DEFAULT_COUNTRY |
| `persistencia.TRUNK_START` | `'all3'` | = vanilla | ? | Baúl inicial de un jugador nuevo (deck \| all3 \| empty) |
| `persistencia.TP_RANK` | `False` | = vanilla | oficial | Puesto de ranking TP en 0x3012 |
| `persistencia.STATS_PVP_ON` | `True` | = vanilla | oficial | Contadores del perfil recogidos durante un PvP: tributos, fusiones, Especiales, activaciones, cartas del Side Deck (B51) |
| `persistencia.STAT_COMEBACK_LP` | `0` | = vanilla | inventado | Remontada (victorias/derrotas anunciadas): LP de diferencia que cuentan; 0 = no se cuentan |
| `persistencia.NPC_IN_RECORD` | `True` | = vanilla | ? | Los duelos contra NPC cuentan en Victorias/Derrotas |
| `persistencia.NPC_MP_WIN` | `0` | = vanilla | oficial | Millas por ganar a una NPC |
| `persistencia.NPC_MP_LOSE` | `0` | = vanilla | oficial | Millas por perder contra una NPC |
| `persistencia.NPC_MP_DRAW` | `0` | = vanilla | oficial | Millas por empatar con una NPC |
| `persistencia.NPC_DP_COST` | `0` | = vanilla | tecnico | Coste en DP del duelo NPC (viejo; el de verdad es DUEL_DP_COST) |
| `persistencia.NPC_THRESHOLD` | `4` | = vanilla | ? | Umbral de victorias para el premio de las NPC (mínimo 2) |
| `persistencia.DUEL_DP_COST` | `1` | = vanilla | oficial | Puntos DUELPASS que cuesta un duelo |
| `persistencia.DP_NPC_BLOCK` | `True` | = vanilla | oficial | Retar a una NPC sin DP se rechaza (0x428a −612) |
| `persistencia.DP_ROOM_CHECK` | `True` | = vanilla | ? | Comprobar los DP al crear la sala |
| `persistencia.DP_START_ONCE` | `True` | = vanilla | ? | Los DP de inicio se dan una sola vez por cuenta |
| `persistencia.CHALLENGE_MODE` | `'free'` | = vanilla | ? | Modo del derecho a retar (挑戦権) |
| `persistencia.PRIZE_CARDS` | `False` | = vanilla | ? | Cartas de premio en los sobres |
| `persistencia.RIGHTS_ON` | `False` | = vanilla | oficial | Derechos (PBIN) de compra |
| `persistencia.PVP_CARD_ON` | `True` | = vanilla | oficial | Carta de regalo tras un PvP |
| `persistencia.PVP_CARD_PACKS` | `(1, 2, 3, 4, 5, 6, 7, 8, 9, 10)` | = vanilla | oficial | Sobres que se ofrecen tras un PvP (por defecto 1-10) |
| `persistencia.XP_ON` | `True` | = vanilla | oficial | Puntuación y LV tras un PvP (+40 por ganar, tabla puntuación->LV de la guía de Duel Evolution; los duelos de NPC no cuentan) |
| `persistencia.DUELPASS_REARM` | `None` | = vanilla | tecnico | Rearmar el DUELPASS al instalar (se lee del entorno al usarse; sin poner = 1) |
| `persistencia.DPAUTH_ON` | `True` | = vanilla | oficial | 0x8101 REQDPAUTH: códigos de DUELPASS (admin_duelpass.json) |

### Duelo (traductor, ocgcore, lockstep) (`duelo`)

| id | vanilla | enhanced | origen | desc |
|---|---|---|---|---|
| `duelo.lp` | `8000` | = vanilla | oficial | Vidas iniciales de cada jugador |
| `duelo.kind_main` | `3` | = vanilla | oficial | Clase de la petición de fase principal (SELECT_IDLECMD) |
| `duelo.kind_battle` | `3` | = vanilla | oficial | Clase de la petición de batalla (SELECT_BATTLECMD) |
| `duelo.kind_select` | `3` | = vanilla | oficial | Clase de las peticiones de elegir carta / tributo / cadena |
| `duelo.select_bit` | `11` | = vanilla | oficial | Bit de las cartas elegibles en SELECT_CARD/TRIBUTE (clic directo) |
| `duelo.target_bit` | `0` | = vanilla | oficial | Bit para elegir el objetivo de un ataque (menú «Atacar») |
| `duelo.yesno` | `'dialog'` | = vanilla | oficial | SÍ/NO: diálogo del cliente o decide el bot (dialog \| auto) |
| `duelo.hide_npc` | `True` | = vanilla | oficial | Tapar mano, mazo, extra y boca abajo de la NPC con cartas tapadera |
| `duelo.token_serial_base` | `None` | = vanilla | oficial | Serie del primer token de cada jugador (None = cartas del mazo + extra + 1) |
| `duelo.finish_mode` | `0` | = vanilla | oficial | P1 del 0x6202 tipo 0x4d (modo de match) |
| `duelo.npc_phrases` | `False` | = vanilla | oficial | La NPC habla (0x60/0x62/0x03; requiere el byte NPC de 0x43a0) |
| `duelo.pile_look` | `True` | = vanilla | oficial | «Ver detalles» en cementerio, desterradas y extra propio |
| `duelo.stat_sync` | `True` | = vanilla | oficial | Imponer el ATK/DEF de ocgcore (0x36/0x37 + EFFECT_SET_STATS) |
| `duelo.stat_sync_turn` | `True` | = vanilla | oficial | ATK/DEF de ocgcore también al cambiar de fase/turno y al cerrar un lote sin decisión propia (§32) |
| `duelo.summon_done` | `True` | = vanilla | oficial | Aviso de invocación terminada |
| `duelo.summon_kind_fx` | `True` | = vanilla | oficial | Efecto según el tipo de invocación |
| `duelo.token_vanish` | `True` | = vanilla | oficial | Los tokens desaparecen con su animación |
| `duelo.yesno_card` | `True` | = vanilla | oficial | SÍ/NO con la carta |
| `duelo.yesno_sys_text` | `True` | = vanilla | oficial | SÍ/NO sin carta con el texto del sistema de ocgcore (§25) |
| `duelo.battle_damage_real` | `True` | = vanilla | oficial | Daño de batalla real (de ocgcore) |
| `duelo.battle_damage_split` | `True` | = vanilla | oficial | El 0x17 lleva solo el daño de batalla (el de efecto, p. ej. la ficha de Ojama, sale aparte) |
| `duelo.lp_type_oficial` | `True` | = vanilla | oficial | Tipo del 0x25 como el oficial: 1 batalla, 0 efecto, 2 coste (antes al revés; solo cambia estadísticas del cliente) |
| `duelo.lp_reconcile` | `True` | = vanilla | inventado | Reconciliación del marcador de LP del cliente con ocgcore (§24) |
| `duelo.lp_resync` | `False` | = vanilla | inventado | Reenviar los LP tras cada cambio |
| `duelo.lp_set_direct` | `True` | = vanilla | tecnico | MSG_LPUPDATE de ocgcore (Duel.SetLP, reinicio) -> 0x26 con el LP absoluto en ese momento (§31) |
| `duelo.yesno_text` | `269` | = vanilla | oficial | Texto del SÍ/NO de cadena (id de texto) |
| `duelo.chain_dialog` | `3` | = vanilla | oficial | Tipo del diálogo de cadena (3 = respeta «Mensaje de activación») |
| `duelo.chain_importance` | `2` | = vanilla | oficial | P2 del diálogo de cadena tipo 3 |
| `duelo.chain_importance_auto` | `True` | = vanilla | oficial | Importancia 2 si la cadena la abrió el rival |
| `duelo.forced_auto_single` | `True` | = vanilla | oficial | Cadena forzada con una sola opción: se contesta sola |
| `duelo.chain_card_name` | `False` | = vanilla | inventado | Nombre de la carta en el aviso de cadena |
| `duelo.chain_priority` | `'oficial'` | = vanilla | oficial | Prioridad de los avisos de cadena (oficial = la de Konami por suceso; None = apagado) |
| `duelo.chain_event_text` | `True` | = vanilla | oficial | Texto del suceso en el aviso de cadena (como el oficial) |
| `duelo.chain_none_prompt` | `False` | = vanilla | oficial | Aviso «no hay ninguna carta que se pueda activar» (204/205) en cada ventana vacía, como el oficial (§30) |
| `duelo.chain_one_name` | `False` | True | inventado | Aviso de cadena con el nombre de UNA sola carta activable (§24) |
| `duelo.chain_link_detail` | `True` | = vanilla | oficial | El aviso de cadena 7/8 deja la carta del eslabón en el panel de detalle (§26) |
| `duelo.chain_priority_hand` | `True` | = vanilla | inventado | Avisos de cadena también para monstruos de la mano |
| `duelo.chain_link_text` | `True` | = vanilla | oficial | Texto «… está activado» en el aviso de cadena |
| `duelo.chain_yes_direct` | `False` | = vanilla | oficial | Tras el SÍ de la cadena, atajo directo (apagado = 1:1) |
| `duelo.pick_panel` | `False` | = vanilla | oficial | Panel de elección de cartas |
| `duelo.activate_flash` | `False` | = vanilla | oficial | Destello 0x30 al activar una carta del campo |
| `duelo.attack_aim` | `True` | = vanilla | oficial | Espada de ataque que sigue al ratón |
| `duelo.aim_target_bit` | `11` | = vanilla | oficial | Bit de las cartas objetivo con la espada (11 = clic directo) |
| `duelo.look_modes` | `{14: 1, 15: 4, 12: 1}` | = vanilla | oficial | Zonas con «Ver detalles» (zona -> modo) |
| `duelo.list_picks` | `True` | = vanilla | oficial | Elecciones en lista |
| `duelo.place_manual` | `True` | = vanilla | oficial | Colocación manual: con «Auto Card Placing» OFF en el cliente (u8 de 0x61fe = 1) el jugador elige la zona de lo que invoca o coloca |
| `duelo.card_link` | `True` | = vanilla | oficial | Vínculo carta → carta (equipos, Círculo…) |
| `duelo.link_kind_equip` | `10` | = vanilla | oficial | Tipo de vínculo para equipos |
| `duelo.link_kind_target` | `9` | = vanilla | oficial | Tipo de vínculo que no es equipo |
| `duelo.link_target_anim` | `True` | = vanilla | oficial | Animación de objetivo (0x42) o silencioso (0x36) |
| `duelo.target_lockon` | `True` | = vanilla | oficial | Marca de objetivo al activar |
| `duelo.lockon_pile` | `True` | = vanilla | oficial | Marca de objetivo también sobre cartas del cementerio y desterradas (como el oficial; NOTES §35) |
| `duelo.chain_reveal` | `True` | = vanilla | oficial | Animación y sonido de «se activa» en cada activación de efecto (0x2e como el oficial; NOTES §35) |
| `duelo.counters` | `True` | = vanilla | oficial | Contadores visibles |
| `duelo.swap_cmd` | `True` | = vanilla | oficial | Intercambio de criaturas (0x3e) |
| `duelo.monst_shuffle` | `True` | = vanilla | oficial | Barajar monstruos colocados (Magical Hats) con 0x49 MONST_SHUFFLE, como el oficial (NOTES MIS-1) |
| `duelo.field_move_3d` | `True` | = vanilla | oficial | Cambio de control / mover de zona (0x3d) |
| `duelo.toss_anim` | `True` | = vanilla | oficial | Animación de moneda y dado |
| `duelo.coin_call` | `1` | = vanilla | oficial | Resultado resaltado al caer la moneda |
| `duelo.tribute_anim` | `True` | = vanilla | oficial | Animación de tributo |
| `duelo.tribute_mark` | `False` | = vanilla | oficial | Marca 0x4a antes de cada 0x46 de tributo |
| `duelo.fusion_anim` | `True` | = vanilla | oficial | Animación de fusión |
| `duelo.fusion_official` | `True` | = vanilla | oficial | Fusión con el orden del oficial: 0x4b de todos los materiales, luego 0x4c y movimiento sin destrucción por material |
| `duelo.turn_timer` | `180` | = vanilla | oficial | Segundos por turno (0 = sin límite) |
| `duelo.turn_limit` | `True` | = vanilla | oficial | Límite de tiempo por turno activo |
| `duelo.tiempo_gunslinger` | `60.0` | = vanilla | oficial | Segundos por jugada en GUNSLINGER |
| `duelo.tiempo_respuesta` | `180.0` | = vanilla | inventado | Segundos para responder a una petición |
| `duelo.tiempo_margen` | `3.0` | = vanilla | tecnico | Margen en segundos del límite de tiempo |
| `duelo.retry_auto` | `2` | = vanilla | tecnico | Reintentos automáticos tras un error del motor |
| `duelo.field_change` | `True` | = vanilla | oficial | Cambio de campo (Field Spell) animado |
| `duelo.position_dialog` | `True` | = vanilla | oficial | Diálogo de posición al invocar |
| `duelo.announce_dialogs` | `True` | = vanilla | oficial | Diálogos de anuncio |
| `duelo.option_dialog` | `True` | = vanilla | oficial | Diálogo de opciones del motor |
| `duelo.sort_list` | `True` | = vanilla | oficial | Ordenar cartas (lista) |
| `duelo.counter_dialog` | `True` | = vanilla | ? | Diálogo de contadores |
| `duelo.hand_limit_dialog` | `True` | = vanilla | oficial | Diálogo de descarte por límite de mano |
| `duelo.turn_counter` | `True` | = vanilla | oficial | Contador de turnos de las cartas que lo usan |
| `duelo.declare_hint` | `True` | = vanilla | oficial | Pista al declarar carta / tipo |
| `duelo.ritual_sum` | `True` | = vanilla | oficial | Invocación por Ritual: el jugador elige los sacrificios con las ventanas del oficial (estrellas, marcas, lista del mazo); apagada = decide bot.ts |
| `duelo.turn_counter_player` | `True` | = vanilla | oficial | Cuenta de Final Countdown / Crush Card Virus / Gold Sarcophagus… al cliente (0x3b con P2, como el oficial; el cliente 080912 no la pinta) |
| `duelo.scripts_2008` | `None` | = vanilla | oficial | Scripts de 2008 (duelcore/scripts_2008): las cartas cuyo texto en el cliente 2008 era otro que el de CardScripts (erratas posteriores) se juegan como en 2008; 0 = scripts modernos de CardScripts (sin poner = 1) |
| `duelo.compat_chaininfo` | `None` | = vanilla | tecnico | Compat CHAININFO de duelcore: el chain.lua de CardScripts pide banderas que ocgcore-wasm 0.1.2 no conoce y cortaba las operaciones que registran un efecto (Strike Ninja no volvia); 0 = sin compat, solo para diagnosticar (sin poner = 1) |
| `duelo.opp_hand_pick` | `True` | = vanilla | oficial | Elegir una carta de la mano del rival (Trap Dustshoot, The Forceful Sentry, Confiscation…): aviso 244 y clic en su mano; apagada = decide bot.ts |
| `duelo.yesno_texts` | `True` | = vanilla | oficial | SÍ/NO de una carta con SU texto del cliente (Ryko «¿Destruir esta carta?», Judgment of Anubis, Messenger of Peace, Six Samurai «¿Destruir otra carta en lugar de…?»): la description de ocgcore es código<<20\|n; apagada = el genérico «¿activar el efecto?» de antes |
| `duelo.option_yn` | `True` | = vanilla | oficial | Yubel y Yu-Jo Friendship: su elección de dos efectos es un SÍ/NO del cliente (dlg 233 / 298) en vez de decidir bot.ts; apagada = decide bot.ts (opción 0) |
| `duelo.hand_open` | `True` | = vanilla | oficial | Mano ENTERA del rival a la vista (Trap Dustshoot, Crush Card Virus, Royal Tribute…) con 0x52 [1]/[0] como el oficial, y el 0x29 de la carta elegida; apagada = un 0x51 por carta (que abre su panel de detalle cada vez) |
| `duelo.number_texts` | `True` | = vanilla | oficial | Diálogo de número con el título y la etiqueta de la carta (Card Trooper 330/331 como el oficial, Sixth Sense 296); apagada = los genéricos «Elige una cantidad» / «%d» |
| `ocg.USE_BRAIN` | `True` | = vanilla | inventado | La NPC juega con el cerebro (research/ia); sin él, bot.ts |
| `ocg.SEND_PAUSE` | `0.05` | = vanilla | tecnico | Pausa en segundos entre mensajes seguidos del duelo |
| `ocg.NPC_THINK` | `0.8` | = vanilla | ? | Segundos antes de cada jugada visible de la NPC |
| `ocg.CORE_ERROR_AUTO` | `1` | = vanilla | tecnico | Errores de duelcore que se dejan decidir a bot.ts antes de cerrar el duelo |
| `duelo.npc_janken_azar` | `True` | = vanilla | inventado | Piedra-papel-tijera contra la NPC: mano al azar |
| `duelo.npc_janken_elige` | `'primero'` | = vanilla | oficial | Qué elige la NPC cuando gana el piedra-papel-tijera (primero \| segundo \| azar) |
| `duelo.CLOSE_OLD_DUEL_SOCK` | `False` | = vanilla | tecnico | Cerrar desde el servidor el socket de duelo viejo al llegar uno nuevo (DUDOSO) |
| `duelo.DUEL_MODE` | `'ocg'` | = vanilla | tecnico | Motor del duelo contra NPC |
| `ocg.DUEL_RULE` | `'MR1'` | = vanilla | oficial | Regla de duelo de ocgcore (MR1 = reglas de 2008; MR5 = actuales) |
| `ocg.NPC_DECKS_EXTRA` | `False` | = vanilla | inventado | Mazos PROPIOS (deducidos, no oficiales) para las 26 NPC sin receta en el cliente; apagada = esas NPC juegan con una copia del mazo del jugador |
| `duelo.DECK_CHECK` | `None` | = vanilla | tecnico | Comprobación del mazo que manda el cliente (off \| avisar \| bloquear; sin poner = avisar, con YO2_PVP_TEST = off) |
| `duelo.DUEL_RESERVA` | `'vigilar'` | = vanilla | tecnico | Modo de la reserva de duelo (seguridad_duelo) |

### Red y límites (`red`)

| id | vanilla | enhanced | origen | desc |
|---|---|---|---|---|
| `red.MAX_CONNS_PER_IP` | `32` | = vanilla | tecnico | Conexiones abiertas a la vez por IP |
| `red.MAX_CONNS` | `256` | = vanilla | tecnico | Conexiones abiertas a la vez en total |
| `red.IDLE_TIMEOUT` | `600` | = vanilla | tecnico | Segundos sin datos tras los que se cierra una conexión |

### Despliegue (entorno) (`sistema`)

| id | vanilla | enhanced | origen | desc |
|---|---|---|---|---|
| `sistema.MAX_SAVED_ACCOUNTS` | `64` | = vanilla | tecnico | Tope de cuentas guardadas en state |
| `sistema.PORT_OFFSET` | `0` | = vanilla | tecnico | Desplazamiento de TODOS los puertos (instancias de prueba) |
| `sistema.BIND_IPS` | `['127.0.0.1']` | = vanilla | tecnico | IPs en las que escuchan los puertos de juego y el HTTP |
| `sistema.PUBLIC_IP` | `DERIVADA` | = vanilla | tecnico | IP que se anuncia en la puerta (0x2003) y en 0x4306 |
| `sistema.NO_CLIENT` | `False` | = vanilla | tecnico | No lanzar yo2.exe |
| `sistema.LOGDIR` | `DERIVADA` | = vanilla | tecnico | Carpeta del log |
| `sistema.STATE_PATH` | `DERIVADA` | = vanilla | tecnico | state.json (y su base .sqlite) |
| `sistema.STORAGE` | `DERIVADA` | = vanilla | tecnico | Almacén: json \| sqlite (por defecto sqlite si existe la base) |
| `sistema.OWNER_ACCT` | `DERIVADA` | = vanilla | tecnico | Id de la cuenta del dueño (yo2srv/cuentas/sesion.py; en modo paquete ninguno salvo YO2_OWNER_ACCT) |
| `sistema.ADMIN_PANEL` | `True` | = vanilla | tecnico | Panel de administración (8790) |
| `sistema.ADMIN_PANEL_PORT` | `None` | = vanilla | tecnico | Puerto del panel (por defecto 8790 + offset) |
| `sistema.ADMIN_TOKEN_FILE` | `None` | = vanilla | tecnico | Fichero del token del panel (pruebas) |
| `sistema.DB` | `None` | = vanilla | tecnico | Ruta de la base SQLite (por defecto <state>.sqlite) |
| `sistema.DB_CRYPT` | `None` | = vanilla | tecnico | Cifrado de la base (auto \| off \| …; sin poner = auto) |
| `sistema.DB_KEYFILE` | `None` | = vanilla | tecnico | Fichero de la clave de la base (por defecto en %LOCALAPPDATA%) |
| `sistema.PERFIL` | `None` | = vanilla | tecnico | Perfil de banderas: vanilla (por defecto) \| enhanced |
