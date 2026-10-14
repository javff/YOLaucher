# Minimal `_vendor` for the package

What **duelcore** reads at run time (`apps/server/src/duel/data.ts` + `strings.ts`) from `VENDOR_DIR` (in the package,
the folder given by `YO2_VENDOR`). This is **ProjectIgnis data, AGPL-3.0**: it ships unchanged, with its
`COPYING`/licence file, and the package states where it comes from (see `THIRD-PARTY.txt`).

Measured on the `_vendor/` of a development checkout.

| File / folder | Size | Files | sha256 / version | Read by |
|---|---:|---:|---|---|
| `cards.cdb` | 7,958,528 B (8.0 MB; 1.8 MB zipped) | 1 | `3530f406ba92b0f8d5699aa107e95158a1f1b4816f8f5ca2dd82c490301ef632` (BabelCDB) | `CardDB` (node:sqlite, read-only): 14,759 cards. Also `tools/build_cards.py` (`_bc.CDB`) from Python via `cardmap` |
| `strings.conf` | 54,252 B | 1 | `b9b9631b562016cb4768aa023641131fd71d393fab81e93dece28a93b9c3c47e` | `SystemStrings` (texts of options/hints) |
| `CardScripts/` **without `.git`** | 48,695,636 B (48.7 MB) | 22,805 (22,793 `.lua`) | ProjectIgnis/CardScripts `37f270dc` (2026-10-05, "added new rush cards") | `ScriptStore`: walks the WHOLE tree at startup and keeps each `.lua` by name (`official/` wins on duplicates) |
| **Total** | **≈ 56.7 MB** | | | |

Compressed (zip deflate level 9, per file): **≈ 24.9 MB** for the three together. With 7z or a solid zip it would be
considerably smaller (many `.lua` files are nearly identical).

## CardScripts by folder (without `.git`)

| Folder | Files | Size |
|---|---:|---:|
| root (`constant.lua`, `utility.lua`, `proc_*.lua`, README, COPYING…) | 31 | 576,267 B |
| `official/` | 13,541 | 31,495,403 B |
| `unofficial/` | 5,530 | 10,561,450 B |
| `rush/` | 3,129 | 4,642,015 B |
| `pre-release/` | 135 | 520,459 B |
| `skill/` | 174 | 434,784 B |
| `goat/` | 191 | 338,463 B |
| `pre-errata/` | 68 | 121,151 B |
| `.github/` | 6 | 5,644 B |
| `.git/` (**NOT included**) | — | 20,975,519 B |

- **Do not copy `.git/`** (21 MB): besides, `ScriptStore` would walk all of it on every startup for nothing.
- The package ships the **full CardScripts** (without `.git`). For reference, in case it is ever trimmed: of the 3,177
  YO2 cards with a passcode (`research/ia/cards_yo2.csv`), 2,530 have a script and they are **all** in `official/`
  (2,529) and `pre-errata/` (1); the rest are normal monsters without a script. Root + `official/` + `pre-errata/`
  (≈ 32.2 MB) would be enough for the YO2 cards, but one script can load another and this has not been tested: if it is
  ever trimmed, run the battery and `verificar_duelcore.py` with that VENDOR_DIR first.

## How it is passed

- duelcore: environment variable `VENDOR_DIR=<folder>` (see DUELCORE.md). Without it, it does not start in the package.
- Python (`cardmap` → `tools/build_cards.py`): by default it computes `CDB` from the repository root
  (`_vendor/cards.cdb`); with `YO2_VENDOR` set it reads the same `cards.cdb` from that folder (see RUTAS.md).
- None of this is downloaded on first start: it is inside the package zip.
