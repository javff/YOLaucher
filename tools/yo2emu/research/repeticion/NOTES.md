# Duel logs (replays) — 0x6010–0x6017

Confidence: **C** = read in the decompilation/disassembly, **P** = deduced from evidence, **D** = unknown (behind a
flag or left as an open question).
ND = CNetDuel object (`*(*(0x012711ac)+0xc70)`). Client source file name: `yo2NetDuel_DuelLog.cpp` (string at
0xc9aecc).

Files:
- `duellog.py`: builds the blob, the RLE and the message list `[(id, data)]` for a client; `Recorder` records the
  0x6202 messages.
- `cliente_sim.py`: what the client does, rewritten separately (header, chunks, finish, `.log`, list.txt, loader,
  decoder, replay filter, room assertion, viewer's block swap and the replay's rock-paper-scissors: `viewer_view`,
  `replay_janken`).
- `rep_archivo.py`: the SERVER-side replay archive (§8).
- In `pvp_match.py`: flag **`PVP_DUELLOG_RECORD`** (on by default).

## 1. Messages (server → client; big-endian unless stated otherwise)

| Id | Handler | Format | Effect | Conf. |
|---|---|---|---|---|
| 0x6010 HEADER | `FUN_006944b0` | u16 +0xba94, u16 +0xba96, cstr title +0xbaa2, u32 **+0xbcec bytes about to arrive**, u32 **+0xbcf8 decoded size**, u32 +0xba98 mode, u32 +0xbbbc, u32 +0xbbc0, 16 B name 0 (+0xbbc4), 16 B name 1 (+0xbbd5), u32 **+0xba9c date (time_t)**, 4 B that are NOT read (`FUN_0068a930` writes: +0xbba8 stays 0), u32 +0xbbac, u8 +0xbbb0, u8 +0xbbb1, u8 +0xbbb2 | `+0xbcf4 = calloc(1, bcec+0x40)`, `+0xbce8 = 0`; answers 0x6013 | C |
| 0x6011 COMMAND | `FUN_006947a0` (not `FUN_00695430`: that one is the decoder) | raw bytes | appends them to +0xbcf4 up to +0xbcec; stops if ND+0x678 & 2 (Cancel); answers 0x6013 | C |
| 0x6012 FINISH | `FUN_00694840` | no data | unless cancelled: writes the `.log` and a line of list.txt, frees the buffer, **ND+0x678 \|= 1** (CResultScene shows "Next"). No answer | C |
| 0x6014 FUSIONMAT init | `FUN_00697ef0` | u32 n (+0xbd00), u32 m (+0xbcfc), n×u16 (+0xbd08), n×u16 (+0xbe4a) | +0xbd04 = 0; answers 0x6013 | C (format) / D (live use) |
| 0x6015 FUSIONMAT | `FUN_00697fe0` | u32 × up to m → +0xbf8c | answers 0x6013 | C / D |
| 0x6016 SERCHINFO init | `FUN_00694340` | u32 total (+0xd790) | **+0xd78c = 0**; answers 0x6013 | C |
| 0x6017 SERCHINFO | `FUN_00694390` | u16,u16 pairs → +0xcf8c up to the total | answers 0x6013 | C |

- The reader's cursor stops at 0x400: each 0x6011 carries ≤ 1024 B (C). How much the official server sent per chunk:
  D (we use 1024).
- 0x6012 writes 8 B per entry of +0xd78c into a ~503 B stack buffer (`esp+0xcc`, "00000000" by default): **without
  0x6016 the counter is garbage and overflows the stack** (client crash, C). Useful maximum: 62 entries.
- `FUN_00697a70` / `FUN_00697d80`: older "structured" version of header/commands (no callers, dead code, C). Useful to
  understand fields: there each command is u32 type, u32 B, u8, u8, u16, 8×u32 and, if type 0x4d and the u8 is 0, it
  adds the u16 to +0xbba8.

Send order: **0x6010 → (0x6013) → 0x6011 × k → (0x6013 each) → 0x6016 u32 0 → (0x6013) → 0x6012**. The exact order
used by the official server: D.

## 2. What the client writes (FINISH)

Folder: `<profile>\duellogs\` (created by `FUN_00618300` together with `mail`, `duellogs`, `duellogs\favorites`; base
path from `[0x12714c4]`, P).

`<title>.log` (little-endian, `FUN_006952c0/695300/695290` = fputc byte by byte):
```
u16 +0xba94 | u16 +0xba96 | u32 0x28 | 32 B of 0 | u32 +0xbcf8 | +0xbcec received bytes
```
The title is used as is in `fopen(...,"wb")` without checking the result (fputc to NULL if it fails, P): only
`[A-Za-z0-9_-]`, ≤ 31 characters.

`list.txt` (one line, format at 0xc9af38, C):
```
<search> <title>.log <ba98> <bbbc> <name0> <bbc0> <name1> <ba9c> 0 0 <bba8> <bbac> <bbb0> <bbb1> <bbb2> 0 0 0 0 0 0 0 0
```
- `<search>` = 8 hex digits per 0x6017 entry (nibbles from low to high of each u16; `FUN_00695370`, P upper case) or
  "00000000".
- With ≥ 100 lines it deletes the oldest `.log` and its line (C).
- List reader for the menu: `FUN_00aa86xx` reads **ba9c with localtime** → date/time of the entry (C). Date 0 =
  "1970/1/1 1.00" (seen live with an empty header). What it draws from `bbbc/bbc0` (next to the names) and from
  `bbac` (filter `&4` in `FUN_00a2f900`): **D**. Currently the pids go in bbbc/bbc0 and 0 in bbac.

## 3. How it is viewed later (no server involved)

Duel log menu (`CDuelLogTask`, `CWindowModelDuellogList/Search/Operation/Tour`, `yo2Duellog.cpp`) → `FUN_0064eff0`
(duellogs) or `FUN_00770f30` (duellogs\favorites) → `FUN_00697860` → `FUN_00695430`:
1. `FUN_00697210` loads the `.log`: reads ba94, ba96, u32, 32 B; if **ba96 ≥ 0xd6** it reads a u32 size (≤ 0x9fffff)
   and the rest are **RLE pairs (value, count)**; if < 0xd6 it takes everything after the 0x28 B as is (the u32
   +0xbcf8 becomes the first 4 bytes of the blob). C (disassembly 0x697446–0x697560).
2. It only decodes if **ba94 == 0x80a and 200 < ba96 < 0xd9** (C). We use 0x80a / 0xd8 + RLE (what the official
   server sent: D).
3. After decoding: if my pid (profile `+0xc` of the active slot) == pid of block 1 → **+0xbaa1 = 1** and it swaps the
   two blocks (names, decks, parts, country, music; +0xbbe6..8: 0↔1, other → 0xff). Sets **ND+0x678 |= 4** (replay
   mode) and +0x684 = music. C
4. Launcher: `FUN_00a587a0(+0xbba3)` → world for the avatar parts; then it requires **+0xba98 == 3 (→ mode 10) or 4
   (→ mode 11)** and loads scene 0x1e (Janken, which in replay mode does not send 0x6100: `FUN_00691ad0` checks
   +0x678 & 4). C
5. **"Lobby Rank Setting Error" assertion (yo2GameManager.cpp L693 = 0x2b5)**: `FUN_00a587a0` accepts room type 1, 9,
   10, 0xb, 0xe, 0x10, 0x11, 0x14 (→ world 1, Academia), 7, 8, 0xd, 0xf (→ 0, Domino), 0xc (My Room → current world);
   **any other value → assertion and exit(1)** (C). It is +0xbba3 of the blob = b2 of the gate (`ROOM_KINDS` in the
   server). With an empty blob +0xbba3 kept whatever was there (0) → the assertion. Now the server sends player 0's
   room type (`PvpMatch._room_kind`), or 12 if unknown (P: that the official server used the room where the duel was
   arranged).
6. Replay: `FUN_0079d0f0`, with an empty 0x6202 queue, takes the next entry **of its own side (+0 == +0xbaa1)** with
   type ≤ 0x70 and (type < 10 or (type > 0xf and ≠ 0x1b)) and copies it (type, B, P0..P7) to the 0x6202 queue
   (+0x68f8). C. The executor ignores 0x1a in replay mode (lockstep notes §3, C).
7. Decks: in modes 10/11 both are dummies (`FUN_0079cbf0(p, ND+0x564+p*0x94, ND+0x568+p*0x94)`) with the sizes from
   the blob (C); the translator imposes the cards with 0x40 at the start, so the recording already carries them
   (checked in a recorded trace: 97 "set card" entries on side 0).
8. **Starting LP**: `FUN_0071fe10` takes them from ND+0x570/+0x604 (`FUN_00790790`, C) and the blob does NOT carry
   them: the replay uses those of the session's last 0x600c (P). With no previous duel since the client was started:
   unchecked value (D, risk).

## 4. Blob (decoded, little-endian; order of `FUN_00695430`, disassembly 0x69554c–0x6965f0)

| Bytes | Field (destination) | What we put | Conf. |
|---|---|---|---|
| 8×u32 + 32 B | read and discarded | 0 | C |
| u32 | +0xba9c date | time() | C (date: P in the blob, C in the list) |
| u8 | +0xbaa0 | 0 | C / D |
| u32 | +0xba98 mode | 3 (single) | C |
| u32 | discarded | 0 | C |
| 4×u8 | +0xbba2, **+0xbba3 room type**, +0xbba4, +0xbba5 | 0, room, 0, 0 | C (room) / D (rest) |
| u32, 3×u8 | +0xbbac, +0xbbb0..2 | 0 | D |
| 3×u8 | +0xbbe6..8 = **who goes first** in each game of the match (0/1 = block; swapped when changing sides; the replay reads +0xbbe5 + DAT_012a9088 in state 7 of the rock-paper-scissors, §7) | who went first (`PvpMatch.first`), 0xff, 0xff | C (earlier this held the winner: a wrong P) |
| 7 B + 32 B | skipped | 0 | C |
| 2 × 172 B | player: u32 pid (+0xbbb4), u32 (+0xbbbc), 16 B name, u32 **deck card count** (→ +0x564), u32 **extra** (→ +0x568), u32 **music** (→ +0x684; 0x20000010..12), 4×u8 (+0xbc04.. player 0 / +0xbc08.. player 1; **the 1st = rock-paper-scissors hand**, §7), u32 (+0xbc0c), 3×32 B avatar parts per world (→ +0x574/+0x585/+0x596, 16 B used), 4 B country (→ +0x5b8), 28 B not read | pid, pid, name, real sizes, 0x20000010, (hand, 0, 0, 0), 0, `duel_parts` (as in 0x600c), "ESP" | C (destinations, hand) / P (meaning) / D (the other 3 u8, the u32, real music) |
| 1 B | skipped | 0 | C |
| u32 N, u32, u32, 32 B | number of commands, two discarded | N, 0, 0 | C |
| N × 44 B | u32 type, u32 B, **u8 side**, u8, u16, 8×u32 P = the 0x6202 sent to that side | recorded live; u8/u16 = 0 (the replay does not use them) | C |
| u32 n, u32 m, n×u16, n×u16, m×u32 | fusion materials (+0xbd00…) | 0, 0 | C / D |
| u32 k, k×(u16,u16) | search (+0xd78c) | 0 | C / D |

## 5. Implementation (flag `PVP_DUELLOG_RECORD`, on)

- `attach`: each player's send queue is wrapped in `_RecTx`, which records every 0x6202 in `duellog.Recorder`
  (side = player index). It does not change what is sent.
- `_on_end` / `_surrender` / `_closed` (mid-game): after both `_finish`, `_duellog_record()` builds ONE blob for both
  (P: the official server sent the same one and the client swaps sides) and puts the 0x6010 in each queue; the rest
  goes out one by one in the 0x6013 branch (`self.log_q`). `_finish` stops sending the empty 0x6010 only when the flag
  is on.
- With the flag off: 0x6013 branch sends 0x6016 → 0x6012 only.
- Real size (recorded PvP trace): 835 commands, 37 KB blob, 13.5 KB RLE = 14 × 0x6011.

## 6. Open questions / risks

1. D fields of the list (bbbc/bbc0, bbac, bbb0..2) and of the blob (the last 3 of the 4×u8, the u32 per player, baa0,
   bba2/4/5): does anyone remember what the official duel log entry showed (level, wins, result)?
2. Starting LP in the replay (§3.8): test by watching a replay right after starting the client.
3. The real duel music is chosen at random by the client: we put 0x20000010.

## 7. Endless rock-paper-scissors loop when watching a replay

**Symptom:** opening a replay from the menu, the rock-paper-scissors screen (two fists) repeated forever; neither
forward nor back worked; the client had to be killed. The replay does not talk to the server. All replay files saved
up to then had the same defect (including those of correctly finished draws).

**Cause (C, `FUN_007c76b0` = CJankenScene, with ND+0x678 & 4):**
- State 2, after 0x3c frames: `+0x8ec` (my hand) = **ND+0xbc04** and `+0x8f4` (the other's) = **ND+0xbc08**: the 1st
  u8 of the "4×u8" of each player block of the blob (decoder: `puVar15 + 0x12` from +0xbbbc/+0xbbc0; the viewer's
  swap exchanges bc04..07 ↔ bc08..0b). 0x6101/0x6102 are not used.
- State 4: `+0x8f8 = (other − mine) mod 3` (1 = I win, 2 = the other wins, 0 = draw). On a draw, state 5 → back to
  state 2, which reads **the same** hands → endless loop.
- Those 4 u8 used to be 0 for both players (D in §4) → 0 against 0 = eternal draw. **That is the cause.**
- State 7: `+0x900 = ND[+0xbbe5 + FUN_00790700()]` (`FUN_00790700` = `DAT_012a9088`, the game number of the match; 1
  in mode 10) → `DAT_012a907c` = who goes first (research/lockstep §0.4). Therefore **+0xbbe6..8 = who goes first in
  each game**, not the winner as previously assumed (P in §4, wrong): with 0xff (draws) +0x900 ended up as -1, and
  with the winner the wrong player went first → the replay would be out of sync even after leaving the loop.

**Fix (server side, as the official server must have done):** `duellog.replay_start(hands, who_first)` and
`PvpMatch._duellog_record` put into each player's block the hand that decided the live rock-paper-scissors
(`PvpMatch.jhands`, the same as in the spectator's 0x6701, same 0..2 code as the u8 of 0x6101 = `+0x8ec % 3`, C) and
into +0xbbe6 who went first (`PvpMatch.first`, block index). That the official server recorded exactly the deciding
throw: P (it is the only one that does not loop). If anything were missing (it should not: the duel only starts after
the rock-paper-scissors), the server fills in its own values (P) and logs a warning: hands that do not tie, with the
first player as the winner, or the winner going first. `build_blob` always applies this: a looping duel log can no
longer be produced. The server log reports the hands and who goes first for every duel log.

**0x6017 / search: not needed for this (C).** +0xcf8c/+0xd78c/+0xd790 are only used by the 0x6016/0x6017 handlers,
FINISH (writes the 1st column of list.txt) and the decoder (constant searched across the whole exe with
`decomp_scalar.py`). The list.txt reader (`FUN_00694140`) turns that column into u16 pairs per entry: it is for the
menu's search feature (P), not for forward/back. What the pairs are: D. 0x6016 is still sent with 0 (necessary:
otherwise FINISH overflows the stack, §1) and no 0x6017.

**Already saved replays:** they live on the client's disk; the server cannot change them. A repair is possible
offline: rewrite +0xbc04/+0xbc08/+0xbbe6 of each `.log` with the hands and first player taken from the server log
(same decoded size; list.txt unchanged).

**Still to check in the real client (D):** that the replay goes from the rock-paper-scissors to the duel and that the
forward/back controls respond during the duel; starting LP (§3.8).

## 8. Server-side replay archive (`REPLAY_ARCHIVE`; server feature, not in the original game)

The official game kept nothing server-side: the duel log was written by the client on its own PC. To support hosted
tools, the server keeps its own copy of every duel, PvP and against NPCs. **What clients receive does not change.**

- `<data>/repeticiones/YYYY-MM/<pvp|npc>_<YYYYMMDD-HHMMSS>_<n>.log`, `<data>` = folder of `STATE_PATH`, or
  `YO2_REPETICIONES_DIR`. `<n>` = first free number for that date (temporary file + hard link: never overwrites).
- Content: the EXACT client `.log` (`cliente_sim.Client` fed with the duel log messages;
  `rep_archivo.log_de_mensajes`). PvP: the SAME 0x6010/0x6011/0x6016/0x6012 sent to both players
  (`PvpMatch._duellog_record`), byte for byte. NPC: the human is player 0 and the NPC player 1 (opponent id as pid, its
  name, deck and parts as in 0x600c), side 0 only (the 0x6202 the human received, recorded by `npc_watch.py`, without
  the "Do you surrender?" dialogue); on a cut, the disconnection ending that spectators receive.
- Rock-paper-scissors and who goes first (+0xbc04/+0xbc08/+0xbbe6, §7): PvP = `PvpMatch.jhands`/`first`; NPC = the
  duel's hands (`c.npc_hand0` from the client's 0x6101, `c.npc_hand1` from the 0x6101 the server sends: with the random
  rock-paper-scissors of research/lockstep these are the last, deciding throw) and who really went first
  (`OcgSession.d.first`). Nothing is assumed.
- `indice.jsonl`: one line per duel, append-only (process lock + file lock; one write per line). Example (test
  instance):
  `{"id": "2026-10/npc_20261007-165059_1.log", "tipo": "npc", "fecha": "2026-10-07 16:50:59", "fichero": "2026-10/npc_20261007-165059_1.log", "jugadores": [{"nombre": "Player1", "pid": 1000, "acct": "0123456789abcdef"}, {"nombre": "Aster Phoenix", "pid": null, "acct": null}], "npc": {"id": 84, "nombre": "Aster Phoenix"}, "ganador": 1, "motivo": "rendición", "causa": "surrender", "turnos": 1, "bytes": 2720, "comandos": 110, "duelo": "npc-ocg-791384604"}`
  `ganador` (winner) = index in `jugadores` (players) (-1 draw, null no result: engine down); `motivo` (reason) =
  normal, rendición (surrender), desconexión (disconnection), tiempo (time), empate (draw), error; `causa` = duelfin
  WHY_* (lp, deckout, exodia…); in PvP `npc` = null. Keys and values are in Spanish as written by the server.
- Flag `pvp_match.REPLAY_ARCHIVE` (on; `YO2_REPLAY_ARCHIVE=0` turns it off). With it, each player's queue is wrapped in
  `_RecTx` even if `PVP_DUELLOG_RECORD` and `PVP_WATCH` are off, and `npc_watch` records even with `YO2_NPC_WATCH=0`
  (the "Watch" feature still depends on `NPC_WATCH`).
- When: PvP in `_duellog_record` (normal end, surrender, time, disconnection, engine down = `_on_end` with "error");
  NPC when the player's 0x4d is seen (`_tap`) or when the duel is cut (`_cut`: disconnection). NPC winner/reason/turns:
  `OcgSession.finish` wrapped in `_arm`, the surrender (0x6423 is observed in `_observe`) or the cut. With no recorded
  0x6202 nothing is saved.
- Safety: blob, RLE and disk I/O run on a separate thread (`rep_archivo.archivar`); any failure = an `[EXC]
  [repeticiones]` log line and the duel ends normally. Each archived duel is logged with its path, size, number of
  commands, reason and winner.
- Files: `rep_archivo.py` (all disk handling), `pvp_match.py` (`REPLAY_ARCHIVE`, `_RA`, `_archive`), `npc_watch.py`
  (`_arch_info`, `_archive`, `_end_from_entries`).
- Known risk: a duel that ends milliseconds before the server stops may not be archived (the thread is a daemon).
