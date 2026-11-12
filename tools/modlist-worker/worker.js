// Cloudflare Worker: resolves Steam Workshop items and their required items.
// ES module, no dependencies. The Steam Web API key exists only as the Worker
// secret env.STEAM_API_KEY and is never echoed, logged or returned.

const STEAM_URL = "https://api.steampowered.com/IPublishedFileService/GetDetails/v1/";
const MAX_IDS = 50;
const BATCH = 100;
const DEFAULT_CAP = 300;
const DEFAULT_ORIGINS = "https://r1sen18.github.io,http://localhost:*";

export class SteamError extends Error {}

export function parseIds(raw) {
  if (typeof raw !== "string" || raw === "") return null;
  const parts = raw.split(",");
  if (!parts.every((p) => /^\d+$/.test(p))) return null;
  const ids = [...new Set(parts)];
  return ids.length >= 1 && ids.length <= MAX_IDS ? ids : null;
}

export function originAllowed(origin, allowed) {
  if (!origin) return false;
  for (const entry of String(allowed || "").split(",").map((s) => s.trim()).filter(Boolean)) {
    if (entry === origin) return true;
    if (entry.endsWith(":*")) {
      const prefix = entry.slice(0, -1); // keeps the trailing ":"
      if (origin.startsWith(prefix) && /^\d{1,5}$/.test(origin.slice(prefix.length))) return true;
    }
  }
  return false;
}

// --- All knowledge of Steam's raw response shape lives in normalizeItem + fetchDetails.

export function normalizeItem(raw) {
  const children = Array.isArray(raw.children) ? raw.children : [];
  const ids = children.map((c) => String(c.publishedfileid));
  const isCollection = raw.file_type === 2;
  return {
    title: String(raw.title ?? ""),
    size: Number(raw.file_size) || 0,
    updated: Number(raw.time_updated) || 0,
    requires: isCollection ? [] : ids,
    available: raw.result === 1 && !raw.banned && [0, 3].includes(raw.visibility),
    appId: Number(raw.consumer_appid) || 0,
    isCollection,
    contains: isCollection ? ids : [],
  };
}

export function missingItem() {
  return { title: "", size: 0, updated: 0, requires: [], available: false, appId: 0, isCollection: false, contains: [] };
}

export async function fetchDetails(ids, key, fetchImpl) {
  const out = {};
  for (let i = 0; i < ids.length; i += BATCH) {
    const params = new URLSearchParams();
    params.set("key", key);
    ids.slice(i, i + BATCH).forEach((id, n) => params.set(`publishedfileids[${n}]`, id));
    params.set("includechildren", "true");
    params.set("short_description", "true");
    let res;
    try {
      res = await fetchImpl(`${STEAM_URL}?${params}`);
    } catch {
      throw new SteamError("steam request failed");
    }
    if (!res || res.status !== 200) throw new SteamError("steam returned an error");
    let data;
    try {
      data = await res.json();
    } catch {
      throw new SteamError("steam returned invalid data");
    }
    const list = data && data.response && data.response.publishedfiledetails;
    if (!Array.isArray(list)) throw new SteamError("steam returned unexpected data");
    for (const d of list) if (d && d.publishedfileid != null) out[String(d.publishedfileid)] = d;
  }
  return out;
}

export async function resolve(rootIds, getDetails, { deps, cap }) {
  const items = {};
  const seen = new Set();
  let truncated = false;
  let wave = [...new Set(rootIds)];
  while (wave.length) {
    const next = [];
    const fresh = [];
    for (const id of wave) {
      if (seen.has(id)) continue;
      if (seen.size >= cap) { truncated = true; continue; }
      seen.add(id);
      fresh.push(id);
    }
    if (!fresh.length) break;
    const raws = await getDetails(fresh);
    for (const id of fresh) {
      const item = raws[id] ? normalizeItem(raws[id]) : missingItem();
      items[id] = { id, ...item };
      if (deps) next.push(...item.requires, ...item.contains);
    }
    wave = next;
  }
  return { roots: [...new Set(rootIds)], items, truncated };
}

const json = (body, status, headers = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });

function cors(origin) {
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    Vary: "Origin",
  };
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const origin = request.headers.get("Origin");
    const ok = originAllowed(origin, env && env.ALLOWED_ORIGINS != null ? env.ALLOWED_ORIGINS : DEFAULT_ORIGINS);

    if (request.method === "OPTIONS") {
      return ok ? new Response(null, { status: 204, headers: { ...cors(origin), "Access-Control-Max-Age": "86400" } })
                : json({ error: "origin not allowed" }, 403);
    }
    if (url.pathname !== "/resolve") return json({ error: "not found" }, 404);
    if (request.method !== "GET") return json({ error: "method not allowed" }, 405, { Allow: "GET, OPTIONS" });
    if (!ok) return json({ error: "origin not allowed" }, 403);

    const ids = parseIds(url.searchParams.get("ids"));
    if (!ids) return json({ error: "ids must be 1-50 numeric Workshop IDs" }, 400, cors(origin));
    const deps = url.searchParams.get("deps") !== "0";

    const cacheKey = `https://cache.internal/resolve?ids=${[...ids].sort().join(",")}&deps=${deps ? 1 : 0}`;
    const cache = globalThis.caches && globalThis.caches.default;
    const headers = { ...cors(origin), "Cache-Control": "public, max-age=3600" };
    if (cache) {
      try {
        const hit = await cache.match(cacheKey);
        if (hit) return json(await hit.json(), 200, headers);
      } catch { /* cache is best effort */ }
    }

    if (!env || !env.STEAM_API_KEY) return json({ error: "Steam lookup failed" }, 502, cors(origin));
    let result;
    try {
      const getDetails = (batch) => fetchDetails(batch, env.STEAM_API_KEY, (u) => fetch(u));
      result = await resolve(ids, getDetails, { deps, cap: DEFAULT_CAP });
    } catch (e) {
      if (e instanceof SteamError) return json({ error: "Steam lookup failed" }, 502, cors(origin));
      return json({ error: "internal error" }, 500, cors(origin));
    }

    if (cache) {
      const store = cache.put(cacheKey, json(result, 200, { "Cache-Control": "public, max-age=3600" })).catch(() => {});
      if (ctx && typeof ctx.waitUntil === "function") ctx.waitUntil(store);
    }
    return json(result, 200, headers);
  },
};
