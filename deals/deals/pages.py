"""Pages the server writes out whole: a product's own page (/p/<id>, with a preview card when it's shared on
WhatsApp or Telegram), the stores (/stores), the categories (/cats) and the coupons (/coupons).

They're written in English and shown in Arabic by i18n.js, like the live page, so the language button works
everywhere. The preview tags (og:*) are Arabic already: link previews never run JavaScript.
"""

import html
import json
import time
from datetime import datetime, timezone, timedelta

from .stores import LABELS

CAIRO = timezone(timedelta(hours=2))
AR_STORE = {"talabat": "طلبات مارت", "amazon_eg": "أمازون مصر", "jumia": "جوميا", "breadfast": "بريدفاست", "dream2000": "دريم 2000",
            "kenzz": "كنز", "raya": "راية شوب"}
APP = {"talabat": "talabat", "amazon_eg": "Amazon", "jumia": "Jumia", "breadfast": "Breadfast", "dream2000": "Dream 2000",
       "kenzz": "Kenzz", "raya": "Raya"}


def e(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def money(n) -> str:
    if n is None:
        return ""
    return f"{n:,.2f}".rstrip("0").rstrip(".")


# the colours, type scale, spacing, radii and shadows come from /tokens.css, the same file the live page uses
CSS = """
* { box-sizing: border-box; } html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--bg); color: var(--ink); font: var(--fs-3)/1.5 var(--font); }
[dir=rtl] body { font-family: var(--font-ar); }
a { color: inherit; } .wrap { max-width: 1100px; margin: 0 auto; padding: 0 var(--sp-4); }
header { position: sticky; top: 0; z-index: 5; background: color-mix(in srgb, var(--bg) 94%, transparent); backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px); border-bottom: 1px solid var(--line); }
.top { display: flex; align-items: center; gap: var(--sp-2); height: var(--bar-h); }
.logo { font-weight: 750; font-size: var(--fs-4); text-decoration: none; display: flex; align-items: center; gap: var(--sp-2); margin-inline-end: auto; min-height: var(--tap); }
.logo .mark { width: 32px; height: 32px; border-radius: var(--r-1); background: var(--accent); display: grid; place-items: center; }
.logo .mark svg { width: 20px; height: 20px; }
.iconbtn { background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-2); height: var(--tap); min-width: var(--tap); padding: 0 10px; cursor: pointer; font: inherit; color: inherit; }
.nav { display: flex; gap: var(--sp-1); overflow-x: auto; scrollbar-width: none; font-size: var(--fs-2); margin-top: -4px; }
.nav a { color: var(--ink-2); text-decoration: none; padding: 0 var(--sp-3); white-space: nowrap; min-height: var(--tap); display: inline-flex; align-items: center; }
.nav a.on { color: var(--ink); font-weight: 650; box-shadow: inset 0 -2px 0 var(--accent); }
main { padding: var(--sp-5) 0 60px; } h1.title { font-size: var(--fs-5); margin: 0 0 var(--sp-4); line-height: 1.25; } h2 { font-size: var(--fs-4); margin: var(--sp-6) 0 var(--sp-3); }
.panel { background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-2); box-shadow: var(--elev-1); padding: var(--sp-4); }
.muted { color: var(--ink-2); } .small { font-size: var(--fs-2); }
.chip { display: inline-block; color: #fff; font-size: var(--fs-1); font-weight: 650; padding: 3px 8px; border-radius: 6px; }
.tag { display: inline-block; font-size: var(--fs-1); padding: 2px 8px; border-radius: 6px; background: var(--surface-2); color: var(--ink-2); margin: 0 0 4px 4px; }
.tag.good { background: var(--good-bg); color: var(--good); } .tag.hot { background: color-mix(in srgb, var(--accent) 14%, var(--surface)); color: var(--accent-text); }
.tag.warn { background: var(--warn-bg); color: var(--warn); }
.btn { display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: var(--tap); padding: 0 var(--sp-4); border-radius: var(--r-2); border: 1px solid var(--line);
  background: var(--surface); text-decoration: none; cursor: pointer; font: inherit; font-size: var(--fs-2); font-weight: 600; color: inherit; }
.btn.primary { background: var(--ink); border-color: var(--ink); color: var(--bg); }
.row { display: flex; flex-wrap: wrap; gap: var(--sp-2); margin-top: var(--sp-4); }
/* product */
.prod { display: grid; grid-template-columns: 300px 1fr; gap: var(--sp-5); }
.prod .img { aspect-ratio: 1; background: var(--plate); border-radius: var(--r-2); display: grid; place-items: center; overflow: hidden; border: 1px solid var(--line); isolation: isolate; }
.prod .img img { width: 86%; height: 86%; object-fit: contain; mix-blend-mode: multiply; }
.prod h1 { font-size: var(--fs-5); margin: var(--sp-2) 0; line-height: 1.3; }
.big { font-size: var(--fs-6); font-weight: 800; font-variant-numeric: tabular-nums; } .was { color: var(--ink-2); text-decoration: line-through; margin-inline-start: var(--sp-2); }
.verdict { margin-top: var(--sp-3); padding: var(--sp-3) var(--sp-4); border-radius: var(--r-2); background: var(--surface-2); }
.verdict.good { background: var(--good-bg); color: var(--good); } .verdict.warn { background: var(--warn-bg); color: var(--warn); }
.verdict b { display: block; font-size: var(--fs-3); }
.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--sp-2); margin-top: var(--sp-4); }
.stat { background: var(--surface-2); border-radius: var(--r-1); padding: var(--sp-2) var(--sp-3); } .stat b { display: block; font-size: var(--fs-3); font-variant-numeric: tabular-nums; }
.stat span { font-size: var(--fs-1); color: var(--ink-2); }
svg.chart { width: 100%; height: 190px; display: block; direction: ltr; }
table { width: 100%; border-collapse: collapse; } td { padding: var(--sp-2) 6px; border-top: 1px solid var(--line); vertical-align: middle; }
td.n { text-align: end; white-space: nowrap; font-weight: 650; }
ul.why { margin: var(--sp-2) 0 0; padding-inline-start: 18px; }
/* grids: the same card language as the live page */
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: var(--sp-3); }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-2); overflow: hidden; text-decoration: none; display: flex; flex-direction: column; box-shadow: var(--elev-1); }
.card .pic { aspect-ratio: 4 / 3; background: var(--pic); display: grid; place-items: center; position: relative; isolation: isolate; }
.card .pic::before { content: ""; position: absolute; inset: 6px; border-radius: var(--r-1); background: var(--plate); z-index: -1; }
.card .pic img { width: 80%; height: 80%; object-fit: contain; mix-blend-mode: multiply; }
.card .badge { position: absolute; top: var(--sp-2); left: var(--sp-2); background: var(--deal); color: var(--deal-ink); font-weight: 750; font-size: var(--fs-2); padding: 3px 7px; border-radius: 6px; }
[dir=rtl] .card .badge { left: auto; right: var(--sp-2); }
.card .b { padding: var(--sp-2) 10px var(--sp-3); display: flex; flex-direction: column; gap: 4px; } .card .nm { font-size: var(--fs-2); line-height: 1.35; height: 2.7em; overflow: hidden; }
.card .p { font-weight: 700; font-size: var(--fs-4); font-variant-numeric: tabular-nums; order: -1; }
.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: var(--sp-3); }
.tile { background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-2); padding: var(--sp-4); text-decoration: none; display: block; box-shadow: var(--elev-1); }
.tile .h { display: flex; align-items: center; gap: var(--sp-2); font-weight: 700; font-size: var(--fs-3); }
.tile .dot { width: 12px; height: 12px; border-radius: 50%; }
.tile .k { font-size: var(--fs-6); font-weight: 800; margin-top: var(--sp-2); font-variant-numeric: tabular-nums; } .tile .s { color: var(--ink-2); font-size: var(--fs-2); }
.coupon { display: flex; align-items: center; gap: var(--sp-3); }
.code { font: 700 var(--fs-3) ui-monospace, Menlo, Consolas, monospace; letter-spacing: .06em; border: 2px dashed var(--accent-text); color: var(--accent-text); padding: var(--sp-2) 14px;
  min-height: var(--tap); border-radius: var(--r-2); background: none; cursor: pointer; }
.empty { padding: 40px; text-align: center; color: var(--ink-2); }
footer { color: var(--ink-3); font-size: var(--fs-1); padding: var(--sp-5) 0 40px; text-align: center; line-height: 1.8; }
footer a { display: inline-block; padding: 11px 8px; }
:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; }
@media (max-width: 760px) { .prod { grid-template-columns: 1fr; } .prod .img { max-width: 230px; margin: 0 auto; width: 100%; }
  .stats { grid-template-columns: repeat(2, 1fr); } .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--sp-2); }
  .wrap { padding: 0 var(--sp-3); } }
"""
LOGO = ('<span class="mark" aria-hidden="true"><svg viewBox="0 0 512 512"><path d="M150 330 L250 230 L300 280 L390 180" stroke="#fff" '
        'stroke-width="44" fill="none" stroke-linecap="round" stroke-linejoin="round"/><circle cx="390" cy="180" r="34" fill="#fff"/></svg></span>')


def chip_style(store) -> str:
    """A store chip's colours: the store's own background and the text that passes AA on it (the live page's pairs)."""
    return f"background:var(--{e(store)}-bg,#555);color:var(--{e(store)}-fg,#fff)"


NAV = [("/", "Home"), ("/?deals=1&sort=newest&status=new", "Just dropped"), ("/cats", "Categories"),
       ("/stores", "Stores"), ("/coupons", "🎟️ Coupons"), ("/?recent=1", "Recently viewed")]

# picks the right link for the phone it runs on (the same rule as the live page)
LINK_JS = """<script>
(function () { const a = /Android/i.test(navigator.userAgent), i = /iPhone|iPad|iPod/i.test(navigator.userAgent);
  document.querySelectorAll("a[data-web]").forEach(x => { const h = a ? x.dataset.android : i ? x.dataset.ios : x.dataset.web;
    if (h) x.href = h; });
  document.querySelectorAll("[data-copy]").forEach(b => b.onclick = async () => {
    try { await navigator.clipboard.writeText(b.dataset.copy); } catch {}
    const t = b.textContent; b.textContent = "Copied"; setTimeout(() => b.textContent = t, 1400);
    if (b.dataset.go) setTimeout(() => window.open(b.dataset.go, "_blank", "noopener"), 450); });
})();
</script>"""


FOOTER = ('<div><a href="/about">About</a> · <a href="/privacy">Privacy</a> · <a href="/contact">Contact</a></div>'
          '<div>Prices are checked around the clock from each store\'s public pages. A price can change at the store '
          'before it changes here.</div><div>Some links earn us a small commission from the store, at no extra cost to '
          'you. As an Amazon Associate we earn from qualifying purchases.</div>')
LANG_CSS = "html[lang=ar] .en-only, html[lang=en] .ar-only { display: none; } .doc { max-width: 760px; } .doc h2 { margin-top: 22px; }"


def layout(title: str, body: str, active: str = "", og: dict = None, title_ar: str = None) -> bytes:
    og = og or {}
    meta = "".join(f'<meta property="og:{k}" content="{e(v)}">' for k, v in og.items() if v)
    if og.get("image"):
        meta += '<meta name="twitter:card" content="summary_large_image">'
    nav = "".join(f'<a href="{e(h)}"{" class=on" if h == active else ""}>{e(t)}</a>' for h, t in NAV)
    return f"""<!doctype html>
<html lang="ar" dir="rtl">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{e(title_ar or title)}</title><meta name="description" content="{e(og.get('description') or title)}">{meta}
<meta name="theme-color" content="#d6402b"><link rel="manifest" href="/manifest.webmanifest"><link rel="icon" href="/icon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/tokens.css"><style>{CSS}{LANG_CSS}</style><script src="/i18n.js"></script></head>
<body><header><div class="wrap"><div class="top"><a class="logo" href="/">{LOGO}<span>Live deals</span></a>
<button class="iconbtn" id="langBtn" aria-label="Language">ع</button></div><nav class="nav" aria-label="Pages">{nav}</nav></div></header>
<main class="wrap">{body}</main>
<footer class="wrap">{FOOTER}</footer>
{LINK_JS}</body></html>""".encode()


def _link_attrs(d: dict) -> str:
    l = d.get("links") or {}
    web = l.get("web") or d.get("url") or "#"
    return (f'href="{e(web)}" data-web="{e(web)}" data-android="{e(l.get("android") or web)}" '
            f'data-ios="{e(l.get("ios") or web)}" target="_blank" rel="noopener"')


def mini_card(d: dict) -> str:
    badge = f'<span class="badge" dir="ltr">−{round(d["discount_pct"])}%</span>' if d.get("discount_pct") else ""
    img = f'<img loading="lazy" src="{e(d["image"])}" alt="" onerror="this.remove()">' if d.get("image") else ""
    return (f'<a class="card" href="/p/{d["id"]}"><div class="pic">{img}{badge}</div><div class="b">'
            f'<div class="nm" dir="auto">{e(d["name"])}</div><div class="p">{money(d["price"])} EGP</div>'
            f'<div class="small muted">{e(LABELS.get(d["store"], d["store"]))}</div></div></a>')


def chart(history: list[dict], now: float) -> str:
    """The price over time as a step line, drawn here so the page needs no script for it."""
    pts = [(h["at"], h["price"]) for h in history if h.get("price") is not None]
    if not pts:
        return ""
    pts.append((now, pts[-1][1]))
    W, H, L, R, T, B = 640, 190, 50, 10, 12, 26
    t0, t1 = pts[0][0], max(pts[-1][0], pts[0][0] + 1)
    lo, hi = min(p for _, p in pts), max(p for _, p in pts)
    if hi - lo < 0.01:
        lo, hi = lo * 0.9, hi * 1.1 + 1
    pad = (hi - lo) * 0.12
    lo, hi = lo - pad, hi + pad
    X = lambda t: L + (t - t0) / (t1 - t0) * (W - L - R)
    Y = lambda p: T + (hi - p) / (hi - lo) * (H - T - B)
    d = f"M{X(pts[0][0]):.1f},{Y(pts[0][1]):.1f}"
    for (t, p), (_, prev) in zip(pts[1:], pts):
        d += f" L{X(t):.1f},{Y(prev):.1f} L{X(t):.1f},{Y(p):.1f}"
    area = d + f" L{X(pts[-1][0]):.1f},{H - B} L{X(pts[0][0]):.1f},{H - B} Z"
    grid = ""
    for i in range(4):
        v = lo + (hi - lo) * (i + 0.5) / 4
        grid += (f'<line x1="{L}" x2="{W - R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="var(--line)"/>'
                 f'<text x="{L - 6}" y="{Y(v) + 4:.1f}" text-anchor="end" font-size="11" fill="var(--muted)">{money(round(v, 1))}</text>')
    day = lambda t: datetime.fromtimestamp(t, CAIRO).strftime("%d/%m")
    labels = (f'<text x="{L}" y="{H - 6}" font-size="11" fill="var(--muted)">{day(t0)}</text>'
              f'<text x="{W - R}" y="{H - 6}" font-size="11" text-anchor="end" fill="var(--muted)">{day(t1)}</text>')
    dots = "".join(f'<circle cx="{X(t):.1f}" cy="{Y(p):.1f}" r="3" fill="var(--accent)"><title>{money(p)} · {day(t)}</title></circle>'
                   for t, p in pts[:-1][-40:])
    return (f'<svg class="chart" viewBox="0 0 {W} {H}" preserveAspectRatio="none" role="img" aria-label="Price history">'
            f'{grid}<path d="{area}" fill="var(--accent)" opacity=".08"/><path d="{d}" fill="none" stroke="var(--accent)" '
            f'stroke-width="2.2"/>{dots}{labels}</svg>')


def product_page(p: dict, d: dict, history: list[dict], matches: list[dict], related: list[dict],
                 base_url: str, now: float = None) -> bytes:
    now = now or time.time()
    d = d or {}
    store = p["store"]
    price = p["price"]
    pct = d.get("discount_pct") or 0
    tags = []
    if d.get("deal_label"):
        tags.append(f'<span class="tag hot">{e(d["deal_label"])}</span>')
    if "lowest" in (d.get("flags") or []):
        tags.append('<span class="tag good">lowest in 90 days</span>')
    if "back" in (d.get("flags") or []):
        tags.append('<span class="tag good">back in stock</span>')
    if d.get("cheapest") and d.get("matches"):
        tags.append('<span class="tag good">cheapest of all stores</span>')
    if "steady" in (d.get("flags") or []):
        tags.append('<span class="tag warn">discount never moves</span>')
    if not p["in_stock"]:
        tags.append('<span class="tag">out of stock</span>')
    was = f'<span class="was">{money(d.get("original_price"))}</span>' if d.get("kind") == "platform" else ""
    badge = ""
    if pct:
        kind = "the store's own discount" if d.get("kind") == "platform" else "under its usual price"
        badge = f'<span class="chip" dir="ltr" style="background:var(--deal);color:var(--deal-ink)" title="{kind}">−{round(pct)}%</span> '
    prices = [h["price"] for h in history if h.get("price") is not None and h["at"] > now - 90 * 86400] or [price]
    stats = [("Lowest in 90 days", min(prices)), ("Highest in 90 days", max(prices)),
             ("Usual price", d.get("usual_price")), ("Price changes", len(history))]
    stat_html = "".join(f'<div class="stat"><span>{e(k)}</span><b>{v if k == "Price changes" else money(v) + " EGP"}</b></div>'
                        for k, v in stats if v is not None)
    v = d.get("verdict") or {}
    verdict = (f'<div class="verdict {e(v.get("tone"))}"><b>{e(v.get("label"))}</b><span>{e(v.get("why"))}</span></div>'
               if v else "")
    why = "".join(f"<li>{e(x)}</li>" for x in d.get("why") or [])
    rows = sorted([d] + matches, key=lambda x: x["price"]) if d and matches else []
    cmp = ""
    if len(rows) > 1:
        cmp = '<h2>Same product in other stores</h2><div class="panel"><table>' + "".join(
            f'<tr><td><span class="chip" style="{chip_style(x["store"])}">{e(LABELS.get(x["store"], x["store"]))}</span> '
            f'<span class="small muted" dir="auto">{e(x["name"][:70])}</span></td><td class="n">{money(x["price"])} EGP'
            f'{"<br><span class=small>cheapest</span>" if i == 0 else ""}</td>'
            f'<td class="n"><a class="btn" style="padding:6px 12px" {_link_attrs(x)}>Open ↗</a></td></tr>'
            for i, x in enumerate(rows) if x.get("id")) + "</table></div>"
    rel = ('<h2>More deals like this</h2><div class="grid">' + "".join(mini_card(r) for r in related) + "</div>"
           if related else "")
    hub = f' · <span>📍 {e(d["hub_name"])}</span>' if d.get("hub_name") else ""
    first = datetime.fromtimestamp(p["first_seen"], CAIRO).strftime("%d/%m/%Y")
    img = f'<img src="{e(p["image"])}" alt="" onerror="this.remove()">' if p.get("image") else ""
    label = "Open in " + APP.get(store, "store") + " ↗"
    body = f"""
<div class="prod"><div class="img">{img}</div><div>
  <div class="small"><span class="chip" style="{chip_style(store)}">{e(LABELS.get(store, store))}</span>
    <span class="muted"> {e(p.get("category") or "")}{hub}</span></div>
  <h1 dir="auto">{e(p["name"])}</h1>
  <div>{badge}<span class="big">{money(price)} EGP</span>{was}</div>
  {f'<div class="small muted" style="margin-top:4px"><span dir="ltr">{e(d["unit_text"])}</span></div>' if d.get("unit_text") else ""}
  <div style="margin-top:8px">{"".join(tags)}</div>
  {verdict}
  {f'<ul class="why">{why}</ul>' if why else ""}
  <div class="row"><a class="btn primary" {_link_attrs(d or p)}>{e(label)}</a>
    <a class="btn" href="/?p={p["id"]}">★ Watch</a>
    <button class="btn" data-copy="{e(base_url)}/p/{p["id"]}">Share</button></div>
</div></div>
<h2>Price history</h2><div class="panel">{chart(history, now)}
  <div class="stats">{stat_html}</div><div class="small muted" style="margin-top:8px">tracked since {first}</div></div>
{cmp}{rel}"""
    ar_name = p["name"][:90]
    desc = " · ".join(x for x in [
        f"خصم {round(pct)}%" if pct else "",
        "🔥 أقل سعر في 90 يوم" if "lowest" in (d.get("flags") or []) else "",
        f"بدل {money(d.get('original_price'))} ج.م" if d.get("kind") == "platform" else "",
        AR_STORE.get(store, store)] if x)
    og = {"title": f"{ar_name} · {money(price)} ج.م", "description": desc, "image": p.get("image"),
          "url": f"{base_url}/p/{p['id']}", "type": "product", "site_name": "عروض لحظة بلحظة"}
    return layout(p["name"], body, og=og, title_ar=f"{ar_name} · {money(price)} ج.م")


def stores_page(status_rows: list[dict], facets_all: dict, facets_deals: dict, best: dict) -> bytes:
    tiles = []
    for s in status_rows:
        sid = s["store"]
        n_deals = facets_deals.get(sid, 0)
        state = ("paused" if s.get("paused_until") else "live") if s.get("live") else "not running"
        tiles.append(f"""<a class="tile" href="/store/{e(sid)}"><div class="h"><span class="dot" style="background:var(--{e(sid)}-bg,#555)"></span>{e(s["label"])}</div>
<div class="k">{n_deals:,}</div><div class="s">deals · {s["products"]:,} products</div>
<div class="s">{f'up to {round(best[sid])}% off' if best.get(sid) else ''}</div><div class="s">{e(state)}</div></a>""")
    body = f'<h1 class="title">All stores</h1><div class="tiles">{"".join(tiles)}</div>'
    return layout("Stores", body, active="/stores", og={"title": "المتاجر · عروض لحظة بلحظة"}, title_ar="المتاجر")


def cats_page(cats: list[tuple[str, int, float]]) -> bytes:
    from urllib.parse import quote
    tiles = "".join(f'<a class="tile" href="/?deals=1&amp;category={quote(c)}"><div class="h" dir="auto">{e(c)}</div>'
                    f'<div class="k">{n:,}</div><div class="s">deals{f" · up to {round(top)}% off" if top else ""}</div></a>'
                    for c, n, top in cats)
    body = f'<h1 class="title">Categories</h1><div class="tiles">{tiles or "<div class=empty>…</div>"}</div>'
    return layout("Categories", body, active="/cats", og={"title": "الأقسام · عروض لحظة بلحظة"}, title_ar="الأقسام")


def coupons_page(coupons: list[dict]) -> bytes:
    live = [c for c in coupons if not c.get("expires") or c["expires"] >= time.strftime("%Y-%m-%d")]
    rows = "".join(f"""<div class="panel coupon" style="margin-bottom:10px">
<button class="code" data-copy="{e(c["code"])}"{f' data-go="{e(c["url"])}"' if c.get("url") else ""}>{e(c["code"])}</button>
<div><div><span class="chip" style="{chip_style(c.get("store"))}">{e(LABELS.get(c.get("store"), c.get("store") or ""))}</span>
<span{"" if c.get("auto") else ' dir="auto"'}> {e(c.get("title") or "")}</span></div>
<div class="small muted">{f'until {e(c["expires"])}' if c.get("expires") else ""}</div></div></div>""" for c in live)
    body = (f'<h1 class="title">🎟️ Coupons</h1><p class="muted">Tap a code to copy it</p>'
            f'{rows or "<div class=empty>No coupons yet.</div>"}')
    return layout("Coupons", body, active="/coupons", og={"title": "كوبونات · عروض لحظة بلحظة"}, title_ar="كوبونات")


def load_coupons(path) -> list[dict]:
    try:
        data = json.loads(open(path, encoding="utf-8").read())
        return [c for c in data if isinstance(c, dict) and c.get("code")]
    except (OSError, ValueError):
        return []


def parse_coupons(text: str) -> list[dict]:
    """One coupon per line: store | CODE | what it gives | until (YYYY-MM-DD, optional) | link (optional)."""
    out = []
    for line in text.splitlines():
        parts = [x.strip() for x in line.split("|")]
        if len(parts) < 2 or not parts[1] or line.lstrip().startswith("#"):
            continue
        store = parts[0].lower().replace(".", "_").replace(" ", "_")
        store = {"amazon": "amazon_eg", "amazon_eg": "amazon_eg", "talabat": "talabat", "jumia": "jumia",
                 "breadfast": "breadfast"}.get(store, store)
        c = {"store": store[:30], "code": parts[1][:40], "title": (parts[2] if len(parts) > 2 else "")[:160]}
        if len(parts) > 3 and parts[3]:
            c["expires"] = parts[3][:10]
        if len(parts) > 4 and parts[4].startswith("https://"):
            c["url"] = parts[4][:500]
        out.append(c)
    return out[:200]


def _doc(ar: str, en: str) -> str:
    return f'<div class="doc"><div class="ar-only" data-noi18n>{ar}</div><div class="en-only" dir="ltr">{en}</div></div>'


def about_page(stores: list[str]) -> bytes:
    names = "، ".join(stores)
    ar = f"""<h1 class="title">عن عروض لحظة بلحظة</h1>
<p>موقع مصري بيتابع أسعار {names} طول اليوم، وبيقولك العرض ده حقيقي ولا لأ.</p>
<h2>بنعمل إيه</h2><ul>
<li>بنسجل كل سعر وكل تغيير، فتشوف تاريخ سعر كل منتج.</li>
<li>بنقارن السعر بسعره المعتاد، مش بس بالسعر المشطوب، وبنكشف <b>الخصم الوهمي</b>: الخصم اللي سعره ما بيتغيرش أبدًا.</li>
<li>بنتعلم مواعيد عروض كل منتج من تاريخه، ونقولك امتى العرض الجاي غالبًا.</li>
<li>بنقارن نفس المنتج بين المتاجر، ونبعتلك تنبيه أول ما السعر اللي مستنيه يوصل.</li></ul>
<h2>منين الأسعار؟</h2><p>من صفحات المتاجر العامة، بهدوء وبهوية واضحة، وبنحترم ملفات robots.txt بتاعتها. ممكن السعر يتغير
في المتجر قبل ما يتغير هنا، فالسعر النهائي هو اللي في المتجر.</p>
<h2>بنكسب إزاي؟</h2><p>بعض اللينكات بتدينا عمولة صغيرة من المتجر لو اشتريت، من غير أي تكلفة زيادة عليك. ده مش بيأثر على ترتيب
العروض: الترتيب بيطلع من الأسعار بس.</p>"""
    en = f"""<h1 class="title">About Live deals</h1>
<p>An Egyptian site that follows the prices of {", ".join(stores)} around the clock, and tells you whether a deal is real.</p>
<h2>What it does</h2><ul>
<li>Records every price and every change, so each product has its full price history.</li>
<li>Compares the price with its usual price, not just the struck-through one, and catches <b>fake discounts</b>: "discounts" whose price never moves.</li>
<li>Learns each product's sale rhythm from its history and says when the next sale is likely.</li>
<li>Compares the same product across stores, and alerts you the moment your target price is reached.</li></ul>
<h2>Where the prices come from</h2><p>From the stores' public pages, at a gentle pace, with a clear identity, following their
robots.txt. A price can change at the store before it changes here; the store's price is the final one.</p>
<h2>How the site is paid for</h2><p>Some links earn us a small commission from the store when you buy, at no extra cost to you.
That never changes the order of the deals: it comes from the prices alone. As an Amazon Associate we earn from qualifying purchases.</p>"""
    return layout("About", _doc(ar, en), active="/about", og={"title": "عن عروض لحظة بلحظة"}, title_ar="عن الموقع")


def privacy_page() -> bytes:
    ar = """<h1 class="title">الخصوصية</h1>
<ul><li>مفيش حسابات ولا تسجيل دخول. قايمة المتابعة والسلة واللي شفته قريب محفوظين على جهازك بس.</li>
<li>لو شغلت التنبيهات، بنحفظ عنوان الاشتراك اللي المتصفح بيدّيهولنا واختياراتك (النسبة، المتاجر، الكلمات) عشان نبعتلك. تقدر تقفلها في أي وقت من 🔔 وبتتمسح.</li>
<li>مش بنستخدم إعلانات ولا أدوات تتبع من شركات تانية. السيرفر بيحتفظ بسجلات تشغيل عادية لفترة قصيرة.</li>
<li>لما تدوس على لينك متجر، بتروح لموقع المتجر، وبتنطبق سياسة الخصوصية بتاعته.</li></ul>"""
    en = """<h1 class="title">Privacy</h1>
<ul><li>No accounts, no sign-in. Your watchlist, basket and recently viewed items are kept in your own browser only.</li>
<li>If you turn on alerts, we keep the push address your browser gives us and your choices (percentage, stores, words) to send
them. Turn them off any time from 🔔 and they are deleted.</li>
<li>No ads and no third-party trackers. The server keeps ordinary operating logs for a short time.</li>
<li>A store link takes you to that store's site, where its own privacy policy applies.</li></ul>"""
    return layout("Privacy", _doc(ar, en), active="/privacy", og={"title": "الخصوصية · عروض لحظة بلحظة"}, title_ar="الخصوصية")


def contact_page(email: str = "", telegram: str = "") -> bytes:
    ways_ar, ways_en = [], []
    if email:
        ways_ar.append(f'<li>إيميل: <a href="mailto:{e(email)}" dir="ltr">{e(email)}</a></li>')
        ways_en.append(f'<li>Email: <a href="mailto:{e(email)}">{e(email)}</a></li>')
    if telegram:
        ways_ar.append(f'<li>تليجرام: <a href="{e(telegram)}" dir="ltr">{e(telegram)}</a></li>')
        ways_en.append(f'<li>Telegram: <a href="{e(telegram)}">{e(telegram)}</a></li>')
    ar = (f'<h1 class="title">تواصل معانا</h1><p>سعر غلط؟ متجر عايزه يتضاف؟ أو انت صاحب متجر وعايز تتكلم معانا؟</p>'
          f'<ul>{"".join(ways_ar) or "<li>قريب.</li>"}</ul>')
    en = (f'<h1 class="title">Contact</h1><p>A wrong price? A store you want added? Or you run a store and want to talk?</p>'
          f'<ul>{"".join(ways_en) or "<li>Soon.</li>"}</ul>')
    return layout("Contact", _doc(ar, en), active="/contact", og={"title": "تواصل · عروض لحظة بلحظة"}, title_ar="تواصل معانا")
