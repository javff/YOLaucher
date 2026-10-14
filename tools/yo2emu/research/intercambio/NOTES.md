# Card trading between players (0x7000 envelope) — protocol and server

Static analysis (decompilation and disassembly of the exe) plus offline tests.

Confidence: **C** = confirmed in code · **P** = probable (deduced from C code) · **D** = doubtful (the client does not
say).

| File | What it is |
|---|---|
| `proposal.py` | `HANDLERS = {0x7000: h_trade}`, `install(ns)`, `on_close(c)`, `handle()`. Flags at the top |

## 1. Client pieces

- **Envelope**: every send is `0x7000` + **u16 subcommand** + fields (big-endian). Senders `FUN_006cde40..006ce450`. C
- **CNetLobby** (`+0x7c` of the task; scene `+0x154`): wait flags `+0x4e..+0x63`; trade block `T = +0x1bb4`
  (`FUN_0086b6e0`): `T+4` = `+0x1bb8` partner's pid, `T+8` = `+0x1bbc` name16, `+0x1bce/+0x1bd0` MOVEOWNCARD,
  `+0x1bd1` my status (0x7117), `+0x1bd2/+0x1bd4` MOVEOTHERCARD, `+0x1bd5` the other's status (0x7134), `+0x1bd6`
  result 0x7120, `+0x1bd7` TRADEEND, `+0x1bd8` "offer pending", `+0x1bda` "offer withdrawn". C
- **Lobby** `FUN_00872d00` / `FUN_00876970` (sub-states `+0x9c`): 0x24 request, 0x25/0x26 wait, 0x28 offer notice,
  0x29/0x2a answer, 0x2e enter the scene, 0x30→0x31 TRADECLEAR and cleanup. C
- **Trade scene** `FUN_008fac30` (state `+0x158`), panel `DAT_012b47b0` (`+0x388` the other's cards, count `+0x410`;
  `+0x398` mine, count `+0x414`), UI `FUN_009019a0`. C
- **Client rule for offering a card** (`FUN_00970xxx`, mode 1): `in_my_offer(id) < trunk[id] − limit[id]` **and**
  `+0x414 < 10`. C
- Texts (Spanish client, translated): 5140 "Requesting Trade", 5141 "You have a Trade Request!", 5142 "Start
  Trading?", 5143 "The Trade Request was declined.", 5147 "Trade Completed", 5148 "Trade Cancelled", 5149 "Your Request
  could not be sent…", 5158 "The opponent has cancelled the transaction.", 5159 "Ready to Trade.". C (ids); where 5149
  appears: P.

## 2. What the client sends (0x7000 + subcommand)

| Sub | Name | Fields after the u16 | Who sends it | Wait | Conf. |
|---|---|---|---|---|---|
| 0x7102 | REQTRADE | u32 target pid | task 0x29 (`FUN_006d4330` ← lobby 0x24, only if `player+0x1214 & 0x40 == 0`) | +0x50 | C |
| 0x7105 | TRADEOFFERREPLY | s32: **1 = yes** (button 0xfa0), **0 = no** (0xfa1 or another busy scene) | task 0x2b | +0x52 | C |
| 0x7108 | CANCELREQTRADE | — | `FUN_006d0350`: **no callers in the exe** (dead code) | +0x54 | C |
| 0x7132 | TRADEREADY | — | scene state 1 (does not wait on +0x4e) | — | C |
| 0x7101 | TRADESTART | — | scene state 4 after the limit | +0x4f | C |
| 0x7113 | ADDCARD | u16 **card id** | UI 0x902f6a (drag; draws it immediately) | +0x56/+0x57 | C |
| 0x7114 | DELETECARD | u16 **index** in my list | UI 0x90304c (`FUN_00993680` removes by position) | +0x56/+0x57 | C |
| 0x7122 | ARRIVETRADECARD | — | scene 9/10/0xb after applying a 0x7116 | — | C |
| 0x7117 | SETTRADESTATUS | u8, **always 0** (only sender 0x99278a, "Ready" button) | → scene 10 | +0x5a | C |
| 0x7119 | TRADELASTCONFIRM | u8 + 10 s32 **my** ids + 10 s32 **their** ids (−1 = empty) | UI: 1 = yes, 2 = no/close; scene 0xc: **0 with all −1 after each 0x7120** | +0x5c | C |
| 0x7112 | TRADEUSEREXIT | — | 0x8f7019 (close; except states 0xd-0x10); no wait | — | C |
| 0x7128 | TRADECLEAR | — | lobby 0x30 (`FUN_00879570`, also `FUN_008603d0`); no wait | — | C |

Task states 0x2f-0x40 (`FUN_006d43d0`… per-task ADDCARD/DELETECARD/STATUS/LASTCONFIRM) **have no callers**: the scene
calls the senders directly and does not look at the results except as noted below. C

## 3. What the client expects (0x71xx)

| Id | Name | Format | Handler | Effect | Conf. |
|---|---|---|---|---|---|
| 0x7103 | REQTRADERESULT | s32 | `FUN_006bf4d0` (k=0x34, +0x50) | **−493 = error**, other = sent → "Requesting…" | C |
| 0x7104 | TRADEOFFER | u32 pid, name16 | `FUN_006bf510` (+0x51) | stores pid in +0x1bb8, +0x1bd8=1 → "Start…?" notice | C |
| 0x7105 | TRADEOFFERREPLY | s32 | `FUN_006bf5f0` (k=0x36, +0x52) | to the requester: **0 = accepted** (lobby 0x2e), ≠0 = 5143; to the answerer: 0 = ok, other = error 0x4a except −618 on a "no" (silent) | C |
| 0x7109 | CANCELREQTRADENOTICE | — | `FUN_006bf650` (+0x54) | closes the offer notice (lobby 0x28) | C |
| 0x7110 / 0x7111 / 0x7107 | CANCELREQTRADERESULT / TRADEOFFERTIMEOUT / CARDLIST | — | empty | nothing | C |
| 0x7112 | TRADEUSEREXIT | u32 pid | `FUN_006bf910` | **only if pid == +0x1bb8** → +0x5e=2 → "The opponent has cancelled…" and closes | C |
| 0x7113 / 0x7114 | ADDCARD / DELETECARD | s32 | k=0x3a/0x3b | **−1010 → closes the scene** (state 0x10); other values ignored | C |
| 0x7116 | MOVEOTHERCARD | u16, u8 | `FUN_006bf750` (+0x59) | u8 0 → adds the id to "the other's cards" (`FUN_00993090`); ≠0 → removes by index (`FUN_00993550`); and sends 0x7122 | C (args P) |
| 0x7115 | MOVEOWNCARD | u16, u8 | `FUN_006bf6c0` (+0x58) | the scene does not look at it | C |
| 0x7117 | SETTRADESTATUS | u8 | `FUN_006bf7e0` (+0x5a) | scene 10: 0 → 9 (edit), **1 → 0xb (ready)**, other → +0x15c (0xb) | C |
| 0x7134 | CHANGETRADESTATUS | u8 | `FUN_006bfc10` (+0x63) | +0x1bd5; with 0/1 redraws the other's "ready" (`FUN_009943d0`) | C |
| 0x7118 | TRADEALLREADY | — | (+0x5b) | state 0xb → final dialogue, state 0xc | C |
| 0x7120 | TRADELASTCONFIRMRESULT | u8 | `FUN_006bf850` (+0x5c) | **3 → 0xd**, **2 → 9** (edit), 1 → 0xb, other → continues | C |
| 0x7121 | TRADEEND | u8 | `FUN_006bf8a0` (+0x5d) | ≠0 "Trade Completed" → on close, state 0xf; 0 "Trade Cancelled" → 0x10 | C |
| 0x7122 | ARRIVETRADECARD | — | `FUN_006bfb00` (+0x60) | the scene clears +0x56/+0x57 | C |
| 0x7125/0x7126×7/0x7127 | TRADETRUNK* | 6×512 + 184 B | dispatcher + `FUN_006bfa00/6bfa80` (+0x5f) | **overwrites the player's trunk** (+0x18ed, the same as 0x3103) | C |
| 0x7129/0x7130×7/0x7131 | TRADELIMIT* | 6×512 + 184 B | dispatcher (0x7130 = `default`) + `FUN_006bfb20/6bfba0` (+0x62) | player +0x25a5: copies per card that may **not** be offered | C |
| 0x7133 | TRADESTARTRESULT | — | `FUN_006bfc60` (+0x4f) | state 2 → 9 | C |

## 4. Complete flow (what `proposal.py` does)

1. **A requests** (0x7102 pidB). Rejected with −493 if: no session, B not connected, oneself, either is already in a
   trade or offer, B in a duel (state 0x67-0x6a). Otherwise: A ← 0x7103 0; B ← 0x7104 (pidA, nameA).
2. **B answers**: B ← 0x7105 0. Yes → A ← 0x7105 0 (both open the scene). No → A ← 0x7105 `DECLINE_VALUE`.
3. **Scene** (each player): 0x7132 → trunk (0x7125 + 7×0x7126 + 0x7127, from the profile via `PP.trunk_bytes`) +
   limit (0x7129 + 7×0x7130 + 0x7131); 0x7101 → 0x7133.
4. **Cards**: ADDCARD/DELETECARD → 0x7113/0x7114 s32 0 to the mover; to the other player 0x7116 (id,0)/(index,1)
   **queued**: only once that player has sent TRADESTART, and one at a time, waiting for its 0x7122, which is
   forwarded to the mover (P: the client keeps a single pending move in +0x1bd2 and acknowledges it with 0x7122;
   without a queue, moves would be lost while the other player is loading the trunk).
5. **Ready**: 0x7117 → 0x7117 u8 1; to the other 0x7134 u8 1; both ready → 0x7118 to both.
6. **Confirmation**: 0x7119 u8 0 is ignored (P). With a 2 → 0x7120 u8 2 to both and "ready" reset. With both 1 → the
   cards are exchanged (under STATE_LOCK, checking trunk − limit; each account's profile with `CTX.sess`) → 0x7120 u8 3
   + 0x7121 u8 1 + the new trunk to each. If a card is missing: 0x7121 u8 0 and nothing changes. The lists of 0x7119
   are compared with the server's (log only; the server's lists win).
7. **Exit**: 0x7112 / 0x7128 / connection closed → the other player gets 0x7112 with **the pid they have in
   +0x1bb8**: the one who received the offer has the requester's pid; **the requester has 0** (only 0x7104 writes
   +0x1bb8; no write of `T+4` with a value). P. If the offer is still open and B leaves → A ← 0x7105 ≠0. B answering a
   dead offer → 0x7105 −618.

## 5. Integration

The module is loaded by `yo2srv/arranque.py` as `TP` (`load_research("intercambio")` then `TP.install(ns)`); its
`HANDLERS` (0x7000) replace the lobby's trade handler. On connection close, `TP.on_close(c)` is called next to the
lobby's `on_close`. Reloading it at runtime loses the in-memory `TRADES`: only do it with no trades in progress.

## 6. Open questions (flags in `proposal.py`, off by default)

1. **`LIMIT_BYTES`** (D): what the official server sent in TRADELIMIT. Currently zeros = everything tradeable.
   Non-tradeable promo/gift cards? Copies in decks? (decks are local: the server does not see them).
2. **`ADDCARD_REJECT_1010`** (D): the client closes the scene on −1010; when it was sent is unknown. Off: the card is
   accepted and the trade ends in "Trade Cancelled".
3. **`RESET_READY_ON_CHANGE`** (D): if the partner changes cards while "ready", was "ready" cleared? Nothing in the
   client says. The final dialogue shows both lists, so there is no deception.
4. **`CLEAR_STATUS_AFTER_NO`** (D): after "No" in the final dialogue, 0x7134 0 to turn off the other's "ready"?
5. **`DECLINE_VALUE`** (D, no visible effect): value ≠0 of 0x7105 on decline; −618 for a dead offer (P).
6. 0x7120 = 1 (back to "ready, waiting") is not used: when the official server sent it is unknown (D).
7. No time limit for the offer (0x7111 TRADEOFFERTIMEOUT does nothing in the client). The requester cannot cancel
   (CANCELREQTRADE is dead code): if B never answers, A stays on "Requesting…" (C for the client).

## 7. Risks not yet tested live

- That the pid the requester holds in +0x1bb8 is really 0 (P): otherwise they will not see "The opponent has
  cancelled".
- That the scene opens on the same lobby connection (`s.lobby`) through which the 0x7000 messages arrive.
- Corrections to `research/lobby/NOTES.md` (Trading section): 0x7105 to the requester is 0 = accepted (not an echo);
  0x7112 has no echo; 0x7119 carries 80 B of lists and its u8 0 is not answered; 0x7132 TRADEREADY does have an answer
  (trunk + limit) even though nothing sets +0x4e; 0x7128 waits for nothing.
