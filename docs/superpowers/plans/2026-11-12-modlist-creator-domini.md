# Modlist Creator + Domini Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix A3Bot's terrain check so Domini Antistasi's Ostmarschen map can be chosen during `/setup`. Ship a GitHub Pages modlist creator that auto-adds Workshop requirements through a Cloudflare Worker, and exports A3Bot-compatible launcher presets.

**Architecture:**
- **Python:** the terrain logic moves out of `A3Bot.py` into a discord-free module with a real PBO header parser. It scans both the installed `@Folder` and the workshop copy of each mod.
- **Page:** a static page (`docs/modlist/`). All its logic is in `modlist.js`, as pure functions tested with `node --test`.
- **Worker:** a single-file Cloudflare Worker that holds the Steam key, resolves dependency closures, and returns a minimal JSON shape. The page depends only on that shape.

**Tech Stack:** Python 3.10+ stdlib (`unittest`), vanilla JS ES modules, Node 24 built-in test runner, Cloudflare Workers + `wrangler` (deploy only), GitHub Pages.

**Spec:** `docs/superpowers/specs/2026-11-12-modlist-creator-design.md`

**Branch / repo:** `feature/modlist-creator-domini` in `C:\steamgames\a3server\A3SAT-fork` (clone of `R1sen18/A3SAT`). The PR goes to `R1sen18/A3SAT` `main`, never upstream `fosytherrac/A3SAT`.

## Global Constraints

- The Steam Web API key exists only as the Worker secret `STEAM_API_KEY`. It must never appear in the repo, page, responses, logs or errors.
- The Worker makes exactly one kind of upstream call: `https://api.steampowered.com/IPublishedFileService/GetDetails/v1/`, with numeric IDs only.
- Worker input: 1–50 numeric IDs per request. Closure cap: 300 items. Cache: 1 hour. Origins come from the `ALLOWED_ORIGINS` variable, default `https://r1sen18.github.io,http://localhost:*`.
- Page: plain HTML/CSS/JS ES modules. No framework, bundler or npm dependencies. A root `package.json` exists only for `"type": "module"` and the `test` script.
- Exported presets use A3Bot's `PRESET_HTML` / `PRESET_ROW` structure verbatim (`scripts/python/A3Bot.py`, the `PRESET_HTML = """` block). The comment line becomes `Created by A3SAT modlist creator`.
- Export is blocked while any listed, available mod has no title. Unavailable and non-107410 items are excluded from export, with a visible note.
- Only app `107410` (Arma 3) items are exportable.
- Python tests: stdlib `unittest`, run with `python -m unittest discover -s tests/python`. pytest and discord.py are not installed locally.
- JS tests: `node --test tests/js/` from the repo root.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- **Workshop link variants:** `/workshop/filedetails/?id=`, `?l=german&id=`, `&searchtext=`, `http://`, surrounding whitespace, IDs on the same line separated by commas or spaces. All must parse. Test in Task 2.
- **Titles with `&`, `<`, `"` and the en dash in "gabes1410 AD – Arma Domini Factions":** they must be escaped on export and come back unchanged through A3Bot's own parse regex. Test in Task 4.
- **A requirement that Steam reports as deleted or private, or that is absent from the response:** the Worker marks it `available:false`. The page lists it as unavailable, leaves it out of export, and its dependents still export. Tests in Tasks 3 and 5.
- **A requirement cycle (A needs B, B needs A):** the closure walk terminates, and export order is deterministic. Tests in Tasks 3 and 5.
- **Pasting more than 50 IDs** (a big collection, or a pasted preset list): the page splits the request into batches of 50 and merges the results. Tested through `chunk()` in Task 2.

---

### Task 1: Terrain check scans the workshop copies (A3Bot fix)

**Files:**
- Create: `scripts/python/terrain.py`
- Modify: `scripts/python/A3Bot.py`:
  - remove `VANILLA_WORLDS`, `_world_cache` and `world_available` (currently around lines 301–346);
  - import from `terrain`;
  - update the caller in `setup_map` (around line 1505).
- Test: `tests/python/test_terrain.py`

**Interfaces:**
- Produces:
  - `terrain.VANILLA_WORLDS: set[str]`, moved unchanged.
  - `terrain.pbo_file_names(path: str) -> list[str]`
  - `terrain.world_available(world: str, mod_dirs: list[str]) -> bool | None`. Note it now takes **absolute directories**, not folder names. Cached per `(world, tuple(sorted(lowercased dirs)))`.
  - `A3Bot.terrain_dirs(mods: dict[str, str] | None, applied: dict[str, str]) -> list[str]`. For each mod ID it returns the existing ones of `WORKSHOP_BASE\<id>` and `SERVER_BASE\<@Folder>`. The `@Folder` comes from `build_folder_map(mods)` for a pending setup, or from `applied` when `mods` is None.

- [ ] **Step 1: Write the failing tests** in `tests/python/test_terrain.py`. A helper `make_pbo(path, names, product=True, pad_entries=0)` writes a minimal PBO:
  - an optional product entry (`b"\0"` + `struct.pack("<5I", 0x56657273, 0,0,0,0)` + `b"prefix\0x\\y\0\0"`);
  - one entry per name (`name.encode()+b"\0"` + `struct.pack("<5I",0,0,0,0,0)`);
  - `pad_entries` extra dummy entries;
  - the terminator (`b"\0"` + 20 zero bytes).

  Tests:
  - `test_pbo_file_names_reads_all_entries_after_product_entry`: names `["config.bin", "worlds\\1400_ad_ostmarschen\\1400_ad_ostmarschen.wrp"]` come back exactly.
  - `test_pbo_file_names_without_product_entry`: same names with `product=False`.
  - `test_pbo_file_names_truncated_file_returns_partial_without_raising`: write the header, then cut the file mid-entry.
  - `test_world_found_in_workshop_copy_when_installed_folders_lack_it`: dir A has `cba.pbo` (no wrp), dir B has the terrain PBO. `world_available("1400_ad_ostmarschen", [A, B]) is True`. This is the bug repro.
  - `test_world_found_beyond_512kb_header`: `pad_entries=40000` puts the wrp entry after 512 KB → `True`.
  - `test_world_missing_returns_false_when_pbos_scanned`: → `False`.
  - `test_nothing_scannable_returns_none`: empty and nonexistent dirs → `None`.
  - `test_vanilla_world_true_without_scanning`: `world_available("Altis", []) is True`.
  - `test_match_is_case_insensitive_and_on_file_basename`: `Worlds\\Foo\\FOO.WRP` matches world `foo`; `worlds\\foobar\\foobar.wrp` does **not** match `foo`.

- [ ] **Step 2: Run** `python -m unittest discover -s tests/python -v` from the repo root. The test file inserts `<repo>/scripts/python` into `sys.path` before `import terrain`. Expected: FAIL with `ModuleNotFoundError: No module named 'terrain'`.

- [ ] **Step 3: Implement `pbo_file_names`** in `terrain.py`. Read null-terminated names from the buffered file, each followed by a 20-byte block whose first uint32 (little-endian) is the packing method:

  ```python
  while True:
      name = read_cstring(f)            # bytes up to b"\0"; b"" at EOF
      block = f.read(20)
      if len(block) < 20: break         # truncated: return what we have
      method = int.from_bytes(block[:4], "little")
      if name == b"":
          if method == 0x56657273:      # 'Vers' product entry: key/value strings until b""
              while read_cstring(f): pass
              continue
          break                         # header terminator
      names.append(name.decode("latin-1"))
  ```

  Guard the loop with a 1,000,000-entry ceiling.

- [ ] **Step 4: Implement `world_available`** using the basename match `name.replace("/", "\\").rsplit("\\", 1)[-1].lower() == f"{world.lower()}.wrp"`. Keep the old semantics:
  - vanilla → `True`;
  - nothing scanned → `None`;
  - otherwise `True` / `False`, cached.

- [ ] **Step 5: Wire into A3Bot.** Add `terrain_dirs` next to `build_folder_map`. In `setup_map`, replace the `folders = …` / `world_available(world, folders)` pair with `world_available(world, terrain_dirs(cfg["mods"] if cfg else None, applied))`. Remove the moved code and add `from terrain import VANILLA_WORLDS, world_available`. Check that nothing else references `_world_cache`.

- [ ] **Step 6: Run** `python -m unittest discover -s tests/python -v`. Expected: 9 tests OK. Then run `python -m py_compile scripts/python/A3Bot.py scripts/python/terrain.py`. Expected: no output.

- [ ] **Step 7: Commit**

  ```bash
  git add scripts/python/terrain.py scripts/python/A3Bot.py tests/python/test_terrain.py
  git commit -m "Fix terrain check missing maps that are only in the workshop cache"
  ```

---

### Task 2: Test scaffold, input parsing, share links, batching

**Files:**
- Create: `package.json`, `docs/modlist/modlist.js`
- Test: `tests/js/parse.test.mjs`

**Interfaces:**
- Produces, all exported from `docs/modlist/modlist.js`:
  - `parseInput(text: string) -> { ids: string[], ignored: string[] }`. IDs are in first-seen order and deduplicated. `ignored` holds the trimmed non-empty tokens or lines that yielded no ID.
  - `chunk(ids: string[], size: number) -> string[][]`
  - `encodeShare(name: string, ids: string[]) -> string`. Returns a hash body without `#`: `name=<encodeURIComponent>&ids=<comma list>`.
  - `decodeShare(hash: string) -> { name: string, ids: string[] }`. Accepts with or without a leading `#`. Non-numeric IDs are dropped. The name defaults to `"Server Mods"`.

- [ ] **Step 1: Create `package.json`:**

  ```json
  {"private": true, "type": "module", "scripts": {"test": "node --test tests/js/"}}
  ```

- [ ] **Step 2: Write the failing tests:**
  - `parseInput` on a multi-line string containing all of:
    - `https://steamcommunity.com/sharedfiles/filedetails/?id=3806463703`
    - `http://steamcommunity.com/workshop/filedetails/?id=450814997&searchtext=cba`
    - `https://steamcommunity.com/sharedfiles/filedetails/?l=german&id=3510959070`
    - `  3020755032  `
    - `3779310113, 2817145194 2817149713`
    - `hello`
    - a repeat of the first URL

    → `ids` equals `["3806463703","450814997","3510959070","3020755032","3779310113","2817145194","2817149713"]`, and `ignored` equals `["hello"]`.
  - A URL without `id=` (`https://steamcommunity.com/app/107410/workshop/`) goes to `ignored`.
  - `chunk` of 120 IDs with size 50 → lengths `[50, 50, 20]`. `chunk([], 50)` → `[]`.
  - The share round trip `decodeShare("#" + encodeShare("Domini & Friends", ["1","22"]))` deep-equals `{name: "Domini & Friends", ids: ["1","22"]}`.
  - `decodeShare("ids=1,abc,3")` → `{name: "Server Mods", ids: ["1","3"]}`.

- [ ] **Step 3: Run** `npm test`. Expected: FAIL (module not found).

- [ ] **Step 4: Implement the four functions.** Rules for `parseInput`:
  - Split the input on newlines, then on commas and whitespace.
  - A token containing `id=` yields the digits after `id=`.
  - A token that is all digits yields itself.
  - Anything else goes to `ignored`.

- [ ] **Step 5: Run** `npm test`. Expected: PASS.

- [ ] **Step 6: Commit** `package.json docs/modlist/modlist.js tests/js/parse.test.mjs` with the message "Add modlist input parsing and share links".

---

### Task 3: List state model and dependency order

**Files:**
- Modify: `docs/modlist/modlist.js`
- Create: `tests/js/fixtures/resolve-domini.json`, the **Worker response shape** (spec § Worker API) for the 10 Domini mods.
- Test: `tests/js/state.test.mjs`

**Fixture content** (from the spec table): `roots: ["3806463703"]`, `truncated: false`. Every item has `available: true`, `appId: 107410`, `isCollection: false`, `contains: []`, plus a size and the title exactly as in the spec table. The `requires` lists are:

| ID | `requires` |
|---|---|
| 3806463703 | `["450814997","3510959070","3020755032","3779310113","2817145194","2817149713","3780409647","3816263034","3794701829"]` |
| 2817145194 | `["450814997"]` |
| 2817149713 | `["2817145194"]` |
| 3020755032 | `["450814997"]` |
| 3510959070 | `["450814997"]` |
| 3779310113 | `["450814997","3510959070"]` |
| 3780409647 | `["3779310113","3510959070"]` |
| 3794701829 | `["450814997"]` |
| 450814997, 3816263034 | `[]` |

**Interfaces:**
- Consumes: the Worker response shape (spec).
- Produces, exported from `modlist.js`:
  - `Mod = { id, title: string|null, size: number, updated: number, requires: string[], available: boolean, appId: number|null, addedBy: "user"|"auto", resolved: boolean }`
  - `State = { name: string, mods: Record<string, Mod> }`
  - `emptyState(name = "Server Mods") -> State`
  - `addUnresolved(state, ids: string[]) -> State`. Adds placeholder mods with `resolved: false`, `title: null`, `addedBy: "user"`. Used by import and share links before or without the Worker.
  - `applyResolve(state, response, mode: "add"|"load") -> State`
    - `"add"`: every non-collection item in `response.items` is upserted. Items that are roots, or the `contains` of a root collection, get `addedBy: "user"`; everything else gets `"auto"`. Never downgrade an existing `"user"` to `"auto"`.
    - `"load"`: only update mods already in `state` (fill in title, requires and so on, and set `resolved: true`). Never add.
  - `removeMod(state, id) -> { state: State, dependents: string[] }`. `dependents` are the IDs still in the list whose `requires` includes `id`.
  - `requiredBy(state, id) -> string[]`. IDs in the list that require `id`, sorted by `sortKey`.
  - `missingRequirements(state, id) -> string[]`. IDs in `mods[id].requires` that are not in the list.
  - `exportPlan(state) -> { ok: boolean, unresolved: string[], included: Mod[], excluded: Mod[] }`
    - `excluded` = mods with `available: false` or `appId !== 107410`.
    - `unresolved` = included candidates with `resolved: false` or no title.
    - `ok` = `unresolved.length === 0 && included.length > 0`.
    - `included` is in `exportOrder` order.
  - `exportOrder(mods: Mod[]) -> Mod[]`. Kahn's algorithm, considering only `requires` edges between mods in the given array. Ready mods are taken smallest-first by `sortKey(mod) = (mod.title ?? mod.id).toLowerCase()`, ties broken by ID. If a cycle leaves mods behind, they are appended in `sortKey` order.

- [ ] **Step 1: Write the failing tests:**
  - `golden_domini_add`: `applyResolve(emptyState(), fixture, "add")`, then `exportPlan`:
    - `ok` is true;
    - `included.map(m => m.id)` deep-equals `["450814997","3020755032","3816263034","3510959070","3779310113","3780409647","2817145194","2817149713","3794701829","3806463703"]`;
    - only `3806463703` has `addedBy: "user"`.
  - `remove_sticks_and_reports_dependents`: remove `3510959070` → `dependents` (sorted) equals `["3779310113","3780409647","3806463703"]`. `missingRequirements(s, "3779310113")` is `["3510959070"]`. A following `applyResolve(…, "load")` with the same fixture does not bring it back.
  - `add_respects_user_flag`: a user-added CBA stays `"user"` after a later `"add"` that also contains it as a requirement.
  - `unavailable_requirement_excluded_dependent_kept`: fixture copy with `3816263034.available = false` → it's in `excluded`, `3806463703` is still in `included`, and `ok` is true.
  - `non_arma_item_excluded`: one item with `appId: 431960` → excluded.
  - `unresolved_blocks_export`: `addUnresolved(emptyState(), ["1"])` → `ok` false, `unresolved` `["1"]`.
  - `cycle_is_deterministic`: A requires B, B requires A (titles "A", "B") → `exportOrder` returns `[A, B]` both times. No hang.

- [ ] **Step 2: Run** `npm test`. Expected: FAIL (missing exports).

- [ ] **Step 3: Implement the functions in `modlist.js`.** All are pure, returning new objects (`structuredClone` is fine).

- [ ] **Step 4: Run** `npm test`. Expected: PASS.

- [ ] **Step 5: Commit** "Add modlist state model and dependency ordering".

---

### Task 4: Preset export and import

**Files:**
- Modify: `docs/modlist/modlist.js`
- Test: `tests/js/preset.test.mjs`

**Interfaces:**
- Consumes: `Mod`, `exportPlan` (Task 3).
- Produces:
  - `buildPresetHtml(name: string, mods: Mod[]) -> string`. Rows in the given order. `name` and titles are HTML-escaped (`&`, `<`, `>`, `"`).
  - `parsePresetHtml(html: string) -> { name: string, ids: string[] } | null`. Returns null unless the `arma:Type` meta is `preset` or `list`. The name comes from `arma:PresetName`, defaulting to "Server Mods".
  - `presetFilename(name: string) -> string`. Characters outside `[A-Za-z0-9 _-]` become `_`, plus `.html`. An empty result becomes `Server_Mods.html`.

- [ ] **Step 1: Write the failing tests.** Copy A3Bot's two checks verbatim as constants in the test file:
  - `A3BOT_TYPE_RE = /name="arma:Type"\s+content="(preset|list)"/`, checked against the first 4096 characters;
  - `A3BOT_ROW_RE = /data-type="DisplayName">([^<]+)<\/td>.*?\?id=(\d+)/gs`.

  Tests:
  - `golden_export_passes_a3bot_checks`: build from the Task 3 golden `included`. The type regex matches, and the row regex yields 10 matches whose IDs equal the golden order. Unescaping `&amp;`, `&lt;` and `&gt;` (as A3Bot does) gives the fixture titles, including `gabes1410 AD – Arma Domini Factions`.
  - `escapes_markup_in_titles`: title `A & B <x> "q"` → output contains `A &amp; B &lt;x&gt; &quot;q&quot;`, and no raw `<x>`.
  - `round_trip_import`: `parsePresetHtml(buildPresetHtml("Domini & Co", mods))` → `name` is `"Domini & Co"` and the IDs are in the same order.
  - `imports_a3bot_preset_output`: the existing file `C:\steamgames\a3server\A3SAT2\Domini_Antistasi.html`, copied to `tests/js/fixtures/a3bot-preset.html`, parses to 10 IDs.
  - `rejects_non_preset_html`: `"<html><body>hi</body></html>"` → `null`.
  - `presetFilename("Domini: Run #1")` → `"Domini_ Run _1.html"`.

- [ ] **Step 2: Run** `npm test`. Expected: FAIL.

- [ ] **Step 3: Implement.** The template is A3Bot's `PRESET_HTML` / `PRESET_ROW` text, copied verbatim with only the comment line changed (Global Constraints). Keep it as two template-literal constants.

- [ ] **Step 4: Run** `npm test`. Expected: PASS.

- [ ] **Step 5: Commit** "Add launcher preset export and import".

---

### Task 5: Cloudflare Worker

**Files:**
- Create:
  - `tools/modlist-worker/worker.js`
  - `tools/modlist-worker/wrangler.toml`: `name = "a3sat-modlist"`, `main = "worker.js"`, `compatibility_date = "2026-11-01"`, `[vars] ALLOWED_ORIGINS = "https://r1sen18.github.io,http://localhost:*"`
  - `tools/modlist-worker/README.md`
- Test: `tests/js/worker.test.mjs`, `tests/js/fixtures/steam-getdetails-domini.json`

**Steam fixture shape.** This is Valve's documented `IPublishedFileService/GetDetails` response:
- `{"response":{"publishedfiledetails":[…]}}`
- each entry has `publishedfileid` (string), `result` (1 = OK), `consumer_appid`, `title`, `file_size` (string), `time_updated`, `visibility`, `banned`, `file_type` (0 = item, 2 = collection), `children: [{publishedfileid, sortorder, file_type}]`.

Build it from the Task 3 table. **Unconfirmed until the live check in Task 8.** If the live check differs, only `normalizeItem` and this fixture change.

**Interfaces:**
- Produces (named exports, plus `export default { fetch }`):
  - `parseIds(raw: string|null) -> string[] | null`. Null unless there are 1–50 comma-separated all-digit IDs. Deduplicated.
  - `originAllowed(origin: string|null, allowed: string) -> boolean`. Exact match, or `http://localhost:*` matching any port.
  - `normalizeItem(raw) -> Item`, where `Item = {title, size, updated, requires, available, appId, isCollection, contains}`.
    - `available = result === 1 && !banned && [0, 3].includes(visibility)`.
    - Children go to `contains` when `file_type === 2`, otherwise to `requires`.
  - `missingItem() -> Item`: `available: false`, `title: ""`, the rest empty or 0.
  - `fetchDetails(ids: string[], key: string, fetchImpl) -> Promise<Record<string, raw>>`. Batches of 100. Builds `publishedfileids[i]` params plus `key`, `includechildren=true`, `short_description=true`. Throws `SteamError` on a non-200 response.
  - `resolve(rootIds: string[], getDetails: (ids) => Promise<Record<string, raw>>, { deps: boolean, cap: number }) -> Promise<{roots, items, truncated}>`.
    - Breadth-first walk with a visited set, enqueueing `requires` and `contains`.
    - Stops at `cap` (sets `truncated: true`).
    - IDs that Steam doesn't return become `missingItem()`.
    - With `deps: false`, only the roots are fetched.
  - `fetch(request, env, ctx)` handler:
    - `OPTIONS` → 204 with CORS headers if the origin is allowed.
    - A path other than `/resolve` → 404.
    - A method other than GET/OPTIONS → 405.
    - Origin not allowed → 403.
    - Bad IDs → 400 `{"error":"ids must be 1-50 numeric Workshop IDs"}`.
    - `SteamError` → 502 `{"error":"Steam lookup failed"}`.
    - Success → 200 JSON with `Access-Control-Allow-Origin: <origin>`, `Vary: Origin`, `Cache-Control: public, max-age=3600`.
    - Uses `caches.default` when `globalThis.caches` exists, keyed by `https://cache.internal/resolve?ids=<sorted>&deps=<0|1>`.

- [ ] **Step 1: Write the failing tests.** Use a mocked `fetchImpl` / `getDetails` backed by the Steam fixture. Tests:
  - `parseIds`: `"1,2,2"` → `["1","2"]`. `""`, `"1,x"` and 51 IDs → `null`.
  - `originAllowed`:
    - `https://r1sen18.github.io` → true;
    - `http://localhost:8000` → true;
    - `https://evil.example` → false;
    - `null` → false.
  - `golden_closure`: `resolve(["3806463703"], …, {deps: true, cap: 300})` → 10 item keys, `truncated` false. `items["3779310113"].requires` equals `["450814997","3510959070"]`.
  - `deps_false_fetches_only_roots`: the mock records requested IDs, and only `3806463703` was requested.
  - `missing_child_becomes_unavailable`: delete `3816263034` from the mock → `items["3816263034"].available === false`.
  - `cycle_terminates`: A ↔ B → 2 items.
  - `cap_truncates`: a chain of 400 → `Object.keys(items).length === 300`, `truncated` true.
  - `collection_expands`: a collection with children X and Y → `items[col].isCollection`, and `contains` is `[X, Y]`. X and Y are resolved.
  - `handler_never_leaks_key`: `env.STEAM_API_KEY = "SECRETKEY123"`. The mock fetch returns 500. The response is 502, and its body contains neither `SECRETKEY123` nor `api.steampowered.com`.
  - Handler status codes: 403 (bad origin), 400 (bad IDs), 404 (`/other`), 405 (POST), 204 (OPTIONS with an allowed origin).
  - `handler_success_shape`: the body keys are exactly `roots`, `items` and `truncated`, and the CORS header echoes the origin. Inject the mock fetch via `globalThis.fetch`, restored after the test.

- [ ] **Step 2: Run** `npm test`. Expected: FAIL.

- [ ] **Step 3: Implement `worker.js`.**

- [ ] **Step 4: Run** `npm test`. Expected: PASS.

- [ ] **Step 5: Write `tools/modlist-worker/README.md`.** It covers the owner's one-time deploy:
  1. Steam key from `https://steamcommunity.com/dev/apikey`.
  2. A Cloudflare account.
  3. `npx wrangler login`
  4. `npx wrangler deploy` (from `tools/modlist-worker`)
  5. `npx wrangler secret put STEAM_API_KEY`
  6. Copy the `*.workers.dev` URL into `docs/modlist/config.js`.

- [ ] **Step 6: Commit** "Add Cloudflare Worker for Workshop dependency lookup".

---

### Task 6: The page

**Files:**
- Create:
  - `docs/modlist/index.html`: markup and inline `<style>`; works at phone width; light and dark via `prefers-color-scheme`.
  - `docs/modlist/app.js`: DOM wiring only.
  - `docs/modlist/config.js`: `export const WORKER_URL = "";`
- Create (dev only): `tests/js/mock-worker.mjs`. A Node `http` server on port 8787 that answers `GET /resolve` from `tests/js/fixtures/resolve-domini.json` with CORS `*`. It honours `deps=0` by returning only the requested IDs. It is never deployed.

**Interfaces:**
- Consumes everything exported by `modlist.js` (Tasks 2–4).
- Worker base URL: `?api=<url>` query override, else `WORKER_URL`. If both are empty, show "Lookup service not configured" and disable Add.

**Behaviour to wire** (spec § Page behaviour, § Data model):

**Add**
- `parseInput` → show `ignored`.
- `chunk(ids, 50)` → one `GET {api}/resolve?ids=` per chunk → `applyResolve(…, "add")` for each.
- On a fetch failure: a plain-language error. Keep the textarea content.

**List rows**
- Title links to the Workshop page.
- Size (GB with 2 decimals, MB under 1 GB).
- Updated date.
- Badge: "added by you", or "required by <titles>" via `requiredBy`.
- Warnings:
  - "missing: <ids/titles>" with an **Add missing** button, which runs Add on those IDs;
  - "unavailable on Steam";
  - "not an Arma 3 item".
- A **Remove** button. If `dependents` is non-empty, ask `confirm()` naming them first.

**Totals:** mod count and total size of `included`.

**Export**
- Name field, default "Server Mods".
- Export is disabled while `!exportPlan(state).ok`, with the reason shown (unresolved IDs, or an empty list). Excluded mods are listed in a note.
- Download a `Blob` of type `text/html;charset=utf-8` named `presetFilename(name)`.

**Import:** file input, `.html` → `parsePresetHtml` → `addUnresolved` → `/resolve?deps=0` in chunks → `applyResolve(…, "load")`. A `null` parse shows "Not an Arma 3 launcher preset".

**Share link**
- Update `location.hash` to `encodeShare(name, exportOrder(...).map(m => m.id))` on every change.
- On load, a non-empty hash → `decodeShare` → same path as Import.

**Persistence:** none beyond the hash.

- [ ] **Step 1: Write `index.html`, `app.js` and `config.js`.**

- [ ] **Step 2: Start the dev servers.**
  - `node tests/js/mock-worker.mjs` (background), and `python -m http.server 8000 -d docs` (background).
  - Or use `.claude/launch.json` entries via the browser pane's `preview_start`, which can't open localhost servers started from Bash.

- [ ] **Step 3: Manual check in the browser pane** at `http://localhost:8000/modlist/?api=http://localhost:8787`:
  - Paste `https://steamcommunity.com/sharedfiles/filedetails/?id=3806463703` → Add → 10 rows. The totals show 10 mods and about 3.75 GB.
  - Remove "IMS: DEV" → the confirm names 3 dependents. Accept → Arma Domini shows "missing: IMS: DEV".
  - Reload the page (hash) → IMS is still absent.
  - Click **Add missing** → IMS returns, with the badge "required by …".
  - Export → the file downloads. Re-import it → the same 10 rows.
  - Stop the mock worker and press Add → the error shows and the textarea is kept.
  - Check phone width (375 px) and dark mode.

- [ ] **Step 4: Run** `npm test`. Expected: still PASS. Stop both dev servers.

- [ ] **Step 5: Commit** `docs/modlist/index.html docs/modlist/app.js docs/modlist/config.js tests/js/mock-worker.mjs` with the message "Add modlist creator page".

---

### Task 7: Domini preset and readme

**Files:**
- Create: `presets/domini_antistasi.html`. Generate it with `buildPresetHtml("Domini Antistasi", exportPlan(applyResolve(emptyState(), fixture, "add")).included)` in a one-off `node -e` that imports `modlist.js`, so it matches the tested export exactly.
- Modify: `readme.md`. Add three sections, keeping the existing tone and structure:
  1. **Updating the bot on the VM.** The zip route:
     - stop A3Bot → back up the folder → download `https://github.com/R1sen18/A3SAT/archive/refs/heads/main.zip` → copy over → clear `__pycache__` → start → check `logs\a3bot_stderr.log` for `[config]`;
     - `.env_setup` and the state files are untouched;
     - the `ADMIN_PASSWORD` / `SERVER_PASSWORD` rules from `.env_setup.example`.

     Plus the optional git route: `git init` → `git remote add origin https://github.com/R1sen18/A3SAT.git` → `git fetch` → `git checkout -f -B main origin/main` (gitignored state is untouched). Later updates are then `git pull` + `nssm restart A3Bot`.
  2. **Modlist creator.** The link `https://r1sen18.github.io/A3SAT/modlist/`, what it does, that removed mods stay removed, and the one-time setup (pointing to `tools/modlist-worker/README.md`, then enabling Pages: Settings → Pages → `main` / `/docs`).
  3. **Domini Antistasi.**
     - The 10 mods.
     - Disk use: about 3.75 GB of downloads, and roughly double during setup (`/cleanup_workshop` frees the workshop copy).
     - The flow: `/setup preset:presets/domini_antistasi.html` → `/download` → `/setup_map map:1400_ad_ostmarschen` → `/setup_apply restart:true`.
     - That faction choice happens in-game.

- [ ] **Step 1: Generate the preset file.** Then verify it with `node -e` by running `parsePresetHtml` on it. Expected: 10 IDs.
- [ ] **Step 2: Write the readme sections.**
- [ ] **Step 3: Commit** "Add Domini Antistasi preset and readme sections".

---

### Task 8: Deploy, live verification, draft PR (needs the owner)

The owner has already asked for the PR to be opened once the work is done (2026-11-12). That covers Step 1's push and PR. Enabling Pages and merging still need the owner's OK at the time. The Cloudflare and Steam steps are the owner's own.

- [ ] **Step 1: Push the branch and open a draft PR.** Pre-approved by the owner.

  ```bash
  git push -u origin feature/modlist-creator-domini
  gh pr create --repo R1sen18/A3SAT --base main --draft --title "Modlist creator + Domini Antistasi terrain fix"
  ```

  The PR body lists what's verified (unit tests) and what's pending (live Worker), and ends with the Claude Code attribution line.

- [ ] **Step 2: Owner deploys the Worker** following `tools/modlist-worker/README.md`. Owner action; Claude never handles the key.

- [ ] **Step 3: Live API check** from the shell:
  - `curl -s -H "Origin: https://r1sen18.github.io" "<worker-url>/resolve?ids=3806463703"` → 10 items, and `items["3779310113"].requires` contains `3510959070`.
  - If `requires` is empty everywhere, the real `children` shape differs. Fix `normalizeItem` plus the Steam fixture, then rerun `npm test`.
  - `curl -s -H "Origin: https://evil.example" …` → 403.

- [ ] **Step 4: Put the Worker URL in `docs/modlist/config.js`** and commit "Point modlist page at deployed Worker".

- [ ] **Step 5: After merge, enable Pages.** Ask first: `gh api -X POST repos/R1sen18/A3SAT/pages -f "source[branch]=main" -f "source[path]=/docs"`. Then open `https://r1sen18.github.io/A3SAT/modlist/` in the browser pane and repeat the Task 6 Step 3 checks against the live Worker.

- [ ] **Step 6: Bot fix on the VM.** After merge, the owner updates the VM (readme route). Then `/setup` with `presets/domini_antistasi.html` → `/download` → `/setup_map map:1400_ad_ostmarschen` must be **accepted** during the pending setup. If it's still refused, the terrain's `.wrp` file name differs from the world name. Collect `pbo_file_names` output for the Arma Domini terrain PBO from the owner and adjust the match.
