# Modlist creator + Domini Antistasi support — design

Date: 2026-11-12
Branch: `feature/modlist-creator-domini` (from `R1sen18/A3SAT` `main` @ `40e2d69`)
PR target: `R1sen18/A3SAT` `main` (the fork, not upstream `fosytherrac/A3SAT`)

## Why

The owner wants to start an Antistasi campaign with **Domini Antistasi**
(Workshop 3806463703), an Antistasi Ultimate extender built on Arma Domini –
1400AD. A3Bot's setup flow starts from an Arma 3 launcher preset `.html`, and
building that preset means finding every required mod by hand: the patch alone
needs 9 other mods, spread over two levels of dependencies.

The owner wants a **modlist creator on GitHub**: paste Workshop links, have the
required mods **added automatically**, adjust the list, export a preset. That
preset then goes to the Arma launcher (players) and to `/setup` (server).

## Goals

1. A web page, hosted on GitHub Pages from the fork, where you paste Workshop
   links/IDs and get the full mod list including all required mods,
   recursively.
2. Export as an Arma 3 launcher preset `.html` that both the launcher (MODS →
   PRESET → IMPORT) and A3Bot's `/setup` accept unchanged.
3. A small Cloudflare Worker that performs the Steam lookup, because the page
   cannot (see Constraints).
4. Domini Antistasi runs on the server through the existing flow. A ready
   preset for it ships in the repo.
5. The readme explains how to update the bot on the VM, and how to deploy and
   use the creator.

## Non-goals

- No changes to A3Bot's setup flow for the creator; the bot keeps consuming
  preset files as today.
- No server-side storage of lists, no accounts, no login.
- No DLC requirement detection (Steam's API doesn't report it reliably).
- No client-side mod syncing or launcher automation.
- No support for other games than Arma 3 (app 107410).

## Anti-goals (would be a failure even if it "works")

- The Steam API key appearing anywhere public: repo, page source, a
  response, or a log line.
- The Worker usable as a general proxy: it does exactly one kind of Steam
  call with numeric IDs, nothing else.
- An exported preset that the launcher or `/setup` rejects, or that silently
  drops a mod.
- A build step, framework or package manager needed just to edit the page.

## Facts established during brainstorming

- `ISteamRemoteStorage/GetPublishedFileDetails` is keyless but has **no
  dependency info** and sends **no CORS headers**, so a browser page can't call it.
- `IPublishedFileService/GetDetails` with `includechildren=true` returns
  required items, but it **requires a Steam Web API key** (keyless call returned
  `401 Unauthorized`). Required items come back as `children`. The builder
  must confirm the exact field shape with a real key before relying on it.
- Domini Antistasi's full closure (verified on the Workshop pages): 10 mods,
  about 3.75 GB, all public, no further nested requirements:

  | ID | Title | Required by |
  |---|---|---|
  | 450814997 | CBA_A3 | most of the list |
  | 3510959070 | IMS: DEV | Arma Domini, Black Dogs, patch |
  | 2817145194 | Libertad - SA - Core | Libertad Horse, patch |
  | 2817149713 | Libertad - SA - Horse | patch |
  | 3779310113 | Arma Domini - 1400AD | Black Dogs, patch |
  | 3780409647 | Arma Domini - The Black Dogs of Burnham | patch |
  | 3816263034 | gabes1410 AD – Arma Domini Factions | patch |
  | 3794701829 | Renaissance Of Warfare | patch |
  | 3020755032 | Antistasi Ultimate - Mod | patch |
  | 3806463703 | Domini Antistasi | (root) |

- A3Bot's preset format is `PRESET_HTML` / `PRESET_ROW` in
  `scripts/python/A3Bot.py`. `/setup` validates with `is_arma_preset_html`
  (the `arma:Type` meta must be `preset` or `list`) and parses with
  `parse_preset_html` (regex over `data-type="DisplayName"` + `?id=`). A file
  built from that template was already generated and passed both checks:
  `Domini_Antistasi.html`, in dependency order.
- The fork is public; GitHub Pages is not yet enabled on it.

## Constraints

- Hosting: GitHub Pages (static) for the page; Cloudflare Workers free tier
  for the lookup. **Decided by the owner.**
- The Steam Web API key lives only as a Worker secret (`STEAM_API_KEY`). The
  owner creates it (steamcommunity.com/dev/apikey; requires a Steam account with
  at least $5 spent) and a Cloudflare account. These are manual steps for the
  owner, documented in the readme.
- Plain HTML/CSS/JS (ES modules), no framework, no bundler. The logic must
  be unit-testable with Node's built-in test runner (`node --test`), with no
  npm dependencies for the tests.
- The Worker's allowed origins are limited to the Pages origin
  (`https://r1sen18.github.io`) plus `http://localhost:*` for local testing,
  configured as a Worker variable, not hard-coded.

## How it works

### Repo layout (the builder may adjust names)

```
docs/
  modlist/
    index.html      page markup + styles
    app.js          UI wiring (DOM only)
    modlist.js      pure logic: parse input, merge, order, export, import
    config.js       WORKER_URL (public, not secret)
  superpowers/specs/…
tools/
  modlist-worker/
    worker.js       Cloudflare Worker
    wrangler.toml   name, vars (ALLOWED_ORIGINS), no secrets
    README.md       deploy steps
tests/
  modlist.test.mjs
  worker.test.mjs
  fixtures/         recorded Steam responses (Domini closure)
presets/
  domini_antistasi.html
```

Pages is served from `main` → `/docs`, so the creator lives at
`https://r1sen18.github.io/A3SAT/modlist/`.

### Worker API

`GET /resolve?ids=<comma-separated numeric ids>[&deps=0]`

(`deps=0` returns details for exactly the given IDs, used by import and
share links. The default walks the full closure.)

- Rejects anything that isn't 1–50 numeric IDs (400), any origin outside
  `ALLOWED_ORIGINS` (403), and any method other than GET/OPTIONS.
- Resolves the **full dependency closure** server-side. It batches Steam
  calls (GetDetails accepts many IDs per call) and walks `children` until no
  new IDs appear. A hard cap of 300 items guards against cycles and abuse.
- Response (only these fields; no raw Steam payload passes through):
  ```json
  {
    "roots": ["3806463703"],
    "items": {
      "3806463703": {
        "title": "Domini Antistasi", "size": 83800000, "updated": 1794421967,
        "requires": ["450814997", "..."], "available": true,
        "appId": 107410, "isCollection": false, "contains": []
      }
    },
    "truncated": false
  }
  ```
- `available: false` for removed, private or banned items. Items that aren't
  for app 107410 are returned with their `appId` so the page can flag them.
- Collections: a pasted collection expands to its contained items, which are
  treated as pasted roots (`contains`), plus their requirements.
- Responses are cached in the Workers Cache API for 1 hour, keyed by the
  sorted ID list.
- Errors from Steam become a 502 with a short message and never include the key
  or the upstream URL.

### Page behaviour

- **Paste box:** accepts Workshop URLs (`?id=`), bare IDs, collection URLs,
  mixed and multi-line. Duplicates are ignored. Unparseable lines are shown
  back as ignored.
- **Add** calls the Worker and merges the result into the current list.
- **List:** every mod shows its title, size, last-updated date, and a link to
  its Workshop page. Auto-added mods get a "required by X, Y" badge. Mods you
  pasted are marked as added by you.
- **Remove:** if other mods in the list require the one being removed, warn
  and name them. Removal is still allowed.
- **Problems are shown, not hidden:** unavailable mods and non-Arma items are
  flagged and excluded from export, with a visible note.
- **Total** download size is shown, like `/setup`'s estimate.
- **Export:** a preset name field (default "Server Mods"), then download
  `<name>.html` using the exact A3Bot `PRESET_HTML`/`PRESET_ROW` structure.
  Rows are in dependency order (requirements before the mods that need them)
  and otherwise alphabetical. Display names are HTML-escaped.
- **Import:** load an existing launcher preset `.html` (from the launcher or
  from `/preset`) to edit it. Its mods become pasted roots and get resolved.
- **Share link:** the current list and name are encoded in the URL hash
  (`#name=…&ids=…`). Opening the link rebuilds the list.
- If the Worker is unreachable, say so plainly and **block export** until
  every mod in the list has a real title. Pasted IDs stay in the box so a retry
  is one click. Exporting IDs as display names would break the server.
  `/setup` keeps the preset's display names as they are. It builds `@Folder`
  names from them, which leads to duplicate installs under `@<id>` folders later. It
  also skips mission scanning unless "antistasi" is in the name.

### Data model: removals stick

- **Auto-adding happens only on Add.** Pasting links and pressing Add
  resolves their requirements and adds any that are missing.
- **Import and share links load the list exactly as it was.** They fetch
  titles and requirement data (Worker `deps=0` mode: details for exactly
  these IDs, no closure walk), but add nothing on their own.
- **A missing requirement is shown, not re-added.** If a mod in the list
  requires something that isn't there (because the user removed it, or the
  imported preset lacked it), the mod gets a "missing: X" warning and a
  one-click "add missing" button.
- **The share link is the exact list:** `#name=…&ids=…` in export order.
  A mod the user removed stays removed when the link is opened.
- An Add of a mod that genuinely needs a removed mod does add it back. That's
  correct, and the badge says why it was added.

### Domini Antistasi on the server

No bot code change is expected. Both "Antistasi" mods in the set are scanned
for mission templates, and the terrain check reads `.wrp` names from the
installed `.pbo` files.

- Ship `presets/domini_antistasi.html`: the 10-mod preset above, in dependency order.
- **Conditional fix:** the template regex in `_TEMPLATE_RE` only matches
  names that start with `Antistasi`. If, after the owner's `/download`,
  `/setup_map` doesn't offer Ostmarschen, add the confirmed template under
  `3806463703` in `KNOWN_MISSION_TEMPLATES` (or widen the scan). This needs the
  real template name from the owner's server and is **not** part of the first
  build.
- Status 2026-11-12: `/setup` parsed all 10 mods. The disk estimate was
  6.2 GB (3.1 GB still to download), and 66 maps were listed, all from the
  Antistasi Ultimate copy already on disk. Ostmarschen wasn't listed, which is
  expected while the patch isn't downloaded yet. Re-check after `/download`.

### Readme additions

- **Updating the bot on the VM:** the zip-and-copy route the owner just used
  (stop A3Bot → back up → copy `main` over the folder → start; `.env_setup` and
  state files are untouched), plus an optional route that turns the folder into
  a `git` clone so later updates are `git pull` + restart.
- **Modlist creator:** what it does, the link, and the owner's one-time
  setup: Steam API key → Cloudflare account → `wrangler deploy` →
  `wrangler secret put STEAM_API_KEY` → put the Worker URL in `config.js` →
  enable Pages (`main` / `/docs`).
- **Domini Antistasi:** the 10 mods, about 7.5 GB of disk during setup (workshop copy plus
  installed copy; `/cleanup_workshop` frees the first), and the `/setup` →
  `/download` → `/setup_map map:Ostmarschen` → `/setup_apply` sequence.

## Testing

- `node --test tests/`, covering:
  - input parsing: URLs, IDs, collections, junk;
  - merging and the "required by" calculation;
  - dependency ordering, including cycle safety;
  - export HTML;
  - import of a launcher preset and of `/preset` output;
  - share-link round trip;
  - the Worker's input validation, CORS, closure walk and cap, all against
    a mocked `fetch`.
- **Golden test:** from a recorded fixture of the Domini closure, resolving
  `3806463703` alone yields exactly the 10 IDs above, and the exported HTML
  passes A3Bot's own `is_arma_preset_html` and `parse_preset_html` rules,
  with all 10 IDs in dependency order.
- **Manual, after deploy:** paste the patch link on the live page → 10 mods;
  export → import into the Arma launcher; attach to `/setup` → 10 mods parsed.

## Decided by the owner

- Domini Antistasi (Antistasi Ultimate + the patch), not Arma Domini's own
  missions.
- Work lives in the fork `R1sen18/A3SAT`, on a new branch.
- The creator is on GitHub and **auto-adds required mods**.
- The lookup runs on a **Cloudflare Worker**.

## Calls made by Claude (owner, please check)

- The page lives under `docs/modlist/` and is served by Pages from `main` `/docs`.
- One branch for both the creator and Domini support.
- The Worker resolves the full closure (not the page), with a 300-item cap,
  1-hour caching, and 1–50 IDs per request.
- Unavailable and non-Arma items are flagged and left out of export.
- Export order is dependencies first, then alphabetical.
- There is a share link via the URL hash, and no server-side saving.
- The Ostmarschen template fix is deferred until real `/setup_map` output exists.
- Export is blocked while any title is unknown, because ID-only presets
  break `/setup` folder naming and mission scanning.
- Removals stick: auto-adding happens only on Add, while import and share
  links load the list exactly and flag missing requirements with a one-click
  fix.
- Order of work: build and unit-test against a hand-built fixture first. The
  PR stays a **draft** until the owner has created the Steam key, deployed the
  Worker and set its secret. Then the live check runs: pasting the patch
  link must give exactly the 10 mods. Only after that does it merge. The key
  never leaves the owner's Cloudflare secret, and no step needs it shared.

## Left to the builder

- Visual design of the page (keep it simple and usable on a phone).
- File and function names inside the layout above.
- The hand-built fixture's shape, based on Valve's documented
  `IPublishedFileService/GetDetails` response. If the live check shows a
  different `children` shape, only the Worker's parsing function and the
  fixture change. The page depends solely on the Worker's own response format.
