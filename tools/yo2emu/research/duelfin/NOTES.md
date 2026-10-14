# End of duel: surrender, win/loss, back to the map

Static analysis of the decompiled client plus Ghidra (`tools/yo2emu/ghidra/a7_multi.py` = `a4_multi.py` plus the
`atx` job, which creates a function exactly at a given address).

- `proposal.py`: ready-to-use functions plus a self-test (`python proposal.py`: sizes against the client message
  table, sequences and `ast.parse`).

Confidence markers: **C** = CONFIRMED (decompiled + a second check: disassembly, a second reader or a table in the
exe), **P** = PROBABLE, **D** = DOUBTFUL.

## 0. Summary

1. **Why the client used to hang.** The client has no way of its own to leave `CDuelScene`: the scene only ends when
   the 0x6202 queue brings **type 0x4d** (sets `_DAT_012d2b4c = 1`, the step table 0xcba500 reaches its end and
   `CDuelScene::Update` (0x799470) loads **scene 0x1d**). If the server never sends it, there is no way out. There is
   also no "I'm leaving" message from the client during the duel (C: complete list of client send sites, 122 of them).
2. **Scene chain after the duel (C, factory `FUN_0061dc30`):**
   - 0x1c `CDuelScene` → 0x1d `CDuelEndScene` (0x8bacc0) → 0x20 `CResultScene` (0x8c1630) → "Next" →
     0x19 `CCardPackCheckScene` (0x850ad0) → **0xc `CAreaMapScene`** (0x9faba0).
   - Other destinations: 0xa `CMyRoomScene` (if the duel started in My Room, `DAT_012a909c`), 0xd `CTournamentScene`
     (lobby type 8), 0x25 `CCardSwapScene` (between games of a match), 0x1e Janken.
3. **The missing lobby piece: `0x4800 YG_MSG_CARDPACKLISTNOTICE` (C).** `CResultScene::Update` (0x8c3f30) only shows
   "Next" and closes the duel session when `lobby+0x39 == 2`, and only the 0x4800 handler (`FUN_006bbe20`) sets it.
   Without 0x4800 the client stays on the results screen forever. Empty data is enough.
4. **Surrender (C):** bit 11 of the COMID masks = "Surrender" (texts 4111/4124, `cursor_?_surrender.png`). The client
   sends `0x6350 (player, zone, index, 11)`. The server decides; the client does nothing else.
5. **Result = client's point of view (C):** 1 = win, 2 = loss, 3 (or anything else) = draw. Valid for engine command
   0x05 (WIN/LOSE/DRAW animation) and for P0 of 0x4d (result in `CResultScene`).

## 1. Sequence the server must send (every way of ending)

Duel port, in the 0x6202 queue (`end_duel_msgs`):

| # | Message | Content | Purpose | Conf. |
|---|---|---|---|---|
| 0 | 0x6202 type 0, cmd **0x23** (optional) | B = loser<<15 \| 0x23, P0 = reason | `FUN_007162f0`: LIFE_SET of the loser to 0 (animation), reason in the winner's `+0x30`, `DAT_012cc2e4 = (loser == me) + 1`. Only if LP did not reach 0 in combat (surrender, time, disconnection) | C |
| 1 | 0x6202 type 0, cmd **0x05** C_DUEL_END | B = 0x0005, P0 = result, P1 = reason of player 0, P2 = reason of player 1 | `FUN_00712840`, several frames: if the reason (P2 if P0 = 2, otherwise P1) is in 3..10, DUEL_VIEW 0x3b (Exodia/FinalCount); then DUEL_VIEW 2 DUEL_END → `CDuelEndEffectTask` (win/lose/drow) | C |
| 2 | **0x6613** DUELSCORE | 9×u32 | `CResultScene` reads it **when created** (`CWindowResult` windows "%s WIN LOSE SCORE" = A0, A1, A3 / B0, B1, B3). Send it before 0x4d | C (format and readers) / P (labels) |
| 3 | 0x6202 type **0x4d** T_FINISH | P0 = result, P1 = closing (0 normal, 3 fill in, 5 close now) | `FUN_007905d0(P1)` and `FUN_00790590(P0)` → result of the game/match in `DAT_00cd82d0`; `_DAT_012d2b4c = 1` → scene 0x1d | C |

Lobby, through the room connection that received 0x4288 (`lobby_after_duel_msgs`):

| # | Message | Content | Purpose | Conf. |
|---|---|---|---|---|
| a | **0x4140** PLAYERDATAUPDATE | 125 B with wins/losses and `NPCBIN[npc]` | profile and the `CWindowResultNpc` window, which adds the NPC's NPCBIN (`FUN_008c3c30(DAT_00cd82dc)`) when created. **Before 0x4d** | C (format) / P (timing) |
| b | **0x4237** NPCPRESENTTYPE | s32 | consumed by the NPC the next time you talk to it (`FUN_0089d590`): 1 → dialogue 8 if you won (`DAT_00cd82d0 == 1`) or 10 otherwise; 2 → dialogue 12; other → normal dialogue | C (read) / P (meaning) |
| c | **0x4800** CARDPACKLISTNOTICE | n×u16 pack id (empty is fine) | `lobby+0x39 = 2` → "Next" on the results screen. If it carries packs, `CCardPackCheckScene` opens the picker → 0x4802 SELECTCARDPACK u16 → server answers 0x4803 (s32 0, n×(u16 card, u8)) | C (id, format, flag) / P (pack flow) |

Afterwards, the client:
1. When "Next" is enabled, sends **0x0003** on the duel port (`FUN_0068cf60`): end of the duel session; the server
   closes it.
2. Goes through `CCardPackCheckScene`; with PBIN at 0 and no packs it requests nothing.
3. Loads `CAreaMapScene`, which requests the same as on first entry: 0x4210/0x4214/0x4218/0x421c/0x4221, 0x4280,
   0x4230 (seen in a recorded session). The server already answers all of those.

### Case by case

| How it ends | What the client sends | What the server does | Conf. |
|---|---|---|---|
| LP to 0 (combat) | nothing of its own (LP are changed by the server's commands) | detects LP ≤ 0 in its simulation → `end_duel_msgs(winner, "lp")`: reason 1, no 0x23 | P (reason 1 comes from `FUN_00720cc0`, which the client does not call) |
| Deck out | nothing (drawing from an empty deck makes `FUN_007120e0` set `+0x24`) | `end_duel_msgs(winner, "deckout")`: reason 2 | P |
| **Surrender** | `0x6350 (0, zone, index, 11)`: menu with bit 11 or a direct click (below) | `on_client_surrender()`: 0x23 for player 0 + 0x05 (P0 = 2) + 0x6613 + 0x4d. In a match, closes the match (P1 = 5) | C (message) / P (sequence) |
| Time out | nothing; the clock is kept by the server (0x6461 only draws it) | `end_duel_msgs(winner, "timeout")`. Optional `0x649b` (code 3, bit0 = player) → dialogue "%s ran out of time" (6514) | P / D (the dialogue) |
| Client disconnects | the socket drops (or 0x0003 arrives) | nothing is sent: close the session and count it as a loss + `duels_void` (`h_disconnect`) | P |
| Opponent disconnects (not for NPCs) | — | `0x649b` code 1 → "%s has disconnected" (4519), sets `+0x676 \|= 2` and `DAT_012a90c4 = 1` | C (effects) |
| Draw | — | `end_duel_msgs(None, "draw")` → P0 = 3, "drow" animation | C |
| No result (abort) | — | lobby **0x4310 DUELABORT** (s32): dialogue 4516 "Time is up. This duel will be considered as having no result". In `CDuelEndScene`, on OK it sends 0x0003 and returns to the map **without** the results screen. But if it arrives during the duel, the duel UI consumes it first (`FUN_0078c140`): dialogue only | C (effects) / D (timing) |

**Surrender without the menu (C, `FUN_007390a0`):** if the pointed slot is **empty** (`FUN_00707750 == 0`) and its mask
is **exactly 0x800**, a click sends `0x6350 (…, 11)` directly. With other bits, "Surrender" appears as one more line in
the action list. `surrender_masks()` puts 0x800 on the player's own deck (zone 0xd): click on the deck → menu with
Surrender (P). It only works while the client has permission to play (0x1a).

## 2. What the client executes, in detail

- **Queue executor (C, lockstep notes + disassembly at 0x7217f0):**
  - type 0x4d: `EDI = P0`, `ESI = P1`; calls `FUN_007905d0(ESI)` then `FUN_00790590(EDI)`; `_DAT_012d2b4c = 1`;
  - if `P1 == 3` and fewer than 3 games were played, it fills the missing games with P0.
  - Between entries, `FUN_00720f30` does not advance while the engine has pending commands (`FUN_0070a290`): the
    order 0x05 → 0x4d is respected automatically.
- **`FUN_00794a90` (match result, C):**
  - stores P0 in `DAT_012a908b[game number]`;
  - if it is not a match (`FUN_007904f0`: modes 1/4/8/0xb) or `DAT_012a9090 == 5`, then `DAT_00cd82d0 = P0`;
  - otherwise it counts wins and losses: difference +2 → 1; −2 → 2; ±1 with 3 games → 1/2; 0 with 3 games → 3.
    Otherwise it changes nothing (match undecided).
  - **`DAT_00cd82d0` is reset to 0 in the init of each `CDuelScene`** (`FUN_00795480`, disassembly 0x79808a
    `MOV [0xcd82d0], ESI(0)`); its static value is −1.
- **`CDuelEndScene::Update` (0x8bb6e0, created with `atx`; vtable 0xbeecfc[4]), states in `+0x80`:**
  - 0: if `lobby+0x31 == 2` (DUELABORT) → dialogue 4516 → 5. Otherwise `DAT_012cc2f4 ? 4 : 1` (4 waits for a click;
    `DAT_012cc2f4` has no writer in the decompilation → normally 1).
  - 1 → 3: if it is a match, `CMatchResultTask` (match scoreboard) → 6.
  - 5: dialogue OK → 0x0003, fade, `FUN_00a55ac0` (map / My Room / tournament) → 7.
  - 6, according to `DAT_012a9084` (mode):
    - modes 0/3/7/10/0xd → scene 0x20 (or 0x1e if tournament and draw);
    - modes 1/4/8/0xb with `DAT_00cd82d0 == 0` → next game (0x25 `CCardSwapScene`, or 0x1e if spectator/replay);
    - otherwise → 0x20;
    - **any other mode → stays in state 6 forever** (does not happen with mode 0).
- **`CResultScene`:**
  - init (0x8c1a70): result = `DAT_00cd82d0` (1 → window 0 wins, 2 → loses, other → draw; or `DAT_012a90a8` if ≠ 0);
    - if `DAT_012bb6b0+0x9c ∈ {0,1}` (`FUN_00a58630`, "NPC duel") → `CWindowResultNpc` with the NPCBIN of
      `DAT_00cd82dc`;
    - otherwise → two `CWindowResult` with DUELSCORE;
    - lobby of type 7/9/0xa/0xf/0x10/0x11/0x14 → GUNSLINGER (`CDuelGunsStarTask`).
  - update (0x8c3f30): waits for `lobby+0x39 == 2` (0x4800) and `+0xcd` (mode 0/1 or `ND+0x678 & 1`) → shows
    `CWindowResultNext` and sends 0x0003. "Next" (`FUN_008c4230`) → `DAT_012aeabc = 0xc` (0xd in a tournament) →
    scene 0x19.
- **0x649b DUELSVRINFOMSG (C, `FUN_0078c140`):** only handled with an empty queue. Each code opens a dialogue; on OK,
  `FUN_0078eed0/ef10/ef50` set `+0xc5` and `+0xc0/+0xc1/+0xc2` (later effect not traced: D). **Do not use it in the
  normal NPC flow.**

  | Code | Text | Extra effect |
  |---|---|---|
  | 1 | 4519 "%s has disconnected" | `+0x676 \|= 2`, `DAT_012a90c4 = 1` |
  | 2 | 6513 "%s has surrendered" | — |
  | 3 | 6514 "%s ran out of time" | — |
  | 4 | 4519 (another window) | — |

  The name comes from `ND+0x54c + (bit0 of the 2nd byte)*0x94`, i.e. player 0 or player 1 of PREPARATION_RESULT.
- **0x6461 TIMEUP_WARNING (C format):** u8 who (0/1, or 3 = global clock at `ND+0x534`), s32 seconds, u8, s32. It only
  draws: it ends nothing. The global clock (who = 3) is used by `CCardSwapScene` in modes other than 0/1
  (`FUN_00692e30`).

## 3. Against an NPC: what the lobby expects

- **Stats, NPCBIN and MP/DPP:** everything goes in 0x4140 with absolute values. `PS.on_npc_duel_end()` adds duels,
  wins/losses and NPCBIN (b0 = wins, b2 = losses, b1 = draws: P; b3 = progress of the NPC chain: C) and returns the
  PDU. If MP are awarded, add them to `rec.mp` before building the PDU.
- **Prize cards:** two ways.
  1. 0x4800 with packs: the player opens them in `CCardPackCheckScene` → 0x4802 → 0x4803 (P).
  2. ITEMGET 0x5205, which sets PBIN bits (intermediate prize or the per-NPC "コンプ景品" completion prize) and adds
     cards (C format). The rule for when each prize is earned was decided by the original server: it is not in the
     client.
- **NPC gift:** 0x4237 (read by the dialogues when you talk to the NPC again).
- **DUELPASS:** no DUELPASS cost at the end of an NPC duel and no cost message were found. According to the lobby
  research, DPP is only changed by PSELECTRESULT, 0x4140 and 0x4903 (P: not every writer of account+0x1248 was
  reviewed here).
- The duel does **not** upload anything to the server when it ends (C): there is no result message from the client.

## 4. Cross-check of earlier assumptions

| Statement | Check | Conf. |
|---|---|---|
| 0x4d: 1 player wins, 2 NPC wins, 3 draw | Yes, from the client's point of view. In a match it is the result **of the game**; the match result is computed by `FUN_00794a90` | C |
| The client picks the closing lines depending on LP | `FUN_007918c0` (called from the NPC dialogues, 0x89fc70) picks the line with `DAT_00cd82d0`, the turn and LP | C |
| The gift is decided by 0x4237 | yes: `lobby+0xdbd4` → dialogue 8/10 (with 1) or 12 (with 2) | C (read) / P (meaning) |
| Meaning of 0x6613 | WIN/LOSE/SCORE in the PvP windows (A0, A1, A3; B0, B1, B3). Not drawn in an NPC duel | P |
| Last u8 of 0x43a0 = 1 → match | `FUN_006baab0`: `lobby+0xda1 = u8`; if it is 1 → `DAT_012bb6b0+0x9c = 1` and `DAT_00cd82dc = u32 opponent` (NPC id). `FUN_0087ca70` → `FUN_00794d90(lobby+0xda1)` = mode 1 = match (`FUN_007904f0`) | C |

## 5. MATCH against an NPC (last u8 of 0x43a0 = 1)

- **Closing the match** (C, `FUN_00794a90`):
  - **win 2 out of 3:** P1 = 0 in every 0x4d; the client decides on its own (2-0, 2-1, or 3 games with a draw → 3);
  - **end after 1 game:** P1 = 5 → `DAT_00cd82d0 = P0` immediately;
  - **surrender or abandonment in the middle of the match:** P1 = 5, or P1 = 3, which fills the missing games with the
    same result.
- **Between games** (match still undecided, `DAT_00cd82d0 == 0`):
  1. `CDuelEndScene` shows `CMatchResultTask` and loads **0x25 `CCardSwapScene`** (side-deck swap,
     `src\Deck\cardswapEntry.cpp`).
  2. That scene only exits when its timer `FUN_00692e30(1)` reaches 0. It then calls `FUN_00794d90(2)` (mode 1,
     game +1) and loads Janken 0x1e (a new rock-paper-scissors: 0x6100/0x6101/0x6102/0x6103 on the same connection, P).
  3. **Problem (P, high):** in modes 0 and 1, `FUN_00692e30` always returns −1, so the timer never reaches 0. No other
     exit from scene 0x25 in mode 1 was found (the only callers of `FUN_00794d90`/scene 0x1e in that area belong to
     `FUN_008d4b80`). **Risk of hanging between games of an NPC match.**
- **Recommendation:** set the last u8 = 1 (so that the NPC "talks" and `CWindowResultNpc` appears), but **close the
  match after the 1st game with P1 = 5** (`DuelEnd(mode=1, single_game_match=True)`, the default). Try best-of-3 live
  only afterwards, watching scene 0x25.
- **Disconnection during rock-paper-scissors** (0x649b code 1):
  - in game 1, the Janken scene (`FUN_007c76b0`) returns to the map and sends 0x0003;
  - in game ≥ 2, it records player wins up to 3 and goes to 0x1d. C

## 5b. Best-of-3 PvP MATCH — `pvp_match.PVP_MATCH` (`YO2_PVP_MATCH`, default `1`)

**Why:** in a recorded session, after the 1st game both clients, on the SAME duel connection, re-sent their decks and
sent 0x6102; the server had already closed the game, ignored the loser's 0x6103, and the other client was left on the
"FIRST / SECOND" screen with the clock at 00:00. The room was a match room: its 0x4288 had byte 0 = **4**.

| What | Where | Conf. |
|---|---|---|
| Duel type of the room = byte 0 of 0x4288 (`dstyle`): 3 duel, 4 match (6: other, not seen). The client stores it in `DAT_012bb6b0+0x9c` and `FUN_00794d90(+0x9c)` gives mode `DAT_012a9084` = 4 (PvP match; 3 = PvP duel) | decompiled client code; `FUN_007904f0` (modes 1/4/8/0xb = match) | C |
| To start a match the client requires 3 DP (1 for a duel) | decompiled check `+0x1248 < ((+0x9c != 3) ? 3 : 1)` | C |
| End of each game: 0x4d with P1 = 0 → `FUN_00794a90` records the result in `DAT_012a908b[game]` and decides by itself (difference ±2, or 3 games; draw 3) | §2 | C |
| Undecided: `CDuelEndScene` → `CMatchResultTask` → `CCardSwapScene` (0x25) → `FUN_00794d90(5)` (mode 4, game + 1) → `CJankenScene` (0x1e). The timer of 0x25 in mode 4 is `ND+0x534` (`FUN_00692e30`) | `FUN_008d4b80` | C |
| **Game ≥ 2**: the init of `CJankenScene` (0x7c3be0-0x7c55e0, not decompiled; disassembled with capstone) reads the previous game's result (`DAT_012a908a[game]`). If it was a win or a loss: re-sends the deck (`FUN_00692840`: 0x6600/0x6601/0x6603, **no 0x600b**), sends **0x6102** (`FUN_00691c20`) and jumps to **state 7** with `+0x8f8 = 1` (chooses: lost the previous game) or `+0x8f8 = 2` (waits for the server's 0x6103: won it). If it was a draw: re-sends the deck, no 0x6102, normal rock-paper-scissors (state 0) | 0x7c45ae-0x7c4611, 0x7c554e-0x7c55a6 | C |
| Therefore **the player who LOST the previous game chooses who goes first**; the value of 0x6103 is the same as in game 1 (0 = I go first, index from that client's point of view) | state 7, `+0x900 = DAT_012a9078 / !DAT_012a9078` (`DAT_012a9078` = always 0) | C |
| The choosing client does not wait for an echo of its 0x6103 before sending 0x61fe (seen back to back in a recorded session); the waiting client needs the server's 0x6103 | recorded session | C |
| 0x649b code 1 in game ≥ 2: state 9 of `CJankenScene` records its own wins up to 3 (`FUN_00794a90(1)`) and goes to results (0x1d); in game 1 it returns to the map | `FUN_007c76b0` state 9 | C |
| Duel log: mode 4 (+0xba98) and +0xbbe6..8 = who went first in each game (the replay reads it in state 7) | research/repeticion/NOTES.md §7 | C (format) / P (that a match replay plays correctly: untested) |

**Server (`pvp_match.py`):**
- `Matches.reserve` records `match_style` (= `dstyle` of the host's room, `_room_style`). `PvpMatch._bo3()` = room 4 and
  `PVP_MATCH == "1"`.
- `_game_end` (called by `_on_end`, `_surrender`, `_timeout`, `_closed` and the 0x7ffd test hook): in a match it records
  the result (`games`, player 0's point of view) and decides like `FUN_00794a90` (`_match_over`). Undecided: 0x4d
  P1 = 0 and 0x6613 with the match score (`_score`, P), 0x4140 with 1 DP per game (persistence notes §13, P), **no**
  card, no 0x4800, no duel log; `_between_begin` (picks `chooser` = the loser; draw → rock-paper-scissors; timer
  `MATCH_WAIT`).
- The first 0x6600/0x6601/0x6603/0x6100-0x6103/0x61fe of the next game → `_next_game`: decks, hands, `echo6102`,
  `first`, `sent6103`, `ready`, `ask`, `log_fase`/`log_q` reset and `jwinner = chooser`. Then everything as in game 1:
  echo of 0x6102, the chooser's 0x6103 → echo, and (1 − v) to the other (`_maybe_6103`), both 0x61fe → `_start` (new
  PvpSession, same per-turn clock). No new 0x600c or 0x6005 (D: the side-deck swap does not change the size).
- When the match ends: 0x4d of the last game, card (n = 3, `pvp_card_offer(p, match=True)`, persistence notes §14, P),
  0x4800 and ONE duel log for the whole match (`_duellog_record` with mode 4 and who went first in each game); the
  server-side archive likewise.
- Surrender or disconnection in the middle of a game: the match ends immediately (0x4d P1 = 5; C for the effect / D
  that the official server did it this way, as §5 already said). Time out: the game is lost, the match goes on (D).
- Disconnection between games (or not coming back within `MATCH_WAIT` = 300 s, `YO2_PVP_MATCH_WAIT`; or a new deck
  blocked by `YO2_DECK_CHECK`): `_match_gone` → the remaining player gets 0x649b (1, 1) (its client awards itself the
  match, C), 0x6613 with the score, 0x4140, card, 0x4800 and the duel log; **no** 0x4d. The leaving player only gets
  the profile update (card goes straight to the trunk).
- `YO2_PVP_MATCH=0`: old behaviour (one game and close; the client hangs between games). `YO2_PVP_MATCH=cerrar`: one
  game with 0x4d P1 = 5 (the client considers the match finished, C).
- Spectator: unchanged (0x6006 still uses mode 7, single duel: sees the 1st game; D).

**Test:** `python fake_client.py pvpmatch --offset <free offset>` (2-0, 2-1, disconnection between games; games are
ended with **0x7ffd** = "I lose this game", only available with `YO2_PVP_TEST`).

**Still to verify live:** a real 2-0 and 2-1 match (`CMatchResultTask`, `CCardSwapScene` and its timer `ND+0x534` in
mode 4), that `CResultScene` draws the match score correctly, and that a match replay opens.

## 6. Doubts and risks

- **D** Reasons 6/7/8/0xb (cards 0x151e, 0x169c, flag +0x2c, turn limit): they come from `FUN_00720cc0`, which the
  client does not call. For the server, 0, 1 and 2 are enough.
- **D** The 0x4800 pack flow with data (picker `FUN_008444c0`): untested. Empty first.
- **D** Who writes `DAT_012cc2f4` (state 4 of `CDuelEndScene`, waits for a click): no writer in the decompilation. If
  the client ever stays on "click to continue", this is it.
- **D** The effect after "OK" in the 0x649b dialogues.
- **P** `DAT_012bb6b0+0x9c` in the Beginners' Room with the last u8 = 0. Other places set it (3/4/6); if it is 3, the
  result is shown with PvP windows and DUELSCORE. Harmless.

## 7. Server integration

`H = proposal.make_handlers(load_state, save_state, lobby_conn_getter, send_burst=...)` (the module is loaded by
`yo2srv/arranque.py` as `DFM`). Hook points:

| Where | What |
|---|---|
| lobby, 0x4288, when 0x43a0 is sent | remember the room connection and call `H["h_autoduelstart"](opponent, mode)` with mode = last u8 of 0x43a0 |
| duel port, 0x6350 | first `if H["h_6350"](c, data): return` (surrender) |
| duel simulation | when LP ≤ 0 or a draw fails → `H["finish"](c, winner, "lp" / "deckout", turns=…, lp_left=…)` |
| 0x6611 masks sent with every request | `surrender_masks(masks)` so that surrendering is always possible |
| duel port, 0x0003 | `H["h_0003"](c, data)` and close the socket |
| duel socket drops | `H["h_disconnect"](c)` |
| admin console | `H["finish"](duel_conn, 1, "surrender")` = emergency exit to unstick a live duel |

Order inside `finish`: 0x4140 through the lobby → duel queue (0x23?/0x05/0x6613/0x4d) → 0x4237? + 0x4800 through the
lobby. Check with `python research/duelfin/proposal.py`.

## 8. Adversarial pass (re-decompiled and cross-checked)

| Statement | How it was re-checked | Conf. |
|---|---|---|
| 0x4800 = CARDPACKLISTNOTICE | disassembly 0x6ac955 `CMP EAX,0x4800; JZ 0x6ac9f0` → `CALL thunk(0x6bbe20)` | C |
| `CResultScene` waits for `lobby+0x39 == 2` | disassembly 0x8c4117–0x8c4137 (`[[0x12711ac]+0xc6c]+0x39`); the only writer of `+0x39 = 2` in the decompilation: `FUN_006bbe20` | C |
| Scenes 0x1d/0x20/0x19/0x25/0xc/0xd/0xa | factory `FUN_0061dc30` + RTTI string / class name in each constructor | C |
| Result 1/2/other = win/lose/drow | init of `CDuelEndEffectTask` (`atx 0x7afcb0`: paths `duel_ef_win/lose/drow`) + argument passing disassembled at 0x79d1fa–0x79d206 + `CResultScene` + cmd 0x23 | C |
| 0x4d: P0 result, P1 into `DAT_012a9090` | disassembly 0x7217f0–0x721840 | C |
| `DAT_00cd82d0` goes back to 0 on every duel | references to 0xcd82d0 (Ghidra `callers`) → 0x79808a inside `FUN_00795480` | C |
| Last u8 of 0x43a0 → mode and NPC | `FUN_006baab0` + `FUN_0087ca70` + `FUN_00794d90` | C |
| Surrender = cmd 11 | `FUN_007390a0` (direct send with mask 0x800) + `cursor_e.txt` line 13 "降参" + texts 4111/4124 | C |
| Sizes | `proposal.selftest()` against the client message table (0x6613 = 36, 0x649b = 2, 0x6461 = 10, 0x4237 = 4, 0x4310 = 4, 0x4140 = 125) + 0x6202 = 40 (lockstep notes) | C |
| Exit from `CCardSwapScene` in mode 1 | `FUN_008d4b80` + `FUN_00692e30`; callers of `FUN_00794d90` and of scene 0x1e show no other path | P |

## 9. Suggested live test

1. Integrate with the last u8 of 0x43a0 = 0. During the duel, click on your own deck → "Surrender".
2. The log should show:
   - `DUEL_VIEW_LIFE_SET`, `DUEL_VIEW_DUEL_END iResult=2` and the LOSE animation;
   - scenes 0x1d → 0x20 (results) → "Next";
   - `<- 0x0003` on 5760;
   - scenes 0x19 → 0xc (map), and requests 0x4210…0x4230.
3. Repeat with `H["finish"](c, 0, "lp")` from the console: WIN, and wins +1 in the state.
4. Last u8 = 1: the NPC window (`CWindowResultNpc`) must appear, with the match closed after 1 game. Talk to the NPC
   again after sending `0x4237 = 1` → dialogue 8 (if you won).
5. For best-of-3: `single_game_match=False` and check whether scene 0x25 exits by itself (risk in §5).

## 10. Draw, end reason and duelcore errors

- **Problem:** the ocgcore bridge used to pass `1 if winner == 1 else 0`, so an ocgcore **draw** (winner None) or a
  close caused by a duelcore **error** was recorded as a player WIN; and the end reason was derived with
  `"deck" in str(reason)` on the MSG_WIN **number**, so every end came out as "LP", deck-out included.
- **Current behaviour:** `ocg_duel.py` passes `winner` as is if it is 0/1 and `None` otherwise (draw → 0x05 result 3
  + 0x4d P0 = 3 "DRAW", NPCBIN b2, as `end_duel_msgs(None, …)` already did); with `reason == "error"` it logs an
  `[ERROR] [ocg]` line saying the duel was closed by a duelcore error. `ocg_finish` uses `why_from_ocg(reason)` (in
  `proposal.py`): 0x1 → `lp` (reason 1), 0x2 → `deckout` (2), 0x10 → `exodia` (3, RUN_EXODIA), 0x11 → `finalcount`
  (7, RUN_FINALCOUNT; card 0x169c = Final Countdown), 0x15 → `destiny` (4, Destiny Board letters; 0x1468);
  "error"/"ended" → `abort` (no animation); anything else → `lp`. The same text is reported to the admin tool.
- **Confidence:** ocgcore WIN_REASON constants and the client's reasons (`FUN_00712840` / DUEL_VIEW 0x3b, cards above)
  **C**; that the official server sent 3/7/4 in those cases **P** (it is the only thing that triggers those
  animations).
- Test: `python research/duelfin/proposal.py` (includes these cases).
- **To check live:** (1) win or lose an NPC duel by LP: unchanged; (2) if a duel ever ends by deck-out, the log must
  say the end reason is 2 and the reason text "deckout"; (3) Exodia: Exodia animation instead of the normal ending.
  A real draw (rare) must appear as DRAW on screen and in the NPC record.

## 11. End ordering, and a crashed or failing duelcore

- **Ordering:** `OcgSession._end` waits until the connection's `Sender` has sent everything already queued
  (`Sender.drain()`: marker at the end of the queue, 20 s limit, returns immediately if the connection is closed) and
  only then calls `finish` (duelfin sends 0x05/0x6613/0x4d directly with `c.send`). Otherwise the 0x05 WIN/LOSE could
  slip in between the engine's last commands (the last LP animation). PvP was already ordered: `PvpMatch._finish` puts
  the end in the SAME queue of each client.
- **duelcore "error" while pumping (NPC and PvP):** if duelcore closed the duel, its "end" (reason "error") comes out
  of the next `wait` → end "without result". If the duel is still waiting (an answer the library cannot encode),
  bot.ts decides **once** (`CORE_ERROR_AUTO = 1`); a second consecutive error closes the duel. No loops.
- **Dead duelcore process:** each session keeps ITS process (`self.dc`); `_core()` raises `DuelCoreError` if that node
  process has died (`proc.poll()`). `TracedCore.next_event` checks `proc.poll()` every 0.5 s. `core()` starts a new
  node if the shared one died (logged as an `[ERROR] [ocg]` line): the next duel works without restarting.
- **Any exception** in `start`/`pump`/`on_client` (NPC and PvP) goes to `_fatal`: an `[ERROR] [ocg]` log line with the
  duel number, cause and traceback, `core.end` if the node is alive, and an end for the client with
  `finish(None, "error")` → duelfin `WHY_ABORT` = 0x05 result 3 + 0x6613 + 0x4d P0 = 3 (DRAW, no LP to 0), 0x4140 +
  0x4800 through the lobby; in PvP, to both. Never two ends (`ended`); an abandoned (`_abandon_duel`) or surrendered
  session does not send another one. A translator failure on a client move (duelcore has not advanced) does not close
  the duel: it is logged and the move is requested again (`_resend`).
- **PvP:** `PvpMatch._on_end` uses `why_from_ocg` (before, an error came out as "lp").
- **Confidence:** that the client leaves the duel with 0x05 + 0x4d P0 = 3 is the same as §10 (C); that the official
  server closed a server failure this way: D (no trace; it is the clean exit that already existed).
- **Test:** `python fake_client.py kill --offset <free offset>` kills the node process of a test instance in the middle
  of a PvP and of an NPC duel; both clients receive 0x4d P0 = 3 and 0x4140 → 0x4800; the next PvP and NPC duel work
  with a new node.
- **Limitation:** a node started before a runtime reload is not a process-watching `TracedCore`; if THAT one dies, the
  running duel can take up to 30 s to close (then just as cleanly). Nodes started later detect death within 0.5 s.

## 12. Hand-card reveal bit (0x4000)

- `DUEL_VIEW 0x12` HAND_SHOW (0x73e6xx): sets 0x4000 on the hand card and its state `+0x18 = 1` with timer
  `+0x1c = 0x200`. **C**
- `FUN_00758540` (every frame, per card): with `+0x14 == 0x100` and `0 < +0x18 < 8`, subtracts 0x10 from the timer and
  at 0 goes back to `+0x18 = 0` (32 frames). **C**
- `FUN_00762ac0`, called **every frame** from `CDuelScene::Update` (0x799470): for each resting hand card
  (`+0x14 == 0x100`, `+0x18 == 0`) it sets bit 0x4000 ("face up") only if the card is the player's or
  `FUN_006fa0e0` says so (opponent's hand visible: `FUN_006fa050` bit 1 of `DAT_012c89a0`, cards 0xfa1/0xfe0/0x1db9,
  replay modes); otherwise it clears it. **C**
- So **the client clears 0x4000 by itself**, without any server message. If a card stays turned live, the cause is
  something else (P: the card does not return to `+0x14 == 0x100`, or `FUN_006fa0e0` returns true). Engine command
  0x51 (`FUN_00713d00`) also sets bit 4 of `DAT_012c89a0[player]`; engine command 0x50 clears it (`FUN_00713b10`,
  shuffle hand + DUEL_VIEW 0x11). **C** (code) / D (effect of bit 4).
- Next step: reproduce live and check in the log whether 0x50 follows 0x51 and whether the card moves before the 32
  frames are over.

## 13. Disconnection in the middle of an NPC duel: the first duel connection

- **Problem:** the duel-port wrapper fixed `c.df_end` (which duelfin game belongs to this connection) with the FIRST
  message on the duel port. On **each client's first duel connection** that message is `0x0005` (heartbeat), which
  arrives before `0x6001`, the message that attaches the session. Without a session, `df_for(c)` returned the
  **global** duelfin, and when the socket dropped `on_conn_closed` compared it with the **session's** duelfin: no
  match, so neither `h_disconnect` (loss, DP, `duels_void`) nor the disconnect activity event ran; only a "duel socket
  closed with duel N unfinished" line appeared. Later connections from the same client start with `0x6001` and were
  counted correctly.
- **Real client order (C):** in recorded sessions, every first connection to 5760 brings `0x0005 #1` and then
  `0x6001 #2`; later ones bring `0x6001 #n` first (the numbering continues). That the bug happened with the fake
  client: **C** (reproduced). That it happened live with the first NPC duel after starting the client: **P** (same
  message order; no real cut in the recordings).
- **Current behaviour:** `df_end_mark(c, DF, msg_id)` (`yo2srv/duelo/sesion_duelo.py`, next to `df_for`): same as
  before but **not with 0x0005**; it is fixed with the first message that is not a heartbeat (0x6001, with the session
  already attached). Nothing else changes: a second 0x4288 before closing still does not count, finished duels, PvP
  and connections without a session are unchanged. The disconnection spends the DP and records the loss like any
  other disconnection (npcreglas `dp`, `no_record`), with an activity event "NPC · by disconnection".
- **Test:** `python fake_client.py desconexion --offset <free offset>` (0x0005 + 0x6001, duel started, socket cut →
  NPCBIN loss +1, `duels_void` +1, DP −1 and one activity event).
- **To check live:** start the client, challenge an NPC and close the client (or cut the network) in the middle of the
  duel: the log must report the duel as unfinished, the activity log a loss "by disconnection", the NPC record one more
  loss, and 1 DP less.
