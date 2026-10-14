# Networking and security of the launcher

How players find servers and connect to them without any central infrastructure, and what protects each step. Code in
`tools/yo2emu/launcher/sala/` (shared core), `app/nucleo/` (window logic) and `juego/` (game hook). Section numbers in
code comments (`§8`, `§11`…) refer to the Spanish design notes this document summarises.

## Overview

```
 yo2.exe ──TCP 127.0.0.x──▶ launcher (player) ══ libp2p (QUIC/TCP, hole punching, relay) or Tor ══▶ launcher (host) ──▶ run.py
           (hook redirects)                       one encrypted tunnel, one yamux stream per game connection
```

- The game client is unmodified. A small hook — our own `version.dll` proxy (`juego/dll/yo2net.c`) or Frida
  (`juego/*.js`) as a fallback — rewrites the client's connections to local ports owned by the launcher.
- The player's launcher opens one tunnel to the host and maps each game connection to a yamux stream. Only the game
  ports are forwarded; the host's admin console (5799) and management API (8791) never cross the tunnel.
- The host's launcher gives each remote player its own loopback source address (`127.0.0.x`) so that `run.py` can tell
  players apart, and passes the player's signed identity to it.

## Host identity and room codes

- Every saved server has a random **ed25519 key** (`identidad.key`, only on the host's PC). The same key is its libp2p
  peer ID and its Tor `.onion` address.
- A **room code** (`YO2-` + 12 groups of 6 letters) contains the host's *public* key, a 12-byte entry secret and a check
  byte. The secret derives the DHT rendezvous point, the mDNS name and the HMAC key of the handshake.
- **Handshake** (`sala/tunel.go`, `YO2T3`): the player proves knowledge of the secret (HMAC) and signs with its own
  per-install key; the host **signs both nonces**, and the player checks that signature against the public key in the
  code. A server that knows the code but not the host's private key cannot impersonate it. A new code (new secret)
  locks out everyone holding the old one; a new identity also changes the key.
- Codes are never written to logs; the window shows a short **fingerprint** (`XXXX-XXXX`) of the host key instead.

## Public server list without a server

- A public host announces (`Provide`) a fixed content ID — sha256 of `yo2online-lista-publica-v1` — on the public
  **IPFS DHT** and re-announces it every 10 minutes. Hidden hosts never announce it.
- Players run `FindProviders` on that CID (up to 300 providers, 16 queries in parallel, 10 s per provider) and ask each
  one for its **server card** over `/yo2online/ficha/1.0.0`: a JSON of at most 2048 bytes (name, description, rules,
  players, max players, game version, launcher/package version, whether a password is needed…) **signed with the same
  key as the peer ID**, bound to the peer ID and to a fresh nonce. Copying another host's card fails verification.
- Texts are validated (no control or bidi characters, length limits). A host can still *lie* about its numbers or use
  another server's name, so favourites, the cache and hidden servers are keyed by peer ID and the fingerprint is shown.
- Limits against abuse: per-peer rate limits and a cap on concurrent card requests on the host; cards are served from
  cached data and never reach `run.py`. DHT records of a closed host expire within 48 h.
- Cache: `%LOCALAPPDATA%\YO2Online\lista\servidores.json` (signed cards, re-verified on read, 7 days).

## Who can join: Public / Password / Hidden

| Mode | Listed | Join from the list | Room code |
|---|---|---|---|
| Public | yes | no secret (`YO2P3`, the "public secret" is derived from the host key and sent in the card) | works |
| Password | yes, with a lock | password (`YO2W3`) | works **without** the password |
| Hidden | no | refused (`YO2NO`) | works |

Password handshake (`sala/contrasena.go`): the host sends a KDF version and a salt **signed** by its key; the player
derives `K = Argon2id(password, salt ‖ host public key)` (t=3, m=64 MiB, p=4) only after checking that signature, and
answers with an HMAC of the connection nonces and both public keys under `K`. The password itself is never stored:
the host keeps `K` encrypted with Windows DPAPI; "Remember password" does the same on the player's side. Brute force
is limited to 5 failures per minute per player identity and 30 per minute in total.

## Ways to connect

1. **mDNS** on the local network (works with no Internet at all; ~0.1 s).
2. **Direct P2P** over the Internet: libp2p with QUIC and TCP, AutoNAT, UPnP/NAT-PMP and **hole punching** through
   circuit relays of the public network.
3. **Tor** fallback: the host publishes its `.onion` (same key). Slower (200-300 ms) but works through most NATs. The
   first Tor start on a PC downloads the directory and can take minutes.
4. **Join by address** (`sala/direccion.go`): `host:port` or a multiaddr, dialled directly (no DHT, no relays, no Tor).
   Without a room code the peer ID is learnt from the security handshake and the signed card is then required as usual.
5. **Fixed port** (*Settings → Network*, default 47300, TCP and UDP): makes direct connections and "join by address"
   predictable; if the port is busy a random one is used and the window says so.

Bootstrap nodes and relays come from, in order: the publisher-signed list shipped inside the package
(`launcher/red/nodos.txt`, signed with `firmar -red`), the user's own `%LOCALAPPDATA%\YO2Online\red-nodos.txt`, and the
go-libp2p-kad-dht defaults. If the public IPFS bootstrap nodes ever disappear, any go-libp2p-kad-dht node in server mode
can replace them and the new list propagates with the next package update.

## Signed updates

- A **publisher key** (ed25519) signs launcher executables (`<exe>.firma`: name, version, sha256, size) and whole server
  packages (`paquete.json` = manifest of every file with its part, sha256 and size, plus a signature over the exact
  manifest bytes). Its public half is compiled in (`sala/firma.go`). Builds of forks must use their own key (see
  BUILDING.md).
- A launcher only installs a version that is signed by that key, matches every hash and size, and is **newer** than the
  installed one (no rollback). Hosts serve only files whose on-disk hash matches their signed manifest.
- Distribution is peer to peer: every hosting launcher serves its package over `/yo2online/actualiza/1.0.0` and over
  logical port 3 of the tunnel (also through Tor), with upload limits per peer and globally. Players fetch only changed
  files, stage them in `_update\`, verify again and apply on restart with an undo journal; a version that fails to start
  is rolled back and marked as failed.
- Not covered (yet): metadata expiry (a host may withhold the latest version), threshold keys, key rotation.

## What a public host exposes

- Its public IP addresses in the DHT (like any IPFS node) and its game server to anyone (with open registration anyone
  can create an account). Bans by tunnel identity are weak because identities are free to regenerate; account-level
  bans and "only accounts I create" are the real controls for large public servers.
- Package files to anyone who asks for updates (rate-limited).

## Tests

`go test ./...` in `tools/yo2emu/launcher` runs small P2P networks on 127.0.0.1 (DHT of three nodes, tunnels, public and
password handshakes, impostors, card tampering, rate limits, signed packages, rollback). Tests against the real public
network are opt-in (`YO2_RED_REAL=1`, `YO2_LAN_REAL=1`).
