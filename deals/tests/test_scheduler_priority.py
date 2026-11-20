"""The live scheduler's priorities (deals/priority.py), on a fake clock: each page 'takes' one store delay."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from deals import db, priority  # noqa: E402
from deals.models import Offer  # noqa: E402
from deals.scheduler import StoreLoop  # noqa: E402
from deals.tasks import Task  # noqa: E402

STEP = 3.0  # seconds per request, like most stores


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class Sim:
    """`n` shelves of 5 products each; `price(shelf, visit, time)` is what that visit finds."""

    def __init__(self, n=200, price=lambda shelf, visit, t: 10, watchlist=None):
        self.clock = Clock()
        self.visits = []  # (time, shelf)
        self.count = {}
        self.price = price

        def page(i):
            def run():
                self.clock.t += STEP
                k = self.count[i] = self.count.get(i, 0) + 1
                self.visits.append((self.clock.t, i))
                price = self.price(i, k, self.clock.t)
                return [Offer("talabat", f"s{i}-{j}", f"P {i}/{j}", price) for j in range(5)], []
            return Task(f"shelf/{i}", run)

        self.loop = StoreLoop("talabat", lambda f: [page(i) for i in range(n)], ":memory:", delay=0,
                              hot_every=60, clock=self.clock, sleep=self.clock.sleep, log=lambda *_: None,
                              watchlist=watchlist)
        self.loop.con = db.connect(":memory:")

    def close(self):
        self.loop.con.close()

    def run(self, seconds):
        end = self.clock.t + seconds
        while self.clock.t < end:
            self.loop.step()

    def times(self, shelf, since=0.0):
        return [t for t, i in self.visits if i == shelf and t >= since]

    def watch(self, *skus, bot=False):
        con = self.loop.con
        ids = [con.execute("SELECT id FROM products WHERE sku=?", (s,)).fetchone()[0] for s in skus]
        with con:
            if bot:
                con.execute("CREATE TABLE IF NOT EXISTS bot_watch (chat INTEGER, product_id INTEGER, price REAL,"
                            " at INTEGER, predicted INTEGER DEFAULT 0, PRIMARY KEY (chat, product_id))")
                con.executemany("INSERT INTO bot_watch (chat, product_id, price, at) VALUES (1, ?, 10, 0)",
                                [(i,) for i in ids])
            else:
                con.execute("INSERT INTO push_subs (endpoint, subscription, prefs) VALUES (?, '{}', ?)",
                            (f"https://x/{len(skus)}/{ids[0]}", json.dumps({"watch": ids})))


def gaps(times):
    return [b - a for a, b in zip(times, times[1:])]


class Base(unittest.TestCase):
    def sim(self, *a, **kw):
        s = Sim(*a, **kw)
        self.addCleanup(s.close)
        return s


class WatchedShelves(Base):
    def test_a_watched_shelf_comes_round_every_5_minutes(self):
        sim = self.sim(200)  # a cold cycle is 200 x 3 s = 10 minutes
        sim.run(700)  # one sweep: every page and its products are known
        sim.watch("s7-2", "s150-0")
        start = sim.clock.t + 61  # once the watch list has been read again
        sim.run(3600)
        for shelf in (7, 150):
            t = sim.times(shelf, start)
            self.assertGreaterEqual(len(t), 11)
            self.assertLessEqual(max(gaps(t)), priority.WATCH_EVERY + 2 * STEP)
        self.assertEqual(sim.loop.watched_shelves, 2)
        quiet = gaps(sim.times(100, start))  # an unwatched page still only comes with the sweep
        self.assertGreater(min(quiet), 500)

    def test_a_new_watch_is_picked_up_within_a_minute(self):
        sim = self.sim(200)
        sim.run(700)
        self.assertFalse(sim.loop.tasks["shelf/42"].watched)
        sim.watch("s42-4", bot=True)  # a Telegram bot watch this time
        sim.run(61 + 2 * STEP)
        self.assertTrue(sim.loop.tasks["shelf/42"].watched)
        sim.run(priority.WATCH_EVERY)
        self.assertTrue(sim.times(42, sim.clock.t - priority.WATCH_EVERY))
        # and a watch that goes away is dropped at the next reload
        with sim.loop.con:
            sim.loop.con.execute("DELETE FROM bot_watch")
        sim.run(61 + 2 * STEP)
        self.assertFalse(sim.loop.tasks["shelf/42"].watched)

    def test_a_predicted_sale_counts_as_watched(self):
        sim = self.sim(50)
        sim.run(200)
        con, now = sim.loop.con, int(sim.clock.t)
        pid = con.execute("SELECT id FROM products WHERE sku='s9-1'").fetchone()[0]
        day = 86400
        # on sale for 2 days every 10 days at 7 instead of 10; the last one began 9.5 days ago: the next is due
        history = []
        for start in (29.5, 19.5, 9.5):
            history += [(now - int(start * day), 7.0), (now - int((start - 2) * day), 10.0)]
        with con:
            con.execute("DELETE FROM prices WHERE product_id=?", (pid,))
            con.executemany("INSERT INTO prices (product_id, at, price, original_price, in_stock) VALUES (?,?,?,NULL,1)",
                            [(pid, at, p) for at, p in [(now - 40 * day, 10.0)] + history])
        self.assertEqual(priority.due_ids(con, now), {pid})
        sim.loop.watchlist.loaded = sim.loop.watchlist.due_loaded = None  # read it again now
        sim.run(2 * STEP)
        self.assertTrue(sim.loop.tasks["shelf/9"].watched)


N = 600  # shelves in the busy store: a 30-minute cold cycle at 3 s


class NoStarvation(Base):
    def busy(self):
        # shelves 0-2 move on every visit (always hot), 10-15 once an hour (volatile), 60-69 are watched
        def price(i, k, t):
            if i < 3:
                return 10 + k % 2
            if 10 <= i < 16:
                return 10 + ((t + i * 600) // 3600) % 2
            return 10
        sim = self.sim(N, price=price)
        sim.run(4 * 3600)  # long enough for the hourly shelves to show their rhythm
        sim.watch(*[f"s{i}-0" for i in range(60, 70)])
        return sim

    def test_every_cold_shelf_is_checked_once_per_full_cycle(self):
        sim = self.busy()
        cold = N * STEP  # 30 minutes with nothing prioritised
        for _ in range(4):
            start = sim.loop.cycle_start
            seen_cycle = sim.loop.cycle_secs
            while sim.loop.cycle_start == start:
                sim.loop.step()
            seen = {i for t, i in sim.visits if t >= start}
            self.assertEqual(seen, set(range(N)))
            self.assertLessEqual(sim.loop.cycle_secs, 2 * cold + 2 * STEP)
            self.assertIsNot(sim.loop.cycle_secs, seen_cycle)
        # the cold pages are in every cycle exactly when the sweep reaches them: about one full cycle apart
        self.assertLessEqual(max(gaps(sim.times(250))), 2 * cold + 2 * STEP)
        self.assertGreater(min(gaps(sim.times(250))), cold - 60)  # and only then: no extra visits

    def test_requests_per_minute_are_unchanged(self):
        sim = self.busy()
        t0, n0 = sim.clock.t, len(sim.visits)
        steps = 2000
        for _ in range(steps):
            sim.loop.step()
        self.assertEqual(len(sim.visits) - n0, steps)  # one page per step, never more, never a skipped turn
        self.assertAlmostEqual(sim.clock.t - t0, steps * STEP)  # i.e. 60 / STEP requests a minute, as before
        # the priority turns only ever take every second request
        order = [i for _, i in sim.visits[n0:]]
        self.assertGreaterEqual(sum(1 for i in order if i >= 70), steps // 2 - 200)

    def test_hot_and_volatile_shelves_come_sooner(self):
        sim = self.busy()
        start = sim.clock.t
        sim.run(3600)
        hot = sorted(gaps(sim.times(1, start)))  # hot: about every minute; at worst 2 when the priority turns are crowded
        self.assertLessEqual(hot[len(hot) // 2], 60 + 2 * STEP)
        self.assertLessEqual(hot[-1], 2 * 60 + 2 * STEP)
        for i in range(10, 16):
            self.assertIsNotNone(priority.volatile_target(sim.loop.tasks[f"shelf/{i}"], sim.clock.t))
            self.assertLessEqual(max(gaps(sim.times(i, start))), priority.VOLATILE_SLOW + 4 * STEP)
        watched = sorted(gaps(sim.times(65, start + 61)))
        self.assertLessEqual(watched[len(watched) // 2], 1.1 * priority.WATCH_EVERY)
        self.assertLessEqual(watched[-1], 1.25 * priority.WATCH_EVERY)  # crowded turns share the slip by lateness
        self.assertGreater(sim.loop.volatile_shelves, 0)


class ChangeRate(Base):
    def test_running_average(self):
        hourly, calm, busy = Task("a", None), Task("b", None), Task("c", None)
        t = 0.0
        for k in range(24 * 60):  # a day of checks once a minute
            t += 60
            priority.observe(hourly, k % 60 == 0, t)  # one change an hour
            priority.observe(calm, False, t)
            priority.observe(busy, k % 10 == 0, t)  # six an hour
        self.assertAlmostEqual(priority.rate(hourly, t), 1.0, delta=0.2)
        self.assertEqual(priority.rate(calm, t), 0.0)
        self.assertAlmostEqual(priority.rate(busy, t), 6.0, delta=0.6)
        self.assertEqual(priority.volatile_target(busy, t), priority.VOLATILE_FAST)
        self.assertIsNone(priority.volatile_target(calm, t))
        self.assertTrue(priority.VOLATILE_FAST < priority.volatile_target(hourly, t) < priority.VOLATILE_SLOW)
        # looking less often doesn't change the answer much: the same hourly shelf checked every 10 minutes
        sparse = Task("d", None)
        for k in range(24 * 6):
            priority.observe(sparse, k % 6 == 0, (k + 1) * 600)
        self.assertAlmostEqual(priority.rate(sparse, t), priority.rate(hourly, t), delta=0.2)
        # a shelf that calms down stops counting as volatile within a few hours
        self.assertIsNotNone(priority.volatile_target(busy, t + 3600))
        self.assertIsNone(priority.volatile_target(busy, t + 10 * 3600))

    def test_one_change_alone_is_not_volatile(self):
        t = Task("a", None)
        priority.observe(t, True, 1000.0)
        self.assertIsNone(priority.volatile_target(t, 1000.0))  # one change in a day is just "hot" for a while

    def test_a_broken_watch_list_never_stops_checking(self):
        class Broken:
            version = 0

            def get(self, now):
                raise RuntimeError("database is locked")

        sim = self.sim(10, watchlist=Broken())
        sim.run(60)
        self.assertEqual(len(sim.visits), 20)


if __name__ == "__main__":
    unittest.main()
