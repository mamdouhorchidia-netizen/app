"""What a run against the real stores (Nov 2026) showed, kept as tests: /api/pulse's ETag must not change just
because a store checked another page."""

import http.client
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from deals import db, livedata, web  # noqa: E402
from deals.models import Offer  # noqa: E402


class PulseETag(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "d.sqlite3")
        self.con = db.connect(self.path)
        now = int(time.time())
        db.save_offers(self.con, [Offer("talabat", "a", "Milk 1L", 50), Offer("raya", "b", "TV 55", 20000, 25000)], now)
        db.write_live_status(self.con, "talabat", updated=now, last_change=0, pages=1, changes=0, tasks=500,
                             cycle_secs=None, error=None, paused_until=0)
        self.con.commit()
        self.keeper = livedata.CatalogKeeper(self.path, livedata.Version(self.path, every=0), poll=3600)
        self.keeper.get()
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
        return r, r.read()

    def status(self, **fields):
        with self.con:
            db.set_live_status(self.con, "talabat", **fields)

    def test_a_page_checked_without_news_keeps_the_pulse(self):
        r, _ = self.get("/api/pulse")
        tag = r.getheader("ETag")
        # another page checked, nothing found: only `updated`, `pages` and (a page 2 found) the page count move
        self.status(updated=int(time.time()) + 5, pages=2, tasks=501)
        self.assertEqual(self.get("/api/pulse", **{"If-None-Match": tag})[0].status, 304)
        # what the page shows still counts: a change found, a finished sweep, an error or a pause
        for fields in ({"last_change": int(time.time())}, {"cycle_secs": 840.0}, {"error": "x: boom"},
                       {"paused_until": int(time.time()) + 1800}):
            self.status(**fields)
            r, _ = self.get("/api/pulse", **{"If-None-Match": tag})
            self.assertEqual(r.status, 200, fields)
            tag = r.getheader("ETag")
        # and a price row moves `v`
        db.save_offers(self.con, [Offer("talabat", "a", "Milk 1L", 45)])
        self.assertEqual(self.get("/api/pulse", **{"If-None-Match": tag})[0].status, 200)

    def test_an_idle_stores_last_run_time_still_counts(self):
        view = web.pulse_etag_view({"v": 1, "status": [
            {"store": "a", "live": True, "updated": 5, "pages_per_cycle": 9, "last_change": 3},
            {"store": "b", "live": False, "updated": 7}]})
        self.assertEqual(view["status"], [{"store": "a", "live": True, "last_change": 3},
                                          {"store": "b", "live": False, "updated": 7}])


if __name__ == "__main__":
    unittest.main()
