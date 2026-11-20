// "⚡ Today in 30 seconds" (the home page, before any filter): one row of tiles you swipe, each one tap away.
import { $, esc, fmt } from "./util.js";
import { t } from "./t.js";
import { F, act } from "./state.js";
import { brief } from "./api.js";
import { syncControls } from "./filters.js";

const tx = s => esc(t(s));
const isHome = () => { const p = new URLSearchParams(location.search); p.delete("hubs"); return !p.toString(); };
let briefOn = false, briefHTML = "";
export async function refreshBrief() {  // asked with its ETag: on the home page, when the catalog was rebuilt
  if (!isHome()) { $("brief").hidden = true; return; }
  const b = await brief();
  if (!b) { $("brief").hidden = !briefOn; return; }  // 304: the brief already drawn is still right
  briefOn = !!b.deals;
  if (!b.deals) { $("brief").hidden = true; return; }
  const st = (n, label, a) => `<button type="button" class="bt st ${a}" data-act="${a}"><b>${n.toLocaleString()}</b><span>${tx(label)}</span></button>`;
  const best = b.biggest_saving;
  // one row of tiles that scrolls sideways; redrawn only when what it says changed (so focus stays put)
  const html = `<div class="brief-row hscroll"><div class="bt head"><h2 id="briefH">${tx("⚡ Today in 30 seconds")}</h2><div class="sub">${tx("What the stores changed while you weren't looking")}</div></div>
    ${st(b.dropped_24h, "price drops in 24 h", "new")}${b.record_lows ? st(b.record_lows, "lowest price in 90 days", "lowest") : ""}
    ${b.fake ? st(b.fake, "fake discounts caught", "fake") : ""}${b.soon ? st(b.soon, "sales due within 3 days", "soon") : ""}
    ${b.promo ? st(b.promo, "extra off with a code", "promo") : ""}${st(b.deals, "real deals right now", "real")}
    ${best ? `<button type="button" class="bt st best" data-id="${best.id}">${tx("💰 Biggest saving now: ")}<b dir="auto">${esc(best.name.slice(0, 60))}</b>${tx(` · save ${fmt(best.saving)} EGP`)}</button>` : ""}</div>`;
  $("brief").hidden = false;
  if (html === briefHTML) return;
  briefHTML = html; $("brief").innerHTML = html;
  $("brief").querySelectorAll(".st[data-act]").forEach(x => x.onclick = () => {
    F.status = [x.dataset.act]; if (x.dataset.act === "soon") F.deals = "0";
    F.sort = x.dataset.act === "new" ? "newest" : "score"; syncControls(); act.apply(); $("brief").hidden = true; });
  const bs = $("brief").querySelector(".best"); if (bs) bs.onclick = () => act.showProduct(+bs.dataset.id);
}
export const loadBrief = () => refreshBrief().catch(() => {});
