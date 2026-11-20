// Talking to the server. /api answers are no-store, so the browser never revalidates them: the page keeps each
// ETag and asks with it. getJSON gives null for a 304 (what's on screen is still right). keep:false leaves the
// ETag to be kept (body.keep()) once the answer has been used, so a step that failed is asked in full again.
import { jitter, store } from "./util.js";
import { F, watch } from "./state.js";
import { understand } from "./search.js";

const etags = new Map();
export const clearETags = () => etags.clear();
export async function getJSON(url, { cond = true, keep = true } = {}) {
  const tag = cond && etags.get(url);
  const res = await fetch(url, tag ? { headers: { "If-None-Match": tag } } : undefined);
  if (res.status === 304) return null;
  if (!res.ok) { const e = new Error("HTTP " + res.status); e.status = res.status; throw e; }
  const body = await res.json(), et = res.headers.get("ETag");
  const save = () => { if (!et) return; etags.delete(url); etags.set(url, et); if (etags.size > 40) etags.delete(etags.keys().next().value); };
  if (keep) save(); else Object.defineProperty(body, "keep", { value: save });
  return body;
}

// the cards: the filters in F (and what a plain-language search understood) as /api/deals parameters
export const PAGE = 48;
export function params(offset, limit) {
  const p = new URLSearchParams({ deals: F.deals, stock: F.stock, sort: F.sort, limit, offset });
  if (F.stores.length) p.set("stores", F.stores.join(","));
  if (F.hubs.length) p.set("hubs", F.hubs.join(","));
  if (F.kind) p.set("kind", F.kind);
  if (F.min) p.set("min", F.min);
  if (F.max < 90) p.set("max", F.max);
  if (F.status.length) p.set("status", F.status.join(","));
  if (F.best) p.set("best", "1");
  if (F.category) p.set("category", F.category);
  const nl = understand(F.q);
  if (nl.q) p.set("q", nl.q);
  for (const [k, v] of Object.entries(nl.set)) p.set(k, v);
  if (F.pmin !== "") p.set("pmin", F.pmin);
  if (F.pmax !== "") p.set("pmax", F.pmax);
  if (F.watch) { p.set("ids", Object.keys(watch).join(",") || "0"); p.set("deals", "0"); p.set("stock", "all"); }
  else if (F.recent) { p.set("ids", store.get("recent", []).join(",") || "0"); p.set("deals", "0"); p.set("stock", "all"); p.set("sort", "newest"); }
  return p;
}
export const dealsURL = (offset, limit) => "/api/deals?" + params(offset, limit);
// these products, whatever the filters say (the watchlist check, the basket)
export const idsURL = ids => `/api/deals?deals=0&stock=all&limit=500&ids=${ids.join(",")}`;
export const pulse = () => getJSON("/api/pulse", { keep: false });
export const changes = after => getJSON(after == null ? "/api/changes?limit=24" : `/api/changes?limit=24&after=${after}`);
export const seen = ids => getJSON("/api/seen?ids=" + ids.join(","), { cond: false });
export const brief = () => getJSON("/api/brief");
export const product = async id => (await fetch("/api/product/" + id)).json();

// the next pulse: ~10 s; after a failure 10, 20, 40… up to 300 s
export const pollDelay = failN => failN ? jitter(Math.min(300, 10 * 2 ** (failN - 1)) * 1000, .15) : jitter(10000);
