// Dev-only mock of the modlist Worker. Run: node tests/js/mock-worker.mjs
import http from "node:http";
import { readFileSync } from "node:fs";

const fx = JSON.parse(readFileSync(new URL("./fixtures/resolve-domini.json", import.meta.url), "utf8"));
const CORS = { "Access-Control-Allow-Origin": "*", "Content-Type": "application/json" };

function resolve(ids, deps) {
  const items = {};
  const queue = [...ids];
  while (queue.length) {
    const id = queue.shift();
    if (items[id] || !fx.items[id]) continue;
    items[id] = fx.items[id];
    if (!deps) continue;
    queue.push(...(fx.items[id].contains || []), ...(fx.items[id].requires || []));
  }
  return { roots: ids.filter((i) => items[i]), items, truncated: false };
}

http.createServer((req, res) => {
  const u = new URL(req.url, "http://localhost");
  if (req.method === "OPTIONS") {
    res.writeHead(204, { ...CORS, "Access-Control-Allow-Headers": "*" });
    return res.end();
  }
  if (u.pathname !== "/resolve") {
    res.writeHead(404, CORS);
    return res.end("{}");
  }
  const ids = (u.searchParams.get("ids") || "").split(",").filter((s) => /^\d+$/.test(s));
  const deps = u.searchParams.get("deps") !== "0";
  res.writeHead(200, CORS);
  res.end(JSON.stringify(resolve(ids, deps)));
}).listen(8787, () => console.log("mock worker on http://localhost:8787"));
