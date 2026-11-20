"""Which shelves matter most right now: what the live scheduler uses to choose the next page to recheck.

Only the order of pages changes here, never how often a store is asked: each store still gets one request
at a time at its own delay (see scheduler.py).

- A shelf's *target* is how often it should ideally be rechecked: hot (a price moved in the last 30 min)
  every `hot_every` (60 s); watched (one of its products is on a web-push ★ watchlist or followed in the
  Telegram bot, or has a sale predicted within a day) every 5 min; volatile (its checks keep finding changes:
  1 every 2 h or more) every 15 min down to 5 min at 2 an hour; anything else has no target and just comes round in the sweep.
- The watch set is read from the database at most once a minute, shared by every store thread; the
  predicted sales (forecast.rhythm over every product with some history) at most once an hour.
"""

import json
import math
import sqlite3
import threading
import time

from . import forecast

DAY = 86400
WATCH_EVERY = 5 * 60
VOLATILE_FAST, VOLATILE_SLOW = 5 * 60, 15 * 60
VOLATILE_MIN, VOLATILE_FULL = 0.5, 2.0  # changes an hour where a shelf starts counting as volatile / is fastest
RATE_TAU = 4 * 3600  # the running average forgets with this time constant (a change counts half after ~2.8 h)


def rate(task, now: float) -> float:
    """Checks of this shelf that found a change, per hour, as an exponentially fading average."""
    if not task.change_rate:
        return 0.0
    return task.change_rate * math.exp(-max(0.0, now - task.rate_at) / RATE_TAU)


def observe(task, changed: bool, now: float):
    """After each check of the shelf. The average is over time, not over checks: the share of checks that
    find a change would fall each time the scheduler rechecks the shelf more often (a hot shelf rechecked
    every minute finds nothing new on most checks), so a shelf would lose its target just for having it.
    Changes an hour doesn't depend on how often we look (as long as we look more often than it changes)."""
    task.change_rate = rate(task, now) + (3600.0 / RATE_TAU if changed else 0.0)
    task.rate_at = now


def volatile_target(task, now: float):
    """5–15 min for a shelf whose checks keep finding changes (VOLATILE_MIN..VOLATILE_FULL an hour), None
    for a calmer one."""
    r = rate(task, now)
    if r < VOLATILE_MIN:
        return None
    x = min(1.0, (r - VOLATILE_MIN) / (VOLATILE_FULL - VOLATILE_MIN))
    return VOLATILE_SLOW - x * (VOLATILE_SLOW - VOLATILE_FAST)


def target(task, now: float, hot_every: float, hot_window: float):
    """The shortest interval any rule asks for, or None (only the sweep)."""
    out = []
    if task.last_change and now - task.last_change < hot_window:
        out.append(hot_every)
    if task.watched:
        out.append(WATCH_EVERY)
    v = volatile_target(task, now)
    if v is not None:
        out.append(v)
    return min(out) if out else None


# ---- the watch set ----------------------------------------------------------------------------------

def watched_ids(con) -> set:
    """Product ids on any web-push watchlist or Telegram bot watch."""
    ids = set()
    try:
        for (prefs,) in con.execute("SELECT prefs FROM push_subs"):
            try:
                ids.update(int(x) for x in (json.loads(prefs or "{}").get("watch") or []))
            except (ValueError, TypeError, AttributeError):
                continue
    except sqlite3.OperationalError:
        pass
    try:  # the bot makes its table on first use
        ids.update(r[0] for r in con.execute("SELECT DISTINCT product_id FROM bot_watch"))
    except sqlite3.OperationalError:
        pass
    return ids


def due_ids(con, now: int, within_days: float = 1.0) -> set:
    """Product ids whose next sale is predicted within `within_days`, by the same rule as push.due_sales
    (in stock, not on sale now, the typical sale at least 3 % under today's price), in one pass over the
    last 120 days of price rows (push.due_sales does one product at a time and needs the push package)."""
    since = now - 120 * DAY
    prices = {r[0]: r[1] for r in con.execute("SELECT id, price FROM products WHERE in_stock = 1 AND price > 0")}
    out, cur, rows = set(), None, []

    def judge(pid, rows):
        if pid in prices and len(rows) >= 3:
            r = forecast.rhythm(rows, now)
            if r and not r["on_sale"] and r["next_in_days"] is not None and r["next_in_days"] <= within_days \
                    and r["typical_sale"] < prices[pid] * 0.97:
                out.add(pid)

    for pid, at, price, orig, stock in con.execute(
            """SELECT product_id, at, price, original_price, in_stock FROM prices
               WHERE at > ? AND product_id IN (SELECT product_id FROM prices WHERE at > ?
                                               GROUP BY product_id HAVING COUNT(*) >= 3)
               ORDER BY product_id, at""", (since, since)):
        if pid != cur:
            if cur is not None:
                judge(cur, rows)
            cur, rows = pid, []
        rows.append({"at": at, "price": price, "original_price": orig, "in_stock": stock})
    if cur is not None:
        judge(cur, rows)
    return out


def skus_by_store(con, ids) -> dict:
    """{store: {sku, ...}} for these product ids."""
    out = {}
    if ids:
        for store, sku in con.execute("SELECT store, sku FROM products WHERE id IN (SELECT value FROM json_each(?))",
                                      (json.dumps(sorted(ids)),)):
            out.setdefault(store, set()).add(sku)
    return out


class Watchlist:
    """{store: skus} that are watched or have a sale due, reloaded at most every `every` seconds (predicted
    sales every `due_every`). Safe to share between store threads: one reloads while the others keep the last
    copy. `version` moves whenever the set changed, so a store knows when to mark its shelves again."""

    def __init__(self, db_path: str = None, con=None, every: float = 60, due_every: float = 3600, log=print):
        self.db_path, self.con, self.every, self.due_every, self.log = db_path, con, every, due_every, log
        self.lock = threading.Lock()
        self.loaded = self.due_loaded = None
        self.due = set()
        self.by_store = {}
        self.version = 0

    def _open(self):
        if self.con is not None:
            return self.con
        con = sqlite3.connect(self.db_path, timeout=30)  # a bare reader: no schema or migration work
        return con

    def get(self, now: float) -> dict:
        if self.loaded is not None and now - self.loaded < self.every:
            return self.by_store
        if not self.lock.acquire(blocking=False):  # another store is reloading: use the last copy
            return self.by_store
        try:
            if self.loaded is None or now - self.loaded >= self.every:
                self._reload(now)
        except sqlite3.Error as e:
            self.loaded = now  # try again in a minute
            self.log(f"watch list: {e}")
        finally:
            self.lock.release()
        return self.by_store

    def _reload(self, now: float):
        con = self._open()
        try:
            if self.due_loaded is None or now - self.due_loaded >= self.due_every:
                self.due, self.due_loaded = due_ids(con, int(now)), now
            by_store = skus_by_store(con, watched_ids(con) | self.due)
        finally:
            if con is not self.con:
                con.close()
        self.loaded = now
        if by_store != self.by_store:
            self.by_store = by_store
            self.version += 1


_shared, _shared_lock = {}, threading.Lock()


def shared(db_path: str) -> Watchlist:
    """One Watchlist per database for all the store threads."""
    with _shared_lock:
        if db_path not in _shared:
            _shared[db_path] = Watchlist(db_path)
        return _shared[db_path]
