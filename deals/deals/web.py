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
"""

import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import db, finder
from .stores import LABELS

PAGE = Path(__file__).with_name("live.html")
CACHE_SECONDS = 5
CATALOG_SECONDS = 15
MANIFEST = json.dumps({
    "name": "Live deals", "short_name": "Deals", "start_url": "/", "display": "standalone",
    "background_color": "#f6f5f2", "theme_color": "#d6402b",
    "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}],
}).encode()
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


def make_handler(db_path: str):
    cache, lock = {}, threading.Lock()
    catalog_state = {"at": 0.0, "items": [], "by_id": {}}
    catalog_lock = threading.Lock()

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
                                best=q.get("best") == "1", sort=q.get("sort", "score"),
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

    return Handler


def serve(db_path: str, host: str = "127.0.0.1", port: int = 8800):
    db.connect(db_path).close()  # create the tables
    server = ThreadingHTTPServer((host, port), make_handler(db_path))
    server.daemon_threads = True
    print(f"live deals on http://{host}:{port}/")
    server.serve_forever()
