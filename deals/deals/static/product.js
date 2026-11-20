// The product view (a dialog over the cards; /?p=ID opens it): price, verdict and why, sale rhythm, history chart,
// the same product in other stores, a target-price alert, the basket, share.
import { $, ago, appHref, appLabel, esc, fmt, isAndroid, LABELS, store } from "./util.js";
import { LOC, t } from "./t.js";
import { basket, watch } from "./state.js";
import { product } from "./api.js";
import { scoreSVG, sparkSVG, tagsFor } from "./card.js";
import { askNotify, saveWatch } from "./alerts.js";
import { saveBasket } from "./basket.js";
import { closeX, openDialog, toast } from "./sheets.js";
import { tickAgo } from "./grid.js";

const tx = s => esc(t(s));
export async function showProduct(id) {
  let r;
  try { r = await product(id); } catch { return; }
  if (r.error) return;
  const p = r.product, d = r.item && r.item.id ? r.item : null, h = r.history;
  store.set("recent", [id, ...store.get("recent", []).filter(x => x !== id)].slice(0, 60));
  const u = new URLSearchParams(location.search); u.set("p", id); history.replaceState(null, "", "?" + u);
  const w = watch[id];
  const rows = d ? [d, ...(r.matches || [])].sort((a, b) => a.price - b.price) : [];
  const link = d ? appHref(d) : p.url;
  const tags = d && tagsFor(d);
  const browser = d && (isAndroid ? d.links.browser : d.links.web);
  $("dbody").innerHTML = `${closeX()}
    <div class="dlg-head"><div class="img">${p.image ? `<img src="${esc(p.image)}" alt="" onerror="this.remove()">` : ""}</div>
      <div><h2 id="dname" dir="auto">${esc(p.name)}</h2>
        <div class="meta"><span style="color:var(--${esc(p.store)});font-weight:650">${tx(LABELS[p.store] || p.store)}</span><span dir="auto">${esc(p.category || "")}</span><span>${tx(`tracked ${ago(p.first_seen).replace(" ago", "")}`)}</span></div></div></div>
    <div class="dprice"><span class="bigprice">${fmt(p.price)} <small>${tx("EGP")}</small></span>
      ${d && d.kind === "platform" ? `<span class="was">${fmt(d.original_price)}</span>` : ""}
      ${d && d.unit_text ? `<span class="unit">${tx(d.unit_text)}</span>` : ""}
      ${d && d.spark && d.spark.length > 1 ? `<span class="dspark">${sparkSVG(d.spark, 96, 28)}</span>` : ""}</div>
    ${tags ? `<div class="tags">${tags}</div>` : ""}
    ${d && d.verdict ? `<div class="verdict ${d.verdict.tone}"><b>${tx(d.verdict.label)}</b><span>${tx(d.verdict.why)}</span></div>` : ""}
    ${d && d.rhythm ? `<div class="rhythm">⏳ <b>${tx("Sale rhythm")}</b><br>${tx(d.rhythm.text)}</div>` : ""}
    ${d && d.promo_code ? `<div class="rhythm">${tx("🎟️ With the code ")}<b>${esc(d.promo_code)}</b>${tx(` at checkout: ${fmt(d.promo_price)} EGP`)}</div>` : ""}
    ${d && d.why && d.why.length ? `<div class="why">${scoreSVG(d.score)}<ul>${d.why.map(x => `<li>${tx(x)}</li>`).join("")}</ul></div>` : ""}
    <h4>${tx("Price history")}</h4>
    <svg class="chart" id="chart" viewBox="0 0 640 200" preserveAspectRatio="none" role="img" aria-label="${tx("Price history")}"></svg>
    <div class="tip" id="tip">${tx(h.length > 1 ? "Touch or point at the chart to see each price." : "One price so far: the chart fills in as it changes.")}</div>
    ${rows.length > 1 ? `<h4>${tx("Same product in other stores")}</h4><table class="cmp">${rows.map((x, i) => `<tr>
      <td><span style="color:var(--${esc(x.store)});font-weight:650">${tx(LABELS[x.store] || x.store)}</span><br><span class="meta" style="display:inline" dir="auto">${esc(x.name.slice(0, 60))}</span></td>
      <td class="${i === 0 ? "best" : ""}">${tx(`${fmt(x.price)} EGP`)}${i === 0 ? `<br>${tx("cheapest")}` : ""}${x.in_stock ? "" : `<br>${tx("out of stock")}`}</td>
      <td><a class="btn" style="padding:5px 10px" href="${esc(appHref(x))}" target="_blank" rel="noopener">${tx("Open ↗")}</a></td></tr>`).join("")}</table>` : ""}
    <div class="alert"><label for="target">${tx("Tell me when it's at or below")}</label>
      <input id="target" type="number" inputmode="decimal" min="0" step="0.5" placeholder="${Math.floor(p.price * 0.9)}" value="${w && w.target ? w.target : ""}">${tx(" EGP")}
      <button class="btn" id="setTarget">${tx(w ? "Update alert" : "Watch")}</button></div>
    <div class="row">
      <a class="btn primary" href="${esc(link)}" target="_blank" rel="noopener">${tx(`Open in ${appLabel[p.store] || "store"} app ↗`)}</a>
      ${d && p.store !== "talabat" && browser && browser !== link
        ? `<a class="btn" href="${esc(browser)}" target="_blank" rel="noopener">${tx("Open in browser")}</a>` : ""}
      <button class="btn" id="toBasket">${tx(`🧺 ${basket[id] ? "In basket (" + basket[id] + ")" : "Add to basket"}`)}</button>
      <button class="btn" id="share">${tx("Share")}</button>
      <a class="btn" href="/p/${id}">${tx("Product page")}</a>
    </div>`;
  tickAgo($("dbody"));
  openDialog($("dlg"));
  drawChart(h, p.price);
  $("setTarget").onclick = () => {
    const v = parseFloat($("target").value);
    watch[id] = { ...(watch[id] || { last: p.price, name: p.name, store: p.store }), target: isFinite(v) ? v : null };
    saveWatch(); askNotify();
    toast(t(isFinite(v) ? `We'll tell you when it's ${fmt(v)} EGP or less.` : "Watching for any price drop."));
    $("setTarget").textContent = t("Update alert");
    document.querySelectorAll(`.card[data-id="${id}"] [data-watch]`).forEach(b => b.setAttribute("aria-pressed", "true"));
  };
  $("toBasket").onclick = () => { basket[id] = (basket[id] || 0) + 1; saveBasket(); $("toBasket").textContent = t(`🧺 In basket (${basket[id]})`);
    toast(t("Added to your basket. 🧺 shows where it's cheapest.")); };
  $("share").onclick = async () => {
    const url = location.origin + "/p/" + id, text = `${p.name}: ${fmt(p.price)} EGP`;
    try { if (navigator.share) await navigator.share({ title: text, text, url });
          else { await navigator.clipboard.writeText(url); toast(t("Link copied.")); } } catch {}
  };
}

function drawChart(h, current) {
  const now = Date.now() / 1000;
  const pts = h.filter(r => r.in_stock).map(r => [r.at, r.price]);
  if (!pts.length) pts.push([now - 60, current]);
  pts.push([now, pts[pts.length - 1][1]]);
  const svg = $("chart"), W = Math.max(280, Math.round(svg.getBoundingClientRect().width) || 640), H = 200, L = 46, R = 8, T = 10, B = 24;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const t0 = pts[0][0], t1 = Math.max(now, t0 + 1), ps = pts.map(p => p[1]);
  const lo = Math.min(...ps), hi = Math.max(...ps), pad = (hi - lo) * .15 || hi * .05 || 1;
  const X = x => L + (W - L - R) * (x - t0) / (t1 - t0), Y = v => T + (H - T - B) * (1 - (v - (lo - pad)) / ((hi + pad) - (lo - pad)));
  let d = `M${X(pts[0][0]).toFixed(1)} ${Y(pts[0][1]).toFixed(1)}`;
  for (let i = 1; i < pts.length; i++) d += ` H${X(pts[i][0]).toFixed(1)} V${Y(pts[i][1]).toFixed(1)}`;
  const area = d + ` V${H - B} H${X(pts[0][0]).toFixed(1)} Z`;
  const date = at => new Date(at * 1000).toLocaleDateString(LOC(), { day: "numeric", month: "short" });
  svg.innerHTML = `<path class="ar" d="${area}"/><path class="ln" d="${d}"/>
    <line class="ax" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"/>
    <text x="4" y="${Y(hi) + 4}">${fmt(hi)}</text>${hi !== lo ? `<text x="4" y="${Y(lo) + 4}">${fmt(lo)}</text>` : ""}
    <text x="${L}" y="${H - 6}">${date(t0)}</text><text x="${W - R}" y="${H - 6}" text-anchor="end">${tx("now")}</text>
    <g class="hover" visibility="hidden"><line y1="${T}" y2="${H - B}"/><circle r="4"/></g>`;
  const g = svg.querySelector(".hover");
  const move = e => {
    const rect = svg.getBoundingClientRect(), x = Math.max(L, Math.min(W - R, (e.clientX - rect.left) / rect.width * W));
    const at = t0 + (x - L) / (W - L - R) * (t1 - t0);
    let i = 0; while (i + 1 < pts.length && pts[i + 1][0] <= at) i++;
    const v = pts[i][1];
    g.setAttribute("visibility", "visible");
    g.querySelector("line").setAttribute("x1", x); g.querySelector("line").setAttribute("x2", x);
    g.querySelector("circle").setAttribute("cx", x); g.querySelector("circle").setAttribute("cy", Y(v));
    $("tip").textContent = t(`${fmt(v)} EGP on ${new Date(Math.min(at, now) * 1000).toLocaleString(LOC(), { dateStyle: "medium", timeStyle: "short" })}`);
  };
  svg.onpointermove = move; svg.onpointerdown = move;
}

export function initProduct() {
  $("dlg").addEventListener("close", () => { const u = new URLSearchParams(location.search); u.delete("p");
    history.replaceState(null, "", u.toString() ? "?" + u : location.pathname); });
}
