// What the page shows, mirrored in the address so any view can be shared (F, the filters); what this browser keeps
// (the watchlist and the basket); and `act`, the few actions modules lower down call without importing the modules
// that define them (main.js fills it in), so the import graph has no cycles.
import { store } from "./util.js";

export const DEFAULTS = { deals: "1", stores: [], hubs: [], kind: "", min: 0, max: 90, stock: "in", status: [], best: false, watch: false, recent: false,
                          category: "", q: "", pmin: "", pmax: "", sort: "score" };
export const F = { ...DEFAULTS, stores: [], hubs: [], status: [] };
export function resetFilters() { Object.assign(F, DEFAULTS, { stores: [], hubs: [], status: [] }); }

export function readURL() {
  const p = new URLSearchParams(location.search);
  resetFilters();
  for (const k of ["deals", "kind", "stock", "category", "q", "pmin", "pmax", "sort"]) if (p.has(k)) F[k] = p.get(k);
  for (const k of ["min", "max"]) if (p.has(k)) F[k] = +p.get(k);
  for (const k of ["stores", "status", "hubs"]) if (p.get(k)) F[k] = p.get(k).split(",");
  if (!p.get("hubs")) F.hubs = store.get("myHubs", []).map(String);  // the hubs this browser chose last time
  F.best = p.get("best") === "1"; F.watch = p.get("watch") === "1"; F.recent = p.get("recent") === "1";
}
export function writeURL() {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(F)) {
    if (Array.isArray(v) ? v.length : v !== DEFAULTS[k] && v !== "" && v !== false)
      p.set(k, Array.isArray(v) ? v.join(",") : v === true ? "1" : v);
  }
  const prod = new URLSearchParams(location.search).get("p");
  if (prod) p.set("p", prod);
  history.replaceState(null, "", p.toString() ? "?" + p : location.pathname);
}

// kept in this browser
export const watch = store.get("watch", {});    // id -> {target, last, name, store}
export const basket = store.get("basket", {});  // id -> how many

// filled in by main.js: apply() (the filters changed: address, chips, cards, brief) and showProduct(id)
export const act = { apply() {}, showProduct() {} };
