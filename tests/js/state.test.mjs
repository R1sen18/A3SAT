import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import {
  emptyState, addUnresolved, applyResolve, removeMod, requiredBy,
  missingRequirements, exportPlan, exportOrder,
} from "../../docs/modlist/modlist.js";

const load = () =>
  JSON.parse(fs.readFileSync(new URL("./fixtures/resolve-domini.json", import.meta.url), "utf8"));
const ids = (mods) => mods.map((m) => m.id);

test("golden_domini_add", () => {
  const s = applyResolve(emptyState(), load(), "add");
  const plan = exportPlan(s);
  assert.equal(plan.ok, true);
  assert.deepEqual(ids(plan.included), [
    "450814997", "3020755032", "3816263034", "3510959070", "3779310113",
    "3780409647", "2817145194", "2817149713", "3794701829", "3806463703",
  ]);
  const users = Object.values(s.mods).filter((m) => m.addedBy === "user").map((m) => m.id);
  assert.deepEqual(users, ["3806463703"]);
});

test("remove_sticks_and_reports_dependents", () => {
  const s0 = applyResolve(emptyState(), load(), "add");
  const { state, dependents } = removeMod(s0, "3510959070");
  assert.deepEqual([...dependents].sort(), ["3779310113", "3780409647", "3806463703"]);
  assert.deepEqual(missingRequirements(state, "3779310113"), ["3510959070"]);
  const again = applyResolve(state, load(), "load");
  assert.equal("3510959070" in again.mods, false);
  assert.deepEqual(requiredBy(s0, "450814997").length > 0, true);
});

test("add_respects_user_flag", () => {
  let s = addUnresolved(emptyState(), ["450814997"]);
  assert.equal(s.mods["450814997"].addedBy, "user");
  s = applyResolve(s, load(), "add");
  assert.equal(s.mods["450814997"].addedBy, "user");
  assert.equal(s.mods["450814997"].resolved, true);
  assert.equal(s.mods["3510959070"].addedBy, "auto");
});

test("unavailable_requirement_excluded_dependent_kept", () => {
  const f = load();
  f.items["3816263034"].available = false;
  const plan = exportPlan(applyResolve(emptyState(), f, "add"));
  assert.ok(ids(plan.excluded).includes("3816263034"));
  assert.ok(ids(plan.included).includes("3806463703"));
  assert.equal(plan.ok, true);
});

test("non_arma_item_excluded", () => {
  const f = load();
  f.items["3794701829"].appId = 431960;
  const plan = exportPlan(applyResolve(emptyState(), f, "add"));
  assert.ok(ids(plan.excluded).includes("3794701829"));
  assert.ok(!ids(plan.included).includes("3794701829"));
});

test("unresolved_blocks_export", () => {
  const plan = exportPlan(addUnresolved(emptyState(), ["1"]));
  assert.equal(plan.ok, false);
  assert.deepEqual(plan.unresolved, ["1"]);
});

test("cycle_is_deterministic", () => {
  const mk = (id, title, requires) => ({ id, title, size: 0, updated: 0, requires, available: true, appId: 107410, addedBy: "user", resolved: true });
  const a = mk("1", "A", ["2"]), b = mk("2", "B", ["1"]);
  assert.deepEqual(ids(exportOrder([a, b])), ["1", "2"]);
  assert.deepEqual(ids(exportOrder([b, a])), ["1", "2"]);
});
