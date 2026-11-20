"""Static checks on the live page, its translations and its service worker (docs/REBUILD.md, "Live page").

They read the files as text, so they run anywhere with `python3 -m unittest tests.test_live_page`. One test runs the
translator under Node when Node is installed, and is skipped otherwise.
"""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent / "deals"
PAGE = (HERE / "live.html").read_text(encoding="utf-8")
I18N = (HERE / "i18n.js").read_text(encoding="utf-8")
SW = (HERE / "sw.js").read_text(encoding="utf-8")


def script(text: str) -> str:
    """The page's own JavaScript, without the markup and the CSS."""
    return "\n".join(re.findall(r"<script>(.*?)</script>", text, re.S))


JS = script(PAGE)


class PulseLoop(unittest.TestCase):
    def test_one_thirty_second_loop_on_pulse(self):
        self.assertIn('fetch("/api/pulse"', JS)
        self.assertRegex(JS, r"EVERY\s*=\s*30000")
        self.assertIn("setTimeout(pulse, EVERY)", JS)

    def test_old_independent_timers_are_gone(self):
        # the old page asked for the list every 10 s, the ticker every 15 s and the brief every 60 s
        for gone in ("setInterval(loadTicker", "setInterval(loadBrief", "setInterval(checkWatch", "load({ quiet: true })"):
            self.assertNotIn(gone, JS)
        self.assertNotRegex(JS, r"setInterval\([^)]*fetch")
        self.assertNotRegex(JS, r"setTimeout\([^,]+,\s*(10000|15000|60000)\)")

    def test_same_version_asks_for_nothing_else(self):
        body = JS[JS.index("async function pulse("):JS.index("async function onNewVersion")]
        pulse_branch = body[body.index('if (mode === "pulse")'):body.index("} else {")]
        # in the pulse branch the only request is the pulse itself; everything else waits for a new v
        self.assertEqual(pulse_branch.count("fetch("), 1)
        self.assertIn("if (changed || !netLoaded) await onNewVersion()", pulse_branch)
        new_v = JS[JS.index("async function onNewVersion"):JS.index("document.addEventListener(\"visibilitychange\"")]
        for call in ("refreshView()", "loadTicker()", "checkWatch()"):
            self.assertIn(call, new_v)

    def test_brief_numbers_come_from_the_pulse(self):
        self.assertIn("if (p.brief) briefNumbers(p.brief)", JS)

    def test_cards_ask_for_card_fields_with_etags(self):
        self.assertIn('fields: "card"', JS)
        self.assertIn('headers["If-None-Match"]', JS)
        self.assertIn("r.status === 304", JS)
        for path in re.findall(r"`/api/deals\?[^`]*`", JS):
            self.assertIn("fields=card", path)

    def test_fallback_for_an_old_server(self):
        self.assertRegex(JS, r'r\.status === 404\)\s*\{\s*mode = "fallback"')
        fallback = JS[JS.index("} else {", JS.index("async function pulse(")):JS.index("markFresh(); setError(false);")]
        self.assertIn("refreshView()", fallback)

    def test_paused_when_hidden_or_offline_and_one_pulse_on_return(self):
        self.assertIn('document.addEventListener("visibilitychange", () => { if (document.hidden) clearTimeout(pulseTimer); else pulse(); })', JS)
        self.assertIn('addEventListener("online", () => { offState = false; pulse(); })', JS)
        self.assertIn('addEventListener("offline"', JS)
        self.assertIn("if (pulsing || document.hidden) return;", JS)

    def test_filter_change_aborts_and_shows_skeletons(self):
        load = JS[JS.index("async function loadView"):JS.index("async function loadMore")]
        self.assertIn("ctrl?.abort()", load)
        self.assertIn("new AbortController()", load)
        self.assertIn("skeleton(", load)

    def test_patch_not_redraw(self):
        self.assertIn("function reconcile(", JS)
        self.assertIn("function patchInPlace(", JS)
        self.assertIn("function sigOf(", JS)
        self.assertIn('id="newPill"', PAGE)
        # the refresh never rewrites the whole grid
        refresh = JS[JS.index("async function refreshView"):JS.index("function apply()")]
        self.assertNotIn("innerHTML", refresh)

    def test_freshness_indicator_refreshes_on_tap(self):
        self.assertIn('id="fresh"', PAGE)
        self.assertIn('$("fresh").onclick = () => pulse(true)', JS)


class Interface(unittest.TestCase):
    TOKENS = {"--bg": "#f7f6f3", "--panel": "#ffffff", "--ink": "#17161a", "--muted": "#6b6871", "--faint": "#a3a0a8",
              "--line": "#e7e4de", "--accent": "#e0452c", "--accent-ink": "#ffffff", "--good": "#16794a", "--good-bg": "#e2f4ea",
              "--warn": "#9a6500", "--warn-bg": "#fbf0d2", "--chip": "#f0eee9", "--r-s": "8px", "--r-m": "12px", "--r-l": "18px"}
    DARK = {"--bg": "#0f0f11", "--panel": "#19191d", "--ink": "#f2f0ea", "--muted": "#a09ca6", "--faint": "#6a6770", "--line": "#2a2930",
            "--accent": "#ff6a52", "--accent-ink": "#1a0906", "--good": "#5fd09a", "--good-bg": "#14301f", "--warn": "#e8bb52",
            "--warn-bg": "#38300f", "--chip": "#232228", "--amazon_eg": "#8aa0bb"}

    def test_design_tokens_match_the_plan(self):
        root = PAGE[PAGE.index(":root {"):PAGE.index("@media (prefers-color-scheme: dark)")]
        for k, v in self.TOKENS.items():
            self.assertIn(f"{k}: {v};", root)
        dark = PAGE[PAGE.index(':root[data-theme="dark"] {'):]
        dark = dark[:dark.index("}")]
        for k, v in self.DARK.items():
            self.assertIn(f"{k}: {v};", dark)

    def test_accessibility_basics(self):
        self.assertIn("--tap: 44px", PAGE)
        self.assertIn(":focus-visible", PAGE)
        self.assertIn("@media (prefers-reduced-motion: reduce)", PAGE)
        self.assertIn("tabular-nums", PAGE)
        self.assertIn('e.key === "/"', JS)
        self.assertIn('e.key === "Escape"', JS)

    def test_pictures_never_shift_the_layout(self):
        self.assertIn("aspect-ratio: 1 / 1", PAGE)
        self.assertIn('loading="lazy"', JS)
        self.assertIn('decoding="async"', JS)
        self.assertIn("content-visibility: auto", PAGE)

    def test_nothing_a_visitor_can_do_is_lost(self):
        for needle in ('href="/?deals=1&amp;sort=newest&amp;status=new"', 'href="/cats"', 'href="/stores"', 'href="/coupons"',
                       'href="/?recent=1"', 'href="/about"', 'href="/privacy"', 'href="/contact"', 'id="langBtn"', 'id="watchBtn"',
                       'id="basketBtn"', 'id="alertsBtn"', 'id="installBtn"', 'id="mic"', 'id="sort"', 'id="filters"',
                       'id="hubBar"', 'id="brief"', 'id="ticker"', 'id="dlg"',
                       "As an Amazon Associate we earn from qualifying purchases."):
            self.assertIn(needle, PAGE, needle)
        for v in ("score", "discount", "saving", "newest", "drop", "price", "price_desc", "unit", "popular", "name"):
            self.assertIn(f'<option value="{v}">', PAGE)
        for v in ("real", "new", "lowest", "label", "back", "compare", "fake", "promo", "soon"):
            self.assertIn(f'value="{v}"', PAGE)
        for fn in ("function understand(", "beforeinstallprompt", "aPredict", "function chime(", '"myHubs"', '"recent"',
                   'get("p")', "links?.android", "function showBasket("):
            self.assertIn(fn, PAGE, fn)


class Translations(unittest.TestCase):
    NEW = ["Watching every store for price changes…", "Store status", "Refresh now", "Clear all", "Nothing matches these filters",
           "Try removing a filter or two, or search for something broader.", "Reset filters", "Clear the search", "You're offline",
           "Can't reach the server", "Check your connection. We'll keep trying every 30 seconds.", "Try again", "Loading price history…",
           "Browse deals", "Your watchlist is empty", "Nothing viewed yet", "Menu", "Price-drop alerts", "Install the app",
           "Watch this price", "Close", "Search", "Sort by", "A new version of the site is ready. Tap to reload.",
           "Open any product and tap Add to basket. The basket shows the cheapest store for each item, using the same products found in other stores."]

    def test_new_strings_have_arabic(self):
        for s in self.NEW:
            self.assertIn(json.dumps(s, ensure_ascii=False) + ":", I18N, s)

    def test_static_text_in_the_page_is_translated(self):
        # every English text node written in the page's markup has an exact translation (product names come from data)
        body = PAGE[PAGE.index("<body>"):PAGE.index("<script>", PAGE.index("<body>"))]
        body = re.sub(r"<svg.*?</svg>", "", body, flags=re.S)
        texts = {t.strip() for t in re.findall(r">([^<>]+)<", body)}
        keys = set(re.findall(r'"((?:[^"\\]|\\.)+)":', I18N))
        missing = [t for t in texts if re.search(r"[A-Za-z]{2,}", t) and t not in keys and t not in ("EN",)]
        self.assertEqual(missing, [])

    @unittest.skipUnless(shutil.which("node"), "needs Node")
    def test_translator_rules(self):
        cases = {"updated 12 s ago": "اتحدّث من 12 ث", "Show 1,343 results": "اعرض 1,343 نتيجة", "3 new deals ↑": "↑ 3 عرض جديد",
                 "dropped 3 min ago": "نزل من 3 د", "Open in Amazon ↗": "افتح في أمازون ↗", "EGP": "ج.م",
                 "12 price changes in the last hour": "12 تغيير سعر في آخر ساعة", "· 4 stores": "· 4 متاجر"}
        js = ("global.localStorage={getItem:()=>'ar'};global.document={readyState:'loading',addEventListener(){},"
              "documentElement:{},getElementById:()=>null,title:''};"
              "global.MutationObserver=function(){this.observe=()=>{}};global.window=global;global.performance={now:()=>0};"
              f"eval(require('fs').readFileSync({json.dumps(str(HERE / 'i18n.js'))},'utf8'));"
              f"const c={json.dumps(cases, ensure_ascii=False)};const out={{}};for(const k in c)out[k]=I18N.tr(k);"
              "process.stdout.write(JSON.stringify(out));")
        got = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(got, cases)


class ServiceWorker(unittest.TestCase):
    def test_versioned_and_prompt_to_update(self):
        self.assertRegex(SW, r'const VERSION = "[^"]+"')
        self.assertIn('"deals-shell-" + VERSION', SW)
        self.assertIn("skipWaiting()", SW)
        self.assertIn("clients.claim()", SW)
        self.assertIn("caches.delete(k)", SW)
        self.assertIn('cache: "reload"', SW)
        self.assertIn("shell-updated", SW)
        self.assertIn("shell-updated", JS)

    def test_never_caches_admin_posts_or_push(self):
        fetch_handler = SW[SW.index('self.addEventListener("fetch"'):SW.index("async function homeView")]
        self.assertIn('if (req.method !== "GET") return;', fetch_handler)
        self.assertIn('url.pathname.startsWith("/admin")', SW)
        self.assertIn('url.pathname.startsWith("/api/push/")', SW)
        self.assertLess(fetch_handler.index("never(url)) return;"), fetch_handler.index("respondWith"))
        self.assertNotIn("/admin", re.search(r"const PRECACHE = \[(.*?)\];", SW).group(1))

    def test_shell_and_home_view_cached(self):
        pre = re.search(r"const PRECACHE = \[(.*?)\];", SW).group(1)
        for u in ('"/"', '"/i18n.js"', '"/icon-192.png"'):
            self.assertIn(u, pre)
        data = re.search(r'const DATA = "([^"]+)"', SW).group(1)
        self.assertIn(f'const DATA_CACHE = "{data}"', PAGE)   # the page reads the same cache for its first paint
        self.assertIn('"X-Home-View"', PAGE)
        self.assertIn('req.headers.get("X-Home-View")', SW)

    def test_push_still_handled(self):
        for needle in ('addEventListener("push"', 'addEventListener("notificationclick"', "deal-push"):
            self.assertIn(needle, SW)


if __name__ == "__main__":
    unittest.main()
