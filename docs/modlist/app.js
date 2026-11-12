// DOM wiring only. All logic lives in modlist.js.
import {
  parseInput, chunk, encodeShare, decodeShare, emptyState, addUnresolved, applyResolve,
  removeMod, requiredBy, missingRequirements, exportPlan, exportOrder,
  buildPresetHtml, parsePresetHtml, presetFilename,
} from "./modlist.js";
import { WORKER_URL } from "./config.js";

const $ = (id) => document.getElementById(id);
const api = (new URLSearchParams(location.search).get("api") || WORKER_URL || "").replace(/\/$/, "");
let state = emptyState();
let busy = false;
const titleCache = {}; // titles seen from Steam, so a removed mod can still be named in "missing:"

function el(tag, props = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") e.className = v;
    else if (k === "onclick") e.addEventListener("click", v);
    else e.setAttribute(k, v);
  }
  for (const kid of kids) e.append(kid);
  return e;
}

const fmtSize = (b) => (b >= 1e9 ? (b / 1e9).toFixed(2) + " GB" : Math.round(b / 1e6) + " MB");
const fmtDate = (s) => (s ? new Date(s * 1000).toISOString().slice(0, 10) : "unknown");
const label = (id) => (state.mods[id] && state.mods[id].title) || titleCache[id] || id;

async function fetchResolve(ids, deps) {
  let res;
  try {
    res = await fetch(`${api}/resolve?ids=${ids.join(",")}${deps ? "" : "&deps=0"}`);
  } catch {
    throw new Error("Could not reach the lookup service. Check your connection and try again.");
  }
  if (!res.ok) throw new Error(`The lookup service returned an error (${res.status}). Try again.`);
  try {
    const data = await res.json();
    for (const [id, it] of Object.entries(data.items || {})) if (it.title) titleCache[id] = it.title;
    return data;
  } catch { throw new Error("The lookup service sent an unreadable answer. Try again."); }
}

function showError(msg) { $("error").textContent = msg || ""; }

async function runAdd(ids) {
  showError("");
  $("trunc").textContent = "";
  let next = state, truncated = false;
  for (const part of chunk(ids, 50)) {
    const resp = await fetchResolve(part, true);
    truncated = truncated || !!resp.truncated;
    next = applyResolve(next, resp, "add");
  }
  state = next;
  if (truncated) $("trunc").textContent = "Warning: the lookup hit its limit, so some requirements may be missing from the list. Check the warnings below.";
}

async function runLoad(ids, name) {
  showError("");
  $("trunc").textContent = "";
  state = addUnresolved({ ...state, name }, ids);
  render();
  let truncated = false;
  try {
    for (const part of chunk(ids, 50)) {
      const resp = await fetchResolve(part, false);
      truncated = truncated || !!resp.truncated;
      state = applyResolve(state, resp, "load");
    }
    // Look up titles of missing requirements for display only (nothing is added).
    const missing = [...new Set(Object.keys(state.mods).flatMap((id) => missingRequirements(state, id)))];
    for (const part of chunk(missing, 50)) await fetchResolve(part, false);
  } catch (e) { showError(e.message); }
  if (truncated) $("trunc").textContent = "Warning: the lookup hit its limit, so some mods may not have been resolved.";
}

async function guarded(fn) {
  if (busy) return;
  busy = true; render();
  try { await fn(); } catch (e) { showError(e.message); }
  busy = false; render();
}

function onAdd() {
  const { ids, ignored } = parseInput($("input").value);
  $("ignored").textContent = ignored.length ? "Ignored (not a Workshop link or ID): " + ignored.join(", ") : "";
  if (!ids.length) return showError(ignored.length ? "" : "Paste at least one link or ID.");
  guarded(async () => {
    await runAdd(ids);
    $("input").value = "";
  });
}

function onRemove(id) {
  const { state: next, dependents } = removeMod(state, id);
  if (dependents.length) {
    const names = dependents.map(label).join(", ");
    if (!confirm(`${label(id)} is required by: ${names}.\nRemove it anyway?`)) return;
  }
  state = next;
  render();
}

function renderMod(m) {
  const title = el("div", { class: "title" });
  if (m.title) {
    const a = el("a", { href: `https://steamcommunity.com/sharedfiles/filedetails/?id=${m.id}`, target: "_blank", rel: "noopener" });
    a.textContent = m.title;
    title.append(a);
  } else title.textContent = m.resolved ? m.id : `${m.id} (not looked up yet)`;

  const meta = el("div", { class: "meta" });
  meta.textContent = m.resolved ? `${fmtSize(m.size)} · updated ${fmtDate(m.updated)}` : "";
  const rm = el("button", { onclick: () => onRemove(m.id) });
  rm.textContent = "Remove";
  const li = el("li", { class: "mod" }, el("div", { class: "line" }, title, rm), meta);

  const badge = (text, cls = "badge") => { const b = el("span", { class: cls }); b.textContent = text; li.append(b); return b; };
  const reqBy = requiredBy(state, m.id);
  if (reqBy.length) badge("required by " + reqBy.map(label).join(", "));
  else if (m.addedBy === "user") badge("added by you");
  if (!m.available) badge("unavailable on Steam", "badge warn");
  else if (m.resolved && m.appId !== 107410) badge("not an Arma 3 item", "badge warn");
  const missing = missingRequirements(state, m.id);
  if (missing.length) {
    badge("missing: " + missing.map(label).join(", "), "badge warn");
    const btn = el("button", { onclick: () => guarded(() => runAdd(missing)) });
    btn.textContent = "Add missing";
    li.append(el("div", { class: "row" }, btn));
  }
  return li;
}

function render() {
  const mods = exportOrder(Object.values(state.mods));
  $("list").replaceChildren(...mods.map(renderMod));
  $("empty").hidden = mods.length > 0;

  const plan = exportPlan(state);
  const total = plan.included.reduce((s, m) => s + m.size, 0);
  $("totals").textContent = plan.included.length ? `${plan.included.length} mods · ${fmtSize(total)}` : "";

  let reason = "";
  if (!plan.included.length && !plan.unresolved.length) reason = "Add at least one mod to export.";
  else if (plan.unresolved.length) reason = "Export is blocked until every mod has a title from Steam (not looked up: " + plan.unresolved.join(", ") + "). Try again when the lookup service is reachable.";
  $("reason").textContent = reason;
  $("excluded").textContent = plan.excluded.length
    ? "Not exported (unavailable or not Arma 3): " + plan.excluded.map((m) => m.title || m.id).join(", ")
    : "";

  $("add").disabled = busy || !api;
  $("import").disabled = busy || !api;
  $("export").disabled = busy || !plan.ok;
  if (!api) showError("Lookup service not configured");

  state.name = $("name").value || "Server Mods";
  const hash = mods.length ? "#" + encodeShare(state.name, mods.map((m) => m.id)) : "";
  history.replaceState(null, "", location.pathname + location.search + hash);
}

function onExport() {
  const plan = exportPlan(state);
  if (!plan.ok) return;
  const name = $("name").value || "Server Mods";
  const blob = new Blob([buildPresetHtml(name, plan.included)], { type: "text/html;charset=utf-8" });
  const a = el("a", { href: URL.createObjectURL(blob), download: presetFilename(name) });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

async function onImport(ev) {
  const file = ev.target.files[0];
  ev.target.value = "";
  if (!file) return;
  const parsed = parsePresetHtml(await file.text());
  if (!parsed) return showError("Not an Arma 3 launcher preset");
  $("name").value = parsed.name;
  guarded(() => runLoad(parsed.ids, parsed.name));
}

$("add").addEventListener("click", onAdd);
$("import").addEventListener("change", onImport);
$("export").addEventListener("click", onExport);
$("name").addEventListener("input", render);
$("copy").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(location.href); $("copy").textContent = "Copied"; }
  catch { $("copy").textContent = "Copy the address bar"; }
  setTimeout(() => ($("copy").textContent = "Copy share link"), 2000);
});

const startHash = decodeShare(location.hash);
render();
if (startHash.ids.length && api) {
  $("name").value = startHash.name;
  guarded(() => runLoad(startHash.ids, startHash.name));
}
