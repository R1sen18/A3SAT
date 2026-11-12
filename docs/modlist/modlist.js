// Pure logic for the modlist creator. No DOM, no Node built-ins.

export function parseInput(text) {
  const ids = [];
  const seen = new Set();
  const ignored = [];
  for (const line of String(text).split(/\r?\n/)) {
    for (const token of line.split(/[\s,]+/)) {
      if (!token) continue;
      let id = null;
      const m = token.match(/id=(\d+)/);
      if (m) id = m[1];
      else if (/^\d+$/.test(token)) id = token;
      if (id === null) {
        ignored.push(token);
      } else if (!seen.has(id)) {
        seen.add(id);
        ids.push(id);
      }
    }
  }
  return { ids, ignored };
}

export function chunk(ids, size) {
  const out = [];
  for (let i = 0; i < ids.length; i += size) out.push(ids.slice(i, i + size));
  return out;
}

export function encodeShare(name, ids) {
  return `name=${encodeURIComponent(name)}&ids=${ids.join(",")}`;
}

export function decodeShare(hash) {
  const params = new URLSearchParams(String(hash).replace(/^#/, ""));
  const name = params.get("name") || "Server Mods";
  const ids = (params.get("ids") || "").split(",").filter((s) => /^\d+$/.test(s));
  return { name, ids };
}

// ---- List state model and dependency order ----

const ARMA3_APP_ID = 107410;

function placeholder(id) {
  return {
    id, title: null, size: 0, updated: 0, requires: [], available: true,
    appId: null, addedBy: "user", resolved: false,
  };
}

const sortKey = (mod) => (mod.title ?? mod.id).toLowerCase();
const byKey = (a, b) => {
  const ka = sortKey(a), kb = sortKey(b);
  return ka < kb ? -1 : ka > kb ? 1 : a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
};

export function emptyState(name = "Server Mods") {
  return { name, mods: {} };
}

export function addUnresolved(state, ids) {
  const next = structuredClone(state);
  for (const id of ids) {
    if (!next.mods[id]) next.mods[id] = placeholder(id);
  }
  return next;
}

export function applyResolve(state, response, mode) {
  const next = structuredClone(state);
  const items = response.items || {};
  const userIds = new Set();
  for (const root of response.roots || []) {
    userIds.add(root);
    const item = items[root];
    if (item && item.isCollection) for (const c of item.contains || []) userIds.add(c);
  }
  for (const [id, item] of Object.entries(items)) {
    if (item.isCollection) continue;
    const existing = next.mods[id];
    if (mode === "load" && !existing) continue;
    let addedBy = existing ? existing.addedBy : userIds.has(id) ? "user" : "auto";
    if (userIds.has(id)) addedBy = "user";
    next.mods[id] = {
      id,
      title: item.title ?? null,
      size: item.size ?? 0,
      updated: item.updated ?? 0,
      requires: [...(item.requires || [])],
      available: item.available !== false,
      appId: item.appId ?? null,
      addedBy,
      resolved: true,
    };
  }
  return next;
}

export function requiredBy(state, id) {
  return Object.values(state.mods)
    .filter((m) => m.id !== id && m.requires.includes(id))
    .sort(byKey)
    .map((m) => m.id);
}

export function removeMod(state, id) {
  const dependents = requiredBy(state, id);
  const next = structuredClone(state);
  delete next.mods[id];
  return { state: next, dependents };
}

export function missingRequirements(state, id) {
  const mod = state.mods[id];
  if (!mod) return [];
  return mod.requires.filter((r) => !state.mods[r]);
}

export function exportOrder(mods) {
  const byId = new Map(mods.map((m) => [m.id, m]));
  const remaining = new Map();
  for (const m of mods) {
    remaining.set(m.id, new Set(m.requires.filter((r) => r !== m.id && byId.has(r))));
  }
  const out = [];
  while (remaining.size) {
    const ready = [...remaining.keys()]
      .filter((id) => remaining.get(id).size === 0)
      .map((id) => byId.get(id))
      .sort(byKey);
    if (!ready.length) {
      out.push(...[...remaining.keys()].map((id) => byId.get(id)).sort(byKey));
      break;
    }
    const m = ready[0];
    out.push(m);
    remaining.delete(m.id);
    for (const deps of remaining.values()) deps.delete(m.id);
  }
  return out;
}

export function exportPlan(state) {
  const all = Object.values(state.mods);
  const excluded = all.filter((m) => !m.available || (m.resolved && m.appId !== ARMA3_APP_ID));
  const excludedIds = new Set(excluded.map((m) => m.id));
  const candidates = all.filter((m) => !excludedIds.has(m.id));
  const unresolved = candidates.filter((m) => !m.resolved || !m.title).map((m) => m.id);
  const included = exportOrder(candidates);
  return { ok: unresolved.length === 0 && included.length > 0, unresolved, included, excluded };
}
