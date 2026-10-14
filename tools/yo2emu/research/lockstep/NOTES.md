# Duel lockstep protocol (port 5760)

Static analysis with Ghidra plus reading of recorded sessions. Ghidra scripts: `tools/yo2emu/ghidra/a2_*.py`
(`a2_recreate`, `a2_fixfunc` and `a2_ptrtable` create or trim functions; run them only on a scratch copy of the
Ghidra project).

Notation:
- `ND` = CNetDuel object = `*(*(0x12711ac)+0xc70)`. It is the same object that dispatches the 0x6xxx messages
  (`FUN_00691310`).
- Confidence markers: **C** = CONFIRMED, **P** = PROBABLE, **D** = DOUBTFUL.

## 0. Summary

1. **Trigger found (C, and confirmed live).** The client does not wait for any flag: every frame the engine polls the
   queue at `ND+0x68f8` and executes it. **Only** the server fills it, with `DUEL_SETCOMMAND 0x6202`. Without 0x6202
   nothing happens.
   - Call chain: `CDuelScene::Update` (0x799470, vtable 0xbecf24[5]) → `FUN_00720f30` → step table 0xcba500:

     | Step | Function | What it does |
     |---|---|---|
     | [0] | — | `return 1` |
     | [1] | `FUN_00720f10` | → `FUN_00720fd0` → executor `FUN_00721020` |
     | [2] | — | end |

     Before each step, `FUN_0070a290` runs the engine's pending commands. While commands remain, the network queue is
     not read.
   - 0x6104 GOTODUELSTART only sets `ND+0x21 = 2` and nobody reads it. The sender of 0x6104 (`FUN_00691d60`) has no
     callers: dead code (C).
2. **The server is authoritative (C).** NPCs and the human are both played on the server. The client:
   - executes the engine commands it receives;
   - asks for permission (0x6611/0x6612 + 0x6202 type 0x1a) before letting the player click;
   - sends the chosen move (0x6350).

   This matches the other client research: there is no local CPU player.
3. **0x61ff and 0x61f0 are debug cheats (C).** They are only sent by `FUN_0079a540`, which requires the debug bit
   `+0x1214 & 0x800`:
   - 0x61ff = change a card;
   - 0x61f0 = LP, draw, etc.

   **0x61fe** is always sent: it is the end of the `CDuelScene` init (`FUN_00795480`) with u8 = "SetPosition" option
   (manual/auto). It is the best moment for the server to start the duel (C).
4. **`0x6103 SELECTFIRST_RESULT` carries the absolute index of the player who goes first (C):**
   - **0 = this client**, 1 = the opponent;
   - it goes through `ND+0x67b` → `+0x900` → `DAT_012a907c` → `DAT_012cc850` → `DAT_012cc2d0`, which is the player
     whose turn it is;
   - the client is always player 0 (`DAT_012a9078 = 0`, `FUN_00794d90`).

   **With `0x6103 = 1` the NPC goes first.** Also, with `JYANKEN_RESULT (0, (mine+2)%3)` the client computes that **the
   NPC wins**: `+0x8f8 = (opponent − mine) mod 3`, where 1 = the player wins, 2 = the opponent wins and 0 = draw. For
   the player to win, send `opponent = (mine+1)%3`. The client then shows "go first or second" and sends 0x6103.

## 1. Duel sequence (from the connection to 5760)

```
client                                     server
0x0005 -------------------------------->  0x0004 s32 0          (greeting and heartbeat)
0x6001 u32 id -------------------------->  0x6007 s32 >0         (ND+0x1c: 1 -> 2)
0x6600/6601/6603 (DUMMY deck 1,1,1,2..; extra 10,10,10)
0x600b -------------------------------->  0x600c (168 B) + 0x6005 u16 main, u16 extra
   [Janken scene]
0x6100 -------------------------------->  0x6100 (echo; ND+0x1e = 2)
0x6101 u8 hand ------------------------->  0x6101 u8 (unused), u8 opponent's hand
   (the client computes who wins)
0x6102 -------------------------------->  0x6102 (echo; ND+0x20 = 2)
  if the player wins:  0x6103 u8 who --->  (optional echo)
  if the NPC wins:                     <---  0x6103 u8 who (0 = player, 1 = NPC)
   [Duel scene 0x1c] init: FUN_00795300 (seed = time(0), decks, LP from 0x600c, first player)
0x61fe u8 SetPosition ------------------>  (the server starts here)
                                     <---  0x6202 type 0, B=0x0001 DUEL_START
                                     <---  0x6202 type 0, B=0x0055 DRAW P0=0xffff P1=5      (player 0)
                                     <---  0x6202 type 0, B=0x8055 DRAW P0=0xffff P1=5      (NPC)
                                     <---  0x6202 B=(F<<15)|0x0c / 0x0d / 0x0e  draw/standby/main1 phases of the first
  if F = player:                     <---  0x6611 (110 B) + 0x6612 (hand masks) + 0x6202 type 0x1a
0x6350 player, zone, index, cmd ------>   (the server decides and answers with 0x6202 commands + a new request)
0x6351 (pass/cancel) ------------------>
0x002e u32 3/4/5 (phase button) ------>
                                     <---  ... 0x6202 type 0, B=0x0004 TURN_CHANGE (bit = the player ending)
                                     <---  0x6202 type 0x4d  end (goes to scene 0x1d)
```

ND wait flags: `+0x1c+n`. The request sets 1 and the answer sets 2; `FUN_00693190(n)` consumes them.

| n | Byte | Request → answer |
|---|---|---|
| 0 | `+0x1c` | 0x6001/0x600b → 0x6007 |
| 1 | `+0x1d` | 0x600c |
| 2 | `+0x1e` | 0x6100 |
| 3 | `+0x1f` | 0x6101 |
| 4 | `+0x20` | 0x6102 |
| 5 | `+0x21` | 0x6104, unused |
| 6 | `+0x22` | 0x6006 / 0x6700 (spectator) |

## 2. Messages

### Server → client (dispatcher `FUN_00691310`)

> **Caution (P):** an id < 0x6101 that is not in the table falls into the `default` case = GOTOJYANKEN (`+0x1e = 2`).

| Id | Name | Format | Effect | Conf. |
|---|---|---|---|---|
| 0x6005 | OTHERDECKINFO | u16 main, u16 extra | size of the opponent's dummy deck (`+0x5f8/+0x5fc`) | C |
| 0x6007 | BOOKED_CHECK_RESULT | s32 | >0: `+0x670 \|= 1` | C |
| 0x600c | PREPARATION_RESULT | 168 B (A16,B16,u32,u32,s32 LP A,s32 LP B,6×16,4,4,4×u32) | each side's LP = the engine's starting LP (`FUN_00790790`) | C |
| 0x6101 | JYANKEN_RESULT | u8 (`+0x67a`, no use seen), u8 opponent's hand (`+0x600`) | the client computes the winner | C |
| 0x6102 | GOTOSELECTFIRST | — | `+0x20 = 2` | C |
| 0x6103 | SELECTFIRST_RESULT | u8 absolute index of the first player | see §0.4 | C |
| 0x6104 | GOTODUELSTART | — | `+0x21 = 2`, no reader | C |
| 0x6105 | COMPULSIONJYANKEN | u8 | `+0x676 = (x&3)<<3 \| 4`: the rock-paper-scissors scene forces the hand `(x>>3)&3` without sending 0x6101 (for when its time runs out; translator notes §15.5) | C |
| 0x6202 | DUEL_SETCOMMAND | u32 type, u32 B, 8×u32 P = **40 B** | entry n at `+0x68f8+n*0x28` (A), `+0x68fc` (B), `+0x6900..` (P). Counter `+0xb8f8` (−1 = empty), 512 slots | C |
| 0x6461 | DUEL_TIMEUP_WARNING | u8 who (0/1; 3 = global), s32 seconds, u8 paused, s32 | clock at `+0x5c0+who*0x94` (or `+0x534`); counted down with time(); bar of max. 180 s; at 0 the client sends nothing (translator notes §15.4, flag `turn_timer`) | C |
| 0x649b | DUELSVRINFOMSG | u8 code (`+0x675`), u8 (`+0x676` bit0) | code 1: `+0x676 \|= 2` (cut); code 4 with an empty queue: connection error | P/D |
| 0x6610 | SELDUELLIST_DATA | list of CARDS (not of duels): u8, u32 mode, u32, u32 n, u32, n × 4 u16 | `CWindowDeckList` window; mode 0xfe → RUN_FUSION table (translator notes §15.5) | C |
| 0x6611 | ONLINE_COMID_FIELD | 2 × [zones 0..15 except 11: u16 mask + u8; then 10×u8] = **110 B** | **clears all masks** and sets the field ones: `+0x66a4+p*0x12a+z*2`, u8 at `+0x676e+..`, 10 u8 at `+0x6764+..` | C (format) |
| 0x6612 | ONLINE_COMID_HAND | u16 n; n×(u8 p, u16 index, u16 mask); 2×(u8 p, u8 k, k×u8) | mask of each hand card (`+0x66c4+p*0x12a+i*2`) and u8 (`+0x677e+..`) | C (format) |
| 0x6613 | DUELSCORE | 9×u32: A0..A3, B0..B3, X | `+0x5a8..`, `+0x63c..`, `+0x680` | C (format) / D (meaning) |
| 0x6701 | WATCH_JSEEN | u8,u8,u8 | spectator | D |
| 0x6010–0x6017 | DUELLOG_* | replays | — | — |

### Client → server

None of these are in the client's send table: they do not carry the "request sent" string.

| Id | Format | Who / when | Meaning | Conf. |
|---|---|---|---|---|
| 0x6001 | u32 | init | check reservation | C |
| 0x6004 | 16 B | `FUN_00691760` | key or room? | D |
| 0x600b | — | init | preparation | C |
| 0x6100 | — | Janken | go to rock-paper-scissors | C |
| 0x6101 | u8 hand | Janken | the player's hand | C |
| 0x6102 | — | Janken (after the result) | ask for turn choice | C |
| 0x6103 | u8 | Janken (if this client won) | who goes first (0 = this client) | C |
| 0x6600 / 6601 / 6603 | 3×u32 / u32 n+n×u16 / u32 n+n×u16 | init | deck (dummy: 1,1,1,2,2,2… / 10,10,10) | C |
| **0x61fe** | u8 | end of the CDuelScene init | SetPosition option. **Start** | C |
| **0x6350** | 4×u32: player, zone, index, command | `FUN_007390a0` → `FUN_007222a0` | **move**. Field: zone = slot 0..10 and index 0; hand: zone 0xb and index = position; 0xc–0xf: index 0. Command = menu bit 0..13 (`DAT_012cc358`; table 0xbeb990 = 0..13) | C (format) |
| 0x6351 | — | `FUN_007222e0` | pass or cancel (right click, except classes 7, 10 and 11) | P |
| **0x002e** | u32 | phase buttons (`FUN_007d5030`) | 3, 4 or 5 = battle / main2 / end? | P (format C) |
| 0x6423 | u32 | dialogues (`FUN_0071f8c0`, DUEL_VIEW 0x32) | chosen index | P |
| 0x6426 | s32 | `FUN_007201f0` (list 0x76a3f0) | chosen number | D |
| 0x6432 | s32, s32 | `FUN_007206e0` | card, value | D |
| 0x004a | — | answer to type 0x4a | echo/ping | C |
| 0x6495 | u8 | `FUN_006927f0` | ? | D |
| 0x6700 / 6702 / 6703 | — | spectator | — | C |
| 0x61ff / 0x61f0 | 5×u32 / 4×u32 | debug only | cheats | C |

0x6350, 0x6351, 0x002e, 0x6423, 0x6426, 0x6432 and 0x61ff/0x61f0 call `FUN_00692300`, which **clears the masks** and
`+0x674` bit0 (end of the permission to play) (C).

## 3. 0x6202: types (executor `FUN_00721020`, table 0x721b40/0x721af0)

How the executor works:
- it processes **one** entry per frame and only if the engine is idle;
- type = low byte of the first u32;
- if a modal dialogue is open (`FUN_00743130(0x32)`), only type 0x11 is processed;
- types without a case are consumed with no effect (C).

| Type | Effect | Conf. |
|---|---|---|
| 0x00 | `FUN_00709b10(B, P0, P1, P2)`: queues an **engine command** (buffer 0x12c78a8, 256×4 u16) | C |
| 0x11 | nothing (the only one that passes with a dialogue open) | C |
| 0x1a | **move request** (only outside spectator or replay mode):<br>• `+0x674 \|= 1` and `DAT_012cc348` = class P0 (with P2≠0: 8→10, 9→11);<br>• `DAT_012a833c = 1`; auto-focus if class ≠ 2;<br>• P1≠0 → clears list B;<br>• P1=0, class>5 and list B not empty → list dialogue (DUEL_VIEW 0x32, 0xff) | C (effect) / D (classes) |
| 0x1b | B=0x32, P0=0xff: copies list A to list B and opens dialogue 0x32. B=0x32, P0=0xfe: copies A to C. B=0x33: RUN_LIST. Other B: DUEL_VIEW(B,P0,P1,P2) | P |
| 0x1c | DUEL_VIEW 0x1c (location packed with P2..P6, P0, P1) | P |
| 0x1d | `FUN_00744a50(uid=P0, card=P1)`: identity of a uid (reveal) | C |
| 0x1e | `u16 DAT_012cc3a8[P0] = P1` | C |
| 0x1f | `DAT_012c872c = P0`, `DAT_012c8730 = P1` | C |
| 0x40, 0x46, 0x4b, 0x4c | **set card**: P0 player, P1 zone, P2 index, P3 card (same id as the deck, goes through `FUN_0073bc50`), P4 serial, P5 owner → word = card \| owner<<14 \| serial<<23. In zones 0xb–0xf it registers `uid = owner + 2·serial → card`. **Can be used to impose the order and the real cards of both decks** | C (write) / P (use) |
| 0x47 | `FUN_00754340` (battle selection) | D |
| 0x48 | shuffles both decks (`FUN_006ec7e0(0/1)`) with the current seed | P |
| 0x49 | phase sub-state = 0x15 | D |
| 0x4a | the client sends message **0x004a** | C |
| 0x4d | **end of duel**: `FUN_00790590(P0)`, `FUN_007905d0(P1)`, `_DAT_012d2b4c = 1` → the step table ends → scene 0x1d. With P1=3 and match<3 it repeats | P |
| 0x4e / 0x4f | spectator or replay only | C |
| 0x60 / 0x61 | list A of pairs (size P0; add (P0,P1),(P2,P3)…) | C |
| 0x62 / 0x63 | clear or set an entry of the chain table | C / P |

## 4. Engine commands (type 0)

Command header:
- `B` = u16 `(player<<15) | id`;
- P0..P2 are u16;
- player 0 = the client, 1 = the opponent.

Dispatch in `FUN_00709cb0` (0x01..0x6b). Names come from the DUEL_VIEW each handler triggers; the `DUEL_VIEW_*`
strings are in `FUN_0073dd80`.

| Id | Handler | DUEL_VIEW / effect | Parameters | Conf. |
|---|---|---|---|---|
| 0x01 | 7127f0 | 1 DUEL_START | — | **C (live)** |
| 0x02 | 712d60 | clears turn flags | — | D |
| 0x03 | 712e80 | 0x48 | — | D |
| **0x04** | 712a10 | 6 TURN_CHANGE: `DAT_012cc2d0 ^= 1`, turn++, banner for (1 − command's player) | player = the one ending | C (effect) |
| 0x05 | 712840 | 2 DUEL_END (+EXODIA / FINALCOUNT) | P0 result (0/1/2), P1/P2 reason per player (3..10; 4 = Exodia) | P |
| 0x06 / 0x07 / 0x08 | — | animations 0x3b / 0x3c / 0x3a | — | D |
| 0x09 | 713090 | bit P0 of `DAT_012ca498` = P1≠0 | — | C |
| 0x0a / 0x0b | — | DUEL_VIEW 3 / 0x3d RUN_EXTRA | P0 | D |
| **0x0c–0x10** | 712c00(0..4) | 5 PHASE_CHANGE (player, phase 0..4: draw, standby, main1, battle (3 calls BATTLE_INIT), main2) | player | P |
| 0x14 | 713240 | 7 FIELD_CHANGE (field card P0) | P0 | P |
| 0x15 | 70a490 | 10 BATTLE_INIT | player | P |
| 0x17 | 70aa20 | 0xd BATTLE_RUN (damage calculation) | P0, P1 | P |
| 0x19 / 0x1a | — | 0xc (attack) | — | D |
| 0x23 / 0x26 / 0x27 | — | LIFE_SET (0xe), BGM (9), LIFE_RESET (0x10) | — | D |
| 0x2d | 7153f0 | CARD_MOVE + 0x35 RUN_SUMMON: **summon**. P0 = packed destination (bit0 player, bits1-5 slot, bit14, bit15), P1 = uid, P2 = slot value | — | P |
| 0x31 | — | 0x1e CARD_BREAK (destroy) | — | D |
| 0x3f / 0x40 / 0x41 / 0x46 | — | CARD_MOVE (move between zones) | — | D |
| 0x48 | — | 0x1c + RUN_SUMMON | — | D |
| 0x49 | — | 0x28 MONST_SHUFFLE | — | D |
| 0x4e | — | 0x11 HAND_SHUFFLE | — | D |
| 0x4f / 0x50 | — | 0x1d CARD_SET (set) | — | D |
| 0x51 | — | 0x12 HAND_SHOW | — | D |
| 0x54 | 712460 | shuffles the player's deck with seed `(P1<<16)\|P0` (anim 0x14, 0x15) | P0, P1 | P |
| **0x55** | 7120e0 | **draw P1 cards** (P0≠0xffff → message 0x3f). Takes index 0 of the deck and puts it at the end of the hand (CARD_MOVE). No cards → `+0x24 = 1` (loses) | P0, P1 | C |
| 0x57 / 0x58 / 0x59 | — | DECK_RESET / FLIPTOP | — | D |
| 0x5d | — | 0x39 RUN_DICE | — | D |

Engine structures:
- **Player:** at `0x12c8880 + p*0xe0c`:

  | Offset | Field |
  |---|---|
  | +0 | LP |
  | +0xc | number of cards in hand |
  | +0x10 | deck |
  | +0x14 | graveyard |
  | +0x18 | extra |
  | +0x1c | banished |
  | +0x24 | out of cards (loses) |
  | +0x30 | reason |
  | +0x34 | 11 zones × 20 B |
  | +0x124 | hand |
  | +0x3a4 | deck |
  | +0x624 | extra |
  | +0x69c | graveyard |
  | +0x994 | banished |

- **Zones** (`FUN_00707580`): 0–4 monster, 5–9 spell/trap, 10 field, 0xb hand, 0xc extra, 0xd deck, 0xe graveyard,
  0xf banished (C).
- **uid** = owner + 2·serial. The init numbers them 1 to N in list order and then shuffles with `time(0)`, so **the
  server does not know the starting order**. It has to be imposed with type 0x40 (P).

## 5. What the server must send to deal and open turn 1

With player 0 = human and F = the player going first. For F = 0, rock-paper-scissors needs
`opponent = (mine+1)%3`, and the player chooses "first", or the server sends 0x6103 = 0.

1. (Optional, recommended when the server runs the duel) 0x6202 type 0x40 for every deck slot of both players,
   **before drawing**: (p, 0xd, i, card_i, serial=i+1, owner=p). This also fixes the dummy deck of 0x6601: the real
   deck is set by the server (e.g. the profile's `.ydc`).
2. `engine(0x01)`: DUEL_START. Confirmed live.
3. `engine(0x55, 0, 0xffff, 5)` and `engine(0x55, 1, 0xffff, 5)`: 5 and 5. The log should show 10
   `DUEL_VIEW_CARD_MOVE`.
4. `engine(0x0c, F)`, `engine(0x0d, F)`, `engine(0x0e, F)`: draw (no draw on turn 1), standby, main1. Log:
   `DUEL_VIEW_PHASE_CHANGE iPlayer=F iPhase=0/1/2`.
   - (D) Do not send TURN_CHANGE on turn 1: `DAT_012cc2d0` already equals F since the init, and 0x04 would toggle it.
5. If F = 0, to allow clicking, in this order:
   - `0x6611` (110 B; all 0, or field masks);
   - `0x6612` (n = 5, (0, i, mask) for i = 0..4; lists (0,0) and (1,0));
   - `0x6202` type 0x1a, P0 = class (try **3**; with 0 or 2 no cards are highlighted), P1 = 1.

   Mask: bits 0..13 = menu options. To find out what each bit means, give each card a different bit and look at what
   the menu offers (D). Bit 12 = draw (automatic answer of DUEL_VIEW 4 with class 1: `0x6350 (0, 0xd, 0, 0xc)`) (P).
   Bit 11 alone = direct action without menu (P).
6. Answers:
   - `0x6350` → the server translates the move (hand + summon → `engine(0x2d, 0, loc(0, slot), uid, 0)`) and repeats
     step 5;
   - `0x002e 5` → `engine(0x04, 0)` + the NPC's turn: phase 0, `engine(0x55, 1, 0xffff, 1)`, phases 1 and 2, its
     moves, `engine(0x04, 1)`, and back to the player's turn (phase 0, draw 1, phases 1 and 2, step 5).

All of this is in `proposal.py` (`DuelSim`, `duel_reply_v2`).

## 5b. Adjustments after the first live test

| Finding | Evidence | Conf. |
|---|---|---|
| **Menu bits (COMID masks)**: bit c → cursor c+1 (`FUN_00786b10`). In `data/cursor/cursor_e.txt` line 1 is OFF, so bit c = line c+2. Bits: 0 generic (ON), **1 attack**, 3 special summon, **4 activate**, **5 summon**, 6 flip, **7 set (monster)**, **8 set (spell/trap)**, 9 to attack position, 10 to defence position, 11 surrender, 12 draw (no icon) | `cursor_e.txt` + `FUN_00786b10` + automatic draw answer `cmd 0xc` | P |
| The action list (CUSummonDialogSp / CWindowTaskListboxDuelCmd) opens on hover if the mask has bits of **0x27ff**. Each bit adds a line with the label from table 0xcd4a10. **Command −1** = the dialogue was closed without choosing (initial result −1, `+0x4d0`). With mask 0x3fff, bit 13 (label 0x13, outside the 17 icons) and surrender/draw also get in. Probable cause of the invisible menu; the "auto set" option may also matter | `FUN_00786b70`, `FUN_0077c940`, `FUN_00739440` | P |
| **Phase buttons**: `FUN_007d5050` only returns 3/4/5 if `FUN_00791460(n)` ≠ 0, i.e. byte n of the 10 u8 at the end of player 0's block in 0x6611 (`ND+0x6764+n`). With everything at 0, no button sends 0x002e | decompiled | C (byte) / P (3 = battle, 4 = main2, 5 = end) |
| Position in C_SUMMON: with P0 bits 14/15 = 0 the card comes out face down. Being tried: bit14 = face up and bit15 = defence. Spells/traps go to slots 5–9 with the same command | live | D |
| "Thinking…" on the player's side | not located. Hypothesis: the client shows it when it is its turn but it has no permission to play (`+0x674` bit0 = 0) between one move and the next 0x1a. If it stays on with the new 0x1a, try class 2 | D |

## 6. Running the NPC on the server: what is needed

- **ocgcore ↔ YO2 translator:**
  - every ocgcore message (MSG_DRAW, MSG_MOVE, MSG_SUMMONING, MSG_NEW_PHASE, MSG_NEW_TURN, MSG_DAMAGE, MSG_CHAINING…)
    is translated into YO2 engine commands (table in §4: the D entries must be completed);
  - ocgcore's SELECT_IDLECMD and SELECT_* for the human are translated into 0x6611/0x6612 + 0x1a (masks = which
    options ocgcore offers per card);
  - answers (0x6350 / 0x002e / 0x6423…) go back to ocgcore.
- **Risk:** the client runs its own rules when applying commands (`FUN_00712a10` applies effects of specific cards
  0x1740 / 0x1dd9…), so it can diverge from ocgcore. To minimise it:
  - use "effect" commands (move / set card / LP) and do not rely on the client engine making decisions;
  - re-impose the state with 0x40/0x1d when needed.
- **Card id table** YO2 ↔ ocgcore (from the project's card tables or from the `.ydc` files).
- **NPC AI:** ocgcore's, or a simple one (summon the strongest, attack when it wins).

## 7. Adversarial pass (re-decompiled and cross-checked)

| Statement | How it was checked | Conf. |
|---|---|---|
| 0x6202 is 40 B (u32,u32,8×u32) and goes to `+0x68f8/+0x68fc/+0x6900` | `FUN_00692580`: `(i+(n+0x2a0)*10)*4 = 0x6900+40n+4i`; the message table says u32,u32,u32 (collapsed loop); test OK | C |
| u32 values are big-endian | `FUN_0068ae20`: shifts by 24/16/8/0 | C |
| Type = low byte | disassembly 0x790915 `MOV CL, byte ptr [..+0x68f8]` | C |
| Type 0 → `709b10(B,P0,P1,P2)` in that order | disassembly 0x721099–0x7210c1 (pushes P2, P1, P0, B) | C |
| List of types with a case | table 0x721b40 read from the exe | C |
| 0x6611 = 110 B | loop 0x3352..0x347c step 0x95 = 2 players × 15 zones (skips 11) × (u16+u8) + 10×u8 | C |
| Format of 0x6612 | `FUN_00693030` | C |
| 0x6350 = 4×u32 (player, zone, index, cmd) | `FUN_00691e40` + `FUN_007390a0` | C |
| 0x6103 = absolute index, 0 = client | `FUN_007c76b0` states 7/8 + `FUN_00795300` + `FUN_00794d90` | C |
| Rock-paper-scissors winner | state 4: `+0x8f8 = (opponent − mine) mod 3`; state 5: 1 → highlights my hand | P (which of 1/2 means "I win" is deduced from the sprite and from the fact that with 2 the client waits for 0x6103) |
| Drawing takes index 0 and appends to the end of the hand | `FUN_007120e0` + `FUN_006f0600(.., 0xd, 0)` | P |
| 0x1a classes (1 = draw, 3 = main) | only hints (DUEL_VIEW 4 and the scene panel with classes 0..3) | D |
| Meaning of the mask bits | — | D |

Possible live failures to watch:
- the queue has 512 slots: do not send huge bursts;
- `0x6202` arriving while the executor holds the lock causes a busy wait (`FUN_00692580`); harmless if the receiver
  runs on another thread;
- 0x6611 clears the hand masks: always send it before 0x6612.

`proposal.py`: `python proposal.py` runs the size test against the client message table.

## 8. Rock-paper-scissors against an NPC

**What the client does (C, `FUN_007c76b0` = CJankenScene, scene 0x1e):**
- State 2: the player picks a hand and sends 0x6101 (u8 0..2). State 4: with the server's 0x6101 (unused u8, u8
  opponent's hand at `+0x600`) it computes `+0x8f8 = (opponent − mine) mod 3`.
- `+0x8f8 == 0` (draw): state 5 shows the hands for 8 frames, returns to 4 with `+0x8e4 = −0x100` and from there to
  **state 2**: the client repeats by itself and sends another 0x6101. The server has nothing else to do.
- `+0x8f8 == 1` (player wins) or 2 (opponent wins): `FUN_00691c20` sends 0x6102 and waits for its echo. With 1 it
  opens "first/second" and sends 0x6103 (or accepts a server 0x6103 if it arrives earlier). With 2 it sends NOTHING:
  it waits for the server's 0x6103 (`+0x67b`, absolute index: 0 = this client, 1 = the opponent).
- In mode 0xd (`DAT_012a9084`) the rock-paper-scissors result is the game result (`FUN_00794a70` →
  `DAT_012a90a8`, `FUN_008c1xxx`): it is not a duel against the CPU and says nothing about what an NPC would choose.
- The client has no NPC AI for this: the NPC's choice was made by the SERVER (C). Client texts: only "先攻/後攻"
  (6515/6516); nothing in `npc_data.csv`, `fuki`, `bgm.txt` or in the NPC avatar CSVs.

**What the official NPC chose after winning: NO DATA (D).** Searches in Japanese, English and Spanish (wikis, forums,
blogs, player notes) found nothing about YO/YO2/YO3; e.g. the
[Japanese Wikipedia article on 遊☆戯☆王ONLINE](https://ja.wikipedia.org/wiki/%E9%81%8A%E2%98%86%E6%88%AF%E2%98%86%E7%8E%8BONLINE)
does not mention it, and a player's write-up on YO describes NPCs only as "win N times" challenges. Several wiki and
archive sites could not be reached. The only similar data point is from ANOTHER game (Legacy of the Duelist, 2015:
against the CPU there are no draws and who goes first is random,
[Steam discussion](https://steamcommunity.com/app/480650/discussions/0/154644787623001246/)); it does not apply to the
2008 game. Default "first" (D): under 2008 rules the first player does not draw on their first turn, but going first
was still the usual choice.

**Server (`proposal.py`: `janken_reply`, `janken_npc`, `npc_elige_first`, `janken_reset`):**
- The NPC's hand comes from `random.SystemRandom`; draw = just answer (the client repeats); if the NPC wins, 0x6103
  with its choice follows the 0x6102 echo; a player 0x6103 when the NPC was the chooser is ignored.
- Per-connection state (`c.duel_st`): `jwin` (None / 0 player / 1 NPC), `jhands` (player hand, NPC hand) of the
  deciding throw, `first` (0 player, 1 NPC; passed to `OcgSession.start` on 0x61fe), `first_por`, `jrounds`. 0x6100
  clears it.
- Flags: `YO2_JANKEN_NPC_AZAR=0` (or `LS.DUEL_CFG['npc_janken_azar'] = False`) = old behaviour, the player always
  wins; `YO2_NPC_JANKEN_ELIGE` / `LS.DUEL_CFG['npc_elige']` = `primero` (default) | `segundo` | `azar` (first, second,
  random). In test instances (`YO2_PVP_TEST=1`) randomness is off unless `YO2_JANKEN_NPC_AZAR=1` (the standard tests
  send 0x6101 0 / 0x6103 0 back to back).
- The duel-port wrapper passes 0x6101/0x6102/0x6103 to `LS.duel_reply_v2` before the base handler, so the base
  handler's "the player always wins" branch for 0x6101 is no longer reached.
- Replays: the NPC replay archive (`npc_watch.py` + `research/repeticion/rep_archivo.py`) takes the hands from the last
  0x6101 (`c.npc_hand0/1`, the deciding throw) and who went first from `OcgSession.d.first`; checked with the NPC
  winning and going first.
- Test: `python proposal.py` (self-test with scripted hands).
