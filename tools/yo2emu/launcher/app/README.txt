Yu-Gi-Oh! Online 2 Launcher
===========================

What it is
----------
YO2OnlineLauncher.exe is a small desktop app to play Yu-Gi-Oh! Online 2 on community servers (DuelCards): it finds public
servers, connects you through an encrypted tunnel and opens the game for you. No accounts to create, no router ports
to open, no VPN. The same window also lets you host your own server.

You need your own copy of the game: yo2.exe Ver.080912 (the 2008 client). Other versions will not work.
The launcher looks for it next to itself, in the folder you picked last time, and in Konami's default install folder.
If you have several game folders (for example an older client such as Ver.060419 next to the 2008 one), it
picks the one that is Ver.080912. If it can't find it, open Settings > Game folder > "Change..." and pick the
folder that contains yo2.exe.

Where to put the launcher: any folder you like (for example your Desktop or Documents). It does NOT need to be in the
game folder: pick the game in Settings > Game folder. Never copy it over Konami's own "Yo2Launcher.exe" in the game
folder (Windows treats both names as the same file); if you replaced it by mistake, reinstall the game or copy that
file back from another installation.

How to join a server
--------------------
1. Double-click YO2OnlineLauncher.exe. The "Play" tab lists public servers (the P2P network takes a few seconds to answer).
2. Pick one and press "Join". Wait for the steps to finish: the game opens by itself.
   - "Direct P2P" means a direct connection between the two PCs (lowest ping).
   - "Tor" is the fallback when direct connection isn't possible; it works but adds delay. The first time Tor is
     used on a PC it downloads the Tor network directory and can take several minutes. That is normal.
3. Log in with any username and password you like: your account is created the first time. Use the same ones to
   come back to your character (unless the host chose "Only accounts I create").
4. Keep the launcher open (minimized is fine) while you play: it holds the connection. Closing the game keeps you
   connected; use "Open the game" to start it again, or "Disconnect" to leave.

Private servers: press "Join with code" and paste the room code the host gave you (it looks like
YO2-XXXXXX-XXXXXX-... with 12 groups of 6 characters). The code is secret: don't post it publicly.

How to host a server
--------------------
1. Open the "Host a server" tab, give it a name and press "START SERVER". Your PC acts as the server for as long as
   the launcher stays open. You can also play from the same PC with "Play here".
2. Hosting needs the server software (run.py) and Python on your PC: YO2OnlineLauncher.exe must sit inside the project's
   tools\yo2emu folder, next to run.py. "Settings > Check installation" tells you what is missing.
3. Public or private:
   - Public: the server appears in everyone's list. Players join with one click.
   - Private: it is NOT listed. Players need the room code ("Copy code" in the panel). Share it only with people
     you trust.
4. Rules: "Vanilla 1:1" is the 2008 game as it was; "Enhanced" adds the optional improvements.
   Accounts: "Open registration" creates an account the first time someone logs in; "Only accounts I create"
   needs you to create them in the "Manage" tab (Accounts) while the server is running.
5. "Stop server" shuts the server down in an orderly way (it saves and exits; up to 30 s). If you close the
   launcher while the server is running, it asks first.

Everything you type in the server form (name, description) is shown to other players as you wrote it.

Windows Firewall prompt
-----------------------
The first time you join or host, Windows Firewall may ask whether to allow YO2OnlineLauncher.exe. You can press "Allow"
(it improves direct connections) or cancel (it keeps working, maybe through Tor).

If something fails
------------------
- "Windows protected your PC" (SmartScreen): "More info" > "Run anyway".
- Antivirus blocks or deletes the launcher, frida-inject.exe or tor.exe: they redirect the game and connect it to the
  host. Allow the folder %LOCALAPPDATA%\YO2Online.
- "Technical details" on the error screen has the exact error; send it, with the log, to whoever helps you.
- Settings > Diagnostics > "Check installation" checks the game, its version, WebView2 (Microsoft's web runtime, it
  ships with Windows 10/11), the local game ports, Python and run.py.

Where the logs are
------------------
%LOCALAPPDATA%\YO2Online (Settings > Diagnostics > "Open logs folder"):
  yo2-lanzador-<date>.log     the launcher (the newest 15 are kept)
  logs\run-py-<date>.log      the game server, when you host from this launcher
Paste that folder path into the Windows Explorer address bar to open it.
Your settings (game folder, hidden servers) are in lanzador.json in the same folder.

Updates
-------
The launcher updates itself when you join a server that offers a newer version signed by the project (and only then).
To turn it off: Settings > Auto-update.
