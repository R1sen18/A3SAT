import { test } from "node:test";
import assert from "node:assert/strict";
import { parseInput, chunk, encodeShare, decodeShare } from "../../docs/modlist/modlist.js";

const URL1 = "https://steamcommunity.com/sharedfiles/filedetails/?id=3806463703";

test("parseInput extracts ids from mixed input, dedupes, collects ignored", () => {
  const text = [
    URL1,
    "http://steamcommunity.com/workshop/filedetails/?id=450814997&searchtext=cba",
    "https://steamcommunity.com/sharedfiles/filedetails/?l=german&id=3510959070",
    "  3020755032  ",
    "3779310113, 2817145194 2817149713",
    "hello",
    URL1,
  ].join("\n");
  assert.deepEqual(parseInput(text), {
    ids: ["3806463703", "450814997", "3510959070", "3020755032", "3779310113", "2817145194", "2817149713"],
    ignored: ["hello"],
  });
});

test("parseInput ignores URL without id=", () => {
  const url = "https://steamcommunity.com/app/107410/workshop/";
  assert.deepEqual(parseInput(url), { ids: [], ignored: [url] });
});

test("chunk splits into batches", () => {
  const ids = Array.from({ length: 120 }, (_, i) => String(i + 1));
  assert.deepEqual(chunk(ids, 50).map((c) => c.length), [50, 50, 20]);
  assert.deepEqual(chunk([], 50), []);
});

test("share round trip", () => {
  assert.deepEqual(decodeShare("#" + encodeShare("Domini & Friends", ["1", "22"])), {
    name: "Domini & Friends",
    ids: ["1", "22"],
  });
});

test("decodeShare drops non-numeric ids and defaults name", () => {
  assert.deepEqual(decodeShare("ids=1,abc,3"), { name: "Server Mods", ids: ["1", "3"] });
});
