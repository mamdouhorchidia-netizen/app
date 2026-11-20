"""The incremental catalog (finder.Catalog via livedata.CatalogKeeper) against a fresh full build.

The acceptance test: over random sequences of price changes, stock flips, renames, new and removed products and
time passing (seconds to weeks), every refresh's items (order, every field, key order), digests and matches equal
what finder.catalog builds from scratch at the same moment. Plus the pieces it relies on: an item's horizon is
never late, and bucketed matching equals the original all-pairs matching."""

import json
import os
import random
import tempfile
import unittest
from collections import defaultdict

from deals import db, finder, forecast, livedata, matching
from deals.models import Offer
from deals.units import pack_size

DAY = 86400
T0 = 1_760_000_000

BRANDS = ["Pepsi", "Juhayna", "Lipton", "Nescafe", "Chipsy"]
NOUNS = ["Milk", "Tea", "Cola", "Coffee", "Chips"]
VARIANTS = ["", "", "Zero", "Gold", "Full Cream", "Can"]
SIZES = ["1L", "500 gm", "250ml X3", "6x300ml", "40 pcs", "1 Liter"]
OTHERS = ["amazon_eg", "kenzz", "breadfast", "jumia"]
HUBS = [(619848, "Maadi Old"), (776372, "Obour"), (700001, "Zamalek")]


class World:
    """A random store world written through db.save_offers, as the scheduler writes it."""

    def __init__(self, con, rng: random.Random):
        self.con, self.rng = con, rng
        self.specs = {}  # (store, sku) -> dict(name, price, original, stock, extra, category)
        self.n = 0

    def new_product(self):
        r = self.rng
        self.n += 1
        name = f"{r.choice(BRANDS)} {r.choice(NOUNS)} {r.choice(VARIANTS)} {r.choice(SIZES)}".replace("  ", " ")
        price = round(r.uniform(10, 300), 2)
        out = []
        if r.random() < 0.6:  # talabat, in one or more hubs, same product
            for hub, hub_name in r.sample(HUBS, r.randint(1, len(HUBS))):
                out.append(("talabat", f"{hub}:{self.n}", name, {"hub": hub, "hub_name": hub_name}))
        for st in r.sample(OTHERS, r.choice([0, 1, 2])):
            nm = name if r.random() < 0.6 else name.replace(" ", ", ", 1)
            extra = {}
            if st == "amazon_eg" and r.random() < 0.3:
                extra["rank"] = r.randint(1, 50)
            if st == "kenzz" and r.random() < 0.4:
                extra.update(promo_code="KBF26", promo_price=price - 5)
            if st == "breadfast" and r.random() < 0.5:
                extra["app_link"] = f"breadfast://product/{self.n}"
            out.append((st, f"{st[:2]}{self.n}", nm, extra))
        if not out:
            out.append(("amazon_eg", f"am{self.n}", name, {}))
        for st, sku, nm, extra in out:
            self.specs[(st, sku)] = {"name": nm, "price": price, "original": round(price * 1.3, 2) if r.random() < 0.3
                                     else None, "stock": True, "extra": extra,
                                     "category": r.choice(["Beverages / Soft", "Dairy & Eggs / Milk", "", "Snacks"])}
        return [(st, sku) for st, sku, _, _ in out]

    def offer(self, key) -> Offer:
        s = self.specs[key]
        return Offer(key[0], key[1], s["name"], s["price"], s["original"], in_stock=s["stock"],
                     category=s["category"], url=f"https://www.amazon.eg/dp/B0{abs(hash(key)) % 10**8:08d}"
                     if key[0] == "amazon_eg" else f"https://example.com/{key[0]}/{key[1]}", extra=dict(s["extra"]))

    def change(self, key):
        r, s = self.rng, self.specs[key]
        roll = r.random()
        if roll < 0.45:  # a new price, usually a sale or back to normal
            s["price"] = round(s["price"] * r.choice([0.7, 0.8, 0.9, 0.95, 1.05, 1.1, 1.25, 1.4]), 2) or 1
        elif roll < 0.6:
            s["stock"] = not s["stock"]
        elif roll < 0.68:
            s["original"] = None if s["original"] else round(s["price"] * r.choice([1.2, 2.0, 4.0]), 2)
        elif roll < 0.72:  # renamed: may move it to another matching bucket
            s["name"] = f"{r.choice(BRANDS)} {r.choice(NOUNS)} {r.choice(VARIANTS)} {r.choice(SIZES)}".replace("  ", " ")
        elif roll < 0.78:
            s["extra"] = dict(s["extra"], rank=r.randint(1, 60))
        elif roll < 0.8:
            s["extra"] = dict(s["extra"], deal_label=None if s["extra"].get("deal_label") else "11.11 Deal")
        elif roll < 0.83:
            s["price"] = 0  # price 0: out of the catalog (and back later)
        elif roll < 0.86 and s["price"] == 0:
            s["price"] = round(r.uniform(10, 300), 2)
        # else: checked again, nothing moved (only last_seen)

    def write(self, keys, at):
        db.save_offers(self.con, [self.offer(k) for k in keys], at)


STEPS = [0, 1, 2, 10, 30, 300, 1800, 3600, 3 * 3600, 6 * 3600, DAY, 2 * DAY, 4 * DAY, 9 * DAY, 25 * DAY]


class IncrementalEqualsFull(unittest.TestCase):
    def run_world(self, seed: int, steps: int = 120):
        rng = random.Random(seed)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "w.sqlite3")
            con = db.connect(path)
            world = World(con, rng)
            # some history first: 100 days of changes
            t = T0 - 100 * DAY
            for _ in range(30):
                for _ in range(rng.randint(1, 4)):
                    world.write(world.new_product(), t)
                keys = rng.sample(list(world.specs), min(len(world.specs), rng.randint(1, 10)))
                for k in keys:
                    world.change(k)
                world.write(keys, t)
                t += rng.randint(3600, 6 * DAY)
            now = [max(t, T0)]
            clock = [1000.0]
            keeper = livedata.CatalogKeeper(path, livedata.Version(path, every=0), clock=lambda: clock[0],
                                            wall=lambda: now[0], poll=3600, full_rebuild=float("inf"))
            snap = keeper.rebuild()
            self.check(snap, path, now[0])
            steps_done = 0
            for step in range(steps):
                dt = rng.choice(STEPS)
                now[0] += dt
                clock[0] += max(dt, 1)
                roll = rng.random()
                if roll < 0.7:
                    keys = rng.sample(list(world.specs), min(len(world.specs), rng.choice([1, 2, 5, 20])))
                    for k in keys:
                        world.change(k)
                    if rng.random() < 0.15:
                        keys += world.new_product()
                    # a writer whose clock read a moment ago (or a page checked twice in one second)
                    world.write(keys, now[0] - rng.choice([0, 0, 0, 1, 3]))
                elif roll < 0.73:  # a product row deleted (the hub migration does this): a full rebuild
                    victim = rng.choice(list(world.specs))
                    with con:
                        pid = con.execute("SELECT id FROM products WHERE store=? AND sku=?", victim).fetchone()
                        if pid:
                            con.execute("DELETE FROM prices WHERE product_id=?", (pid[0],))
                            con.execute("DELETE FROM products WHERE id=?", (pid[0],))
                    del world.specs[victim]
                # else: only time passes
                before = keeper.snap
                snap = keeper.rebuild()
                steps_done += not snap.info.get("full")
                full = self.check(snap, path, now[0])
                # what didn't change keeps its digest (the ETags of unchanged cards stay the same)
                for pid, dg in snap.digests.items():
                    if pid in before.digests and livedata.item_digest(before.by_id[pid]) == livedata.item_digest(full[pid]):
                        self.assertEqual(dg, before.digests[pid])
            con.close()
            self.assertGreater(steps_done, steps * 0.8)  # it really went step by step

    def check(self, snap, path, now):
        con = livedata.open_db(path)
        try:
            full = finder.catalog(con, now)
        finally:
            con.close()
        self.assertEqual([d["id"] for d in snap.items], [d["id"] for d in full])
        for a, b in zip(snap.items, full):
            if json.dumps(a) != json.dumps(b):  # key order too: it's what the page gets
                diff = {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)}
                self.fail(f"item {a['id']} at {now}: {diff or 'key order'} info={snap.info}")
        self.assertEqual(snap.digests, {d["id"]: livedata.item_digest(d) for d in full})
        self.assertEqual(snap.by_id.keys(), {d["id"] for d in full})
        return {d["id"]: d for d in full}

    def test_random_worlds(self):
        for seed in range(6):
            with self.subTest(seed=seed):
                self.run_world(seed)

    def test_matches_follow_renames_and_hubs(self):
        """A talabat product seen in several hubs is matched once (its first hub's copy); when that copy goes, the
        next hub's copy takes its place, and a rename moves a product between buckets."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m.sqlite3")
            con = db.connect(path)
            db.save_offers(con, [
                Offer("talabat", "619848:1", "Juhayna Milk 1L", 50, extra={"hub": 619848, "hub_name": "A"}),
                Offer("talabat", "776372:1", "Juhayna Milk 1L", 52, extra={"hub": 776372, "hub_name": "B"}),
                Offer("amazon_eg", "a1", "Juhayna Milk, 1 Liter", 45),
                Offer("kenzz", "k1", "Juhayna Tea 1L", 45),
            ], T0)
            now = [T0 + 60]
            k = livedata.CatalogKeeper(path, livedata.Version(path, every=0), wall=lambda: now[0], poll=3600,
                                       full_rebuild=float("inf"))
            k.rebuild()
            ids = {d["sku"]: d["id"] for d in k.snap.items}
            self.assertEqual([m["id"] for m in k.snap.by_id[ids["a1"]]["matches"]], [ids["619848:1"]])
            for offers in ([Offer("talabat", "619848:1", "Juhayna Milk 1L", 0, extra={"hub": 619848})],  # first hub gone
                           [Offer("kenzz", "k1", "Juhayna Milk 1L", 44)],  # renamed into the bucket: now ambiguous? no: other store
                           [Offer("amazon_eg", "a1", "Juhayna Milk, 1 Liter", 39)],  # a rival's price
                           [Offer("talabat", "619848:1", "Juhayna Milk 1L", 51, extra={"hub": 619848})]):  # back
                now[0] += 30
                db.save_offers(con, offers, now[0])
                snap = k.rebuild()
                self.assertFalse(snap.info["full"])
                self.check(snap, path, now[0])
            con.close()


def in_window(rows, now):
    """The rows a build at `now` reads: the one in force when the 95-day window opened, then those inside it."""
    cut = now - finder.WINDOW
    older = [r for r in rows if r["at"] <= cut]
    return [r for r in older if r["at"] == older[-1]["at"]] + [r for r in rows if r["at"] > cut] if older else rows


class Horizon(unittest.TestCase):
    """_horizon may be early, never late: until it, the item (but the sparkline's last point) stays the same."""

    def test_never_late(self):
        rng = random.Random(5)
        checked = 0
        for case in range(400):
            first = T0 - rng.randint(0, 120) * DAY - rng.randint(0, DAY)
            rows, t, price, regular = [], first, 100.0, 100.0
            for _ in range(rng.choice([1, 2, 3, 5, 8, 12])):
                rows.append({"at": t, "price": price, "original_price": None, "in_stock": rng.random() > 0.15})
                t += rng.choice([600, 3600, DAY // 2, DAY, 3 * DAY, 7 * DAY, 20 * DAY]) + rng.randint(0, 3600)
                price = rng.choice([regular, regular, regular * 0.8, regular * 0.7, regular * 1.1])
            now = rows[-1]["at"] + rng.choice([5, 600, DAY // 3, DAY, 5 * DAY, 29 * DAY, 60 * DAY])
            s = {"id": 1, "store": "amazon_eg", "sku": "x", "name": "X 1L", "brand": "", "category": "",
                 "top_category": "Other", "url": "", "image": "", "in_stock": bool(rows[-1]["in_stock"]),
                 "price": rows[-1]["price"], "original_price": rng.choice([None, 130.0, 400.0]),
                 "deal_label": None, "rank": None, "first_seen": first, "last_seen": now, "unit_price": None,
                 "unit": None, "unit_text": None, "links": {}, "hub": None, "hub_name": None,
                 "promo_code": None, "promo_price": None}
            window = in_window(rows, now)
            it, a = finder._item(s, window, now)
            h = finder._horizon(window, first, now, a)
            self.assertGreater(h, now - 10)
            probes = {int(h) - 1, int(h) - 2} if h < float("inf") else set()
            span = (min(h, now + 200 * DAY) - now)
            probes |= {now + int(span * f) for f in (0.1, 0.5, 0.9, 0.99)} | {now + 1, now + 60, now + 300}
            for p in probes:
                if p <= now or p >= h:
                    continue
                later, _ = finder._item(s, in_window(rows, p), p)
                for k in it:
                    if k != "spark":
                        self.assertEqual(it[k], later[k], (case, k, now, p, h))
                self.assertEqual(it["spark"][:-1], later["spark"][:-1])
                checked += 1
        self.assertGreater(checked, 500)


    def test_a_point_on_a_rounding_edge(self):
        """A sale that has run 16.7514 - 16.75 days of its usual 16.7514: ends_in_days is about to go from 16.8 to
        16.7, though float division puts 16.75 a hair below its grid point."""
        now = 1760023285
        rows = [{"at": now + at, "price": p, "in_stock": st, "original_price": None} for at, p, st in [
            (-3994362, 92.16, 1), (-3052656, 82.94, 1), (-2547042, 103.67, 1), (-20689, 103.67, 0),
            (-8476, 129.59, 0), (-2118, 90.71, 0), (-120, 90.71, 1)]]
        self.assertEqual(forecast.rhythm(rows, now)["ends_in_days"], 16.8)
        self.assertEqual(forecast.rhythm(rows, now + 60)["ends_in_days"], 16.7)
        self.assertLessEqual(finder._rhythm_turn(rows, now), now + 60)


def reference_match(items):
    """matching.match as it was: every pair inside each pack size, no buckets by first word."""
    buckets = defaultdict(list)
    for it in items:
        size = pack_size(it["name"])
        if size:
            buckets[(round(size[0], 3), size[1])].append((it, matching.words(it["name"])))
    pairs = defaultdict(list)
    for group in buckets.values():
        for i, (a, wa) in enumerate(group):
            for b, wb in group[i + 1:]:
                if a["store"] != b["store"] and matching.similar(wa, wb):
                    pairs[a["id"]].append((b["id"], b["store"]))
                    pairs[b["id"]].append((a["id"], a["store"]))
    unique = {}
    for pid, found in pairs.items():
        per_store = defaultdict(list)
        for oid, store in found:
            per_store[store].append(oid)
        unique[pid] = [oids[0] for oids in per_store.values() if len(oids) == 1]
    out = defaultdict(list)
    for pid, oids in unique.items():
        for oid in oids:
            if pid in unique.get(oid, []):
                out[pid].append(oid)
    return out


class Buckets(unittest.TestCase):
    def test_same_matches_as_all_pairs(self):
        rng = random.Random(3)
        for _ in range(20):
            items = [{"id": i, "store": rng.choice(["talabat", "amazon_eg", "kenzz"]),
                      "name": f"{rng.choice(BRANDS)} {rng.choice(NOUNS)} {rng.choice(VARIANTS)} {rng.choice(SIZES)}"}
                     for i in range(300)]
            self.assertEqual(dict(matching.match(items)), dict(reference_match(items)))

    def test_similar_needs_the_same_first_word(self):
        self.assertFalse(matching.similar(["pepsi", "cola"], ["cola", "pepsi"]))
        self.assertFalse(matching.similar([], ["x"]))
        self.assertEqual(matching.key("Juhayna Milk 1L")[0], (1.0, "L", "juhayna"))
        self.assertIsNone(matching.key("Juhayna Milk")[0])


if __name__ == "__main__":
    unittest.main()
