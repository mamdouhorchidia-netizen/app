"""The live deals page and its JSON API, on Python's built-in HTTP server.

  /                      the live page (refreshes itself every 10 seconds)
  /api/deals             ?store= &q= &category= &min= &sort=discount|saving|newest|price &limit=
  /api/product/ID        one product and its price history
  /api/status            each store: products, last check, last change, cycle time, errors
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


def make_handler(db_path: str):
    cache, lock = {}, threading.Lock()

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
                    args = dict(min_pct=float(q.get("min", 5)), store=q.get("store") or None, q=q.get("q") or None,
                                category=q.get("category") or None, sort=q.get("sort", "discount"),
                                limit=min(int(q.get("limit", 200)), 1000))
                    self.json(cached("deals" + json.dumps(args, sort_keys=True), lambda: {
                        "deals": finder.find_deals(con, **args), "labels": LABELS,
                        "status": status(con), "changes_last_hour": changes_last_hour(con), "now": int(time.time())}))
                elif u.path.startswith("/api/product/"):
                    pid = int(u.path.rsplit("/", 1)[1])
                    p = con.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
                    if not p:
                        return self.json({"error": "not found"}, 404)
                    self.json({"product": dict(p), "history": db.history(con, pid)})
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
