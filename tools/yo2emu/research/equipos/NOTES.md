# Teams between players

Code: `proposal.py` (HANDLERS + `install(ns)` + `list5_body` + `team_chat_targets`).
Confidence: **C** = confirmed in the client code, **P** = probable, **D** = doubtful (this server's decision, with a
constant to change it).

## Protocol (all big-endian)

| Id | Direction | Format | What the client does | Conf. | Where |
|---|---|---|---|---|---|
| 0x4550 TEAMCREATE | C→S | name16 (strlen+1 copied into 32 zeroed bytes, sends 16) | waits on +0x84 | C | FUN_006cd1f0 |
| 0x4551 CREATERESULT | S→C | u32 code → +0x11fc | 0 ok → the window requests TEAMLIST again; **1** → DLG 5323 "This name is already registered."; **4** → DLG 5331 "Inappropriate team name."; other → draws nothing and the window stays in state 5 | C | FUN_006bcfa0; FUN_00a97120 case 5 |
| 0x4566 CREATERESULTEND | S→C | s32 → +0x980 | +0x84=2 (end of wait). Send AFTER 0x4551 | C | FUN_006bd020 |
| 0x4563 CREATE_NOTICE | S→C | — | empty handler: not sent | C | FUN_006bd070 |
| 0x4552 TEAMDISBAND | C→S | — | sets +0x85=1, but the window does NOT wait: goes to state 10 and closes | C | FUN_006cd2b0; case 6→10 |
| 0x4553 DISBANDRESULT | S→C | s32 → +0x984 | clears the member list, +0x11e1=0, +0x85=0x69. Does **not** set +0x11e0=0 | C | FUN_006bd220 |
| 0x4560 DISBAND_NOTICE | S→C | u32 team | text 5327 "Your team has been disbanded." in the Team tab (channel 5). Does not touch the state | C | FUN_006bd090 |
| 0x4554 TEAMJOIN | C→S | u32 invited pid | no wait; 0x4555 TEAMJOINRESULT has no handler → no answer | C | FUN_006cd320 |
| 0x4567 TEAMJOIN_APPY | S→C | u32, name16, u32, name16 | queue +0x11f0 (0x28 B), no duplicates by the **2nd u32**; +0x11e8=1. The map opens the dialogue only if +0x11e0 == 0: "<2nd string>\n" + 5320 "You have an invitation from a Team. Do you want to accept it?" | C (format) / **P (order)** | FUN_006bd300, FUN_00863ec0 (0x868665), dialogue 0x87b96d |
| 0x4568 JOIN_REPLY | C→S | u32 1=yes/0=no, u32 = 1st u32 of 0x4567, u32 = 2nd | +0x11e8=0 | C | FUN_006cd390 ← FUN_0087b170 (DAT_012af248/24c = entry+0/+4) |
| 0x4561 JOIN_NOTICE | S→C | u32 team, u32 pid, name16 | adds (pid, name, authority 0) to the list; text 5324 "%s has joined the team." | C | FUN_006bd610 |
| 0x4556 TEAMBANISH | C→S | u32 pid (selected row) | no wait; the window returns to state 0 (requests TEAMLIST) | C | FUN_006cd420; case 7 |
| 0x4562 BANISH_NOTICE | S→C | u32 team, u32 **pid** (the 2nd u32 is the one compared), name16 | removes from the list; text 5325 | C | FUN_006bd9f0 (0x6bdbeb) |
| 0x4569 TEAMLEAVE | C→S | — | the client itself sets +0x11e0=0 | C | FUN_006cd490; case 8 |
| 0x4570 LEAVE_NOTICE | S→C | u32 team, u32 pid, name16 | removes from the list; text 5326 | C | FUN_006bddd0 |
| 0x4558 TEAMLIST | C→S | — | waits on +0x88; requested by the window (FUN_00a97120 case 0) **and by My Room on entry** (FUN_00826020) | C | FUN_006cd4e0 |
| 0x4564 LIST_BEGIN | S→C | s32 number of members, u32 team, name16, flag16 (40 B) | +0x11e0 = (n≠0); name → +0x11d4; team/name/flag also go to the player's OWN block record (+0x17c/+0x184) if present in +0x9a4; clears the list | C | FUN_006be3e0 (log "チームリスト開始 チーム名:%s, チーム員数:%d") |
| 0x4559 LISTRESULT | S→C | loop (u32 pid, name16, u8 authority), 21 B each, several messages allowed | if pid = own: +0x11e1 = authority | C | FUN_006be190 |
| 0x4565 LIST_END | S→C | s32 → +0x990 | +0x88=2, +0x9b8=1 | C | FUN_006be6e0 |
| 0x4571 (no name) | S→C | u32 pid, u32 team, name16, flag16 | updates that pid's record in the block list; if it is the player itself: name → +0x11d4 and +0x11e0 = (team≠0); refreshes the scene. **If the pid is not in the block list, it does nothing** | C | FUN_006be750 |
| LIST5 0x4223 / 0x3112 | S→C | (u32 pid) + u32 team, name16, flag16 | +0x17c, +0x184 of the record; the profile card shows "Team" (text 5312) | C (fields) / P (card) | FUN_006b9320, 0xa76807 |
| 0x4400 type 5 | C→S | team chat; the window only allows it if +0x11e0≠0 | → 0x4402 channel 5 to the members | C | lobby research |
| 0x3603 / 0x3604 | C→S/S→C | after TEAMLIST the window sends 0x3603 with the pids in the list (max. 100) and waits on +0x40; with 0x3604 it marks the row "online" (state 2) with the 5 u8 (location). Without 0x3604: "Requesting now…" (4029) / "is not online" (5019) | C | FUN_006cd550, case 2, FUN_00a9c4a0 |

**Authority** (u8 of 0x4559 → +0x11e1), window buttons (FUN_00a9abe0, 0xa9bc2d-0xa9bd2b) — **C**:
- no team (+0x11e0=0): only "Create a Team" (5315).
- bit **2** → "Disband the Team" (5317) enabled and "Leave the Team" (5318) disabled (and the other way round without
  the bit): **the leader cannot leave, only disband**.
- bit **8** → "Remove from the Team" (5316).
- bit **4** → another player's profile card offers "Invite to the Team" (5319) if also: you have a team, the other
  player is not in a room/duel (state ∉ 0x65-0x6a, 100, 0x98), your list has **< 100** members and their team name is
  empty (0x898d9e-0x898e27).
- Values used: leader **0x0e** (P: the bits are C, the official exact value is not), member **0** (D).

**Limits (C):** name 16 B (text box limited to 0x10, FUN_00a95c10 +0x3f0); 100 members (profile card and 0x3603).
Confirmations: 5329 disband, 5330 leave, 5328 "You are about to remove %s from the team".

## What the server does (`proposal.py`)

- Global registry `state["equipos"] = {next_id, teams{id: {id, name, flag, leader, members[[pid, name, authority]],
  created}}}` (members come from several accounts) + a **per-profile** pointer `profiles[slot]["team"]` written in the
  account view (like `_friend_add`); it self-corrects on the next TEAMLIST.
- Create → 0x4551 + 0x4566 + 0x4571 (to the creator and their block). Invite → 0x4567 to the invited player
  (connected, no team, team < 100, inviter with bit 4). Yes → 0x4571 (them and block) + 0x4561 to the connected
  members (including the newcomer). Remove/leave → 0x4562/0x4570 to the members and to the affected player + 0x4571
  team 0. Disband → 0x4553 0 + 0x4560 + 0x4571 team 0 to every connected member (needed: 0x4553/0x4560 do not clear
  +0x11e0). LIST5/0x3112 with the real team.

## Integration

The module is loaded by `yo2srv/arranque.py` as `EQ` (`load_research("equipos")` then `EQ.install(ns)`, which also
sets `HUB.list5_body = EQ.list5_body`). Its HANDLERS (0x4550/52/54/56/58/68/69) override the lobby ones. Team chat:
in the lobby's `h_chat`, kind 5 goes to `EQ.team_chat_targets(s)` as 0x4402 (channel 5) to the connected members.

## Open questions (flags / D decisions)

1. **Order of 0x4567** (`APPY_INVITER_FIRST=True`, P): the client log suggests 1st block = inviter, 2nd = team. If
   the dialogue shows the person's name instead of the team's, set `False`. The 0x4568 answer is accepted in either
   order.
2. **Member authority** (`AUTH_MEMBER=0`): could members invite (bit 4) in the official game? Were there sub-leaders
   (bit 8 without bit 2)?
3. **Team flag** (`FLAG16` = zeros): it was downloaded over HTTP (`teamURL` + `teamflag/`, FUN_00b04ba0) and the client
   does not choose it; where it came from is unknown.
4. Names: does a case-only difference count as a duplicate (`NAME_CASEFOLD=True`)? Word blacklist for code 4? Currently
   only the empty name gives 4.
5. Declining an invitation: the client has no notice for the inviter (text 5321 "The invitation was not accepted." is
   not used anywhere in the exe) → nothing is sent.
6. 0x4553 denied (not the leader) = 1 and creating while already in a team = 1: unreachable from the UI; invented codes
   (D).
7. **0x3604, the 5 "location" u8** (row +0x18..+0x28: server, area, block…): the lobby's `h_online` sends zeros; the
   row shows "online" but the location will be wrong. Shared with friends; pending separately.
8. Invitations to NPCs, to offline players or to someone who already has a team are ignored (logged). An invitation
   expires after 600 s (D).

## Live test
Two clients in the same room, LV≥2: A opens "Team List" → Create "Test" → B's profile card → "Invite to the Team" → B
accepts → chat in the Team tab → A removes / B leaves → A disbands. Check the `[equipos]` log lines and that B's
profile card shows the team.
