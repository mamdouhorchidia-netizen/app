"""The live catalogue: built once when the server starts, then kept up to date by a background thread. Everyone in
the process (the web pages and API, the Telegram bot, the channel poster) reads the same immutable snapshot and never
waits for a build.

Each refresh asks the database which products changed since the last one (the product_changes log, kept by
triggers: db.py) and judges only those again, plus the few whose verdict the clock alone is about to change
(finder.valid_until: "tracked 3.4 days", the 24 h "new" flag, the 30-day usual price...). Cross-store matching is
redone only for the pack-size buckets a changed name touches; the score and verdict only for the changed products and
the products matched with them. The result is exactly finder.catalog(con, now) (tests/test_engine.py checks it
after many changes); a full rebuild still runs in the background every DEALS_FULL_EVERY seconds (6 h) as a safety net.

Snapshot items are finder's items with the spark's last step (which only says "now") left out: Snapshot.full(d)
puts it back, with the snapshot's time.

`v` is the catalogue version: it changes when, and only when, a refresh changed something a visitor sees (a price,
stock, a product added or gone, or a verdict or flag the clock changed, such as "new" running out after 24 h). The
slow drift of "tracked N days", "N days at this price" and the last-seen time alone doesn't change it.

Queries (Snapshot.query, the same answers as finder.query) run on an index of bitmasks: one Python int per store,
category, kind, hub and flag, one bit per product, so a filter is an AND and a facet count is a bit count. Each
sort order is kept presorted, and updated in place for the products that changed.
"""

import hashlib
import heapq
import json
import os
import secrets
import sqlite3
import threading
import time
import traceback
from bisect import bisect_left, bisect_right
from collections import OrderedDict, defaultdict
from functools import reduce
from operator import or_

from . import db, finder, matching
from .stores import LABELS

REFRESH_EVERY = float(os.environ.get("DEALS_REFRESH_EVERY", 5))
FULL_EVERY = float(os.environ.get("DEALS_FULL_EVERY", 6 * 3600))
# What the clock alone keeps changing: "tracked 3.4 days", "same price for 15.2 days" (also quoted in `why`), the sale
# rhythm's countdown ("next one in about 2 days"). Snapshots carry them exactly; they just don't make a new version.
VOLATILE = ("last_seen", "tracked_days", "days_at_price", "why")
COUNTDOWN = ("next_in_days", "ends_in_days", "text")
FLAGS = ("new", "lowest", "label", "back", "steady", "inflated", "rose")


# ---- what the status line shows (read from the database at every refresh, served from memory) ---------------

def status(con) -> list[dict]:
    """One row per store that has products or has been checked."""
    counts = dict(con.execute("SELECT store, COUNT(*) FROM products GROUP BY store").fetchall())
    live = {r["store"]: dict(r) for r in con.execute("SELECT * FROM live_status")}
    runs = {r["store"]: dict(r) for r in con.execute("""
        SELECT r.* FROM runs r JOIN (SELECT store, MAX(id) id FROM runs WHERE finished IS NOT NULL GROUP BY store) l
        ON r.id = l.id""")}
    out = []
    for store in sorted(set(counts) | set(live) | set(runs)):
        row = {"store": store, "label": LABELS.get(store, store), "products": counts.get(store, 0)}
        if store in live:
            l = live[store]
            row.update(live=True, updated=l["updated"], last_change=l["last_change"] or None,
                       cycle_secs=l["cycle_secs"], pages_per_cycle=l["tasks"], error=l["error"],
                       paused_until=l["paused_until"] if (l["paused_until"] or 0) > time.time() else None)
        elif store in runs:
            r = runs[store]
            row.update(live=False, updated=r["finished"], last_change=None, cycle_secs=None, error=r["error"])
        out.append(row)
    return out


def changes_last_hour(con) -> int:
    return con.execute("""SELECT COUNT(*) FROM prices x JOIN products p ON p.id = x.product_id
                          WHERE x.at > ? AND x.at > p.first_seen""", (int(time.time()) - 3600,)).fetchone()[0]


def recent_changes(con, limit: int = 30) -> list[dict]:
    rows = con.execute("""
        SELECT x.at, x.price, x.in_stock, p.id, p.store, p.name, p.image,
               (SELECT y.price FROM prices y WHERE y.product_id = x.product_id AND y.at < x.at
                ORDER BY y.at DESC LIMIT 1) AS before
        FROM prices x JOIN products p ON p.id = x.product_id
        WHERE x.at > p.first_seen ORDER BY x.at DESC, x.rowid DESC LIMIT ?""", (limit,))
    out = []
    for r in rows:
        r = dict(r)
        r["label"] = LABELS.get(r["store"], r["store"])
        out.append(r)
    return out


class Meta:
    """The status line, the last hour's count and the latest moves, as of one refresh."""
    __slots__ = ("status", "changes_last_hour", "changes", "at", "seq")

    def __init__(self, status_rows, changes_hour, changes, at, seq):
        self.status, self.changes_last_hour, self.changes, self.at, self.seq = status_rows, changes_hour, changes, at, seq


# ---- the index ------------------------------------------------------------------------------------------------

def mask_of(slots, nbytes: int) -> int:
    b = bytearray(nbytes)
    for s in slots:
        b[s >> 3] |= 1 << (s & 7)
    return int.from_bytes(b, "little")


def slots_of(mask: int) -> list[int]:
    """The set bits of a mask, lowest first."""
    if not mask:
        return []
    s = bin(mask)
    top = len(s) - 1  # bin() is "0b" then the highest bit first
    out = []
    i = s.find("1", 2)
    while i != -1:
        out.append(top - i)
        i = s.find("1", i + 1)
    out.reverse()
    return out


def _bits(slots) -> int:
    return reduce(or_, (1 << s for s in slots), 0)


def _tokens(d: dict) -> set:
    """Every mask a catalogue item belongs to."""
    t = {("store", d["store"]), ("cat", d["top_category"])}
    if d["kind"]:
        t.add(("kind", d["kind"]))
        if d["discount_pct"] > 0:
            t.add("deal")
    if d["in_stock"]:
        t.add("in")
    if d["store"] == "talabat":
        try:
            hash(d["hub"])
            t.add(("hub", d["hub"]))
        except TypeError:  # a hub value no filter can name
            pass
        if d["hub"]:
            t.add(("hubkey", f'{d["hub"]}|{d["hub_name"]}'))
    for f in d["flags"]:
        if f in FLAGS:
            t.add("f:" + f)
    if d["cheapest"]:
        t.add("cheapest")
    if d["matches"]:
        t.add("compare")
    if d.get("promo_code"):
        t.add("promo")
    r = d.get("rhythm")
    if r and r["next_in_days"] is not None and r["next_in_days"] <= 3:
        t.add("soon")
    return t


def _text(d: dict) -> str:
    return (d["name"] + " " + (d["brand"] or "") + " " + (d["category"] or "")).lower()


# the page's sorts, and max(deals, key=saving) for the brief (the first of the biggest savings)
SORT_DIRECTLY = 2000  # hits few enough to sort, rather than walking a presorted order
SMALL = 15_000  # candidates few enough to test one by one rather than through a whole-catalogue mask
ORDERS = {**finder.SORTS, "_saving": lambda d: (-d["saving"],)}


class SortedSlots:
    """Slots in one sort order, in blocks, so a few changed products move without copying the whole list (the blocks
    a change doesn't touch are shared with the previous version)."""
    LOAD = 512
    __slots__ = ("blocks", "maxes")

    def __init__(self, blocks, maxes):
        self.blocks, self.maxes = blocks, maxes

    @classmethod
    def build(cls, slots: list, key) -> "SortedSlots":
        blocks = [slots[i:i + cls.LOAD] for i in range(0, len(slots), cls.LOAD)]
        return cls(blocks, [key(b[-1]) for b in blocks])

    def updated(self, removed: list, added: list, old_key, new_key) -> "SortedSlots":
        """A new version without `removed` (found by their key in the old order) and with `added`."""
        blocks, maxes = list(self.blocks), list(self.maxes)
        mine = set()

        def own(i):
            b = blocks[i]
            if id(b) not in mine:
                b = blocks[i] = list(b)
                mine.add(id(b))
            return b
        for s in removed:
            k = old_key(s)
            i = min(bisect_left(maxes, k), len(maxes) - 1)
            b = own(i)
            b.remove(s)
            if b:
                maxes[i] = old_key(b[-1])
            else:
                del blocks[i], maxes[i]
        for s in added:
            k = new_key(s)
            if not blocks:
                blocks.append([s])
                mine.add(id(blocks[0]))
                maxes.append(k)
                continue
            i = min(bisect_left(maxes, k), len(maxes) - 1)
            b = own(i)
            b.insert(bisect_left(b, k, key=new_key), s)
            maxes[i] = new_key(b[-1])
            if len(b) > 2 * self.LOAD:
                lo, hi = b[:self.LOAD], b[self.LOAD:]
                mine.update((id(lo), id(hi)))
                blocks[i:i + 1] = [lo, hi]
                maxes[i:i + 1] = [new_key(lo[-1]), new_key(hi[-1])]
        return SortedSlots(blocks, maxes)


class Index:
    """Bitmasks over slots (one per product, in id order, so the lowest slot comes first in the catalogue)."""

    def __init__(self):
        self.ids = []          # slot -> product id (None: a product that went)
        self.slot = {}         # product id -> slot
        self.all = 0           # the live slots
        self.masks = {}        # token -> mask (see _tokens)
        self.dims = {}         # "store" | "cat" | "kind" | "hub" | "hubkey" -> {value: mask}
        self.disc, self.price, self.text = [], [], []
        self.orders = {}       # sort -> SortedSlots (filled in the background after a full build)
        self.cache = OrderedDict()  # derived masks (a word, a price bound) for this version
        self.lock = threading.Lock()
        self.blob = None

    @property
    def nbytes(self) -> int:
        return (len(self.ids) + 7) // 8

    @classmethod
    def build(cls, items: list, hidden=()) -> "Index":
        """items in id order. `hidden`: ids of products not in the catalogue now (no price): they get a slot too, so
        one that comes back keeps its place in id order."""
        ix = cls()
        by = {d["id"]: d for d in items}
        ix.ids = sorted(set(by) | set(hidden))
        ix.slot = {pid: s for s, pid in enumerate(ix.ids)}
        lists = defaultdict(list)
        live = []
        for s, pid in enumerate(ix.ids):
            d = by.get(pid)
            if d is None:
                continue
            live.append(s)
            for t in _tokens(d):
                lists[t].append(s)
        nb = ix.nbytes
        ix.all = mask_of(live, nb)
        for t, slots in lists.items():
            ix._set(t, mask_of(slots, nb))
        ix.disc = [by[p]["discount_pct"] if p in by else None for p in ix.ids]
        ix.price = [by[p]["price"] if p in by else None for p in ix.ids]
        ix.text = [_text(by[p]) if p in by else "" for p in ix.ids]
        for pid in hidden:
            if pid not in by:
                ix.ids[ix.slot[pid]] = None
        return ix

    def _set(self, t, m: int):
        if isinstance(t, tuple):
            dim = self.dims.setdefault(t[0], {})
            if m:
                dim[t[1]] = m
            else:
                dim.pop(t[1], None)
        elif m:
            self.masks[t] = m
        else:
            self.masks.pop(t, None)

    def _get(self, t) -> int:
        return self.dims.get(t[0], {}).get(t[1], 0) if isinstance(t, tuple) else self.masks.get(t, 0)

    def key(self, mode: str, by_id: dict):
        f, ids = ORDERS[mode], self.ids
        return lambda s: (f(by_id[ids[s]]), s)

    def build_orders(self, by_id: dict, modes=None):
        """Every sort order, presorted (a second or two for 110k products: the background thread does it)."""
        live = slots_of(self.all)
        for mode in modes or ORDERS:
            k = self.key(mode, by_id)
            self.orders[mode] = SortedSlots.build(sorted(live, key=k), k)

    def updated(self, old_by: dict, new_by: dict, changed: set) -> "Index":
        """A new version for the products in `changed` (ids changed, added or gone); None when a new product's id is
        older than the newest one (then slots would no longer be in id order: build again)."""
        added = sorted(pid for pid in changed if pid not in self.slot and pid in new_by)
        if added and self.slot and added[0] < max(self.slot):
            return None
        ix = Index()
        ix.slot, ix.ids = self.slot, self.ids
        back = [pid for pid in changed if pid in self.slot and pid not in old_by and pid in new_by]
        gone = [pid for pid in changed if pid in old_by and pid not in new_by]
        if added or back or gone:
            ix.ids = list(self.ids)
            if added:
                ix.slot = dict(self.slot)
            for pid in added:
                ix.slot[pid] = len(ix.ids)
                ix.ids.append(pid)
            for pid in back:
                ix.ids[ix.slot[pid]] = pid
            for pid in gone:
                ix.ids[ix.slot[pid]] = None
        ix.masks, ix.dims = dict(self.masks), {k: dict(v) for k, v in self.dims.items()}
        ix.disc, ix.price, ix.text = self.disc, self.price, self.text
        add, clear = defaultdict(list), defaultdict(list)
        all_add, all_clear = [], []
        lists = {}
        for pid in changed:
            old, new = old_by.get(pid), new_by.get(pid)
            s = ix.slot.get(pid)
            if s is None or (old is None and new is None):
                continue
            ot = _tokens(old) if old is not None else set()
            nt = _tokens(new) if new is not None else set()
            for t in ot - nt:
                clear[t].append(s)
            for t in nt - ot:
                add[t].append(s)
            if new is None:
                all_clear.append(s)
            elif old is None:
                all_add.append(s)
            values = {"disc": new["discount_pct"] if new else None, "price": new["price"] if new else None,
                      "text": _text(new) if new else ""}
            for name, value in values.items():
                cur = lists.get(name) or getattr(ix, name)
                if s < len(cur) and cur[s] == value and (value is None) == (cur[s] is None):
                    continue
                if name not in lists:
                    cur = lists[name] = list(cur)
                    setattr(ix, name, cur)
                if s >= len(cur):
                    cur.extend([None if name != "text" else ""] * (s + 1 - len(cur)))
                cur[s] = value
        for t in set(add) | set(clear):
            m = self._get(t)
            if t in clear:
                m &= ~_bits(clear[t])
            if t in add:
                m |= _bits(add[t])
            ix._set(t, m)
        ix.all = (self.all & ~_bits(all_clear)) | _bits(all_add)
        for mode, order in self.orders.items():
            f = ORDERS[mode]
            moved = {p for p in changed if p not in old_by or p not in new_by or f(old_by[p]) != f(new_by[p])}
            if moved:
                ix.orders[mode] = order.updated([self.slot[p] for p in moved if p in old_by],
                                                [ix.slot[p] for p in moved if p in new_by],
                                                self.key(mode, old_by), ix.key(mode, new_by))
            else:
                ix.orders[mode] = order
        if "text" not in lists:
            ix.blob = self.blob
        if not lists:  # nothing the derived masks read has changed
            ix.cache, ix.lock = self.cache, self.lock
        return ix

    def brief(self, by_id: dict) -> dict:
        """finder.brief(items), from the masks."""
        M = self.masks
        live = M.get("in", 0)
        fake = M.get("f:steady", 0) | M.get("f:inflated", 0)
        deals = live & M.get("deal", 0) & ~(fake | M.get("f:rose", 0))
        dropped = deals & M.get("f:new", 0)
        pick = lambda d: {k: d[k] for k in ("id", "name", "store", "price", "original_price", "usual_price", "image",
                                              "discount_pct", "saving", "kind", "flags", "score", "since")}
        ids = self.ids
        saving_today = sum(by_id[ids[s]]["saving"] for s in slots_of(dropped))
        pool = dropped or deals
        top = self.page(by_id, pool, pool.bit_count(), "score", 0, 10)
        best = self.page(by_id, deals, deals.bit_count(), "_saving", 0, 1)
        return {"deals": deals.bit_count(), "dropped_24h": dropped.bit_count(),
                "record_lows": (deals & M.get("f:lowest", 0)).bit_count(), "fake": (live & fake).bit_count(),
                "soon": (live & M.get("soon", 0)).bit_count(), "promo": (live & M.get("promo", 0)).bit_count(),
                "saving_today": round(saving_today, 2), "top": [pick(d) for d in top],
                "biggest_saving": pick(best[0]) if best else None}

    def best_by(self, by_id: dict, dim: str) -> dict:
        """{store or top category: the biggest real discount in stock there} (the /stores and /cats pages)."""
        M = self.masks
        real = M.get("in", 0) & M.get("deal", 0) & ~(M.get("f:steady", 0) | M.get("f:inflated", 0) | M.get("f:rose", 0))
        field = {"store": "store", "cat": "top_category"}[dim]
        out, ids = {}, self.ids
        for s in slots_of(real):
            d = by_id[ids[s]]
            out[d[field]] = max(out.get(d[field], 0), d["discount_pct"])
        return out

    # -- reading --
    def _cached(self, key, build):
        with self.lock:
            hit = self.cache.get(key)
            if hit is not None:
                self.cache.move_to_end(key)
                return hit
        m = build()
        with self.lock:
            self.cache[key] = m
            while len(self.cache) > 256:
                self.cache.popitem(last=False)
        return m

    def _small(self, mask: int, test) -> int:
        return mask_of([s for s in slots_of(mask) if test(s)], self.nbytes)

    def _num(self, field: str, op: str, bound: float) -> int:
        """The slots that a `field < bound` (op "lt") or `field > bound` (op "gt") test would NOT drop."""
        def build():
            vals, ids = getattr(self, field), self.ids
            if op == "lt":
                keep = [s for s, x in enumerate(vals) if ids[s] is not None and not x < bound]
            else:
                keep = [s for s, x in enumerate(vals) if ids[s] is not None and not x > bound]
            return mask_of(keep, self.nbytes)
        return self._cached((field, op, bound), build)

    def _word(self, w: str) -> int:
        def build():
            blob = self.blob
            if blob is None:
                starts, pos = [], 0
                for t in self.text:
                    starts.append(pos)
                    pos += len(t) + 1
                blob = self.blob = ("\n".join(self.text), starts)
            text, starts = blob
            found, n = [], len(starts)
            i = text.find(w)
            while i != -1:
                s = bisect_right(starts, i) - 1
                found.append(s)
                i = text.find(w, starts[s + 1]) if s + 1 < n else -1
            return mask_of(found, self.nbytes) & self.all
        return self._cached(("w", w), build)

    def facet(self, mask: int, dim: str) -> dict:
        rows = []
        for value, m in self.dims.get(dim, {}).items():
            x = mask & m
            if x:
                rows.append((-x.bit_count(), (x & -x).bit_length(), value))
        rows.sort(key=lambda r: r[:2])
        return {value: -c for c, _, value in rows}

    def query(self, by_id: dict, deals_only: bool = True, stores: list = None, kind: str = None, min_pct: float = 0,
              max_pct: float = None, stock: str = "in", status: list = None, category: str = None, q: str = None,
              price_min: float = None, price_max: float = None, ids=None, best: bool = False, sort: str = "score",
              limit: int = 200, offset: int = 0, hubs: list = None) -> dict:
        """finder.query(items, ...), from the masks."""
        args = dict(deals_only=deals_only, stores=stores, kind=kind, min_pct=min_pct, max_pct=max_pct, stock=stock,
                    status=status, category=category, q=q, price_min=price_min, price_max=price_max, ids=ids,
                    best=best, sort=sort, limit=limit, offset=offset, hubs=hubs)
        if ids is not None:  # a watchlist: only these can match anything, so the plain filter on them is exact
            return finder.query([by_id[i] for i in sorted({i for i in ids if i in by_id})], **args)
        if offset < 0 or limit < 0:  # Python's own slicing, as finder.query does it
            return finder.query([by_id[i] for i in self.ids if i is not None], **args)
        words = [w for w in (q or "").lower().split() if w]
        status = set(status or [])
        stores = set(stores or [])
        M = self.masks
        rest = self.all
        if deals_only:
            rest &= M.get("deal", 0)
        if stock == "in":
            rest &= M.get("in", 0)
        elif stock == "out":
            rest &= ~M.get("in", 0)
        if best:
            rest &= M.get("cheapest", 0)
        if "real" in status:
            rest &= ~(M.get("f:steady", 0) | M.get("f:inflated", 0) | M.get("f:rose", 0))
        for f in ("new", "lowest", "label", "back"):
            if f in status:
                rest &= M.get("f:" + f, 0)
        if "compare" in status:
            rest &= M.get("compare", 0)
        if "fake" in status:
            rest &= M.get("f:steady", 0) | M.get("f:inflated", 0)
        if "promo" in status:
            rest &= M.get("promo", 0)
        if "soon" in status:
            rest &= M.get("soon", 0)
        tests = [(f, op, b) for f, op, b in (("disc", "lt", min_pct if min_pct else None), ("disc", "gt", max_pct),
                                             ("price", "lt", price_min), ("price", "gt", price_max)) if b is not None]
        for f, op, b in tests:
            rest &= self._small(rest, lambda s, f=f, op=op, b=b: not (getattr(self, f)[s] < b if op == "lt"
                                                                       else getattr(self, f)[s] > b)) \
                if rest.bit_count() <= SMALL else self._num(f, op, b)
        for w in sorted(words, key=len, reverse=True):
            if not rest:
                break
            text = self.text
            rest = self._small(rest, lambda s: w in text[s]) if rest.bit_count() <= SMALL else rest & self._word(w)
        D = self.dims
        S = reduce(or_, (D.get("store", {}).get(s, 0) for s in stores), 0) if stores else None
        K = D.get("kind", {}).get(kind, 0) if kind else None
        H = ((self.all & ~D.get("store", {}).get("talabat", 0))
             | reduce(or_, (D.get("hub", {}).get(h, 0) for h in hubs if _hashable(h)), 0)) if hubs else None
        C = D.get("cat", {}).get(category, 0) if category else None

        def both(*ms):
            x = rest
            for m in ms:
                if m is not None:
                    x &= m
            return x
        hit = both(S, K, H, C)
        facets = {"store": self.facet(both(K, H, C), "store"), "category": self.facet(both(S, K, H), "cat"),
                  "kind": self.facet(both(S, H, C), "kind"), "hub": self.facet(both(S, K, C), "hubkey")}
        total = hit.bit_count()
        mode = sort if sort in finder.SORTS else "score"
        return {"items": self.page(by_id, hit, total, mode, offset, limit), "total": total, "facets": facets}

    def page(self, by_id: dict, hit: int, total: int, mode: str, offset: int, limit: int) -> list:
        """hit's items from offset to offset + limit in the order `mode` (a key of ORDERS)."""
        need = offset + limit
        if limit <= 0 or offset >= total:
            return []
        order = self.orders.get(mode)
        if total <= SORT_DIRECTLY or order is None:
            slots, key = slots_of(hit), self.key(mode, by_id)
            chosen = sorted(slots, key=key) if need >= len(slots) else heapq.nsmallest(need, slots, key=key)
        else:
            bb = hit.to_bytes(self.nbytes, "little")
            chosen = []
            for blk in order.blocks:
                chosen += [s for s in blk if bb[s >> 3] >> (s & 7) & 1]
                if len(chosen) >= need:
                    break
        ids = self.ids
        return [by_id[ids[s]] for s in chosen[offset:need]]


def _hashable(x) -> bool:
    try:
        hash(x)
        return True
    except TypeError:
        return False


# ---- snapshots ---------------------------------------------------------------------------------------------------

class Snapshot:
    """One immutable version of the catalogue. Never changed after it is published: read it without locks."""

    def __init__(self, v: str, now: int, by_id: dict, index: Index, brief: dict, info: dict):
        self.v, self.now, self.by_id, self.index, self.brief, self.info = v, now, by_id, index, brief, info
        self._items = None
        self.answers = OrderedDict()  # what the web server made from this snapshot (web.py)
        self.answers_lock = threading.Lock()

    @property
    def items(self) -> list:
        """Every item in catalogue (id) order, as finder.catalog lists them (without the spark's last step)."""
        if self._items is None:
            by, ids = self.by_id, self.index.ids
            self._items = [by[i] for i in ids if i is not None]
        return self._items

    def full(self, d: dict) -> dict:
        """The item as finder.catalog gives it (the spark's last step at this snapshot's time)."""
        return finder.with_now(d, self.now)

    def query(self, **args) -> dict:
        return self.index.query(self.by_id, **args)

    def answer(self, key, build, keep: int = 400):
        """build() once per key for this snapshot (the web server's encoded answers)."""
        with self.answers_lock:
            hit = self.answers.get(key)
            if hit is not None:
                self.answers.move_to_end(key)
                return hit
        value = build()
        with self.answers_lock:
            self.answers[key] = value
            while len(self.answers) > keep:
                self.answers.popitem(last=False)
        return value


def _soon(r) -> bool:
    return bool(r) and r["next_in_days"] is not None and r["next_in_days"] <= 3


def _same(a: dict, b: dict) -> bool:
    """Nothing differs but the clock's countdowns (VOLATILE, COUNTDOWN): the same version."""
    if a is b:
        return True
    if a.keys() != b.keys():
        return False
    for k, x in a.items():
        if k in VOLATILE:
            continue
        y = b[k]
        if x == y:
            continue
        if k == "rhythm" and x and y and x.keys() == y.keys() and _soon(x) == _soon(y) \
                and all(x[j] == y[j] for j in x if j not in COUNTDOWN):
            continue
        if k == "verdict" and (x["label"], x["tone"]) == (y["label"], y["tone"]) and a["rhythm"] and b["rhythm"] \
                and x["why"] == a["rhythm"]["text"] and y["why"] == b["rhythm"]["text"]:
            continue
        return False
    return True


class _State:
    """What the refresher keeps between refreshes (only the refresher's thread touches it)."""

    def __init__(self):
        self.seq = 0
        self.by_id = {}       # product id -> finished item (the published snapshot's dict: never changed in place)
        self.until = {}       # product id -> valid until
        self.names = {}       # product id -> (name, matching key) of the products in matching
        self.members = defaultdict(set)  # matching key -> product ids
        self.talabat = defaultdict(list)  # talabat product (sku after the hub) -> its hub copies' ids, sorted
        self.partners = {}    # product id -> ids matched in other stores
        self.index = None


class Catalog:
    def __init__(self, db_path: str, every: float = REFRESH_EVERY, full_every: float = FULL_EVERY, log=print):
        self.db_path, self.every, self.full_every, self.log = db_path, every, full_every, log
        self._snap = None
        self._meta = None
        self._ready = threading.Event()
        self._lock = threading.Lock()  # one refresh at a time
        self._stop = threading.Event()
        self._thread = None
        self._start_lock = threading.Lock()
        self._salt = secrets.token_hex(3)
        self._gen = 0
        self._st = None
        self._con = None
        self._last_full = 0.0
        self.last_error = None
        self.last = {}  # what the last refresh did

    # -- reading --
    def snapshot(self, wait: float = 0) -> Snapshot:
        """The current snapshot; while the first one is being built, waits up to `wait` seconds (None after that)."""
        if self._snap is None and wait:
            self._ready.wait(wait)
        return self._snap

    def meta(self) -> Meta:
        return self._meta

    # -- the background thread --
    def start(self) -> "Catalog":
        with self._start_lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self.run_forever, name="catalog", daemon=True)
                self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def close(self):
        """Stop the background thread and close the database connection (after a refresh in progress)."""
        self._stop.set()
        with self._lock:
            self._close()

    def run_forever(self):
        fails = 0
        while not self._stop.is_set():
            try:
                full = self._st is None or time.monotonic() - self._last_full > self.full_every
                self.refresh(full=full)
                fails = 0
            except Exception as e:  # keep serving the last good snapshot; build from scratch next time
                fails += 1
                self.last_error = f"{type(e).__name__}: {e}"
                self._st = None
                self._close()
                self.log("catalog: refresh failed, still serving the last good version: "
                         + traceback.format_exc(limit=4))
            self._stop.wait(self.every if not fails else min(60, self.every * 2 ** fails))

    # -- building --
    def _connect(self):
        if self._con is None:
            db.connect(self.db_path).close()  # the tables, indexes and change triggers
            con = sqlite3.connect(self.db_path, timeout=30, isolation_level=None, check_same_thread=False)
            con.row_factory = sqlite3.Row
            self._con = con
        return self._con

    def _close(self):
        if self._con is not None:
            try:
                self._con.close()
            except sqlite3.Error:
                pass
            self._con = None

    def _version(self) -> str:
        self._gen += 1
        return hashlib.sha1(f"{self._salt}:{self._gen}".encode()).hexdigest()[:10]

    def refresh(self, full: bool = False, now: int = None) -> dict:
        """Bring the snapshot up to date (from scratch with full=True). Returns what it did. `now` is for tests."""
        with self._lock:
            t0 = time.perf_counter()
            if full or self._st is None:
                info = self._full(now)
            else:
                info = self._incremental(now)
            info["secs"] = round(time.perf_counter() - t0, 4)
            self.last = info
            self.last_error = None
            return info

    def _read_meta(self, con, seq) -> Meta:
        return Meta(status(con), changes_last_hour(con), recent_changes(con, 100), int(time.time()), seq)

    def _full(self, now=None) -> dict:
        con = self._connect()
        con.execute("BEGIN")
        try:
            seq = con.execute("SELECT COALESCE(MAX(seq), 0) FROM product_changes").fetchone()[0]
            now = int(now or time.time())  # after the read began: every row we can see is from before now
            products = finder.load_products(con)
            hidden = [r[0] for r in con.execute("SELECT id FROM products WHERE NOT price > 0 OR price IS NULL")]
            hist = finder.load_history(con, now - finder.WINDOW)
            meta = self._read_meta(con, seq)
        finally:
            con.execute("COMMIT")
        st = _State()
        st.seq = seq
        items = []
        for p in products:
            rows = hist.pop(p["id"], None)
            it, st.until[p["id"]] = finder.judge(p, rows, now)
            items.append(it)
        del hist, products
        by_id = {it["id"]: it for it in items}
        groups = defaultdict(list)
        for it in items:
            base = finder.match_base(it)
            if base is not None:
                st.talabat[base].append(it["id"])
                if len(st.talabat[base]) > 1:
                    continue
            k, w = matching.key_words(it["name"])
            if k:
                st.names[it["id"]] = (it["name"], it["store"], k)
                st.members[k].add(it["id"])
                groups[k].append((it["id"], it["store"], w))
        for group in groups.values():
            st.partners.update(matching.match_group(group))
        del groups
        for it in items:
            finder.finish(it, by_id, st.partners.get(it["id"]))
        st.by_id = by_id
        st.index = Index.build(items, hidden)
        old = self._snap
        same = old is not None and old.by_id.keys() == by_id.keys() and all(
            _same(old.by_id[k], d) for k, d in by_id.items())
        info = {"full": True, "products": len(items), "seen": len(items), "changed": len(items)}
        snap = Snapshot(old.v if same else self._version(), now, by_id, st.index,
                        old.brief if same else st.index.brief(by_id), info)
        snap._items = items
        self._st = st
        self._last_full = time.monotonic()
        self._meta = meta
        self._snap = snap
        self._ready.set()
        st.index.build_orders(by_id)  # after publishing: until then queries sort their hits directly
        return info

    def _incremental(self, now=None) -> dict:
        st = self._st
        con = self._connect()
        con.execute("BEGIN")
        try:
            top = con.execute("SELECT COALESCE(MAX(seq), 0) FROM product_changes").fetchone()[0]
            now = int(now or time.time())
            changed = {r[0] for r in con.execute("SELECT product_id FROM product_changes WHERE seq > ?", (st.seq,))}
            due = {pid for pid, t in st.until.items() if t <= now}
            ids = changed | due
            rebuild = top < st.seq or len(ids) > max(20_000, len(st.by_id) // 3)  # log emptied, or most changed
            if not rebuild:
                products = {p["id"]: p for p in finder.load_products(con, ids)} if ids else {}
                hist = finder.load_history(con, now - finder.WINDOW, ids) if ids else {}
                meta = self._read_meta(con, top)
        finally:
            con.execute("COMMIT")
        if rebuild:
            return self._full()
        old_by = st.by_id
        new = {}
        for pid, p in products.items():
            rows = hist.get(pid)
            new[pid], st.until[pid] = finder.judge(p, rows, now)
        gone = {pid for pid in ids if pid in old_by and pid not in products}
        for pid in ids - set(products):
            st.until.pop(pid, None)
        by_id = dict(old_by)
        for pid in gone:
            del by_id[pid]
        by_id.update(new)

        # matching: talabat's first hub copies, then only the buckets a change touches
        touched = set(new) | gone
        bases = {finder.match_base(d) for pid in touched for d in (old_by.get(pid), new.get(pid))
                 if d is not None and finder.match_base(d) is not None}
        first = {b: st.talabat[b][0] if st.talabat.get(b) else None for b in bases}
        for pid in touched:
            o = old_by.get(pid)
            if o is not None and finder.match_base(o) is not None:
                lst = st.talabat.get(finder.match_base(o))
                if lst and pid in lst:
                    lst.remove(pid)
        for pid in sorted(new):
            b = finder.match_base(new[pid])
            if b is not None:
                lst = st.talabat[b]
                lst.insert(bisect_left(lst, pid), pid)
        for b in bases:
            if not st.talabat.get(b):
                st.talabat.pop(b, None)
            now_first = st.talabat[b][0] if b in st.talabat else None
            if now_first != first[b]:
                touched |= {x for x in (now_first, first[b]) if x is not None}
        dirty = set()
        for pid in touched:
            d, want = by_id.get(pid), None
            if d is not None:
                b = finder.match_base(d)
                if b is None or st.talabat[b][0] == pid:
                    had = st.names.get(pid)
                    k = had[2] if had and had[:2] == (d["name"], d["store"]) else matching.key(d["name"])
                    want = (d["name"], d["store"], k) if k else None
            had = st.names.get(pid)
            if had != want:
                if had:
                    st.members[had[2]].discard(pid)
                    dirty.add(had[2])
                    del st.names[pid]
                if want:
                    st.names[pid] = want
                    st.members[want[2]].add(pid)
                    dirty.add(want[2])
        regrouped = set()  # products whose matches (the ids) changed, and the ones matched with them before
        for k in dirty:
            members = sorted(st.members.get(k, ()))
            if not members:
                st.members.pop(k, None)
                continue
            found = matching.match_group([(pid, by_id[pid]["store"], matching.words(by_id[pid]["name"]))
                                          for pid in members])
            for pid in members:
                mine = found.get(pid)
                if mine != st.partners.get(pid):
                    regrouped.add(pid)
                    regrouped.update(st.partners.get(pid) or ())
                    regrouped.update(mine or ())
                    if mine:
                        st.partners[pid] = mine
                    else:
                        st.partners.pop(pid, None)
        for pid in touched:  # out of matching altogether (gone, or a hub copy that is no longer the first)
            if pid not in st.names and pid in st.partners:
                regrouped.add(pid)
                regrouped.update(st.partners.pop(pid))

        # the score and verdict: the changed products, and the ones matched with them before or now
        redo = set(new) | regrouped
        for pid in set(new) | gone:
            o, d = old_by.get(pid), by_id.get(pid)
            if o is None or d is None or (o["price"], o["in_stock"], o["store"]) != (d["price"], d["in_stock"],
                                                                                       d["store"]):
                redo.update(st.partners.get(pid) or ())
                if o is not None:
                    redo.update(m["id"] for m in o["matches"])
        material, differ = set(gone), set(gone)  # a new version / a new object to publish
        for pid in redo:
            if pid not in by_id:
                continue
            it = new.get(pid)
            if it is None:
                it = by_id[pid] = dict(by_id[pid])
            finder.finish(it, by_id, st.partners.get(pid))
            old = old_by.get(pid)
            if old is None:
                material.add(pid)
                differ.add(pid)
            elif old == it:
                by_id[pid] = old  # exactly as before: keep the old object
            else:
                differ.add(pid)
                if not _same(old, it):
                    material.add(pid)
        if differ:
            index = st.index.updated(old_by, by_id, differ)
            if index is None:
                index = Index.build([by_id[i] for i in sorted(by_id)], st.index.slot.keys())
                index.build_orders(by_id)
        else:
            index = st.index
        snap0 = self._snap
        info = {"full": False, "seen": len(ids), "changed": len(differ), "material": len(material),
                "buckets": len(dirty)}
        snap = Snapshot(self._version() if material else snap0.v, now, by_id, index,
                        index.brief(by_id) if differ else snap0.brief, info)
        st.by_id, st.index, st.seq = by_id, index, top
        self._meta = meta
        self._snap = snap
        return info


class _Rebuild(Exception):
    pass
