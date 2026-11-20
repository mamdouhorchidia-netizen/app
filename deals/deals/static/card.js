// A card: picture, price, one verdict, name, store, open. Written in the page's language as it is drawn.
// A card is made of parts; when new data arrives each part is compared on its own and only what changed is touched
// (patchCard): marks (the discount badge), vd (the verdict chip), price, store (its chip), meta (saving or unit
// price), name, href (the store link), img. The ★ and the store link are never re-created, only their attributes.
// Also the product view's tags, its sparkline and score ring.
import { appHref, appLabel, esc, fmt, fmtWas, LABELS, money, nowS, span } from "./util.js";
import { AR, L, UNIT_AR, t } from "./t.js";
import { watch } from "./state.js";

export function sparkSVG(pts, w = 72, h = 26) {
  if (!pts || pts.length < 2) return "";
  const t0 = pts[0][0], t1 = pts[pts.length - 1][0], ps = pts.map(p => p[1]);
  const lo = Math.min(...ps), hi = Math.max(...ps), range = hi - lo || 1;
  const X = x => 2 + (w - 4) * (x - t0) / ((t1 - t0) || 1), Y = v => hi === lo ? h / 2 : 3 + (h - 6) * (1 - (v - lo) / range);
  let d = `M${X(pts[0][0]).toFixed(1)} ${Y(pts[0][1]).toFixed(1)}`;
  for (let i = 1; i < pts.length; i++) d += ` H${X(pts[i][0]).toFixed(1)} V${Y(pts[i][1]).toFixed(1)}`;
  const first = ps[0], last = ps[ps.length - 1];
  const col = last < first ? "var(--drop)" : last > first ? "var(--accent)" : "var(--faint)";
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" aria-hidden="true"><path d="${d}" fill="none" stroke="${col}" stroke-width="1.8"/>
    <circle cx="${X(t1).toFixed(1)}" cy="${Y(last).toFixed(1)}" r="2.4" fill="${col}"/></svg>`;
}
export function scoreSVG(s) {
  const r = 15, c = 2 * Math.PI * r, col = s >= 70 ? "#1f9d5c" : s >= 40 ? "#a87a00" : "#8f8b84";
  return `<svg class="score" viewBox="0 0 38 38" role="img" aria-label="${L(`Deal score ${s} of 100`, `تقييم العرض ${s} من 100`)}"><title>${L(`Deal score ${s}/100`, `تقييم العرض ${s} من 100`)}</title>
    <circle cx="19" cy="19" r="17.5" fill="rgba(255,255,255,.96)"/>
    <circle cx="19" cy="19" r="${r}" fill="none" stroke="#ece9e2" stroke-width="3"/>
    <circle cx="19" cy="19" r="${r}" fill="none" stroke="${col}" stroke-width="3" stroke-linecap="round" stroke-dasharray="${(c * s / 100).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 19 19)"/>
    <text x="19" y="23.5" text-anchor="middle">${s}</text></svg>`;
}
// a tag's text in the page's language, escaped
const tx = s => esc(t(s));
export function tagsFor(d) {
  const tg = [];
  if (d.deal_label) tg.push(`<span class="tag hot">${tx(d.deal_label)}</span>`);
  if (d.flags.includes("lowest")) tg.push(`<span class="tag good">${tx("lowest in 90 days")}</span>`);
  if (d.flags.includes("new")) tg.push(d.prev_price != null && d.price > d.prev_price
    ? `<span class="tag warn">${tx("went up ")}<span data-ago="${+d.since || 0}"></span></span>`
    : `<span class="tag">${tx("dropped ")}<span data-ago="${+d.since || 0}"></span></span>`);
  if (d.flags.includes("rose")) tg.push(`<span class="tag warn">${tx(`was ${fmt(d.recent_low)} lately`)}</span>`);
  if (d.flags.includes("back")) tg.push(`<span class="tag good">${tx("back in stock")}</span>`);
  if (d.cheapest && d.matches.length) tg.push(`<span class="tag good">${tx("cheapest of all stores")}</span>`);
  else if (d.matches.length) { const m = d.matches.find(x => x.in_stock && x.price < d.price);
    if (m) tg.push(`<span class="tag warn">${tx(`${fmt(m.price)} at ${LABELS[m.store] || m.store}`)}</span>`); }
  if (d.flags.includes("steady")) tg.push(`<span class="tag warn">${tx("discount never moves")}</span>`);
  if (d.flags.includes("inflated")) tg.push(`<span class="tag warn">${tx('⚠️ "was" price looks inflated')}</span>`);
  if (d.promo_code) tg.push(`<span class="tag hot" title="${tx("Code")}">${tx(`🎟️ ${d.promo_code}: ${fmt(d.promo_price)}`)}</span>`);
  if (d.rhythm && d.rhythm.next_in_days != null && d.rhythm.next_in_days <= 3) tg.push(`<span class="tag warn">${tx("⏳ sale due soon")}</span>`);
  if (!d.in_stock) tg.push(`<span class="tag">${tx("out of stock")}</span>`);
  return tg.join("");
}
const initial = n => (n || "?").trim()[0] || "?";
// the one verdict a card shows, by priority: a fake or suspicious discount, then "buy now", the lowest price in
// 90 days, cheaper in another store; then the rest of what the data says. The reason is in the product view.
export function verdictChip(d) {
  const f = d.flags || [], v = d.verdict || {}, ms = d.matches || [];
  const chip = (cls, text, why) => `<span class="vchip ${cls}"${why ? ` title="${tx(why)}"` : ""}>${tx(text)}</span>`;
  if (f.includes("steady")) return chip("bad", "discount never moves", v.why);
  if (f.includes("inflated")) return chip("bad", "Check the price", v.why);
  if (!d.in_stock) return chip("", "out of stock");
  if (v.label === "Buy now") return chip("good", "Buy now", v.why);
  if (f.includes("lowest")) return chip("good", "lowest in 90 days");
  if (v.label === "Cheaper elsewhere") return chip("warn", "Cheaper elsewhere", v.why);
  if (d.cheapest && ms.length) return chip("good", "cheapest of all stores");
  if (v.label === "Wait" || v.label === "Good price") return chip(v.tone === "good" ? "good" : "warn", v.label, v.why);
  if (d.promo_code) return chip("hot", `🎟️ ${d.promo_code}: ${fmt(d.promo_price)}`);
  if (d.deal_label) return chip("hot", d.deal_label);
  if (f.includes("back")) return chip("good", "back in stock");
  return "";
}
export function partsOf(d) {
  const badge = d.discount_pct > 0 ? `<span class="badge ${d.kind === "drop" ? "drop" : ""}" title="${tx(d.kind === "drop" ? "under its usual price" : "the store's own discount")}">−${Math.round(d.discount_pct)}%</span>` : "";
  const vd = verdictChip(d);
  return {
    marks: badge, vd, vdKey: vd.replace(/ title="[^"]*"/g, ""),  // the verdict's explanation follows the clock: not a change
    price: `<span class="now">${money(d.price)} <small>${tx("EGP")}</small></span>${d.kind === "platform" ? `<span class="was">${fmtWas(d.original_price)}</span>` : ""}`,
    store: `<span class="sn">${tx(LABELS[d.store] || d.store)}</span>` + (d.hub_name ? `<span class="hs"> · </span><bdi class="hb" dir="auto">${esc(d.hub_name)}</bdi>` : ""),
    meta: d.saving > 0 ? `<span>${tx(`save ${fmt(d.saving >= 100 ? Math.round(d.saving) : d.saving)}`)}</span>` : d.unit_text ? `<span class="unit">${tx(d.unit_text)}</span>`
      : d.kind === "drop" ? `<span>${tx(`usually ${fmt(d.usual_price)}`)}</span>` : "",
    name: d.name || "", href: appHref(d), img: d.image || "",
  };
}
const CHECKED = L("When the store last checked this price", "آخر مرة اتشيّك فيها على السعر في المتجر");
export function card(d, fresh, p = partsOf(d)) {
  const i = initial(d.name);
  return `<article class="card${fresh ? " fresh" : ""}${d.in_stock ? "" : " out"}" data-id="${d.id}">
    <div class="pic">${p.img ? `<img loading="lazy" src="${esc(p.img)}" alt="" data-i="${esc(i)}">` : `<span class="ph">${tx(i)}</span>`}
      <span class="marks">${p.marks}</span>
      <button class="watch" aria-label="${tx("Watch this price")}" aria-pressed="${!!watch[d.id]}" data-watch>★</button></div>
    <div class="body"><div class="pr">${p.price}</div>
      <div class="sig"><span class="vd">${p.vd}</span><span class="chk" title="${CHECKED}"></span></div>
      <div class="name" dir="auto"><a href="/p/${d.id}" data-detail>${esc(p.name)}</a></div>
      <div class="foot"><span class="store" style="background:var(--${esc(d.store)}-bg,#555);color:var(--${esc(d.store)}-fg,#fff)">${p.store}</span><span class="meta">${p.meta}</span></div>
      <div class="cta"><a href="${esc(p.href)}" target="_blank" rel="noopener" data-open><span>${tx(`Open in ${appLabel[d.store] || "store"} ↗`)}</span></a></div>
    </div></article>`;
}
// a picture that fails to load becomes the product's first letter
export function placeholder(letter) { const s = document.createElement("span"); s.className = "ph"; s.textContent = t(letter); return s; }

function cue(n, dir) {  // the brief tint on a card whose price changed
  clearTimeout(n._cueT); n.classList.remove("moved", "up", "down"); void n.offsetWidth;
  n.classList.add("moved", dir); n._cueT = setTimeout(() => n.classList.remove("moved", "up", "down"), 8000);
}
export function patchCard(n, d) {  // returns true when the price moved
  const o = n._p, od = n._d, p = partsOf(d), q = s => n.querySelector(s);
  if (o.marks !== p.marks) q(".marks").innerHTML = p.marks;
  if (o.vdKey !== p.vdKey) q(".vd").innerHTML = p.vd;
  if (o.price !== p.price) q(".pr").innerHTML = p.price;
  if (o.store !== p.store) q(".store").innerHTML = p.store;
  if (o.meta !== p.meta) q(".meta").innerHTML = p.meta;
  if (o.name !== p.name) q("[data-detail]").textContent = p.name;
  if (o.href !== p.href) q("[data-open]").href = p.href;
  if (o.img !== p.img) {
    const pic = q(".pic"), old = pic.querySelector("img, .ph");
    let nu;
    if (p.img) { nu = document.createElement("img"); nu.loading = "lazy"; nu.alt = ""; nu.dataset.i = initial(d.name); nu.src = p.img; }
    else nu = placeholder(initial(d.name));
    old ? old.replaceWith(nu) : pic.prepend(nu);
  }
  n.classList.toggle("out", !d.in_stock);
  const w = String(!!watch[d.id]), wb = q("[data-watch]");
  if (wb.getAttribute("aria-pressed") !== w) wb.setAttribute("aria-pressed", w);
  n._p = p; n._d = d;
  if (od && (od.price !== d.price || od.in_stock !== d.in_stock)) cue(n, d.price > od.price ? "up" : "down");
  return !!od && od.price !== d.price;
}
// "✓ 40 s ago": when the store last checked this price (seconds in steps of 10), next to the verdict;
// short, to sit next to the verdict on a phone-width card: the ✓ before it (CSS) says "checked"
export function checkedText(at) {
  if (!at) return "";
  const s = Math.max(0, nowS() - at);
  if (s < 10) return L("just now", "دلوقتي");
  const [n, u] = s < 60 ? [Math.floor(s / 10) * 10, "s"] : span(s);
  return AR ? `من ${n} ${UNIT_AR[u]}` : `${n} ${u} ago`;
}
