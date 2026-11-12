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
