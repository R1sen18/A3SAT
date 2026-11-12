# a3sat-modlist Worker

Cloudflare Worker behind the modlist creator (`docs/modlist/`). It looks up Steam Workshop items and
their required items, because browsers cannot call Steam's API (no CORS) and the dependency endpoint
needs a Steam Web API key that must stay secret.

`GET /resolve?ids=1,2,3&deps=1` returns `{roots, items, truncated}`. 1-50 numeric IDs, closure capped at 300,
cached 1 hour. It makes one kind of upstream call (`IPublishedFileService/GetDetails/v1`, numeric IDs only)
and is not a proxy. Allowed origins come from `ALLOWED_ORIGINS` in `wrangler.toml`.

## One-time deploy (owner)

1. Get a Steam Web API key at https://steamcommunity.com/dev/apikey
2. Create a Cloudflare account (free plan is enough).
3. `npx wrangler login`
4. From `tools/modlist-worker`: `npx wrangler deploy`
5. `npx wrangler secret put STEAM_API_KEY` and paste the key. It is stored only as a Worker secret.
6. Copy the `*.workers.dev` URL printed by deploy into `docs/modlist/config.js`.

## Tests

`npm test` from the repo root (no wrangler needed).
