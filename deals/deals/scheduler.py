"""Continuous checking: each store's pages are revisited nonstop at that store's polite pace.

- Cold pages: whichever was checked longest ago goes next, so every shelf comes round in one cycle
  (talabat ~550 pages at 1.5 s = ~14 min).
- Pages that matter more get a target interval (priority.py): hot (a price moved in the last 30 minutes)
  every `hot_every` seconds (default 60), watched (a product on it is watched or has a sale due) every 5 min,
  volatile (its checks keep finding changes) every 5-15 min. Turns alternate: a priority turn takes the page
  most overdue for its target ((now - last check) / target, only pages at or past it), the next turn the page
  checked longest ago of all. So a busy day can at most double the cycle, never stall it, and the store is
  asked exactly as often as before: only the order of pages changes.
- A bot check pauses that store for 30 minutes; an expired Breadfast session pauses it until a new
  capture is imported. Each store runs on its own thread with its own Fetcher.
"""

import os
import time
import traceback

from . import db, priority
from .http import Blocked, Fetcher
from .models import NotConfigured, SessionExpired
from .tasks import Task

HOT_WINDOW = 30 * 60
BLOCKED_PAUSE = 30 * 60
ERROR_PAUSE = 5 * 60


class StoreLoop:
    def __init__(self, store: str, plan, db_path: str, delay: float, hot_every: float = 60,
                 replan_every: float = 6 * 3600, watch_file: str = None, replan_file: str = None,
                 clock=time.time, sleep=time.sleep, log=print, watchlist=None):
        self.store, self.plan, self.db_path = store, plan, db_path
        self.fetcher = Fetcher(delay=delay)
        self.hot_every, self.replan_every, self.watch_file = hot_every, replan_every, watch_file
        self.clock, self.sleep, self.log = clock, sleep, log
        self.tasks: dict[str, Task] = {}
        self.planned_at = 0.0
        self.paused_until = 0.0
        self.error = None
        self.hot_turn = False
        self.cycle_start = 0.0
        self.cycle_secs = None
        self.pages = self.changes = 0
        self.blocks = 0  # bot checks in a row
        self.last_change = 0.0
        self.watch_mtime = self._mtime()
        self.replan_file = replan_file  # when this file changes (settings), the store's pages are listed afresh
        self.replan_mtime = self._file_mtime(replan_file)
        self.con = None
        self.watchlist = watchlist  # None: the database's shared one (made on first use)
        self.watch_version = None
        self.watched_shelves = self.volatile_shelves = 0  # how many pages each rule covers (for the log)
        self.counted_at = float("-inf")

    @staticmethod
    def _file_mtime(path):
        try:
            return os.path.getmtime(path) if path else None
        except OSError:
            return None

    def _mtime(self):
        try:
            return os.path.getmtime(self.watch_file) if self.watch_file else None
        except OSError:
            return None

    def _pause(self, seconds: float, why: str):
        self.paused_until = self.clock() + seconds
        self.error = why
        self.log(f"{self.store}: {why}; pausing {int(seconds // 60)} min")

    def replan(self):
        now = self.clock()
        tasks = self.plan(self.fetcher)
        old = self.tasks
        self.tasks = {t.key: old.get(t.key, t) for t in tasks}
        for t in old.values():  # keep discovered follow-ups (pages 2..n, sub-lists) with their timings
            self.tasks.setdefault(t.key, t)
        self.planned_at = now
        if not self.cycle_start:
            self.cycle_start = now

    def _watched(self, now: float) -> set:
        """This store's watched skus; when the set changed, every page is marked again."""
        if self.watchlist is None:
            self.watchlist = (priority.Watchlist(con=self.con, log=self.log) if self.db_path == ":memory:"
                              else priority.shared(self.db_path))
        try:
            skus = self.watchlist.get(now).get(self.store) or set()
        except Exception as e:  # the order of pages is a nicety: never let it stop the checking
            self.log(f"{self.store}: watch list: {type(e).__name__}: {e}")
            return set()
        if self.watchlist.version != self.watch_version:
            self.watch_version = self.watchlist.version
            for t in self.tasks.values():
                t.watched = bool(t.skus & skus)
            self._count()
        return skus

    def _count(self):
        watched = sum(1 for t in self.tasks.values() if t.watched)
        now = self.clock()
        volatile = sum(1 for t in self.tasks.values() if priority.volatile_target(t, now) is not None)
        changed = (watched, volatile) != (self.watched_shelves, self.volatile_shelves)
        self.watched_shelves, self.volatile_shelves = watched, volatile
        if changed and now - self.counted_at >= 600:  # a line in the log now and then, not every minute
            self.counted_at = now
            self.log(f"{self.store}: {watched} watched and {volatile} busy pages rechecked sooner")

    def pick(self, now: float):
        if not self.tasks:
            return None
        self.hot_turn = not self.hot_turn  # a priority turn, then a sweep turn
        if self.hot_turn:
            best, score = None, 1.0
            for t in self.tasks.values():
                want = priority.target(t, now, self.hot_every, HOT_WINDOW)
                if want is not None and (now - t.last_run) / want >= score:
                    best, score = t, (now - t.last_run) / want
            if best is not None:
                return best
        return min(self.tasks.values(), key=lambda t: t.last_run)

    def step(self):
        now = self.clock()
        if self.paused_until > now:
            mtime = self._mtime()
            if mtime and mtime != self.watch_mtime:  # a new capture was imported: try again now
                self.watch_mtime, self.paused_until = mtime, 0
            else:
                self.sleep(min(self.paused_until - now, 30))
                return
        if self.replan_file:
            m = self._file_mtime(self.replan_file)
            if m != self.replan_mtime:  # e.g. other hubs chosen: drop the old pages, list the new ones
                self.replan_mtime, self.tasks, self.planned_at, self.paused_until = m, {}, 0.0, 0.0
                self.log(f"{self.store}: settings changed, listing pages again")
        if not self.tasks and self.planned_at and now - self.planned_at < 300:
            self.sleep(30)  # the store listed nothing a moment ago: don't ask again right away
            return
        try:
            if not self.tasks or now - self.planned_at > self.replan_every:
                self.replan()
        except NotConfigured as e:
            self._pause(3600, str(e))
            return
        except Exception as e:
            self._pause(ERROR_PAUSE, f"could not list pages: {type(e).__name__}: {e}")
            return
        watched = self._watched(now)
        task = self.pick(now)
        if task is None:
            self.sleep(5)
            return
        try:
            offers, follow = task.run()
        except Blocked as e:
            # repeated bot checks mean "slow down": 30 min, then 1 h, 2 h, 4 h at most; reset after a clean page
            self._pause(min(BLOCKED_PAUSE * 2 ** self.blocks, 4 * 3600), str(e))
            self.blocks += 1
            self._status()
            return
        except SessionExpired as e:  # wait for a new capture (or an hour)
            self._pause(3600, str(e))
            self._status()
            return
        except Exception as e:
            task.last_run, task.fails = self.clock(), task.fails + 1
            self.error = f"{task.key}: {type(e).__name__}: {e}"
            self.log(f"{self.store}: {self.error}")
            if task.fails >= 3:
                self.tasks.pop(task.key, None)  # gone from the store; the next replan brings it back if not
            return
        offers = list(offers)  # read twice: saved, then remembered as this page's products
        done = self.clock()
        self.blocks = 0
        stats = {}
        with self.con:  # the page's prices and the store's status in one commit
            db.write_offers(self.con, offers, int(done), stats)
            task.last_run, task.fails = done, 0
            task.skus = frozenset(o.sku for o in offers)
            task.watched = bool(task.skus & watched)
            priority.observe(task, bool(stats["moved"]), done)
            self.pages += 1
            if stats["moved"]:  # a price or stock really changed (a first sighting doesn't count)
                task.last_change = self.last_change = done
                self.changes += stats["moved"]
            elif stats.get("held"):  # a big jump waiting for a second look: recheck this page within a minute
                task.last_change = done
            for f in follow:
                self.tasks.setdefault(f.key, f)
            if self.pages % 50 == 0:
                self._count()
            self.error = None
            if all(t.last_run >= self.cycle_start for t in self.tasks.values()):
                self.cycle_secs = done - self.cycle_start
                self.cycle_start = done
            db.write_live_status(self.con, self.store, **self._status_fields())

    def _status_fields(self) -> dict:
        return dict(updated=int(self.clock()), last_change=int(self.last_change), pages=self.pages, changes=self.changes,
                    tasks=len(self.tasks), cycle_secs=self.cycle_secs, error=self.error,
                    paused_until=int(self.paused_until))

    def _status(self):
        db.set_live_status(self.con, self.store, **self._status_fields())

    def run_forever(self):
        self.con = db.connect(self.db_path)
        while True:
            try:
                self.step()
            except Exception:  # never let one bad page end the store's thread
                traceback.print_exc()
                self.sleep(10)
