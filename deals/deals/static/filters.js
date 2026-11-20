// The filters: the panel (a rail on wide screens, a sheet on phones), the quick chips in the header row, the
// talabat hub bar, and the chips that show (and undo) each active filter. Drawn in the page's language.
import { $, esc, LABELS, store } from "./util.js";
import { t } from "./t.js";
import { F, act, resetFilters } from "./state.js";
import { showUnderstood } from "./search.js";

const tx = s => esc(t(s));
export function syncControls() {
  document.querySelectorAll(".seg").forEach(seg => seg.querySelectorAll("button").forEach(b =>
    b.setAttribute("aria-pressed", String(String(F[seg.dataset.key]) === b.dataset.v))));
  $("pmin").value = F.min; $("pmax").value = F.max; paintSlider();
  $("fStatus").querySelectorAll("input[value]").forEach(i => i.checked = F.status.includes(i.value));
  $("fBest").checked = F.best; $("fWatch").checked = F.watch;
  $("prmin").value = F.pmin; $("prmax").value = F.pmax; $("sort").value = F.sort; $("q").value = F.q;
  $("qReal").setAttribute("aria-pressed", String(F.status.includes("real")));
  for (const id of ["watchBtn", "tabWatch"]) $(id).setAttribute("aria-pressed", String(F.watch));
  $("qStores").querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", String(F.stores.includes(b.value))));
}
const pctText = (a, b) => a === 0 && b === 90 ? "any" : b === 90 ? `${a}%+` : `${a}–${b}%`;
function paintSlider() {
  const a = +$("pmin").value, b = +$("pmax").value;
  $("pctFill").style.left = (a / 90 * 100) + "%"; $("pctFill").style.right = (100 - b / 90 * 100) + "%";
  $("pctLabel").textContent = t(pctText(a, b));
}
export function setHubs(ids) { F.hubs = ids; store.set("myHubs", ids); act.apply(); }
export function renderFacets(r) {
  const fs = r.facets.store || {}, all = [...new Set([...Object.keys(LABELS), ...Object.keys(fs)])];
  const label = s => r.labels?.[s] || LABELS[s] || s;
  $("fStores").innerHTML = all.map(s => `<label class="opt"><input type="checkbox" value="${esc(s)}" ${F.stores.includes(s) ? "checked" : ""}>
    <span class="dot" style="background:var(--${esc(s)},#888)"></span>${tx(label(s))}<span class="c">${fs[s] ?? 0}</span></label>`).join("");
  $("fStores").querySelectorAll("input").forEach(i => i.onchange = () => {
    F.stores = [...$("fStores").querySelectorAll("input:checked")].map(x => x.value); act.apply(); });
  // the same stores as quick chips in the header row (those that have something to show, and any chosen)
  $("qStores").innerHTML = all.filter(s => fs[s] || F.stores.includes(s)).map(s => `<button type="button" class="qc" value="${esc(s)}" aria-pressed="${F.stores.includes(s)}">
    <span class="dot" style="background:var(--${esc(s)}-bg,#888)"></span>${tx(label(s))}</button>`).join("");
  $("qStores").querySelectorAll("button").forEach(b => b.onclick = () => {
    F.stores = F.stores.includes(b.value) ? F.stores.filter(x => x !== b.value) : [...F.stores, b.value]; syncControls(); act.apply(); });
  // one entry per hub: products saved before the hub had a name carry its number instead
  const hubMap = {};
  for (const [k, n] of Object.entries(r.facets.hub || {})) { const [id, name] = k.split("|");
    const h = hubMap[id] || (hubMap[id] = { name, n: 0 }); h.n += n; if (/^\d+$/.test(h.name) && !/^\d+$/.test(name)) h.name = name; }
  const fh = Object.entries(hubMap).sort((a, b) => b[1].n - a[1].n).map(([id, h]) => [`${id}|${/^\d+$/.test(h.name) ? "Hub " + id : h.name}`, h.n]);
  $("hubGroup").hidden = fh.length < 2 && !F.hubs.length;
  $("fHubs").innerHTML = fh.map(([k, n]) => { const [id, name] = k.split("|");
    return `<label class="opt"><input type="checkbox" value="${esc(id)}" ${F.hubs.includes(id) ? "checked" : ""}>${tx(" " + name)}<span class="c">${n}</span></label>`; }).join("");
  $("fHubs").querySelectorAll("input").forEach(i => i.onchange = () => {
    setHubs([...$("fHubs").querySelectorAll("input:checked")].map(x => x.value)); });
  // the same choice as a bar above the deals: which talabat hubs' stock and prices to show (remembered)
  $("hubBar").hidden = fh.length < 2 && !F.hubs.length;
  $("hubBar").innerHTML = `<span class="lbl">${tx("📍 talabat hubs:")}</span><button data-h="" class="${F.hubs.length ? "" : "on"}">${tx("All hubs")}</button>` +
    fh.map(([k, n]) => { const [id, name] = k.split("|");
      return `<button data-h="${esc(id)}" class="${F.hubs.includes(id) ? "on" : ""}" dir="auto">${esc(name)}<span class="c">${n}</span></button>`; }).join("");
  $("hubBar").querySelectorAll("button").forEach(b => b.onclick = () => {
    const id = b.dataset.h;
    setHubs(!id ? [] : F.hubs.includes(id) ? F.hubs.filter(x => x !== id) : [...F.hubs, id]); });
  const fc = r.facets.category || {}, cats = Object.keys(fc);
  if (F.category && !cats.includes(F.category)) cats.unshift(F.category);
  $("fCat").innerHTML = `<option value="">${tx("All categories")}</option>` + cats.map(c =>
    `<option value="${esc(c)}" ${c === F.category ? "selected" : ""}>${tx(`${c} (${fc[c] ?? 0})`)}</option>`).join("");
  const k = r.facets.kind || {};
  $("kindCounts").textContent = t(`${k.platform || 0} platform discounts · ${k.drop || 0} price drops`);
}
export function activeChips() {
  const chips = [];
  if (F.deals === "0") chips.push(["All products", () => F.deals = "1"]);
  F.stores.forEach(s => chips.push([LABELS[s] || s, () => F.stores = F.stores.filter(x => x !== s)]));
  if (F.hubs.length) chips.push([`${F.hubs.length} talabat hub${F.hubs.length > 1 ? "s" : ""}`, () => { F.hubs = []; store.set("myHubs", []); }]);
  if (F.kind) chips.push([F.kind === "platform" ? "Platform discount" : "Price drop", () => F.kind = ""]);
  if (F.min || F.max < 90) chips.push([pctText(+$("pmin").value, +$("pmax").value) + " off", () => { F.min = 0; F.max = 90; }]);
  if (F.stock !== "in") chips.push([F.stock === "out" ? "Out of stock" : "Any availability", () => F.stock = "in"]);
  const names = { real: "Real deals", new: "Changed 24 h", lowest: "Lowest 90 d", label: "Event deals", back: "Back in stock", compare: "In other stores",
                  fake: "🕵️ Fake discounts caught", promo: "🎟️ Extra off with a code", soon: "⏳ Sale due within 3 days" };
  F.status.forEach(s => chips.push([names[s] || s, () => F.status = F.status.filter(x => x !== s)]));
  if (F.best) chips.push(["Cheapest across stores", () => F.best = false]);
  if (F.watch) chips.push(["Watchlist", () => F.watch = false]);
  if (F.recent) chips.push(["Recently viewed", () => F.recent = false]);
  if (F.category) chips.push([F.category, () => F.category = ""]);
  if (F.q) chips.push([`“${F.q}”`, () => F.q = ""]);
  if (F.pmin || F.pmax) chips.push([`${F.pmin || 0}–${F.pmax || "∞"} EGP`, () => { F.pmin = ""; F.pmax = ""; }]);
  $("activeChips").innerHTML = chips.map(([label], i) => `<button class="chip" data-i="${i}">${tx(label + " ")}<span class="cx" aria-hidden="true">×</span></button>`).join("");
  $("activeChips").querySelectorAll(".chip").forEach(b => b.onclick = () => { chips[+b.dataset.i][1](); syncControls(); act.apply(); });
  $("filterN").hidden = !chips.length; $("filterN").textContent = chips.length;
}

export function initFilters() {
  document.querySelectorAll(".seg").forEach(seg => seg.querySelectorAll("button").forEach(b => b.onclick = () => {
    F[seg.dataset.key] = b.dataset.v; syncControls(); act.apply(); }));
  let slideT;
  ["pmin", "pmax"].forEach(id => $(id).oninput = () => {
    let a = +$("pmin").value, b = +$("pmax").value;
    if (a > b) { if (id === "pmin") $("pmax").value = b = a; else $("pmin").value = a = b; }
    paintSlider(); clearTimeout(slideT); slideT = setTimeout(() => { F.min = a; F.max = b; act.apply(); }, 250); });
  $("fStatus").querySelectorAll("input[value]").forEach(i => i.onchange = () => {
    F.status = [...$("fStatus").querySelectorAll("input[value]:checked")].map(x => x.value); act.apply(); });
  $("fBest").onchange = () => { F.best = $("fBest").checked; act.apply(); };
  $("fWatch").onchange = () => { F.watch = $("fWatch").checked; act.apply(); };
  $("fCat").onchange = () => { F.category = $("fCat").value; act.apply(); };
  let priceT;
  ["prmin", "prmax"].forEach(id => $(id).oninput = () => { clearTimeout(priceT); priceT = setTimeout(() => {
    F.pmin = $("prmin").value; F.pmax = $("prmax").value; act.apply(); }, 400); });
  $("sort").onchange = () => { F.sort = $("sort").value; act.apply(); };
  let typing;
  $("q").oninput = () => { clearTimeout(typing); typing = setTimeout(() => { F.q = $("q").value; showUnderstood(); act.apply(); }, 300); };
  $("reset").onclick = () => { resetFilters(); store.set("myHubs", []); syncControls(); act.apply(); };
  $("watchBtn").onclick = $("tabWatch").onclick = () => { F.watch = !F.watch; syncControls(); act.apply(); };
  $("qReal").onclick = () => { F.status = F.status.includes("real") ? F.status.filter(x => x !== "real") : [...F.status, "real"]; syncControls(); act.apply(); };
}
