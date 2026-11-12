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

import json
import math
import time
from collections import Counter, defaultdict

from . import applinks, matching
from .units import unit_price

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
           "back_in_stock": False}
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
    if "steady" in d["flags"]:
        s -= 25
        why.append(f"same 'discounted' price for {d['days_at_price']:g} days: the old price may never be charged")
    if not d["in_stock"]:
        s -= 30
    return max(0, min(100, round(s))), why


def catalog(con, now: int = None) -> list[dict]:
    now = int(now or time.time())
    products = [dict(r) for r in con.execute("SELECT * FROM products WHERE price > 0")]
    if not products:
        return []
    hist = defaultdict(list)
    cutoff = now - WINDOW
    # the state each product was in when the window opened, then every change inside it
    for r in con.execute("""SELECT x.product_id, x.at, x.price, x.original_price, x.in_stock FROM prices x
                            JOIN (SELECT product_id, MAX(at) at FROM prices WHERE at <= ? GROUP BY product_id) l
                            ON x.product_id = l.product_id AND x.at = l.at""", (cutoff,)):
        hist[r["product_id"]].append(dict(r))
    for r in con.execute("SELECT product_id, at, price, original_price, in_stock FROM prices WHERE at > ?"
                         " ORDER BY at", (cutoff,)):
        hist[r["product_id"]].append(dict(r))

    items = []
    for p in products:
        rows = hist.get(p["id"]) or [{"at": p["first_seen"], "price": p["price"], "original_price": p["original_price"],
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
        up = unit_price(price, p["name"])
        items.append({
            "id": p["id"], "store": p["store"], "sku": p["sku"], "name": p["name"], "brand": p["brand"],
            "category": p["category"],
            "top_category": (p["category"] or ("Today's Deals" if extra.get("deal_pct") else "Other")).split(" / ")[0],
            "url": p["url"], "image": p["image"], "in_stock": bool(p["in_stock"]),
            "price": price, "original_price": orig, "usual_price": a["usual"], "low90": a["low90"],
            "discount_pct": pct, "store_discount_pct": store_pct, "drop_pct": drop_pct, "kind": kind,
            "saving": round(reference - price, 2) if reference else 0.0,
            "flags": flags, "deal_label": extra.get("deal_label"), "rank": extra.get("rank"),
            "since": rows[-1]["at"], "first_seen": p["first_seen"], "last_seen": p["last_seen"],
            "tracked_days": a["tracked_days"], "days_at_price": a["days_at_price"],
            "unit_price": up[0] if up else None, "unit": up[1] if up else None,
            "spark": _spark(rows, now), "links": applinks.links(p["store"], p["url"], extra),
        })

    by_id = {it["id"]: it for it in items}
    for pid, others in matching.match(items).items():
        it = by_id[pid]
        it["matches"] = sorted(({"id": o, "store": by_id[o]["store"], "price": by_id[o]["price"],
                                 "in_stock": by_id[o]["in_stock"]} for o in others), key=lambda m: m["price"])
        rivals = [m["price"] for m in it["matches"] if m["in_stock"]]
        it["cheapest"] = bool(rivals) and it["price"] <= min(rivals)
        it["cheaper_by"] = round(min(rivals) - it["price"], 2) if rivals and it["price"] < min(rivals) else 0.0
    for it in items:
        it.setdefault("matches", [])
        it.setdefault("cheapest", False)
        it.setdefault("cheaper_by", 0.0)
        it["score"], it["why"] = _score(it)
    return items


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
          offset: int = 0) -> dict:
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
        if skip != "category" and category and d["top_category"] != category:
            return False
        if price_min is not None and d["price"] < price_min or price_max is not None and d["price"] > price_max:
            return False
        if best and not d["cheapest"]:
            return False
        if "real" in status and "steady" in d["flags"]:
            return False
        for f in ("new", "lowest", "label", "back"):
            if f in status and f not in d["flags"]:
                return False
        if "compare" in status and not d["matches"]:
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
