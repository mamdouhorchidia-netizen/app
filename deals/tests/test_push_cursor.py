"""push.Notifier reads price rows by rowid, so none is missed when it lands in the same second as a look."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deals import db, push  # noqa: E402
from deals.models import Offer  # noqa: E402


class RowidCursor(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "d.sqlite3")
        self.con = db.connect(self.path)
        db.save_offers(self.con, [Offer("talabat", "a", "Milk", 50), Offer("talabat", "b", "Tea", 100)], 1000)
        self.con.execute("INSERT INTO push_subs (endpoint, subscription, prefs, created) VALUES (?,?,?,0)",
                         ("https://fcm.googleapis.com/x", json.dumps({"endpoint": "https://fcm.googleapis.com/x"}),
                          json.dumps({"all_drops": True})))
        self.con.commit()
        self.sent = []

    def tearDown(self):
        self.con.close()
        self.dir.cleanup()

    def sender(self, vapid, sub, msg):
        self.sent.append(msg)
        return 201

    def notifier(self):
        return push.Notifier(self.path, {}, log=lambda *_: None)

    def test_starts_after_the_rows_already_there(self):
        n = self.notifier()
        self.assertEqual(n.last_rowid, 2)
        n.tick(now=1100, sender=self.sender)
        self.assertEqual(self.sent, [])  # old history is not news

    def test_a_row_written_in_the_same_second_after_a_look_is_alerted(self):
        n = self.notifier()
        db.save_offers(self.con, [Offer("talabat", "a", "Milk", 40)], 2000)
        n.tick(now=2000, sender=self.sender)
        self.assertEqual([m["title"] for m in self.sent], ["↓ 20%  Milk"])
        # same second, just after that look: the old `at > last; last = now` cursor never saw this row
        db.save_offers(self.con, [Offer("talabat", "b", "Tea", 90)], 2000)
        n.tick(now=2100, sender=self.sender)  # found and waiting for the subscriber's 300 s gap
        self.assertEqual(len(self.sent), 1)
        n.tick(now=2300, sender=self.sender)
        self.assertEqual([m["title"] for m in self.sent], ["↓ 20%  Milk", "↓ 10%  Tea"])
        n.tick(now=2700, sender=self.sender)
        self.assertEqual(len(self.sent), 2)  # each row once

    def test_rows_are_read_once_and_the_cursor_moves_to_the_newest(self):
        n = self.notifier()
        db.save_offers(self.con, [Offer("talabat", "a", "Milk", 40)], 2000)
        self.assertEqual(n.advance(self.con), (2, 3))
        self.assertEqual(n.advance(self.con), (3, 3))
        self.assertEqual(n.drops_since(self.con, 3, 3), [])

    def test_a_cursor_past_the_newest_row_resets_to_it(self):
        n = self.notifier()
        n.last_rowid = 999  # e.g. a VACUUM renumbered the rows
        self.assertEqual(n.advance(self.con), (2, 2))
        self.assertEqual(n.last_rowid, 2)
        db.save_offers(self.con, [Offer("talabat", "a", "Milk", 40)], 2000)
        n.tick(now=2000, sender=self.sender)
        self.assertEqual(len(self.sent), 1)  # back to normal from there

    def test_a_fresh_database_starts_at_zero_and_an_unknown_start_waits_for_the_first_tick(self):
        with tempfile.TemporaryDirectory() as d:
            n = push.Notifier(os.path.join(d, "new.sqlite3"), {}, log=lambda *_: None)
            self.assertEqual(n.last_rowid, 0)
        n = self.notifier()
        n.last_rowid = None
        self.assertEqual(n.advance(self.con), (2, 2))


if __name__ == "__main__":
    unittest.main()
