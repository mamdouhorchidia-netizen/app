"""Every product, judged from its price history, and the filters and sorts the page uses.

catalog() builds one enriched list of all products (catalog.py keeps it up to date for the whole process);
query() filters, sorts and counts it.

A product is a deal when it is at least `min` % cheaper than either
  - the struck-through price the store shows (a *platform discount*), or
  - its usual price: the time-weighted median over the last 30 days (a *price drop*; needs 3+ days).
Flags: lowest (cheapest in 90 days, needs 7+ days), new (price moved in the last 24 h), steady (the store
shows a discount but the price hasn't moved in 14+ days: maybe never charged), back (back in stock in
the last 3 days), label (a store event deal such as "11.11 Deal").
The deal score (0-100) weighs all of that; `why` says it in words.
"""

import json
import math
import time
from collections import Counter, defaultdict
from statistics import median

from . import applinks, forecast, matching
from .units import unit_price, unit_text

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
    pts = spark_points(rows, points)
    return pts + [[now, pts[-1][1]]]


def spark_points(rows: list[dict], points: int = 24) -> list[list]:
    """The spark without its last step at `now` (which only depends on the clock)."""
    pts = [[r["at"], r["price"]] for r in rows if r["in_stock"]] or [[rows[-1]["at"], rows[-1]["price"]]]
    if len(pts) > points:
        step = len(pts) / points
        pts = [pts[int(i * step)] for i in range(points - 1)] + [pts[-1]]
    return pts


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


PRODUCT_COLUMNS = ("id, store, sku, name, brand, category, url, image, first_seen, last_seen, price, original_price,"
                   " in_stock, extra")


def load_products(con, ids=None) -> list[dict]:
    """The products the catalogue shows (a price above 0), in id order; only `ids` when given."""
    if ids is None:
        return [dict(r) for r in con.execute(f"SELECT {PRODUCT_COLUMNS} FROM products WHERE price > 0 ORDER BY id")]
    out = []
    ids = sorted(ids)
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        out += [dict(r) for r in con.execute(f"SELECT {PRODUCT_COLUMNS} FROM products WHERE price > 0 AND id IN"
                                             f" ({','.join('?' * len(part))}) ORDER BY id", part)]
    return out


def load_history(con, cutoff: int, ids=None) -> dict:
    """{product id: price rows, oldest first}: the row each product was at when the window opened (`cutoff`), then
    every change after it. Rows at the same second keep the order they were written in."""
    hist = defaultdict(list)
    if ids is None:
        state = defaultdict(list)
        for r in con.execute("""SELECT x.rowid AS rid, x.product_id, x.at, x.price, x.original_price, x.in_stock
                                FROM prices x JOIN (SELECT product_id, MAX(at) at FROM prices WHERE at <= ?
                                                    GROUP BY product_id) l
                                ON x.product_id = l.product_id AND x.at = l.at""", (cutoff,)):
            state[r["product_id"]].append(r)
        for pid, rows in state.items():
            rows.sort(key=lambda r: r["rid"])
            hist[pid] = [{"at": r["at"], "price": r["price"], "original_price": r["original_price"],
                          "in_stock": r["in_stock"]} for r in rows]
        for r in con.execute("SELECT product_id, at, price, original_price, in_stock FROM prices WHERE at > ?"
                             " ORDER BY at, rowid", (cutoff,)):
            hist[r["product_id"]].append({"at": r["at"], "price": r["price"], "original_price": r["original_price"],
                                          "in_stock": r["in_stock"]})
        return hist
    ids = sorted(ids)
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for r in con.execute("SELECT product_id, at, price, original_price, in_stock FROM prices WHERE product_id IN"
                             f" ({','.join('?' * len(part))}) ORDER BY product_id, at, rowid", part):
            hist[r["product_id"]].append({"at": r["at"], "price": r["price"], "original_price": r["original_price"],
                                          "in_stock": r["in_stock"]})
    for pid, rows in hist.items():
        hist[pid] = window(rows, cutoff)
    return hist


def window(rows: list[dict], cutoff: int) -> list[dict]:
    """A product's whole history (oldest first) cut the way load_history cuts it."""
    before = [r["at"] for r in rows if r["at"] <= cutoff]
    if not before:
        return rows
    first = max(before)
    i = next(i for i, r in enumerate(rows) if r["at"] == first)
    return rows[i:]


def item(p: dict, rows: list, now: int, parts: dict = None) -> dict:
    """One product judged from its history, without what depends on other products (matches, cheapest, the score
    and the verdict: finish() adds those) and with the spark's last step at `now` left out (catalog() adds it).
    `parts`, when given, keeps what valid_until() reuses."""
    rows = rows or [{"at": p["first_seen"], "price": p["price"], "original_price": p["original_price"],
                     "in_stock": p["in_stock"]}]
    a = analyse(rows, p["first_seen"], now)
    extra = json.loads(p.get("extra") or "{}")
    price, orig = p["price"], p["original_price"]
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
    if extra.get("deal_label"):
        flags.append("label")
    # dearer than it was lately: a struck-through price doesn't make that a deal
    if a["recent_low"] and price > a["recent_low"] * 1.05:
        flags.append("rose")
    # a "was" price more than 70 % above, never seen charged here: an inflated reference, not a discount
    if store_pct >= 70 and orig and max((r["price"] for r in rows if r["in_stock"]), default=0) < orig * 0.9:
        flags.append("inflated")
    up = unit_price(price, p["name"])
    return {
        "id": p["id"], "store": p["store"], "sku": p["sku"], "name": p["name"], "brand": p["brand"],
        "category": p["category"],
        "top_category": (p["category"] or ("Today's Deals" if extra.get("deal_pct") else "Other")).split(" / ")[0],
        "url": p["url"], "image": p["image"], "in_stock": bool(p["in_stock"]),
        "price": price, "original_price": orig, "usual_price": a["usual"], "low90": a["low90"],
        "discount_pct": pct, "store_discount_pct": store_pct, "drop_pct": drop_pct, "kind": kind,
        "saving": round(reference - price, 2) if reference else 0.0,
        "flags": flags, "deal_label": extra.get("deal_label"), "rank": extra.get("rank"),
        "since": rows[-1]["at"], "prev_price": rows[-2]["price"] if len(rows) > 1 else None,
        "recent_low": a["recent_low"], "recent_low_at": a["recent_low_at"], "first_seen": p["first_seen"],
        "last_seen": p["last_seen"],
        "tracked_days": a["tracked_days"], "days_at_price": a["days_at_price"],
        "unit_price": up[0] if up else None, "unit": up[1] if up else None,
        "unit_text": unit_text(price, p["name"]),
        "spark": spark_points(rows), "links": applinks.links(p["store"], p["url"], extra),
        "hub": extra.get("hub"), "hub_name": extra.get("hub_name"),
        "promo_code": extra.get("promo_code"), "promo_price": extra.get("promo_price"),
        "rhythm": forecast.rhythm(rows, now, parts) if len(rows) >= 3 else None,
    }


def judge(p: dict, rows: list, now: int) -> tuple[dict, float]:
    """(item(p, rows, now), valid_until(...)) at the cost of one."""
    parts = {}
    it = item(p, rows, now, parts)
    return it, valid_until(p, rows, it, now, parts)


def match_base(it: dict):
    """The talabat product a hub copy stands for (each hub keeps its own copy of the same product): only the first
    copy, in catalogue order, is matched with other stores. None for the other stores."""
    return it["sku"].split(":", 1)[-1] if it["store"] == "talabat" else None


def finish(it: dict, by_id: dict, others: list) -> dict:
    """Adds what depends on the other stores (`others`: the ids matching.match found for it) and the score and
    verdict that follow from everything. Changes `it` and returns it."""
    if others:
        it["matches"] = sorted(({"id": o, "store": by_id[o]["store"], "price": by_id[o]["price"],
                                 "in_stock": by_id[o]["in_stock"]} for o in others), key=lambda m: m["price"])
        rivals = [m["price"] for m in it["matches"] if m["in_stock"]]
        it["cheapest"] = bool(rivals) and it["price"] <= min(rivals)
        it["cheaper_by"] = round(min(rivals) - it["price"], 2) if rivals and it["price"] < min(rivals) else 0.0
    else:
        it["matches"], it["cheapest"], it["cheaper_by"] = [], False, 0.0
    it["score"], it["why"] = _score(it)
    it["verdict"] = verdict(it)
    return it


def with_now(it: dict, now: int) -> dict:
    """A copy of a catalogue item kept without its spark's last step, with that step at `now`."""
    sp = it["spark"]
    return {**it, "spark": sp + [[now, sp[-1][1]]]}


def catalog(con, now: int = None) -> list[dict]:
    now = int(now or time.time())
    products = load_products(con)
    if not products:
        return []
    hist = load_history(con, now - WINDOW)
    items = [item(p, hist.get(p["id"]), now) for p in products]
    by_id = {it["id"]: it for it in items}
    seen_base, to_match = set(), []
    for it in items:
        base = match_base(it)
        if base is not None:
            if base in seen_base:
                continue
            seen_base.add(base)
        to_match.append(it)
    found = matching.match(to_match)
    for it in items:
        finish(it, by_id, found.get(it["id"]))
        it["spark"].append([now, it["spark"][-1][1]])
    return items


# ---- how long an item stays as it is -------------------------------------------------------------------
# An item depends on the clock as well as on its rows: "tracked 3.4 days", the 30- and 90-day windows, the 24 h
# "new" flag, the usual price (a time-weighted median whose weights grow with time), the sale rhythm's "next sale
# in 2 days". valid_until() says until when an item judged at `now` is certainly unchanged (the spark's last step
# aside), so the live catalogue only judges a product again when its rows changed or that time has come.

MARGIN = 5  # seconds early, around values rounded for display (floating point can't move them by more)
NEVER = float("inf")


def _rounding_up(origin: float, now: int, step: float = 0.1) -> float:
    """x = (now - origin) / DAY grows: a time no later than the next change of round(x, 1)."""
    x = (now - origin) / DAY
    k = math.ceil((x - MARGIN / DAY) / step - 0.5)
    return max(now, origin + (k + 0.5) * step * DAY - MARGIN)


def _rounding_down(x: float, now: int, whole_days: bool = False) -> float:
    """x (days) falls by one per day: a time no later than the next change of round(max(0, x), 1) (and, with
    whole_days, of round(x) and of x >= 1)."""
    y = x + MARGIN / DAY
    best = (math.floor(y / 0.1 - 0.5) + 0.5) * 0.1
    if whole_days:
        if y >= 1.5:
            best = max(best, math.floor(y - 0.5) + 0.5)
        if y >= 1:
            best = max(best, 1.0)
    if best < 0:
        return NEVER
    return max(now, now + (x - best) * DAY - MARGIN)


def _median_until(rows: list[dict], now: int) -> float:
    """The 30-day time-weighted median: when it may next change with the clock alone. Each in-stock stretch's weight
    is linear in time (the current one grows, the one the window's start cuts shrinks); the median is the first
    price whose running share reaches half, so it can only move when one of those shares crosses half."""
    w0 = now - 30 * DAY
    groups = {}
    last = len(rows) - 1
    for i, r in enumerate(rows):
        start, end = r["at"], rows[i + 1]["at"] if i < last else now
        s = max(start, w0)
        if end > s and r["in_stock"]:
            slope = (1 if i == last else 0) - (1 if start < w0 else 0)
            g = groups.setdefault(r["price"], [0, 0])
            g[0] += end - s
            g[1] += slope
    if len(groups) < 2:
        return NEVER
    total = sum(g[0] for g in groups.values())
    total_slope = sum(g[1] for g in groups.values())
    acc = acc_slope = 0
    until = NEVER
    for price in sorted(groups):
        acc += groups[price][0]
        acc_slope += groups[price][1]
        f, slope = 2 * acc - total, 2 * acc_slope - total_slope
        if not slope:
            continue
        if f == 0:
            return now
        dt = -f / slope
        if dt > 0:
            until = min(until, now + math.floor(dt))
    return until


def _rhythm_until(rows: list[dict], now: int, r, parts: dict = None) -> float:
    if len(rows) < 3:
        return NEVER
    if parts is None or "regular" not in parts:
        parts = {}
        forecast.rhythm(rows, now, parts)
    until = NEVER
    regular, secs = parts.get("regular"), parts.get("secs")
    if secs:  # the regular price is the most-seen upper price, and the current one's time keeps growing
        cur = rows[-1]
        if cur["in_stock"] and cur["price"] and cur["price"] != regular and cur["price"] >= parts["mid"]:
            until = max(now, now + (secs[regular] - secs[cur["price"]]) - MARGIN)
    if r:
        eps = parts["eps"]
        if r["on_sale"]:
            if parts["lasts"] is not None:
                until = min(until, _rounding_down(parts["lasts"] - (now - eps[-1]["start"]) / DAY, now))
        else:
            until = min(until, _rounding_down((eps[-1]["start"] + parts["every"] * DAY - now) / DAY, now,
                                              whole_days=True))
    return until


def valid_until(p: dict, rows: list, it: dict, now: int, parts: dict = None) -> float:
    """A time until which item(p, rows, t) == item(p, rows, now) for every t in [now, that time), apart from the
    spark's last step: the clock alone changes nothing before it. `it` is item(p, rows, now)."""
    rows = rows or [{"at": p["first_seen"], "price": p["price"], "original_price": p["original_price"],
                     "in_stock": p["in_stock"]}]
    first, cur = p["first_seen"], rows[-1]["at"]
    if first > now or cur > now:
        return now  # dates in the future: judge it again every time
    best = min(_rounding_up(first, now), _rounding_up(cur, now))  # "tracked N days", "N days at this price"
    if cur == now:
        best = min(best, now + 1)  # the current stretch starts counting in the 30-day median
    if len(rows) == 1:  # one price: no window, median or rhythm can move
        return best
    for t in (cur + DAY, cur + 3 * DAY):  # "new", "back in stock"
        if now <= t < best:
            best = t
    for i, r in enumerate(rows):
        # the 30-day window passing a change (the median's weights, a stretch ending), the recent low's 31 days,
        # the 90-day low/high, and the 95-day window dropping its oldest row
        at = r["at"]
        for t in ((at + 30 * DAY, at + 31 * DAY, at + 90 * DAY, at + WINDOW) if i else (at + 30 * DAY, at + 31 * DAY)):
            if t >= now:
                if t < best:
                    best = t
                break
    return min(best, _median_until(rows, now), _rhythm_until(rows, now, it["rhythm"], parts))


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
        if "fake" in status and "steady" not in d["flags"] and "inflated" not in d["flags"]:  # the discounts that never move
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
    hits.sort(key=SORTS.get(sort, SORTS["score"]))
    facets = {
        "store": Counter(d["store"] for d in items if keep(d, "store")),
        "category": Counter(d["top_category"] for d in items if keep(d, "category")),
        "kind": Counter(d["kind"] for d in items if keep(d, "kind") and d["kind"]),
        "hub": Counter(f'{d["hub"]}|{d["hub_name"]}' for d in items if d["store"] == "talabat" and d["hub"]
                       and keep(d, "hub")),
    }
    return {"items": hits[offset:offset + limit], "total": len(hits),
            "facets": {k: dict(v.most_common()) for k, v in facets.items()}}


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
