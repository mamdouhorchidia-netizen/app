// "Just dropped": the latest price moves in a still strip under the header, newest first; only the moves newer than
// the last one shown are asked for. Moves already shown keep their buttons (and focus); new ones go in front.
import { $, esc, fmt } from "./util.js";
import { agoL, t } from "./t.js";
import { act } from "./state.js";
import { changes } from "./api.js";

let tickerV = null, ticks = [];
export const tickerVersion = () => tickerV;
export const resetTicker = () => { tickerV = null; };
export async function loadTicker() {
  const r = await changes(tickerV);
  if (!r) return;
  const got = (r.changes || []).filter(c => c.before != null && c.before !== c.price), reset = tickerV == null || r.reset;
  tickerV = r.v ?? null;
  if (!reset && !got.length) return;
  const ids = new Set(got.map(c => c.rowid));
  ticks = (reset ? got : got.concat(ticks.filter(c => !ids.has(c.rowid)))).slice(0, 24);
  $("ticker").hidden = !ticks.length;
  if (!ticks.length) return;
  const rtl = document.documentElement.dir === "rtl", track = $("tickerTrack"), have = new Map();
  for (const n of track.children) have.set(+n.dataset.row, n);
  const nodes = ticks.map(c => { let n = have.get(c.rowid); if (n) return n;
    const down = c.price < c.before, tpl = document.createElement("template");
    tpl.innerHTML = `<button type="button" class="tick" data-id="${c.id}" data-row="${c.rowid}"><b dir="auto">${esc(c.name.slice(0, 60))}</b><span class="ln">${fmt(c.before)} ${rtl ? "←" : "→"} <span class="${down ? "down" : "up"}">${fmt(c.price)} ${down ? "▼" : "▲"}</span></span><span class="tm"><span class="ts">${esc(t(c.label))}</span><span data-ago="${+c.at || 0}">${agoL(c.at)}</span></span></button>`;
    n = tpl.content.firstElementChild; n.onclick = () => act.showProduct(+n.dataset.id); return n; });
  const keep = new Set(nodes);
  for (const n of [...track.children]) if (!keep.has(n)) n.remove();
  nodes.forEach((n, i) => { if (track.children[i] !== n) track.insertBefore(n, track.children[i] || null); });
}
