# Accounts: 0x3003 (user ID / password) and 0x3004 AUTHRESULT

Confidence markers used in this file: **C** = seen in the client code (decompilation, tables read from the exe);
**P** = probable; **D** = decided by this server (there is no evidence of what the official server did).

Module: `cifrado.py` (pure Python). Log masking: `logseguro.py`. Server side: `pvp_hub.py` (`Hub.login`,
`Hub.in_use_by`, `Hub.reject`, `REJECTED`), `yo2srv/cuentas/sesion.py` (`register_account`, `HUB_KEYS`,
`SIN_SESION`, `cur_sess`) and `yo2srv/cuentas/personajes.py` (`game_reply`, branch 0x3003).
No real user ID or password is recorded anywhere; tests use made-up credentials.

## 1. How the 32 bytes of 0x3003 are built

| Step | Where | What | |
|---|---|---|---|
| Fields | `FUN_00642120` / `FUN_006421a0` (setters of the CNet+0x121c sub-object), called by the account task `FUN_006a1d80` case 2 from the global object `DAT_012bb6b0` (+0x78 user ID, +0x88 password) | `strcpy` into CNet+0x1224 (user ID, 17 B) and CNet+0x1235 (password, 17 B). No upper-casing, no trimming, no length check on the copy | C |
| Length | field sizes | user ID ≤ 16 (0x3003 carries only 16 B of the field; with 16 characters there is no NUL), password ≤ 16. The length limit of the login text box has not been found | C (fields) / P (text box) |
| Challenge | `FUN_0068f430` (5731) / `FUN_006b7e50` (5732) read 16 B of 0x3002 into CNet+0x534 / +0x7cc | Our 0x3002 is sent **empty**: the reader copies 16 B from the receive buffer without checking the length. That buffer is malloc'ed and zeroed on every connection (`FUN_0068b630`) and 0x3002 is the first thing that arrives → **challenge = 16 zero bytes**. This is why blobs do not change between sessions nor between 5731 and 5732 | C (reader, zeroing) / P (that nothing arrives earlier) |
| Digest | `FUN_0068efd0` (5731) / `FUN_006cb990` (5732) → `FUN_0068bbb0` = MD5 (`FUN_006022b0` with 0x67452301…) | `MD5(userid[:strlen] + challenge16 + password[:strlen])` | C |
| Body | same place | 16 B of the user ID field **as is** (including leftovers after the NUL) + 16 B digest | C |
| Cipher | `FUN_0068b1b0(pkt, 0)` → `FUN_005830c0` (decrypt = `FUN_005835c0`) | **Standard Blowfish**, 16 rounds, **ECB**, 8-byte blocks as two **big-endian** u32 (`FUN_0068b310` / `FUN_0068b350`). P/S tables at 0xc814c4 / 0xc8150c = digits of pi (checked byte by byte; the module generates them instead of copying them) | C |
| Key | `FUN_0068b3e0` (network start-up, next to `WSAStartup`) builds **56 B** byte by byte on the stack (`local_1c8…local_191`) and calls `FUN_0068b180` → key schedule `FUN_00583ac0`. The bytes are in `cifrado.KEY` | C (and confirmed: it decrypts every recorded blob) |

Another Blowfish key in the exe, `"nameko!"` (0xc89f00), belongs to a different object; it is not used for 0x3003.

**Can it be decrypted?** The user ID, yes. The password, **no**: only its MD5 travels (together with the user ID
and the challenge). The server can **check** a user ID/password pair (`verify`) and **generate** the exact blob the
client would send (`encrypt`), which is what is needed to create accounts server-side.

**Leftovers after the NUL (important).** The user ID field is not cleared between attempts: if a longer user ID was
typed earlier in the same client run, the bytes after the NUL are leftovers (seen in one of the recorded blobs).
The same user ID and password therefore produce **two different blobs**, and with the legacy id derivation
(`legacy_id`) two different account ids. `account_id()` uses the canonical form (leftovers zeroed) and returns the same
id in both cases; for blobs without leftovers it equals the legacy id.

Not verified: that `encrypt(userid, password)` reproduces a real captured blob byte for byte (it needs the real
password, and the digest is one-way).

## 2. 0x3004 AUTHRESULT: codes and texts

Reader on 5731: `FUN_0068f480`: s32 into CNet+0x548; if it is 0, a u32 of flags (+0x1214) and 4 B (+0x325d, "ESP").
If it is not 0, nothing else is read (C). Account task `FUN_006a1d80` case 7: result ≠ 0 → text id =
`FUN_006a6eb0(result)`; the client sends 0x0003 and closes the connection itself (`FUN_0068cdf0`), then goes to
state 0x1d: message box with the text + sound; on OK (button 4000) the task ends with result 3 (error) (C). What the
scene does afterwards (asking for user ID/password again) is P: not traced.

Texts below are from the Spanish client, translated.

| s32 | hex | Text id | Text | Use at login |
|---|---|---|---|---|
| 0 | 0 | — | — | logged in |
| −200 | 0xffffff38 | 3126 | "Could not connect to the server." | |
| **−240** | 0xffffff10 | **3107** | **"Incorrect User ID or Password"** | **wrong user ID or password / unknown account** (the official text does not distinguish) |
| −241 | 0xffffff0f | 4541 | "The User ID is not valid." | |
| **−242** | 0xffffff0e | **4532** | **"This User ID is already in use."** | **already logged in** |
| −244 | 0xffffff0c | 4533 | "This User ID has not been used for more than 180 days." | |
| −245 | 0xffffff0b | 4542 | "This User ID is for Beta testing. …" | |
| −246 | 0xffffff0a | 4583 | "You are banned from logging in." | ban (used by the admin tool) |
| −255 | 0xffffff01 | 4535 | (empty) | |
| −260/−261 | 0xfffffefc/fb | 4536 | "This Player Name already exists. …" | (common translator; not login) |
| −262 | 0xfffffefa | 4537 | "This name contains unsupported characters. …" | same |
| −263 | 0xfffffef9 | 4538 | "Inappropriate name. …" | same |
| −266 | 0xfffffef6 | 4539 | "There are no Players with this name." | same |
| −267 | 0xfffffef5 | 4540 | "There is no information in the Box." | same |
| −269 | 0xfffffef3 | 3024 | "You have not registered your date of birth. …" | |
| −270 | 0xfffffef2 | 3023 | "You cannot connect because of the game's age rating." | |
| other | | 4504 | "There was a transmission error." + " (code)" (`"%s (%d)"`) | |

`FUN_006a6eb0` is the common error translator (C). 4534 "This User ID is restricted." is not produced by this
translator. On 5732 (lobby) 0x3004 is read by `FUN_006b7ea0` and the lobby task `FUN_006d1840` case 7 fails with the
code (`research/lobby/NOTES.md`); after a rejection on 5731 the client never gets there.

## 3. Strict login (`YO2_LOGIN_ESTRICTO`, on by default)

| Piece | Behaviour |
|---|---|
| Flag | `pvp_hub.LOGIN_ESTRICTO` = `YO2_LOGIN_ESTRICTO` (default `1`). With `0`, any 0x3003 creates its own account (with the canonical id). Can be switched at runtime from the console: `pvp_hub.LOGIN_ESTRICTO = False` |
| Account id | `cifrado.account_id(data)` (canonical). Compatibility: if the canonical id is unknown but `cifrado.legacy_id(data)` is known, the legacy id is used (both are equal unless there are leftovers) |
| Rejection | In `Hub.login`, before `register_account`: with the flag on, the account must already exist in the state (`accounts` or the primary account, `owner_acct`). Otherwise 0x3004 s32 **−240** (text 3107); nothing is created; the connection is closed 1.5 s later (like the ban, −246). The log line has the first 6 hex digits of the id and the port, **never** the user ID or the blob. The last 20 rejections are kept in `pvp_hub.REJECTED` (6 hex, time, port; no bytes) |
| `state["logins"]` | `{"alias": {id: account}, "usuarios": {account: userid}}`, written only by the admin tool (not included). Lookup order at login: alias (canonical, legacy) → the account itself (canonical, legacy) unless an alias shadows it → unknown. An account that is the target of an alias no longer logs in with its own id (its password was changed) |
| Creating an account | user ID + password (1–16 bytes of cp1252 each; `ValueError` otherwise) → `acct = account_id(encrypt(userid, password))` → added to `accounts` (same path as `register_account`). Neither the password nor the blob is stored. The user ID is case-sensitive: "Ana" and "ana" are different accounts (as in the client) |
| Changing a password | the id depends on the password, so it changes: remove the old alias entry and add `account_id(encrypt(userid, new_password)) → acct`; the data stays under the same `acct` |
| Approving a rejected attempt | alternative that does not require typing the password server-side: the player tries to log in, the rejected id appears in `REJECTED` and an operator adds it. Also useful if the zero-challenge hypothesis ever fails |

**Why 0x3002 stays empty.** A random 16-byte challenge would be the official behaviour, but then the server would need
the plaintext password to check the MD5. With an empty 0x3002 the blob is fixed and storing its digest is enough.

### Log masking (`logseguro.py`)

The 0x3003 line in the log is `<- 0x3003 #n len=32 md5=ok` plus the first 6 hex digits of the canonical account id;
the 32 bytes are never written. Every log line goes through `logseguro.mask_line`, which also masks: the room
password of 0x4288/0x4300, the contents of 0x81xx, the ID/PW and saved password that the client itself prints
(`[game:log] ID[%s]`, `PW[%s]`, `パスワード…[%s]`, `password:%s`) and the hook dump (`[cli] send` + hex) of those
messages. Remaining exposure: the full account id (16 hex) still appears in `[hub]` lines. It is not the blob, but
together with the user ID it would allow offline password guessing (the same holds for the state file and the ban
list).

## 4. Connections without a session, and limits

**Problem this solves.** If `Hub.login` did not create a session (session limit or saved-account limit reached) but
the 0x3003 branch still answered 0x3004 "ok", the connection continued with no session. A missing session used to
mean "the primary account", so such a connection (or any message sent without a prior 0x3003, on any port, or a duel
whose 0x6001 did not attach to a session) could read and write the primary profile. Sessions were also never released,
so the 65th distinct account since start-up hit the same path.

| Where | Behaviour |
|---|---|
| `pvp_hub.Hub.login` | Never returns None without rejecting: data < 32 B → −240; session limit reached or `register_account` without a free index → 0x3004 **−200** (3126 "Could not connect to the server.", C; the official text that best says "not now", P) with open login, −240 with strict login; close 1.5 s later. Before checking the limit, `sweep()` releases sessions that have gone |
| `game_reply`, 0x3003 | If `HUB.login` returns None: `return` (it already sent its 0x3004); if an older hub did not reject, reject here with −200. No `load_state()` at the start (0x3001/0x3003/0x0005 arrive without a session) |
| Context | Marker `SIN_SESION` (falsy, account `"sin-sesion"`). Network threads (`conn-<port>`, `duelo-…`, `serve-<port>`) always carry a session or `SIN_SESION`; a `None` inside a network thread also counts as "no session" (`cur_sess()`). With `SIN_SESION`: `load_state()` / `save_state()` raise `SinSesion` (the connection handler logs one line and does not answer), `my_pid()` = 0, `ocg_session()` does not create the ocg session (it is created with the first message that has a session, normally 0x6001), `duel_state()` = None. `None` means "primary account" only outside network threads: console (port 5799), admin tool, start-up, tests that call handlers on the main thread. `_is_owner_ctx` no longer treats an object without `owner` as the primary account. Closing a connection (`on_conn_closed`) runs with that connection's session |
| Releasing sessions | `Session.gone` = time since which the session has no live connection (`Hub._live`). `on_close` schedules one timer; `Hub.sweep()` releases sessions that have had no connection for `SESSION_RELEASE_S` (**120 s**, `YO2_SESION_LIBERAR_S`) and have no reserved or running PvP (`PVP.by_pid`); if it was still in a block, it leaves it (0x3112 op 0). The next 0x3003 creates a new session with the same index: same pids and data |
| Configurable limits | `YO2_MAX_CUENTAS` (`MAX_SAVED_ACCOUNTS`, 64; with strict login it only limits accounts created server-side), `YO2_MAX_SESIONES` (`pvp_hub.MAX_ACCOUNTS`, 64 concurrent), `YO2_MAX_CONEXIONES` / `YO2_MAX_CONEXIONES_IP` (256 / 32). All can be changed at runtime from the console |

**Known limitation (addressed elsewhere).** The duel port attaches the connection to a session through the pid in
0x6001 (`Hub.attach`). Pids are easy to guess (1000 + 4·index + slot), so without further checks a connection could
attach to another connected player's session. This is handled by the duel reservation described in
`research/seguridad_duelo/NOTES.md`.

## 5. −242 "already in use"

| Fact | Where | |
|---|---|---|
| −242 (0xffffff0e) → text **4532**: Spanish "Este Código de Usuario ya está en uso.", English "This User ID is already logged in.", Japanese "すでにログインしています。" | `FUN_006a6eb0` (common translator), client text tables | C |
| Same path as −240/−246: message box with the text, the client sends 0x0003 and closes by itself | `FUN_006a1d80` case 7 (§2) | C |
| There is no similar code: the translator has no "kicked by another session", and in the texts (Japanese search: ログイン/二重/別の場所/切断/強制/多重) only the generic 4507 "You have been disconnected from the Server", 4517/4518 (duel/room) and 4543/4544 "The connection was lost…" appear. 4534 "restricted" is not produced by the translator | Japanese text table | C |
| The official server **rejected the second login** and did not kick the first one: the text addresses the one logging in ("you are already logged in") and there is no text for a player who would be kicked | | P |
| Web: nothing about YO2 (English and Japanese searches); the usual behaviour of MMOs of that era is "already logged in" until the server considers the old session dead (minutes) | | P |
| When the official server considered the old session dead: unknown. Here: 90 s without messages | | D |

**How the client uses its connections (C, from a recorded session).** Every connection sends 0x0005 every **60 s**
and the client disconnects if **120 s** pass without receiving anything (`FUN_0068d080`: `0x78 < time - last`).
5731: 0x3003 and, when moving to the lobby, 0x0003 **without closing the socket**; it keeps sending heartbeats during
play and, when returning to the title screen, the client **abandons it open** (stops sending heartbeats; the server
closes it after 600 s without data). 5732 is opened before the 0x0003 on 5731; lobby → room: 0x0003 and the lobby
socket is closed, then the room's 0x3003 (same for room → lobby and room → room). Therefore neither "an open
connection" nor the IP tells whether an account is in use: an abandoned 5731 stays open for 10 minutes, and two
windows on the same PC share the IP.

**Rule** (`pvp_hub.Hub.in_use_by`, called from `Hub.login` when the account's session already exists): the 0x3003
gets **0x3004 −242** and is closed 1.5 s later if the session has ANOTHER connection that:
- is not closed (a client that is closed or killed sends FIN/RST: it stops counting immediately);
- has not said goodbye with 0x0003 (`c.bye`, set by the connection handler);
- sent something within the last `LOGIN_EN_USO_VIVA_S` (**90 s** = one heartbeat + margin, D;
  `YO2_LOGIN_EN_USO_VIVA_S`; `c.rx_t`, set by `Conn.saw_client` on every message);
- is not the gate (5730); and, if the 0x3003 does not arrive on 5731, is on the **same port** (the client itself
  overlaps 5731 → 5732 → room; a real second client always enters through 5731).

It is per account, not per IP: different accounts on the same PC (127.0.0.1) or through a tunnel (other loopback
addresses) are not affected. `YO2_LOGIN_EN_USO=0` (at runtime `pvp_hub.LOGIN_EN_USO = False`) disables it. The
rejection is logged (account prefix, port, the other connection's port, same/different IP, seconds since its last
message) and recorded in `pvp_hub.REJECTED` with code −242.

| Case | Result |
|---|---|
| Two clients at the same time (different IP or same IP) | the second one: −242 and close |
| Client in a room / duel; another one enters through 5731 or directly into the same room | −242 |
| Clean exit (back to title: room 0x0003 + close, 5731 abandoned open) and log in again | logs in immediately |
| Client closed or killed without goodbye (socket closed) | logs in immediately; same session (same pid) if less than 120 s passed, otherwise a new session with the same index |
| Network cut without FIN (half-open connections, stuck tunnel) | −242 until 90 s after the last message; then it logs in (it does not wait for the 120 s sweep or the 600 s `IDLE_TIMEOUT`). Old connections are not closed: they die after 600 s |

**Open points.**
- Policy (P): reject the second login. Kicking the first one would be a different policy (not 1:1).
- `VIVA_S` = 90 s (D): after a network cut without FIN, the player sees "already in use" for up to 90 s.
- A client that skips 5731 and goes straight to 5732 or to a room different from the first client's does not get
  −242 (outside 5731 the check is same-port only, so as not to collide with the client's own overlap). The real client
  always goes through 5731.
- Unrelated issue seen while testing: server message numbering is kept per (IP, port) (`Conn.GATE_SEQ`), so two
  clients on the SAME IP (two windows on 127.0.0.1, same or different accounts) share the counter: the second one's
  `#1` resets it and the first one may receive out-of-order numbers (`FUN_0068bac0`). With −242 the second one leaves
  immediately, but its 0x3001 has already reset the counter. `net_hook.js` with `YO2_LOCAL_IP` (one loopback IP per
  client) avoids it.
