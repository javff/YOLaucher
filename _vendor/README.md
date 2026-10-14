# `_vendor/` — third-party card data (not included)

The duel engine and the card mapping read these files **unmodified** at run time. They belong to the EDOPro /
ProjectIgnis projects (AGPL-3.0) and are not part of this repository. Put them here (the server's default) or anywhere
else and set `YO2_VENDOR=<folder>`.

| Path | Where from | Tested version |
|---|---|---|
| `CardScripts/` | `git clone https://github.com/ProjectIgnis/CardScripts.git` | commit `37f270dc813a12d123707ae255f2bda7922999c4` (2026-10-05) |
| `cards.cdb` | [ProjectIgnis/BabelCDB](https://github.com/ProjectIgnis/BabelCDB), `cards.cdb` | sha256 `3530f406ba92b0f8d5699aa107e95158a1f1b4816f8f5ca2dd82c490301ef632` (14,759 cards) |
| `strings.conf` | [ProjectIgnis/Distribution](https://github.com/ProjectIgnis/Distribution), `config/strings.conf` | sha256 `b9b9631b562016cb4768aa023641131fd71d393fab81e93dece28a93b9c3c47e` |

```
git clone https://github.com/ProjectIgnis/CardScripts.git _vendor/CardScripts
git -C _vendor/CardScripts checkout 37f270dc813a12d123707ae255f2bda7922999c4
curl -L -o _vendor/cards.cdb    https://raw.githubusercontent.com/ProjectIgnis/BabelCDB/master/cards.cdb
curl -L -o _vendor/strings.conf https://raw.githubusercontent.com/ProjectIgnis/Distribution/master/config/strings.conf
```

`cards.cdb` and `strings.conf` change often upstream; newer copies normally work. If duels misbehave, compare with the
tested hashes above (`certutil -hashfile _vendor\cards.cdb SHA256`).

The engine indexes the whole `CardScripts/` tree at start-up; the `.git` folder is not needed at run time (the launcher
package ships the tree without it). Details: `tools/yo2emu/paquete/VENDOR.md`.
