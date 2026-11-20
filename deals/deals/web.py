"""The live deals page and its JSON API, on Python's built-in HTTP server (HTTP/1.1, kept-alive connections).

  /                      the live page
  /api/pulse             tiny, from memory: {v, now, changes_last_hour, status, brief counts}. `v` changes when the
                         catalogue does; a page that saw the same v has nothing to fetch
  /api/deals             filters: deals=1|0 stores=a,b kind=platform|drop min= max= stock=in|out|all
                         status=new,lowest,real,label,back,compare category= q= pmin= pmax= ids=1,2 best=1
                         sort=score|discount|saving|newest|drop|price|price_desc|unit|popular|name limit= offset=
                         fields=card (only what a card shows) since_v=<v> (304 when nothing changed)
  /api/changes           the latest price moves (?limit=)
  /api/product/ID        one product: history, links, matches in other stores, score and why
  /api/status            each store: products, last check, last change, cycle time, errors
  /manifest.webmanifest, /icon.svg   so the page installs as an app
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

Every answer comes from the shared catalogue (catalog.py), which a background thread keeps up to date: no request
builds it. Answers made from one snapshot are kept with it (encoded once). Caching: GET answers carry a weak ETag
and `Cache-Control: no-cache` (store, but ask again; a 304 when nothing changed), icons `public, max-age=86400`;
only /admin and POST answers are `no-store`.
"""

import hashlib
import hmac
import json
import os
import queue
import re
import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import catalog as catalog_mod
from . import db, finder, pages, settings
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
STARTUP_WAIT = float(os.environ.get("DEALS_STARTUP_WAIT", 30))  # a request during the very first build waits this long
KEEPALIVE = 120  # seconds an idle kept-alive connection stays open (longer than Traefik's 90 s idle pool)
JSON = "application/json; charset=utf-8"
HTML = "text/html; charset=utf-8"
CARD_FIELDS = ("id", "store", "name", "image", "price", "original_price", "usual_price", "discount_pct", "kind", "saving",
               "flags", "deal_label", "score", "since", "prev_price", "recent_low", "in_stock", "unit_text", "hub",
               "hub_name", "spark", "links", "verdict", "matches", "cheapest", "promo_code", "promo_price", "rhythm",
               "top_category")
MANIFEST = json.dumps({
    "name": "عروض لحظة بلحظة", "short_name": "العروض", "lang": "ar", "dir": "rtl", "start_url": "/", "scope": "/", "display": "standalone",
    "background_color": "#f6f5f2", "theme_color": "#d6402b", "description": "Live prices and real deals",
    "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
              {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
              {"src": "/icon-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
}).encode()
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://deals.vorath.cloud").rstrip("/")
STATIC = {"/sw.js": ("sw.js", "text/javascript"), "/i18n.js": ("i18n.js", "text/javascript"), "/icon-192.png": ("icon-192.png", "image/png"),
          "/icon-512.png": ("icon-512.png", "image/png"), "/icon-maskable.png": ("icon-maskable.png", "image/png")}
# the browser push services; the server sends pushes nowhere else
PUSH_HOSTS = ("fcm.googleapis.com", "push.services.mozilla.com", "push.apple.com", "notify.windows.com")
ICON = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><rect width="512" height="512" rx="112" '
        b'fill="#d6402b"/><path d="M150 330 L250 230 L300 280 L390 180" stroke="#fff" stroke-width="44" fill="none" '
        b'stroke-linecap="round" stroke-linejoin="round"/><circle cx="390" cy="180" r="34" fill="#fff"/></svg>')


# status rows, the last hour's count and the latest moves: computed by the catalogue at each refresh
status = catalog_mod.status
changes_last_hour = catalog_mod.changes_last_hour
recent_changes = catalog_mod.recent_changes


def dumps(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()


def etag_of(body: bytes) -> str:
    return 'W/"' + hashlib.sha1(body).hexdigest()[:16] + '"'


def card(d: dict, now: int) -> dict:
    """What one card on the page shows (fields=card): CARD_FIELDS, a 12-point spark, links without "browser"."""
    sp = d["spark"] + [[now, d["spark"][-1][1]]]
    if len(sp) > 12:
        step = len(sp) / 12
        sp = [sp[int(i * step)] for i in range(11)] + [sp[-1]]
    out = {k: d[k] for k in CARD_FIELDS}
    l, v, r = d["links"], d["verdict"], d["rhythm"]
    out["spark"] = sp
    out["links"] = {"web": l.get("web"), "android": l.get("android"), "ios": l.get("ios")}
    out["verdict"] = {"label": v["label"], "tone": v["tone"], "why": v["why"]}
    out["matches"] = [{"id": m["id"], "store": m["store"], "price": m["price"], "in_stock": m["in_stock"]}
                      for m in d["matches"]]
    out["rhythm"] = {"next_in_days": r["next_in_days"], "on_sale": r["on_sale"], "text": r["text"]} if r else None
    return out


class Pool:
    """A few sqlite connections, reused across requests (each is used by one request at a time)."""

    def __init__(self, path: str, size: int = 8):
        self.path, self.size, self.q = path, size, queue.LifoQueue()

    @contextmanager
    def connection(self):
        try:
            con = self.q.get_nowait()
        except queue.Empty:
            con = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
            con.row_factory = sqlite3.Row
        ok = False
        try:
            yield con
            ok = True
        finally:
            if ok and not con.in_transaction and self.q.qsize() < self.size:
                self.q.put(con)
            else:
                con.close()


class _File:
    """A file next to this module, read again only when it changes on disk."""

    def __init__(self, path: Path):
        self.path, self.stamp, self.body, self.etag = path, None, b"", ""

    def get(self) -> tuple[bytes, str]:
        st = os.stat(self.path)
        stamp = (st.st_mtime_ns, st.st_size)
        if stamp != self.stamp:
            body = self.path.read_bytes()
            self.body, self.etag, self.stamp = body, etag_of(body), stamp
        return self.body, self.etag


def make_handler(db_path: str, requests_file: str = None, catalog: "catalog_mod.Catalog" = None):
    """The request handler class. `catalog`: the shared live catalogue (one is made, and started on first use, when
    none is given); the class keeps it as Handler.catalog."""
    requests_file = requests_file or breadfast.DEFAULT_REQUESTS_FILE
    coupons_file = os.path.join(os.path.dirname(os.path.abspath(db_path)), "coupons.json")
    cat = catalog or catalog_mod.Catalog(db_path)
    pool = Pool(db_path)
    files = {name: _File(Path(__file__).with_name(name)) for name in
             ["live.html", "admin.html"] + [v[0] for v in STATIC.values()]}
    MANIFEST_TAG, ICON_TAG = etag_of(MANIFEST), etag_of(ICON)
    started = []

    def snapshot():
        if not started:
            cat.start()
            started.append(True)
        return cat.snapshot(wait=STARTUP_WAIT)

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

    def deals_args(q: dict) -> dict:
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
        return args

    def deals_body(snap, args: dict, cards: bool) -> bytes:
        r = snap.query(**args)
        meta = cat.meta()
        now = snap.now
        r["items"] = [card(d, now) if cards else snap.full(d) for d in r["items"]]
        r.update(labels=LABELS, status=meta.status, changes_last_hour=meta.changes_last_hour, now=now, v=snap.v)
        return dumps(r)

    def pulse_tail(snap, meta) -> bytes:
        b = snap.brief
        return dumps({"changes_last_hour": meta.changes_last_hour, "status": meta.status,
                      "brief": {k: b[k] for k in ("deals", "dropped_24h", "record_lows", "fake", "soon", "promo",
                                                  "biggest_saving")}})[1:]

    def product_body(snap, pid: int):
        with pool.connection() as con:
            p = con.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
            if not p:
                return None
            history = db.history(con, pid)
        by_id = snap.by_id
        it = by_id.get(pid)
        item = snap.full(it) if it else {}
        matches = [dict(by_id[m["id"]], spark=None) for m in item.get("matches", []) if m["id"] in by_id]
        return dumps({"product": dict(p), "item": item, "history": history, "matches": matches})

    def product_page(snap, pid: int):
        with pool.connection() as con:
            p = con.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
            if not p:
                return None
            history = db.history(con, pid)
        by_id = snap.by_id
        it = by_id.get(pid)
        item = snap.full(it) if it else {}
        matches = [snap.full(by_id[m["id"]]) for m in item.get("matches", []) if m["id"] in by_id]
        related = []
        if item:
            r = snap.query(deals_only=True, category=item["top_category"], stores=[item["store"]], sort="score",
                           limit=13)
            related = [snap.full(x) for x in r["items"] if x["id"] != pid][:12]
        return pages.product_page(dict(p), item, history, matches, related, PUBLIC_URL, now=snap.now)

    def stores_page(snap):
        deals = snap.query(deals_only=True, limit=0)["facets"]["store"]
        return pages.stores_page(cat.meta().status, {}, deals, snap.index.best_by(snap.by_id, "store"))

    def cats_page(snap):
        r = snap.query(deals_only=True, limit=0)["facets"]["category"]
        top = snap.index.best_by(snap.by_id, "cat")
        return pages.cats_page(sorted(((c, n, top.get(c, 0)) for c, n in r.items() if n), key=lambda x: -x[1]))

    def coupons_page(snap):
        found = {}  # codes the stores show on their own products (Kenzz), with how many they cut
        ix = snap.index
        for s in catalog_mod.slots_of(ix.masks.get("promo", 0) & ix.masks.get("in", 0)):
            d = snap.by_id[ix.ids[s]]
            c = found.setdefault((d["store"], d["promo_code"]), {"n": 0, "best": 0.0})
            c["n"] += 1
            c["best"] = max(c["best"], d["price"] - (d["promo_price"] or d["price"]))
        auto = [{"store": st, "code": code, "auto": True, "url": f"{PUBLIC_URL}/?deals=0&status=promo&stores={st}",
                 "title": f"Extra off on {v['n']} products, up to {v['best']:,.0f} EGP"}
                for (st, code), v in sorted(found.items(), key=lambda kv: -kv[1]["n"])[:60]]
        return pages.coupons_page(pages.load_coupons(coupons_file) + auto)

    def talabat_hubs():
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
        return {"selected": [h["id"] for h in chosen], "districts": order, "hubs": rows}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = KEEPALIVE
        disable_nagle_algorithm = True
        server_version = "deals"
        catalog = cat

        def log_message(self, fmt, *args):
            pass

        def fresh(self, etag: str) -> bool:
            """The client already has this version (If-None-Match)."""
            given = self.headers.get("If-None-Match")
            if not given or not etag:
                return False
            if given.strip() == "*":
                return True
            mine = etag.removeprefix("W/")
            return any(t.strip().removeprefix("W/") == mine for t in given.split(","))

        def not_modified(self, etag: str, cache: str = "no-cache"):
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", cache)
            self.end_headers()

        def send(self, code: int, body: bytes, ctype: str, cache: str = "no-cache", etag: str = None,
                 headers: dict = None):
            if etag and code == 200 and self.fresh(etag):
                return self.not_modified(etag, cache)
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            if etag:
                self.send_header("ETag", etag)
            if ctype == "text/javascript":
                self.send_header("Service-Worker-Allowed", "/")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self._headers_buffer.append(b"\r\n")
            if self.command != "HEAD":
                self._headers_buffer.append(body)  # one write for the headers and the body
            self.flush_headers()

        def json(self, obj, code: int = 200, cache: str = "no-cache", etag: bool = False):
            body = dumps(obj)
            self.send(code, body, JSON, cache, etag_of(body) if etag else None)

        def starting(self):
            self.send(503, dumps({"error": "starting", "detail": "The catalogue is being built; try again in a few"
                                                                  " seconds."}), JSON, headers={"Retry-After": "5"})

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            path = u.path
            try:
                if path in ("/", "/live"):
                    body, tag = files["live.html"].get()
                    self.send(200, body, HTML, etag=tag)
                elif path in ("/admin", "/admin/"):
                    self.send(200, files["admin.html"].get()[0], HTML, cache="no-store")
                elif path == "/healthz":
                    with pool.connection() as con:
                        con.execute("SELECT 1").fetchone()
                    self.send(200, b"ok", "text/plain")
                elif path == "/api/pulse":
                    snap = snapshot()
                    if snap is None:
                        return self.starting()
                    meta = cat.meta()
                    tail = snap.answer(("pulse", id(meta)), lambda: pulse_tail(snap, meta))
                    body = b'{"v":"%s","now":%d,' % (snap.v.encode(), int(time.time())) + tail
                    self.send(200, body, JSON)
                elif path == "/api/deals":
                    args = deals_args(q)
                    cards = q.get("fields") == "card"
                    snap = snapshot()
                    if snap is None:
                        return self.starting()
                    key = json.dumps(args, sort_keys=True, default=sorted) + ("|card" if cards else "")
                    tag = f'W/"{snap.v}-{hashlib.sha1(key.encode()).hexdigest()[:10]}"'
                    if q.get("since_v") == snap.v or self.fresh(tag):
                        return self.not_modified(tag)
                    self.send(200, snap.answer(("deals", key), lambda: deals_body(snap, args, cards)), JSON, etag=tag)
                elif path == "/api/brief":
                    snap = snapshot()
                    if snap is None:
                        return self.starting()
                    tag = f'W/"{snap.v}-brief"'
                    if self.fresh(tag):
                        return self.not_modified(tag)
                    self.send(200, snap.answer("brief", lambda: dumps(snap.brief)), JSON, etag=tag)
                elif path == "/api/changes":
                    limit = min(int(q.get("limit", 30)), 100)
                    snap = snapshot()
                    if snap is None:
                        return self.starting()
                    meta = cat.meta()
                    body, tag = snap.answer(("changes", id(meta), limit), lambda: (
                        lambda b: (b, etag_of(b)))(dumps({"changes": meta.changes[:max(limit, 0)]})))
                    self.send(200, body, JSON, etag=tag)
                elif path.startswith("/api/product/"):
                    pid = int(path.rsplit("/", 1)[1])
                    snap = snapshot()
                    if snap is None:
                        return self.starting()
                    tag = f'W/"{snap.v}-p{pid}"'
                    if self.fresh(tag):
                        return self.not_modified(tag)
                    body = snap.answer(("product", pid), lambda: product_body(snap, pid))
                    if body is None:
                        return self.json({"error": "not found"}, 404)
                    self.send(200, body, JSON, etag=tag)
                elif path == "/api/talabat/hubs":
                    self.json(talabat_hubs(), etag=True)
                elif path in STATIC:
                    name, ctype = STATIC[path]
                    body, tag = files[name].get()
                    self.send(200, body, ctype, "public, max-age=86400" if ctype.startswith("image/") else "no-cache",
                              tag)
                elif path == "/api/push/key":
                    self.json({"key": vapid().public_b64}, etag=True)
                elif path == "/manifest.webmanifest":
                    self.send(200, MANIFEST, "application/manifest+json", etag=MANIFEST_TAG)
                elif path == "/icon.svg":
                    self.send(200, ICON, "image/svg+xml", "public, max-age=86400", ICON_TAG)
                elif re.fullmatch(r"/p/\d+", path):
                    pid = int(path[3:])
                    snap = snapshot()
                    if snap is None:
                        return self.starting()
                    page = snap.answer(("page", pid), lambda: (lambda b: b and (b, etag_of(b)))(product_page(snap, pid)),
                                       keep=400)
                    if not page:
                        return self.send(404, pages.layout("Not found", '<div class="empty">This product isn\'t'
                                                           ' tracked (any more).</div>'), HTML)
                    self.send(200, page[0], HTML, etag=page[1])
                elif path in ("/stores", "/cats", "/coupons"):
                    snap = snapshot()
                    if snap is None:
                        return self.starting()
                    stamp = os.stat(coupons_file).st_mtime_ns if path == "/coupons" and os.path.exists(coupons_file) else 0
                    build = {"/stores": stores_page, "/cats": cats_page, "/coupons": coupons_page}[path]
                    body, tag = snap.answer((path, id(cat.meta()), stamp), lambda: (lambda b: (b, etag_of(b)))(build(snap)))
                    self.send(200, body, HTML, etag=tag)
                elif re.fullmatch(r"/store/[a-z_]+", path):
                    self.send_response(302)
                    self.send_header("Location", "/?deals=1&stores=" + path.rsplit("/", 1)[1])
                    self.send_header("Content-Length", "0")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                elif path == "/about":
                    meta = cat.meta() if snapshot() is not None else None
                    rows = meta.status if meta else []
                    body = pages.about_page([pages.AR_STORE.get(k, v) for k, v in LABELS.items()
                                             if k in {r["store"] for r in rows}])
                    self.send(200, body, HTML, etag=etag_of(body))
                elif path == "/privacy":
                    body = pages.privacy_page()
                    self.send(200, body, HTML, etag=etag_of(body))
                elif path == "/contact":
                    body = pages.contact_page(os.environ.get("CONTACT_EMAIL", "").strip(),
                                              os.environ.get("TELEGRAM_CHANNEL_URL", "").strip())
                    self.send(200, body, HTML, etag=etag_of(body))
                elif path == "/api/coupons":
                    self.json({"coupons": pages.load_coupons(coupons_file)}, etag=True)
                elif path == "/api/status":
                    snap = snapshot()
                    if snap is None:
                        return self.starting()
                    meta = cat.meta()
                    body, tag = snap.answer(("status", id(meta)), lambda: (lambda b: (b, etag_of(b)))(
                        dumps({"status": meta.status, "changes_last_hour": meta.changes_last_hour})))
                    self.send(200, body, JSON, etag=tag)
                else:
                    self.json({"error": "not found"}, 404)
            except (ValueError, KeyError) as e:
                self.json({"error": str(e)}, 400)
            except sqlite3.Error as e:
                self.json({"error": f"database: {e}"}, 503)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
            except Exception as e:  # never an empty reply
                import traceback
                traceback.print_exc()
                self.json({"error": f"{type(e).__name__}"}, 500)

        def do_POST(self):
            self.close_connection = True
            try:
                self._post()
            except (BrokenPipeError, ConnectionResetError):
                pass

        def json_post(self, obj, code: int = 200):
            self.send(code, dumps(obj), JSON, cache="no-store", headers={"Connection": "close"})

        def _post(self):
            u = urlparse(self.path)
            if u.path in ("/api/push/subscribe", "/api/push/unsubscribe", "/api/push/test"):
                try:
                    size = int(self.headers.get("Content-Length") or 0)
                    if not 0 < size <= 1 << 16:
                        return self.json_post({"error": "Send JSON."}, 413)
                    code, out = push_api(u.path, json.loads(self.rfile.read(size)))
                except (ValueError, TypeError, AttributeError):
                    code, out = 400, {"error": "Send JSON."}
                return self.json_post(out, code)
            if u.path not in ("/admin/breadfast", "/admin/breadfast-request", "/admin/settings", "/admin/coupons"):
                return self.json_post({"error": "not found"}, 404)
            token = os.environ.get("DEALS_ADMIN_TOKEN", "")
            if len(token) < 16:
                return self.json_post({"error": "Uploads are off. Set DEALS_ADMIN_TOKEN (16+ characters) in Coolify's"
                                                " environment variables for this app and redeploy."}, 403)
            given = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
            if not hmac.compare_digest(given.encode(), token.encode()):
                time.sleep(1.5)  # slows down guessing
                return self.json_post({"error": "Wrong admin token."}, 401)
            try:
                size = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                size = 0
            if u.path == "/admin/settings":
                if not 0 < size <= 1 << 16:
                    return self.json_post({"error": "Send the settings as JSON."}, 413)
                try:
                    body = json.loads(self.rfile.read(size))
                    hubs = known_hubs(db_path)
                    path = settings.path_for(db_path)
                    current = settings.load(path)
                    chosen = {h["id"]: h for h in current["talabat_hubs"]}
                    if "talabat_hubs" in body:
                        ids = [int(x) for x in body["talabat_hubs"]]
                        if not ids:
                            return self.json_post({"error": "Choose at least one hub."}, 422)
                        unknown = [i for i in ids if i not in hubs and i not in chosen]
                        if unknown:
                            return self.json_post({"error": f"Unknown hub(s): {unknown}. Add a hub by its talabat link."}, 422)
                        chosen = {i: chosen.get(i) or {"id": i, "aid": hubs[i]["aid"], "name": hubs[i]["name"]} for i in ids}
                    if body.get("hub_link"):
                        m = re.search(r"talabat\.com/egypt/grocery/(\d+)/", body["hub_link"])
                        if not m:
                            return self.json_post({"error": "That isn't a talabat mart link (…talabat.com/egypt/grocery/<hub>/…)."}, 422)
                        hid = int(m.group(1))
                        aid = re.search(r"[?&]aid=(\d+)", body["hub_link"])
                        known = hubs.get(hid, {})
                        chosen[hid] = {"id": hid, "aid": int(aid.group(1)) if aid else known.get("aid", 9838),
                                       "name": known.get("name") or str(hid)}
                    if len(chosen) > 10:
                        return self.json_post({"error": "Up to 10 hubs (each one adds ~15 minutes to a talabat sweep)."}, 422)
                    current["talabat_hubs"] = list(chosen.values())
                    settings.save(path, current)
                except (ValueError, TypeError, KeyError):
                    return self.json_post({"error": "Send {\"talabat_hubs\": [ids]} or {\"hub_link\": \"…\"}."}, 400)
                return self.json_post({"selected": [h["id"] for h in current["talabat_hubs"]],
                                       "hubs": current["talabat_hubs"]})
            if u.path == "/admin/coupons":
                if not 0 < size <= 1 << 18:
                    return self.json_post({"error": "Send {\"text\": \"store | CODE | what it gives | until | link\"}."}, 413)
                try:
                    coupons = pages.parse_coupons(str(json.loads(self.rfile.read(size)).get("text") or ""))
                except (ValueError, AttributeError):
                    return self.json_post({"error": "Send {\"text\": \"...\"}."}, 400)
                tmp = coupons_file + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(coupons, f, ensure_ascii=False, indent=1)
                os.replace(tmp, coupons_file)
                return self.json_post({"coupons": coupons})
            if u.path == "/admin/breadfast-request":
                if not 0 < size <= 1 << 20:
                    return self.json_post({"error": "Paste one request (up to 1 MB)."}, 413)
                try:
                    body = json.loads(self.rfile.read(size))
                    divisor = 100.0 if str(body.get("divisor")) == "100" else 1.0
                    with _import_lock:
                        r = breadfast.import_request(db_path, str(body.get("curl") or ""), requests_file, divisor)
                except breadfast.BadRequest as e:
                    return self.json_post({"error": str(e)[:1].upper() + str(e)[1:] + "."}, 422)
                except (ValueError, AttributeError):
                    return self.json_post({"error": "Send {\"curl\": \"...\"}."}, 400)
                except RuntimeError as e:
                    return self.json_post({"error": f"Couldn't reach Breadfast: {e}"}, 502)
                return self.json_post(r)
            if not 0 < size <= MAX_UPLOAD:
                return self.json_post({"error": f"Send the .har file (up to {MAX_UPLOAD // 2**20} MB)."}, 413)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            divisor = 100.0 if q.get("divisor") == "100" else 1.0
            if not _import_lock.acquire(blocking=False):
                return self.json_post({"error": "Another import is running; try again in a minute."}, 409)
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
                    return self.json_post({"error": f"That file isn't a HAR export ({type(e).__name__})."}, 400)
                if not r["products"]:
                    return self.json_post({"error": "No products in that capture. Browse a few Breadfast categories"
                                                    " while recording, then export again."}, 422)
                self.json_post(r)
            finally:
                os.unlink(tmp)  # the capture holds the app session: never kept
                _import_lock.release()

    return Handler


def serve(db_path: str, host: str = "127.0.0.1", port: int = 8800, requests_file: str = None,
          catalog: "catalog_mod.Catalog" = None):
    db.connect(db_path).close()  # create the tables
    cat = (catalog or catalog_mod.Catalog(db_path)).start()  # built in the background; requests wait for the first
    server = ThreadingHTTPServer((host, port), make_handler(db_path, requests_file, cat))
    server.daemon_threads = True
    server.request_queue_size = 128
    print(f"live deals on http://{host}:{port}/")
    server.serve_forever()
