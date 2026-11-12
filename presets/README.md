# Presets

## Domini Antistasi (`domini_antistasi.html`)

Domini Antistasi on the 1400 AD Ostmarschen map, 10 mods (built with the modlist creator):
CBA_A3, Antistasi Ultimate - Mod, gabes1410 AD – Arma Domini Factions, IMS: DEV, Arma Domini - 1400AD,
Arma Domini - The Black Dogs of Burnham, Libertad - SA - Core, Libertad - SA - Horse,
Renaissance Of Warfare, Domini Antistasi. Steam also lists one hidden item (`3780125492`) as
required; nobody can download it and the mission runs without it.

About 3.75 GB, roughly double during setup; `/cleanup_workshop` frees the workshop copy afterwards.

```
/setup              attach domini_antistasi.html
/download
/setup_map map:1400_ad_ostmarschen
/setup_apply restart:true
```

Notes from a real setup:

- The mission is `Antistasi_1400AD.1400_ad_ostmarschen`. On a new campaign the server waits in
  `adminwait`: `#login <ADMIN_PASSWORD>` to set up factions. If the admin login isn't offered at role
  selection, one more `/restart` fixed it.
- Domini needs the Antistasi Ultimate version it targets (12.4.1 at time of writing; the RPT's
  `[DOMINI] Target AU` line shows it). An older `@AntistasiUltimateMod` left from a previous setup
  makes the patch fail (`[DOMINI] INCOMPATIBLE AU SOURCE`) and no role can be picked. Fix: stop the
  server **and** the headless client (it locks the files), then
  `robocopy "<WORKSHOP_BASE>\3020755032" "<SERVER_BASE>\@AntistasiUltimateMod" /MIR /R:2 /W:5`,
  copy its `.bikey` files into `keys\`, start again. Good: `Patch failures: []` in the RPT.
