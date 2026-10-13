# YO2 Online Launcher

Fan-made launcher and server for **Yu-Gi-Oh! Online 2**. Join community servers or host your own one for your friends.
Python, Node and the duel engine are bundled — nothing else to install.

## Download

Get the latest `YO2OnlineLauncher-*.zip` from the [**Releases**](../../releases/latest) page.

## Requirements

- Windows 10 or 11 (64-bit).
- Your own copy of the game: yo2.exe Ver.080912 (the 2008 client). Other versions will not work.
  No game files are included in this package.

## How to play

1. Unzip this folder anywhere you like (for example your Desktop). Keep all the files together.
   Do NOT unzip it into the game folder: it is not needed (you pick the game in Settings), and never let it
   overwrite Konami's own "Yo2Launcher.exe" there (Windows treats both names as the same file).
2. Double-click YO2OnlineLauncher.exe. If Windows SmartScreen warns you, choose "More info" > "Run anyway".
3. Open Settings and, under the game folder, press "Change..." and pick the folder that contains your yo2.exe
   (Ver.080912).
4. "Play" (left menu): pick a server and press "Join" (or "Join with code" for a private server). The game opens by
   itself. Log in with any username and password: your account is created the first time.
5. Pre-built decks: the launcher copies 15 ready-made decks (8 official Structure Decks and 7 popular 2008 decks) to
   each character of your game. A new character starts with an automatic deck: go to Deck Edit, press "Load", pick
   one of the 15 and leave Deck Edit. Your own decks are never overwritten, and a deck you delete stays deleted.

## How to host a server

1. Do steps 1-3 above. Keep YO2OnlineLauncher.exe in THIS folder (it needs the "server", "python", "node", "engine" and
   "vendor" folders next to it).
2. Open "Host a server" (left menu), fill in the form and press "START SERVER".
   Every time it starts, the launcher reads the card, shop and NPC data from your own game folder (a few seconds).
3. Public servers appear in everyone's list; private ones need the room code that the launcher shows you.
   You don't need to open router ports or use a VPN.
4. Keep the launcher open while people play. You can play on your own server with "PLAY HERE".
   Use "Stop server" to close it cleanly.
5. The "Manage" tab (inside "Host a server") lets you kick and ban players, manage accounts, send an
   announcement to everyone and make a backup.

## Updates

The launcher updates itself and this whole folder: when a server you see or join has a newer version, it is
downloaded in the background (only the files that changed) and checked against the publisher's signature.
You will see "Update ready" in the corner: press "Restart now", or simply restart the launcher. Your accounts,
decks and settings are never touched. If you host, stop your server before restarting. You can turn
"Auto-update" off in Settings.

Your server's accounts and logs are kept in `%LOCALAPPDATA%\YO2Online\servidor\` on your PC.

If something goes wrong, the logs are in `%LOCALAPPDATA%\YO2Online\` (`yo2-lanzador-*.log` for the launcher, and
`logs\run-py-*.log` for the server). Send them to whoever gave you this package.


## License & disclaimer

This is a non-commercial fan project. Yu-Gi-Oh! is a trademark of its owners; this project is not affiliated with them.
No files from the original game are included. See [THIRD-PARTY.txt](THIRD-PARTY.txt) for the licenses of the bundled components.
