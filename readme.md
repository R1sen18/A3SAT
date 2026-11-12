# A3SAT2 — A3Bot

A Discord bot that runs an Arma 3 dedicated server: start/stop, set up a new mod
set from a launcher preset, update mods, and back up/restore whole configurations.

- **Modlist creator:** https://r1sen18.github.io/A3SAT/modlist/ builds a launcher preset from Workshop links
- **How it works** (design, safety, map detection, `server.cfg`): [docs/how-it-works.md](docs/how-it-works.md)
- **Domini Antistasi** ready-made preset and notes: [presets/](presets/README.md)


## Install

1. Create a Discord bot at https://discord.com/developers/applications (New Application → Bot →
   copy the token) and invite it:
   `https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&scope=bot+applications.commands&permissions=8`
2. Run the installer as Administrator:

   ```powershell
   powershell -ExecutionPolicy Bypass -File "install.ps1"
   ```

   It asks for the bot token, guild ID, authorized user IDs, an optional Steam username (for
   `/download`) and the server/admin passwords, installs SteamCMD and the Python dependencies if
   needed, writes `scripts\python\.env_setup` and registers the `A3Bot` service.
   `-Advanced` lets you change service names and paths; `-BotOnly` skips the SteamCMD part.
   Re-running keeps an existing `.env_setup`.
3. Log in to SteamCMD once (Steam Guard needs it interactively):

   ```powershell
   & "C:\steamcmd\steamcmd.exe" +login YOUR_STEAM_USERNAME +quit
   ```

   The cached login belongs to the Windows account that ran it. If `/download` reports every mod as
   missing, run the bot service under that account (`$nssm` as defined under *Update the bot*):
   `& $nssm set A3Bot ObjectName ".\Administrator" "WINDOWS_PASSWORD"`, then `& $nssm restart A3Bot`.

Settings live in `scripts\python\.env_setup` (see `.env_setup.example`). Restart the bot after editing.
`BATTLEYE=0` turns BattlEye off from the next `/setup_apply`.


## Update the bot

```powershell
$nssm = "C:\steamgames\a3server\A3SAT\nssm.exe"
cd C:\steamgames\a3server\A3SAT2; git pull; & $nssm restart A3Bot
```

First time only, to turn the bot folder into a git checkout (back it up first; `.env_setup`, the
JSON state files, `profiles_store\` and `logs\` are not touched):

```powershell
& $nssm stop A3Bot
git init; git remote add origin https://github.com/R1sen18/A3SAT.git; git fetch origin
git checkout -f -B main origin/main; git branch -u origin/main
& $nssm start A3Bot
```

On "dubious ownership": `git config --global --add safe.directory C:/steamgames/a3server/A3SAT2`.
Without git: unzip `https://github.com/R1sen18/A3SAT/archive/refs/heads/main.zip` over the folder
(all files, not just `A3Bot.py`) and delete `scripts\python\__pycache__`.


## Commands

`/help` lists everything in Discord, and each wizard step names the next command.
Only users in `AUTHORIZED_USER_IDS` can run commands.

| Command | What it does |
|---|---|
| `/start` `/stop` `/restart` | Control the server; reports when joinable and restarts the headless client after 90 s |
| `/status` `/uptime` `/players` | Service state, uptime, live player count |
| `/setup preset:<file>` | Start a new setup from a launcher preset `.html` (optional name, max players) |
| `/download` | Download the setup's mods with SteamCMD (needs `STEAM_USERNAME`) |
| `/setup_script` | Manual alternative: a SteamCMD script to run on the VM |
| `/setup_map [map]` | Pick the mission/map. Without a pending setup it switches the running server's map |
| `/setup_apply [restart]` | Back up, install mods, rebuild `keys\`, write `server.cfg`, update services |
| `/update [restart]` | Check, download and install mod updates, rebuild keys, restart |
| `/checkupdates` | List outdated mods without changing anything |
| `/preset [name]` | Export the server's current mods as a launcher preset for players |
| `/backup <name>` `/profiles` `/restore <folder>` | Snapshot and restore configurations (`/restore` backs up first) |
| `/delete_profile <folder> confirm:true` | Delete a stored profile |
| `/cleanup_workshop` | Delete workshop copies of mods that are already installed |


## Setting up a new mod set

```
/setup preset:<file>         attach the preset (launcher: MODS → PRESET → SHARE → Export to file,
                             or build one with the modlist creator)
/download                    lists the maps this mod set offers when done
/setup_map map:<name>        refuses maps whose terrain isn't in the mod set
/setup_apply restart:true    refuses while mods are missing; always backs up first
```

Players match the server with `/preset` → launcher **MODS → PRESET → IMPORT**.
Passwords are never asked in Discord; they come from `.env_setup`.


## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Commands missing in Discord | Check `logs\a3bot_stderr.log`, token and guild ID in `.env_setup` |
| "No mods found in that file" | Attach the launcher's exported preset, not a saved Steam page |
| `/download` retries, nothing arrives | See `logs\steamcmd.log`: SteamCMD login under another Windows account (install step 3), or the mod is delisted |
| Mods missing after `/setup_apply` | Not downloaded yet: `/download`, then `/setup_apply` again |
| Can't pick a role / "mission may be corrupt" | Client/server mod mismatch: `/preset`, import it, rejoin. Also check the RPT for `Steam Query data overflow` |
| Dropped to the browser with no error | `gameMode = 1` with no saves: set `gameMode = 0` in `server.cfg` |
| Crash right after "Starting mission" | Terrain mod missing (`CfgWorlds/<world>.worldName` in the RPT): pick another map |
| Headless client never joins | `profiles_hc\*.rpt` shows `Missing addons detected`: its mod list must match the server's |
| Backup shows "0 mods" | Service parameters couldn't be read; that profile restores only `server.cfg` and saves |
| `/players` fails | A2S answers only once a mission is loaded |
| NSSM update failed | The bot service needs admin rights (LocalSystem) |
