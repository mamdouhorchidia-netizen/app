"""Which products are deals right now, judged from the price history.

A product is a deal when it is in stock and at least `min_pct` cheaper than either
  - the struck-through price the store shows (store discount), or
  - its usual price: the time-weighted median of what it cost over the last 30 days.
Extra labels: "lowest" (cheapest in 90 days), "new" (price moved in the last 24 hours) and
"steady" (the store shows a discount but the price has not changed in 14+ days, so the
struck-through price may never have been charged).
"""

import time
from collections import defaultdict

DAY = 86400


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
    out = {"usual": None, "low90": None, "tracked_days": round((now - first_seen) / DAY, 1),
           "days_at_price": round(since_change / DAY, 1), "changed_recently": since_change < DAY and len(rows) > 1}
    # Time at each price within the windows; a row lasts until the next one (the last until now).
    seg30, low90 = [], None
    for i, r in enumerate(rows):
        start, end = r["at"], rows[i + 1]["at"] if i + 1 < len(rows) else now
        if end > now - 90 * DAY and r["in_stock"]:
            low90 = r["price"] if low90 is None else min(low90, r["price"])
        s = max(start, now - 30 * DAY)
        if end > s and r["in_stock"]:
            seg30.append((r["price"], end - s))
    if out["tracked_days"] >= 3 and seg30:
        out["usual"] = weighted_median(seg30)
    if out["tracked_days"] >= 7:
        out["low90"] = low90
    return out


def find_deals(con, min_pct: float = 5, store: str = None, q: str = None, category: str = None,
               sort: str = "discount", limit: int = 200, now: int = None) -> list[dict]:
    now = int(now or time.time())
    where, args = ["p.in_stock = 1", "p.price > 0"], []
    if store:
        where.append("p.store = ?"); args.append(store)
    if q:
        where.append("p.name LIKE ?"); args.append(f"%{q}%")
    if category:
        where.append("p.category LIKE ?"); args.append(f"{category}%")
    products = [dict(r) for r in con.execute(f"SELECT * FROM products p WHERE {' AND '.join(where)}", args)]
    if not products:
        return []
    hist = defaultdict(list)
    ids = [p["id"] for p in products]
    for chunk in range(0, len(ids), 900):
        part = ids[chunk:chunk + 900]
        for r in con.execute(f"SELECT product_id, at, price, original_price, in_stock FROM prices"
                             f" WHERE product_id IN ({','.join('?' * len(part))}) ORDER BY at", part):
            hist[r["product_id"]].append(dict(r))

    deals = []
    for p in products:
        rows = hist.get(p["id"])
        if not rows:
            continue
        a = analyse(rows, p["first_seen"], now)
        price, orig = p["price"], p["original_price"]
        store_pct = round(100 * (orig - price) / orig, 1) if orig and orig > price else 0.0
        drop_pct = round(100 * (a["usual"] - price) / a["usual"], 1) if a["usual"] and a["usual"] > price else 0.0
        pct = max(store_pct, drop_pct)
        if pct <= 0 or pct < min_pct:
            continue
        reference = orig if store_pct >= drop_pct else a["usual"]
        flags = []
        if a["low90"] is not None and price <= a["low90"]:
            flags.append("lowest")
        if a["changed_recently"]:
            flags.append("new")
        if store_pct and not drop_pct and a["days_at_price"] >= 14:
            flags.append("steady")
        deals.append({
            "id": p["id"], "store": p["store"], "sku": p["sku"], "name": p["name"], "brand": p["brand"],
            "category": p["category"], "url": p["url"], "image": p["image"],
            "price": price, "original_price": orig, "usual_price": a["usual"], "low90": a["low90"],
            "discount_pct": pct, "store_discount_pct": store_pct, "drop_pct": drop_pct,
            "saving": round(reference - price, 2),
            "flags": flags, "since": rows[-1]["at"], "last_seen": p["last_seen"],
            "tracked_days": a["tracked_days"],
        })
    keys = {
        "discount": lambda d: (-d["discount_pct"], -d["saving"]),
        "saving": lambda d: (-d["saving"], -d["discount_pct"]),
        "newest": lambda d: (-d["since"], -d["discount_pct"]),
        "price": lambda d: (d["price"],),
    }
    deals.sort(key=keys.get(sort, keys["discount"]))
    return deals[:limit]
