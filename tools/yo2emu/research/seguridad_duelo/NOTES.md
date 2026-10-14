# Duel reservation (security of 0x6001)

Confidence markers where used: **C** = seen in the client code, **P** = probable, **D** = doubtful / decided by this
server.

**Default mode: `vigilar` (watch)**: nothing in the game changes; the server only logs what it would reject.

Files: `research/seguridad_duelo/tickets.py` (the reservations; no networking, loaded as `yo2_tickets`) and
`yo2srv/duelo/reserva.py` (`_duel_ticket_issue`, `_duel_ticket_alive`, `duel_reserva`), with hooks in `Conn.send`
(`yo2srv/red/conexion.py`), the connection handler `_handle` (`yo2srv/red/servidor.py`) and `on_conn_closed`
(`yo2srv/duelo/sesion_duelo.py`).

## What it does

- **A reservation is created** when the server sends `0x4306 DUELSERVERNOTICE` on a connection whose session has a
  login and a character (`Conn.send` → `_duel_ticket_issue`, *before* sending it). One single point for every path:
  NPC (`0x4288` in `lobby_reply`), NPC challenge in a room (`_duel_notice`), PvP challenge (`_challenge_reply`),
  auto-duel (`_auto_pair`), tournament (`torneos._pair`) and the console (`send(…)`), because all of them end in
  `Conn.send` (directly or through the `post` queue). It stores: session, pid, IP of that connection, the connection,
  the specific duel (the `PvpMatch` if one is reserved for that pid; otherwise "npc"/"duelo") and the time. One per pid
  (the latest: the client only uses the one from the last 0x4306).
- **0x6001** (in `_handle`, BEFORE `HUB.attach`, which otherwise attaches blindly) is only accepted if there is a
  reservation for that pid, **unused, less than 90 s old and from the same IP**, and also (checked on arrival,
  `_duel_ticket_alive`): the connection that received the 0x4306 is still open, the session of that pid is the same
  and, for a PvP, that match has not been cancelled or finished. If valid, it is marked as used and attached as usual.
- **Rejection (mode `on`)**: `0x6007` s32 **0** (the client's own error path: "LB_DUEL_BOOKEDCHECK_WAIT ERROR" and it
  closes, `FUN_00691800`) and the connection is closed after 1.5 s (`pvp_hub._close_sock`, like a rejected 0x3003).
  The connection is flagged `booked_fail` and nothing more from it is processed; **the victim's session and duel are
  not touched** (neither `c.sess`, nor `s.conns[5760]`, nor its match; `on_conn_closed` does not count it as an
  abandonment).
- **An attempt from another IP does not consume the reservation** (otherwise an attacker could "burn" the victim's).
- **The reservation dies** if the lobby/room connection that received the 0x4306 is closed (`on_conn_closed` →
  `DT.drop_conn`, also checked when the 0x6001 arrives), if the session is released or changes, or if the PvP is
  cancelled (0x4305, 90 s timeout, opponent leaving before the start…: `PvpMatch.done`, or it is no longer that pid's
  reservation).
- **Spectators**: `0x4321` creates no reservation: the spectator does not send 0x6001 but `0x6700`, which already has
  its own reservation (`Matches.watch_request`: IP + 60 s + single use). New: a connection that entered as a spectator
  and then sends 0x6001 is rejected (before, it could attach to the session of whatever pid it sent).
- **Persistent IP**: 5 *suspicious* rejections (no reservation, different IP, spectator → 0x6001) within 5 min → an
  `[AVISO] [reserva]` warning in the log and a `seguridad` (security) activity event, **once per window. Never a ban.**
  Failures of a legitimate player (expired, already used, cancelled) do not count.
- In mode `on`, every rejection is also recorded as an activity event ("attempt to enter X's duel from IP") and,
  separately (so as not to confuse it with an attack), "duel reconnection rejected" when the legitimate IP repeats its
  0x6001.

## Modes

| Mode | What it does |
|---|---|
| `vigilar` (**default**) | Lets everything through as before, but every 0x6001 that does not qualify is logged as "would reject 0x6001 pid=… from …: <reason> -> let through". Valid ones are logged as valid. The persistent-IP warning is still emitted (log + activity event). |
| `on` | Rejects (see above). An internal error while checking also rejects (better a failed duel than a hijacked one). |
| `off` | No checks. Reservations are still created, so that switching to `on` takes effect immediately. |

- At start-up: `YO2_DUEL_RESERVA=vigilar|on|off` (an unknown value = `vigilar`, with a warning in the log).
- **At runtime** (admin console): `duel_reserva('on')` / `duel_reserva('vigilar')` / `duel_reserva('off')`; the next
  0x6001 already uses it. `duel_reserva()` logs the mode, the live reservations (pid, IP, type, age), the rejections
  per IP and the counters. Setting the `DUEL_RESERVA = "on"` global also works.
- Warning threshold: `DT.insist_n = 5`, `DT.insist_s = 300`; expiry: `DT.ttl = 90`.

## Moving from `vigilar` to `on`

1. A few days of real play in `vigilar` (with players connecting through a tunnel/VPN and with several clients on one
   PC).
2. Search the logs for the `[reserva]` "would reject" lines. Each line carries the reason:
   - `IP distinta` (different IP) / `sin reserva` (no reservation) from an IP that belongs to nobody who was playing →
     attack or probe: fine.
   - `IP distinta` from a legitimate player → their lobby and duel connections go out through different IPs (client
     without `net_hook`, unusual network, local proxy restarted between the 0x4306 and the 0x6001?). **Do not switch to
     `on`** until this is understood.
   - `reserva ya usada` (reservation already used, reconnection?) from the legitimate IP → the client reconnected to
     the duel without a new 0x4306 (currently treated as abandonment). If it shows up in normal play, decide before
     `on` (see "Open points").
   - `caducada` (expired) → someone takes > 90 s between the notice and connecting: raise `DT.ttl`.
   - lobby/room connection closed → the client closed the room before connecting to the duel (not seen: the official
     client keeps the lobby open during the duel).
3. If there are no legitimate rejections: `duel_reserva('on')` from the console and, to survive a restart,
   `YO2_DUEL_RESERVA=on` at start-up.
4. Immediate rollback: `duel_reserva('vigilar')`.

## What it does NOT cover

- **Two clients on the same PC / same IP** (same household; 127.0.0.1): between them the IP does not distinguish;
  whoever arrives first with the other's pid within 90 s gets in (unless `net_hook.js` `YO2_LOCAL_IP` gives each
  client its own 127.0.0.N). Accepted limitation.
- **Cloning a guest id** (same tunnel IP): to be solved by a signed guest id in the launcher.
- **Malware on the server's host** (local processes connect from 127.0.0.1).
- **Cheating inside your own duel** (deck with cards you do not own): see the deck check below.
- **Duels whose 0x4306 went out before the reservation code was loaded at runtime**: they have no reservation (in
  `vigilar` this only shows in the log).
- **Reconnection in the middle of a duel**: the server has no "resume"; a second 0x6001 with the same reservation is
  rejected in `on` (and logged separately).

## Runtime reload

The reservation code can be loaded into a running server: new names, `DT`, and the code of `_handle`,
`on_conn_closed` and `Conn.send`, plus any top-level name that code uses and the running server lacks. At the end it
checks that every top-level name exists in the running server and logs any that are missing. It keeps the current
mode (or `vigilar`), is idempotent, and leaves everything as it was if it fails halfway. Note: if `_handle`,
`on_conn_closed` or `Conn.send` changed on disk for another reason, those changes are loaded too.

## Tests

- `fake_client.py` scenarios `pvp`, `npc`, `desconexion` and `dpcoste` in `vigilar` and in `on` (and `pvp` with
  `YO2_WATCH=1` in `on`): all fine and **no** "would reject"/"rejected" line in legitimate flows.
- A proof-of-concept hijack (a third client sending another player's pid) is **blocked** in `on` (it receives
  0x6007 = 0 and is disconnected; the two legitimate players then play normally; persistent-IP warning; no ban),
  **logged but allowed** in `vigilar`, and works as before (vulnerable) in `off`.

## Open points

- Admin tool integration: a dedicated event type for `seguridad` events, a mode switch for `YO2_DUEL_RESERVA`
  (`vigilar`/`on`/`off`, immediate effect), and a view of `DT.snapshot()` (live reservations and rejections per IP).
- **Reconnection** with an already used reservation from the legitimate IP: currently rejected in `on` (by design,
  "unused"). If legitimate cases appear in `vigilar`, the alternative is to allow it from the same IP and session
  until it expires (opens nothing new: an attacker sharing the IP is already not covered).

---

# Duel deck check (`YO2_DECK_CHECK`)

The client builds the deck by itself and sends it at the start of every duel (0x6600 header, 0x6601 main, 0x6603
extra; the side deck never travels: C, `FUN_00692840`). Without checks, `ocg_duel.client_deck` and `PvpMatch` accept
it as is: with an edited `.ydc` or a modified client a player could duel with cards they do not own.
Code: `research/seguridad_duelo/mazo.py` (loaded as `yo2_mazo`; server side in `yo2srv/duelo/mazo.py`: `DK`,
`_load_mazo`, `deck_check`), hooks in the duel-port wrapper (NPC) and in `PvpMatch._msg/_closed` +
`_deck_check/_deck_svr/_deck_cut` (PvP).

## When and for whom

With the complete deck (when the 0x6603 arrives), before 0x61fe / `start`. NPC: the player. PvP: both, each one when
its 0x6603 arrives. Profile = account + **slot of the session** of that connection (not the thread's "active" slot).

## Modes (`YO2_DECK_CHECK`; at runtime `deck_check("off" | "avisar" | "bloquear")`)

| Mode | What it does | Origin |
|---|---|---|
| `off` | no history, no warning | — |
| `avisar` (warn, **default**) | history in the profile, a `[mazo]` log line (name, 6-hex account prefix, slot, match, number of problems and their list) and a `mazo_invalido` activity event. The duel goes on | this server (changes nothing in the game) |
| `bloquear` (block, off by default) | the same, and the duel **does not start** (below) | **D** (what the official server did is unknown) |

With `YO2_PVP_TEST` and no explicit value: `off` (the fake test clients use NPC decks, which even contain cards
forbidden in 2008, e.g. Snatch Steal 285, and sometimes have an empty trunk).

## Rules (code = `regla` field of each problem)

| Rule | What it checks | Origin |
|---|---|---|
| `baul` (trunk) | copies of an id (main + extra) > copies of it in that character's `p["trunk"]` | **C**: the editor does not allow exceeding the trunk (`FUN_00907660` / `FUN_00907820`: copies in main+side+extra < trunk) |
| `existe` (exists) | the id is not in `research/ia/cards_yo2.csv` (cards of the 2008 client; generated by `paquete/generar_tablas.py`) | C |
| `sitio` (place) | a fusion (card_prop type 2/3 = csv kind 5/7) in the main deck, or a non-fusion in the extra deck | **C**: `FUN_008df280` only drops types 2/3 into the extra deck and never into the main deck (`FUN_0073c010` = (dword+4 >> 1) & 0x1f); auto-build (`FUN_00813xxx`) likewise |
| `tamano` (size) | main 40-80, extra ≤ 30 (with a `texto`, e.g. "main deck of 38 cards (minimum 40)") | **C**: `FUN_00904e50` before DUEL (`0x27 < main < 0x51`, `side < 0x10`, `extra < 0x1f`), the editor's save (`FUN_008df280`) and text 6051 "Your Deck must have between 40 and 80 Cards. Side Decks must have 15 cards or fewer." |
| `lista` (list) | copies (by **Konami** id: versions of a card add up) of a forbidden/limited card above its limit (`limit` 0/1/2 in cards_yo2.csv = what 0x5600 sends); only when `LP.LIMIT_LIST` is on | **C**: `FUN_0073c190` → `FUN_0073c290` looks up by Konami id |
| `lista` (65535) | gods and tokens (`limit` 65535, not sent in 0x5600): maximum 0, `texto` "not allowed in decks (god or token)" | **P** (the client would give them 3; there is no legitimate way to have them in the trunk) |
| `copias` (copies) | more than 3 copies (Konami id) of a card not on the list | **C**: `FUN_0073c290` returns 3 by default |

**Note on deck size:** the paper rule of 2008 was "main 40-60, extra ≤ 15". The Ver.080912 client allows **40-80 and
30** (text 6051 says so): the client's rule is used (1:1). 60/15 would belong to an "enhanced" mode. Not modelled
(**D**): `FUN_0073c190` gives 3 to everything if the character has bit 0x800 of its +0x1214.

Each problem: `{regla, id, nombre, usa, tiene, max}` (rule, id, name, uses, owns, max) (+ `texto` for
`tamano`/`sitio`/`lista` 65535, `zona` "principal"/"extra" for `tamano`/`sitio`, `min` for the main deck size).

## How "bloquear" stops the duel

Searched in the client: 0x600c PREPARATION_RESULT has no rejection code (168 B of data: `FUN_006918d0`); texts 4569
"Invalid Deck." and 4571 "This Deck cannot be used." exist in the text tables but nothing that requests them was found
(neither in the lobby error map `FUN_00884b70` nor as a constant: **D**); 0x4310 DUELABORT says "Time is up…" (4516)
and during the duel it is only a dialogue; the client's deck error (m_pDeckErr, text 6051) is opened by the client
itself when pressing DUEL (`FUN_00863ec0` + `FUN_00904e50`), not by the server. Chosen: **0x649b DUELSVRINFOMSG
code 1 during rock-paper-scissors**, the same path PvP already uses when the opponent does not arrive (`_abort`): the
Janken scene (`FUN_007c76b0`) sets +0xbd with bit 1 of +0x676 and, in game 1, returns to the map (scene 0xc/0xd) and
sends 0x0003 (C, research/duelfin NOTES §5).
- The preparation is let through (0x600b → 0x600c + 0x6005) and, on each client's **0x6100** (already in the Janken
  scene), the server answers its echo and `0x649b 01 xx`: bit0 = 0 to the player with the bad deck ("<them> has
  disconnected", 4519), 1 to the opponent ("<the other> has disconnected"). Everything else in Janken (0x6101-0x6104,
  0x61fe) is ignored.
- NPC: the duelfin game is released (like 0x0003) and the ocg session is marked "finished": closing the socket is not a
  loss. PvP: when both have it (or one leaves), `_close_match` without `_finish`: no result, no DP, no card, no duel
  log, no replay. Tested with the fake client. That `yo2.exe` shows the notice and returns cleanly to the map: **C**
  statically / **P** live (not yet tested with the real client: do so before enabling it).
- If the other player leaves before their 0x6100, the remaining one gets it anyway: P.

## History

`p["decks"]["historial"]`: at most 30 entries, newest last, de-duplicated by fingerprint (sha1 of the SORTED main +
extra, 16 hex). Entry `{hash, main, extra, primera, ultima (epoch), duelos, valido, problemas}` (first seen, last
seen, duels, valid, problems). Written in `avisar` and `bloquear` for ALL decks (good and bad), with `STATE_LOCK` and
the session's `load_state`/`save_state`. `last_duel` is unchanged (NPC only, persistence). Event:
`sys.modules["yo2_eventos"].mazo_invalido(s, problems, mode, partida=…)` (type "mazo"); `partida` = "npc:<ocg id>" or
"pvp:pvp-N". Without that module, nothing is emitted.

## Related fix (persistence)

`h_duel_deck` used to keep ONE `DuelDeck` for the whole server (two simultaneous duels mixed one's main deck with the
other's extra deck) and `profile(st, active_slot(st))`. Now the deck is assembled per connection (`c.duel_deck`) and
stored in the slot of that connection's session.

## Tests

- `fake_client.py` `npc` and `pvp` scenarios unchanged; persistence self-test, lobby/world `--test`, translator test
  and ocg/PvP tests unaffected.

## Open points

- Test `bloquear` with the real `yo2.exe` (the notice appears and it returns to the map) before offering it as a mode.
- Admin tool: a switch for `YO2_DECK_CHECK` (live value `DK.mode`; `DK.snapshot()` = mode and counters).
