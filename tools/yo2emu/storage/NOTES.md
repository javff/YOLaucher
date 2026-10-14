# SQLite storage for the YO2 emulator (`storage/`)

Replaces saving to `state.json` with an SQLite database encrypted with SQLCipher. The source of truth is an
**append-only event history**, chained with SHA-256. The rest of the emulator does not change: it still uses
`load_state()` / `save_state(st)` with a dict.

Status: **implemented and tested; disabled by default** (`YO2_STORAGE=json`).

## Design

```
server  load_state()/save_state(st)          (per-account views, as before)
          │
          ▼
        load_state_raw()/save_state_raw(st)   ← YO2_STORAGE=sqlite replaces them with STORE.load()/STORE.save()
          │
          ▼
storage.backend.Store
   save(st): json → diff against the last saved state → ops
             BEGIN IMMEDIATE
               INSERT events (1 event per save, with a chained hash)
               DELETE/INSERT of the affected state rows only
               (every 200 events) INSERT snapshots
             COMMIT                         ← all or nothing
   load():   in-memory copy of the state (json.loads of the last saved text)
          │
          ▼
storage.adapter.SqliteAdapter               ← the ONLY SQLite/SQLCipher-specific part
```

`load_state_raw` / `save_state_raw` and `STATE_LOCK` live in `yo2srv/datos/estado.py`.

Files:

| File | What it does |
|---|---|
| `backend.py` | `Store`: load/save, initial import, verification, export, `backup_to`, `restore_from`, `boot_backup`, `rebuild_tables` |
| `chain.py` | event hashing, `diff`/`apply_ops` (they preserve key order), account, slot and kind of each event |
| `project.py` | state → state tables, which rows to refresh after each change, and tables → state (for verification) |
| `schema.py` | schema and versioned migrations (`PRAGMA user_version`) |
| `adapter.py` | connection, PRAGMAs, transactions, online backup, encryption |
| `keys.py` | random 32-byte key protected with Windows DPAPI (`CryptProtectData` through ctypes) |
| `__main__.py` | CLI (`python -m storage …`) |
| `test_storage.py` | storage tests (+ `--bench`) |

### History (`events`): the source of truth

| column | |
|---|---|
| `seq` | 1, 2, 3… without gaps |
| `ts` | UTC ISO-8601 date with milliseconds |
| `acct`, `slot` | account (`_owner` = the primary account, whose keys live at the root of state.json) and character slot, if the change concerns a single one |
| `kind` | `importacion_inicial`, `baul`, `mazos`, `ficha`, `titulos`, `npcbin`, `amigos`, `perfil`, `monedero`, `cuenta`, `cuentas`, `correo`, `lobby`, `equipos`, `varios`… (initial import, trunk, decks, character card, titles, npcbin, friends, profile, wallet, account, accounts, mail, lobby, teams, misc) |
| `data` | JSON. Import: `{"state": …, "origen": "state.json"}`. Others: `{"ops": [["set", path, value] \| ["del", path]]}` |
| `prev_hash`, `hash` | `hash = sha256(prev_hash, seq, ts, acct, slot, kind, data)` with a length prefix on each field; the first event links to `000…0` |
| `sig` | empty; reserved for an ed25519 signature |

Ops preserve key order: replaying the history yields the same dict, in the same order, which is why **exporting to
state.json is byte-for-byte identical**. If a list changes, it is stored whole (pbin, friends, mail).

Any tampering is detected: a modified event (its hash does not match), a modified event with a recomputed hash (the
next one does not link) and a deleted event (gap in `seq`). At start-up the events after the last snapshot are
checked; `verificar` checks the whole history.

### State tables (derived, can be rebuilt)

`accounts`, `characters` (name, LV, MP, DPP, title, player_id + the rest as JSON), `trunk` (one row per card),
`decks`, `npc_progress` (npcbin), `friends`, `titles` (PBIN), `mail` (lobby.mail), `teams` (equipos.teams) and `kv`
(everything else; `"[]"` is the skeleton of the root). Each container moved to its own table leaves a marker
`{"$tabla": "trunk"}` in its parent, so the original keys are known. Unknown keys added by another module go to `kv`
automatically: the schema does not need to change for them to be saved.

`snapshots`: a full-state snapshot every 200 events, and at import. At start-up the last snapshot is loaded and later
events are applied.

`python -m storage reconstruir` drops and recomputes the tables from the history. `verificar` checks that tables,
snapshots and state match the history.

### Encryption

- Package: **`sqlcipher3-wheels` 0.5.7** (wheel `cp312-win_amd64`, PyPI). It bundles SQLCipher 4.12.0 community on
  SQLite 3.51.1. Install with `pip install sqlcipher3-wheels==0.5.7`.
- Key: 32 random bytes in `%LOCALAPPDATA%\YO2Online\db.key`, encrypted with DPAPI (only this Windows user on this PC).
  `YO2_DB_KEYFILE` changes the path.
- **If `db.key` is lost, the database cannot be opened.** What must be kept is a JSON export
  (`python -m storage exportar`), which does not depend on the key.
- MSIX: if the server is launched from a packaged (MSIX) application, `%LOCALAPPDATA%` is redirected to
  `AppData\Local\Packages\<package>\LocalCache\Local\YO2Online\db.key`. The key reader also looks there. A new key is
  never created for a database that is already encrypted: if the key is missing, a clear error is raised.
- `YO2_DB_CRYPT=auto|on|off`. With `auto`, the database is encrypted if SQLCipher and DPAPI are available; otherwise
  plain `sqlite3` is used. An existing database is opened as it is (the file header is inspected).

### WAL, atomicity and concurrency

`journal_mode=WAL` and `synchronous=FULL`: every `COMMIT` reaches the disk, like the fsync of state.json did. Every
`save` is one `BEGIN IMMEDIATE … COMMIT` transaction holding the event, the rows and the snapshot together.

Within the process, the lock is the server's `STATE_LOCK` (RLock), so it works as before:
`with STATE_LOCK: st = load_state(); …; save_state(st)`. If another process writes to the same database, for example a
console tool, `save` notices because the head of the history has changed; it then reloads and computes the diff
against what is on disk.

### Moving to PostgreSQL

Outside `adapter.py` there is only standard SQL with `?` placeholders, TEXT, INTEGER, BIGINT and SMALLINT types,
`DELETE` + `INSERT` (no `INSERT OR REPLACE`), `ORDER BY … LIMIT 1` and composite primary keys. A `PostgresAdapter`
would need:

- `execute`/`executemany`/`query`/`query_one`/`iterate`: with psycopg, replace `?` with `%s`.
- `begin_write` → `BEGIN` (or `LOCK TABLE events IN EXCLUSIVE MODE` to number `seq` serially).
- `get/set_schema_version` → a `schema_version(n)` table.
- `backup_to`/`restore_from` → `pg_dump`/`pg_restore`. Encryption at rest would be the server's.

## How to enable it

1. Stop the server. The first time it starts with sqlite, it imports the `state.json` of that moment.
2. Start the server with `YO2_STORAGE=sqlite` in the environment (PowerShell: `$env:YO2_STORAGE='sqlite'`).
3. The log must show an `[almacén]` line about the initial import from state.json into `state.json.sqlite`, and
   another one reporting `YO2_STORAGE=sqlite` with the database path and its event count (1 event).
4. Check: `python -m storage verificar --json state.json` (right after the import everything must be OK).

The database is `<STATE_PATH>.sqlite`, i.e. `state.json.sqlite` next to state.json (`YO2_DB` changes the path). With
this name, tests that delete `state-prueba.json.*` also clean the database. On every start-up a copy
`state.json.sqlite.bak-arranque-<date>` is made and the last 10 are kept. From then on `state.json` **is no longer
updated**.

## How to go back (to JSON)

```powershell
# 1) stop the server
# 2) dump the database to state.json (it first saves state.json.bak-sqlite-<date>)
python -m storage exportar --json state.json --forzar
python -m storage verificar --json state.json      # must report the files as identical, same key order
# 3) start WITHOUT YO2_STORAGE (or with YO2_STORAGE=json)
```

The database stays where it was. If sqlite is enabled again later, it continues from the database, not from the JSON.
If the JSON has changed in the meantime, move the database aside (for example rename it to
`state.json.sqlite.viejo`) so that it is imported again.

## Commands

```
python -m storage info | verificar [--json F] | historial [-n 20] | reconstruir
python -m storage exportar --json F [--forzar]
python -m storage copia --a DEST                 consistent online backup (the server may be running)
python -m storage restaurar --desde BACKUP       with the server STOPPED (validates the backup's chain and leaves
                                                 <db>.antes-de-restaurar-<date>)
python -m storage importar --json F --db NEW
```

(`info` = info, `verificar` = verify, `historial` = history, `reconstruir` = rebuild, `exportar` = export, `copia` =
backup, `restaurar` = restore, `importar` = import.)

From inside the server process: `STORE.backup_to(path)` and `STORE.restore_from(path)` (for example from the admin
console, port 5799). `restore_from` takes the lock and reloads the state.

## Open points / caveats

- **Admin tool (not included):** its state backup functions copy the `STATE_PATH` file, which with sqlite is the old
  JSON. They must call `STORE.backup_to(...)` when `NS.get("STORE")` exists. Whatever reads through `load_state_raw`
  works unchanged.
- The state migration script and `fake_client.py prueba` (which re-reads the test JSON at the end) only know the JSON
  format.
- ed25519 signature of each event: the `sig` column exists; the signing key and the verification are missing.
- The history grows by about 0.3–1 KB per normal save. The import takes about 60 KB. A snapshot takes about 60 KB every
  200 events.

## Tests

| Test | json | sqlite |
|---|---|---|
| `YO2_LIMIT_LIST=1 YO2_WATCH=1 YO2_ROOM_REOPEN=1 python fake_client.py pvp --offset 800` | pass | pass |
| `python fake_client.py npc --offset 820` | pass | pass (3 events written; chain and tables OK) |
| `python research/lobby/proposal.py --test` | 0 failures | 0 failures |
| `python storage/test_storage.py` | — | 30/30 |

`test_storage.py` covers:

- Round trip: import, export and get the same file byte for byte. Also after changes and after reopening.
- 500 random diff/apply changes, with exact key order.
- Valid chain and tamper detection: modified event, modified event with a recomputed hash, deleted event, tampered
  snapshot and tampered table. A tampered table is fixed with "reconstruir".
- Online backup while another thread writes, and restore. A tampered backup is rejected.
- Rotating start-up copies.
- 8 threads × 40 writes without losing any.
- Two connections writing to the same database.

### Measured performance

300 saves of a small trunk change with a real-sized `state.json` (~117 KB). Each one is `load()` + change + `save()`,
as `save_state` does. Desktop Ryzen CPU, NVMe SSD:

| backend | saves/s | reads/s |
|---|---|---|
| SQLCipher (encrypted) | ~115 | ~800 |
| SQLite unencrypted | ~116 | ~800 |
| JSON (indent=1 + fsync + `.ok`) | ~81 | — |

Encryption costs almost nothing. The limit is the fsync of each `COMMIT` (`synchronous=FULL`). With
`synchronous=NORMAL` it would be faster, but a power cut could lose the last save (without corrupting the database).
