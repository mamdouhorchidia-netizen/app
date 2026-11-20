"""/api/deals sends the list shape (only what the live page reads from a list item); ?full=1 the whole item.
Its ETag changes exactly when something in that shape changes, not when only time or an unsent field moves."""

import http.client
import json
import os
import re
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from deals import db, livedata, web
from deals.models import Offer
from test_live import seed

LIVE = Path(web.__file__).with_name("live.html").read_text(encoding="utf-8")
URL = "/api/deals?deals=0&stock=all&limit=60"


def js_function(name: str) -> str:
    """The source of a top-level `function name(…) {…}` or `async function` in live.html (to its closing brace)."""
    m = re.search(r"^(?:async )?function " + name + r"\(.*?^}", LIVE, re.S | re.M)
    if not m:
        raise AssertionError(f"{name} not found in live.html")
    return m.group(0)


class ListShape(unittest.TestCase):
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
        self.keeper.get()

    def tearDown(self):
        self.http.close()
        self.server.shutdown()
        self.server.server_close()
        self.keeper.stop()
        self.con.close()
        self.dir.cleanup()

    def get(self, path, etag=None):
        self.http.request("GET", path, headers={"If-None-Match": etag} if etag else {})
        r = self.http.getresponse()
        body = r.read()
        return r.status, r.getheader("ETag"), json.loads(body) if body else None

    def rewrite(self, sku, **change):
        """The product row as a store check would write it again, with `change` applied; then a catalog refresh."""
        r = self.con.execute("SELECT * FROM products WHERE sku=?", (sku,)).fetchone()
        o = dict(store=r["store"], sku=r["sku"], name=r["name"], price=r["price"], original_price=r["original_price"],
                 url=r["url"], image=r["image"], category=r["category"], brand=r["brand"], in_stock=bool(r["in_stock"]),
                 extra=json.loads(r["extra"] or "{}"))
        o.update(change)
        db.save_offers(self.con, [Offer(**o)])
        self.keeper.rebuild()

    def test_only_the_fields_the_page_reads(self):
        _, _, slim = self.get(URL)
        _, _, full = self.get(URL + "&full=1")
        self.assertEqual([d["id"] for d in slim["items"]], [d["id"] for d in full["items"]])
        allowed = set(web.LIST_FIELDS) | {"links", "url"}
        for s, f in zip(slim["items"], full["items"]):
            self.assertLessEqual(set(s), allowed)
            for k in ("sku", "category", "first_seen", "tracked_days", "spark", "why", "rhythm", "recent_low_at",
                      "score", "brand", "top_category"):
                self.assertNotIn(k, s)
                self.assertIn(k, f)  # still there for whoever asks for the whole item
            for k, v in s.items():
                if k != "links":
                    self.assertEqual(v, f[k], k)
            for k, v in f.items():  # only empty values are left out of the fields it keeps
                if k in web.LIST_FIELDS and v is not None:
                    self.assertIn(k, s)
            self.assertEqual(s["links"]["web"], f["links"]["web"])
            for k in ("android", "ios"):  # the page falls back to "web" when one is missing
                self.assertEqual(s["links"].get(k, s["links"]["web"]), f["links"][k] or f["links"]["web"])
        self.assertLess(len(json.dumps(slim["items"])), len(json.dumps(full["items"])) / 2)
        for k in ("total", "facets", "labels", "status", "now", "v"):
            self.assertEqual(slim[k] if k != "now" else 0, full[k] if k != "now" else 0)

    def test_the_page_reads_nothing_else_from_list_items(self):
        """Every `d.field` the cards, the patcher, the watchlist check and the basket read is in the list shape
        (the product view reads /api/product/ID's item, the whole one)."""
        read = set()
        for fn in ("partsOf", "card", "verdictChip", "patchCard", "makeCard", "toggleWatch", "checkWatch",
                   "showBasket", "took", "appHref"):
            src = js_function(fn) if fn != "appHref" else re.search(r"const appHref = .*", LIVE).group(0)
            read |= set(re.findall(r"\bd\??\.(\w+)", src))
        self.assertIn("price", read)
        read -= {"links"}  # an object: its keys are checked in test_only_the_fields_the_page_reads
        self.assertLessEqual(read, set(web.LIST_FIELDS) | {"url"})

    def test_etag_follows_what_the_list_shows(self):
        _, tag, _ = self.get(URL)
        _, full_tag, _ = self.get(URL + "&full=1")
        self.assertNotEqual(tag, full_tag)
        # time passing (a rebuild later) and a fresh check (last_seen) are not changes
        with mock.patch.object(livedata.time, "time", return_value=time.time() + 120):
            self.keeper.rebuild()
        self.assertEqual(self.get(URL, tag)[0], 304)
        with self.con:
            self.con.execute("UPDATE products SET last_seen=last_seen+30")
        self.keeper.rebuild()
        self.assertEqual(self.get(URL, tag)[0], 304)
        # a field the list doesn't carry (the brand): a new full item, the same list
        self.rewrite("a2", brand="Acme")
        self.assertEqual(self.get(URL, tag)[0], 304)
        status, full_tag2, _ = self.get(URL + "&full=1", full_tag)
        self.assertEqual(status, 200)
        self.assertNotEqual(full_tag2, full_tag)
        # what a card shows: the price, the name, the picture
        for change in ({"price": 70}, {"name": "Steady Soap 120g"}, {"image": "https://img/soap.jpg"}):
            status, tag2, body = self.get(URL, tag)
            self.assertEqual(status, 304)
            self.rewrite("a2", **change)
            status, tag2, body = self.get(URL, tag)
            self.assertEqual(status, 200, change)
            self.assertNotEqual(tag2, tag)
            soap = next(d for d in body["items"] if d["id"] == next(
                x["id"] for x in body["items"] if x["store"] == "amazon_eg" and "Soap" in x["name"]))
            k, v = next(iter(change.items()))
            self.assertEqual(soap[k], v)
            tag = tag2


class ListDigest(unittest.TestCase):
    def test_a_function_of_the_full_digest_and_blind_to_the_clock(self):
        d = {"id": 1, "store": "talabat", "name": "Milk", "price": 10, "in_stock": True, "sku": "x", "last_seen": 5,
             "verdict": {"label": "Buy now", "tone": "good", "why": "Lowest in 90 days."},
             "links": {"web": "https://w", "android": "intent://w", "ios": "https://w", "browser": "b"}}
        a = web.list_digest(d, b"k1")
        self.assertEqual(web.list_item(d)["links"], {"web": "https://w", "android": "intent://w"})
        self.assertEqual(web.list_digest(dict(d, last_seen=99, sku="y", verdict={**d["verdict"], "why": "Later."}),
                                         b"k2"), a)
        self.assertNotEqual(web.list_digest(dict(d, price=9), b"k3"), a)
        self.assertNotEqual(web.list_digest(dict(d, verdict={**d["verdict"], "label": "Wait"}), b"k4"), a)
        self.assertEqual(web.list_item(dict(d, links={"web": ""}, url="https://u"))["url"], "https://u")


if __name__ == "__main__":
    unittest.main()
