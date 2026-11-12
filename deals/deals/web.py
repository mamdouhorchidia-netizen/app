"""The live deals page and its JSON API, on Python's built-in HTTP server.

  /                      the live page (refreshes itself every minute)
  /api/deals             ?store= &q= &category= &min= &sort=discount|saving|newest|price &limit=
  /api/product/ID        one product and its price history
  /api/status            the last collection run per store
"""

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import db, finder
from .stores import LABELS

PAGE = Path(__file__).with_name("live.html")


def status(con) -> list[dict]:
    rows = con.execute("""
        SELECT r.* FROM runs r JOIN (SELECT store, MAX(id) id FROM runs WHERE finished IS NOT NULL GROUP BY store) l
        ON r.id = l.id ORDER BY r.store""")
    out = []
    for r in rows:
        r = dict(r)
        r["label"] = LABELS.get(r["store"], r["store"])
        r["products"] = con.execute("SELECT COUNT(*) FROM products WHERE store=?", (r["store"],)).fetchone()[0]
        out.append(r)
    return out


def make_handler(db_path: str):
    local = {}

    def conn() -> sqlite3.Connection:
        import threading
        t = threading.get_ident()
        if t not in local:
            local[t] = db.connect(db_path)
        return local[t]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def json(self, obj, code: int = 200):
            self.send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if u.path in ("/", "/live"):
                    self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                elif u.path == "/api/deals":
                    deals = finder.find_deals(
                        conn(), min_pct=float(q.get("min", 5)), store=q.get("store") or None,
                        q=q.get("q") or None, category=q.get("category") or None,
                        sort=q.get("sort", "discount"), limit=min(int(q.get("limit", 200)), 1000))
                    self.json({"deals": deals, "labels": LABELS, "status": status(conn())})
                elif u.path.startswith("/api/product/"):
                    pid = int(u.path.rsplit("/", 1)[1])
                    p = conn().execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
                    if not p:
                        return self.json({"error": "not found"}, 404)
                    self.json({"product": dict(p), "history": db.history(conn(), pid)})
                elif u.path == "/api/status":
                    self.json({"status": status(conn())})
                else:
                    self.json({"error": "not found"}, 404)
            except (ValueError, KeyError) as e:
                self.json({"error": str(e)}, 400)

    return Handler


def serve(db_path: str, host: str = "127.0.0.1", port: int = 8800):
    db.connect(db_path).close()  # create the tables
    server = ThreadingHTTPServer((host, port), make_handler(db_path))
    print(f"live deals on http://{host}:{port}/")
    server.serve_forever()
