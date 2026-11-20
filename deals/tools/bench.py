"""Benchmark the deals engine on a synthetic database the size of the live site (~110k products, 6 stores).

  python3 tools/bench.py make --db /tmp/bench.sqlite3            # build the synthetic database once
  python3 tools/bench.py run --db /tmp/bench.sqlite3 [--src DIR]  # measure the engine in DIR (default: this tree)
  python3 tools/bench.py compare --db /tmp/bench.sqlite3 --before DIR   # both, one table

`--src DIR` points at a directory that holds a `deals` package (e.g. an old version unpacked with
`git archive <commit> deals/deals | tar -x -C DIR --strip-components=1`), so the same database and the same
requests measure the code before and after a change. Every run works on its own copy of the database.

What it reports: the full catalogue build, an incremental refresh after ~300 price changes, the server time of
/api/pulse, /api/deals (default view, a filtered + sorted view, a text search, the whole catalogue), /api/product/ID,
the response sizes with and without fields=card, and the process RSS. Latencies are measured over a kept-alive
connection on 127.0.0.1 from another process-free client thread, so they include the HTTP handling, not the network.
"""

import argparse
import http.client
import json
import os
import random
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import quote

DAY = 86400
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # the deals/ project folder (holds the deals package)

# ---- the synthetic database ---------------------------------------------------------------------------

BRANDS = """juhayna almarai pepsi coca nescafe lipton domty panda president lamar labanita beyti nestle cadbury
 galaxy kitkat mars snickers twix oreo lays doritos chipsy tiger molto todo halwani americana kiri lavache puck
 isis abuauf elarosa royal elmalika crystal afia arma sunny dove nivea lux palmolive pantene headshoulders garnier
 loreal colgate signal sensodyne ariel persil tide downy fairy pril clorox dettol harpic finish pampers molfix huggies
 johnsons always kotex fine zewa familia faine heinz hellmanns knorr maggi dolsi elmaleka quaker kellogs fitness
 nesquik milo ovaltine tang vimto rani suntop schweppes sprite fanta mirinda sevenup redbull monster sting fayrouz
 birell aquafina nestlepure baraka siwa hayat dasani safi elnour hero kraft philadelphia lurpak anchor elmarai
 farmfrites mccain koki halal wadi foodbox golden alalali qaha harvest heliopolis bonjorno davidoff jacobs lavazza
 illy tchibo abufaris elmotahida edita bisco elmagic bimbo hohos twinkies freska tiffany loacker ulker biskrem
 sunbites pringles cheetos kinder ferrari nutella lindt toblerone milka mentos trident clorets halls""".split()
TYPES = """milk yoghurt cheese butter juice water tea coffee chocolate biscuits wafer chips crackers cereal oats rice
 pasta oil ghee sugar salt flour beans lentils tuna sardines ketchup mayonnaise mustard honey jam halawa cream
 shampoo conditioner soap toothpaste detergent softener bleach dishwash diapers wipes tissues napkins deodorant lotion
 noodles soup cake croissant bread popcorn nuts dates candy gum energy soda cola""".split()
VARIANTS = ["", "", "", "", "classic", "light", "zero", "vanilla", "chocolate", "strawberry", "mango", "orange",
            "full fat", "skimmed", "extra", "mint", "lemon", "original", "spicy", "salted"]
FILLERS = ["", "egypt", "pack", "family", "new", "value", "super", "premium", "fresh", "natural", "imported", "local"]
SIZES = ["100 g", "120 g", "150 g", "200 g", "250 g", "300 g", "330 ml", "355 ml", "400 g", "450 g", "500 g", "500 ml",
         "600 ml", "750 g", "1 kg", "1 L", "1.5 L", "2 L", "2.25 L", "5 kg", "40 g", "42 g", "45 g", "50 g", "60 g", "75 g",
         "80 g", "85 g", "90 g", "95 g", "185 g", "235 ml", "250 ml", "275 ml", "300 ml", "350 g", "380 g", "800 g",
         "900 g", "1.25 L", "3 L", "4 L", "6x330ml", "12x250ml", "4x235ml", "2 kg", "3 kg", "10 kg", "700 ml", "125 g",
         "170 g", "180 g", "190 g", "220 g", "230 g", "240 g", "260 g", "280 g", "320 g", "360 g", "420 g", "480 g"]
TECH = """samsung apple xiaomi oppo realme honor huawei infinix tecno nokia lenovo hp dell asus acer msi sony lg
 toshiba sharp tornado fresh zanussi ariston hoover kenwood moulinex tefal braun philips panasonic black+decker
 sokany kumtel unionaire white-whale olympic jac media-star""".split()
TECH_TYPES = ["Smartphone", "Laptop", "Tablet", "Smart TV", "Refrigerator", "Washing Machine", "Air Conditioner",
              "Microwave", "Blender", "Hand Mixer", "Iron", "Vacuum Cleaner", "Water Heater", "Headphones", "Smart Watch",
              "Power Bank", "Charger", "Cooker", "Deep Fryer", "Air Fryer", "Kettle", "Toaster", "Fan", "Heater"]
AR_TYPES = ["موبايل", "لاب توب", "تابلت", "شاشة سمارت", "ثلاجة", "غسالة ملابس", "تكييف", "ميكروويف", "خلاط",
            "مكواة", "مكنسة كهربائية", "سخان مياه", "سماعة", "ساعة ذكية", "باور بانك", "شاحن", "بوتاجاز", "قلاية هوائية"]
COLORS = ["Black", "White", "Silver", "Blue", "Gold", "Grey", "Red", "Green", "أسود", "أبيض", "فضي"]
CATS = {"talabat": ["Dairy & Eggs / Milk", "Beverages / Juices", "Beverages / Soft Drinks", "Snacks / Chips",
                    "Snacks / Chocolate", "Pantry / Rice & Pasta", "Pantry / Oils", "Breakfast / Cereals",
                    "Household / Cleaning", "Personal Care / Hair", "Baby / Diapers", "Coffee & Tea / Tea",
                    "Coffee & Tea / Coffee", "Frozen / Ready Meals", "Bakery / Bread"],
        "amazon_eg": ["Grocery / Snacks & Sweets", "Grocery / Beverages", "Beauty / Hair Care", "Electronics / Mobiles",
                      "Home / Kitchen", "Baby / Diapering", "Health / Personal Care", "Electronics / Computers", ""],
        "jumia": ["Supermarket / Food Cupboard", "Supermarket / Beverages", "Phones & Tablets / Mobile Phones",
                  "Home & Office / Appliances", "Health & Beauty / Hair Care", "Baby Products / Diapering",
                  "Computing / Laptops", "Fashion / Men"],
        "dream2000": ["Appliances & electronics", "Mobiles", "Laptops", "TVs"],
        "kenzz": ["Kenzz deals", "Mobiles", "Home appliances", "Supermarket"],
        "raya": ["Mobiles & Tablets", "Home Appliances", "TV & Audio", "Small Appliances", "Computers"]}
HUBS = [(619848, "Maadi Old"), (650270, "El Obour El Hay 9"), (776372, "El Obour Industrial Area"), (619849, "Zamalek")]
COUNTS = {"talabat_base": 9_200, "amazon_eg": 24_000, "jumia": 26_000, "dream2000": 8_000, "kenzz": 6_600, "raya": 9_256}


def _grocery_pool(rng, n):
    pool = set()
    while len(pool) < n:
        pool.add((rng.choice(BRANDS), rng.choice(TYPES), rng.choice(VARIANTS), rng.choice(SIZES)))
    return list(pool)


def _grocery_name(rng, base, store):
    brand, kind, variant, size = base
    words = [brand.title(), kind.title()] + ([variant.title()] if variant else [])
    filler = rng.choice(FILLERS)
    if filler and store != "talabat":
        words.insert(2, filler.title())
    return " ".join(words) + (", " if rng.random() < .5 else " - ") + size


def _tech_name(rng, store):
    brand = rng.choice(TECH)
    if store in ("dream2000", "kenzz", "raya") and rng.random() < .7:
        name = f"{rng.choice(AR_TYPES)} {brand.title()} {rng.randint(100, 9999)} {rng.choice(COLORS)}"
        if rng.random() < .15:
            name += f" {rng.choice([6, 7, 8, 9, 10, 11, 12])} كجم"
        return name + rng.choice(["", " - ضمان 1 سنة", " - ضمان سنتين"])
    return (f"{brand.title()} {rng.choice(TECH_TYPES)} {rng.choice(['A', 'X', 'S', 'Pro', 'Max', 'Neo'])}{rng.randint(1, 99)}"
            f" {rng.choice([64, 128, 256, 512])}GB {rng.choice(COLORS)}")


def _history(rng, now, first_seen, base_price, has_orig):
    """Price rows for one product, oldest first: (at, price, original_price, in_stock)."""
    r = rng.random()
    n = 1 if r < .55 else rng.randint(2, 3) if r < .85 else rng.randint(4, 10) if r < .96 else rng.randint(11, 60)
    span = max(60, now - first_seen - 60)
    times = sorted({first_seen} | {first_seen + rng.randint(1, span) for _ in range(n - 1)})
    rows, regular = [], base_price
    orig = round(base_price * rng.uniform(1.1, 1.6), 0) if has_orig else None
    rhythm = n >= 11 and rng.random() < .6  # a product that goes on sale and back
    for i, at in enumerate(times):
        if i == 0:
            price = regular
        elif rhythm:
            price = round(regular * rng.uniform(.6, .85), 2) if i % 2 else regular
        else:
            price = round(regular * rng.choice([.7, .8, .9, .95, 1.0, 1.05, 1.1]), 2)
        stock = 1 if rng.random() < .93 else 0
        rows.append((at, price, orig if orig and orig > price else None, stock))
    return rows


def make_db(path: str, seed: int = 7, now: int = None, scale: float = 1.0):
    """The synthetic live site: talabat with four hubs (the same product in each), Amazon, Jumia, Dream 2000, Kenzz and
    Raya; grocery names shared across stores (so matching has work to do), electronics names that aren't; most
    products with 1-3 price rows, a few with many (some with a sale rhythm); a share with struck-through prices."""
    rng = random.Random(seed)
    now = int(now or time.time())
    if os.path.exists(path):
        os.remove(path)
    sys.path.insert(0, ROOT)
    from deals import db as dbm  # the schema, whichever version is on the path
    con = dbm.connect(path)
    con.close()
    con = sqlite3.connect(path)
    pool = _grocery_pool(rng, int(30_000 * scale))
    products, prices = [], []

    def add(store, sku, name, brand, category, url, image, extra, base_price, has_orig, first_seen=None):
        pid = len(products) + 1
        first_seen = first_seen or now - int(rng.random() ** 1.5 * 130 * DAY) - 3600
        rows = _history(rng, now, first_seen, base_price, has_orig)
        at, price, orig, stock = rows[-1]
        last_seen = now - rng.randint(0, 6 * 3600)
        products.append((pid, store, sku, name, brand, category, url, image, first_seen, max(last_seen, at), price, orig,
                         stock, json.dumps(extra, ensure_ascii=False)))
        prices.extend((pid,) + r for r in rows)

    # talabat: each base product in every hub (the same sku after the hub prefix), as the live site does
    for b in range(int(COUNTS["talabat_base"] * scale)):
        base = pool[b]
        name = _grocery_name(rng, base, "talabat")
        sku = f"{b:06X}"
        price = round(rng.uniform(8, 400), 2)
        orig = rng.random() < .25
        cat = rng.choice(CATS["talabat"])
        img = f"https://talabat.dhmedia.io/image/darkstores/nv-global-catalog/eg/{rng.getrandbits(64):016x}.jpg"
        first = now - int(rng.random() ** 1.5 * 130 * DAY) - 3600
        for hub, hub_name in HUBS:
            slug = name.lower().replace(" ", "-").replace(",", "")[:60]
            add("talabat", f"{hub}:{sku}", name, "", cat,
                f"https://www.talabat.com/egypt/grocery/{hub}/talabat-mart/product/{slug}/s/{sku}?aid=9838", img,
                {"stock": rng.randint(1, 20), "hub": hub, "hub_name": hub_name},
                round(price * rng.choice([1, 1, 1, .95, 1.05]), 2), orig, first + rng.randint(0, 3600))
    for store in ("amazon_eg", "jumia", "dream2000", "kenzz", "raya"):
        grocery_share = {"amazon_eg": .45, "jumia": .35, "dream2000": 0, "kenzz": .15, "raya": 0}[store]
        for i in range(int(COUNTS[store] * scale)):
            extra = {}
            if rng.random() < grocery_share:
                base = rng.choice(pool[:int(16_000 * scale)])  # overlaps talabat's range: real cross-store matches
                name = _grocery_name(rng, base, store)
                brand = base[0].title()
                price = round(rng.uniform(8, 450), 2)
            else:
                name = _tech_name(rng, store)
                brand = name.split()[0]
                price = round(rng.uniform(150, 60_000), 0)
            if store == "amazon_eg":
                sku = f"B0{rng.getrandbits(40):010X}"[:10]
                url = f"https://www.amazon.eg/-/en/dp/{sku}"
                if rng.random() < .3:
                    extra["rank"] = rng.randint(1, 100)
                if rng.random() < .05:
                    extra.update(deal_label="Limited time deal", deal_pct=rng.randint(10, 60))
            elif store == "jumia":
                sku = f"J{i:07d}"
                url = f"https://www.jumia.com.eg/{name.lower().replace(' ', '-')[:50]}-{sku}.html"
            elif store == "dream2000":
                sku = str(10_318_000_000_000 + i)
                url = f"https://dream2000.com/products/{name.lower().replace(' ', '-')[:60]}"
            elif store == "kenzz":
                sku = f"{rng.getrandbits(60):012X}"[:12]
                url = f"https://kenzz.com/product/{sku}/{name[:40].replace(' ', '-')}"
                if rng.random() < .4:
                    extra.update(deal_label=rng.choice(["اطلبت كتير", "عرض البلاك فرايداي"]),
                                 promo_code=rng.choice(["KBF26", "MOB13"]), promo_price=round(price * .85, 0))
            else:
                sku = f"raya-{i}"
                url = f"https://www.rayashop.com/ar/{sku}/"
            orig = rng.random() < {"amazon_eg": .2, "jumia": .5, "dream2000": .9, "kenzz": 1, "raya": .6}[store]
            add(store, sku, name, brand, rng.choice(CATS[store]), url, f"https://img.example/{store}/{i}.jpg", extra,
                price, orig)
    con.executemany("INSERT INTO products (id, store, sku, name, brand, category, url, image, first_seen, last_seen,"
                    " price, original_price, in_stock, extra) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", products)
    con.executemany("INSERT INTO prices (product_id, at, price, original_price, in_stock) VALUES (?,?,?,?,?)", prices)
    for store in ("talabat", "amazon_eg", "jumia", "dream2000", "kenzz", "raya"):
        con.execute("INSERT OR REPLACE INTO live_status (store, updated, last_change, pages, changes, tasks, cycle_secs,"
                    " error, paused_until) VALUES (?,?,?,?,?,?,?,?,?)",
                    (store, now - 5, now - 60, 1000, 100, 400, 3600.0, None, 0))
    con.commit()
    con.close()
    return {"products": len(products), "prices": len(prices), "now": now}


def price_changes(path: str, n: int = 300, seed: int = 11, at: int = None) -> list[int]:
    """n price changes through the real write path (db.save_offers), as a crawler would make them."""
    from deals import db as dbm
    from deals.models import Offer
    rng = random.Random(seed)
    con = dbm.connect(path)
    rows = con.execute("SELECT id, store, sku, name, price, original_price, in_stock, extra FROM products"
                       " WHERE price > 0 ORDER BY random() LIMIT ?", (n,)).fetchall()
    offers = []
    for r in rows:
        price = round(r["price"] * rng.choice([.7, .8, .9, 1.1]), 2)
        offers.append(Offer(r["store"], r["sku"], r["name"], price, r["original_price"], in_stock=True,
                            extra=json.loads(r["extra"] or "{}")))
    dbm.save_offers(con, offers, at)
    con.close()
    return [r["id"] for r in rows]


# ---- measuring ----------------------------------------------------------------------------------------

def _rss_mb() -> float:
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    return 0.0


def _pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


class Client:
    """One kept-alive HTTP connection (or a new one per request, as HTTP/1.0 servers need)."""

    def __init__(self, port):
        self.port, self.con = port, None

    def get(self, path, headers=None):
        for attempt in (0, 1):
            if self.con is None:
                self.con = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
            try:
                t = time.perf_counter()
                self.con.request("GET", path, headers=headers or {})
                r = self.con.getresponse()
                body = r.read()
                dt = time.perf_counter() - t
                if r.getheader("Connection", "").lower() == "close" or r.version == 10:
                    self.con.close()
                    self.con = None
                return r.status, body, dt, r
            except (http.client.HTTPException, OSError):
                self.con.close()
                self.con = None
                if attempt:
                    raise


def measure(client, paths, rounds, before_each=None):
    """Server time per request, ms: each path in turn, `rounds` times (callable paths get the round number, so every
    request can be a different one: no answer cache helps)."""
    out = {}
    for name, path in paths:
        times, size, status = [], 0, None
        for i in range(rounds):
            p = path(i) if callable(path) else path
            if before_each:
                before_each()
            status, body, dt, _ = client.get(p)
            times.append(dt * 1000)
            size = len(body)
        out[name] = {"p50": statistics.median(times), "p95": _pct(times, 95), "max": max(times), "bytes": size,
                     "status": status, "n": rounds}
    return out


def run(db_src: str, src: str, rounds: int = 60) -> dict:
    """Everything, on a private copy of the database, for the engine in `src`. Runs in this process."""
    sys.path.insert(0, src)
    for m in [m for m in sys.modules if m == "deals" or m.startswith("deals.")]:
        del sys.modules[m]
    import deals
    from deals import db as dbm, finder, web
    from http.server import ThreadingHTTPServer
    tmp = tempfile.mkdtemp(prefix="deals-bench-")
    path = os.path.join(tmp, "bench.sqlite3")
    shutil.copy(db_src, path)
    res = {"src": os.path.dirname(os.path.abspath(deals.__file__)), "rss_start_mb": _rss_mb()}
    new_engine = os.path.exists(os.path.join(res["src"], "catalog.py"))
    t = time.perf_counter()
    dbm.connect(path).close()  # first open: the indexes and triggers of this version
    res["first_connect_s"] = time.perf_counter() - t
    ids = [r[0] for r in sqlite3.connect(path).execute("SELECT id FROM products ORDER BY id")]
    rng = random.Random(3)

    # start the server as `serve` does, and time the first answer
    cat = None
    if new_engine:
        from deals import catalog as catm
        cat = catm.Catalog(path)
        handler = web.make_handler(path, catalog=cat)
    else:
        handler = web.make_handler(path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    c = Client(server.server_address[1])
    t = time.perf_counter()
    if cat:
        cat.start()
    status, body, _, _ = c.get("/api/deals?limit=60")
    res["first_request_s"] = time.perf_counter() - t  # old: this request builds; new: it waits for the first build
    res["first_status"] = status
    if cat:
        while not cat.snapshot().index.orders.get("_saving"):  # the presorted orders, built after the first answer
            time.sleep(0.1)
        res["orders_ready_s"] = time.perf_counter() - t
        cat.stop()  # the bench refreshes by hand from here on

    def clear_index():
        snap = cat.snapshot()
        snap.index.cache.clear()  # what a query finds right after a refresh changed prices: no derived mask yet

    views = [
        ("deals default", lambda i: f"/api/deals?deals=1&stock=in&sort=score&limit={40 + i}&offset=0"),
        ("deals filtered+sorted", lambda i: f"/api/deals?deals=1&stock=in&stores=talabat,jumia&min={10 + i % 30}"
                                            f"&sort=discount&category=Beverages&status=real&limit={40 + i}&offset=0"),
        ("deals search", lambda i: f"/api/deals?deals=0&stock=all&q="
                                   f"{quote(['nescafe', 'milk', 'pepsi can', 'samsung', 'juice 1 l', 'شاشة'][i % 6])}"
                                   f"&sort=score&limit={40 + i}&offset=0"),
        ("deals all products", lambda i: f"/api/deals?deals=0&stock=in&sort={['price', 'newest', 'name'][i % 3]}"
                                         f"&limit={40 + i}&offset={i}"),
        ("deals price range", lambda i: f"/api/deals?deals=0&stock=in&pmin={50 + i}&pmax=500&limit=60"),
        ("product", lambda i: f"/api/product/{rng.choice(ids)}"),
    ]
    if new_engine:
        views.insert(0, ("pulse", "/api/pulse"))
    res["latency"] = measure(c, views, rounds)
    if new_engine:
        cold = measure(c, [(n, (lambda f: lambda i: f(i + 1000))(p)) for n, p in views if n.startswith("deals ")],
                       rounds, clear_index)
        res["latency"].update({k + " (cold index)": v for k, v in cold.items()})
    # the same view again (what a browser's refresh asks for)
    res["latency"].update({k + " (repeat)": v for k, v in
                           measure(c, [("deals default", "/api/deals?deals=1&stock=in&sort=score&limit=60&offset=0")],
                                   rounds).items()})
    if new_engine:
        st, body, _, r = c.get("/api/deals?deals=1&stock=in&sort=score&limit=60&offset=0")
        etag = r.getheader("ETag")
        times = []
        for _ in range(rounds):
            st304, b304, dt, _ = c.get("/api/deals?deals=1&stock=in&sort=score&limit=60&offset=0",
                                       {"If-None-Match": etag})
            times.append(dt * 1000)
        res["latency"]["deals 304"] = {"p50": statistics.median(times), "p95": _pct(times, 95), "max": max(times),
                                       "bytes": len(b304), "status": st304, "n": rounds}
    st, full, _, _ = c.get("/api/deals?limit=60")
    res["bytes_deals_60"] = len(full)
    if new_engine:
        st, card, _, _ = c.get("/api/deals?limit=60&fields=card")
        res["bytes_deals_60_card"] = len(card)
        st, pulse, _, _ = c.get("/api/pulse")
        res["bytes_pulse"] = len(pulse)
    res["rss_serving_mb"] = _rss_mb()  # the catalogue built and serving (before the extra builds below)

    # an incremental refresh after ~300 price changes through the real write path
    price_changes(path, 300, at=int(time.time()) + 1)
    if new_engine:
        t = time.perf_counter()
        res["incremental_info"] = cat.refresh()
        res["incremental_refresh_s"] = time.perf_counter() - t
        time.sleep(5)
        t = time.perf_counter()
        res["idle_info"] = cat.refresh()  # 5 s later, nothing written: only what the clock changed
        res["idle_refresh_s"] = time.perf_counter() - t
        # requests while a full rebuild runs in the background: nobody waits for it
        done = threading.Event()

        def rebuild():
            cat.refresh(full=True)
            done.set()
        threading.Thread(target=rebuild, daemon=True).start()
        times, i = [], 0
        while not done.is_set():
            i += 1
            _, _, dt, _ = c.get(f"/api/deals?deals=1&stock=in&sort=discount&limit={40 + i % 400}&offset={i % 7}")
            times.append(dt * 1000)
        res["latency"]["deals during full rebuild"] = {"p50": statistics.median(times), "p95": _pct(times, 95),
                                                       "max": max(times), "n": len(times), "bytes": 0, "status": 200}
    else:
        # the old engine can only rebuild everything: the request that finds the 15 s catalogue stale does it, and
        # every other request waits behind its lock
        time.sleep(16)
        t = time.perf_counter()
        _, _, dt, _ = c.get("/api/deals?deals=1&stock=in&sort=score&limit=60&offset=7")
        res["incremental_refresh_s"] = time.perf_counter() - t
        res["latency"]["deals when stale"] = {"p50": dt * 1000, "p95": dt * 1000, "max": dt * 1000, "n": 1,
                                              "bytes": 0, "status": 200}
    server.shutdown()
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    t = time.perf_counter()
    res["products_in_catalogue"] = len(finder.catalog(con))
    res["full_build_s"] = time.perf_counter() - t  # the pure function, on its own
    con.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return res


def _run_in_child(db, src, rounds):
    out = subprocess.run([sys.executable, os.path.abspath(__file__), "run", "--db", db, "--src", src,
                          "--rounds", str(rounds), "--json"], capture_output=True, text=True)
    if out.returncode:
        sys.stderr.write(out.stderr)
        raise SystemExit(out.returncode)
    return json.loads(out.stdout.strip().splitlines()[-1])


def table(results: dict) -> str:
    names = list(results)
    lines = ["| | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]

    def row(label, fn):
        vals = []
        for n in names:
            try:
                vals.append(fn(results[n]))
            except (KeyError, TypeError):
                vals.append("n/a")
        lines.append(f"| {label} | " + " | ".join(vals) + " |")
    row("products", lambda r: f"{r['products_in_catalogue']:,}")
    row("finder.catalog full build", lambda r: f"{r['full_build_s']:.2f} s")
    row("first answer after start (old: that request builds; new: it waits for the background build)",
        lambda r: f"{r['first_request_s']:.2f} s")
    row("all sort orders ready (new: in the background, after the first answer)", lambda r: f"{r['orders_ready_s']:.2f} s")
    row("refresh after 300 price changes (old: a full rebuild)", lambda r: f"{r['incremental_refresh_s']:.3f} s")
    row("refresh 5 s later, nothing written (the clock's changes only)", lambda r: f"{r['idle_refresh_s']:.3f} s")
    keys = []
    for r in results.values():
        keys += [k for k in r["latency"] if k not in keys]
    for k in keys:
        row(f"{k} p50 / p95 / max", lambda r, k=k: f"{r['latency'][k]['p50']:.1f} / {r['latency'][k]['p95']:.1f}"
                                                  f" / {r['latency'][k]['max']:.0f} ms")
    row("/api/deals?limit=60 bytes", lambda r: f"{r['bytes_deals_60']:,}")
    row("/api/deals?limit=60&fields=card bytes", lambda r: f"{r['bytes_deals_60_card']:,}")
    row("/api/pulse bytes", lambda r: f"{r['bytes_pulse']:,}")
    row("process RSS while serving", lambda r: f"{r['rss_serving_mb']:.0f} MB")
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["make", "run", "compare"])
    p.add_argument("--db", required=True)
    p.add_argument("--src", default=ROOT)
    p.add_argument("--before")
    p.add_argument("--rounds", type=int, default=60)
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    if a.command == "make":
        t = time.perf_counter()
        info = make_db(a.db, scale=a.scale)
        print(f"{info['products']:,} products, {info['prices']:,} price rows in {time.perf_counter() - t:.1f} s: {a.db}")
    elif a.command == "run":
        r = run(a.db, os.path.abspath(a.src), a.rounds)
        print(json.dumps(r) if a.json else table({"this": r}))
    else:
        res = {}
        if a.before:
            res["before"] = _run_in_child(a.db, os.path.abspath(a.before), a.rounds)
        res["after"] = _run_in_child(a.db, os.path.abspath(a.src), a.rounds)
        print(table(res))


if __name__ == "__main__":
    main()
