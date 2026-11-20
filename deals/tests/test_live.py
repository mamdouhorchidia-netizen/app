"""The refresh plumbing: data version, the background catalog, the one-pass query, and the HTTP transport
(gzip, keep-alive, ETag/304, /api/pulse, /api/changes?after=, /api/seen)."""

import gzip
import http.client
import itertools
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from collections import Counter
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from deals import db, finder, livedata, push, web
from deals.models import Offer
from deals.scheduler import StoreLoop
from deals.tasks import Task

DAY = 86400
T0 = 1_760_000_000


def reference_query(items, deals_only=True, stores=None, kind=None, min_pct=0, max_pct=None, stock="in", status=None,
                    category=None, q=None, price_min=None, price_max=None, ids=None, best=False, sort="score",
                    limit=200, offset=0, hubs=None):
    """finder.query as it was before the one-pass rewrite (five passes): the one-pass version must agree."""
    words = [w for w in (q or "").lower().split() if w]
    status = set(status or [])
    stores = set(stores or [])

    def keep(d, skip=None):
        if ids is not None:
            return d["id"] in ids and (skip == "store" or not stores or d["store"] in stores)
        if deals_only and not (d["kind"] and d["discount_pct"] > 0):
            return False
        if skip != "kind" and kind and d["kind"] != kind:
            return False
        if d["discount_pct"] < (min_pct or 0) and (deals_only or min_pct):
            return False
        if max_pct is not None and d["discount_pct"] > max_pct:
            return False
        if stock == "in" and not d["in_stock"] or stock == "out" and d["in_stock"]:
            return False
        if skip != "store" and stores and d["store"] not in stores:
            return False
        if skip != "hub" and hubs and d["store"] == "talabat" and d["hub"] not in hubs:
            return False
        if skip != "category" and category and d["top_category"] != category:
            return False
        if price_min is not None and d["price"] < price_min or price_max is not None and d["price"] > price_max:
            return False
        if best and not d["cheapest"]:
            return False
        if "real" in status and {"steady", "inflated", "rose"} & set(d["flags"]):
            return False
        for f in ("new", "lowest", "label", "back"):
            if f in status and f not in d["flags"]:
                return False
        if "compare" in status and not d["matches"]:
            return False
        if "fake" in status and "steady" not in d["flags"] and "inflated" not in d["flags"]:
            return False
        if "promo" in status and not d.get("promo_code"):
            return False
        if "soon" in status and not (d.get("rhythm") and d["rhythm"]["next_in_days"] is not None
                                     and d["rhythm"]["next_in_days"] <= 3):
            return False
        if words:
            text = (d["name"] + " " + (d["brand"] or "") + " " + (d["category"] or "")).lower()
            if not all(w in text for w in words):
                return False
        return True

    hits = [d for d in items if keep(d)]
    hits.sort(key=finder.SORTS.get(sort, finder.SORTS["score"]))
    facets = {
        "store": Counter(d["store"] for d in items if keep(d, "store")),
        "category": Counter(d["top_category"] for d in items if keep(d, "category")),
        "kind": Counter(d["kind"] for d in items if keep(d, "kind") and d["kind"]),
        "hub": Counter(f'{d["hub"]}|{d["hub_name"]}' for d in items if d["store"] == "talabat" and d["hub"]
                       and keep(d, "hub")),
    }
    return {"items": hits[offset:offset + limit], "total": len(hits),
            "facets": {k: dict(v.most_common()) for k, v in facets.items()}}


def seed(con, now=T0 + 10 * DAY):
    hub = {"hub": 619848, "hub_name": "Maadi Old"}
    db.save_offers(con, [
        Offer("talabat", "619848:t1", "Juhayna Full Cream Milk 1L", 50, 60, category="Dairy & Eggs / Milk", extra=hub),
        Offer("talabat", "776372:t1", "Juhayna Full Cream Milk 1L", 52, 60, category="Dairy & Eggs / Milk",
              extra={"hub": 776372, "hub_name": "Obour"}),
        Offer("amazon_eg", "a1", "Juhayna Full Cream Milk, 1 Liter", 45, category="Grocery", extra={"deal_label": "11.11"}),
        Offer("talabat", "619848:t2", "Lipton Black Tea, 500 gm", 100, category="Coffee & Tea / Tea", extra=hub),
        Offer("talabat", "619848:t3", "Chipsy Salt 10 pcs", 30, 40, in_stock=False, category="Snacks", extra=hub),
        Offer("amazon_eg", "a2", "Steady Soap 125g", 90, 100, category="Beauty"),
        Offer("kenzz", "k1", "Lego City Set", 900, 1500, category="Toys", extra={"promo_code": "KBF26", "promo_price": 850}),
    ], T0)
    db.save_offers(con, [Offer("talabat", "619848:t2", "Lipton Black Tea, 500 gm", 80, category="Coffee & Tea / Tea",
                               extra=hub)], now - 3600)


class DataVersionAndSchema(unittest.TestCase):
    def test_v_moves_only_when_a_price_row_is_written(self):
        con = db.connect(":memory:")
        self.addCleanup(con.close)
        self.assertEqual(db.data_version(con), 0)
        db.save_offers(con, [Offer("talabat", "a", "Milk", 50)], T0)
        v1 = db.data_version(con)
        self.assertGreater(v1, 0)
        db.save_offers(con, [Offer("talabat", "a", "Milk (renamed)", 50)], T0 + 60)  # nothing moved
        self.assertEqual(db.data_version(con), v1)
        db.save_offers(con, [Offer("talabat", "a", "Milk", 5)], T0 + 120)  # a wild jump: held, not recorded
        self.assertEqual(db.data_version(con), v1)
        db.save_offers(con, [Offer("talabat", "a", "Milk", 45)], T0 + 180)
        self.assertEqual(db.data_version(con), v1 + 1)
        db.save_offers(con, [Offer("talabat", "a", "Milk", 45, in_stock=False)], T0 + 240)  # stock counts too
        self.assertEqual(db.data_version(con), v1 + 2)

    def test_old_database_gets_the_time_index_and_pragmas(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "old.sqlite3")
            raw = sqlite3.connect(path)
            raw.executescript(db.SCHEMA.replace(
                "CREATE INDEX IF NOT EXISTS prices_by_at ON prices (at);", ""))
            raw.execute("INSERT INTO products (store, sku, name, first_seen, last_seen, price) VALUES ('s','x','X',1,1,5)")
            raw.execute("INSERT INTO prices VALUES (1, 1, 5, NULL, 1)")
            raw.commit()
            self.assertNotIn("prices_by_at", [r[0] for r in raw.execute("SELECT name FROM sqlite_master")])
            raw.close()
            con = db.connect(path)
            con.close()
            con = db.connect(path)  # twice: idempotent
            self.assertIn("prices_by_at", [r[0] for r in con.execute("SELECT name FROM sqlite_master")])
            self.assertEqual(con.execute("PRAGMA synchronous").fetchone()[0], 1)  # NORMAL
            self.assertEqual(con.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(con.execute("PRAGMA busy_timeout").fetchone()[0], 30000)
            plan = " ".join(r[-1] for r in con.execute("EXPLAIN QUERY PLAN SELECT COUNT(*) FROM prices WHERE at > 5"))
            self.assertIn("prices_by_at", plan)
            self.assertEqual(db.data_version(con), 1)
            con.close()

    def test_a_checked_page_is_one_commit(self):
        clock = [1000.0]
        loop = StoreLoop("talabat", lambda f: [Task("p", lambda: ([Offer("talabat", "a", "Milk", 50)], []))], ":memory:",
                         delay=0, clock=lambda: clock[0], sleep=lambda s: None, log=lambda *_: None)
        loop.con = db.connect(":memory:")
        self.addCleanup(loop.con.close)
        loop.step()  # lists the pages and checks the first
        commits = []
        loop.con.set_trace_callback(lambda sql: commits.append(sql) if sql.strip().upper().startswith("COMMIT") else None)
        clock[0] += 10
        loop.step()
        self.assertEqual(len(commits), 1)
        row = loop.con.execute("SELECT pages, updated FROM live_status WHERE store='talabat'").fetchone()
        self.assertEqual((row["pages"], row["updated"]), (2, 1010))


class OnePassQuery(unittest.TestCase):
    def test_same_answers_as_the_five_pass_version(self):
        con = db.connect(":memory:")
        self.addCleanup(con.close)
        seed(con)
        items = finder.catalog(con, T0 + 10 * DAY)
        ids = {items[0]["id"], items[3]["id"], items[5]["id"]}
        choices = dict(deals_only=[True, False], stores=[None, ["talabat"], ["amazon_eg", "kenzz"]],
                       kind=[None, "platform", "drop"], stock=["in", "all", "out"],
                       status=[[], ["real"], ["new"], ["compare"], ["promo"], ["fake"]],
                       category=[None, "Dairy & Eggs", "Toys"], hubs=[None, [619848]], ids=[None, ids])
        n = 0
        for combo in itertools.product(*choices.values()):
            kw = dict(zip(choices, combo))
            for extra in ({}, {"min_pct": 15, "sort": "price"}, {"q": "milk", "best": True}, {"price_max": 60, "sort": "name"}):
                a, b = reference_query(items, **kw, **extra), finder.query(items, **kw, **extra)
                self.assertEqual([d["id"] for d in a["items"]], [d["id"] for d in b["items"]], kw)
                self.assertEqual(a["total"], b["total"])
                self.assertEqual(json.dumps(a["facets"]), json.dumps(b["facets"]), kw)  # the order too
                n += bool(a["total"])
        self.assertGreater(n, 100)

    def test_select_returns_everything_sorted(self):
        con = db.connect(":memory:")
        self.addCleanup(con.close)
        seed(con)
        items = finder.catalog(con, T0 + 10 * DAY)
        hits, facets = finder.select(items, deals_only=False, stock="all", sort="price")
        self.assertEqual([d["price"] for d in hits], sorted(d["price"] for d in items))
        self.assertEqual(sum(facets["store"].values()), len(items))


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class Keeper(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "d.sqlite3")
        self.con = db.connect(self.path)
        seed(self.con)

    def tearDown(self):
        self.con.close()
        self.dir.cleanup()

    def test_version_is_read_at_most_once_a_second(self):
        clock = FakeClock()
        v = livedata.Version(self.path, clock=clock)
        first = v.get()
        db.save_offers(self.con, [Offer("amazon_eg", "a2", "Steady Soap 125g", 80, 100)], T0 + DAY)
        self.assertEqual(v.get(), first)  # cached
        clock.t += 1.0
        self.assertEqual(v.get(), first + 1)

    def test_rebuilds_follow_v(self):
        clock = FakeClock()
        version = livedata.Version(self.path, every=0, clock=clock)
        builds = []
        k = livedata.CatalogKeeper(self.path, version, clock=clock, wall=lambda: T0 + 10 * DAY,
                                   build=lambda con, now: builds.append(now) or finder.catalog(con, now))
        first = k.get()
        self.assertEqual(first.v, version.get())
        self.assertFalse(k.check())
        clock.t += 200
        self.assertFalse(k.check())  # nothing changed: no rebuild for 5 minutes
        db.save_offers(self.con, [Offer("amazon_eg", "a2", "Steady Soap 125g", 80, 100)], T0 + DAY)
        self.assertTrue(k.check())  # v moved and the copy is 200 s old
        self.assertEqual(k.snap.v, first.v + 1)
        db.save_offers(self.con, [Offer("amazon_eg", "a2", "Steady Soap 125g", 70, 100)], T0 + DAY)
        clock.t += 14
        self.assertFalse(k.check())  # moved again, but the copy is only 14 s old
        clock.t += 1
        self.assertTrue(k.check())
        clock.t += 299
        self.assertFalse(k.check())
        clock.t += 1
        self.assertTrue(k.check())  # every 5 minutes anyway: some flags follow the clock
        self.assertEqual(len(builds), 4)
        self.assertEqual(k.snap.gen, 4)
        clock.t += 700  # nobody has read it for over 10 minutes: no more rebuilds...
        self.assertFalse(k.check())
        self.assertEqual(k.get().gen, 4)  # ...until someone does (they get the old copy at once)
        self.assertTrue(k.check())
        clock.t += 700
        k.touch()  # a pulse poll counts as looking
        self.assertTrue(k.check())
        k.stop()

    def test_readers_get_the_old_copy_while_a_new_one_builds(self):
        release, building = threading.Event(), threading.Event()

        def slow(con, now):
            if k.builds:  # the second build waits
                building.set()
                release.wait(5)
            return finder.catalog(con, now)
        k = livedata.CatalogKeeper(self.path, build=slow, poll=3600)
        first = k.get()
        t = threading.Thread(target=k.rebuild)
        t.start()
        self.assertTrue(building.wait(5))
        started = time.monotonic()
        self.assertIs(k.get(), first)  # no waiting behind the builder
        self.assertLess(time.monotonic() - started, 0.5)
        release.set()
        t.join(5)
        self.assertIsNot(k.get(), first)
        self.assertEqual(k.get().gen, 2)
        k.stop()

    def test_digest_ignores_the_clock(self):
        a = finder.catalog(self.con, T0 + 10 * DAY)
        b = finder.catalog(self.con, T0 + 10 * DAY + 600)
        self.assertNotEqual([d["spark"] for d in a], [d["spark"] for d in b])
        self.assertEqual([livedata.item_digest(d) for d in a], [livedata.item_digest(d) for d in b])
        c = [dict(d, price=d["price"] + 1) for d in a]
        self.assertTrue(all(livedata.item_digest(x) != livedata.item_digest(y) for x, y in zip(a, c)))

    def test_bot_and_poster_share_the_web_copy(self):
        from deals import bot, telegram
        k = livedata.CatalogKeeper(self.path, poll=3600)
        with mock.patch.dict(livedata._shared, {os.path.abspath(self.path): k}):
            b = bot.Bot(self.path, "t", "https://x", send=lambda *a, **kw: {})
            with mock.patch.object(finder, "catalog", side_effect=AssertionError("built its own")):
                items, by_id = b.catalog(None)
                self.assertIs(items, k.snap.items)
                sent = []
                p = telegram.Poster(self.path, "t", "@c", "https://x", min_pct=10, send=lambda m, body: sent.append(m))
                p.since = 0
                p.once()
                self.assertEqual(p.since, k.snap.built)
        k.stop()


class Notifier(unittest.TestCase):
    def test_wakes_on_v_and_at_least_every_minute(self):
        n = push.Notifier.__new__(push.Notifier)
        n.every, n.seen_v, n.ticked = 60, 7, 1000.0
        self.assertFalse(n.due(7, 1015))
        self.assertTrue(n.due(8, 1015))
        self.assertTrue(n.due(7, 1060))

    def test_run_forever_ticks_when_v_moves(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "d.sqlite3")
            con = db.connect(path)
            db.save_offers(con, [Offer("talabat", "a", "Milk", 50)], T0)
            n = push.Notifier(path, {}, log=lambda *_: None)
            ticks, clock, sleeps = [], FakeClock(5000.0), []

            def sleep(s):
                sleeps.append(s)
                clock.t += s
                if len(sleeps) == 3:
                    db.save_offers(con, [Offer("talabat", "a", "Milk", 40)], T0 + 60)
                if len(sleeps) > 6:
                    raise KeyboardInterrupt
            n.tick = lambda: ticks.append(clock.t)
            with self.assertRaises(KeyboardInterrupt):
                n.run_forever(sleep=sleep, clock=clock)
            con.close()
            self.assertEqual(set(sleeps), {15})
            # first look (v unknown), then the change at the third look; nothing in between
            self.assertEqual(ticks, [5015.0, 5045.0])


class Http(unittest.TestCase):
    """The real server in a thread, spoken to over one keep-alive connection."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "d.sqlite3")
        self.con = db.connect(self.path)
        seed(self.con, int(time.time()))
        self.keeper = livedata.CatalogKeeper(self.path, livedata.Version(self.path, every=0), poll=3600)
        patch = mock.patch.object(web, "PULSE_SECONDS", 0)
        patch.start()
        self.addCleanup(patch.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(self.path, None, self.keeper))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.http = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)

    def tearDown(self):
        self.http.close()
        self.server.shutdown()
        self.server.server_close()
        self.keeper.stop()
        self.con.close()
        self.dir.cleanup()

    def get(self, path, **headers):
        self.http.request("GET", path, headers=headers)
        r = self.http.getresponse()
        body = r.read()
        self.assertEqual(int(r.getheader("Content-Length")), len(body))  # keep-alive needs it on every answer
        if r.getheader("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        return r, body

    def test_keep_alive_gzip_and_304(self):
        r, body = self.get("/api/deals?limit=2")
        sock = self.http.sock
        self.assertEqual((r.status, r.version), (200, 11))
        self.assertIsNone(r.getheader("Content-Encoding"))  # not asked for
        d = json.loads(body)
        for key in ("items", "total", "facets", "labels", "status", "changes_last_hour", "now", "v"):
            self.assertIn(key, d)
        self.assertEqual(len(d["items"]), 2)
        self.assertEqual(r.getheader("Cache-Control"), "no-store")
        etag = r.getheader("ETag")
        self.assertTrue(etag.startswith('W/"'))
        r, body = self.get("/api/deals?limit=2", **{"Accept-Encoding": "gzip, deflate"})
        self.assertEqual((r.getheader("Content-Encoding"), r.getheader("Vary")), ("gzip", "Accept-Encoding"))
        self.assertEqual(json.loads(body)["items"], d["items"])
        self.assertEqual(r.getheader("ETag"), etag)
        r, body = self.get("/api/deals?limit=2", **{"If-None-Match": etag})
        self.assertEqual((r.status, body), (304, b""))
        self.assertEqual(r.getheader("ETag"), etag)
        r, _ = self.get("/api/deals?limit=2&offset=1", **{"If-None-Match": etag})
        self.assertEqual(r.status, 200)  # another page
        r, _ = self.get("/nothing-here")
        self.assertEqual(r.status, 404)
        r, _ = self.get("/api/pulse")
        self.assertEqual(r.status, 200)
        self.assertIs(self.http.sock, sock)  # all on one connection

    def test_keep_alive_answers_without_a_nagle_stall(self):
        self.get("/api/pulse")
        started = time.monotonic()
        for _ in range(30):  # headers and body are two writes: with Nagle on, each answer waited ~40 ms
            self.get("/api/pulse")
        self.assertLess(time.monotonic() - started, 0.6)

    def test_gzip_refused_with_q0(self):
        r, _ = self.get("/api/deals?limit=5", **{"Accept-Encoding": "gzip;q=0"})
        self.assertIsNone(r.getheader("Content-Encoding"))

    def test_a_price_change_moves_v_and_the_etags(self):
        self.keeper.get()  # (a first pulse would start the first build, and the next pulse would show it)
        r, body = self.get("/api/pulse")
        pulse = json.loads(body)
        self.assertEqual(set(pulse), {"v", "now", "status", "changes_last_hour", "catalog"})
        self.assertEqual(pulse["v"], db.data_version(self.con))
        p_etag = r.getheader("ETag")
        self.assertEqual(self.get("/api/pulse", **{"If-None-Match": p_etag})[0].status, 304)
        r, body = self.get("/api/deals?deals=0&stock=all&limit=60")
        d_etag = r.getheader("ETag")
        first = json.loads(body)
        self.assertEqual(first["v"], pulse["v"])
        # a rebuild with nothing changed keeps the ETag, although `now` and the sparks' last points moved
        with mock.patch.object(livedata.time, "time", return_value=time.time() + 120):
            self.keeper.rebuild()
        self.assertEqual(self.get("/api/deals?deals=0&stock=all&limit=60", **{"If-None-Match": d_etag})[0].status, 304)
        db.save_offers(self.con, [Offer("amazon_eg", "a2", "Steady Soap 125g", 70, 100)])
        r, body = self.get("/api/pulse", **{"If-None-Match": p_etag})
        self.assertEqual(r.status, 200)
        self.assertEqual(json.loads(body)["v"], pulse["v"] + 1)
        self.assertEqual(json.loads(body)["catalog"]["v"], pulse["v"])  # not rebuilt yet
        self.keeper.rebuild()
        r, body = self.get("/api/deals?deals=0&stock=all&limit=60", **{"If-None-Match": d_etag})
        self.assertEqual(r.status, 200)
        self.assertNotEqual(r.getheader("ETag"), d_etag)
        self.assertEqual(json.loads(body)["v"], pulse["v"] + 1)
        soap = next(x for x in json.loads(body)["items"] if x["sku"] == "a2")
        self.assertEqual(soap["price"], 70)

    def test_changes_after_a_rowid(self):
        r, body = self.get("/api/changes?limit=24")
        whole = json.loads(body)
        v = whole["v"]
        self.assertEqual(v, db.data_version(self.con))
        self.assertEqual([c["name"] for c in whole["changes"]], ["Lipton Black Tea, 500 gm"])  # first sightings aren't moves
        self.assertIn("rowid", whole["changes"][0])
        r, body = self.get(f"/api/changes?after={v}")
        self.assertEqual(json.loads(body), {"changes": [], "v": v})
        etag = r.getheader("ETag")
        self.assertEqual(self.get(f"/api/changes?after={v}", **{"If-None-Match": etag})[0].status, 304)
        db.save_offers(self.con, [Offer("amazon_eg", "a2", "Steady Soap 125g", 70, 100),
                                  Offer("amazon_eg", "a1", "Juhayna Full Cream Milk, 1 Liter", 40)])
        r, body = self.get(f"/api/changes?after={v}")
        d = json.loads(body)
        self.assertEqual(d["v"], v + 2)
        self.assertEqual([(c["rowid"], c["price"], c["before"]) for c in d["changes"]], [(v + 2, 40, 45), (v + 1, 70, 90)])
        r, body = self.get(f"/api/changes?after={v + 1}")
        self.assertEqual([c["rowid"] for c in json.loads(body)["changes"]], [v + 2])
        r, body = self.get(f"/api/changes?after={v + 100}")  # a cursor from another numbering: start over
        d = json.loads(body)
        self.assertTrue(d["reset"])
        self.assertEqual(len(d["changes"]), 3)
        self.assertEqual(self.get("/api/changes?after=x")[0].status, 400)

    def test_seen(self):
        ids = [r[0] for r in self.con.execute("SELECT id FROM products ORDER BY id")]
        r, body = self.get("/api/seen?ids=" + ",".join(map(str, ids + [99999])) + ",junk")
        d = json.loads(body)
        self.assertEqual(set(d["seen"]), {str(i) for i in ids})
        self.assertEqual(d["seen"][str(ids[-1])], self.con.execute("SELECT last_seen FROM products WHERE id=?",
                                                                    (ids[-1],)).fetchone()[0])
        self.assertIsInstance(d["now"], int)
        r, body = self.get("/api/seen?ids=" + ",".join(str(i) for i in range(1, 200)))
        self.assertLessEqual(len(json.loads(body)["seen"]), web.SEEN_MAX)
        self.assertEqual(json.loads(self.get("/api/seen")[1])["seen"], {})

    def test_static_files_revalidate(self):
        r, body = self.get("/", **{"Accept-Encoding": "gzip"})
        self.assertEqual(r.status, 200)
        self.assertEqual(body, web.PAGE.read_bytes())
        self.assertEqual((r.getheader("Cache-Control"), r.getheader("Content-Encoding")), ("no-cache", "gzip"))
        etag = r.getheader("ETag")
        r, body = self.get("/", **{"If-None-Match": etag})
        self.assertEqual((r.status, body), (304, b""))
        r, _ = self.get("/sw.js", **{"If-None-Match": etag})
        self.assertEqual(r.status, 200)  # another file, another ETag
        self.assertEqual(r.getheader("Service-Worker-Allowed"), "/")
        r, body = self.get("/tokens.css")  # the design tokens the page and the server-written pages share
        self.assertEqual((r.status, r.getheader("Content-Type"), r.getheader("Cache-Control")), (200, "text/css; charset=utf-8", "no-cache"))
        self.assertIn(b"--surface:", body)
        self.assertIn(b'href="/tokens.css"', web.PAGE.read_bytes())
        r, body = self.get("/icon-192.png", **{"Accept-Encoding": "gzip"})
        self.assertIsNone(r.getheader("Content-Encoding"))  # pictures are compressed already
        self.assertEqual(body[:4], b"\x89PNG")

    def test_a_deployed_file_is_read_again(self):
        f = Path(self.dir.name) / "page.html"
        f.write_text("one")
        body, gz, etag = web.static_file(f)
        self.assertEqual((body, gzip.decompress(gz)), (b"one", b"one"))
        self.assertIs(web.static_file(f)[0], body)  # from memory
        f.write_text("two!")
        body2, _, etag2 = web.static_file(f)
        self.assertEqual(body2, b"two!")
        self.assertNotEqual(etag, etag2)

    def test_product_and_brief_etags(self):
        pid = self.con.execute("SELECT id FROM products WHERE sku='a1'").fetchone()[0]
        r, body = self.get(f"/api/product/{pid}")
        self.assertEqual(json.loads(body)["item"]["id"], pid)
        self.assertEqual(self.get(f"/api/product/{pid}", **{"If-None-Match": r.getheader("ETag")})[0].status, 304)
        r, body = self.get("/api/brief")
        self.assertIn("deals", json.loads(body))
        self.assertEqual(self.get("/api/brief", **{"If-None-Match": r.getheader("ETag")})[0].status, 304)
        r, body = self.get("/api/status")
        self.assertEqual(set(json.loads(body)), {"status", "changes_last_hour"})
        self.assertEqual(self.get("/api/status", **{"If-None-Match": r.getheader("ETag")})[0].status, 304)

    def test_a_post_closes_the_connection(self):
        self.http.request("POST", "/api/push/test", body=b"{}", headers={"Content-Type": "application/json"})
        r = self.http.getresponse()
        r.read()
        self.assertEqual((r.status, r.getheader("Connection")), (422, "close"))
        r, _ = self.get("/healthz")  # http.client reconnects by itself
        self.assertEqual(r.status, 200)


if __name__ == "__main__":
    unittest.main()
