import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import worker, {
  parseIds, originAllowed, normalizeItem, missingItem, fetchDetails, resolve,
} from "../../tools/modlist-worker/worker.js";

const steam = JSON.parse(readFileSync(new URL("./fixtures/steam-getdetails-domini.json", import.meta.url)));
const rawById = () => Object.fromEntries(steam.response.publishedfiledetails.map((d) => [d.publishedfileid, d]));
const mockDetails = (map, log) => async (ids) => {
  if (log) log.push(...ids);
  const out = {};
  for (const id of ids) if (map[id]) out[id] = map[id];
  return out;
};
const raw = (id, children = [], file_type = 0) => ({
  publishedfileid: id, result: 1, consumer_appid: 107410, title: "T" + id, file_size: "1",
  time_updated: 1, visibility: 0, banned: 0, file_type,
  children: children.map((c, i) => ({ publishedfileid: c, sortorder: i, file_type: 0 })),
});

test("parseIds", () => {
  assert.deepEqual(parseIds("1,2,2"), ["1", "2"]);
  assert.equal(parseIds(""), null);
  assert.equal(parseIds(null), null);
  assert.equal(parseIds("1,x"), null);
  assert.equal(parseIds(Array.from({ length: 51 }, (_, i) => i + 1).join(",")), null);
  assert.equal(parseIds(Array.from({ length: 50 }, (_, i) => i + 1).join(",")).length, 50);
});

test("originAllowed", () => {
  const a = "https://r1sen18.github.io,http://localhost:*";
  assert.equal(originAllowed("https://r1sen18.github.io", a), true);
  assert.equal(originAllowed("http://localhost:8000", a), true);
  assert.equal(originAllowed("https://evil.example", a), false);
  assert.equal(originAllowed("http://localhost.evil.example", a), false);
  assert.equal(originAllowed(null, a), false);
});

test("normalizeItem", () => {
  const it = normalizeItem(raw("5", ["6"], 2));
  assert.equal(it.isCollection, true);
  assert.deepEqual(it.contains, ["6"]);
  assert.deepEqual(it.requires, []);
  assert.equal(it.size, 1);
  assert.equal(it.available, true);
  assert.equal(normalizeItem({ ...raw("5"), banned: 1 }).available, false);
  assert.equal(normalizeItem({ ...raw("5"), result: 9 }).available, false);
  assert.equal(normalizeItem({ ...raw("5"), visibility: 1 }).available, false);
  assert.equal(missingItem().available, false);
  assert.equal(missingItem().title, "");
});

test("golden_closure", async () => {
  const r = await resolve(["3806463703"], mockDetails(rawById()), { deps: true, cap: 300 });
  assert.equal(Object.keys(r.items).length, 10);
  assert.equal(r.truncated, false);
  assert.deepEqual(r.roots, ["3806463703"]);
  assert.deepEqual(r.items["3779310113"].requires, ["450814997", "3510959070"]);
});

test("deps_false_fetches_only_roots", async () => {
  const log = [];
  await resolve(["3806463703"], mockDetails(rawById(), log), { deps: false, cap: 300 });
  assert.deepEqual(log, ["3806463703"]);
});

test("missing_child_becomes_unavailable", async () => {
  const m = rawById();
  delete m["3816263034"];
  const r = await resolve(["3806463703"], mockDetails(m), { deps: true, cap: 300 });
  assert.equal(r.items["3816263034"].available, false);
});

test("cycle_terminates", async () => {
  const m = { A: raw("A", ["B"]), B: raw("B", ["A"]) };
  const r = await resolve(["A"], mockDetails(m), { deps: true, cap: 300 });
  assert.equal(Object.keys(r.items).length, 2);
});

test("cap_truncates", async () => {
  const m = {};
  for (let i = 0; i < 400; i++) m["n" + i] = raw("n" + i, ["n" + (i + 1)]);
  const r = await resolve(["n0"], mockDetails(m), { deps: true, cap: 300 });
  assert.equal(Object.keys(r.items).length, 300);
  assert.equal(r.truncated, true);
});

test("collection_expands", async () => {
  const m = { C: raw("C", ["X", "Y"], 2), X: raw("X"), Y: raw("Y") };
  const r = await resolve(["C"], mockDetails(m), { deps: true, cap: 300 });
  assert.equal(r.items.C.isCollection, true);
  assert.deepEqual(r.items.C.contains, ["X", "Y"]);
  assert.equal(r.items.X.available, true);
  assert.equal(r.items.Y.available, true);
});

test("fetchDetails batches by 100 and builds params", async () => {
  const urls = [];
  const f = async (url) => {
    urls.push(String(url));
    return new Response(JSON.stringify({ response: { publishedfiledetails: [] } }), { status: 200 });
  };
  const ids = Array.from({ length: 150 }, (_, i) => String(i + 1));
  await fetchDetails(ids, "K", f);
  assert.equal(urls.length, 2);
  assert.ok(urls[0].startsWith("https://api.steampowered.com/IPublishedFileService/GetDetails/v1/?"));
  assert.ok(urls[0].includes("publishedfileids%5B0%5D=1"));
  assert.ok(urls[0].includes("includechildren=true"));
});

const ENV = { STEAM_API_KEY: "SECRETKEY123", ALLOWED_ORIGINS: "https://r1sen18.github.io,http://localhost:*" };
const req = (path, origin, method = "GET") =>
  new Request("https://w.example" + path, { method, headers: origin ? { Origin: origin } : {} });
const OK = "https://r1sen18.github.io";

async function withFetch(impl, fn) {
  const orig = globalThis.fetch;
  globalThis.fetch = impl;
  try { return await fn(); } finally { globalThis.fetch = orig; }
}

test("handler_never_leaks_key", async () => {
  await withFetch(async () => new Response("boom SECRETKEY123", { status: 500 }), async () => {
    const res = await worker.fetch(req("/resolve?ids=1", OK), ENV, {});
    assert.equal(res.status, 502);
    const body = await res.text();
    assert.ok(!body.includes("SECRETKEY123"));
    assert.ok(!body.includes("api.steampowered.com"));
    assert.deepEqual(JSON.parse(body), { error: "Steam lookup failed" });
  });
});

test("handler status codes", async () => {
  assert.equal((await worker.fetch(req("/resolve?ids=1", "https://evil.example"), ENV, {})).status, 403);
  assert.equal((await worker.fetch(req("/resolve?ids=1"), ENV, {})).status, 403);
  const bad = await worker.fetch(req("/resolve?ids=x", OK), ENV, {});
  assert.equal(bad.status, 400);
  assert.deepEqual(await bad.json(), { error: "ids must be 1-50 numeric Workshop IDs" });
  assert.equal((await worker.fetch(req("/other?ids=1", OK), ENV, {})).status, 404);
  assert.equal((await worker.fetch(req("/resolve?ids=1", OK, "POST"), ENV, {})).status, 405);
  const opt = await worker.fetch(req("/resolve", OK, "OPTIONS"), ENV, {});
  assert.equal(opt.status, 204);
  assert.equal(opt.headers.get("Access-Control-Allow-Origin"), OK);
});

test("handler_success_shape", async () => {
  const seen = [];
  const impl = async (url) => {
    seen.push(String(url));
    return new Response(JSON.stringify(steam), { status: 200 });
  };
  await withFetch(impl, async () => {
    const res = await worker.fetch(req("/resolve?ids=3806463703&deps=1", OK), ENV, {});
    assert.equal(res.status, 200);
    assert.equal(res.headers.get("Access-Control-Allow-Origin"), OK);
    assert.equal(res.headers.get("Vary"), "Origin");
    assert.equal(res.headers.get("Cache-Control"), "public, max-age=3600");
    const body = await res.json();
    assert.deepEqual(Object.keys(body).sort(), ["items", "roots", "truncated"]);
    assert.equal(Object.keys(body.items).length, 10);
    assert.ok(seen.every((u) => u.startsWith("https://api.steampowered.com/IPublishedFileService/GetDetails/v1/?")));
  });
});
