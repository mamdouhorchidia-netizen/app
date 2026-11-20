// The basket (🧺, kept in this browser): where each item is cheapest, using the same product found in other stores.
import { $, esc, fmt, LABELS, store } from "./util.js";
import { t } from "./t.js";
import { basket } from "./state.js";
import { idsURL } from "./api.js";
import { panel } from "./sheets.js";

const tx = s => esc(t(s));
export function saveBasket() { store.set("basket", basket); const n = Object.values(basket).reduce((a, b) => a + b, 0);
  for (const id of ["basketN", "tabBasketN"]) { $(id).hidden = !n; $(id).textContent = n; } }
async function showBasket() {
  const ids = Object.keys(basket);
  if (!ids.length) { panel(`<h2>${tx("🧺 Basket")}</h2><p class="tip">${tx("Open any product and tap ")}<b>${tx("Add to basket")}</b>${tx(". The basket shows the cheapest store for each item, using the same products found in other stores.")}</p>`); return; }
  const r = await (await fetch(idsURL(ids))).json();
  const rows = r.items.map(d => {
    const opts = [{ id: d.id, store: d.store, price: d.price, in_stock: d.in_stock }, ...d.matches].filter(o => o.in_stock);
    const best = opts.sort((a, b) => a.price - b.price)[0] || { ...d };
    return { d, best, qty: basket[d.id] || 1, saving: Math.max(0, d.price - best.price) };
  });
  const total = rows.reduce((s, x) => s + x.best.price * x.qty, 0), listed = rows.reduce((s, x) => s + x.d.price * x.qty, 0);
  const byStore = {}; rows.forEach(x => byStore[x.best.store] = (byStore[x.best.store] || 0) + x.best.price * x.qty);
  const plan = Object.entries(byStore);
  // the name cell is the store's own words ([dir=auto]): left as it is, "out of stock" included, as it always was
  panel(`<h2>${tx("🧺 Basket")}</h2>
    <table class="cmp bk">${rows.map(x => `<tr><td dir="auto">${esc(x.d.name.slice(0, 48))}${x.d.in_stock ? "" : " <span class='tag'>out of stock</span>"}</td>
      <td><span style="color:var(--${x.best.store});font-weight:650">${tx(LABELS[x.best.store] || x.best.store)}</span><br>${tx(`${fmt(x.best.price)} EGP`)}${x.saving ? `<br><span class="tag good">${tx(`save ${fmt(x.saving * x.qty)}`)}</span>` : ""}</td>
      <td><span class="qty"><button data-q="${x.d.id}" data-d="-1">−</button>${x.qty}<button data-q="${x.d.id}" data-d="1">+</button></span></td></tr>`).join("")}</table>
    <h4>${tx("Cheapest plan")}</h4>
    <p>${plan.map(([s, v], i) => `<b>${tx(LABELS[s] || s)}</b>${tx(` ${fmt(v)} EGP` + (i < plan.length - 1 ? " · " : ""))}`).join("")}</p>
    <p class="bigprice">${tx(`${fmt(total)} EGP`)}</p>${listed > total ? `<p class="tag good">${tx(`saves ${fmt(listed - total)} EGP against buying each where you found it`)}</p>` : ""}
    <div class="row"><button class="btn" id="bClear">${tx("Empty basket")}</button></div>`);
  document.querySelectorAll("[data-q]").forEach(b => b.onclick = () => { const id = b.dataset.q;
    basket[id] = (basket[id] || 0) + +b.dataset.d; if (basket[id] <= 0) delete basket[id]; saveBasket(); showBasket(); });
  $("bClear").onclick = () => { for (const k of Object.keys(basket)) delete basket[k]; saveBasket(); $("panel").close(); };
}
export function initBasket() { $("basketBtn").onclick = $("tabBasket").onclick = showBasket; }
