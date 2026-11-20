"""What the web server, the Telegram bot and the channel poster share: the data version and one catalog.

- The **data version** `v` is the newest price row's rowid (db.data_version). A price row is written only when
  a price or stock really changed (or a product is new), so `v` stays put while nothing moved. It is read from
  the database at most once a second.
- The **catalog** is refreshed on a thread of its own, never on a request: when `v` has moved and the copy is at
  least MIN_REBUILD seconds old, and every FORCE_REBUILD seconds whatever happened, because some flags depend on
  the clock ("changed in 24 h", "back in stock") and some product details change without a price row (names,
  pictures, ranks). A refresh is incremental (finder.Catalog): only products with a new price row, a rewritten
  product row or a judgement the clock has just changed are recomputed, and only the matching buckets they touch;
  each item's digest is kept while its item is. Every FULL_REBUILD seconds it builds everything afresh anyway, as
  a safety net (the result is the same). Readers always get the latest finished copy at once.
  When nobody has read it for IDLE seconds, it isn't rebuilt (no visitors, no CPU) until the next reader, who
  gets the old copy at once while the new one builds.
"""

import gc
import hashlib
import os
import sqlite3
import threading
import time
import traceback

from . import db, finder

VERSION_SECONDS = 1.0
MIN_REBUILD = 15
FORCE_REBUILD = 300
FULL_REBUILD = 1800  # a from-scratch build this often, though a refresh gives the same items (a safety net)
IDLE = 600  # nobody has asked for the catalog this long: stop rebuilding it until someone does
# fields that follow the clock rather than the prices: left out of an item's digest (and so out of ETags)
VOLATILE = frozenset({"last_seen", "spark", "tracked_days", "days_at_price", "why", "rhythm", "verdict"})
RHYTHM_VOLATILE = frozenset({"next_in_days", "ends_in_days", "text"})


def open_db(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    db.tune(con)
    return con


def item_digest(d: dict) -> bytes:
    """8 bytes that change when what a card shows changes, but not when only time passes."""
    stable = [(k, v) for k, v in d.items() if k not in VOLATILE]
    spark = d.get("spark") or []
    r = d.get("rhythm")
    v = d.get("verdict") or {}
    stable.append(("spark", spark[:-1]))  # the last point is (now, price)
    stable.append(("rhythm", r and {k: x for k, x in r.items() if k not in RHYTHM_VOLATILE}))
    stable.append(("verdict", (v.get("label"), v.get("tone"))))
    return hashlib.blake2b(repr(stable).encode(), digest_size=8).digest()


class Version:
    """`v`, refreshed from the database at most every `every` seconds."""

    def __init__(self, db_path: str, every: float = VERSION_SECONDS, clock=time.monotonic):
        self.db_path, self.every, self.clock = db_path, every, clock
        self._v, self._at = 0, float("-inf")
        self._lock = threading.Lock()

    def get(self, con: sqlite3.Connection = None) -> int:
        if self.clock() - self._at < self.every:
            return self._v
        with self._lock:
            if self.clock() - self._at < self.every:  # another thread just read it
                return self._v
            own = con is None
            con = con or open_db(self.db_path)
            try:
                self._v = db.data_version(con)
            finally:
                if own:
                    con.close()
            self._at = self.clock()
        return self._v

    def set(self, v: int):
        """A newer reading taken elsewhere (the catalog builder's)."""
        with self._lock:
            if v > self._v:
                self._v = v


class Snapshot:
    """One finished catalog. Never changed after it's made: readers share it."""
    __slots__ = ("items", "by_id", "digests", "v", "built", "built_mono", "gen", "secs", "info")

    def __init__(self, items, v, built, built_mono, gen, secs, digests=None, info=None):
        """digests: the items' digests when already known (an incremental refresh keeps the unchanged ones)."""
        self.items, self.v, self.built, self.built_mono, self.gen, self.secs = items, v, built, built_mono, gen, secs
        self.by_id = {d["id"]: d for d in items}
        self.digests = digests if digests is not None else {d["id"]: item_digest(d) for d in items}
        self.info = info or {}


class CatalogKeeper:
    def __init__(self, db_path: str, version: Version = None, min_rebuild: float = MIN_REBUILD,
                 force_rebuild: float = FORCE_REBUILD, build=None, clock=time.monotonic, wall=time.time,
                 poll: float = 1.0, idle: float = IDLE, log=print, full_rebuild: float = FULL_REBUILD,
                 gc_freeze: bool = False):
        """build(con, now) -> items: a full builder of one's own (tests); by default the incremental finder.Catalog.
        gc_freeze: keep Python's cycle collector off the catalog (see _rebuild); the process's shared keeper does."""
        self.db_path = db_path
        self.version = version or Version(db_path)
        self.min_rebuild, self.force_rebuild, self.poll, self.idle = min_rebuild, force_rebuild, poll, idle
        self.last_read = clock()
        self.build = build
        self.full_rebuild = full_rebuild
        self.engine = finder.Catalog()
        self.last_full = float("-inf")
        self.gc_freeze = gc_freeze
        self.clock, self.wall, self.log = clock, wall, log
        self.snap: Snapshot = None
        self.builds = 0
        self._build_lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()

    def due(self, v: int) -> bool:
        s = self.snap
        if s is None:
            return True
        if self.clock() - self.last_read > self.idle:
            return False
        age = self.clock() - s.built_mono
        return (v != s.v and age >= self.min_rebuild) or age >= self.force_rebuild

    def rebuild(self) -> Snapshot:
        """Builds a new copy now (one at a time). Readers keep getting the previous one meanwhile."""
        with self._build_lock:
            return self._rebuild()

    def _rebuild(self) -> Snapshot:
        """With gc_freeze, a refresh runs with the cycle collector paused and ends with gc.freeze(): the catalog is
        hundreds of thousands of small dicts and lists without reference cycles (freed by reference counting), and
        a refresh makes a new dict for every item (its sparkline ends at the new now), so otherwise the collector
        would walk the whole catalog after nearly every refresh (~0.7 s at 100k products). Cycles that other
        threads leave among frozen objects wait for the next full build, which unfreezes and collects first."""
        freeze = self.gc_freeze and self.build is None
        full = self.snap is None or self.clock() - self.last_full >= self.full_rebuild
        paused = False
        if freeze:
            if full:
                gc.unfreeze()
            elif gc.isenabled():
                gc.disable()
                paused = True
        try:
            return self._build_snapshot(full)
        finally:
            if freeze:
                if full:
                    gc.collect()
                gc.freeze()
            if paused:
                gc.enable()

    def _build_snapshot(self, full: bool) -> Snapshot:
        started, wall = self.clock(), int(self.wall())
        con = open_db(self.db_path)
        try:
            con.execute("BEGIN")  # one read snapshot: `v` and the catalog describe the same data
            prev, digests, info = self.snap, None, None
            if self.build is not None:
                v = db.data_version(con)
                items = self.build(con, wall)
            else:
                built = self.engine.refresh(con, wall, full=full)
                v, items, info = built.v, built.items, built.stats
                if built.full:
                    self.last_full = started
                else:  # keep the digests of the items that stayed the same
                    digests = dict(prev.digests)
                    for pid in built.removed:
                        digests.pop(pid, None)
                    for pid, d in built.changed.items():
                        digests[pid] = item_digest(d)
            con.rollback()
        finally:
            con.close()
        self.builds += 1
        snap = Snapshot(items, v, wall, started, self.builds, self.clock() - started, digests, info)
        self.snap = snap
        self.version.set(v)
        return snap

    def get(self) -> Snapshot:
        """The latest copy; only the very first call waits for one to be built."""
        self.last_read = self.clock()
        s = self.snap
        if s is None:
            with self._build_lock:
                if self.snap is None:
                    self._rebuild()
            s = self.snap
        self.start()
        return s

    def touch(self):
        """Someone is looking (e.g. a /api/pulse poll) even without reading the catalog: keep it fresh."""
        self.last_read = self.clock()

    def check(self) -> bool:
        """One look: rebuild if due. Returns whether it rebuilt."""
        if self.due(self.version.get()):
            self.rebuild()
            return True
        return False

    def start(self):
        if self._thread is None:
            with self._start_lock:
                if self._thread is None:
                    self._thread = threading.Thread(target=self._run, name="catalog", daemon=True)
                    self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        failing = False
        while not self._stop.is_set():
            try:
                self.check()
                failing = False
            except Exception:
                if not failing:  # say it once, not every second
                    self.log("catalog: " + traceback.format_exc(limit=3))
                failing = True
            self._stop.wait(self.poll)


_shared: dict = {}
_shared_lock = threading.Lock()


def shared(db_path: str) -> CatalogKeeper:
    """The process's keeper for this database, made on first use (the web server, `run`)."""
    key = os.path.abspath(db_path)
    with _shared_lock:
        if key not in _shared:
            _shared[key] = CatalogKeeper(db_path, gc_freeze=True)
        return _shared[key]


def existing(db_path: str):
    """The shared keeper if this process made one (the bot and the poster use it then), else None."""
    return _shared.get(os.path.abspath(db_path))
