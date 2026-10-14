# Contributing

Thanks for helping! A few ground rules keep the project healthy:

- **No game files.** Never commit anything extracted from the client (images, sounds, text, `.dat`/`.bin` files,
  tables derived from them) or the client itself. The server reads what it needs from each host's own copy.
- **No personal data.** No real account names, IP addresses, logs or `state.json` from a real server in issues or
  pull requests; anonymise them first.
- **1:1 first.** The `vanilla` profile tries to behave exactly like the 2008 servers. A change that differs from the
  original (or guesses where the original is unknown) goes behind a flag in `tools/yo2emu/yo2srv/config.py`, marked
  with its origin, and is listed in `yo2srv/BANDERAS.md` (regenerate it with `python -m yo2srv.gen_banderas` in `tools/yo2emu`; it is
  Spanish and generated, the English summary is `docs/FLAGS.md`).
- **Tests.** Run `python -m yo2srv.tests` (in `tools/yo2emu`) and, for launcher changes, `go vet ./... && go test ./...`
  (in `tools/yo2emu/launcher`). For protocol or duel changes, also a scripted session such as
  `python fake_client.py pvp --offset 300`.
- **Style.** Python 3.12 standard library only for the server; files UTF-8 without BOM; PowerShell scripts in ASCII
  (Windows PowerShell 5.1 reads BOM-less files as ANSI). Comments may be in English or Spanish; player-facing text is
  English only (`launcher/app/frontend/dist/i18n/en.json`).
- **Security issues** (tunnel, handshakes, signatures, updates): please report them privately to the maintainers
  instead of opening a public issue.

Translating Spanish code comments to English is a very welcome first contribution. Keep the `NOTES.md` files in sync
with the code they describe when you change behaviour.
