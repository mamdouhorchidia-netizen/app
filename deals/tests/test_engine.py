"""The live catalogue (catalog.py) and the API on top of it: the same answers as finder's pure functions after any
series of changes, a version that moves only when something visible changed, 304s, the card shape, and readers that
never wait for a refresh."""

import http.client
import json
import os
import random
import sqlite3
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

from deals import catalog, db, finder, web
from deals.models import Offer

DAY = 86400
NAMES = ["Juhayna Milk 1L", "Juhayna Full Cream Milk, 1 Liter", "Pepsi Can 330ml", "Pepsi Soft Drink Can, 330 ml",
         "Lipton Black Tea 500 gm", "Lipton Tea Black, 500g", "Chipsy Salt 10 pcs", "Nescafe Classic 100g",
         "Nescafe Classic Coffee 100 g", "Galaxy Chocolate 36g", "Galaxy Smooth Chocolate, 36 gm", "Samsung Phone A15",
         "Dove Soap 125g", "Dove Beauty Soap 125 g", "Persil Gel 2.5L", "Persil Power Gel 2.5 L", "Tiger Chips 25g"]
STORES = ["talabat", "amazon_eg", "jumia", "kenzz"]
T0 = 1_790_000_000


class World:
    """A small shop world with history (some products on a sale rhythm), and random changes to it."""

    def __init__(self, seed: int, n: int = 120):
        self.rng = rng = random.Random(seed)
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "w.sqlite3")
        self.con = db.connect(self.path)
        self.skus, first = [], []
        for i in range(n):
            st = rng.choice(STORES)
            sku = f"{rng.choice([1, 2])}:{i % 40}" if st == "talabat" else f"s{i}"
            if (st, sku) not in self.skus:
                self.skus.append((st, sku))
                first.append(self.offer(st, sku))
        db.save_offers(self.con, first, T0 - 60 * DAY)
        for k in range(20):
            db.save_offers(self.con, [self.offer(st, sku) for st, sku in rng.sample(self.skus, len(self.skus) // 3)],
                           T0 - 60 * DAY + k * 3 * DAY + rng.randint(0, DAY))
        self.t = T0

    def offer(self, store, sku, name=None):
        rng = self.rng
        price = rng.choice([10, 20, 25, 30, 45, 50, 60, 80, 100, 99.5, 150])
        extra = {"hub": int(sku.split(":")[0]), "hub_name": "H" + sku.split(":")[0]} if store == "talabat" else {}
        if rng.random() < .1:
            extra["deal_label"] = "11.11"
        if rng.random() < .1:
            extra.update(promo_code="X", promo_price=price * .9)
        return Offer(store, sku, name or rng.choice(NAMES), price, rng.choice([None, None, price * 1.5, price * 4]),
                     in_stock=rng.random() < .9, category=rng.choice(["Food / Milk", "Drinks", "", "Snacks / Chips"]),
                     extra=extra)

    def step(self, step: int) -> str:
        """Moves the clock (a second to a week) and makes one random change. Says which."""
        rng, con = self.rng, self.con
        self.t += rng.choice([1, 5, 60, 600, 3600, 4 * 3600, DAY, 2 * DAY, 7 * DAY, 8640, 12 * 3600 + 7])
        r = rng.random()
        if r < .45:
            db.save_offers(con, [self.offer(st, sku) for st, sku in rng.sample(self.skus, rng.randint(1, 12))], self.t)
            return "prices"
        if r < .55:
            st = rng.choice(STORES)
            sku = f"{rng.choice([1, 2, 3])}:{rng.randint(0, 60)}" if st == "talabat" else f"n{step}"
            if (st, sku) not in self.skus:
                self.skus.append((st, sku))
                db.save_offers(con, [self.offer(st, sku)], self.t)
            return "new"
        with con:
            pid = con.execute("SELECT id FROM products ORDER BY random() LIMIT 1").fetchone()[0]
            if r < .6:
                con.execute("UPDATE products SET price = 0 WHERE id = ?", (pid,))
                return "no price"
            if r < .64:
                con.execute("DELETE FROM prices WHERE product_id=?", (pid,))
                con.execute("DELETE FROM products WHERE id=?", (pid,))
                return "deleted"
            if r < .7:
                con.execute("UPDATE products SET name = ? WHERE id = ?", (rng.choice(NAMES), pid))
                return "renamed"
            if r < .75:
                con.execute("UPDATE products SET last_seen = ? WHERE id = ?", (self.t, pid))
                return "seen"
        return "clock"

    def random_query(self) -> dict:
        rng = self.rng
        return dict(deals_only=rng.random() < .6, stores=rng.choice([None, ["talabat"], ["amazon_eg", "jumia"]]),
                    kind=rng.choice([None, "platform", "drop"]), min_pct=rng.choice([0, 10, 30]),
                    max_pct=rng.choice([None, 50]), stock=rng.choice(["in", "out", "all"]),
                    status=rng.sample(["new", "lowest", "real", "label", "back", "compare", "fake", "promo", "soon"],
                                      rng.randint(0, 2)),
                    category=rng.choice([None, "Food", "Drinks", "Other"]),
                    q=rng.choice([None, "milk", "pepsi can", "nescafe classic", "zz"]),
                    price_min=rng.choice([None, 20]), price_max=rng.choice([None, 60]),
                    ids=rng.choice([None, None, None, set(rng.sample(range(1, 150), 10))]), best=rng.random() < .2,
                    sort=rng.choice(list(finder.SORTS) + ["bogus"]), limit=rng.choice([0, 5, 60]),
                    offset=rng.choice([0, 3]), hubs=rng.choice([None, [1], [2, 3]]))


class IncrementalEqualsFull(unittest.TestCase):
    """After every change (and every jump of the clock) the live catalogue is exactly finder.catalog(con, now): the
    items, every query's answer and the brief."""

    def check(self, seed: int, steps: int = 60):
        w = World(seed)
        cat = catalog.Catalog(w.path, log=lambda *a: None)
        cat.refresh(full=True, now=w.t)
        ref = sqlite3.connect(w.path)
        ref.row_factory = sqlite3.Row
        try:
            for step in range(steps):
                what = w.step(step)
                cat.refresh(now=w.t)
                snap = cat.snapshot()
                full = finder.catalog(ref, w.t)
                got = [snap.full(d) for d in snap.items]
                self.assertEqual(got, full, f"seed {seed}, step {step} ({what})")
                self.assertEqual(json.dumps(got), json.dumps(full))  # the same order of keys too
                for _ in range(10):
                    args = w.random_query()
                    a, b = finder.query(full, **args), snap.query(**args)
                    self.assertEqual(a, {**b, "items": [snap.full(d) for d in b["items"]]}, (seed, step, args))
                self.assertEqual(snap.brief, finder.brief(full, w.t))
        finally:
            cat.close()
            ref.close()
            w.con.close()
            w.dir.cleanup()

    def test_many_changes(self):
        for seed in range(4):
            self.check(seed)

    def test_the_index_paths_for_big_catalogues(self):
        # the shortcuts for small candidate sets off: every filter through the masks, every sort through the
        # presorted orders, with tiny blocks so they split
        with mock.patch.object(catalog, "SMALL", 0), mock.patch.object(catalog, "SORT_DIRECTLY", 0), \
                mock.patch.object(catalog.SortedSlots, "LOAD", 3):
            for seed in (10, 11):
                self.check(seed, 40)

    def test_valid_until(self):
        """Judged at t0, a product is the same at any time before valid_until (the spark's last step aside)."""
        w = World(5)
        rng = random.Random(5)
        con = sqlite3.connect(w.path)
        con.row_factory = sqlite3.Row
        products = {p["id"]: p for p in finder.load_products(con)}
        for pid, p in products.items():
            for _ in range(4):
                t0 = T0 + rng.randint(-50 * DAY, 120 * DAY)
                rows = finder.load_history(con, t0 - finder.WINDOW, [pid]).get(pid)
                it, until = finder.judge(p, rows, t0)
                self.assertEqual(until, finder.valid_until(p, rows, it, t0))
                hi = min(until, t0 + 60 * DAY)
                for t in {t0, int(hi) - 1, rng.randint(t0, max(t0, int(hi) - 1))}:
                    if t0 <= t < hi:
                        rows_t = finder.load_history(con, t - finder.WINDOW, [pid]).get(pid)
                        self.assertEqual(finder.item(p, rows_t, t), it, (pid, t0, until, t))
        con.close()
        w.con.close()
        w.dir.cleanup()


class Versions(unittest.TestCase):
    def setUp(self):
        self.w = World(3, 60)
        self.cat = catalog.Catalog(self.w.path, log=lambda *a: None)
        self.cat.refresh(full=True, now=T0)

    def tearDown(self):
        self.cat.close()
        self.w.con.close()
        self.w.dir.cleanup()

    def test_v_changes_only_when_something_visible_does(self):
        v = self.cat.snapshot().v
        self.cat.refresh(now=T0 + 1)
        self.assertEqual(self.cat.snapshot().v, v)  # nothing written, a second later
        with self.w.con:  # only "last seen": a crawler saw the same price again
            self.w.con.execute("UPDATE products SET last_seen = ? WHERE id IN (1, 2, 3)", (T0 + 2,))
        self.cat.refresh(now=T0 + 2)
        snap = self.cat.snapshot()
        self.assertEqual(snap.v, v)
        self.assertEqual(snap.by_id[1]["last_seen"], T0 + 2)  # but the snapshot has it
        p = self.w.con.execute("SELECT store, sku, name, price FROM products WHERE id = 4").fetchone()
        db.save_offers(self.w.con, [Offer(p[0], p[1], p[2], p[3] * 0.5)], T0 + 3)
        self.cat.refresh(now=T0 + 3)
        self.assertNotEqual(self.cat.snapshot().v, v)  # a price moved

    def test_a_full_rebuild_keeps_the_version_when_nothing_changed(self):
        v = self.cat.snapshot().v
        self.cat.refresh(full=True, now=T0 + 1)
        self.assertEqual(self.cat.snapshot().v, v)

    def test_a_failing_refresh_keeps_the_last_good_snapshot(self):
        snap = self.cat.snapshot()
        logs = []
        self.cat.log = logs.append
        self.cat.every = 0.01
        with mock.patch.object(finder, "load_products", side_effect=sqlite3.OperationalError("disk I/O error")):
            t = threading.Thread(target=self.cat.run_forever, daemon=True)
            t.start()
            time.sleep(0.3)
            self.cat.stop()
            t.join(5)
        self.assertIs(self.cat.snapshot(), snap)
        self.assertTrue(logs and "still serving the last good version" in logs[0])
        self.assertIn("disk I/O error", self.cat.last_error)

    def test_readers_never_wait_for_a_refresh(self):
        started, release = threading.Event(), threading.Event()
        real = finder.load_products

        def slow(con, ids=None):
            started.set()
            release.wait(10)
            return real(con, ids)
        db.save_offers(self.w.con, [self.w.offer(*self.w.skus[0])], T0 + 10)
        with mock.patch.object(finder, "load_products", slow):
            t = threading.Thread(target=self.cat.refresh, kwargs={"now": T0 + 10})
            t.start()
            started.wait(5)
            t1 = time.perf_counter()
            snap = self.cat.snapshot()
            r = snap.query(limit=10)
            self.assertLess(time.perf_counter() - t1, 0.5)  # answered while the refresh is stuck
            self.assertTrue(r["total"] >= 0)
            release.set()
            t.join(10)
        self.assertIsNot(self.cat.snapshot(), snap)


class Api(unittest.TestCase):
    """The HTTP side: pulse, fields=card, v, ETag and 304, caching headers, kept-alive connections."""

    @classmethod
    def setUpClass(cls):
        cls.w = World(4, 80)
        cls.cat = catalog.Catalog(cls.w.path, log=lambda *a: None)
        cls.cat.refresh(full=True)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(cls.w.path, catalog=cls.cat))
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.cat.close()
        cls.w.con.close()
        cls.w.dir.cleanup()

    def setUp(self):
        self.c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)

    def tearDown(self):
        self.c.close()

    def get(self, path, headers=None, method="GET"):
        self.c.request(method, path, headers=headers or {})
        r = self.c.getresponse()
        return r, r.read()

    def test_pulse(self):
        r, b = self.get("/api/pulse")
        p = json.loads(b)
        self.assertEqual(r.status, 200)
        self.assertEqual(set(p), {"v", "now", "changes_last_hour", "status", "brief"})
        self.assertEqual(p["v"], self.cat.snapshot().v)
        self.assertLess(abs(p["now"] - time.time()), 5)
        brief = self.cat.snapshot().brief
        self.assertEqual(p["brief"], {k: brief[k] for k in ("deals", "dropped_24h", "record_lows", "fake", "soon",
                                                            "promo", "biggest_saving")})
        self.assertEqual(p["status"], json.loads(self.get("/api/status")[1])["status"])
        self.assertEqual(r.getheader("Cache-Control"), "no-cache")

    def test_card_fields(self):
        r, b = self.get("/api/deals?limit=60&fields=card&deals=0&stock=all")
        d = json.loads(b)
        self.assertTrue(d["items"])
        for it in d["items"]:
            self.assertEqual(list(it), list(web.CARD_FIELDS))
            self.assertLessEqual(len(it["spark"]), 12)
            self.assertEqual(set(it["links"]), {"web", "android", "ios"})
            self.assertEqual(set(it["verdict"]), {"label", "tone", "why"})
            for m in it["matches"]:
                self.assertEqual(set(m), {"id", "store", "price", "in_stock"})
            self.assertTrue(it["rhythm"] is None or set(it["rhythm"]) == {"next_in_days", "on_sale", "text"})
        for k in ("v", "now", "status", "labels", "changes_last_hour", "total", "facets"):
            self.assertIn(k, d)
        # the same items as the full answer, trimmed
        full = json.loads(self.get("/api/deals?limit=60&deals=0&stock=all")[1])
        self.assertEqual([x["id"] for x in full["items"]], [x["id"] for x in d["items"]])
        self.assertEqual(full["items"][0]["spark"][-1], d["items"][0]["spark"][-1])  # the step at "now" kept
        self.assertIn("browser", full["items"][0]["links"])  # the full answer keeps every field

    def test_watchlist_check_with_cards(self):
        ids = sorted(self.cat.snapshot().by_id)[:5]
        r, b = self.get(f"/api/deals?ids={','.join(map(str, ids))}&fields=card&deals=0&stock=all",
                        {"X-Home-View": "1"})
        d = json.loads(b)
        self.assertEqual(sorted(x["id"] for x in d["items"]), ids)
        for it in d["items"]:
            for k in ("matches", "in_stock", "price", "name", "store"):
                self.assertIn(k, it)

    def test_etag_and_304(self):
        for path in ("/api/deals?limit=20", "/api/deals?limit=20&fields=card", "/api/brief", "/api/changes",
                     "/api/product/1", "/p/1", "/stores", "/cats", "/coupons", "/", "/sw.js", "/i18n.js",
                     "/icon-192.png", "/manifest.webmanifest", "/about"):
            r, b = self.get(path)
            self.assertEqual(r.status, 200, path)
            tag = r.getheader("ETag")
            self.assertTrue(tag and tag.startswith('W/"'), path)  # on the 200 answer itself
            r2, b2 = self.get(path, {"If-None-Match": tag})
            self.assertEqual((r2.status, b2), (304, b""), path)
            self.assertEqual(r2.getheader("ETag"), tag)
            self.assertIsNone(r2.getheader("Content-Length"))
        v = self.cat.snapshot().v
        r, b = self.get(f"/api/deals?limit=20&since_v={v}")
        self.assertEqual((r.status, b), (304, b""))
        r, b = self.get("/api/deals?limit=20&since_v=old")
        self.assertEqual(r.status, 200)
        tag = r.getheader("ETag")
        self.assertTrue(tag.startswith(f'W/"{v}-'))
        self.assertNotEqual(tag, self.get("/api/deals?limit=21")[0].getheader("ETag"))  # one per query

    def test_cache_control(self):
        cases = {"/api/deals": "no-cache", "/": "no-cache", "/sw.js": "no-cache", "/p/1": "no-cache",
                 "/icon-192.png": "public, max-age=86400", "/icon.svg": "public, max-age=86400", "/admin": "no-store"}
        for path, want in cases.items():
            self.assertEqual(self.get(path)[0].getheader("Cache-Control"), want, path)
        self.c.request("POST", "/api/push/unsubscribe", body=b"{}", headers={"Content-Type": "application/json"})
        r = self.c.getresponse()
        r.read()
        self.assertEqual(r.getheader("Cache-Control"), "no-store")
        self.assertEqual(r.getheader("Connection"), "close")

    def test_keep_alive_head_and_errors(self):
        r, _ = self.get("/api/pulse")
        sock = self.c.sock
        self.assertEqual(r.version, 11)
        for path in ("/api/deals?limit=5", "/nope", "/api/deals?limit=x", "/store/jumia", "/healthz"):
            r, b = self.get(path)
            self.assertEqual(int(r.getheader("Content-Length")), len(b), path)
        r, b = self.get("/api/deals?limit=5", method="HEAD")
        self.assertEqual((r.status, b), (200, b""))
        self.assertGreater(int(r.getheader("Content-Length")), 0)
        self.assertIs(self.c.sock, sock)  # every request on the same connection

    def test_old_page_answers_unchanged(self):
        """The full answer is what the old server sent: finder.query on finder.catalog, plus v."""
        r, b = self.get("/api/deals?limit=30&sort=discount")
        d = json.loads(b)
        con = sqlite3.connect(self.w.path)
        con.row_factory = sqlite3.Row
        snap = self.cat.snapshot()
        want = finder.query(finder.catalog(con, snap.now), sort="discount", limit=30)
        con.close()
        self.assertEqual(d["items"], json.loads(json.dumps(want["items"])))
        self.assertEqual((d["total"], d["facets"]), (want["total"], want["facets"]))
        p = json.loads(self.get(f"/api/product/{d['items'][0]['id']}")[1])
        self.assertEqual(set(p), {"product", "item", "history", "matches"})
        self.assertEqual(p["item"], d["items"][0])


class StartUp(unittest.TestCase):
    def test_a_request_during_the_first_build_gets_a_clear_answer(self):
        d = tempfile.TemporaryDirectory()
        path = os.path.join(d.name, "s.sqlite3")
        db.connect(path).close()
        cat = catalog.Catalog(path, log=lambda *a: None)  # never started: no snapshot yet
        server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(path, catalog=cat))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cat.start = lambda: cat  # keep it from building
        try:
            with mock.patch.object(web, "STARTUP_WAIT", 0.05):
                c = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
                c.request("GET", "/api/deals")
                r = c.getresponse()
                body = json.loads(r.read())
                self.assertEqual((r.status, body["error"], r.getheader("Retry-After")), (503, "starting", "5"))
                # The health check never waits. While the first catalogue is being built it answers 503 at once
                # (so a new deploy takes traffic only when it can answer), and ok after READY_GRACE regardless.
                c.request("GET", "/healthz")
                r = c.getresponse(); r.read()
                self.assertEqual(r.status, 503)
                with mock.patch.object(web, "READY_GRACE", 0):
                    c.request("GET", "/healthz")
                    r = c.getresponse(); r.read()
                    self.assertEqual(r.status, 200)
                c.close()
        finally:
            server.shutdown()
            server.server_close()
            d.cleanup()


if __name__ == "__main__":
    unittest.main()
