# How A3Bot works

Background for the [readme](../readme.md): what the bot guarantees, how it finds maps, and what it
writes into `server.cfg`.

## Folder layout

```
A3SAT2\
  ├── install.ps1              interactive installer
  ├── nssm.exe                 optional; falls back to A3SAT's copy
  ├── scripts\python\          A3Bot.py, terrain.py, modstate.py, .env_setup(.example)
  ├── profiles_store\          backup profiles (created automatically)
  ├── logs\                    bot + SteamCMD logs
  ├── last_setup.json          what the applied setup contains, incl. each mod's installed version
  ├── pending_setups.json      in-progress wizard state (survives bot restarts)
  ├── docs\modlist\            the modlist creator page (GitHub Pages)
  └── tools\modlist-worker\    its Cloudflare Worker (see its README)
```

## Design guarantees

- **Mod folders are never deleted, and only ever replaced by a newer copy of the same mod.** A new
  setup only adds folders; different mod sets coexist on disk, and switching setups is a change of
  the `-mod=` string, which is what profiles store. `/setup_apply` and `/update` replace an existing
  folder only when the downloaded copy is provably newer (see
  [Mod versions and updates](#mod-versions-and-updates)), and never when its `meta.cpp` names a
  different workshop item.
- **Replacing is a safe swap.** The old folder is renamed to `@Folder.old`, the new copy is put in
  place, and only then is the old one deleted. If the rename fails (files in use) nothing changes; if
  the copy fails the old folder is put back. Either way the mod is reported as kept, not lost.
- **Mod folders are never replaced under a running server or headless client.** Both lock the mod
  files, so `/setup_apply` and `/update` stop both services first, and abort if they don't stop.
- **`/setup_apply` and `/restore` always back up first.**
- **Saves are copied per profile**, so switching mission mods can't clobber a campaign.
- **Folder-name collisions are impossible**: if two mods sanitize to the same `@Name`, the workshop
  ID is appended.
- **`/cleanup_workshop` only deletes a workshop copy whose `@Folder` exists on the server**, based on
  the mapping recorded at `/setup_apply`.
- **`keys\` is rebuilt** on every `/setup_apply`, `/update` and `/restore`: all `.bikey` files except
  `a3.bikey` are removed and re-copied from exactly the active mods, so stale keys can't let clients
  join with mods the server no longer runs.

## Safety and validation

- Preset uploads must be a `.html` under 2 MB carrying the launcher's `arma:Type` signature.
- `/setup` asks Steam about every mod and skips delisted/private ones (`keep_unavailable:true`
  overrides).
- Discord-supplied values and `.env_setup` passwords are stripped of quotes, semicolons and braces
  before going into `server.cfg`. `SERVER_PASSWORD` may not contain spaces, quotes or backslashes
  (the headless client gets it on the command line); `/setup_apply` refuses an empty or placeholder
  `ADMIN_PASSWORD`.
- `/restore` only accepts existing profile names, and refuses unusable service parameters.
- SteamCMD login failures abort immediately with the re-login command; an empty workshop folder
  counts as not downloaded and is retried.

## Mod versions and updates

`last_setup.json` records, for every `@Folder`, the Steam `time_updated` of the copy that was
actually installed, taken from SteamCMD's `steamapps\workshop\appworkshop_107410.acf` at copy time.
A mod is outdated when Steam's `time_updated` is newer than that, not newer than the last time a
setup was applied: re-applying a setup does not make an old folder current.

`/update` lists the outdated mods, downloads them with SteamCMD (including mods whose workshop copy
exists but is itself outdated), stops the server and headless client, swaps in the new copies,
rebuilds `keys\` and restarts both (`restart:false` skips that). `/setup_apply` does the same swap
for installed folders that are older than the download, and reports which mods it replaced and which
it kept, with the reason.

Setups applied before versions were recorded have none, so after upgrading the bot the first
`/checkupdates` lists every mod once; `/update` or the next `/setup_apply` replaces and records them.
A folder whose installed version is unknown is only replaced when the downloaded copy is Steam's
current one, so the swap can never be a downgrade.

## Map and mission detection

Antistasi 3.x and Ultimate register their missions inside the mod's addons, so the bot reads the
template names out of the mission mod's `.pbo` files (Ultimate offers 75+ maps this way). Sources,
in order: the setup's own mission mod, the `KNOWN_MISSION_TEMPLATES` table in `A3Bot.py`, then
`mpmissions\`. Only the current setup's mods are scanned.

The list shows maps the **mission** supports; the **terrain** must also be in the mod set.
Weather/ambience mods named after a map (Sullen Skies, Livonia Ambience, …) are not terrains.
`/setup_map` checks the `.pbo` headers of the setup's mods for `<world>.wrp` and refuses maps without
a terrain, which would otherwise crash the server at mission start.

## Headless client

The HC gets the **same mod list as the server**. It loads the mission like any client, so if
anything is missing (usually the terrain) it logs `Missing addons detected` and silently never
joins. The bot restarts it 90 s after every `/start` and `/restart`, and `/stop` stops it with the
server (a pending restart is cancelled, so it can't come back while mod folders are replaced). When it works, the server RPT
shows `A3A_fnc_addHC | Headless Client Connected`, and it counts as one player.

## Generated `server.cfg`

| Setting | Value | Meaning |
|---|---|---|
| `gameMode` | `0` | Start a new campaign. `1` loads a save; with no saves players get dropped to the browser |
| `autoLoadLastGame` | `60` | Load the last save 60 s after the first player joins |
| `autoSaveInterval` | `1800` | Save every 30 min |
| `A3A_GCThreshold` | `3600` | Garbage collection every 60 min |
| `A3A_builderPermissions` | `1` | Everyone may build |
| `steamProtocolMaxDataSize` | `8192` | The default 1024 overflows with big mod sets: players join but can't pick a role |
| `BattlEye` | from `BATTLEYE` | `1` by default; `0` in `.env_setup` turns it off |

## What stays manual

Creating the Discord application, the one-time SteamCMD Steam Guard login, and in-game Antistasi
faction setup (on a new campaign the RPT shows `A3A_startupState is "adminwait"`: `#login` as admin).

## Modlist creator

A static page in `docs/modlist/` (served by GitHub Pages from `main` `/docs`) plus a Cloudflare
Worker in `tools/modlist-worker/` that looks up Workshop items and their required items with a
secret Steam key. Removed mods stay removed when more links are added; unavailable items are left
out of the export. The Worker only answers allowed origins, but a script could still use up the
key's daily quota (no secret is exposed). Never commit `.dev.vars`.
