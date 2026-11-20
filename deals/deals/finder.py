"""Every product, judged from its price history, and the filters and sorts the page uses.

catalog() builds one enriched list of all products (the web server keeps it for a few seconds);
query() filters, sorts and counts it.

A product is a deal when it is at least `min` % cheaper than either
  - the struck-through price the store shows (a *platform discount*), or
  - its usual price: the time-weighted median over the last 30 days (a *price drop*; needs 3+ days).
Flags: lowest (cheapest in 90 days, needs 7+ days), new (price moved in the last 24 h), steady (the store
shows a discount but the price hasn't moved in 14+ days: maybe never charged), back (back in stock in
the last 3 days), label (a store event deal such as "11.11 Deal").
The deal score (0-100) weighs all of that; `why` says it in words.
"""

import heapq
import json
import math
import time
from collections import Counter, defaultdict
from statistics import median

from . import applinks, db, forecast, matching, units
from .units import pack_size

DAY = 86400
WINDOW = 95 * DAY


def weighted_median(segments: list[tuple[float, float]]) -> float:
    """segments: (price, seconds at that price)."""
    segments = sorted(segments)
    total = sum(s for _, s in segments)
    acc = 0.0
    for price, secs in segments:
        acc += secs
        if acc >= total / 2:
            return price
    return segments[-1][0]


def analyse(rows: list[dict], first_seen: int, now: int) -> dict:
    """rows: this product's price rows, oldest first. Returns usual/lowest/flags for the latest price."""
    current = rows[-1]
    since_change = now - current["at"]
    out = {"usual": None, "low90": None, "high90": None, "tracked_days": round((now - first_seen) / DAY, 1),
           "days_at_price": round(since_change / DAY, 1), "changed_recently": since_change < DAY and len(rows) > 1,
           "back_in_stock": False, "recent_low": None, "recent_low_at": None}
    seg30, low90, high90 = [], None, None
    for i, r in enumerate(rows):
        start, end = r["at"], rows[i + 1]["at"] if i + 1 < len(rows) else now
        if end > now - 90 * DAY and r["in_stock"]:
            low90 = r["price"] if low90 is None else min(low90, r["price"])
            high90 = r["price"] if high90 is None else max(high90, r["price"])
        s = max(start, now - 30 * DAY)
        if end > s and r["in_stock"]:
            seg30.append((r["price"], end - s))
    if out["tracked_days"] >= 3 and seg30:
        out["usual"] = weighted_median(seg30)
    if out["tracked_days"] >= 7:
        out["low90"], out["high90"] = low90, high90
    # the lowest in-stock price of the last 30 days before this one, however short the history
    for r in rows[:-1]:
        if r["in_stock"] and r["at"] > now - 30 * DAY - DAY and (out["recent_low"] is None or r["price"] < out["recent_low"]):
            out["recent_low"], out["recent_low_at"] = r["price"], r["at"]
    if current["in_stock"] and len(rows) > 1 and not rows[-2]["in_stock"] and since_change < 3 * DAY:
        out["back_in_stock"] = True
    return out


def _spark(rows: list[dict], now: int, points: int = 24) -> list[list]:
    """Up to `points` (time, price) steps, the last at now."""
    pts = [[r["at"], r["price"]] for r in rows if r["in_stock"]] or [[rows[-1]["at"], rows[-1]["price"]]]
    if len(pts) > points:
        step = len(pts) / points
        pts = [pts[int(i * step)] for i in range(points - 1)] + [pts[-1]]
    return pts + [[now, pts[-1][1]]]


def _score(d: dict) -> tuple[int, list[str]]:
    why, s = [], 0.0
    pct = d["discount_pct"]
    if d["kind"] == "platform":
        why.append(f"{pct:g}% off the store's own price")
    elif d["kind"] == "drop":
        why.append(f"{pct:g}% under its usual {d['usual_price']:g} EGP")
    s += min(pct, 60) / 60 * 50
    if d["saving"] > 0:
        s += min(math.log10(1 + d["saving"]) / 3, 1) * 15  # 1000 EGP saved = full marks
    if "lowest" in d["flags"]:
        s += 15
        why.append("lowest price in 90 days")
    if d["kind"] == "platform" and d["drop_pct"] > 0:
        s += 10
        why.append(f"and {d['drop_pct']:g}% under its usual price")
    if "new" in d["flags"]:
        s += 5
        why.append("price changed in the last 24 h")
    if "label" in d["flags"]:
        s += 5
        why.append(d["deal_label"])
    if "back" in d["flags"]:
        why.append("back in stock")
    if d.get("cheapest") and d.get("matches"):
        s += 5
        why.append("cheapest of the stores that sell it")
    if "rose" in d["flags"]:
        s -= 40
        why.append(f"it was {d['recent_low']:g} EGP lately: {round(100 * (d['price'] / d['recent_low'] - 1)):g}% dearer now")
    if "inflated" in d["flags"]:
        s -= 35
        why.append(f"the 'was' price ({d['original_price']:g} EGP) has never been charged here: likely inflated")
    if "steady" in d["flags"]:
        s -= 25
        why.append(f"same 'discounted' price for {d['days_at_price']:g} days: the old price may never be charged")
    if not d["in_stock"]:
        s -= 30
    return max(0, min(100, round(s))), why


def catalog(con, now: int = None) -> list[dict]:
    """Every product (price > 0), judged at `now`, in id order. One full build; Catalog keeps one up to date."""
    return Catalog(track=False).refresh(con, now).items


# ---------------------------------------------------------------------------------------------------------------
# One item, in three steps that cost very different amounts:
#   _static   the product row alone: its extra JSON, app links, pack size and price per unit (the dear part)
#   _item     + its price history at `now`: analyse, flags, sparkline, rhythm
#   _finish   + the other stores' items: matches, cheapest, score, why, verdict
# A full build does all three for every product. Catalog.refresh redoes each step only for the products whose
# inputs to it changed, and knows from each product's history the next moment its judgement can change by the
# clock alone (_horizon), so time passing costs nothing until then.

def _static(p: dict, size=None, ex: dict = None) -> tuple[dict, dict]:
    """(the parts of an item that follow from the product row alone, what it read from the extra JSON).
    size: pack_size(name). ex: that second dict from before, when the store, address and extra JSON are the same."""
    price = p["price"]
    if ex is None:
        extra = json.loads(p.get("extra") or "{}")
        ex = {"deal_label": extra.get("deal_label"), "rank": extra.get("rank"),
              "links": applinks.links(p["store"], p["url"], extra), "hub": extra.get("hub"),
              "hub_name": extra.get("hub_name"), "promo_code": extra.get("promo_code"),
              "promo_price": extra.get("promo_price"), "deal_pct": extra.get("deal_pct")}
    up = units.unit_price_of(price, size)
    return {
        "id": p["id"], "store": p["store"], "sku": p["sku"], "name": p["name"], "brand": p["brand"],
        "category": p["category"],
        "top_category": (p["category"] or ("Today's Deals" if ex["deal_pct"] else "Other")).split(" / ")[0],
        "url": p["url"], "image": p["image"], "in_stock": bool(p["in_stock"]),
        "price": price, "original_price": p["original_price"], "deal_label": ex["deal_label"], "rank": ex["rank"],
        "first_seen": p["first_seen"], "last_seen": p["last_seen"],
        "unit_price": up[0] if up else None, "unit": up[1] if up else None,
        "unit_text": units.unit_text_of(price, size),
        "links": ex["links"], "hub": ex["hub"], "hub_name": ex["hub_name"],
        "promo_code": ex["promo_code"], "promo_price": ex["promo_price"],
    }, ex


def _item(s: dict, rows: list[dict], now: int) -> tuple[dict, dict]:
    """(item without the cross-store fields, analyse's answer). s: _static's dict, or a previous item of the
    same product row."""
    a = analyse(rows, s["first_seen"], now)
    price, orig = s["price"], s["original_price"]
    store_pct = round(100 * (orig - price) / orig, 1) if orig and orig > price else 0.0
    drop_pct = round(100 * (a["usual"] - price) / a["usual"], 1) if a["usual"] and a["usual"] > price else 0.0
    pct = max(store_pct, drop_pct)
    kind = "platform" if store_pct and store_pct >= drop_pct else "drop" if drop_pct else None
    reference = orig if kind == "platform" else a["usual"] if kind == "drop" else None
    flags = []
    if a["low90"] is not None and price <= a["low90"] and (a["high90"] or 0) > price:
        flags.append("lowest")
    if a["changed_recently"]:
        flags.append("new")
    if store_pct and not drop_pct and a["days_at_price"] >= 14:
        flags.append("steady")
    if a["back_in_stock"]:
        flags.append("back")
    if s["deal_label"]:
        flags.append("label")
    # dearer than it was lately: a struck-through price doesn't make that a deal
    if a["recent_low"] and price > a["recent_low"] * 1.05:
        flags.append("rose")
    # a "was" price more than 70 % above, never seen charged here: an inflated reference, not a discount
    if store_pct >= 70 and orig and max((r["price"] for r in rows if r["in_stock"]), default=0) < orig * 0.9:
        flags.append("inflated")
    return {
        "id": s["id"], "store": s["store"], "sku": s["sku"], "name": s["name"], "brand": s["brand"],
        "category": s["category"], "top_category": s["top_category"],
        "url": s["url"], "image": s["image"], "in_stock": s["in_stock"],
        "price": price, "original_price": orig, "usual_price": a["usual"], "low90": a["low90"],
        "discount_pct": pct, "store_discount_pct": store_pct, "drop_pct": drop_pct, "kind": kind,
        "saving": round(reference - price, 2) if reference else 0.0,
        "flags": flags, "deal_label": s["deal_label"], "rank": s["rank"],
        "since": rows[-1]["at"], "prev_price": rows[-2]["price"] if len(rows) > 1 else None,
        "recent_low": a["recent_low"], "recent_low_at": a["recent_low_at"], "first_seen": s["first_seen"],
        "last_seen": s["last_seen"], "tracked_days": a["tracked_days"], "days_at_price": a["days_at_price"],
        "unit_price": s["unit_price"], "unit": s["unit"], "unit_text": s["unit_text"],
        "spark": _spark(rows, now), "links": s["links"], "hub": s["hub"], "hub_name": s["hub_name"],
        "promo_code": s["promo_code"], "promo_price": s["promo_price"],
        "rhythm": forecast.rhythm(rows, now) if len(rows) >= 3 else None,
    }, a


def _finish(it: dict, others: list[dict]):
    """Adds matches, cheapest, cheaper_by, score, why and verdict. others: the matched items of other stores."""
    it["matches"] = sorted(({"id": o["id"], "store": o["store"], "price": o["price"], "in_stock": o["in_stock"]}
                            for o in others), key=lambda m: m["price"])
    rivals = [m["price"] for m in it["matches"] if m["in_stock"]]
    it["cheapest"] = bool(rivals) and it["price"] <= min(rivals)
    it["cheaper_by"] = round(min(rivals) - it["price"], 2) if rivals and it["price"] < min(rivals) else 0.0
    it["score"], it["why"] = _score(it)
    it["verdict"] = verdict(it)


# --- when the clock alone changes an item -------------------------------------------------------------------------
_MARGIN = 2  # seconds: float rounding in the crossing times below can't be off by this much
INF = float("inf")


_TOL = 1e-6  # days: a point this close counts as not yet passed (float division can land either side of it)


def _next_up(x: float, now: int, off: float, step: float) -> float:
    """x days, growing a day a day: the moment it reaches the next point off + k * step (round()'s edges)."""
    k = math.ceil((x - off) / step)
    if off + step * (k - 1) >= x - _TOL:  # (x - off) / step came out a hair above a whole number
        k -= 1
    return math.floor(now + (off + step * k - x) * DAY) - _MARGIN


def _next_down(y: float, now: int, off: float, step: float) -> float:
    """y days, shrinking a day a day: the moment it reaches the next point off + k * step below it."""
    k = math.floor((y - off) / step)
    if off + step * (k + 1) <= y + _TOL:  # (y - off) / step came out a hair below a whole number
        k += 1
    return math.floor(now + (y - off - step * k) * DAY) - _MARGIN


def _median_turn(rows: list[dict], now: int) -> float:
    """When analyse's 30-day weighted median ("usual") can next change, until the next segment edge (those are
    _horizon's own events). Inside that stretch each in-stock segment's length is linear in time: the last one
    grows, one cut by the window's start shrinks. The median is the lowest price P whose segments add up to half
    the total or more (2 * cum(P) >= total, all whole seconds), so each test flips at one exact second."""
    lo = now - 30 * DAY
    groups = {}
    last = len(rows) - 1
    for i, r in enumerate(rows):
        if not r["in_stock"]:
            continue
        start, end = r["at"], rows[i + 1]["at"] if i < last else now
        s = max(start, lo)
        if end <= s:
            continue
        g = groups.setdefault(r["price"], [0, 0])
        g[0] += end - s
        g[1] += (i == last) - (lo >= start)
    if len(groups) < 2:
        return INF
    total = sum(d for d, _ in groups.values())
    slope = sum(m for _, m in groups.values())
    cum = cum_slope = 0
    turn = INF
    for price in sorted(groups):
        d, m = groups[price]
        cum, cum_slope = cum + d, cum_slope + m
        g, gm = 2 * cum - total, 2 * cum_slope - slope
        if g >= 0:  # today's median: it stops being one when g drops below 0
            if gm < 0:
                turn = min(turn, now + g // -gm + 1)
            break
        if gm > 0:  # a cheaper price that will reach half
            turn = min(turn, now + (-g + gm - 1) // gm)
    return turn


def _rhythm_turn(rows: list[dict], now: int) -> float:
    """When forecast.rhythm's answer can next change by the clock alone."""
    secs = {}
    for i, r in enumerate(rows):
        end = rows[i + 1]["at"] if i + 1 < len(rows) else now
        if r["in_stock"] and r["price"]:
            secs[r["price"]] = secs.get(r["price"], 0) + max(0, end - r["at"])
    regular = forecast.regular_price(rows, now)
    if not regular:
        return INF  # no in-stock price at all: never one
    turn = INF
    last = rows[-1]
    # only the last row's time grows: its price takes over as "regular" when it has been charged longest
    if last["in_stock"] and last["price"] and last["price"] != regular and last["price"] >= median(secs):
        lead = secs[regular] - secs[last["price"]] + (0 if last["price"] > regular else 1)
        turn = now + max(1, math.ceil(lead))
    eps = forecast.episodes(rows, regular, now)
    if len(eps) < 2:
        return turn
    starts = [e["start"] for e in eps]
    every = median(b - a for a, b in zip(starts, starts[1:])) / DAY
    done = [e for e in eps if e["end"]]
    lasts = median(e["end"] - e["start"] for e in done) / DAY if done else None
    if eps[-1]["end"] is None:  # on sale: ends_in_days counts down (its text follows it)
        if lasts is not None:
            left = lasts - (now - eps[-1]["start"]) / DAY
            if round(max(0.0, left), 1) > 0:
                turn = min(turn, _next_down(left, now, 0.05, 0.1))
    else:  # next_in_days counts down; the text rounds it to whole days from 1 day up
        due = (eps[-1]["start"] + every * DAY - now) / DAY
        if round(max(0.0, due), 1) > 0 or due >= 1:
            turn = min(turn, _next_down(due, now, 0.05, 0.1))
        if due >= 1:
            turn = min(turn, _next_down(due, now, 0.5, 1.0), _next_down(due, now, 0.0, 1.0))
    return turn


def _horizon(rows: list[dict], first_seen: int, now: int, a: dict) -> float:
    """The first moment at which this product's item can differ from the one made at `now` (apart from the
    sparkline's last point) while nothing new is written: before it, the item can be reused. Errs early, never
    late: an early answer costs one recompute, a late one would be a stale item."""
    cur = rows[-1]["at"]
    if cur >= now or first_seen >= now or rows[0]["at"] >= now:
        return now + 1  # history from the future (another machine's clock): don't plan, look again
    turn = min(_next_up((now - first_seen) / DAY, now, 0.05, 0.1),  # tracked_days (and its 3 / 7 day steps)
               _next_up((now - cur) / DAY, now, 0.05, 0.1))  # days_at_price (and "steady" at 14)
    for t in (cur + DAY, cur + 3 * DAY):  # "new", "back"
        if t > now:
            turn = min(turn, t)
    first = rows[0]["at"]
    for r in rows:  # the window's opening row is replaced when a later one falls out of the 95 days
        if r["at"] > first:
            turn = min(turn, r["at"] + WINDOW)
            break
    last = len(rows) - 1
    for i, r in enumerate(rows):
        t = r["at"] + 30 * DAY  # the 30-day window starts cutting this segment
        if t > now:
            turn = min(turn, t)
        if i < last:
            for t in (r["at"] + 31 * DAY, rows[i + 1]["at"] + 30 * DAY, rows[i + 1]["at"] + 90 * DAY):
                if t > now:  # out of recent_low; out of the 30-day median; out of the 90-day low/high
                    turn = min(turn, t)
    if a["tracked_days"] >= 3:
        turn = min(turn, _median_turn(rows, now))
    if len(rows) >= 3:
        turn = min(turn, _rhythm_turn(rows, now))
    return turn


# --- reading the history ------------------------------------------------------------------------------------------

def _history(con, cutoff: int, ids=None) -> dict:
    """product id -> its price rows: the one in force when the window opened, then every one inside it (oldest
    first). ids: only these products (in batches, by the (product_id, at) index); None: all of them."""
    hist = defaultdict(list)
    if ids is None:
        for r in con.execute("""SELECT x.product_id, x.at, x.price, x.original_price, x.in_stock FROM prices x
                                JOIN (SELECT product_id, MAX(at) at FROM prices WHERE at <= ? GROUP BY product_id) l
                                ON x.product_id = l.product_id AND x.at = l.at""", (cutoff,)):
            hist[r["product_id"]].append(dict(r))
        for r in con.execute("SELECT product_id, at, price, original_price, in_stock FROM prices WHERE at > ?"
                             " ORDER BY at", (cutoff,)):
            hist[r["product_id"]].append(dict(r))
        return hist
    ids = sorted(ids)
    for i in range(0, len(ids), _BATCH):
        chunk = ids[i:i + _BATCH]
        marks = ",".join("?" * len(chunk))
        for r in con.execute(f"""SELECT x.product_id, x.at, x.price, x.original_price, x.in_stock FROM prices x
                                 JOIN (SELECT product_id, MAX(at) at FROM prices
                                       WHERE product_id IN ({marks}) AND at <= ? GROUP BY product_id) l
                                 ON x.product_id = l.product_id AND x.at = l.at
                                 ORDER BY x.product_id, x.at, x.rowid""", (*chunk, cutoff)):
            hist[r["product_id"]].append(dict(r))
        for r in con.execute(f"SELECT product_id, at, price, original_price, in_stock FROM prices"
                             f" WHERE product_id IN ({marks}) AND at > ? ORDER BY product_id, at, rowid",
                             (*chunk, cutoff)):
            hist[r["product_id"]].append(dict(r))
    return hist


def _rows(hist: dict, p: dict) -> list[dict]:
    return hist.get(p["id"]) or [{"at": p["first_seen"], "price": p["price"], "original_price": p["original_price"],
                                  "in_stock": p["in_stock"]}]


_BATCH = 500
_ROW = ("store", "sku", "name", "brand", "category", "url", "image", "first_seen", "price", "original_price",
        "in_stock", "extra")  # what an item reads from its product row, besides last_seen
_TOUCH_MARGIN = 120  # s: a product row written this long before the last refresh's moment is still looked at
_TOUCH_SKEW = 10  # s: ... and one written this close to it is read again even if last_seen looks the same


class Built:
    """What Catalog.refresh made: the items, and for a step (full False) the items that changed (id -> item;
    the rest are the previous ones with only their sparkline's last point and last_seen moved) and the ids gone."""
    __slots__ = ("items", "v", "now", "full", "changed", "removed", "stats")

    def __init__(self, items, v, now, full, changed, removed, stats):
        self.items, self.v, self.now, self.full = items, v, now, full
        self.changed, self.removed, self.stats = changed, removed, stats


_UNSET = object()  # a matching bucket not worked out yet (only matched items need theirs)


class _Product:
    __slots__ = ("key", "size", "ex", "mkey", "horizon", "item")

    def __init__(self, key, size, ex, mkey, horizon, item):
        self.key, self.size, self.ex, self.mkey, self.horizon, self.item = key, size, ex, mkey, horizon, item


class Catalog:
    """The catalog, kept up to date between refreshes instead of rebuilt.

    refresh(con, now) returns the same items a full build (catalog()) would at `now`, but recomputes only:
    - products with a new price row (prices.rowid above the last refresh's data version) or a changed product row
      (last_seen moved: write_offers sets it on every check), with their history read again;
    - products whose judgement the clock has changed since (each item knows the first moment it can: _horizon);
    - the matching buckets (pack size + first word) that gained, lost or renamed a member, and the items whose
      matches or matched rivals' prices changed.
    Every other item is the previous one with its sparkline ending at `now` (and its last_seen) brought forward.
    A full build happens on the first refresh, when asked (full=True), and when something can't be followed
    step by step: rows deleted, the data version or the clock going backwards, an error half way.
    Call refresh inside one read transaction, so `v` and what is read describe the same data. Items handed out
    are never changed afterwards: readers can keep an old list while a new one is made.
    """

    def __init__(self, track: bool = True):
        self.track = track  # False: a one-off full build (no horizons, nothing kept for next time)
        self.reset()

    def reset(self):
        self.v = None
        self.now = None
        self.products: dict[int, _Product] = {}
        self.seen: dict[int, int] = {}  # every product row (price > 0 or not) -> last_seen
        self.order: list[int] = []
        self.order_set: set = set()
        self.heap: list = []  # (horizon, id), stale entries skipped
        self.base_of: dict[int, str] = {}  # talabat id -> its product across hubs
        self.reps: dict[str, set] = {}  # ... and back: the lowest id stands for them all in matching
        self.member: dict[int, tuple] = {}  # matched ids -> bucket (None: can't match)
        self.buckets: dict[tuple, set] = {}
        self.bucket_out: dict[tuple, dict] = {}
        self.matches: dict[int, list] = {}  # id -> matched ids, in matching.match's order

    def refresh(self, con, now: int = None, full: bool = False) -> Built:
        now = int(now or time.time())
        v = db.data_version(con)
        if full or not self.track or self.v is None or v < self.v or now < self.now:
            return self._full(con, now, v)
        try:
            return self._step(con, now, v)
        except BaseException:
            self.reset()  # half-updated state: the next refresh builds afresh
            raise

    # -- the full build ----------------------------------------------------------------------------------------
    def _full(self, con, now: int, v: int) -> Built:
        started = time.perf_counter()
        self.reset()
        rows_all = [dict(r) for r in con.execute("SELECT * FROM products")]
        hist = _history(con, now - WINDOW)
        items = []
        for p in rows_all:
            self.seen[p["id"]] = p["last_seen"]
            if not (p["price"] is not None and p["price"] > 0):
                continue
            size = pack_size(p["name"])
            rows = _rows(hist, p)
            s, ex = _static(p, size)
            it, a = _item(s, rows, now)
            items.append(it)
            if self.track:
                horizon = _horizon(rows, p["first_seen"], now, a)
                self.products[p["id"]] = _Product(tuple(p[k] for k in _ROW), size, ex, _UNSET, horizon, it)
                if horizon < INF:
                    self.heap.append((horizon, p["id"]))
            else:
                self.products[p["id"]] = _Product(None, size, None, _UNSET, INF, it)
        items.sort(key=lambda d: d["id"])  # what a scan of the table gives: rowid order
        heapq.heapify(self.heap)
        self.order = [d["id"] for d in items]
        self.order_set = set(self.order)
        by_id = {d["id"]: d for d in items}
        # one talabat item per product for matching (each hub keeps its own copy of the same product)
        names = {}
        for it in items:
            if it["store"] == "talabat":
                base = it["sku"].split(":", 1)[-1]
                self.base_of[it["id"]] = base
                if base in self.reps:
                    self.reps[base].add(it["id"])
                    continue
                self.reps[base] = {it["id"]}
            st = self.products[it["id"]]
            st.mkey, names[it["id"]] = matching.key(it["name"], st.size)
            self.member[it["id"]] = st.mkey
            if st.mkey:
                self.buckets.setdefault(st.mkey, set()).add(it["id"])
        for k, ids in self.buckets.items():
            out = matching.match_group([(i, by_id[i]["store"], names[i]) for i in sorted(ids)])
            if out:
                self.bucket_out[k] = out
                self.matches.update(out)
        for it in items:
            _finish(it, [by_id[o] for o in self.matches.get(it["id"], ())])
        self.v, self.now = v, now
        if not self.track:
            self.reset()
        return Built(items, v, now, True, None, (), {"full": True, "products": len(items),
                                                     "secs": round(time.perf_counter() - started, 3)})

    # -- one step ----------------------------------------------------------------------------------------------
    def _step(self, con, now: int, v: int) -> Built:
        started = time.perf_counter()
        prev_now = self.now
        # product rows written since the last refresh: last_seen moved (or was written close to that moment)
        look = set()
        for pid, seen in con.execute("SELECT id, last_seen FROM products WHERE last_seen >= ?",
                                     (prev_now - _TOUCH_MARGIN,)):
            if seen >= prev_now - _TOUCH_SKEW or self.seen.get(pid) != seen:
                look.add(pid)
        # (not DISTINCT: that would scan a whole index instead of the rowids above v)
        priced = {pid for (pid,) in con.execute("SELECT product_id FROM prices WHERE rowid > ?", (self.v,))}
        look |= priced
        fresh = {}
        ids = sorted(look)
        for i in range(0, len(ids), _BATCH):
            chunk = ids[i:i + _BATCH]
            for r in con.execute(f"SELECT * FROM products WHERE id IN ({','.join('?' * len(chunk))})", chunk):
                fresh[r["id"]] = dict(r)
        count = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        if count != len(self.seen) + sum(pid not in self.seen for pid in fresh):
            return self._full(con, now, v)  # a product row went away: start over

        products = self.products
        static, redo, renamed, removed, seen_only = {}, set(), set(), set(), {}
        for pid, p in fresh.items():
            self.seen[pid] = p["last_seen"]
            old = products.get(pid)
            if not (p["price"] is not None and p["price"] > 0):
                if old is not None:
                    removed.add(pid)
                continue
            key = tuple(p[k] for k in _ROW)
            if old is not None and old.key == key:
                if p["last_seen"] != old.item["last_seen"]:
                    seen_only[pid] = p["last_seen"]
                if pid in priced:
                    redo.add(pid)
                continue
            same_name = old is not None and old.key[2] == key[2]
            size = old.size if same_name else pack_size(p["name"])
            reuse = old is not None and old.key[0] == key[0] and old.key[5] == key[5] and old.key[11] == key[11]
            static[pid] = (*_static(p, size, old.ex if reuse else None), key, size)
            redo.add(pid)
            if old is None or not same_name or old.key[0] != key[0] or old.key[1] != key[1]:
                renamed.add(pid)  # matching reads store, sku and name
        # the clock: items whose horizon has come
        heap = self.heap
        while heap and heap[0][0] <= now:
            h, pid = heapq.heappop(heap)
            st = products.get(pid)
            if st is not None and st.horizon == h and pid not in removed:
                redo.add(pid)
        redo -= removed

        # step 2: history and judgement for the products that need it
        hist = _history(con, now - WINDOW, redo) if redo else {}
        new_items = {}
        moved = set()  # price, stock or store changed: their matches' rivals see it
        for pid in redo:
            st = products.get(pid)
            if pid in static:
                s, ex, key, size = static[pid]
            else:
                s = st.item if pid not in seen_only else dict(st.item, last_seen=seen_only[pid])
                ex, key, size = st.ex, st.key, st.size
            rows = _rows(hist, s)
            it, a = _item(s, rows, now)
            horizon = _horizon(rows, s["first_seen"], now, a)
            if st is None:
                st = products[pid] = _Product(key, size, ex, None, horizon, None)
            else:
                o = st.item
                if o["price"] != it["price"] or o["in_stock"] != it["in_stock"] or o["store"] != it["store"]:
                    moved.add(pid)
                st.key, st.size, st.ex, st.horizon = key, size, ex, horizon
            if pid in renamed:
                st.mkey = _UNSET
            if horizon < INF:
                heapq.heappush(heap, (horizon, pid))
            new_items[pid] = it

        # step 3: matching, for the buckets whose members changed
        def current(pid):
            return new_items.get(pid) or products[pid].item

        rematched = self._rematch(renamed | removed, removed, current)
        for pid in removed:
            del products[pid]
        # step 4: matches, score and verdict where the item or its rivals changed
        finish = set(new_items) | rematched
        for pid in moved:
            finish.update(self.matches.get(pid, ()))
        finish -= removed
        for pid in finish:
            it = new_items.get(pid)
            if it is None:  # same item, other stores moved: copy it with the time brought forward
                it = new_items[pid] = self._carry(products[pid].item, now, seen_only.get(pid))
            _finish(it, [current(o) for o in self.matches.get(pid, ())])
        # everything else: the previous item, with the sparkline's end at now (and last_seen) brought forward
        added = [pid for pid in new_items if pid not in self.order_set]
        if removed or added:
            self.order_set = (self.order_set - removed) | set(added)
            self.order = sorted(self.order_set)
        items = []
        append, fresh_item, touched = items.append, new_items.get, seen_only.get
        for pid in self.order:  # (the loop every refresh runs over every product: kept lean)
            st = products[pid]
            it = fresh_item(pid)
            if it is None:
                old = st.item
                it = old.copy()
                spark = old["spark"]
                it["spark"] = spark[:-1] + [[now, spark[-1][1]]]
                last_seen = touched(pid)
                if last_seen is not None:
                    it["last_seen"] = last_seen
                st.item = it
            else:
                st.item = it
            append(it)
        self.v, self.now = v, now
        if len(heap) > 4 * len(products) + 1000:  # drop the stale entries now and then
            self.heap = [(st.horizon, pid) for pid, st in products.items() if st.horizon < INF]
            heapq.heapify(self.heap)
        changed = {pid: new_items[pid] for pid in finish}
        return Built(items, v, now, False, changed, removed, {
            "full": False, "products": len(items), "looked": len(look), "priced": len(priced),
            "static": len(static), "judged": len(redo), "rematched": len(rematched), "finished": len(finish),
            "secs": round(time.perf_counter() - started, 3)})

    @staticmethod
    def _carry(old: dict, now: int, last_seen=None) -> dict:
        it = old.copy()
        spark = old["spark"]
        it["spark"] = spark[:-1] + [[now, spark[-1][1]]]
        if last_seen is not None:
            it["last_seen"] = last_seen
        return it

    def _rematch(self, changed: set, removed: set, current) -> set:
        """Updates the matching buckets for products added, removed or renamed; returns the ids whose matches
        (the list of ids) changed."""
        products = self.products
        bases = set()
        for pid in changed:
            old = self.base_of.pop(pid, None)
            if old is not None:
                self.reps[old].discard(pid)
                bases.add(old)
            if pid not in removed:
                it = current(pid)
                if it["store"] == "talabat":
                    base = it["sku"].split(":", 1)[-1]
                    self.base_of[pid] = base
                    self.reps.setdefault(base, set()).add(pid)
                    bases.add(base)
        check = set(changed)
        for base in bases:
            check |= self.reps.get(base, set())
            if not self.reps.get(base):
                self.reps.pop(base, None)
        dirty = set()
        for pid in check:
            gone = pid in removed
            if gone:
                want = False
            else:
                base = self.base_of.get(pid)
                want = base is None or pid == min(self.reps[base])
            new_key = None
            if want:
                st = products[pid]
                if st.mkey is _UNSET:
                    st.mkey = matching.key(current(pid)["name"], st.size)[0]
                new_key = st.mkey
            had = pid in self.member
            old_key = self.member.get(pid)
            if pid not in changed and had == want and old_key == new_key:
                continue
            if had:
                del self.member[pid]
                if old_key:
                    self.buckets[old_key].discard(pid)
                    dirty.add(old_key)
            if want:
                self.member[pid] = new_key
                if new_key:
                    self.buckets.setdefault(new_key, set()).add(pid)
                    dirty.add(new_key)
        before, after = {}, {}
        for k in dirty:
            ids = sorted(self.buckets.get(k, ()))
            if not ids:
                self.buckets.pop(k, None)
            out = matching.match_group([(i, current(i)["store"], matching.words(current(i)["name"])) for i in ids])
            before.update(self.bucket_out.pop(k, {}))  # (a renamed product may leave one bucket for another)
            if out:
                self.bucket_out[k] = out
                after.update(out)
        rematched = {pid for pid in before.keys() | after.keys() if before.get(pid) != after.get(pid)}
        for pid in rematched:
            if pid in after:
                self.matches[pid] = after[pid]
            else:
                self.matches.pop(pid, None)
        return rematched


def verdict(d: dict) -> dict:
    """Buy now / Good price / Fair price / Wait, from what the price has done. {"label", "tone", "why"}."""
    usual, price = d.get("usual_price"), d["price"]
    rival = min((m["price"] for m in d.get("matches") or [] if m["in_stock"]), default=None)
    if not d["in_stock"]:
        return {"label": "Out of stock", "tone": "muted", "why": "Watch it to hear when it's back."}
    if rival is not None and rival < price * 0.97:
        return {"label": "Cheaper elsewhere", "tone": "warn", "why": f"{rival:g} EGP in another store."}
    if "rose" in d["flags"]:
        return {"label": "Wait", "tone": "warn",
                "why": f"It was {d['recent_low']:g} EGP lately: it's {round(100 * (price / d['recent_low'] - 1)):g}% dearer now."}
    if "steady" in d["flags"]:
        return {"label": "Fair price", "tone": "muted", "why": "The 'discount' never moves: it's the normal price."}
    if "inflated" in d["flags"]:
        return {"label": "Check the price", "tone": "warn",
                "why": "The 'was' price looks inflated: compare with other stores before buying."}
    if "lowest" in d["flags"] or (d["discount_pct"] >= 25 and d["kind"]):
        return {"label": "Buy now", "tone": "good",
                "why": "Lowest price in 90 days." if "lowest" in d["flags"] else f"{d['discount_pct']:g}% off."}
    r = d.get("rhythm")
    if r and not r["on_sale"] and r["next_in_days"] is not None and r["next_in_days"] <= 10 \
            and r["typical_sale"] <= price * 0.93:
        return {"label": "Wait", "tone": "warn", "why": r["text"]}
    if usual and price > usual * 1.05:
        return {"label": "Wait", "tone": "warn", "why": f"Usually {usual:g} EGP: it's above its normal price now."}
    if d["discount_pct"] >= 10:
        return {"label": "Good price", "tone": "good", "why": f"{d['discount_pct']:g}% off."}
    return {"label": "Fair price", "tone": "muted",
            "why": f"Its usual price ({usual:g} EGP)." if usual else "Not enough history yet to say more."}


SORTS = {
    "score": lambda d: (-d["score"], -d["discount_pct"]),
    "discount": lambda d: (-d["discount_pct"], -d["saving"]),
    "saving": lambda d: (-d["saving"], -d["discount_pct"]),
    "newest": lambda d: (-d["since"], -d["discount_pct"]),
    "drop": lambda d: (-d["drop_pct"], -d["discount_pct"]),
    "price": lambda d: (d["price"],),
    "price_desc": lambda d: (-d["price"],),
    "unit": lambda d: (d["unit_price"] is None, d["unit"] or "", d["unit_price"] or 0),  # per L, then kg, then piece
    "popular": lambda d: (d["rank"] is None, d["rank"] or 0, -d["discount_pct"]),
    "name": lambda d: (d["name"].lower(),),
}


def query(items: list[dict], deals_only: bool = True, stores: list[str] = None, kind: str = None,
          min_pct: float = 0, max_pct: float = None, stock: str = "in", status: list[str] = None,
          category: str = None, q: str = None, price_min: float = None, price_max: float = None,
          ids: list[int] = None, best: bool = False, sort: str = "score", limit: int = 200,
          offset: int = 0, hubs: list[int] = None) -> dict:
    """Returns {"items", "total", "facets"}. Facets count the matches per store, category and kind,
    each ignoring its own filter, so the page can show "(123)" next to every choice."""
    hits, facets = select(items, deals_only=deals_only, stores=stores, kind=kind, min_pct=min_pct, max_pct=max_pct,
                          stock=stock, status=status, category=category, q=q, price_min=price_min,
                          price_max=price_max, ids=ids, best=best, sort=sort, hubs=hubs)
    return {"items": hits[offset:offset + limit], "total": len(hits), "facets": facets}


def select(items: list[dict], deals_only: bool = True, stores: list[str] = None, kind: str = None,
           min_pct: float = 0, max_pct: float = None, stock: str = "in", status: list[str] = None,
           category: str = None, q: str = None, price_min: float = None, price_max: float = None,
           ids: list[int] = None, best: bool = False, sort: str = "score", hubs: list[int] = None) -> tuple[list, dict]:
    """query() before the page is cut: (every match, sorted; facets), in one pass over the items. The web server
    keeps this per filter set, so "more" and the 10-second refresh only slice it."""
    words = [w for w in (q or "").lower().split() if w]
    status = set(status or [])
    stores = set(stores or [])
    hubs = set(hubs) if hubs else None
    flag_needs = [f for f in ("new", "lowest", "label", "back") if f in status]
    min_pct = min_pct or 0

    def common(d) -> bool:
        """The filters no facet ignores."""
        if deals_only and not (d["kind"] and d["discount_pct"] > 0):
            return False
        if d["discount_pct"] < min_pct and (deals_only or min_pct):
            return False
        if max_pct is not None and d["discount_pct"] > max_pct:
            return False
        if stock == "in" and not d["in_stock"] or stock == "out" and d["in_stock"]:
            return False
        if price_min is not None and d["price"] < price_min or price_max is not None and d["price"] > price_max:
            return False
        if best and not d["cheapest"]:
            return False
        if status:
            flags = d["flags"]
            if "real" in status and ("steady" in flags or "inflated" in flags or "rose" in flags):
                return False
            for f in flag_needs:
                if f not in flags:
                    return False
            if "compare" in status and not d["matches"]:
                return False
            if "fake" in status and "steady" not in flags and "inflated" not in flags:  # discounts that never move
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

    hits = []
    f_store, f_cat, f_kind, f_hub = Counter(), Counter(), Counter(), Counter()
    for d in items:
        if ids is not None:  # a list of products: only the store filter applies
            if d["id"] not in ids:
                continue
            miss_kind = miss_hub = miss_cat = False
        else:
            if not common(d):
                continue
            miss_kind = bool(kind) and d["kind"] != kind
            miss_hub = bool(hubs) and d["store"] == "talabat" and d["hub"] not in hubs
            miss_cat = bool(category) and d["top_category"] != category
        miss_store = bool(stores) and d["store"] not in stores
        misses = miss_kind + miss_hub + miss_cat + miss_store
        if misses > 1:
            continue
        # a facet counts the items that pass every filter but its own
        if not misses or miss_store:
            f_store[d["store"]] += 1
        if not misses or miss_cat:
            f_cat[d["top_category"]] += 1
        if (not misses or miss_kind) and d["kind"]:
            f_kind[d["kind"]] += 1
        if (not misses or miss_hub) and d["store"] == "talabat" and d["hub"]:
            f_hub[f'{d["hub"]}|{d["hub_name"]}'] += 1
        if not misses:
            hits.append(d)
    hits.sort(key=SORTS.get(sort, SORTS["score"]))
    facets = {"store": f_store, "category": f_cat, "kind": f_kind, "hub": f_hub}
    return hits, {k: dict(v.most_common()) for k, v in facets.items()}


def find_deals(con, min_pct: float = 5, store: str = None, q: str = None, category: str = None,
               sort: str = "discount", limit: int = 200, now: int = None) -> list[dict]:
    """The deals in stock at least `min_pct` off (the original simple form)."""
    items = catalog(con, now)
    if category:
        items = [d for d in items if (d["category"] or "").startswith(category)]
    return query(items, min_pct=min_pct, stores=[store] if store else None, q=q, sort=sort, limit=limit)["items"]


def brief(items: list[dict], now: int = None) -> dict:
    """Today in 30 seconds: what moved, the record lows, the fake discounts caught, what's about to go on sale."""
    now = int(now or time.time())
    live = [d for d in items if d["in_stock"]]
    deals = [d for d in live if d["kind"] and d["discount_pct"] > 0
             and not {"steady", "inflated", "rose"} & set(d["flags"])]
    dropped = [d for d in deals if "new" in d["flags"]]
    lows = [d for d in deals if "lowest" in d["flags"]]
    fake = [d for d in live if "steady" in d["flags"] or "inflated" in d["flags"]]
    soon = [d for d in live if d.get("rhythm") and d["rhythm"]["next_in_days"] is not None
            and d["rhythm"]["next_in_days"] <= 3]
    promo = [d for d in live if d.get("promo_code")]
    pick = lambda d: {k: d[k] for k in ("id", "name", "store", "price", "original_price", "usual_price", "image",
                                          "discount_pct", "saving", "kind", "flags", "score", "since")}
    best = max(deals, key=lambda d: d["saving"], default=None)
    return {"deals": len(deals), "dropped_24h": len(dropped), "record_lows": len(lows), "fake": len(fake),
            "soon": len(soon), "promo": len(promo),
            "saving_today": round(sum(d["saving"] for d in dropped), 2),
            "top": [pick(d) for d in sorted(dropped or deals, key=lambda d: (-d["score"], -d["discount_pct"]))[:10]],
            "biggest_saving": pick(best) if best else None}
