# 2008 world — notes

The notes on the "2008 world" (original NPCs, achievement titles) are in `research/lobby/NOTES.md`. This file covers
GM accounts and special titles.

Confidence: **C** = checked in the client (code or data) · **P** = probable · **D** = doubtful, or a decision of this
project.

## GM and special titles

### Summary

1. **KONAMI and GAME MASTER are staff titles, not prizes.** KONAMI = "Title exclusive to the development staff"
   (Japanese 開発スタッフ = "development team"). GAME MASTER = "Title exclusive to the guardians of the game". The event
   title is a different one: JUMP FESTA ("granted to players at the JUMP FESTA trials"). **C**
2. **The only thing the client does differently for a GM is the chat.** If the equipped title is GAME MASTER, every line
   that person writes appears **in blue, bold and slightly larger** for everyone. There is no name colour, icon, flag
   or special appearance. KONAMI changes nothing: it is just the title text. **C** (that it happens) / **P** (the
   exact colour)
3. **There is no in-game administration.** The client has no GM chat commands, hidden menus, kick, mute, teleport or
   invisibility. Konami did all that from its servers. The faithful alternative is server-side moderation (kick, ban,
   chat notice, maintenance, D-mail) from an admin tool (not included). **C** (not in the client) / **P** (that Konami
   did it server-side)

### What the client contains

| What | Where | Detail | Conf. |
|---|---|---|---|
| Title table | exe 0xc98d50 (`FUN_0068fc50` id → text, `FUN_0068fe10` bit → id) | 16 B per title: id, PBIN bit, name text, description text. **Nothing else**: no colour or icon. KONAMI = id 1 / bit 225 / texts 9150 and 7601. GAME MASTER = id 2 / bit 226 / 9151 and 7602. JUMP FESTA = id 3 / bit 227. Normal titles have ids from 0xF102 upwards | C |
| Title images | `merge2008/data` | There are none: titles are text only (profile, other players' card, "Change title" window) | C |
| **GM chat** | `FUN_00a77c70` (send chat), 0xa7892f | Reads the equipped title of the active character (`FUN_006a5bc0` + 0x112). **If it is 2 (GAME MASTER), it prepends "@1" to the text** (`FUN_00acdde0`, string 0xc83574) before sending it with 0x4400 | C |
| A hand-typed "@" | `FUN_00a77c70`, 0xa78144-0xa781b7 | The client sends it doubled ("@@"). So a normal player cannot colour their text | C |
| **Observed live with GAME MASTER equipped** | chat window | The text after "Name:" appears in **dark red** (the name keeps the player's own pink). The chat window uses its own palette for "@n": the "blue 18 px" below is the general style table, not the chat one | C (live) |
| Meaning of "@1" | `FUN_00604410` (draws styled text) | "@0"…"@9" select one of 10 font styles (each = a 60 B LOGFONT + a colour); "@@" = one @ | C |
| The 10 styles | init `FUN_00604cb0`; settings at 0x608a2c (global object DAT_0126fa8c + 0x2c) | Default: MS Gothic, 16 px, weight 400, white. Then: @0 black · **@1 blue, 18 px, maximum weight** · @2 red · @3 green · @4 teal · @5 magenta · @6 yellow · @7 sky blue · @9 white | C (values) / P (colours: the "#RRGGBB" reader in the same code stores BGR, so 0xff0000 = blue) |
| Incoming chat | `FUN_006bb780` (0x4402) | Copies the text as is (does not strip "@n"). The first u8 of the message is unused. Channels 0, 1, 2, 3 and 5 go to their tab; **4 is dropped**. No "GM" message type | C |
| "/" in the chat | `FUN_00a77c70`, `FUN_00a799c0` | "/Name text" = **whisper by name**: looks the name up among known players; if not found, text 5018 "There are no Players with this name." It is not a GM command | C (looks up the name) / P (that it is a whisper) |
| GM/admin strings | exe and `yo2text_*` | The exe has no "admin", "operator", "staff", 運営, ゲームマスター, 管理者… In the texts, "GM/ゲームマスター" only appears in titles 9151 and 7602 | C |
| Network messages | protocol table extracted from the client (not included) | No GM message: no kick, mute or moving players. Server notices are 0x4402 CHATNOTICE, 0x4700 MAINTENOTICE, 0x4306 and 0x5000 EVENTINFO | C |
| Player data | 0x4101, 0x4140, 0x4212/0x4216, 0x3112 (`research/persistencia/NOTES.md` §3) | No "account type" or "staff" field. The fields with no known use are read by nobody in the client: even if a 1 were sent, nothing would change | C |

### The small "7D" flag of the NPCs

It is not "7D": it is the small **"DA" logo of Duel Academia** (red D, blue A, little yellow star). Flag table in the
exe (0xd0b758; 12 dwords per entry: index, code, 22×15 crop in `flag_2215_01.png`, 42×28 crop in
`flag_4228_0N.png`): 146 countries with their ISO code, plus **NPCL** (yellow Millennium Eye, for the Domino NPCs) and
**NPCG** (DA logo, for the Academy / GX NPCs). Players carry their country's flag (4 B of country in LIST1).
**There is no GM flag**; the code "GM" in that table is Gambia. **C** (table and images) / **P** (L = Domino, G = GX).

### Period sources

Nothing was found about GMs in YO2 (searches in English and Japanese; a Feb-2008 fan blog does not mention them). All
of the above comes from the client alone. Who held these titles in the original game and whether the server enforced
them: **D**.

### Implementation (flag `GM_ACCOUNTS`, ON by default)

- **Where**: `research/mundo/proposal.py` (GM section). Module flag `GM_ACCOUNTS` (registry id `mundo.GM_ACCOUNTS`):
  on by default; `YO2_GM_ACCOUNTS=0` at startup turns it off, and it can also be changed at runtime.
- **Who is a GM**: `gm_cuentas.json` next to `state.json` (`{account: {nota, desde}}`; UTF-8 without BOM; on save the
  previous version is kept as `gm_cuentas.json.bak-gm`). Accounts are marked as GM with an admin tool (not included).
- **With the flag on** (all of this is a decision of this project, **D**; the client does not require it):
  1. 0x4174 with KONAMI (bit 225) or GAME MASTER (bit 226) from a non-GM account → 0x4175 = 1: not equipped.
  2. A GM account without GAME MASTER receives it when entering a map (ITEMGET(1,0), new-item window).
  3. Chat (0x4400) from a non-GM account loses "@0"…"@9" (keeps "@@"). GM chat passes as is: the others see it in
     blue, as in the original.
- **With the flag off**: anyone with the GAME MASTER title equipped writes in blue (that is what the client does).
- Limitations: a player who already had GAME MASTER equipped before the flag was turned on still shows it on their
  card (they only lose the chat colour). A Japanese client could have "@" as the second byte of a Shift-JIS character:
  the filter does not distinguish it (tested clients use cp1252). **D**
- Tests: `python research/mundo/proposal.py` (self-test, including GM), `python research/lobby/proposal.py --test`,
  `python fake_client.py pvp`.
- The server loads changes to this module on **restart**.

### How to see it in the game (step by step)

1. Give the character the title bit (KONAMI = PBIN bit 225, GAME MASTER = 226), e.g. with an admin tool (not included).
2. In the game, enter a map (or leave and enter again): the new-item window appears with the title.
3. Go back to **My Room** ("Main Menu" → "Return to your Room"). There, open the **Main Menu**: the third button is
   **"Change title"** → choose **GAME MASTER** → accept. The player card now says GAME MASTER. **In the lobby / maps
   that button does not exist** (see "Where is 'Change title'" below). **C**
4. Write something in the chat: your lines appear **in blue and bold** for you and for the others. With KONAMI the chat
   does not change; only the title on the card is visible.
5. With `GM_ACCOUNTS` on, the account must be marked as GM first, or the game will not let it equip KONAMI or
   GAME MASTER.

### Where is "Change title" (Main Menu)

**Short answer:** the button only exists in the Main Menu **of My Room**. In the lobby (the world maps) the same menu
appears without it. It does not depend on anything the server sends (not 0x4101, not 0x4140, not how many titles you
have): it is fixed per scene. Nothing to fix in the emulator. **C**

| What | Where | Detail | Conf. |
|---|---|---|---|
| Menu class | `CMainMenu`: constructor `FUN_00a650f0` (vtable 0xbf2750; the second one, 0xbf2734 at +0x340, has the click handler `FUN_00a67270`) | Window "メインメニュー" (text 5124) | C |
| Creates the 14 buttons | `FUN_00a655a0` (missing from the decompilation; disassembly 0xa65cd5-0xa65ea7) | `CWindowTaskButtonMain` (`FUN_00a63cf0`) at this+0x3b0…0x3e4 (= +0x70…+0xa4 of the handler). Index → text: 0 Edit Deck (5116) · 1 Profile (5112) · **2 Change title (5132)** · 3 Create Comment (5126) · 4 DUEL OPTIONS (6540) · 5 D-Mail (5114) · 6 Address Book (5127) · 7 Duel record (5119) · 8 DUELPASS (4050) · 9 Ignore list (5121) · 10 Friend List (5120) · 11 Team List (5313) · 12 Return to your Room (6002) · 13 Return to the Title Screen (6003) | C |
| Which are visible | `FUN_00a666f0(i)` shows / `FUN_00a66720(i)` hides (byte this+0x42e+i) → `FUN_00a66750` re-lays out the visible ones; `FUN_00a66880` hides them all | Decided by **whoever opens the menu**, not by the menu | C |
| My Room | `FUN_00828290` (called by `FUN_00826020`, My Room init, source `src/MyRoom…`) | Shows 0,1,**2**,3,4,9,10,11,7,5,8,13 = 12 buttons **with** "Change title", without "Return to your Room". `FUN_00828620` (also My Room) shows 0,1,2,13 in another state | C |
| Lobby / maps | 0x8611db-0x8612d7 (lobby scene, `src/Lobby/yo2LobbyEntry.cpp`) | Shows 0,1,3,4,7,5,9,10,11,8,12,13 = 12 buttons **without** "Change title" (2) or Address Book (6). If the lobby is of type 8 (`[[0x12711ac]+0xc6c]+0x9ed == 8`, tournament) only 0, 12 and 13 | C |
| Greyed-out button | `FUN_00a668b0` / end of `FUN_00a655a0`: button+0x350 = 0 if `DAT_0127188c`->vfunc(0xa4) (byte +0x1b4) = 1 | The same byte disables "Create Comment" and "Change title"; since Comment works, Title does too | C |
| On click | `FUN_00a67270` → `FUN_00adae50(5)` (free window) → `CChangeTitleTask` (`FUN_00ac73e0`) → `CWindowModelTitleConfig` | The list (`FUN_00ac9a30`) walks the table at 0xd2fc28 and shows the titles whose PBIN bit is set (`FUN_00ad6a90`); the rest appear empty. Choosing one → 0x4174 → 0x4175 (implemented) | C |
| "Miles" (5122) / "News" (5125) | — | Not `CMainMenu` buttons (not among its 14 texts) | C |

**What the player has to click:** Main Menu → "Return to your Room" → in My Room, Main Menu → "Change title" (third
button) → GAME MASTER → accept. Neither the server nor the client needs a restart: the bit has already arrived (the
"You have a new Title!" message appeared). If GAME MASTER appears empty in the list, leave and enter again (PBIN is
re-read on entry). **C** (button) / **P** (the re-read).
