"""The live deals page and its JSON API, on Python's built-in HTTP server.

  /                      the live page (refreshes itself every 10 seconds)
  /api/deals             filters: deals=1|0 stores=a,b kind=platform|drop min= max= stock=in|out|all
                         status=new,lowest,real,label,back,compare category= q= pmin= pmax= ids=1,2 best=1
                         sort=score|discount|saving|newest|drop|price|price_desc|unit|popular|name limit= offset=
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
"""

import hmac
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import db, finder, settings
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
CATALOG_SECONDS = 15
MANIFEST = json.dumps({
    "name": "Live deals", "short_name": "Deals", "start_url": "/", "scope": "/", "display": "standalone",
    "background_color": "#f6f5f2", "theme_color": "#d6402b", "description": "Live prices and real deals",
    "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
              {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
              {"src": "/icon-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
}).encode()
STATIC = {"/sw.js": ("sw.js", "text/javascript"), "/icon-192.png": ("icon-192.png", "image/png"),
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


def changes_last_hour(con) -> int:
    return con.execute("""SELECT COUNT(*) FROM prices x JOIN products p ON p.id = x.product_id
                          WHERE x.at > ? AND x.at > p.first_seen""", (int(time.time()) - 3600,)).fetchone()[0]


def recent_changes(con, limit: int = 30) -> list[dict]:
    rows = con.execute("""
        SELECT x.at, x.price, x.in_stock, p.id, p.store, p.name, p.image,
               (SELECT y.price FROM prices y WHERE y.product_id = x.product_id AND y.at < x.at
                ORDER BY y.at DESC LIMIT 1) AS before
        FROM prices x JOIN products p ON p.id = x.product_id
        WHERE x.at > p.first_seen ORDER BY x.at DESC LIMIT ?""", (limit,))
    out = []
    for r in rows:
        r = dict(r)
        r["label"] = LABELS.get(r["store"], r["store"])
        out.append(r)
    return out


def make_handler(db_path: str, requests_file: str = None):
    requests_file = requests_file or breadfast.DEFAULT_REQUESTS_FILE
    cache, lock = {}, threading.Lock()
    catalog_state = {"at": 0.0, "items": [], "by_id": {}}
    catalog_lock = threading.Lock()

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

    def catalog(con) -> list[dict]:
        with catalog_lock:  # one rebuild at a time; everyone else gets the fresh copy
            if time.monotonic() - catalog_state["at"] > CATALOG_SECONDS:
                items = finder.catalog(con)
                catalog_state.update(at=time.monotonic(), items=items, by_id={d["id"]: d for d in items})
            return catalog_state["items"]

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
        def log_message(self, fmt, *args):
            pass

        def send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if ctype == "text/javascript":
                self.send_header("Service-Worker-Allowed", "/")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def json(self, obj, code: int = 200):
            self.send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            con = None
            try:
                con = sqlite3.connect(db_path, timeout=10)
                con.row_factory = sqlite3.Row
                if u.path in ("/", "/live"):
                    self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                elif u.path in ("/admin", "/admin/"):
                    self.send(200, ADMIN_PAGE.read_bytes(), "text/html; charset=utf-8")
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

                    def build():
                        r = finder.query(catalog(con), **args)
                        r.update(labels=LABELS, status=status(con), changes_last_hour=changes_last_hour(con),
                                 now=int(time.time()))
                        return r
                    self.json(cached("deals" + json.dumps(args, sort_keys=True, default=sorted), build))
                elif u.path == "/api/changes":
                    limit = min(int(q.get("limit", 30)), 100)
                    self.json(cached(f"changes{limit}", lambda: {"changes": recent_changes(con, limit)}))
                elif u.path.startswith("/api/product/"):
                    pid = int(u.path.rsplit("/", 1)[1])
                    p = con.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
                    if not p:
                        return self.json({"error": "not found"}, 404)
                    catalog(con)
                    item = catalog_state["by_id"].get(pid, {})
                    matches = [dict(catalog_state["by_id"][m["id"]], spark=None) for m in item.get("matches", [])
                               if m["id"] in catalog_state["by_id"]]
                    self.json({"product": dict(p), "item": item, "history": db.history(con, pid), "matches": matches})
                elif u.path == "/api/talabat/hubs":
                    chosen = settings.load(settings.path_for(db_path))["talabat_hubs"]
                    hubs = known_hubs(db_path)
                    for h in chosen:
                        hubs.setdefault(h["id"], {**h, "areas": []})
                    rows = []
                    for h in hubs.values():
                        cities = sorted({a["city"] for a in h["areas"]})
                        area_names = [a["name"] for a in h["areas"]]
                        label, words = talabat.district(h["name"] + " " + " ".join(area_names[:5]), cities)
                        rows.append({"id": h["id"], "name": h["name"], "aid": h["aid"], "areas": area_names[:40],
                                     "cities": cities, "district": label, "words": words})
                    order = [d[0] for d in talabat.DISTRICTS]
                    rows.sort(key=lambda h: (order.index(h["district"]) if h["district"] in order else 99, h["name"].lower()))
                    self.json({"selected": [h["id"] for h in chosen], "districts": order, "hubs": rows})
                elif u.path in STATIC:
                    name, ctype = STATIC[u.path]
                    self.send(200, (Path(__file__).parent / name).read_bytes(), ctype)
                elif u.path == "/api/push/key":
                    self.json({"key": vapid().public_b64})
                elif u.path == "/manifest.webmanifest":
                    self.send(200, MANIFEST, "application/manifest+json")
                elif u.path == "/icon.svg":
                    self.send(200, ICON, "image/svg+xml")
                elif u.path == "/api/status":
                    self.json({"status": status(con), "changes_last_hour": changes_last_hour(con)})
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
            if u.path not in ("/admin/breadfast", "/admin/breadfast-request", "/admin/settings"):
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

    return Handler


def serve(db_path: str, host: str = "127.0.0.1", port: int = 8800, requests_file: str = None):
    db.connect(db_path).close()  # create the tables
    server = ThreadingHTTPServer((host, port), make_handler(db_path, requests_file))
    server.daemon_threads = True
    print(f"live deals on http://{host}:{port}/")
    server.serve_forever()
