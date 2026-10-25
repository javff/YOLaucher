# scripts_2008: cartas que se juegan con el texto del cliente 2008

`_vendor/CardScripts` (ProjectIgnis, AGPL) lleva los textos de HOY. Varias cartas tuvieron errata despues de 2008 y el
cliente Ver.080912 enseña el texto viejo: jugarlas con el script moderno es jugar otra carta. Aqui van los `cNNNNN.lua`
que **sustituyen** al de CardScripts para esas cartas. El vendor no se toca.

## Como funciona

- `duelcore.mts` lee esta carpeta (`scripts_2008/` junto a el; en el paquete, junto a `duelcore.mjs`) UNA vez al arrancar
  y su `scriptReader` busca primero aqui (por nombre de fichero) y luego en CardScripts. Tambien vale para
  `Duel.LoadCardScript("cNNNNN.lua")` y para los `s.listed_names` que carguen otro script.
- El evento `ready` de duelcore lleva `scripts_2008: [passcodes]` con lo cargado (y `compat_chaininfo`).
- Bandera `duelo.scripts_2008` (`yo2srv/config.py`, `yo2srv/BANDERAS.md`): variable `YO2_SCRIPTS_2008`. **Por defecto
  encendida** (comportamiento 2008 = el original); `YO2_SCRIPTS_2008=0` deja los scripts modernos. Cambia al reiniciar
  el servidor (el motor se lee al arrancar). `YO2_SCRIPTS_2008_DIR` apunta a otra carpeta (pruebas).
- Viaja en el paquete (`paquete/empaquetar.py` copia esta carpeta a `engine/scripts_2008/`, junto al `.mjs`) y en la
  exportacion publica (`publico/exportar.py`, `duelcore/**`).

## Como añadir una carta

1. Texto del cliente: `python duelcore/errata_informe.py --carta "Nombre"` (o `card_desc_e.bin` del `merge.dat`).
   **Esa es la fuente de verdad**, no la wiki ni la web de hoy.
2. Copia `_vendor/CardScripts/official/cNNNNN.lua` aqui y cambia SOLO lo que difiere del texto de 2008. Cabecera con el
   texto del cliente y la lista de diferencias (como las que hay).
3. Prueba dirigida en `test_errata_2008.py` (el 2008 y, al lado, el moderno con `YO2_SCRIPTS_2008=0`).
4. Si el script usa cadenas (`aux.Stringid(id, n)`), son las de `cards.cdb` de hoy: reutiliza las existentes.

## Cartas

| Passcode | Carta | Texto 2008 (cliente) | Que cambia frente a CardScripts |
|---|---|---|---|
| 16226786 | Night Assailant | FLIP: Select 1 monster on your opponent's side of the field and destroy it. When this card is sent directly from your hand to the Graveyard, return 1 Flip Effect Monster from your Graveyard to your hand. | sin `except "Night Assailant"` |
| 21502796 | Ryko, Lightsworn Hunter («Raiko») | FLIP: You can destroy 1 card on the field. Send the top 3 cards of your Deck to the Graveyard. | objetivo al activar; destruye y luego manda 3 del Deck (no simultaneo) |
| 56570271 | Destiny HERO - Disk Commander | When this card is Special Summoned from the Graveyard, draw 2 cards. | sin «no se puede Invocar el turno que va al GY», sin «una vez por Duelo», robar es obligatorio |
| 41006930 | Strike Ninja | You can remove this card from play until the End Phase of this turn by removing from play 2 DARK monsters in your Graveyard. ... once per turn. | coste solo del Cementerio |
| 57728570 | Crush Card Virus | Tribute 1 DARK monster with 1000 or less ATK. Check all monsters your opponent controls, your opponent's hand, and all cards they draw (until the end of your opponent's 3rd turn after this card's activation), and destroy all monsters with 1500 or more ATK. | mira campo y mano al resolver y cada robo del rival 3 turnos; sin proteccion de daño ni destruccion del Deck |
| 26202165 | Sangan | When this card is sent from the field to the Graveyard, select 1 monster with an ATK of 1500 or less from your Deck, show it to your opponent, and add it to your hand. Then shuffle your Deck. | sin «once per turn» (dos Sangan caidos a la vez buscan los dos), sin el bloqueo «cannot activate cards with that name this turn», baraja el Deck |
| 78010363 | Witch of the Black Forest | idem con DEF 1500 o menos | igual que Sangan |
| 14878871 | Rescue Cat | You can send this face-up card to the Graveyard to Special Summon 2 Level 3 or lower Beast-Type monsters from your Deck. Those monsters are destroyed during the End Phase. | sin «effects are negated» ni «once per turn» |
| 8131171 | Sinister Serpent | During your Standby Phase, if a "Sinister Serpent" exists in your Graveyard, you can return the "Sinister Serpent" to your hand. | sin el destierro de la End Phase del rival ni «once per turn» (cada copia del Cementerio vuelve) |
| 21593977 | Makyura the Destructor | During the turn this card is sent to the Graveyard, the owner of this card can activate Trap Card(s) from his/her hand. | desde cualquier sitio (no solo del campo), sin limite (no «1 Trap»), continuo sin cadena (base: pre-errata/c21593987.lua de ProjectIgnis) |
| 40737112 | Dark Magician of Chaos | When this card is Normal or Special Summoned, you can select and add 1 Spell Card from your Graveyard to your hand. Monsters this card destroys by battle are removed from play instead of going to the Graveyard. If this face-up card is destroyed or removed from the field, it is removed from play. | recupera la Magia AL INVOCARLA (no en la End Phase); el monstruo destruido en combate se destierra EN LUGAR de ir al Cementerio (EFFECT_BATTLE_DESTROY_REDIRECT), no despues |
| 37383714 | Soul Rope | Activate only by paying 1000 Life Points when a monster you control is destroyed and sent to the Graveyard. Special Summon 1 Level 4 monster from your Deck. | vale cualquier destruccion (no solo «by a card effect»); para la de combate se registra tambien con EVENT_BATTLE_DESTROYED (ocgcore pierde el EVENT_TO_GRAVE que cae en el Damage Step) |
| 93431518 | Gateway to Dark World | If you activate this card, you cannot Summon other monsters this turn. Special Summon 1 "Dark World" monster from your Graveyard. | se puede activar despues de haber invocado (el requisito «no haber invocado antes» es el juramento moderno) |
| 95727991 | Catapult Turtle | Tribute 1 monster on your side of the field. Inflict damage equal to half of the Tributed monster's ATK to your opponent's Life Points. | sin «once per turn»: tantas veces por turno como monstruos se tributen |
| 4906301 | Necro Gardna | Remove from play this card in your Graveyard to negate 1 attack from a monster your opponent controls. | solo con un ataque ya declarado (el de hoy se puede activar antes del combate y anula «el siguiente ataque») |
| 47297616 | Light and Darkness Dragon | ... When a Spell or Trap Card is activated, or the effect of an Effect Monster is activated, that activation is negated and this card loses 500 ATK and DEF. ... | sin «once per Chain»: anula toda activacion de la cadena (mientras tenga 500 o mas de ATK/DEF, para que dos LaDD no se anulen sin fin; su propio efecto no se anula) |

## No es de esta carpeta: compat CHAININFO

Strike Ninja no volvia en la End Phase **no por el texto**: el `chain.lua` de CardScripts envuelve
`Duel.RegisterEffect` y pide propiedades de la carta con `CHAININFO_TRIGGERING_LINK/LSCALE/RSCALE` (31-33), que
ocgcore-wasm 0.1.2 no conoce (`Passed invalid CHAININFO flag`). Cualquier carta que registre un efecto desde su
operacion se cortaba justo ahi (el destierro ya hecho, la vuelta sin registrar). `duelcore.mts` carga un parche Lua
(`COMPAT_CHAININFO_LUA`, bandera `duelo.compat_chaininfo`, `YO2_COMPAT_CHAININFO=0` lo apaga) que contesta `nil` a esas
tres banderas. Es un arreglo de motor para TODAS las cartas, no de 2008.

## Revisadas contra el texto de 2008 y NO cambiadas (solo plantilla / redaccion)

Tethys (Goddess of Light), Prohibition, Seismic Shockwave, Kuriboh, Magical Hats, Trap of Darkness, Virus Cannon, Desrook
Archfiend, Thunder King Rai-Oh, Puppet King, Dark Paladin, Rainbow Dragon, Blue-Eyes Shining Dragon, Big Shield Gardna, D.D. Crow,
Kunai with Chain, Rise of the Snake Deity. Notas por si el cliente 2008 contradice algo:

- **Kuriboh**: el efecto es el mismo, pero ocgcore solo lo ofrece al empezar el Damage Step (`EVENT_PRE_DAMAGE_CALCULATE`), no al
  declararse el ataque; si el original lo ofrecia al declarar, el aviso saldria mas tarde (el resultado es el mismo).
- **D.D. Crow**: el texto de 2008 no dice «select» (podria no apuntar); el script de hoy apunta. Solo cambia que el objetivo se
  fija al activar.
- **Prohibition**: mismo efecto; pide `Duel.AnnounceCard` (ANNOUNCE_CARD), que depende del traductor (no se toca aqui).
- **Puppet King**: el texto de 2008 no dice «from the Deck» (el de hoy si); ambiguo, se deja el de hoy.
- **Trap of Darkness**: hoy no puede copiarse a si misma («except "Trap of Darkness"»); el texto de 2008 no lo excluye. Caso limite.
- **Magical Hats**: el efecto es el mismo (lo cubre otro trabajo: el traductor/colocacion de las cartas boca abajo).
- **Doomcaliber Knight** (se comprobo de paso): ya es obligatorio (`QUICK_F`) como el «you must Tribute» de 2008; sin cambios.

### Candidatas sin tocar (el texto de 2008 no dice «once per turn» y el script de hoy si)

Dark Master - Zorc, Chaos Emperor Dragon - Envoy of the End, Gaia Soul the Combustible Collective, Goddess of Whim,
Anteatereatingant, Union Rider. Con el texto al pie de la letra serian ilimitadas; no se han cambiado porque podria ser una
omision de la traduccion al ingles y no una regla distinta (Catapult Turtle si, por ser un caso conocido). Decision del owner.

Tambien **Mausoleum of the Emperor**: el texto de 2008 es pasivo y vale para cualquier numero de Tributos
(1000 LP por cada uno, tambien 3); el script de hoy es una ignicion que solo cubre 1 o 2 Tributos.
