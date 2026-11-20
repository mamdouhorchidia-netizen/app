// The cards on screen and keeping them right: the first page and "more" as you scroll; refreshes patched in place
// by id (an unchanged card, its picture and whatever has focus are never re-created); a re-rank held back with
// "N new deals ↑" while someone is reading further down; one short summary for screen readers.
import { $, esc, setSkew } from "./util.js";
import { L, agoL, t } from "./t.js";
import { F, act } from "./state.js";
import { PAGE, dealsURL, getJSON } from "./api.js";
import { card, checkedText, partsOf, patchCard, placeholder } from "./card.js";
import { contact, net, setOffline, setStatus } from "./status.js";
import { renderFacets } from "./filters.js";
import { toggleWatch } from "./alerts.js";

export const view = { items: [], total: 0, loading: false, dealsStale: false };
let seen = new Set(), pending = null, countHTML = "", facetsKey = "", gen = 0;
const seenAt = new Map();  // product id -> when its store last checked the price (server time)
export const noteSeen = (id, at) => { if (at && !(seenAt.get(id) >= at)) seenAt.set(id, at); };
const onScreen = new Set();
const visIO = new IntersectionObserver(es => es.forEach(e => e.isIntersecting ? onScreen.add(e.target) : onScreen.delete(e.target)),
  { rootMargin: "200px 0px" });
function bind(n, d, p) { n._d = d; n._p = p; seen.add(d.id); visIO.observe(n); tickAgo(n); }
function makeCard(d, fresh) {
  const p = partsOf(d), tpl = document.createElement("template");
  tpl.innerHTML = card(d, fresh, p);
  const n = tpl.content.firstElementChild;
  bind(n, d, p);
  if (fresh) setTimeout(() => n.classList.remove("fresh"), 3000);
  return n;
}
const emptyHTML = () => `<div class="empty">${esc(t(F.watch ? "Your watchlist is empty: tap ★ on any product to follow its price." :
  "Nothing matches these filters yet. The first sweep of each store takes about 15 minutes, and price-drop deals need a few days of history."))}</div>`;
function renderAll(list) {
  const ps = list.map(partsOf), grid = $("grid");
  onScreen.clear(); visIO.disconnect();
  grid.innerHTML = list.length ? list.map((d, i) => card(d, false, ps[i])).join("") : emptyHTML();
  if (list.length) [...grid.children].forEach((n, i) => bind(n, list[i], ps[i]));
  view.items = list.slice(); pending = null; hidePill();
}
function appendCards(list) {
  const have = new Set(view.items.map(d => d.id)), add = list.filter(d => !have.has(d.id));
  if (!view.items.length) return renderAll(add);
  const frag = document.createDocumentFragment();
  add.forEach(d => frag.append(makeCard(d, false)));
  $("grid").append(frag);
  view.items = view.items.concat(add);
}
// the longest run of cards already in the right order: those stay put, the rest move around them
function lis(a) {
  const tails = [], at = [], prev = [];
  a.forEach((v, k) => { if (v < 0) return;
    let lo = 0, hi = tails.length; while (lo < hi) { const m = (lo + hi) >> 1; if (tails[m] < v) lo = m + 1; else hi = m; }
    tails[lo] = v; at[lo] = k; prev[k] = lo ? at[lo - 1] : -1; });
  const keep = new Set(); for (let k = at[tails.length - 1]; k !== undefined && k >= 0; k = prev[k]) keep.add(k);
  return keep;
}
function restructure(next, have) {  // insert, remove and reorder cards, keeping every card that stays (and focus)
  const grid = $("grid"), focused = document.activeElement, had = grid.contains(focused);
  const keepIds = new Set(next.map(d => d.id));
  for (const [id, n] of have) if (!keepIds.has(id)) { visIO.unobserve(n); onScreen.delete(n); n.remove(); have.delete(id); }
  const pos = new Map([...have.values()].map((n, i) => [n, i]));
  let added = 0;
  const nodes = next.map(d => { let n = have.get(d.id); if (!n) { n = makeCard(d, !seen.has(d.id)); added++; } return n; });
  const stay = lis(nodes.map(n => pos.has(n) ? pos.get(n) : -1));
  let ref = null;
  for (let k = nodes.length - 1; k >= 0; k--) {
    const n = nodes[k];
    if (!stay.has(k)) {
      if (n.isConnected && grid.moveBefore) { try { grid.moveBefore(n, ref); } catch { grid.insertBefore(n, ref); } }
      else grid.insertBefore(n, ref);
    }
    ref = n;
  }
  view.items = next.slice(); pending = null; hidePill();
  if (had && focused.isConnected && document.activeElement !== focused) focused.focus({ preventScroll: true });
  return added;
}
function nearTop() {  // the first row of cards is still (half) in view below the header
  const c = $("grid").querySelector(".card"); if (!c) return true;
  const r = c.getBoundingClientRect();
  return r.top >= document.querySelector("header").getBoundingClientRect().bottom - r.height / 2;
}
function patchGrid(next, force = false) {
  const grid = $("grid"), have = new Map();
  for (const n of grid.children) if (n._d) have.set(n._d.id, n);
  if (!have.size || !next.length) { renderAll(next); return; }
  const byId = new Map(next.map(d => [d.id, d]));
  let changed = 0, added = 0, held = 0;
  for (const [id, n] of have) { const d = byId.get(id); if (d && patchCard(n, d)) changed++; }  // prices: always, in place
  view.items = view.items.map(d => byId.get(d.id) || d);
  const same = next.length === view.items.length && next.every((d, i) => view.items[i].id === d.id);
  if (same) { pending = null; hidePill(); }
  else if (force || nearTop()) added = restructure(next, have);
  else { pending = next; held = next.filter(d => !have.has(d.id)).length; showPill(held); }  // don't move what they're reading
  announce(changed, added, held);
}
const noun = n => F.deals === "1" && !F.watch && !F.recent ? L(`new deal${n > 1 ? "s" : ""}`, "عرض جديد") : L(`new product${n > 1 ? "s" : ""}`, "منتج جديد");
function showPill(n) {
  const b = $("newPill"), text = n ? `${n} ${noun(n)} ↑` : L("Updated order ↑", "الترتيب اتحدّث ↑");
  if (b.textContent !== text) b.textContent = text;
  b.style.top = Math.round(document.querySelector("header").getBoundingClientRect().bottom + 10) + "px";
  b.hidden = false;
}
function hidePill() { $("newPill").hidden = true; }
function announce(changed, added, held) {  // one short summary for screen readers, never one per card
  const parts = [];
  if (changed) parts.push(L(`${changed} price${changed > 1 ? "s" : ""} changed`, `${changed} سعر اتغير`));
  if (added) parts.push(`${added} ${noun(added)}`);
  if (held) parts.push(L(`${held} ${noun(held)} above: the button at the top shows them`, `${held} ${noun(held)} فوق: الزرار اللي فوق بيعرضهم`));
  if (parts.length) $("announce").textContent = parts.join(" · ");
}
export function tickAgo(root = document) {  // times that count up by themselves; only text that changed is written
  for (const n of root.querySelectorAll("[data-ago]")) { const s = agoL(+n.dataset.ago); if (n.textContent !== s) n.textContent = s; }
  for (const c of root.querySelectorAll(".chk")) {
    const k = c.closest(".card"), s = checkedText(k && k._d && seenAt.get(k._d.id));
    if (c.textContent !== s) c.textContent = s;
  }
}
// the cards on screen (and near it) first, at most 60: whose "checked … ago" to ask for
export function seenIds() {
  const cards = [...$("grid").children].filter(n => n._d);
  let a = cards.findIndex(n => onScreen.has(n)), b = a;
  for (let k = cards.length - 1; k > a; k--) if (onScreen.has(cards[k])) { b = k; break; }
  if (a < 0) a = b = 0;
  return cards.map((n, k) => [k < a ? a - k : k > b ? k - b : 0, n._d.id]).sort((x, y) => x[0] - y[0]).slice(0, 60).map(x => x[1]);
}

function facets(r, force) {
  window.lastFacets = r.facets;
  const k = JSON.stringify([r.facets, r.labels]);
  if (!force && k === facetsKey) return;
  facetsKey = k;
  // a refresh that redraws the filters puts keyboard focus back on the same box
  const a = document.activeElement, box = a && a.closest && a.closest("#fStores, #fHubs, #hubBar, #qStores");
  const which = box && (a.value || a.dataset.h);
  renderFacets(r);
  if (box) [...$(box.id).querySelectorAll("input, button")].find(x => (x.value || x.dataset.h) === which)?.focus({ preventScroll: true });
}
function count(r) {
  view.total = r.total;
  const h = `${view.total.toLocaleString()} <small>${esc(t(F.deals === "1" && !F.watch ? (view.total === 1 ? "deal" : "deals") : "products"))}</small>`;
  if (h !== countHTML) { countHTML = h; $("count").innerHTML = h; }
}
function took(list) { list.forEach(d => noteSeen(d.id, d.last_seen)); }
export async function load({ append = false } = {}) {
  if (append && view.loading) return;
  const my = append ? gen : ++gen;
  view.loading = true;
  if (!append) { $("grid").innerHTML = Array.from({ length: 8 }, () => '<div class="skel"></div>').join(""); onScreen.clear(); visIO.disconnect(); pending = null; hidePill(); }
  try {
    // the first page is always fetched in full (it's a new view), but its ETag is kept for the refreshes
    const r = await getJSON(dealsURL(append ? view.items.length : 0, PAGE), { cond: false });
    if (my !== gen) return;  // the filters changed meanwhile
    contact(r.now);
    if (!append) view.dealsStale = false;
    setStatus(r.status || net.status, r.changes_last_hour ?? net.changes);
    facets(r, !append); count(r); took(r.items);
    if (append) appendCards(r.items); else renderAll(r.items);
  } catch (e) {
    if (my !== gen) return;
    if (!append) view.dealsStale = true;
    setOffline(true);
    // never leave skeletons up: put back the last good cards, or say nothing could be loaded
    if ($("grid").querySelector(".skel")) { if (view.items.length) renderAll(view.items);
      else $("grid").innerHTML = `<div class="empty">${esc(t("Couldn't load the deals. They'll show here as soon as the server answers."))}</div>`; }
  } finally { if (my === gen) view.loading = false; }
}
// the cards already shown, asked again with their ETag: 304 when none changed, else patched in place
export async function refreshDeals() {
  if (view.loading) return false;
  const my = gen, url = dealsURL(0, Math.max(PAGE, view.items.length));
  const r = await getJSON(url, { cond: !view.dealsStale });
  if (my !== gen || view.loading) return false;
  view.dealsStale = false;
  if (!r) return false;
  setSkew(r.now);
  if (!net.hasPulse) setStatus(r.status || [], r.changes_last_hour || 0);
  facets(r); count(r); took(r.items);
  patchGrid(r.items);
  return true;
}

export function initGrid() {
  // a picture that fails to load becomes the product's first letter
  document.addEventListener("error", e => { const img = e.target;
    if (img.tagName === "IMG" && img.dataset.i) img.replaceWith(placeholder(img.dataset.i));
  }, true);
  $("newPill").onclick = () => {
    const kb = document.activeElement === $("newPill");
    if (pending) patchGrid(pending, true);
    hidePill();
    window.scrollTo({ top: 0, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    if (kb) $("grid").querySelector("[data-detail]")?.focus({ preventScroll: true });
  };
  let scrollQ = 0;
  addEventListener("scroll", () => {  // back near the top by themselves: show what was held
    if (pending && !scrollQ) scrollQ = requestAnimationFrame(() => { scrollQ = 0; if (pending && nearTop()) patchGrid(pending, true); });
  }, { passive: true });
}
export function initGridInput() {
  $("grid").addEventListener("click", e => {
    const c = e.target.closest(".card"); if (!c) return;
    const d = view.items.find(x => x.id === +c.dataset.id); if (!d) return;
    if (e.target.closest("[data-watch]")) { e.stopPropagation(); toggleWatch(d, e.target.closest("[data-watch]")); return; }
    if (e.target.closest("[data-open]")) return;  // the link opens the store's app at this product
    if (e.target.closest("[data-detail]") && (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey)) return;
    e.preventDefault();
    act.showProduct(d.id);
  });
  $("grid").addEventListener("keydown", e => {  // Space opens the details too (Enter already clicks the link)
    if (e.key !== " " || !e.target.matches("[data-detail]")) return;
    e.preventDefault(); e.target.click();
  });
  new IntersectionObserver(es => { if (es[0].isIntersecting && view.items.length && view.items.length < view.total && !view.loading) load({ append: true }); },
    { rootMargin: "800px" }).observe($("more"));
}
