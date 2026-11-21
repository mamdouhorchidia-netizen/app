"""The live deals page and its JSON API, on Python's built-in HTTP server.

  /                      the live page (refreshes itself every 10 seconds): one document, its styles and modules written in
  /api/deals             filters: deals=1|0 stores=a,b kind=platform|drop min= max= stock=in|out|all
                         status=new,lowest,real,label,back,compare category= q= pmin= pmax= ids=1,2 best=1
                         sort=score|discount|saving|newest|drop|price|price_desc|unit|popular|name limit= offset=
                         each item carries only what the page's cards read (web.LIST_FIELDS); full=1: every field
  /api/changes           the latest price moves (?limit=), or only those newer than a price row (?after=ROWID)
  /api/pulse             tiny and cheap: data version `v`, server time, each store's status, changes in the last hour
  /api/seen?ids=1,2      when each product was last checked (up to 60)
  /api/product/ID        one product: history, links, matches in other stores, score and why
  /api/status            each store: products, last check, last change, cycle time, errors
  /manifest.webmanifest, /icon.svg   so the page installs as an app
  /tokens.css            the design tokens the live page and the server-written pages share
  /static/BUILD/NAME     the live page's stylesheets and modules (static/*.css, *.js, plus tokens.css and i18n.js),
                         addressed by a hash of all of them, so cached for good (immutable)
  /healthz               200 when the database answers (Coolify's health check)
  /admin                 the owner's page: talabat hubs to follow, and adding Breadfast
  /api/talabat/hubs      every known talabat mart hub (name, number, areas) and the ones followed
  POST /admin/settings   {"talabat_hubs": [ids]} or {"hub_link": "https://www.talabat.com/egypt/grocery/<hub>/..."}
  POST /admin/breadfast  the capture itself (?divisor=1|100), with "Authorization: Bearer $DEALS_ADMIN_TOKEN";
                         off (403) until DEALS_ADMIN_TOKEN is set
  POST /admin/breadfast-request   {"curl": "...", "divisor": 1}: one product-list request copied as cURL
  /p/ID                  a product's own page (history, verdict, other stores; a preview card on WhatsApp/Telegram)
  /stores, /store/ID     every store, and one store's deals · /cats every category · /coupons the owner's codes
  POST /admin/coupons    {"text": "store | CODE | what it gives | until | link" one per line}; GET /api/coupons
"""

import gzip
import hashlib
import hmac
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import db, finder, livedata, pages, settings
from .stores import LABELS, breadfast, talabat

PAGE = Path(__file__).with_name("live.html")
ADMIN_PAGE = Path(__file__).with_name("admin.html")
HUBS_FILE = Path(__file__).parent / "stores" / "talabat_hubs.json"


def known_hubs(db_path: str = None) -> dict:
    """{hub id: {"id", "name", "aid", "areas": [...]}}: the list shipped with the app, plus what the server's
    own weekly look-up (talabat_hubs.json next to the database) has found."""
    raw = {}
    for f in [HUBS_FILE] + ([Path(os.path.dirname(os.path.abspath(db_path))) / "talabat_hubs.json"] if db_path else []):
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for k, h in (d.get("hubs") if "hubs" in d else d).items():
            old = raw.get(str(k), {})
            areas = {a["id"]: a for a in (old.get("areas") or []) + (h.get("areas") or [])}
            raw[str(k)] = {**old, **h, "areas": list(areas.values())}
    out = {}
    for h in raw.values():
        areas = h.get("areas") or []
        out[int(h["id"])] = {"id": int(h["id"]), "name": h.get("name") or talabat.hub_from_slug(h.get("slug") or ""),
                             "aid": areas[0]["id"] if areas else 9838, "areas": areas}
    return out


MAX_UPLOAD = 300 * 1024 * 1024
_import_lock = threading.Lock()
CACHE_SECONDS = 5
PULSE_SECONDS = 1.0  # /api/pulse and the status inside /api/deals are read from the database at most this often
QUERY_CACHE = 64  # filter sets whose sorted matches are kept, per catalog copy
SEEN_MAX = 60
KEEP_ALIVE = 120  # seconds an idle connection stays open (longer than a reverse proxy's 90 s idle pool)
GZIP_MIN = 1024
COMPRESSIBLE = ("text/", "application/json", "application/manifest+json", "image/svg+xml")
MANIFEST = json.dumps({
    "name": "عروض لحظة بلحظة", "short_name": "العروض", "lang": "ar", "dir": "rtl", "start_url": "/", "scope": "/", "display": "standalone",
    "background_color": "#f6f5f2", "theme_color": "#d6402b", "description": "Live prices and real deals",
    "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
              {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
              {"src": "/icon-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
}).encode()
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://deals.vorath.cloud").rstrip("/")
STATIC = {"/sw.js": ("sw.js", "text/javascript"), "/i18n.js": ("i18n.js", "text/javascript"), "/tokens.css": ("tokens.css", "text/css; charset=utf-8"),
          "/icon-192.png": ("icon-192.png", "image/png"),
          "/icon-512.png": ("icon-512.png", "image/png"), "/icon-maskable.png": ("icon-maskable.png", "image/png")}
# the browser push services; the server sends pushes nowhere else
PUSH_HOSTS = ("fcm.googleapis.com", "push.services.mozilla.com", "push.apple.com", "notify.windows.com")
ICON = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><rect width="512" height="512" rx="112" '
        b'fill="#d6402b"/><path d="M150 330 L250 230 L300 280 L390 180" stroke="#fff" stroke-width="44" fill="none" '
        b'stroke-linecap="round" stroke-linejoin="round"/><circle cx="390" cy="180" r="34" fill="#fff"/></svg>')


def status(con) -> list[dict]:
    """One row per store that has products or has been checked."""
    counts = dict(con.execute("SELECT store, COUNT(*) FROM products GROUP BY store").fetchall())
    live = {r["store"]: dict(r) for r in con.execute("SELECT * FROM live_status")}
    runs = {r["store"]: dict(r) for r in con.execute("""
        SELECT r.* FROM runs r JOIN (SELECT store, MAX(id) id FROM runs WHERE finished IS NOT NULL GROUP BY store) l
        ON r.id = l.id""")}
    out = []
    for store in sorted(set(counts) | set(live) | set(runs)):
        row = {"store": store, "label": LABELS.get(store, store), "products": counts.get(store, 0)}
        if store in live:
            l = live[store]
            row.update(live=True, updated=l["updated"], last_change=l["last_change"] or None,
                       cycle_secs=l["cycle_secs"], pages_per_cycle=l["tasks"], error=l["error"],
                       paused_until=l["paused_until"] if (l["paused_until"] or 0) > time.time() else None)
        elif store in runs:
            r = runs[store]
            row.update(live=False, updated=r["finished"], last_change=None, cycle_secs=None, error=r["error"])
        out.append(row)
    return out


# what a live store's status row carries but the page never shows: `updated` moves with every page checked, so
# in it the pulse's ETag changed on every poll (no 304s at all on real stores), and `pages_per_cycle` with
# every page discovered. Left out of the pulse's ETag (still in the answer); an idle store's `updated` counts.
PULSE_UNSHOWN = frozenset({"updated", "pages_per_cycle"})


def pulse_etag_view(value: dict) -> dict:
    return {**value, "status": [{k: x for k, x in s.items() if not (s.get("live") and k in PULSE_UNSHOWN)}
                                for s in value.get("status") or []]}


def etag_of(*parts) -> str:
    """A weak ETag (W/"…") over JSON-able parts: weak because a 200 may differ in its time fields."""
    h = hashlib.blake2b(digest_size=12)
    for x in parts:
        h.update(x if isinstance(x, bytes) else json.dumps(x, sort_keys=True, ensure_ascii=False, default=sorted).encode())
        h.update(b"\0")
    return f'W/"{h.hexdigest()}"'


def accepts_gzip(header: str) -> bool:
    for part in (header or "").lower().split(","):
        name, _, params = part.strip().partition(";")
        if name.strip() in ("gzip", "*"):
            q = params.strip()
            return not (q.startswith("q=") and q[2:].strip() in ("0", "0.0", "0.00", "0.000"))
    return False


def etag_matches(header: str, etag: str) -> bool:
    if not header or not etag:
        return False
    if header.strip() == "*":
        return True
    bare = lambda t: t.strip().removeprefix("W/")
    return bare(etag) in {bare(t) for t in header.split(",")}


_static: dict = {}  # path -> (mtime_ns, size, body, gzipped, etag)


def _entry(body: bytes) -> tuple[bytes, bytes, str]:
    return body, gzip.compress(body, 9, mtime=0), f'W/"{hashlib.blake2b(body, digest_size=12).hexdigest()}"'


def static_file(path: Path) -> tuple[bytes, bytes, str]:
    """(body, gzipped body, ETag), read once and again only when the file changes (a deploy)."""
    st = path.stat()
    hit = _static.get(path)
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2:]
    entry = (st.st_mtime_ns, st.st_size, *_entry(path.read_bytes()))
    _static[path] = entry
    return entry[2:]


# The live page's styles and modules: plain files in static/ (no build step), plus the two it shares with the
# server-written pages. The build is a short hash of all of them. The page is sent as one document: live.html with
# its stylesheets, /i18n.js and its modules written into it (the modules joined into one, below), so a first visit
# is one request instead of twenty, the page arrives with exactly the code it was written with, and a repeat visit is
# one 304. Each file is also served at /static/<build>/<name>: that address changes whenever any file does, so it is
# cached for good (immutable); an address from another build answers 404. DEALS_PAGE=split sends live.html with
# those addresses instead (each module its own file, for working on them in the browser's tools).
STATIC_DIR = Path(__file__).with_name("static")
SHARED_ASSETS = {"tokens.css": Path(__file__).with_name("tokens.css"), "i18n.js": Path(__file__).with_name("i18n.js")}
ASSET_TYPES = {".js": "text/javascript", ".css": "text/css; charset=utf-8"}
IMMUTABLE = "public, max-age=31536000, immutable"
PAGE_MODE = os.environ.get("DEALS_PAGE", "inline")  # "inline" | "split"
ENTRY = "main.js"
_build: dict = {}  # "key": the files' (name, mtime, size), "id": their hash; "page": key -> (body, gzipped, etag)


def assets() -> dict[str, Path]:
    """name -> file, for everything the live page loads from /static/<build>/."""
    files = {p.name: p for p in sorted(STATIC_DIR.iterdir()) if p.suffix in ASSET_TYPES and p.is_file()}
    return {**files, **SHARED_ASSETS}


def build_id() -> str:
    """A short hash of every asset's content, worked out again only when one of them changes (a deploy)."""
    files = assets()
    key = tuple((name, st.st_mtime_ns, st.st_size) for name, p in sorted(files.items()) for st in [p.stat()])
    if _build.get("key") != key:
        h = hashlib.blake2b(digest_size=6)
        for name, p in sorted(files.items()):
            h.update(name.encode() + b"\0" + static_file(p)[0] + b"\0")
        _build.update(key=key, id=h.hexdigest(), page={})
    return _build["id"]


class NotBundled(ValueError):
    """The modules use something the joiner doesn't handle: the page is sent with one file per module instead."""


# The modules are written in one narrow form, which is what lets them be joined without a parser: every import is
# `import { a, b as c } from "./x.js";` on a line of its own, and every export is `export` at the start of a line
# in front of a const, function or async function declaration (no `export let`: an imported binding never changes).
# Anything else at the start of a line that says import or export makes the joiner give up (NotBundled).
IMPORT_LINE = re.compile(r'^import\s*\{([^}]*)\}\s*from\s*"\./([\w-]+\.js)";[ \t]*(?://[^\n]*)?$', re.M)
EXPORT_DECL = re.compile(r'^export\s+(?=(?:async\s+function|function|const)\b)', re.M)
LEFTOVER = re.compile(r'^\s*(?:import\b(?!\s*\()|export\b)', re.M)
IDENT = re.compile(r"^[A-Za-z_$][\w$]*$")


def bundle(sources: dict[str, str], entry: str = ENTRY) -> str:
    """The modules as one module that does what loading them separately does. Each runs in its own function scope
    (so their top-level names never meet), in the order the browser evaluates them: depth first from the entry,
    each module's imports in the order written, a module after everything it imports. Its imports become a
    destructuring of the modules before it, and it returns what the others import from it. With no cycles (refused
    here) and no `export let`, a module's exports are final once it has run, so these copies are the same values
    the live bindings would give."""
    imports, bodies = {}, {}
    for name, src in sources.items():
        found = []
        for m in IMPORT_LINE.finditer(src):
            names = []
            for part in m.group(1).split(","):
                part = part.strip()
                if not part:
                    continue
                bits = part.split()
                if len(bits) == 1 and IDENT.match(bits[0]):
                    names.append((bits[0], bits[0]))
                elif len(bits) == 3 and bits[1] == "as" and IDENT.match(bits[0]) and IDENT.match(bits[2]):
                    names.append((bits[0], bits[2]))
                else:
                    raise NotBundled(f"{name}: import {{{m.group(1)}}}")
            found.append((m.group(2), names))
        body = EXPORT_DECL.sub("", IMPORT_LINE.sub("", src))
        left = LEFTOVER.search(body)
        if left:
            raise NotBundled(f"{name}: {body[left.start():].splitlines()[0][:60]}")
        if re.search(r"\$mod_", body):
            raise NotBundled(f"{name}: uses the joiner's own names ($mod_)")
        imports[name], bodies[name] = found, body
    order, state = [], {}

    def visit(n, path):
        if n not in sources:
            raise NotBundled(f"{' -> '.join(path)} imports {n}, which isn't one of the modules")
        if state.get(n) == "done":
            return
        if state.get(n) == "open":
            raise NotBundled("cycle: " + " -> ".join(path + [n]))
        state[n] = "open"
        for dep, _ in imports[n]:
            visit(dep, path + [n])
        state[n] = "done"
        order.append(n)
    visit(entry, [])
    wanted = {n: [] for n in order}  # what each module has to hand on
    for n in order:
        for dep, names in imports[n]:
            for orig, _ in names:
                if orig not in wanted[dep]:
                    wanted[dep].append(orig)
    var = lambda n: "$mod_" + re.sub(r"\W", "_", n[:-3])
    out = [f"// the live page's modules ({len(order)}) joined into one by web.bundle(); each is static/<name>"]
    for n in order:
        for orig in wanted[n]:  # a name another module imports must be one this module exports
            if not re.search(rf"^export\s+(?:async\s+)?(?:function\s+{re.escape(orig)}\b|const\b[^\n]*[\s,]{re.escape(orig)}\s*=)",
                             sources[n], re.M):
                raise NotBundled(f"{n} doesn't export {orig}")
        lines = [f"// ---- {n} ----", f"const {var(n)} = (() => {{"]
        for dep, names in imports[n]:
            spec = ", ".join(o if o == a else f"{o}: {a}" for o, a in names)
            lines.append(f"const {{ {spec} }} = {var(dep)};")
        lines.append(bodies[n].strip("\n"))
        lines.append(f"return {{ {', '.join(wanted[n])} }};" if wanted[n] else "return {};")
        lines.append("})();")
        out.append("\n".join(lines))
    return "\n".join(out) + "\n"


# What is written into the page is sent without its comments (about a tenth of it, gzipped). Only what can be
# found without a parser goes: in a script, lines that are nothing but a // comment, found by following strings,
# template literals (and their ${…}), regular expressions and comments from the start; in a stylesheet, every
# /* … */ outside a string. When something can't be followed for certain the file is sent with its comments.
REGEX_AFTER = set("(,=:[!&|?{};+-*%<>~^") | {""}
REGEX_WORDS = {"return", "typeof", "case", "do", "else", "in", "of", "new", "delete", "void", "throw", "yield", "await"}


def strip_js_comments(src: str) -> str:
    """src without its whole-line // comments, or src itself when its strings and regexes can't be followed."""
    try:
        drop, i, n = set(), 0, len(src)
        frames = [["code", 0]]  # template literals and the ${…} inside them
        prev, word = "", ""  # the last significant character in code, and the word it ended
        line_start = 0
        while i < n:
            c = src[i]
            top = frames[-1]
            if top[0] == "tpl":
                if c == "\\":
                    i += 2
                    continue
                if c == "`":
                    frames.pop()
                    prev, word = "`", ""
                elif c == "$" and src.startswith("${", i):
                    frames.append(["code", 1])
                    prev, word = "{", ""
                    i += 1
                i += 1
                continue
            if c == "\n":
                line_start = i + 1
                i += 1
                continue
            if c in " \t\r":
                i += 1
                continue
            if c == "/" and src.startswith("//", i):
                end = src.find("\n", i)
                end = n if end < 0 else end
                if not src[line_start:i].strip():
                    drop.add((line_start, end + 1))  # the line and its newline
                i = end
                continue
            if c == "/" and src.startswith("/*", i):
                end = src.find("*/", i + 2)
                if end < 0:
                    return src
                i = end + 2
                continue
            if c in "'\"":
                j = i + 1
                while src[j] != c:
                    if src[j] == "\n":
                        return src
                    j += 2 if src[j] == "\\" else 1
                i, prev, word = j + 1, c, ""
                continue
            if c == "`":
                frames.append(["tpl"])
                i += 1
                continue
            if c == "/" and (prev in REGEX_AFTER and not word or word in REGEX_WORDS):
                j, cls = i + 1, False
                while cls or src[j] != "/":
                    if src[j] == "\n":
                        return src
                    if src[j] == "\\":
                        j += 1
                    elif src[j] == "[":
                        cls = True
                    elif src[j] == "]":
                        cls = False
                    j += 1
                j += 1
                while j < n and (src[j].isalpha()):
                    j += 1  # flags
                i, prev, word = j, "/", ""
                continue
            if c == "{":
                top[1] += 1
            elif c == "}":
                top[1] -= 1
                if top[1] == 0 and len(frames) > 1:
                    frames.pop()  # the end of a ${…}: back in the template
                    i += 1
                    continue
            if c.isalnum() or c in "_$":
                j = i
                while j < n and (src[j].isalnum() or src[j] in "_$"):
                    j += 1
                prev, word, i = src[j - 1], src[i:j], j
                continue
            prev, word = c, ""
            i += 1
        if len(frames) != 1 or frames[0][1] != 0:
            return src
    except IndexError:  # a string or regex that never ends
        return src
    out, at = [], 0
    for a, b in sorted(drop):
        out.append(src[at:a])
        at = b
    return "".join(out) + src[at:]


def strip_css_comments(src: str) -> str:
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c in "'\"":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1])
            i = j + 1
        elif src.startswith("/*", i):
            end = src.find("*/", i + 2)
            if end < 0:
                return src
            i = end + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _text(name: str) -> str:
    return static_file(assets()[name])[0].decode("utf-8")


def inline_page(html: str) -> str:
    """live.html as one document: each stylesheet as a <style>, /i18n.js as a classic <script> where it was (so the
    language and direction are set before anything is drawn), and the modules joined into one module script at the
    end of the body (a module runs once the page is parsed wherever it sits; at the end the markup paints first)."""
    files = assets()
    sources = {n: _text(n) for n in files if n.endswith(".js") and files[n].parent == STATIC_DIR}

    def safe(text, tag):  # what would end the element early, or change how the parser reads it
        if re.search(rf"</{tag}|<!--", text, re.I):
            raise NotBundled(f"a file contains </{tag} or <!--")
        return text
    tag_css = re.compile(r'<link rel="stylesheet" href="/static/\{\{BUILD\}\}/([\w-]+\.css)">')
    tag_js = re.compile(r'<script src="/static/\{\{BUILD\}\}/([\w-]+\.js)"></script>')
    tag_pre = re.compile(r'[ \t]*<link rel="modulepreload" href="/static/\{\{BUILD\}\}/[\w-]+\.js"[^>]*>\n?')
    tag_mod = re.compile(rf'[ \t]*<script type="module" src="/static/\{{\{{BUILD\}}\}}/{re.escape(ENTRY)}"[^>]*></script>\n?')
    if not tag_mod.search(html) or "</body>" not in html:
        raise NotBundled("live.html: no module script or no </body>")
    html = re.sub(r"<!--.*?-->\n?", "", html, flags=re.S)  # the template's notes (there are no scripts in it yet)
    html = tag_css.sub(lambda m: f"<style>\n{safe(strip_css_comments(_text(m.group(1))), 'style')}</style>", html)
    html = tag_js.sub(lambda m: f"<script>\n{safe(strip_js_comments(_text(m.group(1))), 'script')}</script>", html)
    html = tag_mod.sub("", tag_pre.sub("", html))
    js = safe(bundle({n: strip_js_comments(src) for n, src in sources.items()}), "script")
    i = html.rindex("</body>")
    html = html[:i] + f'<script type="module">\n{js}</script>\n' + html[i:]
    if "{{BUILD}}" in html or re.search(r'(?:href|src)="/static/', html):
        raise NotBundled("live.html links a file that isn't written in")
    return html


def live_page(mode: str = None) -> tuple[bytes, bytes, str]:
    """The live page for the current build, kept in memory (and gzipped) per build: one document with everything
    written in (mode "inline", the default), or live.html with the build's asset addresses ("split")."""
    mode = mode or PAGE_MODE
    build, st = build_id(), PAGE.stat()
    key = (st.st_mtime_ns, st.st_size, build, mode)
    hit = _build["page"].get(key)
    if not hit:
        html = PAGE.read_text(encoding="utf-8")
        body = html.replace("{{BUILD}}", build)
        if mode == "inline":
            try:
                body = inline_page(html)
            except NotBundled as e:  # still a working page: one file per module
                print(f"live page: sent with one file per module ({e})")
        hit = _build["page"][key] = _entry(body.encode("utf-8"))
    return hit


def asset(build: str, name: str):
    """(file, content type) for /static/<build>/<name>, or None: not one of the assets, or not this build's."""
    p = assets().get(name)
    if p is None or build != build_id():
        return None
    return p, ASSET_TYPES[p.suffix]


def changes_last_hour(con) -> int:
    return con.execute("""SELECT COUNT(*) FROM prices x JOIN products p ON p.id = x.product_id
                          WHERE x.at > ? AND x.at > p.first_seen""", (int(time.time()) - 3600,)).fetchone()[0]


def recent_changes(con, limit: int = 30, after: int = None, upto: int = None) -> list[dict]:
    """The latest price moves, newest first. With `after` (a price rowid): only rows after it, up to `upto`."""
    where, args, order = "", [], "x.at DESC"
    if after is not None:
        where, args, order = " AND x.rowid > ? AND x.rowid <= ?", [after, upto], "x.rowid DESC"
    rows = con.execute(f"""
        SELECT x.rowid AS rowid, x.at, x.price, x.in_stock, p.id, p.store, p.name, p.image,
               (SELECT y.price FROM prices y WHERE y.product_id = x.product_id AND y.at < x.at
                ORDER BY y.at DESC LIMIT 1) AS before
        FROM prices x JOIN products p ON p.id = x.product_id
        WHERE x.at > p.first_seen{where} ORDER BY {order} LIMIT ?""", args + [limit])
    out = []
    for r in rows:
        r = dict(r)
        r["label"] = LABELS.get(r["store"], r["store"])
        out.append(r)
    return out


# /api/deals sends each item with only what the live page reads from a list item: its card (and the patcher that
# compares cards part by part), the ★ watchlist check and the 🧺 basket. Everything else (sku, category, first_seen,
# tracked_days, the sparkline, score and why, the sale rhythm, …) is in /api/product/ID, which the product view
# fetches anyway. `?full=1` gives the whole item, as before, for any other caller.
LIST_FIELDS = ("id", "store", "name", "image", "in_stock", "price", "original_price", "usual_price", "discount_pct",
               "kind", "saving", "unit_text", "flags", "deal_label", "promo_code", "promo_price", "hub_name",
               "matches", "cheapest", "verdict", "last_seen")
LIST_VOLATILE = frozenset({"last_seen"})  # follows the clock: never a reason for a new ETag (as in livedata.VOLATILE)


def list_item(d: dict) -> dict:
    """The list shape of a catalog item. Empty values (None) are left out: the page reads a missing field as empty.
    links: "web" always; "android" and "ios" only where they differ from it (the page falls back to "web");
    "browser" only the product view uses. "url" only when there is no web link (the page's last fallback)."""
    out = {k: d[k] for k in LIST_FIELDS if d.get(k) is not None}
    links = d.get("links") or {}
    web = links.get("web") or ""
    out["links"] = {"web": web, **{k: links[k] for k in ("android", "ios") if links.get(k) and links[k] != web}}
    if not web and d.get("url"):
        out["url"] = d["url"]
    return out


_list_digests: dict = {}  # an item's full digest -> the digest of its list shape (a function of it)


def list_digest(d: dict, full_digest: bytes) -> bytes:
    """8 bytes that change exactly when the list shape changes, leaving out what follows the clock (last_seen,
    the verdict's wording; its label and tone count), like livedata.item_digest does for the full item. Everything
    it covers is covered by the full digest too, so it's kept per full digest and computed once per change."""
    hit = _list_digests.get(full_digest)
    if hit is None:
        s = list_item(d)
        v = s.get("verdict") or {}
        stable = [(k, x) for k, x in s.items() if k not in LIST_VOLATILE and k != "verdict"]
        stable.append(("verdict", (v.get("label"), v.get("tone"))))
        hit = hashlib.blake2b(repr(stable).encode(), digest_size=8).digest()
        if len(_list_digests) > 400_000:  # a few catalogs' worth; then start again
            _list_digests.clear()
        _list_digests[full_digest] = hit
    return hit


def make_handler(db_path: str, requests_file: str = None, keeper: livedata.CatalogKeeper = None):
    """keeper: the catalog to serve (serve() passes the process's shared one); by default one of its own,
    whose builder thread starts with the first request that needs it (Handler.keeper, for tests)."""
    requests_file = requests_file or breadfast.DEFAULT_REQUESTS_FILE
    coupons_file = os.path.join(os.path.dirname(os.path.abspath(db_path)), "coupons.json")
    cache, lock = {}, threading.Lock()
    keeper = keeper or livedata.CatalogKeeper(db_path)
    version = keeper.version
    queries = OrderedDict()  # (catalog gen, filter set) -> (sorted matches, facets, facets digest)
    queries_lock = threading.Lock()
    pulse_box = {"at": float("-inf"), "value": None}
    pulse_lock = threading.Lock()

    vapid_box = {}

    def vapid():
        if "v" not in vapid_box:
            from . import push
            vapid_box["v"] = push.Vapid.load_or_create(os.path.join(os.path.dirname(os.path.abspath(db_path)), "vapid.json"))
        return vapid_box["v"]

    def push_api(path: str, body: dict):
        """subscribe {subscription, prefs} · unsubscribe {endpoint} · test {endpoint}"""
        from . import push
        sub = body.get("subscription") or {}
        endpoint = str(body.get("endpoint") or sub.get("endpoint") or "")
        host = (urlparse(endpoint).hostname or "").lower()
        if not endpoint.startswith("https://") or not any(host == h or host.endswith("." + h) for h in PUSH_HOSTS):
            return 422, {"error": "That isn't a browser push address."}
        con = db.connect(db_path)
        try:
            if path == "/api/push/subscribe":
                keys = sub.get("keys") or {}
                if not (keys.get("p256dh") and keys.get("auth")):
                    return 422, {"error": "The subscription has no keys."}
                prefs = body.get("prefs") or {}
                prefs = {"watch": [int(x) for x in prefs.get("watch") or []][:500],
                         "targets": {str(k): float(v) for k, v in (prefs.get("targets") or {}).items() if v},
                         "all_drops": bool(prefs.get("all_drops")), "min_drop": float(prefs.get("min_drop") or 0),
                         "stores": [str(x) for x in prefs.get("stores") or []][:10],
                         "hubs": [int(x) for x in prefs.get("hubs") or []][:20],
                         "categories": [str(x) for x in prefs.get("categories") or []][:50],
                         "words": [str(x)[:40] for x in prefs.get("words") or []][:20]}
                if con.execute("SELECT COUNT(*) FROM push_subs").fetchone()[0] > 20000:
                    return 503, {"error": "Too many subscriptions."}
                with con:
                    con.execute("""INSERT INTO push_subs (endpoint, subscription, prefs, created) VALUES (?,?,?,?)
                                   ON CONFLICT(endpoint) DO UPDATE SET subscription=excluded.subscription,
                                   prefs=excluded.prefs""",
                                (endpoint, json.dumps({"endpoint": endpoint, "keys": {"p256dh": keys["p256dh"],
                                                                                      "auth": keys["auth"]}}),
                                 json.dumps(prefs), int(time.time())))
                return 200, {"ok": True, "prefs": prefs}
            row = con.execute("SELECT * FROM push_subs WHERE endpoint=?", (endpoint,)).fetchone()
            if path == "/api/push/unsubscribe":
                with con:
                    con.execute("DELETE FROM push_subs WHERE endpoint=?", (endpoint,))
                return 200, {"ok": True}
            if not row:
                return 404, {"error": "Not subscribed."}
            if time.time() - (row["last_sent"] or 0) < 30:
                return 429, {"error": "One test every 30 seconds."}
            status = push.send(vapid(), json.loads(row["subscription"]),
                               {"title": "Live deals: alerts are on", "body": "This is how a price drop will look.",
                                "url": "/", "tag": "deals-test"})
            with con:
                con.execute("UPDATE push_subs SET last_sent=? WHERE endpoint=?", (int(time.time()), endpoint))
            return (200, {"ok": True}) if 200 <= status < 300 else (502, {"error": f"The push service answered {status}."})
        finally:
            con.close()

    def catalog(con=None) -> list[dict]:
        return keeper.get().items

    def matches_for(snap, fargs: dict):
        """Every match of a filter set, sorted, with its facets: kept per catalog copy, so paging and the
        page's refresh only slice it."""
        key = (snap.gen, json.dumps(fargs, sort_keys=True, default=sorted))
        with queries_lock:
            hit = queries.get(key)
            if hit:
                queries.move_to_end(key)
                return hit
        hits, facets = finder.select(snap.items, **fargs)
        entry = (hits, facets, hashlib.blake2b(json.dumps(facets, sort_keys=True).encode(), digest_size=8).digest())
        with queries_lock:
            for k in [k for k in queries if k[0] != snap.gen]:  # an older copy's results
                del queries[k]
            queries[key] = entry
            while len(queries) > QUERY_CACHE:
                queries.popitem(last=False)
        return entry

    def pulse(con) -> dict:
        """{v, status, changes_last_hour, catalog}: what changes from second to second, read at most once a second."""
        keeper.touch()  # a page polling only the pulse still needs fresh catalog copies to be told about
        now = time.monotonic()
        if now - pulse_box["at"] < PULSE_SECONDS:
            return pulse_box["value"]
        with pulse_lock:
            if time.monotonic() - pulse_box["at"] < PULSE_SECONDS:
                return pulse_box["value"]
            snap = keeper.snap
            keeper.start()  # builds the first catalog in the background if nobody asked for one yet
            value = {"v": version.get(con), "status": status(con), "changes_last_hour": changes_last_hour(con),
                     "catalog": {"v": snap.v, "built": snap.built} if snap else None}
            value["etag"] = etag_of("pulse", pulse_etag_view(value))
            pulse_box.update(at=time.monotonic(), value=value)
            return value

    def cached(key: str, build):
        now = time.monotonic()
        with lock:
            hit = cache.get(key)
            if hit and now - hit[0] < CACHE_SECONDS:
                return hit[1]
        value = build()
        with lock:
            if len(cache) > 500:
                cache.clear()
            cache[key] = (now, value)
        return value

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # keep-alive: every answer carries its Content-Length
        timeout = KEEP_ALIVE
        disable_nagle_algorithm = True  # headers and body go out as two writes: don't wait 40 ms between them

        def log_message(self, fmt, *args):
            pass

        def send(self, code: int, body: bytes, ctype: str, cache: str = "no-store", etag: str = None,
                 gzipped: bytes = None):
            """One answer. gzip when the client takes it and it's worth it; 304 when the client's copy (its
            If-None-Match) is still the right one. `gzipped`: the body already compressed (static files)."""
            compressible = ctype.startswith(COMPRESSIBLE)
            encoded = False
            if etag and code == 200 and etag_matches(self.headers.get("If-None-Match"), etag):
                code, body = 304, b""
            elif compressible and len(body) >= GZIP_MIN and accepts_gzip(self.headers.get("Accept-Encoding")):
                body, encoded = (gzipped if gzipped is not None else gzip.compress(body, 5, mtime=0)), True
            self.send_response(code)
            if code != 304:
                self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            if etag:
                self.send_header("ETag", etag)
            if compressible:
                self.send_header("Vary", "Accept-Encoding")
            if encoded:
                self.send_header("Content-Encoding", "gzip")
            if ctype == "text/javascript" and self.path == "/sw.js":
                self.send_header("Service-Worker-Allowed", "/")
            self.send_header("X-Content-Type-Options", "nosniff")
            if self.command == "POST":  # a POST's body may not have been read: don't read past it as a request
                self.close_connection = True
                self.send_header("Connection", "close")
            self.end_headers()
            if body and self.command != "HEAD":
                self.wfile.write(body)

        def json(self, obj, code: int = 200, etag: str = None):
            self.send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8", etag=etag)

        def static(self, path: Path, ctype: str, cache: str = "no-cache"):
            """A file from the package, kept in memory. no-cache (may be kept, but revalidated every time) when its
            address stays the same across a deploy; a build's /static/<build>/ address is cached for good."""
            body, gz, etag = static_file(path)
            self.send(200, body, ctype, cache=cache, etag=etag, gzipped=gz)

        def unchanged(self, etag: str) -> bool:
            """Answers 304 (and returns True) when the client already has this version of a JSON answer."""
            if etag_matches(self.headers.get("If-None-Match"), etag):
                self.send(200, b"", "application/json; charset=utf-8", etag=etag)
                return True
            return False

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            con = None
            try:
                con = sqlite3.connect(db_path, timeout=10)
                con.row_factory = sqlite3.Row
                if u.path in ("/", "/live"):
                    body, gz, etag = live_page()
                    self.send(200, body, "text/html; charset=utf-8", cache="no-cache", etag=etag, gzipped=gz)
                elif u.path.startswith("/static/"):
                    m = re.fullmatch(r"/static/([0-9a-f]{1,32})/([\w-]+\.(?:js|css))", u.path)
                    found = m and asset(m.group(1), m.group(2))
                    if found:  # its address changes with any change to any file: never asked again
                        self.static(*found, cache=IMMUTABLE)
                    else:  # another build's (or no such file): the page that asked reloads once
                        self.send(404, b"not in this build", "text/plain; charset=utf-8")
                elif u.path in ("/admin", "/admin/"):
                    self.static(ADMIN_PAGE, "text/html; charset=utf-8")
                elif u.path == "/healthz":
                    con.execute("SELECT 1").fetchone()
                    self.send(200, b"ok", "text/plain")
                elif u.path == "/api/deals":
                    def nums(key):
                        return [int(x) for x in q[key].split(",") if x.strip().isdigit()] if q.get(key) else None

                    def num(key):
                        return float(q[key]) if q.get(key) not in (None, "") else None

                    args = dict(deals_only=q.get("deals", "1") != "0",
                                stores=[x for x in q.get("stores", q.get("store", "")).split(",") if x] or None,
                                kind=q.get("kind") or None, min_pct=num("min") or 0, max_pct=num("max"),
                                stock=q.get("stock", "in"), status=[x for x in q.get("status", "").split(",") if x],
                                category=q.get("category") or None, q=q.get("q") or None,
                                price_min=num("pmin"), price_max=num("pmax"), ids=nums("ids"),
                                best=q.get("best") == "1", sort=q.get("sort", "score"), hubs=nums("hubs"),
                                limit=min(int(q.get("limit", 60)), 500), offset=int(q.get("offset", 0)))
                    if args["ids"] is not None:
                        args["ids"] = set(args["ids"])
                    limit, offset = args.pop("limit"), args.pop("offset")
                    snap = keeper.get()
                    hits, facets, facets_digest = matches_for(snap, args)
                    page = hits[offset:offset + limit]
                    # the ETag covers what the items carry (only the list shape's fields unless ?full=1); status,
                    # changes_last_hour, now and v are left out
                    full = q.get("full") == "1"
                    digests = (snap.digests[d["id"]] for d in page) if full else \
                        (list_digest(d, snap.digests[d["id"]]) for d in page)
                    etag = etag_of("deals-full" if full else "deals", limit, offset, len(hits), facets_digest,
                                   b"".join(digests))
                    if self.unchanged(etag):
                        return
                    p = pulse(con)
                    self.json({"items": page if full else [list_item(d) for d in page], "total": len(hits),
                               "facets": facets, "labels": LABELS,
                               "status": p["status"], "changes_last_hour": p["changes_last_hour"],
                               "now": int(time.time()), "v": snap.v}, etag=etag)
                elif u.path == "/api/pulse":
                    p = pulse(con)
                    if not self.unchanged(p["etag"]):
                        self.json({"v": p["v"], "now": int(time.time()), "status": p["status"],
                                   "changes_last_hour": p["changes_last_hour"], "catalog": p["catalog"]}, etag=p["etag"])
                elif u.path == "/api/seen":
                    ids = list(dict.fromkeys(int(x) for x in q.get("ids", "").split(",") if x.strip().isdigit()))[:SEEN_MAX]
                    rows = con.execute(f"SELECT id, last_seen FROM products WHERE id IN ({','.join('?' * len(ids))})",
                                       ids).fetchall() if ids else []
                    self.json({"seen": {str(r["id"]): r["last_seen"] for r in rows}, "now": int(time.time())})
                elif u.path == "/api/brief":
                    snap = keeper.get()
                    b = cached(f"brief{snap.gen}", lambda: (lambda r: (r, etag_of("brief", r)))(finder.brief(snap.items)))
                    if not self.unchanged(b[1]):
                        self.json(b[0], etag=b[1])
                elif u.path == "/api/changes":
                    limit = min(int(q.get("limit", 30)), 100)
                    after = int(q["after"]) if q.get("after", "").strip() else None
                    v = version.get(con)

                    def build_changes():
                        upto = db.data_version(con)
                        reset = after is not None and after > upto  # the client's cursor is from another numbering
                        r = {"changes": recent_changes(con, limit, None if reset or after is None else after, upto),
                             "v": upto}
                        if reset:
                            r["reset"] = True
                        return r, etag_of("changes", r)
                    r, etag = cached(f"changes{limit}:{after}:{v}", build_changes)
                    if not self.unchanged(etag):
                        self.json(r, etag=etag)
                elif u.path.startswith("/api/product/"):
                    pid = int(u.path.rsplit("/", 1)[1])
                    p = con.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
                    if not p:
                        return self.json({"error": "not found"}, 404)
                    snap = keeper.get()
                    item = snap.by_id.get(pid, {})
                    history = db.history(con, pid)
                    found = [m["id"] for m in item.get("matches", []) if m["id"] in snap.by_id]
                    etag = etag_of("product", {k: v for k, v in dict(p).items() if k != "last_seen"}, history,
                                   b"".join(snap.digests[i] for i in [pid] * bool(item) + found))
                    if self.unchanged(etag):
                        return
                    matches = [dict(snap.by_id[i], spark=None) for i in found]
                    self.json({"product": dict(p), "item": item, "history": history, "matches": matches}, etag=etag)
                elif u.path == "/api/talabat/hubs":
                    chosen = settings.load(settings.path_for(db_path))["talabat_hubs"]
                    hubs = known_hubs(db_path)
                    for h in chosen:
                        hubs.setdefault(h["id"], {**h, "areas": []})
                    rows = []
                    for h in hubs.values():
                        cities = sorted({a["city"] for a in h["areas"]})
                        area_names = [a["name"] for a in h["areas"]]
                        label, words = talabat.district(h["name"], cities)  # the hub's own name decides
                        if not words:  # a name that says nothing: go by the areas it serves
                            label, words = talabat.district(" ".join(area_names[:5]), cities)
                        rows.append({"id": h["id"], "name": h["name"], "aid": h["aid"], "areas": area_names[:40],
                                     "cities": cities, "district": label, "words": words})
                    order = [d[0] for d in talabat.DISTRICTS]
                    rows.sort(key=lambda h: (order.index(h["district"]) if h["district"] in order else 99, h["name"].lower()))
                    self.json({"selected": [h["id"] for h in chosen], "districts": order, "hubs": rows})
                elif u.path in STATIC:
                    name, ctype = STATIC[u.path]
                    self.static(Path(__file__).parent / name, ctype)
                elif u.path == "/api/push/key":
                    self.json({"key": vapid().public_b64})
                elif u.path == "/manifest.webmanifest":
                    self.send(200, MANIFEST, "application/manifest+json", cache="no-cache", etag=etag_of(MANIFEST))
                elif u.path == "/icon.svg":
                    self.send(200, ICON, "image/svg+xml", cache="no-cache", etag=etag_of(ICON))
                elif re.fullmatch(r"/p/\d+", u.path):
                    pid = int(u.path[3:])
                    p = con.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
                    if not p:
                        return self.send(404, pages.layout("Not found", '<div class="empty">This product isn\'t'
                                                           ' tracked (any more).</div>'), "text/html; charset=utf-8")
                    snap = keeper.get()
                    by_id = snap.by_id
                    item = by_id.get(pid, {})
                    matches = [by_id[m["id"]] for m in item.get("matches", []) if m["id"] in by_id]
                    related = []
                    if item:
                        r = finder.query(snap.items, deals_only=True, category=item["top_category"],
                                         stores=[item["store"]], sort="score", limit=13)
                        related = [x for x in r["items"] if x["id"] != pid][:12]
                    self.send(200, pages.product_page(dict(p), item, db.history(con, pid), matches, related, PUBLIC_URL),
                              "text/html; charset=utf-8")
                elif u.path == "/stores":
                    def build_stores():
                        items = catalog(con)
                        deals = finder.query(items, deals_only=True, limit=0)["facets"]["store"]
                        best = {}
                        for d in items:
                            if d["in_stock"] and d["discount_pct"] and not {"steady", "inflated", "rose"} & set(d["flags"]):
                                best[d["store"]] = max(best.get(d["store"], 0), d["discount_pct"])
                        return pages.stores_page(status(con), {}, deals, best)
                    self.send(200, cached(f"stores{keeper.get().gen}", build_stores), "text/html; charset=utf-8")
                elif re.fullmatch(r"/store/[a-z_]+", u.path):
                    self.send_response(302)
                    self.send_header("Location", "/?deals=1&stores=" + u.path.rsplit("/", 1)[1])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                elif u.path == "/cats":
                    def build_cats():
                        items = catalog(con)
                        r = finder.query(items, deals_only=True, limit=0)["facets"]["category"]
                        top = {}
                        for d in items:
                            if d["in_stock"] and d["discount_pct"] and not {"steady", "inflated", "rose"} & set(d["flags"]):
                                top[d["top_category"]] = max(top.get(d["top_category"], 0), d["discount_pct"])
                        rows = sorted(((c, n, top.get(c, 0)) for c, n in r.items() if n), key=lambda x: -x[1])
                        return pages.cats_page(rows)
                    self.send(200, cached(f"cats{keeper.get().gen}", build_cats), "text/html; charset=utf-8")
                elif u.path == "/coupons":
                    def build_coupons():
                        found = {}  # codes the stores show on their own products (Kenzz), with how many they cut
                        for d in catalog(con):
                            if d.get("promo_code") and d["in_stock"]:
                                c = found.setdefault((d["store"], d["promo_code"]), {"n": 0, "best": 0.0})
                                c["n"] += 1
                                c["best"] = max(c["best"], d["price"] - (d["promo_price"] or d["price"]))
                        auto = [{"store": st, "code": code, "auto": True, "url": f"{PUBLIC_URL}/?deals=0&status=promo&stores={st}",
                                 "title": f"Extra off on {v['n']} products, up to {v['best']:,.0f} EGP"}
                                for (st, code), v in sorted(found.items(), key=lambda kv: -kv[1]["n"])[:60]]
                        return pages.coupons_page(pages.load_coupons(coupons_file) + auto)
                    self.send(200, cached(f"coupons{keeper.get().gen}", build_coupons), "text/html; charset=utf-8")
                elif u.path == "/about":
                    self.send(200, pages.about_page([pages.AR_STORE.get(k, v) for k, v in LABELS.items()
                                                     if k in {r["store"] for r in status(con)}]),
                              "text/html; charset=utf-8")
                elif u.path == "/privacy":
                    self.send(200, pages.privacy_page(), "text/html; charset=utf-8")
                elif u.path == "/contact":
                    self.send(200, pages.contact_page(os.environ.get("CONTACT_EMAIL", "").strip(),
                                                      os.environ.get("TELEGRAM_CHANNEL_URL", "").strip()),
                              "text/html; charset=utf-8")
                elif u.path == "/api/coupons":
                    self.json({"coupons": pages.load_coupons(coupons_file)})
                elif u.path == "/api/status":
                    p = pulse(con)
                    etag = etag_of("status", p["status"], p["changes_last_hour"])
                    if not self.unchanged(etag):
                        self.json({"status": p["status"], "changes_last_hour": p["changes_last_hour"]}, etag=etag)
                else:
                    self.json({"error": "not found"}, 404)
            except (ValueError, KeyError) as e:
                self.json({"error": str(e)}, 400)
            except sqlite3.Error as e:
                self.json({"error": f"database: {e}"}, 503)
            finally:
                if con:
                    con.close()

        def do_POST(self):
            u = urlparse(self.path)
            if u.path in ("/api/push/subscribe", "/api/push/unsubscribe", "/api/push/test"):
                try:
                    size = int(self.headers.get("Content-Length") or 0)
                    if not 0 < size <= 1 << 16:
                        return self.json({"error": "Send JSON."}, 413)
                    code, out = push_api(u.path, json.loads(self.rfile.read(size)))
                except (ValueError, TypeError, AttributeError):
                    code, out = 400, {"error": "Send JSON."}
                return self.json(out, code)
            if u.path not in ("/admin/breadfast", "/admin/breadfast-request", "/admin/settings", "/admin/coupons"):
                return self.json({"error": "not found"}, 404)
            token = os.environ.get("DEALS_ADMIN_TOKEN", "")
            if len(token) < 16:
                return self.json({"error": "Uploads are off. Set DEALS_ADMIN_TOKEN (16+ characters) in Coolify's"
                                           " environment variables for this app and redeploy."}, 403)
            given = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
            if not hmac.compare_digest(given.encode(), token.encode()):
                time.sleep(1.5)  # slows down guessing
                return self.json({"error": "Wrong admin token."}, 401)
            try:
                size = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                size = 0
            if u.path == "/admin/settings":
                if not 0 < size <= 1 << 16:
                    return self.json({"error": "Send the settings as JSON."}, 413)
                try:
                    body = json.loads(self.rfile.read(size))
                    hubs = known_hubs(db_path)
                    path = settings.path_for(db_path)
                    current = settings.load(path)
                    chosen = {h["id"]: h for h in current["talabat_hubs"]}
                    if "talabat_hubs" in body:
                        ids = [int(x) for x in body["talabat_hubs"]]
                        if not ids:
                            return self.json({"error": "Choose at least one hub."}, 422)
                        unknown = [i for i in ids if i not in hubs and i not in chosen]
                        if unknown:
                            return self.json({"error": f"Unknown hub(s): {unknown}. Add a hub by its talabat link."}, 422)
                        chosen = {i: chosen.get(i) or {"id": i, "aid": hubs[i]["aid"], "name": hubs[i]["name"]} for i in ids}
                    if body.get("hub_link"):
                        m = re.search(r"talabat\.com/egypt/grocery/(\d+)/", body["hub_link"])
                        if not m:
                            return self.json({"error": "That isn't a talabat mart link (…talabat.com/egypt/grocery/<hub>/…)."}, 422)
                        hid = int(m.group(1))
                        aid = re.search(r"[?&]aid=(\d+)", body["hub_link"])
                        known = hubs.get(hid, {})
                        chosen[hid] = {"id": hid, "aid": int(aid.group(1)) if aid else known.get("aid", 9838),
                                       "name": known.get("name") or str(hid)}
                    if len(chosen) > 10:
                        return self.json({"error": "Up to 10 hubs (each one adds ~15 minutes to a talabat sweep)."}, 422)
                    current["talabat_hubs"] = list(chosen.values())
                    settings.save(path, current)
                except (ValueError, TypeError, KeyError):
                    return self.json({"error": "Send {\"talabat_hubs\": [ids]} or {\"hub_link\": \"…\"}."}, 400)
                return self.json({"selected": [h["id"] for h in current["talabat_hubs"]],
                                  "hubs": current["talabat_hubs"]})
            if u.path == "/admin/coupons":
                if not 0 < size <= 1 << 18:
                    return self.json({"error": "Send {\"text\": \"store | CODE | what it gives | until | link\"}."}, 413)
                try:
                    coupons = pages.parse_coupons(str(json.loads(self.rfile.read(size)).get("text") or ""))
                except (ValueError, AttributeError):
                    return self.json({"error": "Send {\"text\": \"...\"}."}, 400)
                tmp = coupons_file + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(coupons, f, ensure_ascii=False, indent=1)
                os.replace(tmp, coupons_file)
                return self.json({"coupons": coupons})
            if u.path == "/admin/breadfast-request":
                if not 0 < size <= 1 << 20:
                    return self.json({"error": "Paste one request (up to 1 MB)."}, 413)
                try:
                    body = json.loads(self.rfile.read(size))
                    divisor = 100.0 if str(body.get("divisor")) == "100" else 1.0
                    with _import_lock:
                        r = breadfast.import_request(db_path, str(body.get("curl") or ""), requests_file, divisor)
                except breadfast.BadRequest as e:
                    return self.json({"error": str(e)[:1].upper() + str(e)[1:] + "."}, 422)
                except (ValueError, AttributeError):
                    return self.json({"error": "Send {\"curl\": \"...\"}."}, 400)
                except RuntimeError as e:
                    return self.json({"error": f"Couldn't reach Breadfast: {e}"}, 502)
                return self.json(r)
            if not 0 < size <= MAX_UPLOAD:
                return self.json({"error": f"Send the .har file (up to {MAX_UPLOAD // 2**20} MB)."}, 413)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            divisor = 100.0 if q.get("divisor") == "100" else 1.0
            if not _import_lock.acquire(blocking=False):
                return self.json({"error": "Another import is running; try again in a minute."}, 409)
            fd, tmp = tempfile.mkstemp(prefix=".capture-", suffix=".har", dir=os.path.dirname(os.path.abspath(db_path)))
            try:
                with os.fdopen(fd, "wb") as f:
                    left = size
                    while left:
                        chunk = self.rfile.read(min(left, 1 << 20))
                        if not chunk:
                            break
                        f.write(chunk)
                        left -= len(chunk)
                try:
                    r = breadfast.import_capture(db_path, tmp, requests_file, divisor)
                except (ValueError, KeyError, UnicodeDecodeError) as e:
                    return self.json({"error": f"That file isn't a HAR export ({type(e).__name__})."}, 400)
                if not r["products"]:
                    return self.json({"error": "No products in that capture. Browse a few Breadfast categories while"
                                               " recording, then export again."}, 422)
                self.json(r)
            finally:
                os.unlink(tmp)  # the capture holds the app session: never kept
                _import_lock.release()

    Handler.keeper = keeper
    return Handler


def serve(db_path: str, host: str = "127.0.0.1", port: int = 8800, requests_file: str = None):
    db.connect(db_path).close()  # create the tables
    keeper = livedata.shared(db_path)  # the same catalog as the Telegram bot and poster, built off the requests
    keeper.start()
    server = ThreadingHTTPServer((host, port), make_handler(db_path, requests_file, keeper))
    server.daemon_threads = True
    print(f"live deals on http://{host}:{port}/")
    server.serve_forever()
