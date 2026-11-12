import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import {
  emptyState, applyResolve, exportPlan,
  buildPresetHtml, parsePresetHtml, presetFilename,
} from "../../docs/modlist/modlist.js";

// A3Bot's own acceptance checks, copied verbatim.
const A3BOT_TYPE_RE = /name="arma:Type"\s+content="(preset|list)"/;
const A3BOT_ROW_RE = /data-type="DisplayName">([^<]+)<\/td>.*?\?id=(\d+)/gs;

const fixture = () =>
  JSON.parse(fs.readFileSync(new URL("./fixtures/resolve-domini.json", import.meta.url), "utf8"));
const golden = () => exportPlan(applyResolve(emptyState(), fixture(), "add")).included;
// Mirrors A3Bot's parse_preset_html: only &amp; &lt; &gt; are decoded.
const unesc = (s) => s.replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").trim();

test("golden_export_passes_a3bot_checks", () => {
  const mods = golden();
  const html = buildPresetHtml("Domini", mods);
  assert.match(html.slice(0, 4096), A3BOT_TYPE_RE);
  const rows = [...html.matchAll(A3BOT_ROW_RE)];
  assert.equal(rows.length, 10);
  assert.deepEqual(rows.map((r) => r[2]), mods.map((m) => m.id));
  assert.deepEqual(rows.map((r) => unesc(r[1])), mods.map((m) => m.title));
  assert.ok(rows.some((r) => unesc(r[1]) === "gabes1410 AD – Arma Domini Factions"));
});

test("escapes_markup_in_titles", () => {
  const html = buildPresetHtml("n", [{ id: "1", title: 'A & B <x> "q"' }]);
  assert.ok(html.includes('>A &amp; B &lt;x&gt; "q"</td>'));
  assert.ok(!html.includes("<x>"));
});

test("quote_in_title_survives_a3bot_unescape", () => {
  const title = 'The "Best" Mod';
  const html = buildPresetHtml("n", [{ id: "7", title }]);
  const rows = [...html.matchAll(A3BOT_ROW_RE)];
  assert.equal(rows.length, 1);
  assert.equal(unesc(rows[0][1]), title);
});

test("round_trip_import", () => {
  const mods = golden();
  const p = parsePresetHtml(buildPresetHtml("Domini & Co", mods));
  assert.equal(p.name, "Domini & Co");
  assert.deepEqual(p.ids, mods.map((m) => m.id));
});

test("round_trip_name_with_quote", () => {
  const p = parsePresetHtml(buildPresetHtml('Say "hi" <b>', golden()));
  assert.equal(p.name, 'Say "hi" <b>');
});

test("imports_a3bot_preset_output", () => {
  const html = fs.readFileSync(new URL("./fixtures/a3bot-preset.html", import.meta.url), "utf8");
  const p = parsePresetHtml(html);
  assert.equal(p.ids.length, 10);
  assert.equal(p.name, "Domini Antistasi");
});

test("rejects_non_preset_html", () => {
  assert.equal(parsePresetHtml("<html><body>hi</body></html>"), null);
});

test("default_name_when_missing", () => {
  const p = parsePresetHtml('<meta name="arma:Type" content="list" />');
  assert.deepEqual(p, { name: "Server Mods", ids: [] });
});

test("presetFilename", () => {
  assert.equal(presetFilename("Domini: Run #1"), "Domini_ Run _1.html");
  assert.equal(presetFilename(""), "Server_Mods.html");
});
