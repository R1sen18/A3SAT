# A3SAT2 — A3Bot

One Discord bot for the whole Arma 3 dedicated server: service management,
automated server setup from an Arma 3 launcher mod preset, and backup/restore
profiles of complete configurations.

The folder is **portable** — zip it, unzip it anywhere, run `install.ps1`.

```
A3SAT2\
  ├── install.ps1              ← interactive installer
  ├── readme.md
  ├── nssm.exe                 ← optional; falls back to A3SAT's copy
  ├── scripts\python\
  │   ├── A3Bot.py             ← the bot
  │   └── .env_setup.example
  ├── profiles_store\          ← backup profiles (created automatically)
  ├── logs\                    ← bot + SteamCMD logs
  ├── last_setup.json          ← what the applied setup contains
  └── pending_setups.json      ← in-progress wizard state
```


## Installation

1. **Create a Discord application** (the only manual step):
   https://discord.com/developers/applications → New Application → Bot → copy the
   token. Invite it to your server:
   `https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&scope=bot+applications.commands&permissions=8`

2. **Run the installer** as Administrator:

   ```powershell
   powershell -ExecutionPolicy Bypass -File "install.ps1"
   ```

   It asks for: **bot token, guild ID, authorized user IDs**, the **Steam
   username** for automated downloads (optional — Enter skips it), and the
   **server + admin passwords** that go into `server.cfg`. Service names and
   paths use standard values (`Arma3Server`, `Arma3HeadlessClient`,
   `C:\steamgames\a3server`, `C:\steamcmd`) — run with `-Advanced` to change
   them:

   ```powershell
   powershell -ExecutionPolicy Bypass -File "install.ps1" -Advanced
   ```

   Use `-BotOnly` to install just the bot (dependencies, `.env_setup`,
   service) and skip the SteamCMD check/install — e.g. when the server and
   SteamCMD are managed elsewhere:

   ```powershell
   powershell -ExecutionPolicy Bypass -File "install.ps1" -BotOnly
   ```

   Then it:

   - validates SteamCMD is present — offers to **download and install it**
     automatically if missing
   - installs the Python dependencies
   - writes `.env_setup`
   - registers the `A3Bot` NSSM service with logging and starts it

   Re-running the installer keeps an existing `.env_setup`; delete the file to
   reconfigure.

3. **One-time SteamCMD login** (needed for `/download`) — Steam Guard requires
   an interactive prompt once:

   ```powershell
   & "C:\steamcmd\steamcmd.exe" +login YOUR_STEAM_USERNAME +quit
   ```

   > The cached session belongs to the **Windows account that ran it**. The bot
   > service runs as `LocalSystem` by default, which has its own (empty) cache —
   > if `/download` reports every mod as still missing, that's why. Either run
   > the login as SYSTEM, or run the bot service under the account that holds
   > the cached login:
   >
   > ```powershell
   > & "C:\steamgames\a3server\A3SAT\nssm.exe" set A3Bot ObjectName ".\Administrator" "WINDOWS_PASSWORD"
   > & "C:\steamgames\a3server\A3SAT\nssm.exe" restart A3Bot
   > ```


## Commands

`/help` shows all commands and the typical workflows directly in Discord —
every wizard step also tells you what the next command is.

### Server management

| Command | Description |
|---|---|
| `/start` | Start the server; notifies when joinable; restarts HC after 90s |
| `/stop` | Stop the server |
| `/restart` | Restart the server (waits for full stop); notifies when joinable; restarts HC after 90s |
| `/status` | 🟢 running / 🔴 stopped |
| `/uptime` | How long the server process has been running |
| `/players` | Live player count via A2S query |

### Setup wizard

| Command | Description |
|---|---|
| `/setup` | Attach a launcher preset `.html` (+ optional name/max players) — parses mods, estimates disk space |
| `/download` | **Automated** SteamCMD download of pending mods + updates (needs `STEAM_USERNAME`) |
| `/setup_map [map]` | Choose the mission/map — dropdown for small mod sets, type-to-search autocomplete for large ones (Antistasi Ultimate offers 75+). Without a pending setup it changes the map of the **running** server: backup → stop → rewrite `server.cfg` → start |
| `/setup_script` | Manual alternative: generate the SteamCMD download script |
| `/setup_apply [restart]` | Auto-backup → copy mods → rebuild keys → write server.cfg → update services; optional immediate restart |
| `/cleanup_workshop` | Delete workshop download copies of installed mods to free space |

### Mod updates

| Command | Description |
|---|---|
| `/update` | **One command:** check → download → install → rebuild keys → restart (`restart:false` to skip) |
| `/checkupdates` | Read-only check: lists mods with updates without changing anything |

Updating is a single command:

```
/update    →  1. checks Steam for updates to the installed mods
              2. downloads them via SteamCMD (with retries)
              3. re-copies the updated mods onto the server
              4. rebuilds the keys\ folder
              5. restarts the server (pass restart:false to skip)
```

`/checkupdates` is the read-only version — it only lists what's outdated.
No background polling; checks happen on demand (and `/setup_script` includes
outdated mods in its generated script).

Without `STEAM_USERNAME` the download step is skipped — `/update` then lists
the mods as "not in workshop cache"; run the `/setup_script` output on the VM
and `/update` again.

`/update` only ever replaces a mod folder with a newer copy of the *same* mod.

### Sharing

| Command | Description |
|---|---|
| `/preset [name]` | Export the server's currently loaded mod list as an Arma 3 launcher preset `.html`, attached to the message |

The generated preset contains only the mods actually in the server's `-mod=`
line, so players can import it (**MODS → PRESET → IMPORT**) and match the server
exactly. Display names come from the applied setup; for setups applied before
names were recorded they are fetched from Steam on first use.

### Backup & restore

| Command | Description |
|---|---|
| `/backup <name>` | Snapshot NSSM params (server + HC), server.cfg, saves |
| `/profiles` | List stored profiles |
| `/restore <folder>` | Restore a profile (autocomplete; auto-backs up current state first) |
| `/delete_profile <folder> confirm:true` | Permanently delete a stored profile (including its saves) |

Only users in `AUTHORIZED_USER_IDS` can execute commands.


## The setup flow

```
/setup preset:<file>       → attach your exported launcher preset .html
                             (optional: name, max_players)
                           → bot lists parsed mods + per-drive disk estimate
                           → lists the available maps if the mods are already
                             on disk
/download                  → bot runs SteamCMD with retries
                             (manual route: /setup_script on the VM)
                           → prints the full list of maps this mod set offers
/setup_map map:<name>      → type-to-search; vanilla maps are suggested first
/setup_apply restart:true  → 1. asks for the mission/map (dropdown) if not
                                chosen yet — options come only from the mods
                                THIS setup will load, since it goes into
                                server.cfg; re-run after choosing
                             2. refuses if mods are still missing (no half-config)
                             3. auto-backup of current setup (always)
                             4. copies downloaded mods to @Folders
                             5. rebuilds keys\ to match the new mod set
                             6. writes server.cfg (passwords from .env_setup)
                             7. updates NSSM params for server + HC
                             8. restarts and reports when joinable
```

Server and admin passwords are **never asked via Discord** — they live in
`.env_setup` (`SERVER_PASSWORD`, `ADMIN_PASSWORD`), queried by the installer.

Exporting a preset from the Arma 3 launcher: **MODS tab → PRESET (top right) →
SHARE → Export to file**.


## Design guarantees

- **Mod folders are never deleted or overwritten.** A new setup only *adds*
  folders; different mod sets coexist on disk. Switching between setups is just
  a change of the `-mod=` string — which is exactly what profiles store.
- **`/setup_apply` and `/restore` always auto-backup first** — you cannot lose
  a working configuration by applying something new.
- **Saves are fully copied per profile** (a few MB). This protects campaigns
  when switching mission mods that write to the same save files.
- **Folder-name collisions are impossible** — if two mods sanitize to the same
  `@Name`, the workshop ID is appended.
- **`/cleanup_workshop` only deletes a workshop copy if the mod's `@Folder`
  exists on the server**, based on the mapping recorded at `/setup_apply`.


## Headless client mods

The HC gets the **same mod list as the server**. It's tempting to trim it to
"just CBA + ACE + the mission mod", but the HC loads the mission like any
client: if anything the mission references is missing — most commonly the
**terrain mod** — it fails with

```
Missing addons detected: CUP_Editor_A2_Roads_Config
You cannot play/edit this mission; it is dependent on downloadable content that has been deleted.
```

and silently never joins. The server keeps running, `Players` stays one lower
than expected, and no AI offloading happens.

The bot restarts the HC 90 seconds after every `/start` and `/restart` so it
reconnects once the mission is loaded.

To check the HC is really working, look for
`A3A_fnc_addHC | Headless Client Connected: [N]` in the server RPT, or run
`/status` — it reports both services plus the player count (the HC counts as
a player).


## Map/mission detection

Antistasi 3.x and Ultimate register their missions **inside the mod's addons**
rather than shipping `mpmissions` files, so the bot reads the template names
directly out of the mission mod's `.pbo` binaries. That works for any Antistasi
fork without maintaining a hardcoded list — Ultimate exposes 75+ maps this way.

Sources, in order:

1. **Mission-mod scan** — `.pbo` files of the setup's own Antistasi mod
   (workshop cache or installed folder), matched for `Mission.World` names
2. **`KNOWN_MISSION_TEMPLATES`** in `A3Bot.py` — fallback table for mods that
   can't be scanned
3. **`mpmissions\` scan** — any mission `.pbo` placed there manually

Only the mods of the current setup are scanned, so unrelated mods in the
workshop cache never appear in the list.

> The list shows maps the **mission** supports. The matching **terrain** mod
> must also be in your mod set — vanilla maps and CUP Terrains maps are covered
> by a typical Antistasi preset; maps like Namalsk or S.O.G. terrains need their
> own mods. Weather/ambience mods (e.g. Sullen Skies, Livonia Ambience,
> Ambient Animals) are **not** terrains, even when named after one.

Choosing a map whose terrain is missing makes the server crash at mission init
(`No entry 'bin\config.bin/CfgWorlds/<world>.worldName'` followed by an
ACCESS_VIOLATION). To prevent that, `/setup_map` verifies the terrain exists —
it looks for `worlds\<name>\<name>.wrp` in the installed mods' `.pbo` headers —
and refuses the selection if no mod provides it.

The generated `server.cfg` includes these Antistasi params:

| Param | Value | Meaning |
|---|---|---|
| `gameMode` | `0` | Start a **new** campaign (`1` = load a save — only useful once saves exist) |
| `autoLoadLastGame` | `60` | Auto-load the last save 60 s after the first player joins |
| `autoSaveInterval` | `1800` | Auto-save every 30 min |
| `A3A_GCThreshold` | `3600` | Garbage collection every 60 min |
| `A3A_builderPermissions` | `1` | All players may build |

> `gameMode = 1` on a server with no saves leaves the mission waiting to load
> something that doesn't exist — players connect, see the map briefly and get
> dropped back to the browser with no error in either log. Switch it to `1`
> only after a campaign has been saved.

It also sets `steamProtocolMaxDataSize = 8192`. With large mod sets the default
(1024 bytes) overflows the Steam query response, so clients receive an
incomplete mod/signature list — the symptom is joining successfully but being
unable to pick a role, with the mission reported as corrupt. The server logs
`Warning: Steam Query data overflow` when this happens.


## Safety & validation

- **Preset uploads are validated**: must be a `.html` file under 2 MB carrying
  the launcher's `arma:Type` signature (`preset` or `list` export) — arbitrary
  files are rejected.
- **Delisted mods are detected up front**: `/setup` asks Steam about every
  parsed mod and skips ones that no longer exist (removed or private), instead
  of failing later during download. `keep_unavailable:true` overrides this.
- **Service parameters are validated when captured**: a backup that couldn't
  read the real launch parameters is flagged, and `/restore` refuses to write
  unusable parameters into a service.
- **Config injection is blocked**: Discord-supplied values (server name) and the
  passwords from `.env_setup` are stripped of quotes/semicolons/braces before
  being written into `server.cfg`.
- **`/restore` only accepts profile names that exist in the store** (no path
  traversal), and profile autocomplete only answers authorized users.
- **SteamCMD login failures abort immediately** with the exact command to re-login
  interactively, instead of silently burning retries.
- **Interrupted downloads are detected**: an empty workshop folder counts as
  not downloaded and gets retried.
- **The setup wizard survives bot restarts** — pending setups are persisted to
  `pending_setups.json` and reloaded when the service comes back up.


## Stale key protection

`keys\` is **rebuilt** (not just added to) on every `/setup_apply`,
`/update`, and `/restore`: all `.bikey` files except Arma's own `a3.bikey`
are removed and re-copied from exactly the mods in the active `-mod=` list.
Without this, keys from previously loaded mods would accumulate and let clients
join with mods the server no longer runs.


## What stays manual

- Discord application/token creation (step 1 above)
- One-time SteamCMD Steam Guard login on the machine (then `/download` works)
- In-game Antistasi faction/settings setup


## Troubleshooting

- **Commands don't appear in Discord** — check `logs\a3bot_stderr.log`; verify
  token and guild ID in `scripts\python\.env_setup`.
- **"No mods found in that file"** — attach the launcher's *exported preset*
  HTML, not a saved Steam page.
- **Mods reported missing after `/setup_apply`** — they aren't downloaded yet.
  Run `/download` (or the `/setup_script` command on the VM), then
  `/setup_apply` again.
- **`/download` retries but nothing downloads** — check
  `logs\steamcmd.log` for the real reason. Most common causes: the cached Steam
  login belongs to another Windows account (see installation step 3), or the
  mod is delisted ("File Not Found" — it cannot be downloaded by anyone).
- **A backup shows "0 mods"** — the service parameters could not be read; that
  profile only covers `server.cfg` and saves. `/restore` still restores those
  two and tells you the mod list was skipped, so set the `-mod=` parameters
  yourself if that profile used a different mod set. Take a fresh `/backup`
  once the setup is good again.
- **NSSM update failed** — the bot service needs admin rights (LocalSystem).
- **`/players` fails** — A2S only responds once a mission is loaded.
- **Joined but can't select a role / "mission may be corrupt"** — usually the
  Steam query overflow above (`steamProtocolMaxDataSize`), or a client/server
  mod mismatch. Run `/preset`, import it in the launcher, and rejoin.
- **Server crashes right after "Starting mission"** — the map's terrain mod is
  missing. The RPT shows `No entry .../CfgWorlds/<world>.worldName`. Pick a map
  you have the terrain for with `/setup_map`, which now blocks this.
- **HC never joins (`Players` one short, no `Headless Client Connected`)** —
  check `profiles_hc\*.rpt` for `Missing addons detected`. Its mod list must
  match the server's; setups applied by older bot versions used a reduced list.
- **Connect succeeds, then you're dropped back to the browser with no error** —
  check for `No usable saves found for automatic loading` in the server RPT
  together with `gameMode = 1` in `server.cfg`. Set `gameMode = 0` to start a
  new campaign.
- **Restored profile doesn't load the old campaign** — the mission mod from
  that profile must still be installed (mod folders are never deleted, so it
  should be); restart the server after restoring.
