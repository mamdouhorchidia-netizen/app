"""When will it be cheaper? Each product's own sale rhythm, read from its price history.

A product's *regular* price is the price it spends the most in-stock time at (among the upper half of its
prices). A *sale* is any stretch at least 5 % under that. With two or more sales seen, the gaps between
their starts and how long they lasted say how often it goes on sale, how long a sale lasts and the price
it usually reaches; from the last one, when the next is due (or when the current one tends to end).
Nothing is guessed: no rhythm is reported until two sales have actually been seen.
"""

from statistics import median

DAY = 86400
SALE = 0.95  # 5 % under the regular price


def regular_price(rows: list[dict], now: int):
    secs = {}
    for i, r in enumerate(rows):
        end = rows[i + 1]["at"] if i + 1 < len(rows) else now
        if r["in_stock"] and r["price"]:
            secs[r["price"]] = secs.get(r["price"], 0) + max(0, end - r["at"])
    if not secs:
        return None
    mid = median(secs)
    upper = {p: s for p, s in secs.items() if p >= mid}
    return max(upper, key=lambda p: (upper[p], p))


def episodes(rows: list[dict], regular: float, now: int) -> list[dict]:
    """[{start, end (None while it lasts), low}] for every stretch under the sale line."""
    out, cur = [], None
    for r in rows:
        on = r["in_stock"] and r["price"] <= regular * SALE
        if on and cur is None:
            cur = {"start": r["at"], "end": None, "low": r["price"]}
        elif on:
            cur["low"] = min(cur["low"], r["price"])
        elif cur is not None:
            cur["end"] = r["at"]
            out.append(cur)
            cur = None
    if cur is not None:
        out.append(cur)
    return out


def rhythm(rows: list[dict], now: int) -> dict | None:
    """None until two sales have been seen. Otherwise {regular, sales, every_days, lasts_days, typical_sale,
    on_sale, next_in_days (when not on sale), ends_in_days (when on sale), text}."""
    if len(rows) < 3:
        return None
    regular = regular_price(rows, now)
    if not regular:
        return None
    eps = episodes(rows, regular, now)
    if len(eps) < 2:
        return None
    starts = [e["start"] for e in eps]
    every = median(b - a for a, b in zip(starts, starts[1:])) / DAY
    done = [e for e in eps if e["end"]]
    lasts = median(e["end"] - e["start"] for e in done) / DAY if done else None
    typical = median(e["low"] for e in eps)
    on_sale = eps[-1]["end"] is None
    out = {"regular": regular, "sales": len(eps), "every_days": round(every, 1),
           "lasts_days": round(lasts, 1) if lasts is not None else None, "typical_sale": typical,
           "on_sale": on_sale, "next_in_days": None, "ends_in_days": None}
    if on_sale:
        if lasts is not None:
            out["ends_in_days"] = round(max(0.0, lasts - (now - eps[-1]["start"]) / DAY), 1)
            out["text"] = (f"On sale now. Its sales usually last about {_days(lasts)}"
                           + (f", so about {_days(out['ends_in_days'])} left." if out["ends_in_days"] >= 0.5
                              else ": this one may end any time."))
        else:
            out["text"] = "On sale now."
    else:
        due = (eps[-1]["start"] + every * DAY - now) / DAY
        out["next_in_days"] = round(max(0.0, due), 1)
        out["text"] = (f"Goes on sale about every {_days(every)}, usually down to {typical:g} EGP. "
                       + (f"Next one likely in about {_days(due)}." if due >= 1 else "One is due any day now."))
    return out


def _days(d: float) -> str:
    if d < 1:
        return f"{max(1, round(d * 24))} h"
    return f"{round(d)} day" + ("s" if round(d) != 1 else "")
