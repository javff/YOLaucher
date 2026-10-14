# paridad/ — regression battery and byte-level parity checks

The safety net used during development to check that a refactor does not change the server's behaviour: a battery of
tests (T1-T10) plus a byte-for-byte comparison of everything the server sends and receives in a set of recorded
scenarios.

> **Note for the public repository:** many steps of the battery need material that is **not** in this repository:
> private recordings and copies of player data (`paridad/_fixtures/`, e.g. `state-paridad.json` and `replay.log`),
> logs of real sessions, the extracted game files under `_original/` and protocol tests from `research/*/test_*.py`
> that are not published. Out of the box, the battery does not run end to end. Contributions of synthetic fixtures are
> welcome (see [CONTRIBUTING.md](../../../CONTRIBUTING.md)).

Run everything from `tools/yo2emu` of a working copy, never against a live server or its files. `_out/` and
`_fixtures/` are in `.gitignore` (recordings and copies of player data).

| File | Purpose |
|---|---|
| `bateria.py` | The battery (T1-T10). `--banda N --etiqueta X`; with `--etiqueta base` it writes `_out/base.json` (known failures included); with any other label it compares against the base and reports IGUAL/DISTINTO (same/different) per test. `--listar`, `--solo T1,T5.login`. |
| `grabar.py` | Byte parity: `--preparar` (freezes `_fixtures/state-paridad.json` and `_fixtures/replay.log`), `--base` (3 passes + masks), `--etiqueta X` (one pass). ALWAYS port offset 2200, with the lock `_out/.lock`. |
| `comparar.py` | `comparar.py X` → IGUAL or the first difference (scenario, client, port, connection, index, id, hex). `--base` rebuilds the masks; `--mascara` prints the masked % per id. |
| `sondeo.py` | "sondeo" (probe) scenario: every known request id in accounts/lobby/room. `--listar [--escribir]` extracts the ids from the code again and compares them with the ones frozen in `escenarios.json`. |
| `escenarios.json` | Scenarios (steps, environment) and the FROZEN list of probe ids. |
| `mascaras.json` | Hand-written masks (with a reason) and documented random ids. |
| `comun.py` | Paths, allowed ports, lock, test instance. |
| `contrato_ns.txt` | Contract of the `run.py` namespace: name, type and who uses it. Generated, but it can be edited by hand with a reason. |
| `test_contrato.py` | T10: starts a test instance and checks through ITS console that every name in the contract exists and has the same type. `--extraer` regenerates it; `--candidatos` only lists what it finds in the code. |
| `semilla/sitecustomize.py` | Only with `YO2_PARIDAD_SEMILLA` (set by `comun.Instancia` during parity): `random.seed(N)` in the test instance + `PYTHONHASHSEED=0`. Makes duels (ocgcore seed and brain seed), packs and rock-paper-scissors reproducible without touching the server. |

The development setup also used a `worktree.ps1` script (sparse checkout without `_original/` + junctions); it is not
included.

The tap lives in `fake_client.py` (`YO2_TAP=<folder>`; without the variable it does nothing).

## Running a step

```
python paridad/bateria.py --banda 1000 --etiqueta my-change  # battery + parity (T9) against _out/base.json and the parity base
```

If a change alters the scenarios (it should not), rebuild the base with `python paridad/grabar.py --base`.

**Flag profile**: test instances ALWAYS start with `YO2_PERFIL=vanilla`. To run parity with the other profile:
`YO2_PERFIL_PRUEBA=enhanced python paridad/grabar.py --etiqueta X && python paridad/comparar.py X`. At the time of
writing, enhanced = vanilla in every flag, so this must give IGUAL; once `enhanced=` differs for some flag, it will give
DISTINTO on purpose.

**Shared lock**: `YO2_CERROJO=<path>` makes `comun.Cerrojo` use that file instead of the worktree's `_out/.lock`
(useful when several worktrees run parity on the same PC: only one parity at a time).

## Battery baseline

The last recorded baseline has **71 tests** (`bateria.py --listar`), and a refactored tree gave **IGUAL** to the base in
all of them, T9 parity included. Reference timings: a full pass with 59 tests took ≈ 46 min (2,742 s; T9 parity
≈ 6 min); a full pass with 72 tests and `--sin-paridad` took 57 min.

Tests that were added over time (on top of the original set):
- `T3.partidas_vivo`, `T4.hot_npc_mirar`, `T5.en_uso`, `T8.janken`.
- `T4.sala_tras_pvp`, `T4.hot_sala_tras_pvp`, `T8.repeticion_archivo`, `T8.hot_descarte`, `T8.hot_mano_cadena`,
  `T8.hot_invocaciones`.
- `T10.yo2srv` (the unit tests in `yo2srv/tests`), `T10.yo2srv_vivo` (test instance driven through its console,
  26 checks) and `T10.compilar`.
- `T8.mazo` (`test_mazo.py`, 43), `T8.mazo_vivo` (`test_mazo_vivo.py`, test instance), `T8.hot_yesno_sys`.
- `T8.reglas_torpeza` (`research/ia/test_reglas_torpeza.py`).

`T8.janken` plays duels until it has seen all three rock-paper-scissors outcomes (random): its number of OKs varies
(34 / 58), so it is marked `ok_variable` and compared by return code and failures.

Traps the battery already handles:
- each test gets its own TEMP, `YO2_PRUEBA_DIR` and `YO2_LOGDIR`;
- `T8.repeticion` runs before `T3.sec_visor` (it rewrites `logs/repeticion/pvptraza.log`);
- `T8.gm` runs with `YO2_LOGIN_ESTRICTO=0`;
- `T3.sec_mundo` runs with `YO2_ROOM_LANG_MERGE=0` (the server now merges the rooms of all languages; the test expects
  the old split);
- the `--ref/--rev` of the hot-reload tests (`T6.hot_pvp_carta`, `T8.hot_reserva`, `T4.hot_npc_mirar`,
  `T4.hot_sala_tras_pvp`, `T8.hot_descarte`, `T8.hot_mano_cadena`, `T8.hot_invocaciones`, `T8.hot_yesno_sys`) are
  pinned to the commit before the change they test;
- the outputs TRACKED IN GIT that some tests rewrite (`duelcore/out/*`, `research/ia/brain_out/resultados.json`) are
  restored to their committed version at the end.

**Config registry**: the flag registry in `yo2srv/config.py` must follow `DEFAULT_CFG` (checked by `yo2srv/tests`).
When a default changes or a key is added to `DEFAULT_CFG`, `python -m yo2srv.tests` warns. At the last baseline:
`chain_priority` defaults to `oficial` (options `oficial|evento|spe|None`), `yesno_card` and `lp_resync` are off, and the
newer keys are `chain_event_text` (off), `chain_one_name`, `chain_link_detail`, `yesno_sys_text`, `lp_reconcile`, plus
the extras `ocg.DUEL_RULE` (MR1), `duelo.DECK_CHECK` (environment) and `lobby.ROOM_LANG_MERGE`.

### Known failures in the baseline (5, deterministic)

- `T2.prueba`: the `state.json` tracked in git contains card 3255, which was later removed from `valid_cards`; the
  server purges it on load, and the "root profile intact" check compares against the worktree's `state.json`. In a tree
  whose live state is already purged, it passes.
- `T8.hot_mano_cadena` and `T8.hot_invocaciones`: hot reloads that no longer recognise the code on disk
  (`_chain_priority`, `_yesno` and `_s_SELECT_CHAIN` changed afterwards).
- `T6.hot_pvp_carta`: outdated since the best-of-3 match was added; it takes 770 s because of the timeouts.
- `T8.hot_yesno_sys`: `_yesno` changed after the change it tests.

### Flaky tests (random duel outcomes)

- `T8.pvp_test` failed once (`los dos han jugado`, `límite: 0x6461…`) and then passed 5 of 5 on its own and when the
  battery repeated it.
- `T8.hot_descarte` needs a hand of 8+ cards for its "there is one with 2+ cards" check; once it got a 1-card panel
  (22/23) and, repeated twice on its own, gave 23/23 like the base. If only this one fails, repeat it before blaming the
  change.
- `research/traductor/test_hot_contador_turnos.py` (outside the battery) is unstable: it failed 2 of 2 passes with
  different checks (both with `fin None` = the duel did not finish in time). When it is stable it should enter as
  `T8.hot_contador_turnos`.

### Outside the battery

These are only valid at the commit where they were written, with HEAD = "before the change"; against later trees they
fail by design (the code fingerprint on disk is no longer theirs): `test_hot_lp_cadena`, `test_hot_detalle_cadena`,
`test_hot_aviso_oficial`, `test_hot_regla_duelo`, `test_hot_mazo`. `test_regla_duelo.py` is not in the battery either
(it expects `MR5` as the default and the default is now `MR1`).

## Parity baseline

5 scenarios (`prueba`, `npc`, `pvp`, `kill`, `sondeo` = 7 + 70 + 70 ids), 110 connections, ≈ 266,000 B per pass (rx +
tx), 3 passes with seed 1 (`grabar.py --base`, 18 min; output in `_out/base-grabar.txt`). Every new pass against the
base = IGUAL, and so does the T9 pass inside the battery.

The masked share has been 0.1 %-0.6 % depending on the baseline, with 198-199 of 203 rx/tx ids at 0 %. Per id:

| id | % | where it comes from |
|---|---:|---|
| rx 0x6010 DUELLOG_HEADER | 43.5 | hand-written masks: title `pvpYYYYMMDDhhmmss`, `when` and blob size (time of day); and the tail of `kill` |
| rx 0x6461 | 40.5 | bytes 1-4 = seconds left on the turn clock (`_clk_show`, turn time limit): they depend on the wall clock (179 or 180 depending on the millisecond). Masked by hand |
| rx 0x6613 | 18.2 | only `kill`: the node process dies at a moment that varies; what follows is an unstable tail |
| rx 0x4140 PLAYERDATAUPDATE | 0.3 | only `kill`: byte 46 (result of the interrupted duel) |
| rx 0x6202 | 0.1 | only `kill`: last 0x6202 before the cut, and the tail |

The `0x6461` mask was added after a `pvp` pass came out DISTINTO against a 3-pass base that had not seen it vary.

Without a seed (first attempt) 5.6 % was masked and, worse, a 4th pass gave DISTINTO: 3 passes are not enough to see all
the randomness of a duel (the point where two duels diverge changes from one pass to the next; random bytes can
coincide by chance in all 3). With the seed fixed, two consecutive new passes against the base = IGUAL.

**Sensitivity of the `kill` scenario**: the node process is killed "after 4 decisions", detected by polling
(`_wait_until`); how many more rounds the duel plays before that depends on the load of the PC. One base (3 passes equal
to each other, 160 messages) differed from two later passes (155, two fewer turn cycles) and T9 gave DISTINTO; with
the base rebuilt on an idle PC, new passes give IGUAL. If T9 gives DISTINTO ONLY in `kill` (repeated
`0x6461/0x6611/0x6612`), repeat it on a PC with no other load before blaming the change.

## Namespace contract

`globals()` of `run.py` is the shared space: the console on port 5799 executes each line there, the
`hot_*`/`reload_*`/`*_live.py` scripts are loaded with `exec(..., globals())`, the admin tool (not included) and
`research/*` receive the SAME dict (`NS`, `ns`, `self.env`), and `ocg_test`/`pvp_test` execute the text of `run.py` in
a dict and read from it.

`test_contrato.py --extraer` collects the names they use:
- A: `NS[...]`/`.get`/`in` in the code;
- B: free names, found with `symtable`, of the scripts executed inside the namespace;
- C: console lines (`admin.py "..."`) in notes and comments, and `console(...)` calls in the tests;
- D: flags read by the admin tool, also as paths such as `LP.WATCH` / `MUNDO_2008[spots]`;
- E: a table of names from the refactoring plan.

It then starts a TEST instance like a live one (unencrypted sqlite with the key in its folder, admin web tool on, no
client) and keeps the names that exist, with `type(v).__name__`. The check starts another identical instance and dumps
`{name: type}` to a file through the console (the console only answers "ok"/"error").

First baseline: 167 candidates → **139 names** checked (30 of them flag paths) and 28 absent, commented at the end of
the file and reviewed by hand: variables of the reload scripts themselves (`_RL_LOCK`, `_RL_LAST`, `_HJ_SRC`…),
optional names read with `.get` (`admin_maintenotice`), old patches (`shop_patch.py`, `seq_patch_live.py`) and gaps in
the documentation (`PUERTO`, `datos`). `test_contrato.py` = 139/139 twice in a row (≈ 25 s); with a tampered contract
(non-existent name, non-existent attribute, changed type) it reports the 3 as MAL (bad) and exits with 1. A later
baseline has 140 names (+ `SRV`, added by the `yo2srv` line in `run.py`); the commented absent names grow with the
internal variables of newer `hot_*` scripts and with `_HOT_LAST` (created by `hot.recargar` the first time).

If a change REMOVES a name or changes its type on purpose, edit its line with the reason; if a new consumer appears (a
hot script, a section of the admin tool), run `--extraer` again and review the diff.
