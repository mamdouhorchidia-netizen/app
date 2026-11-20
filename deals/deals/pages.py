"""Pages the server writes out whole: a product's own page (/p/<id>, with a preview card when it's shared on
WhatsApp or Telegram), the stores (/stores), the categories (/cats), the coupons (/coupons) and About / Privacy /
Contact.

They're written in English and shown in Arabic by i18n.js, like the live page, so the language button works
everywhere. Words i18n.js doesn't know are written in both languages here (`bi`), and the page shows the one that
matches <html lang>. The preview tags (og:*) are Arabic already: link previews never run JavaScript.

Every page works with JavaScript off: links are real links, the chart is drawn here, the search is a form. The
small script at the end only picks the right store link for the phone, copies codes and shares.

The look follows docs/REBUILD.md ("Interface direction"): the same tokens, type sizes and radii as the live page.
"""

import html
import json
import math
import re
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

from .stores import LABELS

CAIRO = timezone(timedelta(hours=2))
AR_STORE = {"talabat": "طلبات مارت", "amazon_eg": "أمازون مصر", "jumia": "جوميا", "breadfast": "بريدفاست", "dream2000": "دريم 2000",
            "kenzz": "كنز", "raya": "راية شوب"}
APP = {"talabat": "talabat", "amazon_eg": "Amazon", "jumia": "Jumia", "breadfast": "Breadfast", "dream2000": "Dream 2000",
       "kenzz": "Kenzz", "raya": "Raya"}
DAY = 86400
MON_EN = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
MON_AR = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]


def e(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def money(n) -> str:
    if n is None:
        return ""
    return f"{n:,.2f}".rstrip("0").rstrip(".")


def bi(en: str, ar: str) -> str:
    """Both languages; the page shows the one that matches <html lang> (for words i18n.js doesn't know)."""
    return f'<span class="en-only">{en}</span><span class="ar-only">{ar}</span>'


def day_bi(t: float, year: bool = False) -> str:
    """A date as '12 Oct' / '12 أكتوبر' (and the year if asked)."""
    d = datetime.fromtimestamp(t, CAIRO)
    return f"{d.day} {bi(MON_EN[d.month - 1], MON_AR[d.month - 1])}{f' {d.year}' if year else ''}"


def _num(s) -> str:
    """A number that must read left to right inside Arabic text (−49%, +12)."""
    return f'<span class="num">{s}</span>'


def _minify(css: str) -> str:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"\s+", " ", css)
    css = re.sub(r"\s*([{};,>])\s*", r"\1", css)
    css = re.sub(r"([{;]) *([a-z-]+) *: *", r"\1\2:", css)
    return css.replace(";}", "}").strip()


# ---------------------------------------------------------------------------------------------- styles
# Tokens: docs/REBUILD.md. Each page gets BASE plus only the part it uses, minified once at import.

DARK = """--bg:#0f0f11; --panel:#19191d; --ink:#f2f0ea; --muted:#a09ca6; --faint:#6a6770; --line:#2a2930;
  --accent:#ff6a52; --accent-ink:#1a0906; --good:#5fd09a; --good-bg:#14301f; --warn:#e8bb52; --warn-bg:#38300f;
  --chip:#232228; --amazon_eg:#8aa0bb; --lift:0 8px 24px -10px rgb(0 0 0 / .7); --pic:#f4f3f0; color-scheme:dark;"""

BASE_CSS = _minify("""
:root { --bg:#f7f6f3; --panel:#fff; --ink:#17161a; --muted:#6b6871; --faint:#a3a0a8; --line:#e7e4de;
  --accent:#e0452c; --accent-ink:#fff; --good:#16794a; --good-bg:#e2f4ea; --warn:#9a6500; --warn-bg:#fbf0d2; --chip:#f0eee9;
  --talabat:#ff5a00; --amazon_eg:#232f3e; --breadfast:#a80080; --jumia:#f68b1e; --dream2000:#0b4ea2; --kenzz:#7b2cbf; --raya:#c8102e;
  --r-s:8px; --r-m:12px; --r-l:18px; --lift:0 8px 24px -10px rgb(23 22 26 / .22); --pic:#fff; color-scheme:light; }
@media (prefers-color-scheme: dark) { :root:not([data-theme=light]) { %DARK% } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); -webkit-text-size-adjust:100%;
  font:15px/1.5 system-ui, -apple-system, "Segoe UI", "Noto Sans Arabic", Tahoma, sans-serif; }
a { color:inherit; }
button, input { font:inherit; color:inherit; }
:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.wrap { max-width:1100px; margin:0 auto; padding:0 16px; }
.num { direction:ltr; unicode-bidi:isolate; font-variant-numeric:tabular-nums; }
html[lang=ar] .en-only, html[lang=en] .ar-only { display:none; }
header { position:sticky; top:0; z-index:5; background:color-mix(in srgb, var(--bg) 92%, transparent);
  backdrop-filter:blur(12px); border-bottom:1px solid var(--line); }
.top { display:flex; align-items:center; gap:12px; padding-top:8px; }
.logo { display:flex; align-items:center; gap:8px; min-height:44px; font-weight:750; font-size:17px; text-decoration:none; white-space:nowrap; }
.pulse { width:9px; height:9px; border-radius:50%; background:var(--accent); flex:none; animation:pulse 2s infinite; }
@keyframes pulse { 50% { opacity:.3; } }
.search { flex:1; min-width:0; max-width:480px; margin-inline-start:auto; position:relative; }
.search svg { position:absolute; inset-inline-start:12px; top:14px; color:var(--faint); }
.search input, .iconbtn, .btn { min-height:44px; border:1px solid var(--line); border-radius:var(--r-m); background:var(--panel); }
.search input { width:100%; padding:0 12px; padding-inline-start:38px; appearance:none; }
.iconbtn { flex:none; min-width:44px; font-weight:650; cursor:pointer; }
.nav { display:flex; gap:4px; overflow-x:auto; scrollbar-width:none; font-size:13.5px; margin:0 -8px; padding:0 8px; }
.nav a { flex:none; display:flex; align-items:center; height:44px; padding:0 12px; border-block:6px solid transparent;
  background-clip:padding-box; border-radius:999px; color:var(--muted); text-decoration:none; white-space:nowrap; }
.nav a:hover { color:var(--ink); }
.nav a.on { background-color:var(--chip); color:var(--ink); font-weight:650; }
main { display:block; padding:16px 0 32px; min-height:60vh; }
h1.title { font-size:21px; line-height:1.3; margin:8px 0 4px; }
h2 { font-size:17px; margin:32px 0 12px; }
.sub, .muted { color:var(--muted); }
.sub { font-size:13.5px; margin:0 0 16px; }
.btn { display:inline-flex; align-items:center; justify-content:center; gap:8px; padding:0 16px; font-weight:650;
  text-decoration:none; cursor:pointer; transition:transform .18s ease-out, box-shadow .18s ease-out; }
.btn:hover { border-color:var(--faint); }
.btn.primary { background:var(--accent); border-color:var(--accent); color:var(--accent-ink); }
.btn.primary:hover { box-shadow:var(--lift); transform:translateY(-2px); }
.empty { text-align:center; color:var(--muted); padding:48px 16px; font-size:17px; }
.next { display:flex; flex-wrap:wrap; justify-content:center; gap:8px; }
footer { border-top:1px solid var(--line); color:var(--muted); font-size:12px; line-height:1.7; text-align:center; padding:8px 16px 32px; }
footer a { display:inline-flex; align-items:center; min-height:44px; padding:0 8px; font-size:13.5px; }
footer p { margin:4px auto; max-width:640px; }
.toast { position:fixed; z-index:20; left:50%; bottom:24px; transform:translateX(-50%); background:var(--ink); color:var(--bg);
  padding:12px 16px; border-radius:var(--r-m); box-shadow:var(--lift); }
@media (max-width: 640px) { header { position:static; } .search input { font-size:16px; } }
@media (max-width: 370px) { .logo .t { display:none; } }
@media (prefers-reduced-motion: reduce) { * { animation:none !important; transition:none !important; } }
""".replace("%DARK%", DARK))

# the store label and the tap-to-copy code (product page and coupons)
SHARED_CSS = """
.sd { display:inline-flex; align-items:center; gap:6px; font-weight:650; }
.sd i { width:10px; height:10px; border-radius:3px; background:var(--c, #777); flex:none; }
.good { background:var(--good-bg); color:var(--good); } .warn { background:var(--warn-bg); color:var(--warn); }
.code { display:inline-flex; align-items:center; gap:8px; min-height:44px; padding:0 16px; border:2px dashed var(--accent);
  border-radius:var(--r-m); background:color-mix(in srgb, var(--accent) 6%, var(--panel)); color:var(--accent); cursor:pointer;
  font-weight:700; vertical-align:middle; transition:background-color .18s ease-out, color .18s ease-out; }
.code code { font:700 17px ui-monospace, Menlo, Consolas, monospace; letter-spacing:.06em; direction:ltr; }
.code.done { border-style:solid; border-color:var(--good); color:var(--good); background:var(--good-bg); }
"""

PRODUCT_CSS = _minify(SHARED_CSS + """
.grid { display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:12px; }
.card, .panel, .shot { background:var(--panel); border:1px solid var(--line); border-radius:var(--r-l); }
.card { display:flex; flex-direction:column; overflow:hidden; text-decoration:none; transition:transform .18s ease-out, box-shadow .18s ease-out; }
.card:hover { transform:translateY(-2px); box-shadow:var(--lift); }
.pic, .shot { position:relative; aspect-ratio:1; background:var(--pic); display:grid; place-items:center; overflow:hidden; }
.pic img, .shot img { position:absolute; inset:0; width:100%; height:100%; object-fit:contain; padding:10%; background:var(--pic); color:transparent; }
.ph { font-size:32px; font-weight:750; color:#d9d5cc; }
.shot .ph { font-size:64px; }
.off { background:var(--accent); color:var(--accent-ink); font-weight:750; font-size:13.5px; padding:0 8px; border-radius:var(--r-s);
  direction:ltr; unicode-bidi:isolate; white-space:nowrap; }
.pic .off { position:absolute; top:8px; inset-inline-start:8px; }
.card .b { padding:8px 12px 12px; display:flex; flex-direction:column; gap:4px; flex:1; }
.nm { font-size:13.5px; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; min-height:2.9em; }
.pr { margin-top:auto; } .pr b { font-size:17px; }
small { color:var(--muted); font-weight:600; }
.was { color:var(--muted); font-size:13.5px; margin-inline-start:4px; }
.tags, .meta, .crumb, .price, .advice, .legend { display:flex; flex-wrap:wrap; align-items:center; gap:4px 12px; }
.tags { gap:4px; margin-top:12px; }
.tag { font-size:12px; padding:2px 8px; border-radius:6px; background:var(--chip); color:var(--muted); white-space:nowrap; }
.tag.hot { background:color-mix(in srgb, var(--accent) 14%, transparent); color:var(--accent); }
.tag.good, .tag.warn, .tag.hot { font-weight:600; }
.crumb { font-size:13.5px; color:var(--muted); margin-bottom:12px; }
.crumb a { text-decoration:none; } .crumb .sd { color:var(--ink); }
.hero { display:grid; grid-template-columns:5fr 6fr; gap:32px; align-items:start; }
.info { min-width:0; }
.info h1 { font-size:21px; line-height:1.35; font-weight:650; margin:0 0 12px; overflow-wrap:anywhere; }
.now { font-size:28px; font-weight:800; line-height:1.15; letter-spacing:-.02em; font-variant-numeric:tabular-nums; }
.now small { font-size:15px; }
.price .was { font-size:15px; margin:0; }
.meta { color:var(--muted); font-size:13.5px; margin-top:4px; }
.unit { direction:ltr; unicode-bidi:isolate; }
.advice { gap:4px 8px; margin-top:16px; font-size:13.5px; }
.pill { display:inline-flex; align-items:center; gap:6px; padding:4px 12px; border-radius:999px; background:var(--chip); font-weight:700; }
.pill:before { content:""; width:7px; height:7px; border-radius:50%; background:currentColor; }
.alert { border-radius:var(--r-m); padding:8px 12px; font-size:13.5px; margin-top:8px; color:var(--ink); }
.alert b { display:block; color:var(--warn); }
.cta { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:16px; max-width:460px; }
.cta .primary { grid-column:1 / -1; min-height:52px; font-size:17px; }
.note { margin-top:12px; padding:8px 12px; border:1px dashed var(--line); border-radius:var(--r-m); font-size:13.5px; }
.note .code { min-height:36px; padding:0 10px; } .note code { font-size:15px; }
.panel { padding:16px; }
.ch { margin:0; direction:ltr; }
.plot { position:relative; height:210px; margin:12px 0 0 56px; }
.chart { position:absolute; inset:0; width:100%; height:100%; overflow:visible; }
.chart path { vector-effect:non-scaling-stroke; fill:none; stroke:var(--line); }
.chart .u { stroke:var(--muted); stroke-dasharray:5 5; }
.chart .ar { fill:color-mix(in srgb, var(--accent) 11%, transparent); stroke:none; }
.chart .ln { stroke:var(--accent); stroke-width:2.25; }
.chart rect { fill:var(--chip); }
.plot>span { position:absolute; font-size:12px; white-space:nowrap; font-variant-numeric:tabular-nums; }
.yl { right:100%; padding-right:8px; color:var(--muted); transform:translateY(-50%); }
.ul, .mt { background:var(--panel); padding:0 4px; border-radius:4px; }
.ul { left:4px; transform:translateY(-110%); color:var(--muted); }
.mk { width:11px; height:11px; margin:-5.5px 0 0 -5.5px; border-radius:50%; background:var(--panel); border:2.5px solid var(--ink); }
.mk.lo { border-color:var(--good); }
.mk.now { background:var(--accent); border-color:var(--panel); box-shadow:0 0 0 1.5px var(--accent); }
.mt { font-weight:700; transform:translate(-50%, -135%); }
.mt.lo { color:var(--good); transform:translate(-50%, 45%); }
.mt.now { color:var(--accent); transform:translate(calc(-100% - 8px), -50%); }
.mt.l { transform:translate(-4px, -135%); } .mt.lo.l { transform:translate(-4px, 45%); }
.mt.r { transform:translate(calc(-100% + 4px), -135%); } .mt.lo.r { transform:translate(calc(-100% + 4px), 45%); }
.xl { position:relative; height:24px; margin-left:56px; color:var(--muted); font-size:12px; }
.xl>span { position:absolute; top:6px; transform:translateX(-50%); white-space:nowrap; }
.xl>span:first-child { transform:none; } .xl>span:last-child { transform:translateX(-100%); }
.legend { font-size:12px; color:var(--muted); margin-top:4px; }
.legend i { display:inline-block; width:14px; border-top:2px dashed var(--muted); vertical-align:middle; margin-inline-end:6px; }
.legend i.o { height:10px; border:0; background:var(--chip); }
.stats { display:grid; grid-template-columns:repeat(5, minmax(0, 1fr)); gap:8px; margin-top:16px; }
.stat { background:var(--chip); border-radius:var(--r-m); padding:8px 12px; }
.stat>span { display:block; font-size:12px; color:var(--muted); }
.stat b { font-size:17px; white-space:nowrap; font-variant-numeric:tabular-nums; }
.dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-inline-end:6px; border:2px solid var(--ink); }
.dot.lo { border-color:var(--good); }
.log { margin-top:12px; font-size:13.5px; }
.log summary { cursor:pointer; color:var(--muted); padding:12px 0; }
table { width:100%; border-collapse:collapse; }
td { padding:8px 4px; border-top:1px solid var(--line); }
td.n { text-align:end; white-space:nowrap; font-variant-numeric:tabular-nums; }
.dn { color:var(--good); } .up { color:var(--accent); }
.cmp td:first-child { width:100%; }
.sn { display:block; color:var(--muted); font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:52ch; }
.cmp .me { background:var(--chip); }
.cmp .btn { min-height:40px; padding:0 12px; font-size:13.5px; }
.best { display:block; color:var(--good); font-weight:700; font-size:12px; }
@media (max-width: 760px) {
  .hero { grid-template-columns:minmax(0, 1fr); gap:16px; }
  .shot { aspect-ratio:auto; height:210px; }
  .info h1 { font-size:17px; margin-bottom:8px; }
  .cta { max-width:none; }
  .stats { grid-template-columns:1fr 1fr; } .stat:last-child:nth-child(odd) { grid-column:1 / -1; }
  .plot { height:180px; margin-left:48px; } .xl { margin-left:48px; }
  .grid { grid-template-columns:1fr 1fr; gap:8px; }
  .sn { max-width:26ch; }
}
""")

STORES_CSS = _minify("""
.tiles { display:grid; grid-template-columns:repeat(auto-fill, minmax(250px, 1fr)); gap:12px; }
.tile { display:flex; flex-direction:column; gap:4px; background:var(--panel); border:1px solid var(--line); border-radius:var(--r-l);
  padding:16px; text-decoration:none; position:relative; overflow:hidden; transition:transform .18s ease-out, box-shadow .18s ease-out; }
.tile::before { content:""; position:absolute; inset-block:0; inset-inline-start:0; width:4px; background:var(--c, #777); }
.tile:hover, .tile:focus-visible { transform:translateY(-2px); box-shadow:var(--lift); }
.tile .h { display:flex; align-items:center; gap:8px; font-weight:700; font-size:17px; }
.tile .h i { width:12px; height:12px; border-radius:4px; background:var(--c, #777); flex:none; }
.state { margin-inline-start:auto; display:inline-flex; align-items:center; gap:6px; font-size:12px; font-weight:600;
  color:var(--muted); background:var(--chip); padding:2px 8px; border-radius:999px; white-space:nowrap; }
.state::before { content:""; width:7px; height:7px; border-radius:50%; background:var(--faint); }
.state.live { color:var(--good); background:var(--good-bg); } .state.live::before { background:var(--good); animation:pulse 2s ease-out infinite; }
.state.paused { color:var(--warn); background:var(--warn-bg); } .state.paused::before { background:var(--warn); }
.tile .k { font-size:28px; font-weight:800; letter-spacing:-.02em; line-height:1.2; margin-top:8px; font-variant-numeric:tabular-nums; }
.tile .s { color:var(--muted); font-size:13.5px; }
.tile .row { display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin-top:8px; font-size:12px; color:var(--muted); }
.hot { background:color-mix(in srgb, var(--accent) 14%, transparent); color:var(--accent); font-weight:700; font-size:13.5px;
  padding:2px 8px; border-radius:var(--r-s); }
.bar { height:6px; border-radius:3px; background:var(--chip); overflow:hidden; margin-top:8px; }
.bar i { display:block; height:100%; background:var(--c, #777); border-radius:3px; }
.tile.off { opacity:.7; }
""")

CATS_CSS = _minify("""
.find { display:block; position:relative; margin:0 0 16px; max-width:480px; }
.find input { width:100%; height:44px; border:1px solid var(--line); border-radius:var(--r-m); background:var(--panel); padding:0 12px; }
.cats { display:grid; grid-template-columns:repeat(auto-fill, minmax(300px, 1fr)); gap:0 24px; margin:0; padding:0; list-style:none; }
.cats a { display:flex; align-items:center; gap:12px; min-height:48px; padding:4px 8px; border-bottom:1px solid var(--line);
  text-decoration:none; border-radius:var(--r-s); transition:background-color .16s ease-out; }
.cats a:hover, .cats a:focus-visible { background:var(--panel); }
.cats bdi { flex:1; min-width:0; text-align:match-parent; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-weight:600; }
.cats b { font-weight:700; font-variant-numeric:tabular-nums; }
.cats i { font-style:normal; font-size:12px; font-weight:700; color:var(--accent); min-width:44px; text-align:end;
  direction:ltr; unicode-bidi:isolate; }
.cats .big a { min-height:56px; } .cats .big b { font-size:17px; }
.legend { display:flex; gap:16px; font-size:12px; color:var(--muted); margin:-8px 0 8px; }
.legend i { color:var(--accent); font-style:normal; font-weight:700; }
""")

COUPONS_CSS = _minify(SHARED_CSS + """
.coupons { display:grid; grid-template-columns:repeat(auto-fill, minmax(330px, 1fr)); gap:12px; }
.cpn { display:flex; flex-direction:column; gap:8px; background:var(--panel); border:1px solid var(--line);
  border-radius:var(--r-l); padding:16px; position:relative; overflow:hidden; }
.cpn::before { content:""; position:absolute; inset-block:0; inset-inline-start:0; width:4px; background:var(--c, #777); }
.cpn .t { font-weight:650; font-size:15px; line-height:1.4; }
.cpn .m { display:flex; flex-wrap:wrap; align-items:center; gap:4px 12px; font-size:12px; color:var(--muted); }
.cpn .sd { font-size:12px; color:var(--ink); font-weight:650; }
.cpn .row { display:flex; flex-wrap:wrap; align-items:center; gap:8px; }
.cpn .go { font-size:13.5px; color:var(--muted); min-height:44px; display:inline-flex; align-items:center; padding:0 8px; }
.cpn .code { flex:1 1 auto; justify-content:space-between; max-width:100%; }
.code .cp { font-size:12px; font-weight:600; opacity:.85; } .code .ok, .code.done .cp { display:none; }
.code.done .ok { display:inline; } .code .ok:before { content:"✓ "; }
.soon { color:var(--warn); background:var(--warn-bg); font-weight:700; padding:0 8px; border-radius:6px; }
""")

DOC_CSS = _minify("""
.doc { max-width:720px; background:var(--panel); border:1px solid var(--line); border-radius:var(--r-l); padding:8px 24px 24px; }
.doc h1.title { margin-top:16px; }
.doc h2 { margin:24px 0 8px; }
.doc p, .doc li { line-height:1.7; }
.doc ul { padding-inline-start:20px; }
@media (max-width: 640px) { .doc { padding:4px 16px 16px; } }
""")

NAV = [("/", "Home"), ("/?deals=1&sort=newest&status=new", "Just dropped"), ("/cats", "Categories"),
       ("/stores", "Stores"), ("/coupons", "🎟️ Coupons"), ("/?recent=1", "Recently viewed")]

# The page scripts, each page taking only the parts it uses: the right store link for the phone (the same rule as
# the live page), tap-to-copy, share, the category filter, and "recently viewed".
JS = {
    "links": """const ua = navigator.userAgent, a = /Android/i.test(ua), i = /iPhone|iPad|iPod/i.test(ua);
  document.querySelectorAll("a[data-web]").forEach(x => { const h = a ? x.dataset.android : i ? x.dataset.ios : x.dataset.web;
    if (h) x.href = h; });""",
    "copy": """async function copy(t) { try { await navigator.clipboard.writeText(t); return true; } catch {
    const x = document.createElement("textarea"); x.value = t; x.style.position = "fixed"; x.style.opacity = "0";
    document.body.append(x); x.select(); let ok = false; try { ok = document.execCommand("copy"); } catch {} x.remove(); return ok; } }
  document.addEventListener("click", async ev => {
    const b = ev.target.closest("[data-copy]"), s = ev.target.closest("[data-share]");
    if (b) { ev.preventDefault(); await copy(b.dataset.copy); b.classList.add("done"); clearTimeout(b.t);
      b.t = setTimeout(() => b.classList.remove("done"), 1800); if (navigator.vibrate) navigator.vibrate(12);
      if (b.dataset.go) setTimeout(() => window.open(b.dataset.go, "_blank", "noopener"), 600); }
    if (s) { const url = s.dataset.share, title = s.dataset.title || document.title, n = document.getElementById("toast");
      if (navigator.share) { ev.preventDefault(); try { await navigator.share({ title, text: title, url }); } catch {} }
      else if (navigator.clipboard && n) { ev.preventDefault(); if (await copy(url)) { n.hidden = false; clearTimeout(n.t);
        n.t = setTimeout(() => n.hidden = true, 2200); } } } });""",
    "product": """document.addEventListener("error", ev => { if (ev.target.tagName === "IMG") ev.target.remove(); }, true);
  document.querySelectorAll("img").forEach(x => { if (x.complete && !x.naturalWidth) x.remove(); });
  try { const k = "recent", id = +document.querySelector("main").dataset.pid, r = JSON.parse(localStorage.getItem(k)) || [];
    localStorage.setItem(k, JSON.stringify([id, ...r.filter(x => x !== id)].slice(0, 60))); } catch {}""",
    "cats": """const f = document.getElementById("cf"), rows = [...document.querySelectorAll(".cats li")], low = x => x.toLowerCase();
  if (window.I18N && I18N.lang === "en") f.placeholder = "Find a category";
  f.oninput = () => { const q = low(f.value.trim()); rows.forEach(r => r.hidden = !!q && !low(r.textContent).includes(q)); };""",
}
JS = {k: re.sub(r"\n\s*", " ", v) for k, v in JS.items()}


def _script(parts) -> str:
    return f'<script>(function () {{ {" ".join(JS[k] for k in parts)} }})();</script>' if parts else ""


FOOTER = ('<div class="links"><a href="/about">About</a> · <a href="/privacy">Privacy</a> · <a href="/contact">Contact</a></div>'
          '<p>Prices are checked around the clock from each store\'s public pages. A price can change at the store '
          'before it changes here.</p><p>Some links earn us a small commission from the store, at no extra cost to '
          'you. As an Amazon Associate we earn from qualifying purchases.</p>')

SEARCH_ICON = ('<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" '
               'aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>')

NOT_FOUND_NEXT = (f'<p class="muted" style="text-align:center">'
                  f'{bi("Search above, or start from the latest deals:", "دوّر فوق، أو ابدأ من آخر العروض:")}</p>'
                  '<div class="next"><a class="btn primary" href="/?deals=1&amp;sort=newest&amp;status=new">Just dropped</a>'
                  '<a class="btn" href="/">Home</a><a class="btn" href="/cats">Categories</a></div>')


def layout(title: str, body: str, active: str = "", og: dict = None, title_ar: str = None, css: str = "",
           head: str = "", main_attrs: str = "", toast: bool = False, js: tuple = ()) -> bytes:
    og = og or {}
    meta = "".join(f'<meta property="og:{k}" content="{e(v)}">' for k, v in og.items() if v)
    if og.get("image"):
        meta += '<meta name="twitter:card" content="summary_large_image">'
    nav = "".join(f'<a href="{e(h)}"{" class=on aria-current=page" if h == active else ""}>{e(t)}</a>' for h, t in NAV)
    if title == "Not found":  # web.py's 404 page: say what to do next
        body += NOT_FOUND_NEXT
    note = '<div class="toast" id="toast" role="status" hidden>Link copied.</div>' if toast else ""
    return f"""<!doctype html>
<html lang="ar" dir="rtl">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{e(title_ar or title)}</title><meta name="description" content="{e(og.get('description') or title_ar or title)}">{meta}
<meta name="theme-color" content="#f7f6f3" media="(prefers-color-scheme: light)"><meta name="theme-color" content="#0f0f11" media="(prefers-color-scheme: dark)">
<link rel="manifest" href="/manifest.webmanifest"><link rel="icon" href="/icon.svg" type="image/svg+xml">{head}
<style>{BASE_CSS}{css}</style><script src="/i18n.js"></script></head>
<body><header><div class="wrap"><div class="top"><a class="logo" href="/" aria-label="Live deals"><span class="pulse"></span><span class="t">Live deals</span></a>
<form class="search" action="/" role="search">{SEARCH_ICON}<input type="search" id="q" name="q" placeholder="Try: milk under 50 at talabat · حفاضات أرخص للكيلو" aria-label="Search" autocomplete="off" enterkeyhint="search"></form>
<button class="iconbtn" id="langBtn" type="button" title="English" aria-label="Language">EN</button></div><nav class="nav" aria-label="Sections">{nav}</nav></div></header>
<main class="wrap"{main_attrs}>{body}</main>
<footer>{FOOTER}</footer>{note}
{_script(js)}</body></html>""".encode()


def _link_attrs(d: dict) -> str:
    l = d.get("links") or {}
    web = l.get("web") or d.get("url") or "#"
    return (f'href="{e(web)}" data-web="{e(web)}" data-android="{e(l.get("android") or web)}" '
            f'data-ios="{e(l.get("ios") or web)}" target="_blank" rel="noopener"')


def _store(sid: str, cls: str = "sd") -> str:
    return f'<span class="{cls}" style="--c:var(--{e(sid)})"><i></i>{e(LABELS.get(sid, sid))}</span>'


def _initial(name) -> str:
    return e((str(name or "?").strip() or "?")[0])


def mini_card(d: dict, store: bool = True) -> str:
    off = f'<span class="off">−{round(d["discount_pct"])}%</span>' if d.get("discount_pct") else ""
    img = f'<img src="{e(d["image"])}" alt="" loading="lazy" decoding="async">' if d.get("image") else ""
    was = f'<s class="was">{money(d.get("original_price"))}</s>' if d.get("kind") == "platform" and d.get("original_price") else ""
    return (f'<a class="card" href="/p/{int(d["id"])}"><div class="pic"><span class="ph">{_initial(d.get("name"))}</span>{img}{off}</div>'
            f'<div class="b"><div class="nm" dir="auto">{e(d["name"])}</div>'
            f'<div class="pr"><b>{money(d["price"])}</b> <small>EGP</small>{was}</div>{_store(d["store"]) if store else ""}</div></a>')


# ---------------------------------------------------------------------------------------------- chart

def _nice_step(span: float, n: int = 3) -> float:
    raw = span / max(n, 1)
    if raw <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def _series(history: list[dict], now: float, days: int = 90, current: float = None):
    """[(at, price, in_stock)] for the last `days`, starting with the state the window opened in."""
    rows = sorted((h for h in history if h.get("price") is not None), key=lambda h: h["at"])
    start = now - days * DAY
    before = [h for h in rows if h["at"] <= start]
    pts = [(start, before[-1]["price"], before[-1].get("in_stock", 1))] if before else []
    pts += [(h["at"], h["price"], h.get("in_stock", 1)) for h in rows if start < h["at"] <= now]
    if not pts and current is not None:
        pts = [(now, current, 1)]
    return pts


def chart(history: list[dict], now: float, usual: float = None, current: float = None, days: int = 90) -> str:
    """The price over the last 90 days as a step line. The lines are SVG (they stretch with the page); the words,
    the dots and the axes are HTML on top, so they stay sharp and readable at any width, in either direction."""
    pts = _series(history, now, days, current)
    if not pts:
        return ""
    t0 = pts[0][0]
    t1 = max(now, t0 + 3600)
    if t1 - t0 < 6 * 3600:  # a few hours: give the line some room on the left
        t0 = t1 - 6 * 3600
    steps = pts + [(t1, pts[-1][1], pts[-1][2])]
    stocked = [p for _, p, s in pts if s] or [p for _, p, _ in pts]
    lo, hi = min(stocked), max(stocked)
    span_vals = stocked + ([usual] if usual and lo * 0.5 <= usual <= hi * 2 else [])
    vlo, vhi = min(span_vals), max(span_vals)
    pad = (vhi - vlo) * 0.18 if vhi - vlo >= max(0.01, vhi * 0.02) else max(vhi * 0.1, 1.0)
    ylo, yhi = max(0.0, vlo - pad), vhi + pad
    W, H = 1000, 200
    X = lambda t: (t - t0) / (t1 - t0) * W
    Y = lambda p: (yhi - p) / (yhi - ylo) * H
    pct_x = lambda t: f"{X(t) / W * 100:.2f}%"
    pct_y = lambda p: f"{Y(p) / H * 100:.2f}%"

    x_first = X(steps[0][0])
    d = f"M{x_first:.1f} {Y(steps[0][1]):.1f}"
    for (t, p, _), (_, prev, _) in zip(steps[1:], steps):
        if p != prev:
            d += f"H{X(t):.1f}V{Y(p):.1f}"
    d += f"H{W}"
    area = d + f"V{H}H{x_first:.1f}Z"
    oos = "".join(f'<rect class="oos" x="{X(a):.1f}" width="{max(X(b) - X(a), 1):.1f}" y="0" height="{H}"/>'
                  for (a, _, s), (b, _, _) in zip(steps, steps[1:]) if not s)
    step = _nice_step(yhi - ylo)
    ticks, v = [], (ylo // step + 1) * step
    while v < yhi and len(ticks) < 6:
        ticks.append(v)
        v += step
    grid = "".join(f"M0 {Y(v):.1f}H{W}" for v in ticks)
    ref = f'<path class="u" d="M0 {Y(usual):.1f}H{W}"/>' if usual and ylo < usual < yhi and abs(usual - pts[-1][1]) > (yhi - ylo) * 0.03 else ""
    svg = (f'<svg class="chart" viewBox="0 0 {W} {H}" preserveAspectRatio="none" aria-hidden="true">{oos}'
           f'<path class="g" d="{grid}"/>{ref}<path class="ar" d="{area}"/><path class="ln" d="{d}"/></svg>')
    ylab = "".join(f'<span class="yl" style="top:{pct_y(v)}">{money(round(v, 2))}</span>' for v in ticks)
    ulab = (f'<span class="ul" style="top:{pct_y(usual)}">usually {money(usual)}</span>'
            if ref and abs(Y(usual) - Y(steps[0][1])) > 12 else "")

    # markers: now (always), the lowest and the highest when they aren't where the line ends
    marks = []
    now_p = steps[-1][1]
    marks.append(f'<span class="mk now" style="left:100%;top:{pct_y(now_p)}"></span>'
                 f'<span class="mt now" style="left:100%;top:{pct_y(now_p)}">{money(now_p)}</span>')

    def mark(cls, value):
        segs = [(a, b) for (a, p, s), (b, _, _) in zip(steps, steps[1:]) if p == value and (s or not any(x[2] for x in pts))]
        if not segs or (value == now_p and segs[-1][1] >= t1):
            return
        a, b = max(segs, key=lambda ab: ab[1] - ab[0])
        x = (X(a) + X(b)) / 2
        if x > W * 0.86 and abs(Y(value) - Y(now_p)) < 30:
            return  # it would sit on the "now" label
        edge = " l" if x < W * 0.1 else " r" if x > W * 0.9 else ""
        marks.append(f'<span class="mk {cls}" style="left:{x / W * 100:.2f}%;top:{pct_y(value)}"></span>'
                     f'<span class="mt {cls}{edge}" style="left:{x / W * 100:.2f}%;top:{pct_y(value)}">{money(value)}</span>')
    if hi != lo:
        mark("lo", lo)
        mark("hi", hi)

    # dates along the bottom: the start, two in between, "now" at the end
    if t1 - t0 <= 2 * DAY:
        fmt = lambda t: datetime.fromtimestamp(t, CAIRO).strftime("%H:%M")
    else:
        fmt = lambda t: day_bi(t)
    xs = [t0 + (t1 - t0) * f for f in (0, 1 / 3, 2 / 3)]
    xlab = "".join(f'<span style="left:{pct_x(t)}">{fmt(t)}</span>' for t in xs) + '<span style="left:100%">now</span>'
    legend = []
    if ref:
        legend.append(f'<span><i></i>Usual price</span>')
    if oos:
        legend.append('<span><i class="o"></i>out of stock</span>')
    return (f'<figure class="ch"><div class="plot" role="img" aria-label="Price history">{svg}{ylab}{ulab}{"".join(marks)}</div>'
            f'<div class="xl" aria-hidden="true">{xlab}</div></figure>'
            + (f'<div class="legend">{"".join(legend)}</div>' if legend else ""))


def _changes(history: list[dict]) -> list[tuple]:
    """(at, price, previous price or None) for every real price change, oldest first."""
    out, prev = [], None
    for h in sorted((h for h in history if h.get("price") is not None), key=lambda h: h["at"]):
        if h["price"] != prev:
            out.append((h["at"], h["price"], prev))
            prev = h["price"]
    return out


def _dmy(t: float, now: float) -> str:
    d = datetime.fromtimestamp(t, CAIRO)
    return d.strftime("%d/%m" if d.year == datetime.fromtimestamp(now, CAIRO).year else "%d/%m/%Y")


def _json_ld(obj) -> str:
    """JSON for a <script> block: nothing in it can close the tag or start markup."""
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


# ---------------------------------------------------------------------------------------------- product

def product_page(p: dict, d: dict, history: list[dict], matches: list[dict], related: list[dict],
                 base_url: str, now: float = None) -> bytes:
    now = now or time.time()
    d = d or {}
    flags = d.get("flags") or []
    store = p["store"]
    price = p["price"]
    pid = int(p["id"])
    pct = d.get("discount_pct") or 0
    try:
        extra = json.loads(p.get("extra") or "{}")
    except (TypeError, ValueError):
        extra = {}
    hub_name = d.get("hub_name") or extra.get("hub_name")
    in_stock = bool(p.get("in_stock"))
    page_url = f"{base_url}/p/{pid}"

    # what the price is doing, in small tags
    tags = []
    if d.get("deal_label"):
        tags.append(f'<span class="tag hot">{e(d["deal_label"])}</span>')
    if "lowest" in flags:
        tags.append('<span class="tag good">lowest in 90 days</span>')
    if "back" in flags:
        tags.append('<span class="tag good">back in stock</span>')
    if d.get("cheapest") and d.get("matches"):
        tags.append('<span class="tag good">cheapest of all stores</span>')
    if "new" in flags:
        tags.append('<span class="tag">price changed in the last 24 h</span>')
    if "steady" in flags:
        tags.append('<span class="tag warn">discount never moves</span>')
    if not in_stock:
        tags.append('<span class="tag">out of stock</span>')

    # warnings: a discount that isn't one, a "was" price never charged, a price that just went up
    alerts = []
    if "inflated" in flags and d.get("original_price"):
        alerts.append(f'<div class="alert warn"><b>⚠️ "was" price looks inflated</b>'
                      f'<span>the \'was\' price ({money(d["original_price"])} EGP) has never been charged here: likely inflated</span></div>')
    if "steady" in flags:
        days = round(d.get("days_at_price") or 14)
        alerts.append(f'<div class="alert warn"><b>🕵️ {bi("Fake discount", "خصم وهمي")}</b>'
                      f"<span>same 'discounted' price for {days} days: the old price may never be charged</span></div>")
    if "rose" in flags and d.get("recent_low"):
        up = round(100 * (price / d["recent_low"] - 1))
        alerts.append(f'<div class="alert warn"><b>📈 {bi("The price went up", "السعر زاد")}</b>'
                      f'<span>it was {money(d["recent_low"])} EGP lately: {up}% dearer now</span></div>')

    was = (f'<s class="was">{money(d.get("original_price"))}</s>'
           if d.get("kind") == "platform" and d.get("original_price") else "")
    off = ""
    if pct:
        kind = "the store's own discount" if d.get("kind") == "platform" else "under its usual price"
        off = f'<span class="off" title="{kind}">−{round(pct)}%</span>'
    meta = []
    if d.get("kind") == "drop" and d.get("usual_price"):
        meta.append(f"<span>usually {money(d['usual_price'])}</span>")
    if d.get("saving"):
        meta.append(f"<span>save {money(d['saving'])}</span>")
    if d.get("unit_text"):
        meta.append(f'<span class="unit">{e(d["unit_text"])}</span>')
    v = d.get("verdict") or {}
    # the "went up" warning already says what the advice's reason would say
    why = "" if "rose" in flags and str(v.get("why") or "").startswith("It was ") else v.get("why")
    advice = (f'<div class="advice"><span class="pill {e(v.get("tone") or "muted")}">{e(v.get("label"))}</span>'
              + (f'<span class="muted">{e(why)}</span>' if why else "") + "</div>" if v.get("label") else "")
    notes = ""
    r = d.get("rhythm")
    if r and r.get("text"):
        notes += f'<div class="note">⏳ <b>Sale rhythm</b><br>{e(r["text"])}</div>'
    if d.get("promo_code"):
        code = e(d["promo_code"])
        notes += (f'<div class="note">🎟️ With the code <button class="code" type="button" data-copy="{code}" data-noi18n>'
                  f'<code>{code}</code></button> at checkout: {money(d.get("promo_price"))} EGP</div>')

    # history: the chart, then the numbers
    pts = _series(history, now, 90, price)
    in90 = [x for _, x, s in pts if s] or [x for _, x, _ in pts] or [price]
    changes = _changes(history)
    stats = [(f'<span><i class="dot lo"></i>Lowest in 90 days</span>', f"{money(min(in90))} <small>EGP</small>"),
             (f'<span><i class="dot"></i>Highest in 90 days</span>', f"{money(max(in90))} <small>EGP</small>")]
    if d.get("usual_price"):
        stats.append(("<span>Usual price</span>", f"{money(d['usual_price'])} <small>EGP</small>"))
    stats.append(("<span>Price changes</span>", str(max(len(changes) - 1, 0))))
    stats.append((f"<span>{bi('Tracked since', 'متابَع من')}</span>", day_bi(p["first_seen"], year=True)))
    stat_html = "".join(f'<div class="stat">{k}<b>{v}</b></div>' for k, v in stats)
    log = ""
    if len(changes) > 1:
        rows = "".join(
            f'<tr><td class="num">{_dmy(t, now)}</td><td class="n"><b>{money(x)}</b></td>'
            f'<td class="n {"dn" if prev is not None and x < prev else "up"}">'
            f'{_num(("−" if x < prev else "+") + money(abs(x - prev))) if prev is not None else ""}</td></tr>'
            for t, x, prev in reversed(changes[-8:]))
        log = f'<details class="log"><summary>Price changes</summary><table>{rows}</table></details>'

    # the same product elsewhere
    cmp = ""
    others = sorted([dict(d, id=pid)] + list(matches), key=lambda x: x["price"]) if d and matches else []
    if len(others) > 1:
        rows = []
        for i, x in enumerate(others):
            if not x.get("id"):
                continue
            me = int(x["id"]) == pid
            diff = x["price"] - price
            delta = (f'<span class="{"dn" if diff < 0 else "up"} small">{_num(("−" if diff < 0 else "+") + money(abs(diff)))}</span>'
                     if not me and abs(diff) >= 0.01 else "")
            rows.append(
                f'<tr{" class=me" if me else ""}><td>{_store(x["store"])}<span class="sn" dir="auto">{e(str(x.get("name") or "")[:80])}</span></td>'
                f'<td class="n"><b>{money(x["price"])}</b> <small>EGP</small> {delta}'
                f'{"<span class=best>cheapest</span>" if i == 0 else ""}'
                f'{"<span class=best style=color:var(--muted)>out of stock</span>" if not x.get("in_stock", True) else ""}</td>'
                f'<td class="n"><a class="btn" {_link_attrs(x)}>Open ↗</a></td></tr>')
        cmp = f'<h2>Same product in other stores</h2><div class="panel"><table class="cmp">{"".join(rows)}</table></div>'
    rel = ('<h2>More deals like this</h2><div class="grid">' + "".join(mini_card(x, store=False) for x in related[:8]) + "</div>"
           if related else "")

    crumb = [_store(store)]
    if hub_name:
        crumb.append(f'<span dir="auto">📍 {e(hub_name)}</span>')
    top_cat = d.get("top_category") or (p.get("category") or "").split(" / ")[0]
    if top_cat:
        crumb.append(f'<a href="/?deals=1&amp;category={e(quote(top_cat))}"><bdi>{e(top_cat)}</bdi></a>')
    img = (f'<img src="{e(p["image"])}" alt="{e(p["name"])}" width="600" height="600" decoding="async" fetchpriority="high" '
           f'onerror="this.remove()">' if p.get("image") else "")
    label = "Open in " + APP.get(store, "store") + " ↗"
    share_title = f'{p["name"][:90]} · {money(price)} EGP'
    wa = "https://wa.me/?text=" + quote(f"{share_title} {page_url}")
    body = f"""<div class="crumb">{"".join(crumb)}</div>
<div class="hero"><div class="shot"><span class="ph">{_initial(p["name"])}</span>{img}</div><div class="info">
<h1 dir="auto">{e(p["name"])}</h1>
<div class="price"><span class="now">{money(price)} <small>EGP</small></span>{was}{off}</div>
{f'<div class="meta">{"".join(meta)}</div>' if meta else ""}
{f'<div class="tags">{"".join(tags)}</div>' if tags else ""}{advice}{f'<div class="alerts">{"".join(alerts)}</div>' if alerts else ""}
<div class="cta"><a class="btn primary" {_link_attrs(d or p)}>{e(label)}</a>
<a class="btn" href="/?p={pid}">★ Watch</a>
<a class="btn" href="{e(wa)}" target="_blank" rel="noopener" data-share="{e(page_url)}" data-title="{e(share_title)}">Share</a></div>
{notes}</div></div>
<h2>Price history</h2><div class="panel">{chart(history, now, usual=d.get("usual_price"), current=price) or ""}
<div class="stats">{stat_html}</div>{log}</div>
{cmp}{rel}"""

    ar_name = p["name"][:90]
    desc = " · ".join(x for x in [
        f"خصم {round(pct)}%" if pct else "",
        "🔥 أقل سعر في 90 يوم" if "lowest" in flags else "",
        f"بدل {money(d.get('original_price'))} ج.م" if d.get("kind") == "platform" else "",
        AR_STORE.get(store, store)] if x)
    og = {"title": f"{ar_name} · {money(price)} ج.م", "description": desc, "image": p.get("image"),
          "url": page_url, "type": "product", "site_name": "عروض لحظة بلحظة", "locale": "ar_EG"}
    offer = {"@type": "Offer", "price": round(float(price), 2), "priceCurrency": "EGP",
             "availability": "https://schema.org/" + ("InStock" if in_stock else "OutOfStock"),
             "url": p.get("url") or page_url, "seller": {"@type": "Organization", "name": LABELS.get(store, store)}}
    ld = {"@context": "https://schema.org", "@type": "Product", "name": p["name"], "url": page_url, "offers": offer}
    if p.get("image"):
        ld["image"] = [p["image"]]
    if p.get("sku"):
        ld["sku"] = str(p["sku"])
    if p.get("brand"):
        ld["brand"] = {"@type": "Brand", "name": p["brand"]}
    if p.get("category"):
        ld["category"] = p["category"]
    head = (f'<link rel="canonical" href="{e(page_url)}">'
            f'<meta property="product:price:amount" content="{e(round(float(price), 2))}">'
            f'<meta property="product:price:currency" content="EGP">'
            f'<script type="application/ld+json">{_json_ld(ld)}</script>')
    return layout(p["name"], body, og=og, title_ar=f"{ar_name} · {money(price)} ج.م", css=PRODUCT_CSS, head=head,
                  main_attrs=f' data-pid="{pid}"', toast=True, js=("links", "copy", "product"))


# ---------------------------------------------------------------------------------------------- stores

def _clock(t: float, now: float) -> str:
    """When something last happened, short: 'today 14:05' / '12 Oct'."""
    if now - t < DAY and datetime.fromtimestamp(t, CAIRO).date() == datetime.fromtimestamp(now, CAIRO).date():
        return f'{bi("today", "النهارده")} <span class="num">{datetime.fromtimestamp(t, CAIRO).strftime("%H:%M")}</span>'
    return day_bi(t)


def stores_page(status_rows: list[dict], facets_all: dict, facets_deals: dict, best: dict, now: float = None) -> bytes:
    now = now or time.time()
    rows = sorted(status_rows, key=lambda s: (not s.get("live"), -facets_deals.get(s["store"], 0)))
    tiles = []
    for s in rows:
        sid = s["store"]
        n_deals = facets_deals.get(sid, 0)
        products = s.get("products") or 0
        state = ("paused" if s.get("paused_until") else "live") if s.get("live") else "not running"
        share = min(100, round(100 * n_deals / products)) if products else 0
        last = s.get("last_change") or s.get("updated")
        foot = []
        if best.get(sid):
            foot.append(f'<span class="hot">up to {round(best[sid])}% off</span>')
        if last:
            foot.append(f'<span>{bi("Last change", "آخر تغيير")} {_clock(last, now)}</span>')
        tiles.append(
            f'<a class="tile{" off" if state == "not running" else ""}" href="/store/{e(sid)}" style="--c:var(--{e(sid)})">'
            f'<div class="h"><i></i><span>{e(s.get("label") or LABELS.get(sid, sid))}</span>'
            f'<span class="state {state.split()[0]}">{e(state)}</span></div>'
            f'<div class="k">{n_deals:,}</div><div class="s">deals · {products:,} products</div>'
            f'<div class="bar" aria-hidden="true"><i style="width:{share}%"></i></div>'
            f'<div class="row">{"".join(foot)}</div></a>')
    total = sum(facets_deals.get(s["store"], 0) for s in status_rows)
    sub = bi(f"{total:,} deals right now across {len(status_rows)} stores. Tap a store to see its deals.",
             f"{total:,} عرض دلوقتي في {len(status_rows)} متاجر. دوس على أي متجر تشوف عروضه.")
    body = (f'<h1 class="title">All stores</h1><p class="sub">{sub}</p>'
            f'<div class="tiles">{"".join(tiles) or "<div class=empty>…</div>"}</div>')
    return layout("Stores", body, active="/stores", og={"title": "المتاجر · عروض لحظة بلحظة"}, title_ar="المتاجر",
                  css=STORES_CSS)


# ---------------------------------------------------------------------------------------------- categories

def _q(s: str) -> str:
    """A query value, percent-encoding only what has to be (Arabic stays as it is: shorter, and browsers encode it)."""
    return "".join(c if ord(c) > 127 else quote(c, safe="") for c in s)


def cats_page(cats: list[tuple[str, int, float]]) -> bytes:
    rows = []
    for i, (c, n, top) in enumerate(cats):
        pct = f"<i>−{round(top)}%</i>" if top else "<i></i>"
        rows.append(f'<li{" class=big" if i < 6 else ""}><a href="/?deals=1&amp;category={e(_q(c))}">'
                    f'<bdi>{e(c)}</bdi><b>{n:,}</b>{pct}</a></li>')
    find = ('<form class="find" action="/" role="search"><input type="search" id="cf" name="q" '
            'placeholder="دوّر على قسم" aria-label="Categories" autocomplete="off" enterkeyhint="search"></form>')
    total = sum(n for _, n, _ in cats)
    sub = bi(f"{len(cats)} categories, {total:,} deals. The busiest first; the number is how many deals, then the biggest discount.",
             f"{len(cats)} قسم، {total:,} عرض. الأكتر عروض الأول؛ الرقم عدد العروض، وبعده أكبر خصم.")
    body = (f'<h1 class="title">Categories</h1><p class="sub">{sub}</p>{find if len(cats) > 8 else ""}'
            f'<ul class="cats">{"".join(rows)}</ul>' if rows else
            f'<h1 class="title">Categories</h1><div class="empty">{bi("No deals in any category yet: the first sweep takes about 15 minutes.", "مفيش عروض في أي قسم لسه: أول لفة بتاخد حوالي ربع ساعة.")}</div>')
    return layout("Categories", body, active="/cats", og={"title": "الأقسام · عروض لحظة بلحظة"}, title_ar="الأقسام",
                  css=CATS_CSS, js=("cats",) if len(cats) > 8 else ())


# ---------------------------------------------------------------------------------------------- coupons

def _today() -> str:
    return datetime.now(CAIRO).strftime("%Y-%m-%d")


def _expiry(exp: str, today: str) -> str:
    try:
        end = datetime.strptime(exp, "%Y-%m-%d").replace(tzinfo=CAIRO)
        left = (end.date() - datetime.strptime(today, "%Y-%m-%d").date()).days
    except ValueError:
        return f"<span>until {e(exp)}</span>"
    soon = ""
    if left == 0:
        soon = f' <span class="soon">{bi("last day", "آخر يوم")}</span>'
    elif left <= 3:
        soon = f' <span class="soon">{bi(f"{left} days left" if left > 1 else "1 day left", f"فاضل {left} يوم" if left > 1 else "فاضل يوم")}</span>'
    return f"<span>until {day_bi(end.timestamp(), year=True)}{soon}</span>"


COPY = bi("Copy", "انسخ")


def coupons_page(coupons: list[dict]) -> bytes:
    today = _today()
    live = [c for c in coupons if not c.get("expires") or c["expires"] >= today]
    # the owner's own coupons first, then the codes found on the stores' products; each store together
    order = {}
    for c in live:
        order.setdefault(c.get("store") or "", len(order))
    live.sort(key=lambda c: (bool(c.get("auto")), order[c.get("store") or ""]))
    cards = []
    for c in live:
        code = e(c["code"])
        go = f' data-go="{e(c["url"])}"' if c.get("url") else ""
        sid = c.get("store") or ""
        link = (f'<a class="go" href="{e(c["url"])}" target="_blank" rel="noopener">Go to store</a>' if c.get("url") else "")
        cards.append(
            f'<div class="cpn" style="--c:var(--{e(sid)})"><div class="m">{_store(sid)}'
            f'{_expiry(c["expires"], today) if c.get("expires") else ""}</div>'
            f'<div class="t"{"" if c.get("auto") else " dir=auto"}>{e(c.get("title") or "")}</div>'
            f'<div class="row"><button class="code" type="button" data-copy="{code}"{go}><code>{code}</code>'
            f'<span class="cp">{COPY}</span><span class="ok">Copied</span></button>{link}</div></div>')
    body = (f'<h1 class="title">🎟️ Coupons</h1><p class="sub">Tap a code to copy it</p>'
            + (f'<div class="coupons">{"".join(cards)}</div>' if cards else '<div class="empty">No coupons yet.</div>'))
    return layout("Coupons", body, active="/coupons", og={"title": "كوبونات · عروض لحظة بلحظة"}, title_ar="كوبونات",
                  css=COUPONS_CSS, js=("copy",))


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


# ---------------------------------------------------------------------------------------------- about, privacy, contact

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
    return layout("About", _doc(ar, en), active="/about", og={"title": "عن عروض لحظة بلحظة"}, title_ar="عن الموقع",
                  css=DOC_CSS)


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
    return layout("Privacy", _doc(ar, en), active="/privacy", og={"title": "الخصوصية · عروض لحظة بلحظة"}, title_ar="الخصوصية",
                  css=DOC_CSS)


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
    return layout("Contact", _doc(ar, en), active="/contact", og={"title": "تواصل · عروض لحظة بلحظة"}, title_ar="تواصل معانا",
                  css=DOC_CSS)
