# duelcore as a single file (`duelcore.mjs`)

The duel engine (ocgcore in Node, `tools/yo2emu/duelcore/`) is launched in development with `tsx` from `node_modules`.
For the self-contained package it is bundled into **a single `duelcore.mjs`** that only needs `node.exe`.

## Building it

```
python tools\yo2emu\paquete\bundle_duelcore.py            # -> tools\yo2emu\paquete\_build\duelcore.mjs
python tools\yo2emu\paquete\verificar_duelcore.py         # .mjs against tsx: same duels + the duelcore/ tests
```

- It uses the esbuild **already installed** by pnpm (`node_modules\.pnpm\@esbuild+win32-x64@0.28.2\...\esbuild.exe`).
  It downloads nothing. It takes < 1 s.
- `_build/` is in `.gitignore`: the `.mjs` is not committed (it is regenerated).
- esbuild options: `--bundle --platform=node --format=esm --target=node22`. Nothing else.

## What it contains and what it does not

| | |
|---|---|
| Size | **2,630,167 B** (2.6 MB; ~0.9 MB zipped). sha256 of the measured build: `d27c9987…2a52b87` (it changes if duelcore.mts or apps/server/src/duel change) |
| Inside | `duelcore.mts` + `apps/server/src/duel/{engine,data,bot,view,strings}.ts` + `ocgcore-wasm` 0.1.2 with **the WASM core embedded** (the library already ships it as base64 in its `dist/`: no `.wasm` file is needed next to it) |
| Imports left outside | only Node modules: `node:crypto`, `node:fs`, `node:path`, `node:readline`, `node:sqlite`, `node:url`. No `require()` and no npm packages (bundle_duelcore.py checks this and fails if they appear) |
| Outside (read at run time) | `cards.cdb`, `strings.conf` and `CardScripts/` from **`VENDOR_DIR`** (see VENDOR.md) |

## How it is launched

```
set VENDOR_DIR=<folder with cards.cdb, strings.conf and CardScripts\>
node --disable-warning=ExperimentalWarning <path>\duelcore.mjs
```

- **Node ≥ 22.13** (`node:sqlite` without a flag since 22.13; before that it required `--experimental-sqlite`). Tested
  with node **v24.13.1**. The minimum version has not been tested with a Node 22 build (nothing was downloaded for
  this); it remains to be checked with the `node.exe` that ships in the package.
- `--disable-warning=ExperimentalWarning` only removes the `node:sqlite` warning on stderr; it works the same without
  it.
- **`VENDOR_DIR` is required** in the package: without it, `data.ts` looks for
  `<folder of the .mjs>\..\..\..\..\_vendor`, which does not exist outside the repository, and the process dies at
  startup without sending `ready` (on stderr: `Error: unable to open database file`, `ERR_SQLITE_ERROR`: it cannot find
  cards.cdb).
- The working directory does not matter (the `.mjs` uses no paths relative to the cwd): tested by copying it alone to
  a temporary folder and launching it from there (`ping`, `cardinfo` OK).
- Same protocol as with tsx (line-delimited JSON over stdin/stdout; `duelcore/README.md`). First event
  `{"ev":"ready","cards":14759,…}` in ~3-5 s.

## How the server uses it (`YO2_DUELCORE`)

In `duelcore/duelcore.py`:

1. `_find_launch()`: if `YO2_DUELCORE=<path to .mjs>` is set → `[node, "--disable-warning=ExperimentalWarning", <mjs>]`,
   with `node` = `YO2_NODE` or `shutil.which("node")`. Otherwise, tsx as in development.
2. **`cwd`**: in development `DuelCore.__init__` does `Popen(..., cwd=str(SERVER))` (`apps/server`). In the package
   that folder does not exist and `Popen` would fail with `FileNotFoundError` before node starts. With `YO2_DUELCORE`,
   `cwd=dirname(mjs)` is used.
3. **Environment**: `VENDOR_DIR = YO2_VENDOR` is passed to the child process (`env=dict(os.environ, VENDOR_DIR=...)`)
   when `YO2_VENDOR` is set, and also with a `.mjs` when `VENDOR_DIR` has not been set by hand (see RUTAS.md).

`verificar_duelcore.py` does the same (it replaces `duelcore._find_launch` with
`[node, "--disable-warning=ExperimentalWarning", mjs]` and sets `VENDOR_DIR`); with the development `cwd` it works in a
checkout because `apps/server` exists.

## Verification (node v24.13.1)

`verificar_duelcore.py` copies the `.mjs` to a temporary folder **outside the repository** and:

- Plays the same 12 duels (fixed seeds, each side going first, MR5 and MR1) + 1 duel in brain mode (both players from
  Python) + one `cardinfo` query, first with tsx and then with the `.mjs`, and compares **the full sequence of events**
  (removing only `ms`, `load_ms`, `pid`): **14/14 IGUAL (identical), 4,526 events**. Same cards loaded (14,759).
- Runs the `duelcore/` tests with the `.mjs`: `test_bucle.py 20` (20 duels + reproducibility) **OK**,
  `test_errores.py` **OK**, `test_cerebro.py` **OK**.
- Also, by hand: `ocg_test.py` (loading `run.py` + a full duel against NPC 2 with the translator) with the `.mjs`:
  normal end of duel, `avisos traductor: []`, same as with tsx.
- Startup: the `.mjs` loads in ~5 s and tsx in ~9 s with the test battery running at the same time (without load,
  duelcore README: 3.1-3.5 s with tsx).
