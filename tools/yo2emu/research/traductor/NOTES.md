# Translator: ocgcore ↔ YO2 client (research notes)

These notes document `translator.py` (ocgcore/duelcore events → YO2 client engine commands, and client replies → duelcore
responses) and `cardmap.py` (card id mapping). Everything about the client comes from static analysis of the 2008
client (build 080912): decompiled code, disassembly and bytes of the executable, plus offline tests with duelcore and,
where stated, emulation of client functions with unicorn and live sessions with the real client.

Supporting material used during the research (a test harness with a simulated client, a unicorn-based emulator for
client functions, decompiler helper scripts, recorded duel traces, hot-reload scripts) is not part of the public tree.
Test results are summarised where they support a conclusion.

**Confidence legend** used throughout: **C** = CONFIRMED (code read and, where stated, verified live or emulated) ·
**P** = PROBABLE (code read, not verified live) · **D** = DOUBTFUL / unknown.

Notation: "the client" = the original YO2 executable; "the official server" = the original game server, whose behaviour
is only known indirectly (client code, recorded official duel logs); "rule of ours" = behaviour chosen by this project
where the official behaviour is unknown, always behind a flag.

## 0. Summary

1. **The translator works against real ocgcore.** The test harness plays complete duelcore duels with a "simulated
   client". That client applies every 0x6202 exactly as the yo2.exe engine executes it according to the decompiled
   code. After **every** decision its board is compared with ocgcore's (`query`): hand, deck, graveyard, banished,
   zones, face/position and LP. **Result of the reference run: 30/30 duels with no differences, 0 RETRY, 1,245
   decisions.** Player 0 chooses by reading the generated 0x6611/0x6612 and replies in bytes
   (0x6350/0x002e/0x6351/0x6423).
2. **The engine command set is complete** (0x01-0x6b, §2). The commands used by the translator have their parameters
   read from their handler. Important corrections to `research/lockstep/NOTES.md` §4:
   - in the engine, **P0 is the high half of B**: `B>>16` in Ghidra;
   - 0x23 is not LIFE_SET: it sets LP to 0 with a reason. LIFE_SET is **0x26**;
   - 0x31 is not just CARD_BREAK: it is "to another zone with the destruction animation";
   - the generic move is **0x3f / 0x41** (by uid);
   - placing from the hand is **0x4f** (monster) and **0x50** (Spell/Trap; face-up = activate);
   - 0x34 = change position and 0x35 = flip;
   - 0x19/0x1a = attack / direct attack;
   - 0x24/0x25 = gain / lose LP;
   - 0x12/0x13 = chain;
   - 0x48 = create token.
3. **Cards: 3,226 of 3,243 YO2 ids linked (99.5 %)** using a YO2 card table (`research/ia/cards_yo2.csv`, not included)
   and `cards.cdb`. It reuses `tools/build_cards.py`. The network id (1..0xcb7) is the **card_prop index**; the engine
   stores the **internal id** (4007 = Blue-Eyes). A few commands expect the internal id (§7).
4. **Hidden information:** the NPC's hand, deck, extra deck and face-down cards carry a **placeholder card** in the
   client. The real identity is written with 0x40 right before the card becomes visible. Tested: the NPC's draw does
   not leak ids.
5. **NPC phrases:** option `npc_phrases`, which emits 0x60 before a summon, 0x62 when chaining and 0x03 in the NPC's
   End Phase. Tested without errors; **not verified live**.
6. **Most doubtful items**, to verify live (§10):
   - the 0x1a class for battle, card selection and chain;
   - the menu bit for "select card";
   - the YES/NO dialog;
   - 0x1c to close the attack;
   - 0x40 on a field slot.

## 1. Files

| File | What it is |
|---|---|
| `translator.py` | `YO2Duel`: duelcore events → [(id, bytes)]; client messages → response for duelcore. Imports `research/lockstep/proposal.py` (does not copy it) |
| `cardmap.py` | `CardMap`: YO2 id ↔ passcode ↔ internal id, placeholders, coverage (`python cardmap.py`) |

Not included: the test harness (sizes, real dump replay, unit tests, N live duels with a simulated client), the unicorn
emulation of the client's ATK/DEF computation (`FUN_006fae40`) and of command 0x45 (§19), helper scripts to extract a
function from the decompiled code by address and to resolve thunks / read the executable, and the annotated
decompilation of the 107 command handlers and of the DUEL_VIEW dispatcher `FUN_0073dd80`.

## 2. Engine commands (0x6202 type 0, dispatcher `FUN_00709cb0`)

Current command: `DAT_012c78a0` = u16 B (`player<<15 | id`), u16 P0, u16 P1, u16 P2 (`FUN_00709b10` stores them this
way).

In Ghidra's C output:
- `B>>16` / `B._2_2_` = **P0**;
- `P1 & 0xffff` = **P1**;
- `P1>>16` / `P1._2_2_` = **P2**.

`DUEL_VIEW(n, …)` = `FUN_0073dd80` (view table in §3). "uid" = owner + 2·serial.

Packed **source** location (0x3f/0x41): `player | zone<<1 | uid<<6`. Packed **destination** location:
- in 0x3f: `player | zone<<1 | index<<6`;
- in 0x2d/0x48/0x4f: `player | slot<<1 | face_up<<14 | defense<<15`.

Engine field, one 0x14-byte record per slot at `0x12c88b4 + j*0xe0c + r*0x14`:
- +0 word (internal id | owner<<14 | serial<<23);
- +6 defense;
- +8 face-up;
- +0xc value;
- +0x10 flags.

Checked with `FUN_006eaf70`, `FUN_006eb090` and the use of +6/+8 in battle (`FUN_0070a5e0`).

| Id | Handler | Effect / DUEL_VIEW | Parameters | Conf. |
|---|---|---|---|---|
| 0x01 | 7127f0 | DUEL_START | — | C (live) |
| 0x02 | 712d60 | clears the turn markers of the cards | — | P |
| 0x03 | 712e80 | view 0x48 (end of turn: NPC phrase 0x17) + marks graveyard/banished | player | P |
| 0x04 | 712a10 | TURN_CHANGE: other player's turn, turn+1 | player = the one ending the turn | C (live) |
| 0x05 | 712840 | DUEL_END (+ Exodia/FINALCOUNT if the reason is between 3 and 10) | P0 result: 1 P0 wins, 2 P1 wins, 0 draw (`DAT_012cc2e4`); P1/P2 = reason for P0/P1 | P |
| 0x06 / 0x07 / 0x08 | 716100 / 715fa0 / 716090 | views 0x3b (Exodia) / 0x3c (pieces) / 0x3a | player | P |
| 0x09 | 713090 | bit P0 of `DAT_012ca498` = P1≠0 | P0, P1 | C |
| 0x0a / 0x0b | 713000 / 7137e0 | view 3 / 0x3d RUN_EXTRA | P0 | P |
| 0x0c-0x10 | 712c00(0..4) | PHASE_CHANGE: draw, standby, M1, battle, M2 | player | C (live 0-2) / P |
| 0x11 / 0x18 / 0x16 / 0x2b / 0x56 / 0x63 | — | no effect | — | C |
| **0x12** | 713330 | CHAIN_SET (view 0x2f) | P0 = zone, P1 = index in hand, P2 = chain link number | P |
| **0x13** | 7134a0 | CHAIN_RUN (view 0x30) | P0 = zone, P2 = chain link | P |
| 0x14 | 713240 | FIELD_CHANGE (view 7) | P0 = Field Spell id | P |
| 0x15 | 70a490 | BATTLE_INIT | player | P |
| **0x17** | 70aa20 | BATTLE_RUN (view 0xd: damage figures) | P0 = damage to P0, P1 = damage to P1, P2 = flags (low byte P0, high byte P1). **Does not change LP** | P |
| **0x19** | 70a500 | attack (`FUN_0070a5e0` + view 0xc BATTLE_ATTACK) | P0 = attacker slot, P1 = target slot<<8 \| defending player | P |
| **0x1a** | 70a980 | direct attack (target = slot 5) | P0 = attacker slot | P |
| 0x1b | 70ad70 | flip the attacked target and battle rules | P0 slot, P1, P2 | P |
| **0x1c** | 70b170 | end of attack (`cc314 = 0xc`, clears state) | P0 = effect, P1 = phase to jump to (0 = none) | D |
| 0x1d-0x21 | 70b2e0… | battle sub-states (`cc314` = 6/9/0xb…) | — | D |
| 0x22 | 713110 | bit P0 of the player flags = P1≠0 | — | C |
| 0x23 | 7162f0 | player's LP to 0 and reason P0 for the other (view 0x31 + 0xe) | P0 = reason | P |
| **0x24 / 0x25** | 716440(1/0) | gain / lose P0 LP (view 0xf LIFE_DAMAGE + 0x49) | P0 = amount, P1 = type (0 battle, 1 effect, 2 cost), P2 = card (internal id) | P |
| **0x26** | 716a20 | LIFE_SET | P0 = LP | P |
| 0x27 | 716ba0 | LIFE_RESET (view 0x10) | — | P |
| 0x28 / 0x29 | 70e7a0 / 70e920 | graveyard to top + RUN_LIST / **CARD_LOCKON** (target, §13) | 0x29: P0 zone (field = slot), P1 uid, P2 0 | D / C (0x29) |
| 0x2a | 7131d0 | `FUN_00707f20(j, P0, P1, P2)` (card flag) | — | C |
| **0x2c** | 715230 | **NORMAL summon effect** (view 0x34 → `FUN_007a28a0`, summon.txt), §23 | P1 = slot, P2 = type (low byte; 2 → flags \| 0x200008) \| uses the normal summon<<8 | C |
| **0x2d** | 7153f0 | **summon** by uid (view 0x19 + 0x35 RUN_SUMMON) | P0 = packed destination, P1 = uid, P2 = value | C (live) / P (bits 14-15) |
| 0x2e | 7112c0 | reveal card (views 0x43 + 0x21) | P0 zone \| bits, P1 card, P2 uid | D |
| **0x2f** | 711620 | "card X activates" (view 0x43 + 0x21 CARD_HAPPEN) | P0 = **YO2 id** | P |
| 0x30 | 7116c0 | same, highlighting the field cards | P0 = internal id, P1 | P |
| **0x31** | 70ebd0 | **to another zone with destruction** (view 0x1e CARD_BREAK + CARD_MOVE 0x50000). Destination belongs to the owner; deck = top | P0 = zone \| index<<8 (field: zone = slot, index 0), P1 = destination zone (+flags in 0xf00), P2 = uid (for hand/extra/deck) | P |
| 0x32 | 70f650 | same with explosion (view 0x1f) | — | D |
| 0x33 | 710930 | on banishing (view 0x20) | — | D |
| **0x34** | 70d550 | **change position**: ALWAYS toggles ATK/DEF; P1≠0 also toggles the face | P0 = slot, P1 = flip, P2≠0 = flip animation 0x46 | P |
| **0x35** | 70da00 | **flip** without changing position (activate a set card, flip an attacked card). Views 0x47/0x1b/0x43 | P0 = slot, P1 (u16) = 1, P2 flags: bit0 view 0x47, bit1 flag 3, bit2 view 0x43, bit3 do not flip | P |
| 0x36-0x39 | — | add/remove card effects (`FUN_006e2930`) | — | P |
| **0x3a** | 70e300 | sets/adds the slot value +0xc (counter, turns or declared value), no view (§22) | P0 = slot (high byte 0 = set, otherwise = add), value = P1 \| P2<<16 | C (emulated) |
| **0x3b** / **0x3c** | 711b50 / 70e3e0 | **TURN counter** (+1 with view 0x24 = hourglass, §22; earlier misread as "+1 spell counter") / **counter** (view 0x26, §14) | 0x3b: P0 slot, P1 0 = reset to 0 / ≠0 = +1, P2 ≠ 0 = number over the player (D). 0x3c: P0 slot, P1 delta s16, P2 type | C / C |
| **0x3d / 0x3e** | 70cd80 / 70d0f0 | slot → slot with the whole record (view 0x19) / swap two slots (view 0x1a), §14 | P0/P1 = j \| slot<<8; 0x3d: P2 = face | C |
| **0x3f** | 70b6b0 | **move by uid** to hand/deck/graveyard/banished/extra. Checks that the uid is in the source (player, zone); otherwise does nothing | P0 = packed source, P1 = destination (deck: index 0 = top, otherwise bottom), P2 = flags (bits 6-7: face-down when banished) | P |
| 0x40 | 70bdc0 | move by position (not by uid) | — | D |
| **0x41** | 70c660 | **move by uid to a field slot** with face and position | P0 = source, P1 = destination (`j \| slot<<1`), P2 = defense<<8 \| face_up | P |
| **0x42** / 0x43 | 7119f0 / — | **card → card link** (view 0x23 CEquipEffect + entry of type P2, §13) / view 0x22 | 0x42: P0 = source `j \| slot<<8`, P1 = destination `j \| slot<<8`, P2 = type | C (0x42) / D (0x43) |
| 0x44 / **0x45** / 0x47 | — / 714620 / — | view 0x1c / **summon finished**: clears the slot's bit 3 "being summoned" (set by 0x2d, 0x4f, 0x34 and 0x35), records the summon type and counts it, §19 / view 0x27 | 0x45: P0 = slot, P1 = bits (2 special, 4 set, 8 flip), P2 ≠ 0 → flag 0x20 | D / **C** (0x45; bits P) / D |
| **0x46** | 7102b0 | **tribute for a summon** (view 0x2b "生贄召喚開始" + to the owner's pile with CARD_MOVE 0x80000, §15) | P0 = slot, P1 = destination zone (0xe), P2 = uid | C |
| **0x48** | 715b60 | **create token**: new serial (`FUN_006eaf00`) + views 0x1c and 0x35 | P0 = packed destination, P1 = **internal id**, P2 = value | P |
| 0x49 | 714ea0 | MONST_SHUFFLE (view 0x28) | — | P |
| 0x4a / 0x4b / 0x4c | 714970 / 714a00 / 714a90 | tribute marker (view 0x29) / fusion material marker (0x2c) / start of the material (0x2e), §15 | 0x4a: P0 = slot; 0x4b/0x4c: P0 = zone, P1 = material index, P2 = uid | C |
| 0x4d | 714c90 | RUN_FUSION (views 0x41 empty + 0x36), §15 | P1 = uid of the Fusion Monster, P2 = n \| flag<<8 | C |
| **0x4e** | 713b10 | HAND_SHUFFLE with seed `P1<<16\|P0` | — | P |
| **0x4f** | 713ef0 | **hand → monster zone** (views 0x44 and 0x1d CARD_SET if face-down) | P0 (view only), P1 = `slot<<1 \| face<<14 \| def<<15`, P2 = `uid<<1 \| uses the normal summon` | P |
| **0x50** | 714230 | **hand → Spell/Trap zone** (face-down: CARD_SET; face-up: view 0x43 = activate) | P1 = `slot<<1 \| face<<14`, P2 = uid | P |
| 0x51 | 713d00 | HAND_SHOW: shows a card from the hand | P0 = uid | P |
| 0x52 / 0x53 | — | view 0x13 / random-pick animation in the hand | — | D |
| 0x54 | 712460 | shuffle the deck with seed `(P1<<16)\|P0` | — | P |
| 0x55 | 7120e0 | draw P1 (takes index 0) | P0 = 0xffff, P1 = n | C (live) |
| 0x57-0x59 / 0x5b | — | DECK_RESET / DECK_FLIPTOP | — | D |
| **0x5c / 0x5d** | 7135d0 / 713650 | coin (view 0x38) / die (view 0x39), §14 | 0x5c: P0 = j \| zone<<1 \| n<<6 \| called<<14, P2 bits; 0x5d: P0 zone, P1 1..6 | C |
| 0x60 | 714e00 | view 0x40: NPC "summons" phrase | P0 = internal id or `0x8000\|uid` | P |
| 0x62 | 7132c0 | view 0x42: "chains" phrase | — | P |
| 0x66 | 713900 | message dialog (P1 = text id) | — | D |
| 0x68 | 716170 | entry of the engine's chain table | — | D |
| 0x6b | 70b3f0 | end of battle (`cc314 = 0xc`, view 0x4b) | — | D |

## 3. DUEL_VIEW of build 080912 (`FUN_0073dd80`, `DUEL_VIEW_*` and SJIS strings)

| Id | View |
|---|---|
| 1 | START |
| 2 | END |
| 5 | PHASE |
| 6 | TURN |
| 7 | FIELD_CHANGE |
| 8 | RUN_LIST |
| 9 | BGM |
| 0xa | BATTLE_INIT |
| 0xb | BATTLE_SELECT |
| 0xc | BATTLE_ATTACK |
| 0xd | BATTLE_RUN |
| 0xe | LIFE_SET |
| 0xf | LIFE_DAMAGE |
| 0x10 | LIFE_RESET |
| 0x11 | HAND_SHUFFLE |
| 0x12 | HAND_SHOW |
| 0x14 | FIELD_CHANGE |
| 0x15 | DECK_RESET |
| 0x16 | DECK_FLIPTOP |
| 0x17 | GRAVE_TOP |
| 0x18 | CARD_LOCKON |
| 0x19 | CARD_MOVE |
| 0x1a | CARD_SWAP |
| 0x1d | CARD_SET |
| 0x1e | CARD_BREAK |
| 0x1f | EXPLOSION |
| 0x21 | CARD_HAPPEN |
| 0x28 | MONST_SHUFFLE |
| 0x29 | tribute, place material |
| 0x2a / 0x2d | cancel fusion or tribute |
| 0x2b | start tribute |
| 0x2c | fusion material |
| 0x2e | start fusion |
| 0x2f | CHAIN_SET |
| 0x30 | CHAIN_RUN |
| 0x32 | RUN_DIALOG |
| 0x33 | RUN_LIST |
| 0x35 | RUN_SUMMON |
| 0x36 | FUSION |
| 0x37 | DETAIL |
| 0x39 | DICE |
| 0x3b | EXODIA / FINALCOUNT |
| 0x3c | VIJA |
| 0x3d | EXTRA |

## 4. ocgcore → YO2 (what `translator.py` does)

Actual ocgcore ordering (from a duelcore dump):
- **first MOVE, then SUMMONING, SPSUMMONING, SET or CHAINING**: the translator holds back the MOVE to the field until it
  sees the next message;
- **a Flip Summon does NOT bring POS_CHANGE**, only FLIPSUMMONING;
- **activating a set card does bring POS_CHANGE**, and it arrives before CHAINING.

| ocgcore message | Commands to the client | Conf. |
|---|---|---|
| (start) | 0x01 + 0x6202 type 0x40 for every deck and extra slot of both players (serial = position+1; NPC with placeholder) | P |
| NEW_TURN | 0x04 with the player ending the turn (nothing on the first turn; warns if it does not match `first`) | C |
| NEW_PHASE 1/2/4/8/256 | 0x0c/0x0d/0x0e/0x0f/0x10 (16/128 = battle, not repeated; 512 nothing) | P |
| DRAW | type 0x40 with the identity of deck slots 0..n-1 (the real one for the human) + 0x55 (P0 = 0xffff, P1 = n) | C/P |
| MOVE hand → monster + SUMMONING (normal / tribute) | 0x4f face-up (P2 bit0) + 0x2c (slot, 2), §23 (+0x60 before it if it is the NPC and `npc_phrases`) | C/P |
| MOVE → field + SPSUMMONING (and SUMMONING not coming from the hand) | 0x2d (view 0x35 = special + reborn.wav) | C |
| MOVE hand → monster + SET | 0x4f (P2 bit0 = 1) + 0x45 P1 = 4 (§19) | P |
| MOVE hand → Spell/Trap + SET / + CHAINING | 0x50 face-down / face-up | P |
| MOVE → field (other cases: from the graveyard, control…) | 0x41 (or 0x3d field → field, §14) | P |
| MOVE monster → graveyard | 0x31 (destruction) | P |
| MOVE → hand/deck/graveyard/banished/extra | 0x3f (deck: top if sequence = n-1; otherwise bottom) | P |
| MOVE from location 0 (token) | 0x48 (P1 = internal id; uid = next serial: number of cards + 1…) | P/D (serial) |
| MOVE to location 0 (token) | tribute for a summon → 0x46; otherwise → 0x31. The client does NOT put it in the pile (type 10 in card_prop) and deletes its object, §23 | C |
| MOVE with OVERLAY | ignored (no Xyz in YO2) | — |
| POS_CHANGE / FLIPSUMMONING | defense changes → 0x34 (P1 = face changes; P2 = 1 on a flip); face only, or in Spell/Trap → 0x35 (P1 = 1). Before that, real identity if it ends face-up | P |
| CHAINING | [0x62] + identity + (face-down on the field → 0x35; if not in Spell/Trap → 0x2f "activates") + 0x12 (zone, hand index, chain link) | P |
| CHAIN_SOLVING n | 0x13 (zone of link n, n) | P |
| CHAIN_END | — (empties the stack) | — |
| DAMAGE / PAY_LPCOST / RECOVER | 0x25 (type 0 in the damage step, 1 effect) / 0x25 type 2 / 0x24 | P |
| LPUPDATE | 0x26 (see §31) | P |
| ATTACK | 0x19 (target) or 0x1a (direct) | P |
| BATTLE | 0x17 with the REAL damage (the DAMAGE messages that follow in the batch; without a batch, the computed one) and the destroyed flags, §23 | C |
| DAMAGE_STEP_END / ATTACK_DISABLED | 0x1c (P0 = 0, P1 = 0) | D |
| SHUFFLE_DECK | 0x54 (animation) + re-impose the deck with 0x40 | P |
| SHUFFLE_HAND | 0x4e with a seed CHOSEN so that the client RNG (MSVC LCG, Fisher-Yates in `FUN_006fa2d0`) leaves its hand in ocgcore's order (`hand_seed_for`, n ≤ 9); the mirror takes that order. Then 0x40 + refresh 0x1c per index (if no exact seed exists, that fixes the image). A random seed made the image at index i differ from its card (clicking one card played another) | C (code + trace) |
| CONFIRM_CARDS / CONFIRM_DECKTOP | real identity with 0x40 (+0x51 if in the hand) | P |
| TOSS_DICE / TOSS_COIN | 0x5d / 0x5c (§14) | C |
| ADD_COUNTER / REMOVE_COUNTER, SWAP, MOVE field → field | 0x3c / 0x3e / 0x3d (§14) | C / P |
| WIN / `end` | 0x05 (result 1/2/0, reasons 0) + 0x6202 type 0x4d (P0 = 1 win, 2 lose, 3 draw; P1 = `finish_mode`) | P / D (P1) |
| NPC End Phase (`npc_phrases`) | 0x03 | P |
| SUMMONED / SPSUMMONED / FLIPSUMMONED | 0x45 for every card of the preceding SUMMONING/SPSUMMONING/FLIPSUMMONING still in its zone (P1 = 0 / 2 / 8), §19 | C (that it is needed) / P (bits) |
| HINT, CHAINED, CHAIN_SOLVED, FIELD_DISABLED… (EQUIP/BECOME_TARGET: §13; counters, TOSS_COIN, SWAP: §14; CARD_HINT: §22) | nothing (`IGNORED` list; anything else is logged in `unknown`) | — |

## 5. Locations, uids and identities

- (controller, location, sequence) → mirror:
  - MZONE n → slot n;
  - SZONE 0-4 → 5-9;
  - SZONE 5 → 10 (field);
  - hand/graveyard/banished/extra: index = sequence;
  - **deck: YO2 index = n-1-sequence** (in ocgcore the top is the highest sequence).
- Position: face-up = `pos & 5`; defense = `pos & 12`. In the packed form, **bit14 = face-up and bit15 = defense**
  (P: slot +8 / +6).
- Only the mirror knows the uid. That is why the commands use the uid (0x2d/0x3f/0x41/0x4f/0x50): the client looks up
  the card with `FUN_006eb1b0(uid)`. 0x3f/0x41 carry the uid in 8 bits, so **serial ≤ 127** (deck + extra + tokens).
  That is enough.
- **Lazy identity:** the client shuffles with `time(0)` at startup.
  - Start: deck and extra are re-imposed with 0x40.
  - Before drawing: the real card is written into slot 0..n-1.
  - Before a card becomes public: it is written into its current slot.
  - If the client shuffles (0x54/0x4e), the whole deck or hand is re-imposed. For the hand, in addition, the 0x4e seed
    is chosen so that the client already shuffles into ocgcore's order, and every 0x40 goes with its 0x1c (0x40 does
    not change the object's image).
- **0x40 on the field (P):** the default case writes the word into `slot = zone + index` and the owner bit into slot
  `index` (`FUN_00721020`, type 0x40). Therefore **zone 0, index = slot** is sent.
- **Placeholders** (`CardMap.placeholders`): Earthbound Spirit (Normal Monster), Exile of the Wicked (Spell) and Skull
  Dice (Trap).
  - Placeholders are used for: the NPC's deck, hand, extra deck and anything it sets face-down. What it sets in
    Spell/Trap gets the Spell or Trap placeholder according to the real card.
  - Risk: the client's local rules compare specific ids (0x1740, 0x1dd9…), and the placeholders are not among them.

## 6. Player 0 decisions

Always in this order:
1. **0x6611** (field masks per (player, zone) and phase buttons in player 0's tail);
2. **0x6612** (hand);
3. **0x6202 type 0x1a** (class).

Bits: 1 attack, 3 special, 4 activate, 5 summon, 6 flip, 7 set monster, 8 set Spell/Trap, 9 to ATK, 10 to DEF
(`research/lockstep/NOTES.md` §5b, P).

| Selection | What is sent | Client reply → duelcore | Conf. |
|---|---|---|---|
| SELECT_IDLECMD | `summons`→bit 5, `special_summons`→3, `pos_changes`→6 (face-down), 10 (in ATK) or 9 (in DEF), `monster_sets`→7, `spell_sets`→8, `activates`→4. Tail: 3 if `to_bp`, 5 if `to_ep`. Class `kind_main` = 3 | 0x6350 (j, zone, index, cmd) → the option with that bit on that card (cmd −1/0 = the card's first option; zones 0xc-0xf without index). 0x002e 3 → TO_BP, 5 → TO_EP. 0x6351 → re-sent | P (bits, partially live) |
| SELECT_BATTLECMD | `attacks`→bit 1, `chains`→bit 4; tail 4 if `to_m2`, 5 if `to_ep`; class `kind_battle` | 0x6350 → SELECT_BATTLE / SELECT_CHAIN; 0x002e 4 → TO_M2, 5 → TO_EP | P / D (class) |
| SELECT_CHAIN (with options, or forced; empty ones are passed by duelcore unless §30 is enabled) | bit 4 on the chainable cards; class `kind_select` | 0x6350 → index; 0x6351 → do not chain (unless forced) | D |
| SELECT_CARD / SELECT_TRIBUTE | bit `select_bit` on each candidate on the field (both players) or in the own hand. In battle, if all are opponent monsters, `target_bit` (the attack command) | one 0x6350 per card up to `max`; 0x6351 finishes if ≥ `min`, or cancels if `can_cancel` | D |
| SELECT_UNSELECT_CARD | same with `select_cards` | 0x6350 → `index`; 0x6351 → `null` if `can_finish`/`can_cancel` | D |
| SELECT_EFFECTYN / SELECT_YESNO | type 0x60 (list A: size 1) + 0x61 (internal id, class 4 = card name) + **type 0x1b B=0x32 P0=2** = `CUYesNoDialog` (texts: §23, §25) | **0x6423 u32: 5 = YES (button 4000), 7 = NO (0xfa1)** (`FUN_00774e70`) | P |
| SELECT_PLACE / DISFIELD | nothing: automatic reply = first free own zones | — | C (format) |
| SELECT_POSITION | DUEL_VIEW 0x32 type 8 (CUSelectStandDialog) if ATK vs DEF can be chosen; otherwise (or without uid), automatic: face-up ATK > face-up DEF > face-down DEF (§18) | 0x6423 0 = ATK, 1 = DEF | C |
| ANNOUNCE_RACE / ATTRIB / NUMBER / CARD | types 6 / 7 / 0xb and list mode 0x4b (§18) | 0x6423 | C |
| SELECT_OPTION, SORT_*, SELECT_COUNTER | type 4 / list 6+n / type 0xb (§18); with the flag off, bot.ts decides | 0x6423 / 0x6432 | C (window) / P (texts, rules) |
| Opponent's hand; SELECT_SUM, RPS | `needs_auto()` = True → `auto_response()` = `{"cmd":"auto"}` (bot.ts decides) | — | — |

Other client dialogs:
- `DUEL_VIEW 0x32` with P0 = type: 0/1 OK, 2/3 YES-NO, 4 selection, 5 phase, 6 type, 7 attribute, 8 ATK/DEF, 9 coin,
  10 die, 0xb number, 0xc nothing (no-op in 080912) / 0xd discard panel CUDiscardDialog (§20), 0xe ritual, 0xff list B.
  The text comes from list A (value/class pairs: 0 message, 4 card name by internal id, 5-7 type/attribute/race).
- Replies from the list and number dialogs also go out as 0x6423 (`FUN_00772240`… and `FUN_00778ba0` = value). Types
  4, 6, 7, 8 and 0xb are used; formats and replies are read in §18 (**C**).

## 7. CardMap (`cardmap.py`)

- `yo2_to_code(id)`, `code_to_yo2(passcode)` (if not found, tries the cards.cdb alias), `yo2_to_internal(id)`,
  `internal_to_yo2(internal)`, `kind(passcode)` and `coverage()`.
- Source: `research/ia/cards_yo2.csv` (not included in the public tree).
  - Its names match `card_name_e.bin` 100 %.
  - Exact-name matching reuses `build_cards.norm` and `RENAMES` against cards.cdb.
  - "Similar" passcodes from the CSV with confidence ≥ 0.85 are accepted; `card_pass+stats` ones (0.53-0.80) are not.
- Without the CSV it falls back to `card_name_e.bin`: 3,198 linked.
- Coverage with the CSV:

  | Concept | Cards |
  |---|---|
  | ids with a name | 3,243 |
  | linked | **3,226** (3,198 by name + 28 from the CSV) |
  | not linked | 17 (e.g. Swordsman from a Foreign Land, Frog the Jam, Dryad, Ulevo…) |
- Which numbering each command expects:

  | Command | Numbering |
  |---|---|
  | type 0x40 P3, 0x2f P0, decks 0x6601 | **YO2 id** |
  | 0x48 P1, 0x24/0x25 P2, 0x30 P0, dialog list class 4, 0x60 P0 | **internal id** |

## 8. NPC phrases (option `npc_phrases`, off by default)

- 0x60 with `0x8000|uid` before the NPC's normal summon.
- 0x62 on every NPC CHAINING.
- 0x03 in the NPC's End Phase.
- Activating or setting (0x35, 0x50 and 0x4f generate views 0x43/0x44) and damage (LIFE_DAMAGE) already trigger their
  phrases by themselves.
- **Requires** the last byte of AUTODUELSTART 0x43a0 = 1. That turns the duel into a best-of-3 match: P1 of type 0x4d
  (`finish_mode`) will have to match that. **D**.

## 9. Tests (reference run)

1. **Start:** 81 × 0x6202 of 40 B (protocol description: `u32,u32,u32` with the loop collapsed). OK.
2. **Real dump** (a 3-turn duelcore dump, 187 events): simulated client without errors. Alexandrite does not exist in
   YO2 and appears as a placeholder, with a warning.
3. **Unit tests:**
   - YES/NO (`0x60`, `0x61 (4007,4)`, `0x1b 0x32`; 0x6423 5 → yes);
   - the NPC does not leak ids when drawing;
   - token 0x48 (P0 = 0xc005, P1 internal, uid 83);
   - phase buttons (tail 3 and 5), 0x6351 re-sends and 0x002e 3 → TO_BP.
4. **Live** (duelcore + node; test decks with cards that do not exist in YO2 replaced by Mystical Elf):
   - **30/30 duels without differences**: 1,245 board comparisons and 0 RETRY;
   - commands exercised: 0x04, 0x05, 0x0c-0x10, 0x12, 0x13, 0x17, 0x19, 0x1a, 0x1c, 0x24, 0x25, 0x2d, 0x31, 0x34,
     0x35, 0x3f, 0x4f, 0x50, 0x55, types 0x1a, 0x40 and 0x4d;
   - with `npc_phrases`, also 0x03, 0x60 and 0x62;
   - decision modes seen: idle, battle, chain, card, tribute, automatic placement and position.
   - Limitation: the simulated client applies **our reading** of the engine. The test confirms that the translator is
     consistent with that reading and with ocgcore, **not** that the executable behaves the same way.

## 10. Second adversarial pass (handler of every used command re-read)

| Claim | Check | Conf. |
|---|---|---|
| P0 = `B>>16`, P1 = `P1&0xffff`, P2 = `P1>>16` in the C output | `FUN_00709b10` writes 4 consecutive u16 at 0x12c78a8; `FUN_0070a290` copies them to 0x12c78a0 | C |
| 0x2d: bit14 = face-up, bit15 = defense | `FUN_006f07e0(j, slot, CONCAT11(bit15, bit14))` → `FUN_006eaf70` +6 = bit15, +8 = bit14. In battle, "+8 == 0 and +6 ≠ 0" = face-down DEF. Live, 0/0 came out face-down | P+ |
| 0x3f validates the source and does nothing if it does not match | `if ((uVar7&0xff) != P0&1 ...) DAT_012c80ac = 0` | C |
| 0x3f to deck: index 0 = top | `FUN_006f07e0` zone 0xd: `param_3 == 0` → `FUN_006ec600` (inserts at 0); otherwise `ec6a0` (appends) | C |
| 0x41: P2 = def<<8 \| face | `FUN_006f07e0(j, slot, P2)` → monster: `param_3>>8` = +6, `&0xff` = +8 | C |
| 0x31: the destination belongs to the card's owner | `uVar11 = ((word>>14) ^ …) & 1` | P |
| 0x34 always toggles the position | state 2: `+6 = (+6 == 0)` unconditionally | C |
| 0x35 toggles the face, not the position | state 2: `+8 = (+8 == 0)` unless bit 0x80000 | C |
| 0x4f: uid in P2>>1 and hand index looked up by uid | `FUN_006eb7c0(j, P2>>1)` | C |
| 0x50: face = bit14 of P1 | `uVar9 = (P1>>14)&1` → `FUN_006eb090(…, uVar9)` | C |
| 0x48: P1 is the internal id | `ca4c8[uid] = P1` (internal) and `ca4ca[uid] = FUN_0073bbe0(P1)` (internal → YO2) | C |
| 0x24/0x25: P0 amount, P1 type, P2 card | `FUN_00716440`: `uVar7 = ±P0`, `uVar6 = P1`, `uVar5 = P2` | C |
| 0x26 = LIFE_SET | view 0xe + `LP = P0` | C |
| 0x05: result 1 = P0 wins | `0x23`: `cc2e4 = (loser == local) + 1`; at the end of the duel, `cc2e4 == 2` → opponent's reason | P |
| 0x6423 5/7 = YES/NO | `FUN_00774e70`: button 4000 → 5, 0xfa1 → 7; `CUYesNoDialog` is created with (4000, 0xfa1) | P |
| type 0x1b B=0x32 P0=2 opens YES/NO with the list A text | executor: B=0x32 and P0 ∉ {0xfe, 0xff} → `DUEL_VIEW(0x32, P0, P1, P2)`; `FUN_00744670(P0≠0xfe/0xff)` reads list A; `switch(P0) case 2` → `FUN_00773f20` | P |
| network id = card_prop index | `FUN_0073bc50(i) = prop[i*8] & 0x3fff`; type 0x40 applies it to P3 | C |

**Pending live verification (by risk):**
1. 0x1a class in battle, chain and card selection, and which bit lets the client click an opponent card or a pile;
2. that the YES/NO dialog appears and answers 0x6423;
3. that 0x1c does not break the next attack;
4. 0x40 on the field (zone 0, index = slot) when flipping NPC cards;
5. serial of the 1st token;
6. P1 of type 0x4d;
7. that 0x31 for a monster destroyed in battle does not clash with what 0x17 already does (the client's battle engine
   also marks cards).
8. If something fails, set `kind_*`/`select_bit` in `cfg` without touching code.

## 11. Use from the server (sketch)

```python
from translator import YO2Duel            # sys.path += research/traductor
duel = YO2Duel(decks, first=st["first"], log=log, cfg={"npc_phrases": False})
send_all(duel.start())                    # after 0x61fe
for ev in dc.wait(id):                    # duelcore
    send_all(duel.feed(ev))
if duel.needs_auto():
    dc_send(duel.auto_response())         # {"cmd":"respond",...} or {"cmd":"auto"}  (+ "id")
# in the 5760 handler:
cmd, back = duel.on_client(msg_id, data)  # 0x6350 / 0x6351 / 0x002e / 0x6423
send_all(back)
if cmd: dc_send({**cmd, "id": id})
```

- `decks` = the passcodes of the human's deck (from their `.ydc`, via `CardMap.yo2_to_code`) and of the NPC.
- Their size must match what the client believes it has: 0x6601 and 0x6005.
- duelcore must be given `shuffle: True`. The order imposed on the client is irrelevant thanks to lazy identity.
- The actual integration lives in `ocg_duel.py` (`OcgSession`, `PvpSession`).

## 12. Chain prompt priority ("chain?")

### 12.1 What the official client/server did (client + data)

| Fact | Evidence | Conf. |
|---|---|---|
| The chain prompt is DUEL_VIEW 0x32 type 3 with P2 = priority, and the SERVER sends it (0x1b B=0x32). In the online scene the client never opens a type 3 on its own: the only call to `FUN_0071f540` (type 3) is at `0x72a07f` | search for CALL rel32 to 0x71f540 and its thunk 0x4087a1 in the whole `.text` | C |
| The client decides only from P2: P2 < 3 with the right mouse button held (`[DAT_012711ac+0x148]+8`) → NO; P2 < 2, without the `+0xc` key and option ≠ Manual → NO; otherwise it shows the dialog | `FUN_0073dd80` case 0x32/3 (and its copy at 0x740000) | C |
| "Automatic cancel" (0) and "semi-automatic cancel" (1) behave **identically** in 080912: both cancel P2 < 2. The help texts (yo2text 6534 "all messages" / 6535 "low priority") do not match the code | there are only 3 reads of `+0x437`: case 0x32/3 (×2) and the debug menu "DialogRes: AutoCancel / SemiAutoCancel / Manual <Ctrl F3>" (0x790000) | C |
| The option is NOT sent to the server (it is stored in the local config `DAT_012714c4+0xf4`; there is no send) → the server did not know each player's option: it sent the same P2 to everyone | writes and reads of `+0x437` and `+0xf4` | C |
| Scale: 0-1 = low (auto-cancelled except in Manual), 2 = normal (only skipped with the right button), 3 = always | conditions above | C (client) / D (which values the server used) |
| **There is no per-card priority table in the client.** `data/bin#`: card_prop/card_genre already reviewed; `d_magic_proto.inc` = only `DEF_MAGIC_FUNC(id)` (2689 lines, list of spell functions); `s_cardnum.inc` = sizes. No other per-card file | listing of the client's `data/bin#` | C |
| Command 0x66 (P1 = text, P2 = card): in the online scene (`FUN_00709cb0` → `0x713900`) it only shows text P1 (type 0). In scenes 0xc9/0xca/0xd2 of `FUN_007917b0` (another dispatcher, `FUN_00722690` → `0x72a010`; probably tutorial or practice) P1 = 169..203 (the event: "Your opponent Summoned…", "…declared the battle"…) + "\n" + dlg 204 "There is no card that can be activated during this time", with **type 3 and P2 = 3** | disassembly of 0x713900 and 0x72a010 (the "FUN_00407b44" in the C output is the thunk of 0x72a010) | C (code) / D (which scene) |
| Official prompt texts: dlg 7 (English "Chain another card?", Japanese 「…の効果が発動されました。チェーンしますか？」; the Spanish client text reads "The effect of "%s" is activated. Resolve it as part of a Chain?") and dlg 8 ("%s is activated…"). dlg 269 "activate the card's effect?" is generic | dlgtext s/e/j | C |
| List A format for 7/8: `[(internal id, 4), (7 or 8, 0)]`. Class 4 copies the name (FUN_0073bc80) into a buffer and sets "pending text"; class 0 calls `FUN_007437d0(out, value = dlg template, buffer)` and substitutes `%s`. With the flag at 0, the 3rd argument is the value of the NEXT pair: that is why `[(269, 0)]` alone read stack garbage and crashed (access violation) | disassembly of 0x744670 (jump table 0x74495c) | P (not tested live) |

Conclusion: which P2 each prompt had was decided by the official server and leaves no trace in the client (D). The
rules below are ours, behind flags. Recorded official duel logs later gave the official values (§28).

### 12.2 What ocgcore provides

`SELECT_CHAIN` carries `spe_count`, `hint_timing` (timings caused by the chooser) and `hint_timing_other` (caused by
the opponent), plus code/loc/description per option. In 244 optional windows from 119 recorded traces, `spe_count` > 0
appears for:
- effects with an event (Mirror Force on attack, Solemn Judgment on a flip…);
- any window with an open chain, including the player's OWN chain;
- free cards whose script `SetHintTiming` matches the timing.

This is what EDOPro uses for its "auto-chain" (P: the ocgcore source is not at hand; deduced from traces). The Lua
scripts (`CardScripts`) give, per effect, `SetCode` (EVENT_FREE_CHAIN = free) and `SetHintTiming(own, opponent)`.

### 12.3 Implemented (`translator.py`)

- `chain_priority`. Only applies with `chain_dialog = 3`; when set, it overrides `chain_importance_auto`. Current
  default: `"oficial"` (§28). Other values:
  - `"evento"` (rule of ours): P2 = 2 if one of the following holds; otherwise P2 = 1 (free activation).
    - **(a)** There is an open chain and the last link belongs to the OPPONENT. If the last link is own → 1.
    - **(b)** Some option is an effect with an event: all its chainable effects have `SetCode` ≠ EVENT_FREE_CHAIN. If
      the option carries `description` = aux.Stringid, only that effect is checked.
    - **(c)** `hint_timing_other` has TIMING_ATTACK (the opponent has just declared an attack) and a free option has
      TIMING_ATTACK in the "opponent" part of its `SetHintTiming`.
  - `"spe"`: P2 = 2 if `spe_count` > 0 and the last link is not own; otherwise 1. It asks more often: Mystical Space
    Typhoon on every opponent summon and phase, because of `TIMINGS_CHECK_MONSTER_E`.
- `chain_link_text` (default on): with an open chain, list A `[(internal id of the last link's card, 4), (7 if it is a
  monster or 8 if Spell/Trap, 0)]`. Without a chain, the usual text. Choosing 7 or 8 by card type is an inference (P):
  the effect of a face-up Continuous Trap would also use 8.
- Script reader `script_info(code)`, cached in the module; alternate artwork uses the cards.cdb alias.
  `self.chain_codes` = passcode of each link (class value `()` for hot reload).

Client side: with "Activation message" set to automatic or semi-automatic (identical), only P2 ≥ 2 is shown; "Manual"
shows everything.

### 12.4 Example cards (scripts + real traces; columns "evento" / "spe" = P2)

| Card | Script | Timing | evento | spe |
|---|---|---|---|---|
| Aqua Chorus | free, no HintTiming | draw, phases, opponent summon | 1 | 1 |
| Aqua Chorus | | opponent attack | 1 | 2 (ocgcore flags it) |
| Aqua Chorus | | opponent activation (Dark Snake Syndrome) | 2 | 2 |
| Mystical Space Typhoon | free, Hint(0, STANDBY\|MAIN_END\|TIMINGS_CHECK_MONSTER_E) | draw, summon, set | 1 | 1; 2 if the opponent summons |
| Mystical Space Typhoon | | opponent's standby, end of main or End Phase (8 of 52 windows) | 1 | 2 |
| Mystical Space Typhoon | | chaining to your own Spell (9 of 52) | 1 | 1 |
| Call of the Haunted | free, Hint(0, STANDBY\|MAIN_END\|…_E) | draw / opponent's standby and main | 1 | 1 / 2 |
| Necro Gardna | QUICK_O free, Hint(0, TIMING_ATTACK) | no attack | 1 | 1 |
| Necro Gardna | | opponent attack | 2 (c) | 2 |
| Raigeki Break | free, Hint(ATTACK, …_E\|ATTACK) | draw, phases | 1 | 1 (2 in the opponent's End Phase) |
| Mirror Force, Magic Cylinder | EVENT_ATTACK_ANNOUNCE | attack | 2 (b) | 2 |
| Torrential Tribute | EVENT_SUMMON/FLIP/SPSUMMON_SUCCESS | summon | 2 (b) | 2 |
| Solemn Judgment | summon event | Flip Summon | 2 (b) | 2 |
| Magic Jammer | EVENT_CHAINING | opponent's Spell / yours | 2 / 1 | 2 / 1 |
| Ring of Destruction, Book of Moon | free, Hint(…_E) | opponent summon | 1 | 2 |
| Scapegoat, Shrink, Ceasefire, Gravity Bind, Birthright | free | draw, phases | 1 | 1 (except their HintTiming) |

In the traces, out of 244 optional windows, "evento" leaves 11 with P2 = 2 and "spe" 34. The earlier rule
(`chain_importance_auto`: P2 = 2 if the last thing was done by the opponent) asked in every window following an
opponent summon, attack or activation.

### 12.5 Open questions

- Which text the official server used when there is no link (attack, summon): 269, or event 169-203 + 269? Answered
  by the official logs: event + 269 (§28).
- The key `[DAT_012711ac+0x148]+0xc` (prevents auto-cancel): unidentified.
- (c) only looks at TIMING_ATTACK: free cards that respond to a summon (Ring of Destruction, Book of Moon) stay low
  under "evento". Adding TIMING_SUMMON to (c) would raise them, but Mystical Space Typhoon would then prompt on every
  summon, since it shares the same HintTiming.

## 13. Card-target link / equip

Question: how the original showed **which monster is affected** by a card that stays on the field pointing at it
(equips, Spellbinding Circle, Call of the Haunted…).

### 13.1 What the original client did

- **There is no persistent indicator.** No line, no arrow, no "equipped" mark, nothing on hover. Checked:
  - the engine functions that read links (`FUN_006e4ca0`, `6e4a30`, `6e4e00`, `6e4ea0`, `6e4f60`, `6e4410`) only have
    engine callers (0x6e–0x72), none in the UI (0x73+);
  - the UI (0x73–0x7f) does not read the effect list (`DAT_012cb8c8…`), except the engine handlers that copy effects
    when moving;
  - there are no link graphics (only `duel_arrow_01_c01.png` = window, and the shop and tournament arrows). **C** (with
    the caveat that "does not exist" is deduced from not finding it).
- **It is visible at two moments:**
  1. **On creation:** the `CEquipEffect` animation (RTTI `.?AVCEquipEffect@@`, vtable 0xbed30c, constructor
     `FUN_007aa0a0`, step `FUN_007aa3c0`). It receives the source card's image (`DAT_012ca4ca[uid]`) and two packed
     locations. The image flies from the source card to the monster: the counter increases by 8 per frame up to 0xc0
     (24 frames) and then `0x1000000a` plays. **C**
  2. **While it lasts:** in the ATK/DEF the client computes itself (`FUN_006fae40`). Type 10/11 links add the bonus of
     each known Equip Card (Kunai with Chain +500, Axe of Despair +1000…), and type 6-9 links apply effects depending
     on the source card. **C**
- **Target on activation:** engine 0x29 → `DUEL_VIEW 0x18` CARD_LOCKON. It puts the card in animation state 1
  (`FUN_00742e10`: +0x18 = 1, +0x1c = 0x100), the same one that blinks in HAND_SHOW. **C** (code), not tested live.

### 13.2 Format

**Effect list per slot.** 10-byte entries at `DAT_012cb8c8`:

| Offset | Field |
|---|---|
| +0 | id |
| +2 | type (low nibble) |
| +4 | value |
| +6 | turn stamp |
| +8 | next |

- With **type 6..11** the entry is a **link**: id = `player | slot<<8` of the **source** card. **C** Confirmed by:
  - `FUN_006e2870(j, r)`: deletes all links with id = (j, r); called by `FUN_006f0600`, which removes a card from the
    field;
  - `FUN_006e4290`: relocates ids when the source card changes slot (0x41, 0x3d…);
  - `FUN_006e41d0`: converts the link into a card id (`FUN_006ec290`).
- Types used by the client engine:
  - 6: zone lock, Seismic Shockwave;
  - 7: Judgment of the Desert;
  - 8: battle, Asura Priest;
  - 10-11: equip (`FUN_006e4a30` counts those ≥ 10). **C**
- Neither 9 nor 10/11 are created by the executable: they come from the server. **C** (search for `FUN_006e2930` with
  a constant type).
- The client engine checks Spellbinding Circle (internal id 0x1103) with `FUN_006e4700(j, r, 0x1103)`, i.e. "the
  monster has a type 6-11 link whose source is 0x1103". Therefore **the official server attached a link to the
  targeted monster**. With which type is unknown: **D**.

**Engine 0x42 = `FUN_007119f0`**. **C**
- P0 = source `j | slot<<8`.
- P1 = destination `j | slot<<8`.
- P2 = type.
- The player in B is not used.
- Step 0: `DUEL_VIEW 0x23(P0, P1 (bit 0 and slot only), uid from the source's word)`. If the source belongs to the
  client, `FUN_00721f10(0x27, 1)` (an empty function, §15).
- When the view ends: `FUN_006e2930(destination, type = P2, id = P0, value 0)` + `FUN_006e4050` + view 0x25.
- Requires the source card to already be in its slot (reads its word for the uid).
- P2 being a parameter suggests the official server used 0x42 for all links, not only equips (P).

**Engine 0x37 = `FUN_0070e0b0`**, to delete a link. **C**
- P0 = destination slot `| type<<8`.
- P1 = source id.
- P2 = 0: with type > 5 it calls `FUN_006e2ba0`, which deletes the 1st link entry with that id.
- A non-zero high byte of P2 would delete by exact type.

**Engine 0x36**, the same link without animation. **C**
- P0 = slot `| type<<8`.
- P1 = source id.
- P2 = 0.

**Engine 0x29 = `FUN_0070e920`**. **C**
- P0 = zone: on the field, slot 0..10; 0xb = hand, with the index derived from the uid.
- P1 = uid.
- P2 = 0. With P2 > 5 it records the target in a queue of 128 (`DAT_012c8618`), use D.
- With zones 0xe/0xf it first **moves the card to the top of its pile** (`FUN_006f7c10`/`7d20`) and starts view 0x17.
  That would desynchronise the mirror, so these zones are not used.

**Automatic cleanup.** When the source card leaves the field the client deletes its links (`FUN_006f0600` →
`FUN_006e2870`). When the monster leaves, its list goes with it. 0x37 only needs to be sent when re-equipping to
another monster or when cancelling a target while both cards are still on the field. **C**

### 13.3 Implemented (`translator.py`; not tested live)

- **`card_link`** (default on):
  - `EQUIP` → 0x42 with P2 = `link_kind_equip` (10). If the card was already equipped to another monster still on the
    field, 0x37 first: ocgcore does not send UNEQUIP when re-equipping, and the 122 recorded traces contain no UNEQUIP.
  - `UNEQUIP` → 0x37.
  - `CARD_TARGET` → 0x42 with P2 = `link_kind_target` (9, **D**). With `link_target_anim=False`, a silent 0x36.
  - `CANCEL_TARGET` → 0x37.
  - The mirror keeps `links` = ((source uid, destination uid, type), …) and forgets links whose cards are no longer on
    the field.
  - With the flag off, the four messages produce no output, and UNEQUIP gives the same warning as before.
- **`target_lockon`** (default on): `BECOME_TARGET` → 0x29 for each target on the field or in the hand.
- **ATK/DEF:** `FUN_006fae40` resets the accumulators at the `EFFECT_SET_STATS` entry, but entries **after** it keep
  adding. Therefore:
  - when attaching a link to a monster that already had its stats pair, `stat_sent[uid] = LINK_STALE`, and the next
    `sync_stats` sends 0x37 + a new pair;
  - with `card_link` on, a monster with a link gets the pair **even if** its ATK is the printed one. Otherwise the
    client would add its own equip bonus (which may not match ocgcore).
  - `stat_sync` and `query` are required, as now.
- **Tests:** flag off = nothing; 0x42 / 0x37 / 0x36 / 0x29 with exact parameters; re-equip; pair repeated after the
  link; source off the field. With both flags on, 4 live test duels pass. With the flags at their previous defaults,
  the 0x6202 count is identical to before.

### 13.4 Open questions

- The **type** of non-equip links (Spellbinding Circle, Call of the Haunted, Demotivation) and whether the official
  server used the 0x42 animation or just 0x36: **D**. To be tested live with 9 and animation. If the flying Circle
  image looks odd, `link_target_anim=False`.
- Equip 10 vs 11: the ATK computation and the equip counters treat them the same. No difference has been found (P for
  10).
- With 0x42, which of the two `CEquipEffect` locations is the animation start: P (the image is the source's).
- Whether the detail panel (`CCardDetailViewDlg`) showed anything about the equip: nothing found (it does not call the
  link functions). **P**

## 14. Engine: the missing commands

Engine commands used went from 24 to 29 of 107 with this section. Verified with the real trace of a Breaker the
Magical Warrior activation, the 30-duel run and the PvP fake client run (`fake_client.py pvp`: 43/43).

### 14.1 Map: ocgcore message → client command

| ocgcore message | Before | Client command (handler) | What it does | Conf. | Status |
|---|---|---|---|---|---|
| ADD_COUNTER / REMOVE_COUNTER | nothing | **0x3c** (`FUN_0070e3e0`) | P0 slot, P1 delta s16, P2 type. View 0x26 (+/− animation on the card, `FUN_00742e70`) and `FUN_0075b5c0` always draws them (sprite 14 of `card.txt` = `magic_Counter.png`) | C | **done**, `counters` (on) |
| TOSS_COIN | nothing | **0x5c** (`FUN_007135d0` → view 0x38, `CCoin` from `coin_new.txt`, ctor `FUN_007ab150`) | P0 = j \| zone<<1 \| n<<6 \| called<<14, P2 = bits | C (format) / D (face) | **done**, `toss_anim` (on) |
| TOSS_DICE | 0x5d on slot 0 | **0x5d** (`FUN_00713650` → view 0x39 RUN_DICE, `CDice` ctor `FUN_007aa630`) | P0 = zone, P1 = 1..6 (tables of step `FUN_007aa990` indexed 1..6) | C | zone = card of the chain link (`toss_anim`) |
| SWAP (Creature Swap) | **nothing → desync** | **0x3e** (`FUN_0070d0f0`) | swaps the 0x14-byte records of two OCCUPIED slots (view 0x1a CARD_SWAP); P0/P1 = j \| slot<<8 | C | **done**, `swap_cmd` (on) |
| MOVE field → field (change of control, zone move) | 0x41 by uid | **0x3d** (`FUN_0070cd80`) | copies the whole record (type-3 effects, counters, links via `FUN_006e4290`); if the player changes it sets +0x10 \|= 0x400; P2 → +8 (face); view 0x19 CARD_MOVE. If ocgcore also changes the position, 0x34 afterwards | C (handler) / P (that the official server used it) | **done**, `field_move_3d` (on; False = 0x41) |
| ATK/DEF from effects | — | 0x36/0x37 + `EFFECT_SET_STATS` (already present) | — | C | unchanged |
| Fusion (materials + summon) | 0x3f/0x31 + 0x2d | 0x4b + 0x4c per material, 0x4d before the 0x2d (§15) | — | C (format) / P-D (order) | **done**, `fusion_anim` (§15) |
| Tribute | 0x31 | 0x46 (+ optional 0x4a) (§15) | — | C / P (recognition) | **done**, `tribute_anim` (on) |
| LP change | 0x24/0x25 (animation) + 0x26 | — | already the official way | P | unchanged |
| CONFIRM_CARDS (hand) / deck | 0x40 (+0x51 hand) | 0x51 HAND_SHOW. For the deck there is no "show": 0x5b DECK_FLIPTOP redraws both decks; 0x2e is "activate from graveyard/banished" | — | P / D | unchanged |
| SHUFFLE_DECK / SHUFFLE_HAND | 0x54 / 0x4e | — | already the official way | P | unchanged |
| Phases | 0x0c-0x10 | End Phase has no command | — | C | unchanged |
| Timer | — | NOT an engine command: network message **0x6461** DUEL_TIMEUP_WARNING (`research/lockstep/NOTES.md`; `research/lockstep/proposal.py: timeup_warning`) | must be sent by `ocg_duel`/`run.py` with their own clock | P | **done**, `turn_timer` (§15) |
| Mandatory with several options | masks + prompt | the client has no "mandatory": the server decided (no trace) | — | D | unchanged (§12) |

### 14.2 Counters (details)

- Value: **slot +0xc** (a single number per slot) for types 0..14; types ≥ 15 are type-3 effect entries with id =
  `table 0xbeb748[type]` (valid on any card). Read with `FUN_006ebc00(j, slot, type)`: type 0 only if
  `FUN_0071c210(card)` ≠ 0 (and 0x3c caps it at that maximum: Breaker = 1); type 1 if `FUN_0071c420` (a list that
  changes with the scene); type 10 only cards 0x1e95/0x1e97; 2..14 only if the card is the one in the table. **C**
- Table type → card (0xbeb748) + ocgcore counter (script AddCounter): 0 Spell (0x1) · 2 Clock Tower Prison (0x1b) ·
  3 Vennominaga (0x11) · 4 Crystal Tree (0x6) · 5 Spell Chronicle (0x25) · 6 Six Samurai United / Bushido (0x3) ·
  7 D - Formation (0x1c) · 8 Realm of Light (0x5) · 16 Magic Reflector (0x2a) · 17 Alien Grey / A (0x100e) · 18 Ice
  Master (0x1015) · 19 Venom Swamp (0x1009) · 20 Cloudian Fog (0x1019). `COUNTER_MAP` in translator.py; bits
  0x1000/0x2000 are stripped. A counter without a type logs a warning and is not sent. **C** (table) / **P** (matching
  by ocgcore number)
- When the card leaves the slot, the client clears +0xc and the type-3 entries (`FUN_006e3e70`); ocgcore does not send
  REMOVE_COUNTER in that case. **P**
- `FUN_0075a030` does NOT draw counters: it draws the declared type/attribute (DNA Surgery, The Regulation of Tribe,
  Elemental Absorber…) or the declared card (Prohibition, Cloak and Dagger), which also live in +0xc. **C**
- **Breaker (real trace):** with 0x3c the client shows the counter and its own computation gives 1900
  (`FUN_006fae40` case 0x1617: 300 × type-0 counters). The server log showed the 0x36 pair with 1900/1000 sent after
  CHAIN_RUN and 0x37 removing it on activation: the 1900 was visible for ~2 s while the player decided. If the number
  still does not show live, look at `FUN_00759430` (interpolates +0x4c/+0x50), not at the detail panel (it shows the
  printed value). **P**

### 14.3 Coin and die

- 0x5c: the ctor stores +0x14 = number of coins (loop of step `FUN_007ab4f0`), +0x18 = result bits, +0x1c = "called".
  Frame 6 of `duel_ef_coin.png` (eye) if the bit is 1, frame 13 (Millennium Puzzle) if 0; if result == called, the
  sparkle appears (sprites 3/4). Sent: bit = ocgcore result (True = heads) and `coin_call` = 1. Which face the
  official server called "heads" and what was called: **D**.
- Start position (coin and die): the zone of the resolving chain link (or the last open one); without a chain, slot 0
  of the player (**D**; the animation ends in the centre of the screen).

### 14.4 Flags

`counters`, `swap_cmd`, `field_move_3d`, `toss_anim`, `coin_call` (all in `DEFAULT_CFG`, on). Turning them off
restores the previous behaviour. Not tested live yet: first Breaker (counter + 1900) and a coin (Time Wizard /
Graceful Dice for the die).

## 15. Fusion, tribute, TIME bar and loose messages

Verification: replaying REAL traces with `replay_trace()` (cuts batches at each select and drops the NPC's selects,
like `ocg_duel.pump()`); the 30-duel run passes; `fake_client.py pvp` 43/43 (one run gave 41/43 because of the two
ROOM_REOPEN, a timing issue; repeated, 43/43). Engine commands used: 29 → 30 of 107 (0x46). The 61 non-PvP recorded
traces were replayed with and without the flags: the simulated client reports no error in any of them.

### 15.1 What the client expects (SJIS debug strings next to each view)

| Command | View | String | What it does | Conf. |
|---|---|---|---|---|
| 0x4a `FUN_00714970` | 0x29 → `FUN_007efd20` | 生贄モンスターセット | Creates the **tribute marker** on (player, slot P0), in mode 0. Moves nothing | C |
| 0x46 `FUN_007102b0` | 0x2b → `FUN_007eff20` | 生贄召喚開始 | If there is a marker, **starts** it (vtable+0x14); otherwise creates it already running (mode 1). Then moves the card from slot P0 to pile P1 of its **owner**, with CARD_MOVE \| 0x80000 (to the graveyard it goes at the end; to the deck, on top). P2 = uid | C |
| 0x4b `FUN_00714a00` | 0x2c → `FUN_007f0130` | 融合召喚モンスターセット | Stores the card word in table `DAT_012ccb34[P1]` (the same one used by 0x6610 lists). Creates the **material marker** where the card is: field or hand (no marker in piles ≥ 0xc) | C |
| 0x4c `FUN_00714a90` | 0x2e → `FUN_007f0330` | 融合召喚開始_DoMaterial | Same as 0x4b, but the view **only starts an existing marker**: without a preceding 0x4b nothing is shown. With P0 = 0xe, it first moves the card to the top of the graveyard (`FUN_006f7c10` + view 0x17). With P0 = 0x10 (the material already went to 0xe/0xf), it sets bits 16/17 of its word and ends without a view | C |
| 0x4d `FUN_00714c90` | 0x41 (empty in 080912) + 0x36 → `FUN_0079d320` | DUEL_VIEW_RUN_FUSION | P1 = uid of the Fusion Monster, P2 = n \| flag<<8. With flag 0 it attaches to the **uid** n type-3 effects with id 0x12e5, one per material, with its uid as value (`FUN_006e2a80`, by uid, wherever it is). 0x12e5 falls into a `break` in `FUN_006fae40`, so it does not affect ATK. Starts the fusion animation with the uid's card (`DAT_012ca4ca[uid*5]`). **Moves nothing**: the monster comes out with 0x2d, as always | C |
| views 0x2a/0x2d | `FUN_007f0370` | 融合＆生贄召喚キャンセル | Delete all markers. No engine command starts them with a constant (probably UI-driven) | C / D |

Other facts (**C**):
- `FUN_00721f10`, called by 0x46 and 0x4d for the local player, is an empty `return`: there are no statistics.
- Dispatchers `FUN_00722690` (scenes 0xc9/0xca/0xd2) and `FUN_0072ca70` (scenes 0xd5/0xd6) are copies for other
  scenes. The online duel uses `FUN_00709cb0` (from `FUN_0070a290`).
- The client's data contains no official replays or scripts that show the real order.

### 15.2 Tribute (`tribute_anim`, on)

- ocgcore does not state the reason for a MOVE. Recognition (**P**: the rule is ours, but it does not change the board):
  - HINT type 3 **500** (HINTMSG_RELEASE) arms it.
  - A MOVE from the monster zone to the graveyard counts as a tribute if, **in the same batch**, one of these follows
    (skipping MOVE, HINT and messages such as MISSED_EFFECT):
    - the MOVE hand → monster zone;
    - SUMMONING/SET;
    - the HINT type 3 "choose zone" whose value is the code of a card in the hand. It comes before SELECT_PLACE, so it
      is in the batch even if the select cuts it.
  - CHAINING, CHAIN_*, phase change, attack, damage or draw stop the search: an effect **cost** still uses 0x31.
  - Disarmed by: CHAINING, chain, phase, turn, SUMMONING, SPSUMMONING and SET.
- `0x46 (controlling player, slot, 0xe, uid)` is emitted instead of `0x31`. The destination's owner is set by the
  client: a card taken with Change of Heart goes back to its owner's graveyard. An Equip Card that falls afterwards
  still uses 0x3f.
- `tribute_mark` (off): puts a 0x4a before each 0x46. The client accepts both forms (0x2b checks whether a marker
  exists); which one the official server used: **D**.
- Real traces checked: 10/10 tribute summons recognised (NPC tributes for Blue-Eyes; NPC and player tributes with
  MISSED_EFFECT in between, where 2 costs with HINT 500 still use 0x31; an older core with SELECT_UNSELECT_CARD).

### 15.3 Fusion (`fusion_anim`, on by default; `YO2_FUSION_ANIM=0` disables it)

- HINT type 3 **511** (HINTMSG_FMATERIAL) arms it.
- Every MOVE of a monster from field, hand, graveyard or deck to graveyard, banished or deck emits `0x4b (j, zone, i,
  uid)` + `0x4c (j, zone, i, uid)`, after its identity 0x40 and **before** the move:
  - zone = the slot if on the field, 0xb hand, 0xe graveyard, 0xd deck;
  - i = 0, 1…
- When the MOVE extra → field with SPSUMMONING arrives, after its 0x40 come:
  - a list **0x6610 mode 0xfe** with the n materials (YO2 id, serial, owner);
  - `0x4d (j, 0, uid, n)`;
  - the usual 0x2d.
- Why the list: `CFusionEffectTask` (`FUN_007b4690`, created by view 0x36) copies `n` ids `& 0x3fff` from `0x12be194`,
  which is the 0x6610 item table **only with mode 0xfe** (`FUN_006929b0`). Without it the animation shows stale ids or
  zeros. Mode 0xfe opens no window. **C**. That the id is the YO2 one (as in the other lists) and not the internal one:
  **P**.
- Why this order:
  - 0x4c only starts markers placed by 0x4b (C).
  - 0x2c/0x2e compute the position from the card's CURRENT location, so they must come before the move (C).
  - 0x4d reads the table filled by 0x4b/0x4c (C).
  - It is not verified that 0x4d goes before the 0x2d (and not after), nor that materials go one after another rather
    than placing all markers first: **P/D**.
- Field materials still go out with 0x31 (destruction animation) after their marker. If it looks wrong live, try 0x3f.
- Real trace (NPC Polymerization, 70781052 + 79109599 → 11901678): `0x4b,0x4c,0x31,0x4b,0x4c,0x31 … 0x40, 0x6610
  (0xfe, 2 ids), 0x4d (n = 2), 0x2d`, and the simulated client reports no errors.
- The live duelcore test duels contain no fusions: the test decks have no extra deck.

### 15.4 TIME bar: 0x6461 (`turn_timer`)

- **Format** (`FUN_00692bf0`, **C**): u8 side (0/1; 3 = global clock for rock-paper-scissors and deck change), s32
  seconds, u8 paused, s32 sec2.
  - Writes at `ND+0x5c0+side*0x94` (or at `+0x534` with side 3). `ND+0x544` becomes the active clock.
  - The client counts down by itself, with `time()` (`0x692d50`), and never goes below 0.
- **Drawing** (**C**): drawn by `FUN_00799d80` with a `CYo2ProgressBar` whose maximum is **fixed at 0xb4 = 180 s**.
  `0x692cc0(j)` returns:
  - the active clock, if j is the active side;
  - the stored value, frozen, if j is the turn player;
  - −1 otherwise.
- It is only visible if the room's time option (`DAT_012bb6b0+0xc2`) is enabled or the room is of type 8. In a
  local/CPU duel (`DAT_012a9084` = 0/1) it is never shown (**C**).
- When it reaches 0 the client **sends nothing** for the turn bar; in the deck change it closes the window. Neither the
  turn change nor 0x6611/0x6612 touch the clock: the server decides everything (**C/P**).
- **0x6400 does not exist** in the client's table. The 0x64xx it receives are 0x6461 and 0x649b DUELSVRINFOMSG (**C**).
- sec2 (at most 0x708 = 1800 s) feeds the `CJankenTimer`s. It looks like a total clock; −1 is sent (**P**).
- Implemented (rule of ours, visual only: nobody loses on time). With `turn_timer = N` (default 180; `YO2_TURN_TIMER=0`
  disables it):
  - on the opponent's NEW_TURN: `0x6461 (1, N, paused 1, −1)`;
  - on the own NEW_TURN the clock resets to N, and each `_request` (permission 0x1a) is preceded by `0x6461 (0,
    remaining, 0, −1)`;
  - in chain windows during the opponent's turn: N.
- Which values and at which moments the official server sent them: **D**.
- The lobby must enable the room's time option (+0xc2); without it the bar does not appear even if sent.
- **Real time limit in PvP** (180 s per turn, a separate allowance to answer during the opponent's turn, loss when it
  runs out) is enforced by `ocg_duel.PvpSession`, not by the translator (flag `turn_limit`, `YO2_LIMITE_TIEMPO`). In PvP
  with the limit, the views use `turn_timer = 0` and the session sends the 0x6461 messages; against an NPC and in PvP
  without the limit, everything above applies.

### 15.5 0x6105 and 0x6610

- **0x6105 COMPULSIONJYANKEN** (`FUN_00692290`, **C/P**):
  - Does `+0x676 |= 4 | (x&3)<<3`.
  - The rock-paper-scissors scene (≈`0x7c8c70`) sees bit 2, clears it, **imposes the hand** `(x>>3)&3` and continues
    the animation **without sending 0x6101**.
  - The scene has a `CJankenTimer` ("じゃんけんの制限時間") that runs on the global clock (side 3 of 0x6461); at 0 it
    does nothing by itself.
  - It lets the server pick the hand when time runs out. **Not needed** as long as the server does not time-limit
    rock-paper-scissors: currently the player always chooses.
- **0x6610 SELDUELLIST_DATA** (`FUN_006929b0`, **C**):
  - Despite the name, it is the **card list** the translator already uses. Header: u8, u32 mode, u32, u32 n, u32. Each
    item is 4 × u16 forming a card word.
  - With mode 0xfe it goes to `0x12be180`, read only by `0x7a6440` and `0x7b4690` (the RUN_FUSION object), and only the
    card ids. With any other mode it goes to the `CWindowDeckList` window.
  - It is not a list of duels to spectate (corrects `research/lockstep/NOTES.md`).
  - `0x7b4690` is `CFusionEffectTask` (RUN_FUSION): it reads the ids of the n materials from that table, so the official
    server must have sent a 0x6610 mode 0xfe before 0x4d (**C**). `fusion_anim` does this (§15.3). The other reader,
    `0x7a6440`, has not been examined.

### 15.6 Revealing cards (CONFIRM_CARDS / CONFIRM_DECKTOP)

Unchanged.
- The hand already uses 0x51 HAND_SHOW (view 0x12). If the card belongs to the opponent, it also opens its
  `CCardDetailViewDlg`.
- For the deck there is no "show the top card" command:
  - the "top face-up" state lives in a per-card flag (`FUN_00707b00(j, 0xd, 0)`), read by view 0x15 DECK_RESET when
    redrawing the deck;
  - view 0x16 DECK_FLIPTOP only plays a sound + `FUN_0076d240`.
- 0x52 starts view 0x13, which does not exist in `FUN_0073dd80`. It sets player flags (`FUN_00707e90(j, 1, P0)`) or an
  effect 0x1db9.
- There is no official sequence that uses it (**D**): left alone.

### 15.7 Flags (in `DEFAULT_CFG`)

| Flag | Default | Conf. |
|---|---|---|
| `tribute_anim` | True | C |
| `tribute_mark` | False | D |
| `fusion_anim` | on (`YO2_FUSION_ANIM`) | P/D |
| `turn_timer` | 180 (`YO2_TURN_TIMER`; 0 disables) | D |

- Turning them off restores the previous behaviour.
- Live checks: (1) an own Tribute Summon and an NPC one: the marker should show and the card go to the graveyard
  without an explosion; (2) Polymerization with `fusion_anim`.

## 16. Client hang at the start of the opponent's turn (diagnosis; not a protocol issue)

Symptom: in PvP, one client machine (without the network hook) became "Not responding" five times in a row, always
**at the START OF THE OPPONENT'S TURN**; its own turns played fine. One of the hangs happened right after a chain
prompt, but others happened without one, so the chain was a coincidence (C, logs).

Findings:
- **The initial deal was not broken.** The small ids were that player's real deck (from its own 0x6601). Nothing was
  missing at hand index 1: identity is lazy (`_m_DRAW` only sends 0x40 if the card imposed in `start()` differs from
  the one ocgcore draws). C (code + log).
- **What reached the hanging client was structurally IDENTICAL** (same order of types and B; only ids and indices
  differ) to what the same machine had received in earlier PvP duels that lasted 8-11 turns. The hand-order fix
  (§4, SHUFFLE_HAND) emits nothing on this path. C (diff of both sequences).
- The P2P tunnel stayed alive during the hang and the server logged no errors. C.
- Hypotheses considered (D, never confirmed): the opponent's avatar had changed between sessions (it travels in 0x600c,
  block B, `pvp_match.preparation`), and `FUN_0073dd80` case 5/6 → `FUN_007d4720` performs the field turn change
  (`FUN_007d3e20`/`FUN_007eb480`) with the opponent's part; candidates for a closer look would be the opponent's
  bust/cut-in at the start of their turn (`FUN_0079a370`, `FUN_009e6e30`, `FUN_00a58cc0`).
- **Resolution:** after rebooting the affected machine, a full PvP duel with the same avatar and the same deck played
  without any hang. The hangs were local to that machine (probably graphics driver/DXVK or memory after many hours),
  not protocol. If it recurs: reboot the client machine before investigating; the avatar hypothesis remains
  unconfirmed.

## 17. Field Spell background and RETRY cap

### 17.1 Field Spell → engine 0x14 (`field_change`, on by default; `YO2_FIELD_CHANGE=0` disables it)

- **What the client does (C):** `FUN_00713240` (command 0x14) stores P0 in `DAT_012ca4c0` and starts DUEL_VIEW 7
  FIELD_CHANGE; the view passes that value through `FUN_00704100`, which converts the card's **internal id** (0x10f0..
  = Mountain, Yami…) into the background/BGM number; when the view ends it calls `FUN_006e4050`. It is the **only**
  writer of `DAT_012ca4c0`: without 0x14 the board stays at "field 0" (observed with Rainbow Ruins).
- **What the translator sends:** after each message (with no held-back MOVE) and before each request, `_field_check()`
  looks at SZONE 5 of both players in the mirror. If the face-up Field Spell changes: `engine(0x14, owner, internal
  id)`; if none is left: `engine(0x14, 0, 0)`. The client keeps ONE: the last one placed is shown; if it leaves and the
  other player's remains (MR5 has one field per player), the other one.
- **Doubtful:** when the official server sent it (when it became face-up, as now, or when the chain link resolved)
  and whether `FUN_006e4050` also adds the field bonus to the ATK/DEF already imposed by `sync_stats` (it could show
  twice). P/D. See §19.3 for the interaction with the stats pair.
- **Live checks:** with the flag on, against an NPC, activate an own Field Spell (or wait for the NPC's): (1) do the
  board background and music change? (2) is the ATK/DEF of affected monsters correct (bonus not added twice)? (3) when
  the Field Spell is destroyed, does the normal background return? If (2) shows double, the bonus must be removed in
  `sync_stats`.

### 17.2 RETRY cap (`retry_auto` = 2, on)

- Previously a duelcore RETRY (illegal response) was only logged. If the response had been generated by the server
  itself (automatic SELECT_PLACE/POSITION, `auto_response`), the same one was resent forever and the duel thread kept
  spinning.
- Now (`_on_select` + `auto_response`): an automatic server response that already failed once is not repeated (bot.ts
  decides with `{"cmd": "auto"}`); for a player decision, on the 1st RETRY the player is asked again and from the 2nd
  **consecutive** RETRY bot.ts decides (log line "RETRY n.º 2 … decide bot.ts"). Re-asking the SAME decision
  (`_resend`: menu closed with −1, list closed…) does not count as a RETRY. `retry_auto = 0` = previous behaviour.
- It is server robustness (the client sees no different message), hence on by default.
- If something "does not respond" in a duel, search the log for `RETRY n.º`.

## 18. The player decides through the client's windows (option, position, announce, sort, counters)

Previously all these decisions were taken by bot.ts (`needs_auto`) and the player saw nothing.

### 18.1 Which window the client has (RE: DUEL_VIEW case 0x32 + disassembly)

All are opened with 0x6202 type 0x1b B=0x32 (P0 = type, P2 = parameter) and answer with **0x6423 u32**
(`FUN_0071f8c0` → `FUN_00791670` → `FUN_00692190`). The header text comes from list A (0x60/0x61); **class 2** (dlg
text) is used, never class 0 alone (§12.1).

| Type | Class (creator) | P2 | 0x6423 reply | Conf. |
|---|---|---|---|---|
| 4 | `CUSelectDialog` (`FUN_00774ee0`) | — (items go in 0x6610: one item = dlg id of the option) | **ORIGINAL index** of the item: `[0x38b+k] = i` (0x776100). Only items with **bit 15** (0x6610 flags bit 1) = 1 are shown: `[0x392]` is the mask of VISIBLE items, not of disabled ones (corrects earlier menu notes). Text of each option = dlg[id], and only for the **first 4** (`FUN_0073dbf0(…, 4)` at 0x73f243) | C |
| 6 | `CUSelectTypeDialog` (`FUN_0077a320` / ctor `0077a400`) | mask: bit i = race i (**1..20**; 21, Divine-Beast, is not offered) | race i (0x77acb9). Always chooses 1 (`+0x3b0 = 1`) | C |
| 7 | `CUSelectAttributeDialog` (`FUN_0077ad20` / `0077ae10`) | count (16 bits) \| mask<<16 (bits **1..6**) | OR of `1<<i` (0x77b40f); OK requires exactly "count" | C |
| 8 | `CUSelectStandDialog` (`FUN_00776510`, init `FUN_00776cf0`) | uid (image `ca4ca[uid]`) \| (face-up DEF)<<16 | **0 = card in face-up ATK; 1 = card in DEF**, face-up if P2>>16 ≠ 0 and face-down otherwise (word `0x20 \| face<<14 \| i<<15`, 0x776ea9-0x776ec2; reply `FUN_00776a30`) | C |
| 0xb | `CUSelectNumDialog` (`FUN_00778160` / `00778280`) | dlg of the LABEL (format with %d, `FUN_00743ae0`) \| min<<16 (4 bits) \| max<<24 | the number (`FUN_00778ba0`, between min and max; starts at min) | C |
| list 0x4b | `CWindowDeckList` mode 0x4b: the client fills in ALL cards itself (`FUN_007689d0`; 0x4c-0x4e = filters, D) | — (0x1b B=0x33 P1 = 0x4b; no 0x6610) | on click: **0x6423 internal id** and 0x6432 i 1; on close, 0x6423 1 (`FUN_0076a3f0`). Cannot be cancelled | C |
| list 6+n | the usual one ("choose exactly n") | — | 0x6432 i, order of each mark (on unmark it renumbers and sends a 0x6432 for each) + 0x6423 n | C |

Races and attributes: the name of race i is dlg 500+i (`FUN_0073be50`) and that of attribute i is dlg 530+i
(`FUN_0073bec0`): 1 Dragon … 20 Reptile; 1 LIGHT, 2 DARK, 3 WATER, 4 FIRE, 5 EARTH, 6 WIND. Hence `YO2_RACE` /
`YO2_ATTR` (→ ocgcore bits). **C**.

Useful dlg texts (meaning): 326 "Select the card's position.", 241/242 type/attribute, 259 "Which effect to
activate?", 260 "Select the card type.", 280 "Declare the monster's Level", 301 "Decide how many Life Points you will
pay as a cost.", 594 "Select amount"; labels 283 "%d", 304 "Level %d", 302 "%d000LP", 303 "%d00LP", 318 "%d
counters.".

**The official option texts exist:** dlg 406-480 are, one by one, the selectable effects of specific cards of the
period (416-418 Kunai with Chain; 421/449/458/459 Fuh-Rin-Ka-Zan; 422/423 Don Zaloog; 438/439 "Top / bottom of the
Deck"; 440-442 "Monster / Spell / Trap Cards"; 448/449 Legacy of Yata-Garasu; 462-464 Excelion…). The official server
sent those ids as items. The mapping ocgcore code → dlg (`OPTION_DLG`, 34 cards) is ours, by text (cards.cdb vs
`dlg_text_e`): **P**.

### 18.2 Implemented (`translator.py`)

| ocgcore decision | What is sent | Reply → ocgcore | Flag (default) | Conf. |
|---|---|---|---|---|
| SELECT_POSITION with FU_ATK and some DEF (no FD_ATK) | [0x40 of the card if it is in own deck/extra] + A [(326, 2)] + type 8, P2 = uid of a card with that code (hand > graveyard > banished > extra > deck; own cards first) \| (FU_DEF offered)<<16 | 0 → FU_ATK; 1 → FU_DEF (or FD_DEF if FU_DEF was not offered) | `position_dialog` (True) | C (window) / P (which uid provides the image: cosmetic only) |
| ANNOUNCE_RACE (count 1) | A [(241, 2)] + type 6, P2 = available YO2 races | `races: [bit]` | `announce_dialogs` (True) | C |
| ANNOUNCE_ATTRIB | A [(242, 2)] + type 7, P2 = count \| mask<<16 | `attributes: [bits]` | same | C |
| ANNOUNCE_NUMBER | A [(text, 2)] + type 0xb, P2 = label \| min<<16 \| max<<24. Multiples of 1000 → "%d000LP" (1..8 = 1000..8000) and text 301; of 100 → "%d00LP"; 1..12 with 5 or more values → "Level %d" and 280; otherwise "%d" and 594 | `value` = index of the number in `options`; a number that is not an option (gap) → reopened | same | C (window) / P (label) |
| ANNOUNCE_CARD | 0x1b B=0x33 (0, 0x4b) | internal → YO2 id → passcode; if it does not satisfy the `opcodes` (`brain.declarable` with cards.cdb) the list is reopened; otherwise `card` | same | C |
| SELECT_OPTION (2-4 options with text) | 0x6610 (mode 0) with one item per option (dlg id, serial 0, flags 2) + A [(259 or 260, 2)] + type 4 | `index` = 0x6423 | `option_dialog` (on) | C (window) / P (texts) |
| SELECT_OPTION 1213/1214 (Magic Drain, Call of the Earthbound) | CUYesNoDialog (type 2) with dlg 11 (if Magic Drain is in the chain) or 269 | 1 → index of 1213; 0 → that of 1214 | same | P |
| SELECT_OPTION with 1 option | nothing: index 0 | — | always | C (no decision) |
| SORT_CARD / SORT_CHAIN (2..32 cards) | 0x6610 mode 6+n with the cards (code given by ocgcore) + 0x1b B=0x33 | `order[i]` = rank at which card i was marked (0 = first = top) | `sort_list` (on) | C (list) / P (that ocgcore reads `order` this way, as EDOPro) |
| SELECT_COUNTER | card by card: if the remainder is forced it is set automatically; otherwise A [(card, 4)] + type 0xb with "%d counters." and min/max | `counters` | `counter_dialog` (on) | P (rule of ours; the official server may have asked "from which card?" first, dlg 344) |

Details:
- Right click (0x6351) with one of these windows open: ignored (the window stays open). A 0x6423 with an impossible
  value (e.g. 0 if the client closes by itself because of `DAT_012a90a4`) → bot.ts decides (`{"cmd": "auto"}`):
  nothing is invented.
- `retry_auto` = 2 (§17.2) unchanged: an ocgcore RETRY reopens the same window; on the 2nd consecutive one bot.ts
  decides.
- `option_texts` (cfg, dict description → dlg) adds or corrects option texts without touching `OPTION_DLG`.
- The duel-end handler `h_6423` only keeps the 0x6423 if a "Do you surrender?" dialog is open; the rest reaches the
  translator.

### 18.3 What remains with bot.ts and why

- **SELECT_OPTION without text** (Light of Judgment, Rain Storm, Yubel, Mausoleum of the Emperor, Judgment of the
  Pharaoh, Reign-Beaux, Pyro Clock of Destiny, Mysterious Guard, Yu-Jo Friendship…) or with more than 4 options: the
  window only shows dlg texts and there is none for those effects. If one is found, it is added to `OPTION_DLG` (or
  `option_texts`).
- **ANNOUNCE_RACE with count > 1** (no YO2 card asks for it) and races that do not exist in YO2 (Psychic, Wyrm…): the
  window picks 1 and only races 1..20.
- **ANNOUNCE_NUMBER** whose minimum does not fit in 4 bits or maximum in 8 (e.g. 20000-30000).
- **SELECT_POSITION** with FD_ATK, or with FU_ATK + FU_DEF + FD_DEF at once (the client offers 2: FD_DEF is lost), or
  of a token (no uid yet).
- **SELECT_SUM, RPS, opponent's hand**: out of scope.
- With the flags off (`option_dialog`, `sort_list`, `counter_dialog`): previous behaviour.

### 18.4 Tests

- Unit tests: the 7 families with their sequence ocgcore → commands → simulated 0x6423 → response (and flag off =
  previous behaviour).
- The 30-duel run passes (3 SELECT_POSITION go through the new window: the simulated client answers 0x6423 reading its
  P2).
- `fake_client.py pvp`: 43/43; `fake_client.py npc`: 5/5.

### 18.5 Live checks

1. **Position:** Monster Reborn (or any Special Summon that allows a choice) on a monster in the graveyard → window
   with two cards (ATK upright, DEF sideways). Choose DEF: the monster comes out in defense. Check that the image is
   the monster's.
2. **Announce:** Mind Crush or Prohibition → list with ALL cards; clicking one applies it. DNA Surgery or Rivalry of
   Warlords → type window; DNA Transplant → attribute. Wall of Revealing Light → number "1000LP…8000LP"; Reasoning →
   "Level n"; Card Trooper → 1..3.
3. `option_dialog`: Kunai with Chain, Legacy of Yata-Garasu, Fuh-Rin-Ka-Zan or Mooyan Curry → window with the official
   texts. Do the options appear (not an empty window)? Is the chosen one applied?
4. `sort_list`: Fruits of Kozaky's Studies or Big Eye → sorting list; the first marked card must end up on TOP of the
   deck (drawn next turn).
5. `counter_dialog`: remove counters spread over two cards → one number per card.
6. If something hangs: search the log for "[traductor]" and the last line with 0x6423.

## 19. ATK/DEF of equips, field and effects not shown: missing 0x45 "summon finished"

Symptom: Armaill (700/1300) + Malevolent Nuzzler. The server sent both views `0x42` (type-10 link; the equip animation
played) and then the `0x36` EFFECT_SET_STATS pair 1400 / 1300. The client kept showing **700**. The same had happened
with every ATK/DEF change: the 0x36 entered the client's effect table, but the painter did not use it.

### 19.1 Cause (C)

- `FUN_006fae40` (the ATK/DEF computation used by the painter) starts from the printed ATK/DEF. Before looking at the
  slot's effect list it does:
  `if (slot +8 face-down || flags(+0x10) & 8) return;`. With **bit 3** set it returns the printed value: it ignores
  equips (0x42), the EFFECT_SET_STATS pair (0x36), the field and the card's own effects. `FUN_006ebe10` ("effect
  active?") also returns 0 with that bit.
- Bit 3 is set by `FUN_00707f20(j, slot, 3, 1)`:
  - **0x2d** (summon), always;
  - **0x4f** if it ends FACE-UP (P1 bit 14; not via P2 bit0: emulated in §23);
  - **0x34** if P1 and P2 ≠ 0 (flip with animation = Flip Summon);
  - **0x35** if P2 bit1.
- Only **0x45** clears it (`FUN_00714620` → `FUN_00707f20(j, slot, 3, 0)`). 0x02 only clears the high half of the
  flags. The translator never sent 0x45: every summoned monster stayed "being summoned" forever.
- A live memory dump showed slot flags = `0x00200408` (bit 3 set) with the 0x36 entry already in the list.
- Unicorn emulation of `FUN_006fae10` on the client executable, Armaill j1 m0 + Nuzzler j1 Spell/Trap 0:

  | Flags | Nothing | Type-10 link | Link + pair 1400/1300 | Pair only |
  |---|---|---|---|---|
  | `0x00200408` (bit 3) | 700 | 700 | 700 | 700 |
  | `0x00200400` | 700 | **1400** (the client adds Nuzzler's +700 by itself: `FUN_006fae40` case 10/11, internal id 0x10e7) | 1400 | 1400 |

  After executing 0x45 (`FUN_00714620`) on the slot with bit 3: flags `0x00210400` (P1 = 0), `0x00220400` (P1 = 2),
  `0x00240440` (P1 = 4), `0x00214400` (P1 = 8), `0x00210420` (P2 = 1). ATK goes from 700 to 1400 in every case.
- Since the engine queue is filled only by the server (`research/lockstep/NOTES.md` §0.2), **the official server must
  have sent 0x45 after every summon**. Otherwise no monster would ever have shown an equip or a field bonus. **C**
  (code + emulation).

### 19.2 What the official server did and how it looks (C/P)

- **Equip:** it sent the 0x42 link (§13: CEquipEffect animation + type 10/11 entry). The bonus is computed **by the
  client itself**: `FUN_006fae40` hard-codes the bonus of each Equip Card, e.g. Nuzzler +700 and Axe +1000. Same for
  the field: the card in slot 10 is checked in `FUN_00701620`, ordered by time stamp against the list. **C**
- **Figure on the field** (`FUN_00759430`, every frame; **C** by code):
  - the number moves from the shown value toward the new one (7/8 of the difference per frame: it "counts" up or
    down);
  - while it moves, a rising sprite (10/11 of `DAT_012a682c`) or falling sprite (12/13) blinks beside it;
  - it is drawn with another font if the value differs from the printed one: set 2 if higher, 3 if lower, 1 if equal.
    Exact colours: **P** (not observed).
  - The detail panel still shows the printed value.
- **0x45 = "summon finished"** (P0 = slot, P1 = bits, P2). Besides clearing bit 3:
  - P1 bit1 → flag `0x20000`; bit2 → `0x40040`; bit3 → byte +0x11 `|= 0x40`; none and face-up → `0x10000`;
  - bit0 → word `|= 0x8000` if `FUN_0071b470(card)` (fusion/ritual/"special summon only": "properly summoned");
  - bit4 → counted by the opponent; P2 ≠ 0 → flag `0x20`;
  - adds the turn's summon (`DAT_012c88a0`), calls `FUN_006e4050`, opens view 0x25 and, for the local player,
    `FUN_00721f10` (empty function, §15).
  - Read by local rules (`0x20`/`0x40` on banish, `FUN_00710930`), with no visible effect with an authoritative server.
  - **Which bit is which summon type: P**. Derived from the other handlers: 0x4f only sets bit 3 together with "uses
    the normal summon" → bit2 = set; 0x34 sets it on a flip → bit3 = flip.

### 19.2b What ocgcore provides

- ocgcore does not send MSG_UPDATE_DATA / MSG_UPDATE_CARD: in YGOPro the server builds them by querying after each
  message. None appear in 171 recorded traces.
- duelcore provides `query()` with QUERY_CODE | POSITION | ATTACK | DEFENSE | LEVEL: the **current** ATK/DEF, without
  BASE_*. The printed value comes from cards.cdb (`cm.stats`).
- The value changes when an equip resolves (EQUIP), when a Field or Continuous Spell becomes face-up or leaves, when
  counters change and when an effect ends (phase resets). No message says so.
- Therefore the translator queries:
  - at every decision (`sync_board`, before 0x6611/0x6612/0x1a);
  - when each chain closes and before each attack (`_live_stats`);
  - at phase/turn changes and at the end of batches that do not end in a decision of that view (§32);
  - and sends the new pair if it changed.

  That was already correct (sent to both views in PvP with `mirror_state`). What was missing was the client actually
  using it: bit 3.

### 19.3 Implemented (`translator.py`, flag `summon_done`, on; `YO2_SUMMON_DONE=0` disables it)

| ocgcore | To the client |
|---|---|
| SUMMONING / SPSUMMONING / FLIPSUMMONING (card already in its mirror zone) | `(uid, P1)` is recorded in `self.summoning` |
| SUMMONED / SPSUMMONED / FLIPSUMMONED | `0x45 (player, slot, P1 = 0 / 2 / 8, 0)` for each recorded card of that type still in a monster zone |
| MOVE hand → monster + SET (0x4f with P2 bit0) | `0x45 P1 = 4` right after. ocgcore does not send "set"; without this, flipping it in battle would still show no effects |
| Negated summon (Solemn Judgment) | nothing: no *SUMMONED arrives and `_remove` drops it from the list |

- Tokens: SPSUMMONING + SPSUMMONED → 0x45 P1 = 2 (0x48 does not set bit 3; it only marks "special" and counts it).
- Not used: bit0 (it would require knowing whether ocgcore considered it "properly summoned"), bit4 and P2 (Tribute
  Summon?). **D**.
- PvP: both views receive the mirrored *SUMMONED; each sends its 0x45 with its own player (`fake_client pvp`: 16 × 0x2d
  and 16 × 0x45).
- `stat_sync` and `card_link` are unchanged. With bit 3 cleared, the EFFECT_SET_STATS pair (newer than the link, which
  is entered when its animation ends: the 0x42 waits for view 0x23) overrides the bonus computed by the client. If
  ocgcore reports the printed value and there is no link, the client is left alone, as with the official server.
  - Risk **P**: a card whose effect the client computes differently from ocgcore while ocgcore reports the printed
    value (e.g. an effect negated without the client knowing). If it appears, the fix is to send the pair whenever the
    client adds something.
- Field Spell with `field_change` (§17.1): the bonus added by the client goes before or after the pair depending on the
  time stamp (`local_c0` vs entry +6). If after, the figure already matches ocgcore until the next `sync_stats`, which
  sends a new pair. Not doubled in tests, but not observed live: **P**.

### 19.4 Tests

- The 30-duel run passes, plus 6 new duels with equips/fields.
  - The simulated client tracks bit 3 "being summoned" and the pair values per slot (`shown_stats`). It fails if a
    monster is still being summoned when the turn changes.
  - The live test uses `d.query` like `ocg_duel` and compares, at every decision, the ATK/DEF the client would paint
    with ocgcore's.
  - The 6 "equips/field" duels (spells → Nuzzler, Axe, Black Pendant, Legendary Sword, Horn, Yami, Sogen, Mountain,
    Wasteland, Umi, Book of Taiyou, Book of Moon) checked 152 decisions with ATK/DEF different from the printed one,
    all equal.
  - With `YO2_SUMMON_DONE=0` those duels fail: "client (1300, 2000), ocgcore (2300, 2000)" and "still being summoned".
- Unit tests: normal, set, flip, special ×2 (one by the opponent), negated, mirrored PvP and flag off. Checks the equip
  (Armaill + Nuzzler → 1400/1300 painted) and the field (Mystical Elf + Yami → 1000/2200).
- `fake_client.py npc`: 5/5. `fake_client.py pvp`: 36/36.

### 19.5 Hot reload

The translator can be reloaded into a running server without restarting it: only the translator module (and its
`proposal.py`/`cardmap.py` copies) is replaced; the live CardMap, duelcore, sessions, queues and running duels are kept.
Before switching, the reload checks that the modules compile, that the new translator has every name of the old one,
that the live CardMap provides everything the new translator needs, that a new `YO2Duel` has every attribute of an old
one and that a smoke duel produces the expected commands; it rolls back if any global name changed. Only duels started
afterwards use the new translator (a duel in progress finishes with the old one). The current mechanism is
`yo2srv/hot.py` (see §32).

### 19.6 Live checks

1. New duel after the reload; summon Armaill (or anything) and equip Nuzzler. Expected:
   - the equip animation;
   - the figure rising from 700 to 1400, with the rising arrow while it counts;
   - 1400 in another colour.
2. Field Spell (Yami / Sogen…) with an affected monster: the figure changes. Is the bonus applied only once?
3. Monster set and then flipped (by attack or Book of Taiyou) with an equip: the bonus shows.
4. If something looks wrong: `OCG.TR.DEFAULT_CFG['summon_done'] = False` from the server console (new duels only).

## 20. Discard for hand size: CUDiscardDialog panel (DUEL_VIEW 0x32 type 0xd)

Symptom: after TO_EP with 7 cards, ocgcore sends HINT 3 501 "Choose a card to discard" + SELECT_CARD min = max = 1 on
the hand. The translator treated it as any SELECT_CARD: masks on the hand and 0x1a, with no text or prompt. The
player clicked a card and it was discarded. Rules correct, presentation not.

### 20.1 What the original client did (RE)

| What | Evidence | Conf. |
|---|---|---|
| Type **0xc**: does nothing in 080912 (jumps to the end of the switch, like types > 0xe) | jump table `0x741ccc`: [0xc] = `0x740320` = exit | C |
| Type **0xd** = `CUDiscardDialog`: creator `FUN_00778dd0`, ctor `FUN_00778ee0`, vtable `0xbec570`. Not created if a modal window exists (`CWindowTaskModel` in `DAT_012a9070+0x108`) | case 0xd at `0x73fe9b`; SJIS strings "CUDiscardDialog 手札を捨ててください" (`0xccc6e0`) and "… 生成失敗！？" | C |
| It is a **NON-modal panel** of 314 px (`FUN_00779520`: `0x13a`×`0x10`, opens up to 112 px high with an animation) with the list A text and, below it, **one small card** (`data/mini_list/card.txt`) per card still to be discarded. **No buttons**: it does not send 0x6423 (`+0x3f5` is never set; the button methods in its vtable are empty) | `FUN_00779660` (`+0x14`, every frame) draws `FUN_00722670()` cards; vtable read from the executable | C |
| The count is the u16 **`DAT_012cc3aa`** (`FUN_00722670`). At 0, `FUN_00779660` returns 0 and the task ends: **the panel closes by itself** | `FUN_00779660`; task runner `FUN_0060e8f0` (state 2: `+0x14` = 0 → end) | C / P (task end) |
| The only writer of `DAT_012cc3aa` is **0x6202 type 0x1e** (`u16 DAT_012cc3a8[P0] = P1`, `0x7217af`-`0x7217c2`) with **P0 = 1** (the fusion view `FUN_0072af90` sets it to 0). The tutorial version of DUEL_VIEW (`FUN_0074bfa0`, modes 0xd3-0xd6) sets it right before creating the panel | references to `0x12cc3aa` in the executable | C |
| No client code opens view 0xd on its own (internal calls to `FUN_0073dd80(0x32, …)` are types 0-4): **the server opened it** | search in the decompiled code | C |
| Text: **dlg 235** "Discard from your @2hand@0." (j: 「@2手札@0を捨ててください。」 = the text of the panel's debug string). The other discard texts (11, 20, 40, 41, 238, 277, 347 "Choose the card(s) in your hand to discard") belong to card effects | `dlg_text_*` | P |
| Cards are chosen as in any hand SELECT_CARD: 0x6612 masks + 0x1a; direct click (bit 11) sends 0x6350 (0, 0xb, i, 11). The panel does not block the queue (`FUN_00743130(0x32)` only checks `+0x108`) | `FUN_007390a0`, `FUN_0071f8a0` | P |
| That the official server used this panel precisely for the **End Phase hand limit** | panel with "how many left" + generic text; the client has no other discard window; no official trace | P |

Earlier menu notes said "0xd `CUDiscardDialog` → 0x6423 (D)": in fact it **answers nothing**.

### 20.2 Implemented (`translator.py`, flag `hand_limit_dialog`, on; `YO2_HAND_LIMIT_DIALOG=0` disables it)

The hand-limit discard is recognised (`_hand_limit`) if ALL of the following hold:
- ocgcore's last phase is the End Phase (0x200; `ocg_phase`, because PHASE_MAP does not include it) and it is player
  0's turn;
- there is no open chain and it cannot be cancelled;
- the SELECT_CARD is over the WHOLE hand (all candidates in the own hand) with min = max = hand − 6;
- the HINT 3 immediately before it in the same batch (`_last_hint3`) is 501.

Otherwise (Graceful Charity, costs, End Phase effects inside a chain…), everything as before.

| Moment | To the client |
|---|---|
| Hand-limit SELECT_CARD | list A `[(235, 2)]` + **0x6202 type 0x1e (P0 = 1, P1 = N)** + **0x1b B=0x32 P0=0xd** + 0x1e again + 0x6611/0x6612 with bit 11 on each hand card + 0x1a. The count goes BEFORE the view: the panel reads it in its first frame and would close at 0 |
| Each 0x6350 (0, 0xb, i, 11) that is not the last | 0x1e (1, cards remaining) + masks without the already chosen ones |
| The last one | response to ocgcore `indicies` (in click order) and **0x1e (1, 0)**: the panel closes (`_answer`) |
| 0x6351, click on an already chosen card, menu closed (−1) | the request is re-sent (with the count); it cannot be cancelled |
| bot.ts decides (auto, time limit) | the panel closes with the next NEW_PHASE (`_m_NEW_PHASE`) |

With 8+ cards ocgcore asks a single SELECT_CARD with min = max = N: N clicks and the count goes N → … → 0. Tested up
to N = 16.

Methods touched: `_m_NEW_PHASE`, `_s_SELECT_CARD`, `_pick_request`, `_answer`. New: `_discard_var`, `_last_hint3`,
`_hand_limit`, `_discard_pick`; class attributes `ocg_phase`, `discard_open`. `_pick` and `_click` are unchanged.

### 20.3 Tests

- Unit tests:
  - 7 cards: exact sequence, response and close;
  - 8 cards: 2 clicks, count 2 → 1 → 0, right click and repeated click;
  - what is NOT a hand-limit discard: no HINT, another HINT, Main Phase, min ≠ hand − 6, with a chain;
  - flag off, cfg without the key, and bot.ts deciding.
- The simulated client tracks the type 0x1e table and the panel: it fails if the panel opens with count 0, or is still
  open when the turn or phase changes.
- Real duels with duelcore (24 Pot of Greed + 16 Mystical Elf, and a passive player that activates what it can and goes
  to the End Phase): 6 duels, 9 panels (6 of 2+ cards), boards equal to ocgcore, 0 RETRY. The 30 random duels add 31
  more panels. A real trace shows the panel in its 3 discards.
- `fake_client.py descarte`: NPC duel over the network with 14 Pot of Greed. The fake client passes turns and clicks
  cards with the panel open. Checks the text, the count (N → … → 0), that N cards go to the graveyard and that the turn
  does not change with the panel open: 7/7 (panels of 1, 2, 4, 7 and 16 cards across runs).

### 20.4 Live checks

1. End your turn with 7+ cards (pass without playing, or draw with Pot of Greed). On entering the End Phase, the panel
   "Discard from your hand." should appear in the centre with as many small cards as you must discard.
2. Click a card in the hand (direct click): it goes to the graveyard. With 8+, the panel removes one small card per
   click and closes with the last one.
3. The panel must not cover the hand or remain during the NPC's turn.
4. If something goes wrong: `OCG.TR.DEFAULT_CFG['hand_limit_dialog'] = False` (immediate).

Not done (proposals):
- the official server probably showed the opponent "Your opponent has discarded their hand" (dlg 202) when the NPC or
  the other player discards;
- discards BY EFFECT (dlg 347/20/41 as panel 0xff, `pick_panel`) still use masks only.

## 21. Monster effects from the HAND in a chain window (opponent's turn / damage step)

Trigger: a hand card that "would not activate" turned out to be Cyber Dragon (only in your Main Phase, correct). The
untested case was reviewed in depth: monster effects from the hand in response.

### 21.1 2008-era cards present in the YO2 card table (effect from the hand, quick or trigger)

Sweep of the scripts (`SetRange(LOCATION_HAND)` + QUICK/TRIGGER). Battle Fader and Tragoedia are **not** in the client.

| Card | Script | How it arrives (duelcore) |
|---|---|---|
| Kuriboh, Arcana Force XIV - Temperance | QUICK_O, EVENT_PRE_DAMAGE_CALCULATE | SELECT_CHAIN at damage calculation (`hint_timing_other` 0x4000) |
| Gorz the Emissary of Darkness | FIELD+TRIGGER_O, EVENT_DAMAGE (Special Summon from the hand) | **SELECT_CHAIN** (not EFFECTYN); on resolution, MOVE hand → zone + SPSUMMONING → 0x2d |
| Honest | QUICK_O free, Hint(DAMAGE_STEP); condition: damage step before calculation with your LIGHT in battle | SELECT_CHAIN in the damage step |
| Cyber Ogre | QUICK_O free, Hint(BATTLE_PHASE); condition: your Cyber Ogre in battle | SELECT_CHAIN (not tested in a duel) |
| D.D. Crow | QUICK_O free, Hint(0, STANDBY\|MAIN_END\|…_E) | SELECT_CHAIN in almost every window |
| Herald of Green Light, Gravekeeper's Watcher | QUICK_O, EVENT_CHAINING | SELECT_CHAIN when the opponent activates |
| Watapon, Green Baboon, Dragon Ice, Cyber Dinosaur, Phantom Dragon, Golden Ladybug, Dark Sage… | triggered from the hand | not tested one by one (same path as Gorz) |

The recorded traces contained only ONE Kuriboh window for player 0 (an automated test that ends there); elsewhere
Kuriboh only appears as "summon" in SELECT_IDLECMD. A monster had never been activated from the hand in a chain window
in a live session.

### 21.2 What the client does with a hand card carrying the activate bit (C, code)

- `FUN_007390a0` (input loop): with permission (`+0x674` bit 0, set by 0x1a) and the mouse over the hand (zone 0xb),
  it reads the mask with `FUN_00791350(j, index)` = the u16 of 0x6612 as is (`+0x66c4 + j*0x12a + i*2`). It only
  checks the scene mode `DAT_012a9084` (7/8/10/11 = no masks, `FUN_00794d90`); it does **not** check the card type, the
  phase or whose turn it is.
- Mask = only bit 11 → direct click. Otherwise `FUN_00739440` → `FUN_00786b70` (bits of 0x27ff) → `FUN_0077c940`: one
  CWindowTaskListboxDuelCmd line per set bit, labelled from table `0xcd4a10` (bit 3 → label 7, the same "Activate" used
  for Spells in the hand in the Main Phase, already seen live). **There is no card-type filter**: a hand monster with
  bit 3 shows "Activate" just like a Spell.
- On selection, `FUN_007222a0` → `FUN_00691e40` sends 0x6350 (0, 0xb, index, 3) without doing anything locally.
- Activating from the hand a monster with a discard cost had already been played live (King of the Swamp in the Main
  Phase). ocgcore order: CHAINING (location 2) → MOVE (cost) → CHAINED. The translator sends 0x2f (card) + 0x12 (zone
  0xb, index) and then 0x3f to the graveyard.

### 21.3 What was failing (C, duelcore + network)

The whole path YES → mask → click → index → CHAINING worked. What failed was the **prompt priority** (§12,
`chain_priority = "evento"`): with "Activation message" on automatic or semi, the client answers NO by itself to
P2 < 2 and the player sees nothing.

- Kuriboh, Temperance, Herald, Watcher and Gorz's 1st window: P2 = 2 by (b) (effect with an event). Correct.
- **Honest** (and Cyber Ogre, Destiny Hero - Blade Master): free effect → P2 = 1 → never shown. duelcore with the client
  on automatic: 20 Honest windows and 0 activations. Over the network: prompts `[1, 1]`.
- **Gorz pending after an own link** (you take damage, chain D.D. Crow and Gorz is offered again): (a) gave 1 → Gorz
  was lost.

### 21.4 Implemented (`translator.py`, flag `chain_priority_hand`, on; `YO2_CHAIN_PRIORITY_HAND=0` disables it)

In `_chain_priority` (applies to "evento" and "spe"; see §28.3 for "oficial"), with new `_hand_priority` and
`_opt_effects`. Only counts for options in the HAND whose card is a MONSTER:
- **(d)** free quick effect with a battle timing in its SetHintTiming (`TIMINGS_BATTLE` = ATTACK | DAMAGE_STEP |
  DAMAGE_CAL | BATTLE_PHASE) during an attack (`in_battle`/`in_damage` or those bits in `hint_timing*`) → 2. The script
  condition already limits the window (Honest only appears in the damage step with your LIGHT in battle).
- **(e)** with an open chain whose last link is OWN, an optional trigger (TRIGGER_O) → 2 (previously (a) gave 1).
- Unchanged:
  - D.D. Crow outside those cases (free, like Book of Moon: 1, unless the chain is the opponent's);
  - Book of Moon from the hand in battle (it is a Spell: not included);
  - a 2nd Kuriboh after your own Kuriboh (1).
- Also, `_opt_effects` now understands the real `description` of this ocgcore (`aux.Stringid` = code << 20 | n).
  Previously it only understood the old format (code·16 + n), which never matched, so all of a card's effects were
  checked.

Rule of ours (D): which P2 the official server sent is unknown here (see §28 for the official logs).

### 21.5 Tests

- Unit tests:
  - (d) Honest and Cyber Ogre → 2 (flag off → 1); Book of Moon and D.D. Crow → 1;
  - (e) Gorz after your D.D. Crow → 2 (flag off → 1; "spe" the same); cfg without the key → DEFAULT_CFG;
  - full path in the damage step: type 3 prompt with P2 2 → YES → bit 3 on Honest in the hand → 0x6350 → index;
    CHAINING from the hand → 0x2f + 0x12 zone 0xb; the cost goes to the graveyard.
- Live duelcore test: 6 duels. The player carries Kuriboh, Temperance, Gorz, Honest, Herald, D.D. Crow, Watcher and
  Cyber Ogre, with a "client" on automatic.
  - Activated from the hand: Kuriboh 7, Gorz 4, Temperance 3, Honest 3, Herald 2, D.D. Crow 2. Board = ocgcore at
    every decision.
  - With the flag off, Honest is never activated (20 prompts with P2 1).
- Gorz's token (44330099) stays in the simulated client's graveyard when it leaves the field: a limitation of the
  simulated client only (see §23.2).

### 21.6 Live checks

1. Deck with Kuriboh. Let the NPC attack you directly: at damage calculation the prompt "activate the card's effect?"
   must appear. YES → mouse over Kuriboh in the hand → "Activate" menu → you take no damage.
2. With Honest in hand and your LIGHT monster attacked (or attacking): the prompt must appear in the damage step, even
   with the option on automatic.
3. Gorz: with an empty field, take damage from the NPC: prompt → YES → Gorz in the hand → Activate → it comes out.
4. If something goes wrong: `OCG.TR.DEFAULT_CFG['chain_priority_hand'] = False` (immediate).

Noted in passing:
- `_yesno` also used `desc >> 4` for SELECT_YESNO without a code; with the real format (code << 20) it did not match and
  the generic text 269 appeared instead of the card name (see also §23.3 and §25).
- 0x17 (BATTLE) sent the damage computed from ATK/DEF, not the real one. With Waboku (no destruction, no damage) the
  client drew "hit" (state 7) instead of "bounce" (state 6) (see `battle_damage_real`, §23.4).

## 22. Turn counter and declared values on a card (MSG_CARD_HINT)

Problem seen live: Swords of Revealing Light (72302403, internal 0x1102) stayed on the field without showing its 3
turns. ocgcore sends `CARD_HINT` with `card_hint` 1 (CHINT_TURN) and `description` = 0, 1, 2, 3 (SetTurnCounter: it
COUNTS UP, 0 on activation; at 3 it destroys the card). The translator had it in IGNORED.

### 22.1 What the original did

- **0x3b (`FUN_00711b50`) is the TURN counter**, not "+1 spell counter" (corrected in §2). P0 = slot. It only acts if
  `FUN_0071bfa0(card)` ≥ 1. With P2 = 0: P1 = 0 → +0xc = 0, no view; P1 ≠ 0 → +0xc += 1 and DUEL_VIEW 0x24
  (`FUN_00742e30`: state 3, timer 0x200). With P2 ≠ 0: view 0x24 with slot 0x10 and the number P2 (over the player;
  **D**). The same handler is repeated in `FUN_00733d10` and `FUN_007283c0` (other dispatch tables). **C**, emulated
  with unicorn on the executable: 0, 1, 2, 3, 4 and back to 0; on a card outside the table (Waboku) it does nothing.
- **`FUN_0071bfa0` = per-card cap** (emulated for all ids 0..0x23ff, `TURN_CARDS` in the translator). Positive (on the
  slot): Swords of Revealing Light 3, Swords of Concealing Light 2, Nightmare's Steelcage 2, Viser Des 3, Dust Barrier
  2, Ekibyo Drakmord 2, Different Dimension Capsule 2, Big Evolution Pill 3, Seismic Shockwave 3, Future Fusion 2, Soul
  Devouring Bamboo Sword 2, Deck Lockdown 2, Sanguine Swamp 2 (+ 0x1ee4, no YO2 id); 0xffff (no cap): Stim-Pack, Castle
  of Dark Illusions, Cocoon of Evolution (only with Petit Moth), Pumpking, Germ Infection, Dark Snake Syndrome,
  Wave-Motion Cannon, Great Dezard, Shattered Axe, Blockman, Spark Blaster. Negative (counted outside the slot): Final
  Countdown −20, Crush Card Virus −3, Lightforce Sword −4, Gold Sarcophagus / The Wicked Avatar / Pikeru's Second Sight
  −2, Eradicator Epidemic Virus / 0xecc / 0x188c −3. **C**
- **Drawing** (`FUN_0075ac50`, called from `FUN_00759430` for every face-up field card, next to `FUN_0075a030` and
  `FUN_0075b5c0`): if `FUN_006eb8d0` (= +0xc while it is < cap and the card is in its zone type) is > 0, or during the
  animation, it draws sprite 6 of `card.txt` = `pas_eff_suna.png` "砂時計アニメ" (hourglass) and the number; during the
  animation sprite 7 "経過アニメ" also appears. So: **Swords shows 1 and 2; 3 only during the animation**, and then it
  leaves. At 0 nothing is drawn. **C**
- **There are no texts** for turns in `yo2text.json` (neither "残り" nor "turn" for this): only icon + number. The client
  has no engine of its own that counts turns: it only executes 0x3b. **C** (no other callers of the table).
- **Declared values** (`FUN_0075a030`, same place): slot +0xc is drawn as a race icon (sprite 5, 1..20 = `YO2_RACE`),
  an attribute icon (sprite 4, 1..6 = `YO2_ATTR`) or a card image (internal id): DNA Surgery 0x1357, The Regulation of
  Tribe 0x1358, Array of Revealing Light 0x14d1 (race, Spell/Trap); Scroll of Bewitchment 0x145b, DNA Transplant
  0x171f, Elemental Absorber 0x1a19 (attribute, Spell/Trap); Prohibition 0x1368, Cloak and Dagger 0x1b67 (card); The
  Hunter with 7 Weapons 0x14cc (race, monster), Tualatin 0x197d (attribute, monster), Disciple of the Forbidden Spell
  0x19c2 (mask: bit i = attribute i). All carry `SetHint(CHINT_…)` in their ocgcore script except Tualatin. **C**
  (drawing and encoding) / **P** (that the official server wrote it with 0x3a: it is the only command that sets +0xc to
  a value).
- 0x3a (`FUN_0070e300`): +0xc = P1 | P2<<16 (high byte of P0 = 0) or it is added; no view. **C** (emulated). When the
  card leaves, `FUN_006e3e70` resets +0xc to 0 (§14.2); 0x3d (change of control) copies it.

### 22.2 CARD_HINT and the rest of IGNORED

Seen in logs: 231 CARD_HINT (191 DESC_ADD/REMOVE in the hand and monster zone, 21 CHINT_TURN in Spell/Trap, 3 in
banished), 8 PLAYER_HINT (Scapegoat).

| Message | Before this section | Original | Status / proposal |
|---|---|---|---|
| CARD_HINT CHINT_TURN (1) on the field | nothing | 0x3b: hourglass + number (1, 2…) and animation when it increases | **done**, `turn_counter` (C/P) |
| CHINT_RACE (3) / CHINT_ATTRIBUTE (4) | nothing | +0xc = YO2 race/attribute → icon on the card | **done**, `declare_hint` (C drawing / P command) |
| CHINT_CARD (2) | nothing | +0xc = internal id → image of the declared card (Prohibition, Cloak and Dagger) | **done**, `declare_hint` |
| CHINT_TURN off the field (Gold Sarcophagus, Lightforce Sword in banished) and negative-cap cards (Final Countdown, Crush Card Virus) | nothing | 0x3b with P2 ≠ 0 = number over the player (view 0x24 slot 0x10); what it counts and when: **D** | proposal: test a 0x3b with P2 = turns with Final Countdown live; nothing invented |
| CHINT_NUMBER (5), DESC_ADD / DESC_REMOVE (6/7) | nothing | the client has nowhere to draw a loose number or per-card effect texts | nothing (C: no drawing) |
| PLAYER_HINT | nothing | no equivalent | nothing |
| RANDOM_SELECTED | nothing | 0x53 "random-pick animation in the hand" (handler not read) | **D**: read 0x53 before using it (random discard) |
| SHUFFLE_SET_CARD | nothing | 0x49 MONST_SHUFFLE (view 0x28) | **D**: slots must be remapped; risk of desync |
| DECK_TOP | nothing | 0x5b DECK_FLIPTOP redraws the decks (§14) | **D** |
| CHAIN_NEGATED / CHAIN_DISABLED | nothing | 0x68 (chain table)? | **D** |
| FIELD_DISABLED, MISSED_EFFECT | nothing | no equivalent found | nothing |
| CHAINED, CHAIN_SOLVED, CARD_SELECTED, HAND_RES, SHUFFLE_EXTRA, REFRESH_DECK, REMOVE_CARDS, MATCH_KILL, AI_NAME, START, WAITING, UPDATE_*, RETRY | nothing | covered by others (0x12/0x13, the client itself) or internal | nothing |
| ADD/REMOVE_COUNTER, SWAP, TOSS_COIN, EQUIP, *_TARGET | already translated (§13, §14) | — | unchanged |

### 22.3 Implementation (`translator.py`)

- Constants `C_SLOT_VALUE` (0x3a), `C_TURN_COUNT` (0x3b), `CHINT_*`, `TURN_CARDS`, `DECL_RACE/ATTR/ATTR_BITS/CARD`; new
  methods `_m_CARD_HINT` and `_hint_slot` (CARD_HINT stays in IGNORED: `feed()` checks `_m_<type>` first). Only
  face-up cards in a slot and in the client's tables.
- CHINT_TURN v = 0 → `0x3b P1 0`; v ≥ 1 → `0x3a (v−1)` + `0x3b P1 1`. The 0x3a is invisible (the value is already
  that) and makes the number match ocgcore even if the client did not have it (duel started before a hot reload,
  change of control, jumps). The official server sent at most the 0x3b (**P**).
- Declared values → `0x3a` with the YO2 race/attribute (lowest bit) or the internal id. A race with no YO2 equivalent
  logs a warning and is not sent.
- Flags in `DEFAULT_CFG`, on: `turn_counter` (`YO2_TURN_COUNTER=0` disables it), `declare_hint`
  (`YO2_DECLARE_HINT=0`). A duel without the keys in its cfg uses DEFAULT_CFG.

### 22.4 Tests

- Unit tests with the real Swords trace: the simulated client (which now executes 0x3a/0x3b like the executable) has
  +0xc = 0, 1, 2 at each CHINT_TURN, view 0x24 shows 1, 2, 3 and the slot is clean after going to the graveyard; flag
  off = no 0x3a/0x3b. Declared values with loose events (DNA Surgery → race 1, Prohibition → internal id of
  Blue-Eyes, Hunter with 7 Weapons → 2; NUMBER/DESC/empty slot/card without a table → nothing; flag off; no key →
  DEFAULT_CFG).
- Live duelcore test (6 duels with Swords of Revealing Light, Swords of Concealing Light and Nightmare's Steelcage in
  both decks): after each batch, every counter card still in its slot holds the value of the last CHINT_TURN in the
  client; no empty slot keeps a value. CHINT_TURN translated {0: 38, 1: 37, 2: 36, 3: 13}. The 30-duel run passes.
- Network: `fake_client.py pvp` 36/36; a hot-reload test with Swords ALREADY on the field gave 0x3a 0 + 0x3b 1, then 2
  and 3, and the card leaves with 0x3f.

Live check: Swords of Revealing Light → after the opponent's first End Phase, hourglass with "1" over the card (and
animation), then "2", and the "3" animation right before it leaves. Prohibition / DNA Surgery → the declared card or
icon on top. If the icon does not appear: check that the card is face-up and its internal id is in the tables (§22.1).

## 23. Sound of each summon type, tokens leaving the field, YES/NO with the card, and the LP display

### 23.1 Sounds and animations of each summon type (C, code + unicorn emulation of 0x4f/0x2c/0x2d)

Sound table = `data/etc/se.txt` in order: id `0x10000000 + n` = line n (0 cursor … 0xc ikenie, 0xd magic_active, 0xe
**reborn**, 0xf **card_move**, 0x10 **card_open**, 0x1c/0x1d fusion1/fusion2).

| Type | Original commands | Views | Sound / animation |
|---|---|---|---|
| Normal | **0x4f** face-up + **0x2c** | 0x19 CARD_MOVE wEffect **1** + 0x34 RUN_SUMMON normal (`FUN_007a28a0`, summon.txt: rainbow + stars) | card_move.wav (0x1000000f, in view 0x19); the effect has no sound of its own |
| Tribute | 0x46 per tribute + 0x4f + 0x2c | 0x2b (yo2SummonManager, `FUN_007ee020`) + 0x19 wEffect 8; then as normal | ikenie.wav (0x1000000c) + card_move.wav |
| Flip | 0x34 (P1 = P2 = 1) | 0x46 (gods only) + 0x1b | card_open.wav (0x10000010) |
| Special | **0x2d** | 0x19 wEffect **2** (no sound) + 0x35 RUN_SUMMON (`FUN_007a3100`, sp_summon.txt) | **reborn.wav** (0x1000000e, in the sp_summon constructor) |
| Fusion | 0x4b/0x4c + 0x4d + 0x2d | 0x36 RUN_FUSION + as special | fusion1/fusion2 (`FUN_007b69d0`) + reborn.wav |
| Token | 0x48 | 0x1c + 0x35 | reborn.wav |

- 0x2c (`FUN_00715230`) is the ONLY command that starts view 0x34. P1 = slot (reads the card in the slot: it goes AFTER
  the 0x4f), P2 low byte = type (2 → flags \|= 0x200008), high byte ≠ 0 → `FUN_006e5f10` (uses the normal summon; not
  used: bit0 of 0x4f already does it). Which type the official server sent: D (we send 2).
- **What was wrong:** a normal summon (Celtic Guardian) went out with 0x2d → `DUEL_VIEW_CARD_MOVE wEffect=2` +
  `DUEL_VIEW_RUN_SUMMON` in the client log = the animation and sound of a SPECIAL summon. Same for a tribute summon.
- Correction to §19: 0x4f sets bit 3 if the card ends FACE-UP (P1 bit 14), not via P2 bit0 (emulated: setting
  face-down leaves the flags without bit 3).

### 23.2 Tokens leaving the field (C)

- There is no "delete" command. The client recognises a token by its type 10 in card_prop (`FUN_0073c010` = (dword +4
  >> 1) & 0x1f; `FUN_007179e0`): 44 ids (Sheep, Metal Fiend, Emissary of Darkness = Gorz…).
- `FUN_006f07e0` → `FUN_006f7960` (graveyard), `FUN_006f7a00` (banished), `FUN_006f9680` (hand) **do not insert it**;
  the CARD_MOVE view (`FUN_00761970`) with a pile destination calls `FUN_007615e0` = deletes the slot object. So the
  original sent it to its destination like any card and the client made it disappear.
- 0x31 already deleted it correctly (a token "remaining in the graveyard" was only a simulated-client artefact), but
  tributing Gorz's token showed the DESTRUCTION animation. Now: tribute for a summon → 0x46; otherwise → 0x31; no
  warning (`token_vanish`).

### 23.3 YES/NO with the card (C thumbnail / P text)

- Thumbnail: the drawing routine of CUYesNoDialog (0x774650) draws the `DAT_012ccb2c` cards of `DAT_012ccb34` only if
  the mode of the last 0x6610 list (`DAT_012ccb24`) is **0xff** (the self-closing type 3 resets it to 0). 0x6610 opens
  no window. Without a list, the card from the last list was shown (an unrelated card). Now a 0x6610 mode 0xff with the
  card (EFFECTYN/YESNO) or the candidates (chain prompt, up to 5) goes before the 0x32 (`yesno_card`, on).
- EFFECTYN text: dlg 250 "Activate the effect of "%s"?" (328 in the hand, 329 in the graveyard), list A [(internal id,
  4), (dlg, 0)] in one 0x61 (the format of DLG 7/8, already used live). Previously [(id, 4)] alone. YESNO (script text)
  is unchanged.
- **D, proposal:** text of the chain prompt without an open chain. Initially dlg 269 generic (now with the card
  thumbnail). Options: `chain_card_name` (exists, off) or dlg 250 with the name if there is only one candidate (§24.1);
  superseded by the official event text (§28).

### 23.4 LP display (C)

- The client applied each 0x25 correctly (`FUN_00716440` subtracts P0 and clamps at 0; P1 = 2 only adds effect
  0x14c0). What drifts is the **LP display**, which is a separate number: object `DAT_012a9070 + 0xa9c + j*0x44`
  (+0x28 number, +0x2c pending, +0x34 hold).
  - `FUN_007cefc0(d, hold)`: if holding the SAME figure, releases it; otherwise overwrites it. The number moves toward
    the pending value only when not holding (0x7cf070) and stays at 0 if it goes down.
  - 0x25: step 0 view 0xf with hold (only if NO display is holding); step 3 without hold.
  - **0x17 BATTLE_RUN** (view 0xd, 0x754ad5) leaves every non-zero damage on the display WITH hold.
- Observed case: a direct attack of 600 WITHOUT a DAMAGE message (ocgcore dealt no damage). The 0x17 announced 600
  (computed from ATK) → −600 held on the defender's display. The next 0x17 of 600 released it (600 too many) and its
  0x25 subtracted another 600; after a Brain Control cost (800) the display reached 0 while the real LP was 600.
  Reproduced with the simulated client (`ClientModel.hud`) on the trace.
- Fix (`battle_damage_real`, on): 0x17 carries the DAMAGE messages that follow BATTLE in the batch (without a batch,
  the computed value). Safety net of ours (`lp_resync`): at the start of each turn, 0x26 LIFE_SET with the mirror LP for
  both players (view 0xe sets the number; if equal nothing is visible). **Off by default since §24**: replaced by
  `lp_reconcile`.

### 23.5 Flags (`DEFAULT_CFG`; `YO2_*=0` disables them)

`summon_kind_fx` (on), `token_vanish` (on), `yesno_card` (on), `battle_damage_real` (on), `lp_resync` (off by default
since §24; `YO2_LP_RESYNC=1` enables it). A duel started before a hot reload has no keys in its cfg and uses
`DEFAULT_CFG`.

### 23.6 Tests

- 30-duel run passes. New unit tests (normal, special, tribute, flip, fusion, tokens, flags off, missing keys, NPC,
  three real PvP traces, YES/NO, LP before/fix/safety net) and live duelcore tests (2 decks: normal 96, tribute 15,
  special 53, flip 11, fusion 3, tokens leaving 28 / tributed 8). The simulated client handles 0x2c, tokens out of the
  piles and the LP display (error if it differs from LP at a turn change; all duels pass).
- `fake_client.py pvp`: 36/36; `npc`: 5/5.

Live checks: normal summon = card-move sound + rainbow/stars (not the special one); special as before; tributing a
token = tribute animation and it does not appear in the graveyard; YES/NO for an effect = "Activate the effect of
"X"?" with card X; chain prompt = thumbnail of the card you can activate; LP display correct after the next turn
change.

## 24. Chain prompt with the name of ONE card, and LP reconciliation

### 24.1 Chain prompt with the card name (`chain_one_name`) — P (text)

Default off (enabled in the "enhanced" profile); env `YO2_CHAIN_ONE_NAME`.

- Optional SELECT_CHAIN without an open chain and candidates = ONE card (same place, even if it offers two effects):
  list A [(internal id, 4), (dlg, 0)] with dlg 250 "Activate the effect of "X"?", 328 if it is in the hand, 329 in the
  graveyard. It is the EFFECTYN format of §23.3. Thumbnail 0x6610 mode 0xff: that card, once.
- Several cards: the generic (269, 2) with their thumbnails. Open chain: `chain_link_text` (dlg 7/8), unchanged.
  `chain_card_name` (off) only applies if there is not a single card.
- With the official event text (§28, `chain_event_text`) the official form wins when there is an event.
- Methods: `_s_SELECT_CHAIN` (changed), `_chain_one_pairs` (new).

### 24.2 LP reconciliation (`lp_reconcile`) — C (client) / D (not done by the official server)

Default on; `YO2_LP_RECONCILE=0` disables it.

Client LP display (disassembly):
- `FUN_007cef90` (view 0xe = 0x26): only writes the number (+0x28), clamped at 0. Does not touch pending.
- `FUN_007cefc0(d, -1, hold)`: if holding the SAME figure, releases the hold (`FUN_007cf030`); otherwise overwrites
  pending/hold (what was held is lost), restarts the delay (+0x38) and plays a sound.
- `0x7cf070` (every frame): without a hold, the number moves toward pending (in steps of 100, 10, 1) after ~32 frames.
- **0x27 LIFE_RESET** (`FUN_00716ba0`): sets a per-player field to 0 (`DAT_012cc480 + j*0x14`, the same one cleared at
  duel start; use unidentified, P) and starts view 0x10 → `FUN_007cf050` on BOTH displays: pending and hold to 0. No
  animation.
- **0x26 with the same value**: view 0xe (sets the same number), LP = P0, statistics (`FUN_00721ef0/ed0` = `ret`) and
  view 9 BGM_UPDATE (also started by every 0x24/0x25). No animation or sound. With another value, the number jumps
  without counting.
- 0x26 alone is NOT enough: a −N held from the 0x17 keeps waiting and the next 0x17 might apply it. Also, 0x25 starts
  counting WHEN ITS COMMAND ENDS, so a 0x26 right after it would be overwritten by what remains to count. Hence the
  correction is **0x27 + 0x26 for both players**.

Implemented (`translator.py`):
- `YO2Duel.hud` = model of that view's per-player LP display ([number, pending, hold]), same as `ClientModel.hud` in
  the tests. `_hud_track` updates it with every 0x24/0x25/0x17/0x26/0x27 going out to the client.
- `_lp_watch` compares with the engine LP (the mirror, or the `lp` of the duelcore select if present):
  - after each DAMAGE/RECOVER/PAY_LPCOST/LPUPDATE, for that player;
  - before each request (select, before 0x1a/0x32) and at the end of each `feed_many` batch (BATTLE, chain…), for
    both.
- Match (number = LP and nothing held): nothing is sent (1:1). Mismatch: 0x27 + 0x26 × 2. Also:
  - log line `[lp] desfase duelo <id> vista <v> turno <n> jugador <j>: cliente X (±N en espera) / motor Y; último:
    <evento j cifra (carta passcode)> -> 0x27 + 0x26`;
  - an entry for monitoring.
- PvP: each view is its own `YO2Duel` (view 1 mirrored), so each reconciles independently. Spectators and `npc_watch`
  receive the same as the player.
- Duels started before a hot reload: the model is created at their next event with the mirror LP (assumed to match at
  that point).
- `lp_resync` (0x26 at the start of each turn) is **off by default** (`YO2_LP_RESYNC=1` enables it).

**Monitoring data** (read by an admin tool, not included): on each translator (`OcgSession.d`; PvP
`PvpSession.views[0]`/`[1]`):
- `lp_desync_n` (int, desyncs corrected in that view);
- `lp_desync` = list (new on each entry, last 50) of dicts {`hora` "HH:MM:SS", `ts`, `duelo`, `vista`, `turno`,
  `jugador` (relative to that view: 0 = the view's owner), `jugador_abs` (= jugador ^ vista; NPC: 0 human / 1 NPC),
  `cliente`, `pendiente`, `espera`, `motor`, `evento`}.
- Duel and view come from the session holding the `query` lambda (`_lp_tag`); if `ocg_duel` ever sets `duel_id` on
  the translator, that takes precedence.

### 24.3 Tests

- 30-duel run passes. New:
  - unit tests:
    - prompt: trap 250, hand 328, graveyard 329, two effects of one card = one thumbnail, several 269, open chain 7/8,
      flag off, no key;
    - LP: no desync → nothing; desync forced in the model → 0x25 + 0x27 + 0x26 × 2 + warning + entry; hold left by a
      0x17 without DAMAGE (at the end of the batch); select LP ≠ mirror; no key / old duel; duel/view/turn from the PvP
      session;
    - the real PvP trace of §23.4 with `battle_damage_real` off: the 1st 0x27 goes out IN THE BATCH of the BATTLE
      without DAMAGE and the display = LP throughout (no errors); three real PvP traces with defaults: 0 desyncs.
  - live duelcore checks in ALL duels (`chain_text_errors`, `lp_desync_n == 0`). In the battery: 287 one-card prompts
    in the hand (328), 24 on the field (250), 128 with several (269), 13 with an open chain. All correct and 0 desyncs.
- `fake_client.py pvp|npc|npcmirar`: 36/36, 5/5, 15/15.

Live checks:
1. With ONE set trap (or a lone Kuriboh in the hand) during the opponent's turn: "Activate the effect of "X"?" with its
   thumbnail (if `chain_one_name` is on). With two activatable cards: the generic text with both thumbnails.
2. LP: in a normal duel no `[lp] desfase` line should appear in the log. If it does, the display jumps to the correct
   value without animation; the line says which event/card caused it.

## 25. ocgcore system YES/NO prompts with the client's own text

Seen live: ocgcore requested `SELECT_YESNO` with `description: 30` ("A replay occurred, continue the attack?", the
battle replay because the opponent summoned a monster during the attack). Without a card, `_yesno` always showed the
generic dlg 269 "activate the card's effect?".

### 25.1 Which `description` arrives without a card

- Traces (386 files, `select` events): `SELECT_YESNO` with `description` < 1<<20 only **30**, once (the bug). All other
  YES/NO carry a card: 144 `SELECT_YESNO` with `passcode<<4 | n` (aux.Stringid) and 44 `SELECT_EFFECTYN` with `code`
  (39 with 221, 5 with 0). No `SELECT_OPTION` [1213, 1214] (the only one present uses card texts).
- ocgcore source: not in the repository (`ocgcore-wasm` 0.1.2, compiled). What the core itself sends is P (EDOPro
  source): 30 battle replay, 31 direct attack, 32 another Battle Phase, 90-93 summon/tribute/materials.
- Scripts (`CardScripts`, `Duel.SelectYesNo(tp, N)` with a system N): 94 (14 scripts, 9 official), 31 (3, unofficial),
  93 (2, unofficial), 210 (1), 98 (`utility.lua`), 65, 2 and 500 (unofficial). `SelectEffectYesNo(tp, c, 95/96)`
  carries the card: it takes the card path (§23), unchanged.

### 25.2 Pairings (`YESNO_SYS_DLG`)

Client texts extracted from the client's dlg tables (Spanish and Japanese). Only texts with the same meaning and the
same answer (YES = what ocgcore asks) are paired.

| description | ocgcore (strings.conf) | dlg | Client text (meaning) | Client text (Japanese) | Conf. |
|---|---|---|---|---|---|
| 30 | A replay occurred, continue the attack? | 223 | "Battle replay. Continue to @3attack@0?" | 「戦闘が巻き戻されました。@3攻撃@0を続けますか？」 | C (arrives in traces; same meaning) |
| 31 | Attack directly? | 225 | "Will you @3attack the player directly@0?" | 「プレイヤーへの@3直接攻撃@0にしますか？」 | C (text) / P (that it arrives: no cases in traces) |
| 32 | Conduct another Battle Phase? | 221 | "Will you start the 2nd @2Battle Phase@0?" | 「２回目の@2バトルフェイズ@0を開始しますか？」 | P (no cases in traces) |
| 94 | Activate this card's effect now? | 270 | "Use the effect now?" | 「今、効果を使用しますか？」 | P (scripts; no cases in traces) |
| 210 | Continue selecting cards? | 93 | "Select another @3card@0?" | 「さらに@3カード@0を指定しますか？」 | P (1 script; no cases in traces) |

No clear pairing: they keep the generic 269.
- 90 "Normal Summon without Tribute(s)?": dlg 114 ("Summon "%s" without a tribute?") contains `%s`, and without a card
  there is no name. Class 2 alone copies the text verbatim (25.3), so "%s" would appear.
- 91 (additional Normal Summon), 92 (opponent's monster), 93 (keep choosing materials), 98 (effect replacing the cost):
  the client has no such questions.
- 65 "Use Effect", 2 "Special Summon", 500: labels or hints, not questions. Only used by unofficial scripts.
- 95/96/97/218-222 arrive with a card (EFFECTYN). They do not go through here.

`SELECT_OPTION` with [OPT_YES, OPT_NO] (1213 "Yes" / 1214 "No"): not applicable. The options are the answers, and the
question comes from the card script (there is no system `description`). It keeps dlg 11 (Magic Drain) or 269.

### 25.3 Format (list A [(dlg, 2)], the same as the generic one)

- Class 2 of list A (`FUN_00744670`, jump table 0x74495c, case 2 = 0x7447e8): with ONE pair, `0x743750(out, value)`.
  If the value is < 0x20000, `0x73be10(value)` = dlg text (`dlg_indx` + `dlg_text`) and `0xb0d6d8` copies it verbatim:
  no formatting, no `%s`. C (disassembly)
- That buffer is the `s_sStr` of case 0x32 (`0x73ec77`, log "DUEL_VIEW_RUN_DIALOG s_sStr=%s iType=%d") and goes to the
  type 2 CUYesNoDialog (`0x73ee7e` → `FUN_00773f20` → `0x5e6b50`). C (disassembly)
- `@3…@0`: the client's text styles (`FUN_00604410`, see `research/mundo/NOTES.md`: @3 green, @2 red). Texts 7/8/250/269
  already go out live with the same codes. P (the dialog's painting was not traced down to `FUN_00604410`; it goes
  through a virtual table)

### 25.4 Implemented (`yesno_sys_text`, on; `YO2_YESNO_SYS_TEXT=0` disables it) — P (text)

- `YESNO_SYS_DLG = {30: 223, 31: 225, 32: 221, 94: 270, 210: 93}` (module constant).
- `_yesno` (and its aliases `_s_SELECT_EFFECTYN` / `_s_SELECT_YESNO`): without a card →
  `YESNO_SYS_DLG.get(description, 269)` in list A [(dlg, 2)] + 0x32 type 2. With a card, unchanged (§23).
- A duel started before a hot reload has no key: `DEFAULT_CFG` applies. The old key `yesno_text` remains unused.

### 25.5 Tests

- 30-duel run passes. New unit tests:
  - 30 → 223, 31 → 225, 32 → 221, 94 → 270, 210 → 93, with 0x32 type 2;
  - 0, 90, 221, 222 and a passcode without a YO2 card → 269; flag off → 269; no key in the cfg → `DEFAULT_CFG` (also
    switched off immediately);
  - with a card: an EFFECTYN → (id, 4) + (250, 0) and a YESNO for Ryko → (id, 4), flag on or off;
  - real trace: the YES/NO 30 goes out with (223, 2); flag off, (269, 2). No errors in the simulated client.
- A hot-reload test mid-duel: running `YO2Duel`s (without the key in their cfg) switch from 269 ×3 to 223 / 225 / 269
  and the duel continues to the surrender without numbering errors.

Live check: in a battle replay (the opponent summons a monster during the attack), "Battle replay. Continue to
attack?" with "attack" in green, and YES keeps attacking. If a loose "@3" or a blank text appears, disable and report.

## 26. Detail panel while the chain prompt is open (hover) and thumbnail of the 7/8 prompt

Seen live: the NPC activates Zoma the Spirit (set trap: POS_CHANGE + CHAINING) and the prompt "'Zoma the Spirit' is
activated. Resolve it as part of a Chain?" (0x32 type 3, text 8) appears. When hovering the hand, the field or the
thumbnail, the left panel (large card + text) does not change. The thumbnail is the Mystical Space
Typhoon from the player's own hand, not Zoma.

### 26.1 How it worked in the original (RE: decompiled code + disassembly)

| Fact | Evidence | Conf. |
|---|---|---|
| The left panel is `CCardDetailViewDlg` (vtable `0xbebc30`, scene `DAT_012a9070`+0x138). It paints the card at `+0x438` (internal id) / `+0x440` (uid). The setter is `FUN_00742eb0(id, uid)`; if the uid changes, it sets `+0x43c` to 0 | `FUN_0075cd20` (ctor), `FUN_00742eb0`, `FUN_00742f30` (dynamic_cast) | C |
| Hover is done by **`FUN_00799030`**, called every frame from the scene update (`FUN_00799470`, 0x7998fd). It does the picking (`FUN_007625a0`, scene+0x1e0, mouse position) and, with the mouse still for ~4 frames over the card (`+0x1974` = 3), writes the panel's `+0x438`/`+0x440` (0x7992a5). A face-down opponent card leaves uid = −1 and id 0 (card back) | disassembly of 0x799030-0x7992f4 | C (code) / P (the 4 frames) |
| **The modal dialog blocks it.** Before picking: if scene+0x104/+0x108 (yo2TaskPtr) holds a `CWindowTaskModel` whose virtual method **+0x3c** returns ≠ 0, picking is skipped. No card is under the mouse and the panel does not change | 0x799061-0x7990a6 | C |
| Every DUEL_VIEW 0x32 dialog is stored in that slot: `thunk_FUN_00747210` with `ecx = DAT_012a9070 + 0x104` (0x74030e-0x74031b), including type 2/3 (`CUYesNoDialog`, creator `FUN_00773f20`) | disassembly of case 0x32 | C |
| `CUYesNoDialog` (vtable `0xbec058`; RTTI: CWindowModelDialogYesNo → CWindowModelDialogWithBtns → CWindowTaskModel) **does not override** +0x3c: it is the base one, `0x5deee0` = `return 1`. It always "captures" the screen. `CUDiscardDialog` (§20), however, overrides it (`0x772320`, from `CUGDialogBaseHP`: depends on whether the mouse is over the panel or dragging it). That is why hovering the hand works with the discard panel | vtables read from the executable + RTTI (COL/CHD) | C (vtables) / P (what 0x772320 does) |
| With the dialog open no card can be clicked either: the scene calls `FUN_007390a0`/`FUN_007397e0` (card clicks) only if there is no modal or its +0x48 ≠ 0 (or `DAT_012a90a4`). CUYesNoDialog's is `0x5def00` = `return 0` | `FUN_00799470` (branch after `FUN_007862a0`, both times) | C |
| The thumbnail has no mouse handling. The dialog's `+0x14` (`0x774650`) only draws the `DAT_012ccb2c` cards of the 0x6610 mode 0xff list (`FUN_007202a0` + sprites `FUN_007d6790`). It does not check the mouse over them or touch the panel. `+0x10` (`0x7744e0`) only moves the dialog down 20 px to make room | disassembly | C |
| **There is no way to see the info with the dialog open.** The right button (`[DAT_012711ac+0x148]+8`, §12) closes the dialog with NO (0x7748c0 → `FUN_0071f8c0(0)`), unless it was already pressed when the dialog opened. There is no "detail" key or button | 0x7748c0-0x774949 | C |
| With the dialog open, the panel only changes if the **server** changes it: view 0x21 CARD_HAPPEN (engine **0x2f** and **0x30**, "card X activates"; ends in `FUN_00742eb0(id, uid)` at 0x741693), view 0x12 HAND_SHOW (opponent's hand card) or view **0x37 RUN_DETAIL** (0x6202 type 0x1b; `id = DAT_012ca4ca[P0*5]`, panel = (id, P0) at 0x7416c6-0x7416ea, nothing else) | cases 0x21/0x12/0x37 of `FUN_0073dd80` | C |

Conclusion. Hover not working while the prompt is open is **client** behaviour (1:1, C) and cannot be fixed on the
server. What does depend on the server is **which card is in the panel** when the prompt opens. On the official server
the opponent's activation probably came with 0x2f/0x30 (CARD_HAPPEN: animation + panel with that card), so the panel
already showed the chain link's card (P: those commands have no other use; no official trace). Here:
- from the hand, graveyard or banished, 0x2f is already sent (`_m_CHAINING`), and the panel changes;
- from the field (a set trap such as Zoma, or a monster effect on the field) only 0x30 with `activate_flash` is sent,
  which is OFF (not tested live). The panel kept the last card hovered before the prompt.

### 26.2 Thumbnail of the 7/8 prompt (C client / D official)

- The client draws whatever is in the 0x6610 mode 0xff list, without choosing anything itself (C). The only chain
  prompt the client opens on its own (tutorial, command 0x66 → `0x72a010`, type 3) sets no list (C). Which card the
  official server sent in a 7/8 prompt: **D**, no trace.
- Since §23, the player's OWN activatable cards are sent (up to 5): the Mystical Space Typhoon observed is the intended
  behaviour, not a bug (P). With the dialog open there are no masks and no hover (C, above), so the thumbnail is the
  only thing that tells WHAT you can chain with. The opponent's card is already named by the text and, since §26,
  appears in the left panel. Showing the opponent's card in the thumbnail instead would mean changing `pic` in
  `_s_SELECT_CHAIN` when there is a `link` (not done).

### 26.3 Implemented (`chain_link_detail`) — C (client) / P (official server behaviour)

Default on; `YO2_CHAIN_LINK_DETAIL=0` disables it. P: that the official server left that card in the panel.

- `_s_SELECT_CHAIN`: if the 7/8 text is sent (`chain_link_text` + `_chain_link_pairs`), **0x6202 type 0x1b B=0x37
  (P0 = uid)** with the last link's card (`_chain_link_detail`) goes before the 0x32.
- `_chain_link_detail`: only if that card is still on the field (`self.chain[-1]` = player, slot 0-10), face-up and
  with the link's code. From the hand, graveyard or banished nothing is sent (0x2f already did it). Never for a
  face-down card: the panel would reveal the real card.
- Constant `T_VIEW_DETAIL = 0x37`. A duel started before a hot reload has no key: `DEFAULT_CFG` applies. The
  translator's uid is the client's (`owner + 2·serial`, same as `FUN_00799030`).
- More faithful alternative (P), without new code: enable `activate_flash` (0x30 when a field card activates → view
  0x21 → panel and flash). It changes more things (animation on every own and opponent activation) and has not been
  tested live. If enabled, `chain_link_detail` just repeats the same card.

### 26.4 Tests

- 30-duel run passes. New unit tests:
  - real trace (first 72 lines; `replay_trace(..., upto=)`): (0x37, uid of Zoma) + (0x32, 3) with [(Zoma, 4), (8, 0)].
    The simulated client puts Zoma in the panel. Flag off: only the 0x32;
  - synthetic: on / off / no key (also switched off immediately via `DEFAULT_CFG`) / no 7/8 / no chain / link already
    in the graveyard → nothing / last link OWN on the field → its uid.
- The simulated client understands view 0x37 (`details`); it errors if the uid does not exist or the card is face-down
  on the field.
- `chain_text_errors` (all duelcore duels) checks the 0x37 of every prompt with an open chain and that it precedes the
  0x32: 9 of 13 in the battery (the other 4, link from the hand).

Live check: when the NPC activates a set trap and "'X' is activated. …Chain?" appears, the left panel must show X
(large card and text) BEFORE answering. Hover still does not work with the prompt open: that is how the original
works. After closing it (YES/NO) hover returns.

## 27. Duel rule: MR5 (2020) vs the YO2 (2008) rules, and the `YO2_DUEL_RULE` selector

### 27.1 What changes in ocgcore between MR1 and MR5

Sources (C): `OcgDuelMode` from ocgcore-wasm 0.1.2 and `constant.lua`; what each bit does comes from the C++ code of
edo9300/ygopro-core (commit 46779fb).

| Bit | MR1 | MR5 | What it does |
|---|---|---|---|
| 0x100 OBSOLETE_IGNITION | yes | no | Ignition priority (old OCG rule). In M1/M2 with no chain, when a chain ends or right after one of their summons, the turn player may activate ignition effects of their field monsters before the opponent responds (`processor.cpp`, PointEvent/QuickEffect) |
| 0x200 FIRST_TURN_DRAW | yes | no | The first player draws on turn 1 |
| 0x400 1_FIELD | yes | no | Only one face-up Field Spell between both players. When a new one's activation resolves, the opponent's is destroyed (done by the core, in `SolveChain`); one's own old one is destroyed when placing the new one. There are still two zones (SZONE 5 of each player) |
| 0x800 PZONE / 0x2000 EMZONE / 0x4000 FSX_MMZONE | no | yes | Pendulum zones (= Spell/Trap 0 and 4) and Extra Monster Zones (MZONE 5-6). Without EMZONE, everything from the extra deck goes to MZONE 0-4 |
| 0x8000 TRAP_MONSTERS_NOT_USE_ZONE | no | yes | In MR1 a trap monster occupies its monster zone AND its Spell/Trap zone (old rule) |
| 0x10000 RETURN_TO_DECK_TRIGGERS | yes | no | Triggers of cards sent to the deck or face-down to the extra deck: in MR1 they can activate; in MR5 only "when returned to the deck" ones |
| 0x20000 TRIGGER_ONLY_IN_LOCATION | no | yes | In MR5 a trigger only activates if the card is still where it was when the event happened (2020 rule). MR1 is more permissive |
| 0x40000 SPSUMMON_ONCE_OLD_NEGATE | yes | no | "Only 1 Special Summon per turn" is used up even if the summon is negated |
| 0x80000 CANNOT_SUMMON_OATH_OLD | yes | no | The Special Summon counter of oaths ("you cannot Special Summon this turn") counts on declaration, even if later negated |

Same in both (C):
- hand limit 6 in the End Phase (fixed in the core unless NO_HAND_LIMIT);
- 8000 LP and starting hand 5 (set by duelcore);
- the first player CANNOT enter the Battle Phase on turn 1 (ATTACK_FIRST_TURN is in neither);
- "missing the timing" (MSG_MISSED_EFFECT): does not depend on a flag. In live traces it appears with Green Baboon and
  Axe of Despair;
- OCG-style SEGOC (the TCG_SEGOC bits are in neither).

### 27.2 Differences vs the 2008 client and affected YO2 cards

Base: the YO2 card table (3232 cards with a passcode).

| Rule | MR5 | 2008 / client | Source | YO2 cards |
|---|---|---|---|---|
| First player's draw on turn 1 | No draw: 421 of 421 recorded traces without a draw in turn 1's Draw Phase | The executable does not decide: phases and draws are sent by the server (`FUN_00722440` is dead code; `FUN_007120e0` and `FUN_00712c00` do not check the turn). The 2008 OCG rule did draw: EDOPro sets 1ST_TURN_DRAW in MR1/MR2 and removes it in MR3 (2014) | C (traces) / D (exe) / P (2008) | all |
| Field Spell | One per player: two can be face-up (133 such decisions in tests) | The client keeps ONE (`DAT_012ca4c0`, command 0x14). The ATK/DEF computation `FUN_006fae40` `break`s at the first face-up card in slot 10 of either player. A single background | C (client model) / P (rule) | 38 Field Spells (Yami, Sogen, Umi, Mystic Plasma Zone, Necrovalley, Skyscraper, Neo Space…). 22 NPC decks contain one |
| Extra / pendulum zones | Exist. With FSX_MMZONE a fusion goes to MZONE 0-4 or 5-6 | Do not exist: 11 slots per player (0-4 monster, 5-9 Spell/Trap, 10 field; `FUN_00707d00`, `FUN_00707a90`) | C | 145 Fusion Monsters. Real risk in MR5, see below the table |
| Ignition priority | no | yes in MR1 | C (core) / P (that the official server had it) | 295 monsters with ignition effects. 8 such windows appeared in tests; the translator treats them as another chain prompt |
| Trap monsters | Monster zone only | Also occupy their Spell/Trap zone | C (core) / D (how the client drew it) | Embodiment of Apophis, Metal Reflect Slime, Zoma the Spirit, Cyber Shadow Gardna, Stronghold the Moving Fortress (NPCs 28, 32, 34, 75, 76, 80, 81, 83) |
| "Cannot Special Summon" oaths | Counts only if the summon happens | Counts on declaration | C (core + scripts) | Scapegoat, Stray Lambs, Dark Magic Curtain, Lava Golem, Fires of Doomsday, Gateway to Dark World, Arms Hole, Spell Gear |
| "1 Special Summon per turn" (SetSPSummonOnce) | — | — | C | No YO2 card uses it: the bit changes nothing |
| Triggers from deck/extra and "trigger only in location" | 2020 rules | Old rules | C (core) / P (specific effect) | Few. Magma Neos, Storm Neos and Serpentine Princess use EVENT_TO_DECK and behave the same in both. The rest are rare cases of cards moved before activating (banished Sangan/Witch…) |
| Hand limit 6, 8000 LP, hand 5, no attack on turn 1 | Same | The client does not check the hand limit (the discard panel was opened by the server). 8000 LP by default in `FUN_00691050`, overwritten by 0x600c | C | — |

Extra zone risk in MR5: the translator maps MZONE n to client slot n, so MZONE 5/6 would land on Spell/Trap 0/1. The
human does not choose it (`_free_places` only uses 0-4). But the NPC brain (order 2,1,3,0,4,**5,6**) and bot.ts (0..7)
take it if the 5 main zones are full. In MR1 the core does not offer it.

**Independent of the rule (no flag fixes it):**
- Project Ignis scripts implement the CURRENT errata of each card, while the client carries the 2008 text (C,
  `card_desc_e.bin`). Examples:
  - Witch of the Black Forest and Sangan, without "once per turn";
  - Ring of Destruction, without its restrictions;
  - Rescue Cat, without "effects negated";
  - Future Fusion, Brain Control…
- The script collection contains 68 `pre-errata/` scripts, but their 511xxxxx ids are not in `cards.cdb` and duelcore
  prefers `official/`. Using them is a separate piece of work (P).

### 27.3 Tests

- `YO2_DUEL_RULE=MR1` with the 30-duel run: **all OK** (138 duels/tests, same as MR5, with the same decision modes).
  `DuelCore.new` without `rule` reads `YO2_DUEL_RULE` (default "MR5"), so any test can run under MR1 unchanged.
- Rule tests:
  - units: the selector and `rule=` in NPC and PvP;
  - duelcore directly, Sogen×20 vs Yami×20: in MR1 the first player draws 1 on turn 1 and two Field Spells are never
    face-up at a free decision; in MR5 they are;
  - 64 translator + simulated-client duels with 2008 decks and NPC decks (fusions, Field Spells + trap monsters):
    **identical boards under both rules** and no monster in MZONE 5-6;
  - counts: MR5 = 0 draws on turn 1, 133 decisions with two Field Spells, 2 SELECT_PLACE with the extra zone free.
    MR1 = 31 draws, 0 decisions with two Field Spells, 0 SELECT_PLACE with an extra zone and 8 ignition windows.
- NPC brain (`research/ia/brain.py` test run): MR1 gives 0 problems, same as MR5. The `chain.lua:85 CHAININFO` warning
  appears under both: not rule-related.
- PvP and NPC end-to-end tests with `YO2_DUEL_RULE=MR1`: the log says "regla MR1".
- Pre-existing failure unrelated to the rule: NPC 4 (Guardian) vs NPC 60 stops with "the bot does not give a valid
  response to SELECT_SUM", under MR5 and MR1 (a known ocgcore-wasm 0.1.2 issue).

### 27.4 Implemented

- `ocg_duel.py`:
  - `DUEL_RULES` ("MR1".."MR5"), `DUEL_RULE = YO2_DUEL_RULE or "MR1"` and `duel_rule()`. An invalid value gives "MR5".
    GOAT/SPEED/RUSH are not accepted: the client cannot render them;
  - `OcgSession._start` and `PvpSession._start` pass `rule=duel_rule()` to duelcore and log it:
    `[ocg] duelo N: NPC x, empieza y, regla MR1, …`. The duel trace records it in `created`.
- `duelcore/duelcore.py`: `new(rule=None)` uses `YO2_DUEL_RULE` or "MR5". Only relevant for tests: the server always
  passes it.
- At runtime, `OCG.DUEL_RULE = 'MR5'` (or `'MR1'`) from the server console changes the rule for NEW duels; a duel
  already started keeps its own.

### 27.5 Rationale and live checks

**MR1 as is** (no extra bits) is the server default. It is what the client models (one field, no extra or pendulum
zones) and it removes the MZONE 5-6 risk. The turn-1 draw comes from the OCG rule of the period (P), not from the
executable. The errata issue is separate.

Checks under MR1:
1. The starting player draws on their turn 1 (6 cards in hand).
2. Field Spell vs Field Spell (the NPCs with Mystic Plasma Zone: 75/77/81/83/97). When the new one resolves, the old one
   goes to the graveyard and the background is the new one's.
3. After summoning a monster with an ignition effect, an "activate?" prompt may appear before the opponent responds
   (ignition priority). That is the 2008 rule (P).

## 28. OFFICIAL chain prompt: event text + question, and the official P2

Source: a set of 100 official duel logs from 2009 (not included), read with a duel-log reader. Before every DUEL_VIEW
0x32 type 3, the official server sent **the text of the event being responded to + the question**, and a P2 per
event. Previously only the question (269) was sent, with the P2 from the `chain_priority="evento"` rule (§12, D).

### 28.1 What the official server did (C: 18,933 prompts from the 100 duel logs)

Every prompt without an open chain = `0x60` P0 = 3 + `0x61` [(event, 2), (0xffff, 3), (269, 3)] + `0x1b B=0x32` P0 =
3 (P1 = stack garbage, ignored; P2 = priority). With an open link: `0x61` [(internal id, 4), (7|8, 0), (internal id,
0)]. The text is **always from the point of view of the receiver** (197 "You drew" / 198 "Your opponent drew"; bit 15
of the preceding 0x25 and 0x55 is the player relative to that view, checked). Own count:

| dlg | Text (meaning) | Prompts | P2 distribution | P2 used |
|---|---|---|---|---|
| 62 / 63 | This is the Draw / Standby Phase | 564 / 573 | all 1 | 1 |
| 64 | This is before the end of the Main Phase | 318 | 1: 306 · 2: 11 · 3: 1 | 1 |
| 67 | This is the End Phase (own turn) | 163 | all 1 | 1 |
| 251 | Your opponent's turn is about to end | 208 | all 2 | **2** |
| 272 | Your opponent's Battle Phase will end soon | 102 | all 1 | 1 |
| 182 / 183 | You declared / Your opponent declared the battle (= the attack, following 0x19/0x1a) | 101 / 209 | all 2 (183: 3 twice) | **2** |
| 177 | The Damage Step was entered | 51 | 2: 50 · 3: 1 | **2** |
| 266 | We are before damage calculation | 29 | all 2 | **2** |
| 159 / 160 | You set / Your opponent sets a Spell or Trap Card | 117 / 148 | all 1 | 1 |
| 175 / 176 | You set / Your opponent set a monster | 63 / 76 | all 1 | 1 |
| 178 | The monster's position was changed | 48 | all 1 | 1 |
| 169 / 170 | You summoned / Your opponent summoned (when ANNOUNCED) | 16 / 27 | 169: 1 · 170: 2 (26) | 1 / **2** |
| 171 / 172 | Own / opponent Flip Summon (announced) | 8 / 8 | 1 / 2 | 1 / **2** |
| 173 / 174 | Own / opponent Special Summon (announced) | 6 / 8 | 173: 1 (4) · 2 (2) · 174: 2 | 1 / **2** |
| 252 / 253 | You successfully summoned! / Your opponent… | 102 / 80 | 252: 2 (61) · 1 (41) · 253: 1 (65) · 2 (13) · 3 (2) | **2** / 1 |
| 254 / 255 | Successful Flip Summon (own / opponent) | 5 / 6 | 254: 1 (3) · 2 (2) · 255: 1 (5) · 2 (1) | 1 / 1 |
| 256 / 257 | Successful Special Summon (own / opponent) | 76 / 60 | 256: 2 (48) · 1 (28) · 257: 1 (50) · 2 (10) | **2** / 1 |
| 189 / 190 | You took damage / You damaged your opponent | 31 / 43 | all 1 | 1 |
| 196 | A monster was equipped with an Equip Card | 8 | all 1 | 1 |
| 197 / 198 | You drew / Your opponent drew card(s) | 253 / 350 | 1 (198: once 2) | 1 |
| 267 | Card(s) on the Field were destroyed | 66 | all 1 | 1 |
| 199 · 200 · 201 · 202 · 203 | returned to the hand · Deck → Graveyard · discards · sent to the Graveyard | 5 · 26 · 3 · 3 · 1 | all 1 | not mapped (→ 269 alone, 1) |
| 181 · 154 | This is the Battle Step · An attack target was selected | 20 · 2 | 2 (19 of 20) · 2 | not mapped (D: unknown TIMING) |
| 269 alone | "activate the card's effect?" | 96 | 1: 95 · 2: 1 | 1 |
| **204 / 205 + 206** | "There is no card that can be activated during this time / by a chain" + "Will you check the Field?" | 12,285 / 1,778 | **0** | §30 |
| link (id, 4), (7\|8, 0), (id, 0) | "'X' is activated. …Chain?" | ~767 | 2: 627 · 1: 140 | 2 if set by the opponent, 1 if own (seen in context) |

P2 varies WITHIN some events (252, 253, 256, 257, 173): it is not explained by the preceding commands in the log (the
last 14 were examined) and each player's cards are unknown (D). The majority value is used. A consequence worth
knowing (C from the distribution): an own Trap Hole in response to an opponent's summon (253) gets P2 1 in most
official cases, so with "automatic/semi" the client cancels it by itself; in "Manual" it always shows.

### 28.2 List A format: why ONE card does not get event + name (C, disassembly of 0x744670 = `FUN_00744670`)

Each pair (value, class) goes through jump table 0x74495c. `bl` = "text already present", `[esp+0x12]` = "a name is in
the buffer" (set by class 4), `[esp+0x194]` = accumulated text:
- **class 2** in the 1st pair when there is more than one: the value's dlg text goes to the name buffer (flag set) and
  to the output; **class 3**: `strcat` of the value's dlg text, or "\n" if the value is 0xffff; **class 4**: the card
  name goes to the name buffer.
- **class 0 (`0x744743`)**: with `bl = 0` it formats the VALUE's dlg with (the name if present; otherwise the value of
  the next pair); **with `bl = 1` it uses the ACCUMULATED TEXT as the template and ignores its dlg**. It always does
  `inc esi` at 0x74479e: **it skips the next pair**. That is why the official server sends (id, 0) as the 3rd pair of
  7/8: nothing executes it; it is padding (with class 4 before it, its value is not even read).
- Consequence: [(event, 2), (0xffff, 3), (id, 4), (250, 0)] (the form considered for ONE card) would leave only
  "event\n" (class 0 with text already started ignores the 250). The form that does format, [(event, 2), (0xffff, 3),
  (250, 3), (id, 4), (X, 0)], has 5 pairs and a 0x61 takes 4 (the executor fills the list with a single command; a
  2nd one does not append). **With an event, the official form (event + 269) wins, with the card's thumbnail** (0x6610,
  once); without an event, the name form of §24 remains. `YO2_CHAIN_EVENT_TEXT=0` restores the name in all cases.

### 28.3 Implemented (`translator.py`; `chain_event_text` and `chain_priority="oficial"` on by default)

- `_chain_event(ev)` (new) → (dlg or None, P2). Source = the `TIMING_*` of ocgcore's SELECT_CHAIN: `hint_timing`
  (caused by the chooser) / `hint_timing_other` (caused by the opponent); table `CHAIN_EVENTS` by priority (attack >
  calculation > Damage Step > set > position > draw > destroy > equip > End Phase > end of Battle Phase > Main >
  Standby > Draw) and `CHAIN_SUMMONS` for summons: ocgcore sends one prompt when the summon is ANNOUNCED (Solemn
  Judgment) and another when it COMPLETES ("successfully"); they are told apart by the last message (`ev_last`:
  SUMMONING vs SUMMONED; the summoner = the controller of the *ING message, because the *ED one carries no card).
  Damage (`CHAIN_DAMAGE`) looks at who took the last DAMAGE. Without TIMING_* (tests), the last thing tracked by
  `_track_trigger`. If nothing matches, (None, 1): 269 alone, as the official server did.
- `_track_trigger` (extended) records `ev_last = (type, player, phase)` and `dmg_player`; they are overwritten by
  NEW_TURN/NEW_PHASE/CHAINING/CHAIN_END/ATTACK/DRAW/SET/DAMAGE_STEP_START/BATTLE/DAMAGE and any other summon.
- `_s_SELECT_CHAIN`: no open chain and an event → list A [(event, 2), (0xffff, 3), (269, 3)]; with an open chain, the
  7/8 carries the 3rd pair (id, 0) (harmless, 28.2). With `chain_event_text` off, everything as in §26.
- `_chain_priority` mode `"oficial"`: with an open chain, 2 if the last link is the opponent's and 1 if own (plus (e)
  from §21); without one, the event's P2. **Rule of ours on top (D)**: if some option is an optional trigger
  (TRIGGER_O) of a monster in the hand (Gorz after damage, 189 → P2 1), P2 = 2: with 1 the client on "automatic"
  cancelled it by itself and it was lost. `YO2_CHAIN_PRIORITY_HAND=0` removes this and leaves the pure table.
- An admin tool (not included) can switch `duelo.chain_priority` (including "oficial") and `duelo.chain_event_text`.
- New constants: `CHAIN_EVENTS`, `CHAIN_SUMMONS`, `CHAIN_DAMAGE`, `CHAIN_SEP`. Class attributes: `ev_last`,
  `dmg_player`, `_EV_KEEP`, `_EV_DROP` (a duel started before a hot reload inherits them).

Final table event → ocgcore TIMING (P: the TIMING ↔ dlg pairing is ours; texts and P2 values are C):

| TIMING (bit) | own / opponent (dlg) | P2 own / opponent |
|---|---|---|
| ATTACK 0x1000 | 182 / 183 | 2 / 2 |
| DAMAGE_CAL 0x4000 · DAMAGE_STEP 0x2000 | 266 · 177 | 2 · 2 |
| MSET 0x200 · SSET 0x400 | 175 / 176 · 159 / 160 | 1 |
| POS_CHANGE 0x800 | 178 | 1 |
| DRAW 0x10000 | 197 / 198 | 1 |
| DAMAGE 0x20000 (who took it) | 189 / 190 | 1 |
| DESTROY 0x80000 · EQUIP 0x2000000 | 267 · 196 | 1 |
| END_PHASE 0x20 | 67 / 251 | 1 / **2** |
| BATTLE_END 0x10 | (no text) / 272 | 1 |
| MAIN_END 0x4 · STANDBY 0x2 · DRAW_PHASE 0x1 | 64 · 63 · 62 | 1 |
| SUMMONING / SUMMONED (0x40) | 169 / 170 · 252 / 253 | 1 / **2** · **2** / 1 |
| SPSUMMONING / SPSUMMONED (0x80) | 173 / 174 · 256 / 257 | 1 / **2** · **2** / 1 |
| FLIPSUMMONING / FLIPSUMMONED (0x100) | 171 / 172 · 254 / 255 | 1 / **2** · 1 / 1 |
| anything else (BATTLE_START 0x8, BATTLE_PHASE 0x1000000, BATTLE_STEP_END 0x4000000, CHAIN_END 0x8000, TOHAND, TOGRAVE…) | 269 alone | 1 |

Coverage on real traces (120 duels, 130 optional player-0 prompts without an open chain): 114 with an event (62 Draw
18 · 63 Standby 19 · 198 16 · 64 9 · 197 8 · 183 7 · 177 5 · 67 5 · 251 4 · 253/176/252 3 each…) and 16 without one
(BATTLE_START 6, BATTLE_PHASE after an attack 4, end of chain 2, end of own Battle Phase 3, BATTLE_STEP_END 1).

### 28.4 Tests

- 30-duel run passes. New unit tests: 39 events (each row = TIMING + preceding messages → dlg and P2 written by hand),
  5 cases without TIMING_* (attack, Damage Step, draw, Standby, summon), an old event that does not stick, ONE card
  (with an event: official form and 1 thumbnail; without: name; flag off: name), open chain (7/8 with 3 pairs, P2 2
  opponent / 1 own; flag off: 2 pairs), flags ("evento" gives 2 to Trap Hole after an opponent's summon and "oficial"
  1; duel without the key; `DEFAULT_CFG` switched off immediately; `YO2_CHAIN_EVENT_TEXT=0` in a separate process),
  and 8 real traces with the simulated client.
- `chain_text_errors` (in every duelcore duel) checks each prompt: list A and P2 against a hand-copied version of the
  28.1 table, independent from the translator's.

### 28.5 Runtime switches and live checks

- Switch off immediately: `OCG.TR.DEFAULT_CFG['chain_event_text'] = False` (no event text; also the 2-pair 7/8) and
  `OCG.TR.DEFAULT_CFG['chain_priority'] = 'evento'` (new duels). Duels IN PROGRESS keep their cfg.

Checks: with "Semi-automatic cancel" only these prompts should appear: attacks (yours and the opponent's), Damage Step /
before calculation, end of the opponent's turn, your successful summon (and Special Summon), the ANNOUNCEMENT of an
OPPONENT's summon (Solemn Judgment) and the opponent's chain links; the rest (phases, draws, sets, damage…) with P2 1
is cancelled automatically; in "Manual" all appear with their text. If a text does not match what happened (e.g. 252
after an opponent's summon), that would be a mismatched TIMING.

### 28.6 Open items (P/D)

- 204 / 205 + 206 with P2 = 0: see §30.
- 181 "Battle Step" (P2 2), 154, 199-203: no reliable TIMING; they stay at 269 alone.
- The majority P2 does not explain the variation within 252/253/256/257/173 (D). If there are too many/few prompts on
  those summons, `CHAIN_SUMMONS` is the first place to adjust.
- Text of the BATTLE_START (0x8) and BATTLE_PHASE (0x1000000) windows: no clear official trace; currently 269 alone.
- The 7/8 prompt thumbnail is still D (§26.2).

## 29. Thumbnail and client queue: crashes with the YES/NO prompt after a fusion

Marks: C = read in code/logs, P = probable, D = doubtful.

### 29.1 Cause

- **C** The client applies `0x6610` (`FUN_006929b0`) **on arrival**; the `0x6202` commands go to its **queue** and
  execute with their animations (it ran 12-25 s behind). The `0x4b`/`0x4c` of a Fusion Summon do `DAT_012ccb34[P1] =
  card word(uid)` with the **internal** id.
- **C** The `CUYesNoDialog` with mode `0xff` draws `ccb34[0..n-1]` as YO2 ids from `mini/list_card.txt` (3256 rows). An
  internal id (e.g. 6527) falls outside the table: garbage name (`CTextureManager.cpp:L784`) or a NULL pointer (access
  violation at `0x7d88b9`), and the sprite is written outside the array (heap corruption, `0x63823b` later). Two client
  crashes were observed in duels against fusion-deck NPCs.
- **C** Mode `0xff` is **not cleared** when answering: a later YES/NO without its own `0x6610` (no card, `option_yn`)
  would draw whatever is in `ccb34`.
- **D** How the official server synchronised this (official replays only store `0x6202`).

### 29.2 Fix (ours; D regarding the official behaviour)

- `YO2Duel.list_dirty` (class attribute, False): `_fusion_material` sets it; `on_client` clears it when it answers a
  pending decision (the request was queued after the `0x4b`/`0x4c`, so they have already executed). **C** for the
  logic, **P** for the real client behaving the same (not tested live).
- `_dialog_cards`: with no cards or with `list_dirty`, it sends `0x6610` mode 0, n = 0 (`00 | 00000000 | 0 | 0 | 0`),
  which draws nothing and does not inherit a previous `0xff`. With `yesno_card` off it still returns `[]`. `_yesno`
  always calls `_dialog_cards`, and `option_yn` (`_s_SELECT_OPTION`) prepends `_dialog_cards([])`.
- Cost: the first prompt after a fusion has no thumbnail (it has its text).
- Tests: unit tests + simulated client on the two crash traces: with the previous code 1 error per trace, with the new
  one 0.

### 29.3 Operational notes

- `yesno_card` and `chain_event_text` are on by default (the crashes were caused by the 0x6610 / 0x4b-0x4c race, not by
  the text; fixed with `list_dirty`).
- **Reload between duels**: `list_dirty` is a class attribute, so a duel in progress starts with False; if a fusion
  happened right before a hot reload, its first prompt is still at risk. With duels in progress, set `list_dirty =
  True` on each from the reload script.
- Live check: an NPC with a fusion deck and a set trap until it Fusion Summons and attacks; the prompt appears without
  a thumbnail and does not crash, the next one (after answering) with a thumbnail. The log must not show `[cli]
  exception ... 0x7d88b9` or `テクスチャ[data/mini/`.

## 30. "There is no card that can be activated…" + "Will you check the Field?" in empty chain windows

The item left open in 28.6. Sources: the 100 official duel logs, a gameplay video of the original (204 + 206 in the
opponent's Standby Phase) and `duel_view_dispatch` (0x73dd80) in the decompiled client. Marks: C = confirmed,
P = probable, D = deduced or unknown.

### 30.1 What the official server did

| Fact | Detail | Mark |
|---|---|---|
| Texts | dlg 204 "There is no card that can be activated during this time."; 205 "…by a chain."; 206 "Will you check the Field?" (`dlg_text_s/e`) | C |
| Format | All **14,063** prompts (12,285 of 204 and 1,778 of 205) are identical: `0x60` P0 = 3 + `0x61` [(204\|205, 2), (0xffff, 3), (206, 3)] + `0x1b B=0x32` P0 = 3, **P2 = 0**. No event text or card name | C |
| 204 or 205 | 205 = with an open chain: 1,740 of 1,778 come right after a `0x12` (chain link), and BOTH players receive it, including the activator. 204 = no open chain | C |
| Who receives it | Each window sends one prompt to EACH player; almost always in pairs (turn player first, then the opponent; in the Draw Phase, two rounds). The player with something activatable gets the event prompt (§28) and the other gets 204. Example: after a summon, the summoner gets 204 and the opponent gets 253 with P2 2 | C |
| In which windows | In **every** chain window where the player has nothing activatable. There are 11.8 prompts per turn and side (8.4 of them 204/205; 877 turns of side 0). ocgcore opens 11.4 empty windows per turn for player 0 (8 duels, 45 turns, `auto_chain=False`). ocgcore's windows are used, which do not map one-to-one to the official ones: the official server sends two rounds in the Draw Phase and one in the Standby Phase, and ocgcore produced none in the Standby Phase. Also, the share of 205 is 12.6 % officially and 3.7 % in ocgcore | P (the rule) / D (each window) |
| What the client does with P2 0 | In case 3 of 0x32, if (skip key held and P2 < 3) or (P2 < 2 and the option is not "Manual", `DAT_012bb6b0+0x437 != 2`), it calls `duel_dialog_answer_send(0)` without showing anything. With P2 0 it is **only visible in "Manual"**, so only players using manual see it. In "automatic" and "semi" the client answers NO by itself | C |
| Answer to YES and NO | `CUYesNoDialog`: YES = `0x6423 1`, NO = `0x6423 0`. The client gives 206 no special treatment | C |
| What the server did on YES | After a 204/205 the official server **never** sends a `0x1a` request to the same player: 0 of 14,063 cases. After an event prompt it does: more than 990 `0x1a` (classes 4/5/7/8/9), i.e. the YES followed by the masks. "Check the Field" opened no selection. Whether anyone pressed YES in these logs, or whether the server did anything else, is unknown | C (no 0x1a) / D (the rest) |
| Did it wait for the answer? | The duel log stores neither client messages nor timings. The client always answers a type 3, even when it answers by itself (C). Waiting for that `0x6423` is the only way not to confuse it with the next decision | P |

### 30.2 Implemented (`chain_none_prompt`, env `YO2_CHAIN_NONE_PROMPT`; **OFF** by default)

- **Why off.** Format and P2 are C, but showing it in every window is P, and what the official server did on YES is D.
  It also changes the pace: the server waits for the client's answer about 11 times per turn, and the client only
  sends it when its queue reaches the prompt; the queue ran 12-25 s behind (§29). Not tested with the real client. To
  enable: `YO2_CHAIN_NONE_PROMPT=1`, or `OCG.TR.DEFAULT_CFG['chain_none_prompt'] = True` for new duels.
- **duelcore.** Empty windows did not reach the translator: `duelcore.mts` passes them automatically with `auto_chain`
  (`autoPassed`). `ocg_duel.py` passes `auto_chain = not cfg['chain_none_prompt']` in `OcgSession._start` and
  `PvpSession._start`, so it only affects new duels. In `OcgSession._pump`, the NPC's empty windows are answered "no
  chain" without going through the brain or counting toward `MAX_NPC_DECISIONS`.
- **`translator.py`:**
  - Constants `CHAIN_NONE = (204, 205, 206)` and `CHAIN_NONE_P2 = 0`.
  - `_s_SELECT_CHAIN`: with empty `selects`, a non-mandatory window, `chain_dialog == 3` and the key on, it calls
    `_chain_none()`. Otherwise, as before (`auto`).
  - `_chain_none()` (new) sends `_dialog_cards([])` (with `yesno_card`, a mode 0 0x6610 with n = 0, §29; the official
    thumbnail is D) and list A + 0x32 type 3 with P2 0. It sets mode `chain_none`.
  - `on_client`, in mode `chain_none`: on `0x6423`, whether 1 or 0, it answers ocgcore `SELECT_CHAIN index None` ("no
    chain"). Any other message is ignored, because no play permission was sent.
  - `auto_response`: an empty non-mandatory window is answered "no chain" without asking bot.ts. This happens with the
    key off and `auto_chain=False`, or with `chain_dialog` other than 3.
- A duel started before a hot reload, without the key, uses `DEFAULT_CFG`. But if its duelcore was created with
  `auto_chain=True`, it never receives empty windows.
- Cost: each empty window adds a `query` + `sync_board` / `sync_stats` and a round trip with the client.

### 30.3 Tests

- Unit tests:
  1. 204 without an open chain: list A, 0x32 (3, 0, 0), 0x6610 with n = 0 and mode `chain_none`.
  2. Both NO and YES return `index None`.
  3. A click or a 0x6351 does not answer.
  4. With an open chain (an opponent link and then an own one), 205 is sent.
  5. With an activatable card, the usual prompt is sent.
  6. Not sent with the key off (and `auto_response` = no chain), with `chain_dialog` 2, "pass" or 0, in a mandatory
     window or for player 1.
  7. A duel without the key uses `DEFAULT_CFG`. `YO2_CHAIN_NONE_PROMPT` unset or 0 = False; 1 = True.
- Live duelcore test (`auto_chain=False`, the simulated client answers YES or NO at random): in 4 duels there were 460
  empty windows, 443 with 204 and 17 with 205. None with cards got the 204. No differences with ocgcore.
  `chain_text_errors` checks list A and P2 0 in every empty window.

### 30.4 Live checks (with the key on)

- In "Manual", 204 + 206 should appear at almost every step of the opponent's turn (phases, draws, summons…) and 205
  when a chain is open.
- In "semi" or "automatic" nothing new should be visible. If the duel gets slower or stalls waiting, that is the round
  trip: disable the key and start another duel.

## 31. MSG_LPUPDATE → direct 0x26 (`lp_set_direct`)

Basis: in EDOPro, LPUPDATE = set an absolute value. ocgcore only emits it in `Duel.SetLP` and when a player is reset.
`_m_LPUPDATE` used to send 0x26 if the value differed from the mirror, but did not look at the LP display: with a
0x24/0x25/0x17 for the same player in the batch, the 0x26 was overwritten by what remained to count (§24.2) and
`lp_reconcile` fixed it with a "[lp] desfase" warning.

- Flag `lp_set_direct` (on; `YO2_LP_SET_DIRECT=0` disables it = previous behaviour). Registered in `yo2srv/config.py`
  (`duelo.lp_set_direct`).
- `_lp_set_direct(p, lp)`: LP >= 0 in the mirror; if the display already shows the value (and nothing is held) nothing
  is sent; otherwise 0x26 for that player (view bit); if the display holds a −N or that player had a 0x24/0x25/0x17 in
  the batch (`lp_anim`, emptied at the start of the batch, at the select and with 0x27), 0x27 + 0x26 for both, WITHOUT
  a desync warning. `_hud_track` records the outgoing 0x26/0x27, so `lp_reconcile` (safety net, still on) sends nothing
  afterwards. `lp_resync` stays off.
- Tests: unit tests (standalone, same value, after own/other/0x17 damage, limits 0/0xffff, flag off, no key, hud None)
  and a live duelcore test (Yu-Jo Friendship, `Duel.SetLP` to the average for both: every LPUPDATE that changes
  something carries its 0x26 and display = engine, with and without `lp_reconcile`).

## 32. ATK/DEF that expires without a message (Shrink, "until the end of the turn") in the non-deciding view

Seen live (PvP): Mechanicalchaser (1850/800) of player 0 + Shrink on their turn → 925. During the opponent's turn
player 0's client still showed the modified ATK; a screenshot on the next turn showed "1834 / 800" with an arrow.
ocgcore had undone it correctly (BATTLE two turns later with 1850).

### 32.1 Cause (C, from the server log)

- The `0x36` EFFECT_SET_STATS pair 925/800 went to both views when the chain closed (`_m_CHAIN_END` → `_live_stats`).
  Correct.
- Shrink expires at ocgcore's phase reset (RESET_PHASE | PHASE_END): **without a message**. The translator only
  re-queried ATK/DEF (§19.2b) before a decision of THAT view (`_on_select` → `sync_board`), when a chain closed and
  before an attack.
- At the turn change (TO_EP → opponent's turn), the view of player 1 (the deciding one) received the `0x37` (P2 = 1,
  delete the pair) before its request. Player 0's view receives the batch **without** the select
  (`PvpSession._pump`: `feed_many(evs[:-1])`), so nothing.
- The `0x37` finally reached view 0 at the `CHAIN_END` of an opponent's chain, 32 s late. Had the opponent not chained
  anything, the 925 would have stayed until player 0's next decision or an attack. The same happens in an NPC duel
  while the NPC is thinking (its selects do not go through the translator either).
- The translator sends **absolute values** (the pair sets ATK and DEF, `FUN_006fae40` case 0x1c5f); there are no
  deltas. The client does not expire the pair: it is type 3 ("until it leaves the field or is turned face-down").

### 32.2 Why 1834 (C by code; the screenshot itself, P)

- `FUN_00759430` (paints the figure every frame) does not jump to the new value: `+0x4c = target + trunc((current −
  target)·7/8)`. From 925 to 1850 the sequence is 925, 1041, 1143, … 1824, 1828, 1831, **1834**, 1836, … 1850 (42
  frames). **1834 is frame 29** of that count: the client already had the `0x37` and was counting up to 1850.
- The arrow is the rising sprite (10/11 of `DAT_012a682c`): it is only drawn while `current ≠ target`. And since 1834
  < 1850 (printed), the figure uses font 3 ("lower than printed").
- Why the screenshot caught it mid-count (or whether it stayed there): **P**. With both clients on the same machine,
  the window not in front may repaint irregularly.
- The client can do Shrink by itself: `FUN_006fae40` knows the card (internal id 0x14eb) → ATK = (printed + 1) >> 1
  for type 3/4 effect entries with that id. How the official server made them expire: **D**. Not used here; the
  ocgcore pair is authoritative.

### 32.3 Fix (`translator.py`, flag `stat_sync_turn`, on; `YO2_STAT_SYNC_TURN=0` disables it)

- `_stat_boundary()` = `_live_stats()` (duelcore query + `_stat_cmds`, which only sends something if the value changed
  relative to `stat_sent`) at:
  - `NEW_PHASE` (whatever expired at the end of the previous phase: battle, damage step…) and `NEW_TURN` (before the
    turn-change 0x0c), if there is already a turn player;
  - the end of a batch that does **not** end in a decision of this view (`feed_many`): the waiting view in PvP and the
    batches before each NPC decision. This also covers equips/continuous cards leaving or entering without a chain.
    With the view's own decision it is not repeated (`sync_board` already does it).
- General: applies to any temporary modifier ocgcore undoes without a message (Shrink, Rush Recklessly, Fairy Box…),
  to the bonus of an equip that leaves, and to that of a continuous card entering or leaving.
- The query is taken at the end of the batch (as already done at `CHAIN_END`): in the middle of a batch it may
  anticipate a value; `_stat_cmds` skips slots whose code no longer matches.
- Registered in `yo2srv/config.py` (`duelo.stat_sync_turn`); `yo2srv/BANDERAS.md` regenerated.

### 32.4 Tests

- Unit tests: pair with ATK halved when the chain closes; batch EP → NEW_TURN → DP without an own decision → **one**
  `0x37`, before the `0x0c`; batch ending in the other player's decision → `0x37`; with the own decision, only once;
  flag off → no `0x37` (previous behaviour).
- PvP replay test: replays **the real trace** in duelcore (same seed, same 245 responses) with both views as
  `PvpSession` and checks at every decision that what each simulated client would paint = ocgcore. Flag off: 5 desyncs
  ("j0 m0: client (925, 800), ocgcore (1850, 800)", view 0) → **fails**; flag on: 0 desyncs and the `0x37` reaches
  view 0 in the batch of the turn change → **passes**.
- 30-duel run passes.

**Hot reload:** only `translator.py` changes (plus the flag registry, which the admin tool sees after a restart).
`hot('duelo.traductor')` from the server console (pulls in `duelo.ocg`): duels in progress keep the old translator;
the next one uses the new one.

## Appendix: translator flags discussed in these notes (current defaults in `DEFAULT_CFG`)

Environment variables override the default where listed (`=0` disables, `=1` enables). A duel uses the cfg it was
created with; keys missing from an older duel's cfg fall back to `DEFAULT_CFG`.

| Flag | Default | Env | Section |
|---|---|---|---|
| `kind_main` / `kind_battle` / `kind_select` | 3 | — | §6 |
| `select_bit` / `target_bit` | `CMD_GENERIC` / `CMD_ATTACK` | — | §6 |
| `yesno` | "dialog" | — | §6 |
| `hide_npc` | True | — | §5 |
| `token_serial_base` | None | — | §4 |
| `finish_mode` | 0 | — | §4, §8 |
| `npc_phrases` | False | — | §8 |
| `stat_sync` | True | — | §13, §19 |
| `stat_sync_turn` | on | `YO2_STAT_SYNC_TURN` | §32 |
| `summon_done` | on | `YO2_SUMMON_DONE` | §19 |
| `summon_kind_fx` | on | `YO2_SUMMON_KIND_FX` | §23 |
| `token_vanish` | on | `YO2_TOKEN_VANISH` | §23 |
| `yesno_card` | on | `YO2_YESNO_CARD` | §23, §29 |
| `yesno_sys_text` | on | `YO2_YESNO_SYS_TEXT` | §25 |
| `battle_damage_real` | on | `YO2_BATTLE_DAMAGE_REAL` | §23 |
| `lp_resync` | off | `YO2_LP_RESYNC` | §23, §24 |
| `lp_reconcile` | on | `YO2_LP_RECONCILE` | §24 |
| `lp_set_direct` | on | `YO2_LP_SET_DIRECT` | §31 |
| `chain_one_name` | off (on in the "enhanced" profile) | `YO2_CHAIN_ONE_NAME` | §24 |
| `chain_link_detail` | on | `YO2_CHAIN_LINK_DETAIL` | §26 |
| `chain_dialog` | 3 | — | §12 |
| `chain_importance_auto` | True | — | §12 |
| `chain_card_name` | False | — | §23, §24 |
| `chain_priority` | "oficial" | — | §12, §28 |
| `chain_event_text` | on | `YO2_CHAIN_EVENT_TEXT` | §28 |
| `chain_none_prompt` | off | `YO2_CHAIN_NONE_PROMPT` | §30 |
| `chain_priority_hand` | on | `YO2_CHAIN_PRIORITY_HAND` | §21, §28 |
| `chain_link_text` | True | — | §12 |
| `activate_flash` | False | — | §26 |
| `card_link` | True | — | §13 |
| `link_kind_equip` / `link_kind_target` | 10 / 9 | — | §13 |
| `link_target_anim` | True | — | §13 |
| `target_lockon` | True | — | §13 |
| `counters`, `swap_cmd`, `field_move_3d`, `toss_anim` | True | — | §14 |
| `coin_call` | 1 | — | §14 |
| `tribute_anim` / `tribute_mark` | True / False | — | §15 |
| `fusion_anim` | on | `YO2_FUSION_ANIM` | §15 |
| `turn_timer` | 180 | `YO2_TURN_TIMER` | §15 |
| `turn_limit` | on | `YO2_LIMITE_TIEMPO` | §15 |
| `retry_auto` | 2 | — | §17 |
| `field_change` | on | `YO2_FIELD_CHANGE` | §17 |
| `position_dialog`, `announce_dialogs` | True | — | §18 |
| `option_dialog` | on | `YO2_OPTION_DIALOG` | §18 |
| `sort_list` | on | `YO2_SORT_LIST` | §18 |
| `counter_dialog` | on | `YO2_COUNTER_DIALOG` | §18 |
| `hand_limit_dialog` | on | `YO2_HAND_LIMIT_DIALOG` | §20 |
| `turn_counter` | on | `YO2_TURN_COUNTER` | §22 |
| `declare_hint` | on | `YO2_DECLARE_HINT` | §22 |

Server-level (not translator) setting: duel rule `DUEL_RULE` in `ocg_duel.py`, default "MR1" (`YO2_DUEL_RULE`), §27.
