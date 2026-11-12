import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from deals import db, finder, sigv4
from deals.http import Blocked, Fetcher
from deals.models import NotConfigured, Offer
from deals.scheduler import StoreLoop
from deals.stores import amazon_eg, breadfast, talabat
from deals.tasks import Task

FIX = Path(__file__).with_name("fixtures")
DAY = 86400


class TalabatParsing(unittest.TestCase):
    def test_categories(self):
        store, subs = talabat.parse_categories((FIX / "talabat_categories.html").read_text())
        self.assertEqual(store["branchId"], "619848")
        self.assertEqual(store["area"]["id"], 7882)
        self.assertEqual(len(subs), 4)
        self.assertEqual({s["parent"] for s in subs}, {"Beverages", "Dairy & Eggs"})
        self.assertTrue(all(s["slug"] and s["parent_slug"] for s in subs))

    def test_items(self):
        offers, pages = talabat.parse_items((FIX / "talabat_items.html").read_text(), "619848", 9838, "Beverages")
        self.assertEqual(pages, 2)
        self.assertEqual(len(offers), 5)
        cola = offers[0]
        self.assertEqual(cola.name, "V Super Soda Diet Cola Can 300Ml x8")
        self.assertEqual((cola.price, cola.original_price), (108, 120))
        self.assertEqual(cola.store_discount_pct, 10.0)
        self.assertTrue(cola.url.startswith("https://www.talabat.com/egypt/grocery/619848/talabat-mart/product/"))
        self.assertEqual(cola.sku, "619848:S6WENO")  # each hub is its own product
        self.assertTrue(cola.url.endswith("/s/S6WENO?aid=9838"))
        self.assertEqual((cola.extra["hub"], cola.extra["hub_name"]), (619848, "Maadi Old"))
        undiscounted = offers[-1]
        self.assertIsNone(undiscounted.original_price)  # originalPrice == price is not a discount

    def test_layout_change_is_reported(self):
        with self.assertRaises(talabat.PageError):
            talabat.parse_items("<html>new design</html>", "1", 1)

    def test_collect_walks_pages(self):
        cats, items = (FIX / "talabat_categories.html").read_text(), (FIX / "talabat_items.html").read_text()
        asked = []

        class Fake(Fetcher):
            def get(self, url, headers=None):
                asked.append(url)
                return 200, cats if "/categories" in url else items

        offers = list(talabat.collect(Fake(delay=0), hubs=[{"id": 619848, "aid": 7882, "name": "Maadi Old"}],
                                      log=lambda *_: None))
        self.assertEqual(len(asked), 1 + 4 * 2)  # categories + 4 subcategories x 2 pages
        self.assertIn("/egypt/grocery/619848/talabat-mart/dairy-eggs/", asked[1])
        self.assertTrue(asked[2].endswith("aid=7882&page=2"))
        self.assertEqual(len(offers), 5)  # every fake page holds the same 5 products: each is kept once
        asked.clear()
        two = [{"id": 619848, "aid": 9838, "name": "Maadi Old"}, {"id": 776372, "aid": 8032, "name": "Obour"}]
        offers = list(talabat.collect(Fake(delay=0), hubs=two, log=lambda *_: None))
        self.assertEqual(len(asked), 1 + 2 * 4 * 2)  # every shelf in each hub
        self.assertTrue(any("/grocery/776372/talabat-mart/" in u and "aid=8032" in u for u in asked))
        self.assertEqual(len(offers), 10)  # the same products, once per hub
        self.assertEqual({o.extra["hub_name"] for o in offers}, {"Maadi Old", "Obour"})
        self.assertTrue(all(o.sku.startswith(f"{o.extra['hub']}:") for o in offers))
        asked.clear()
        list(talabat.collect(Fake(delay=0), max_pages=3, log=lambda *_: None))
        self.assertEqual(len(asked), 1 + 3)


class SigV4(unittest.TestCase):
    def test_aws_get_vanilla_vector(self):
        # AWS's published Signature V4 test suite, case "get-vanilla".
        h = sigv4.sign("GET", "example.amazonaws.com", "/", {},
                       {"Host": "example.amazonaws.com", "X-Amz-Date": "20150830T123600Z"}, b"",
                       "AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "us-east-1", "service",
                       "20150830T123600Z")
        self.assertEqual(h["Authorization"],
                         "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
                         "SignedHeaders=host;x-amz-date, "
                         "Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31")


class Amazon(unittest.TestCase):
    def test_needs_keys(self):
        with self.assertRaises(amazon_eg.NotConfigured):
            amazon_eg.Client(Fetcher(), access_key="", secret_key="", partner_tag="")

    def test_request_shape(self):
        c = amazon_eg.Client(Fetcher(), "AK", "SK", "me-21")
        url, headers, body = c.build_request("SearchItems", {"Keywords": "coffee"},
                                             now=datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc))
        self.assertEqual(url, "https://webservices.amazon.eg/paapi5/searchitems")
        self.assertIn(b'"Marketplace": "www.amazon.eg"', body)
        self.assertIn(b'"PartnerTag": "me-21"', body)
        self.assertTrue(headers["Authorization"].startswith(
            "AWS4-HMAC-SHA256 Credential=AK/20261112/eu-west-1/ProductAdvertisingAPI/aws4_request"))

    def test_parse_item(self):
        item = {"ASIN": "B0TEST", "DetailPageURL": "https://www.amazon.eg/dp/B0TEST?tag=me-21",
                "ItemInfo": {"Title": {"DisplayValue": "Nescafe Gold 190g"},
                             "ByLineInfo": {"Brand": {"DisplayValue": "Nescafe"}}},
                "Images": {"Primary": {"Medium": {"URL": "https://m.media-amazon.com/x.jpg"}}},
                "Offers": {"Listings": [{"Price": {"Amount": 450.0}, "SavingBasis": {"Amount": 600.0},
                                         "Availability": {"Type": "Now"}}]}}
        o = amazon_eg.parse_item(item)
        self.assertEqual((o.sku, o.price, o.original_price, o.brand), ("B0TEST", 450, 600, "Nescafe"))
        self.assertEqual(o.store_discount_pct, 25.0)
        v2 = {"ASIN": "B0V2", "OffersV2": {"Listings": [{"Price": {"Money": {"Amount": 90},
                                                                   "SavingBasis": {"Money": {"Amount": 100}}}}]}}
        self.assertEqual(amazon_eg.parse_item(v2).original_price, 100)
        self.assertIsNone(amazon_eg.parse_item({"ASIN": "B0NONE"}))


class AmazonBestSellers(unittest.TestCase):
    page = (FIX / "amazon_bestsellers.html").read_text()

    def test_parse(self):
        offers = amazon_eg.parse_bestsellers(self.page, "Grocery")
        self.assertEqual([(o.sku, o.price) for o in offers],
                         [("B07BYPSDZV", 23.5), ("B0842HKTMQ", 56.0), ("B0BZPJ9R6S", 246.88)])
        first = offers[0]
        self.assertEqual(first.name, "Italiano fettucini 400gm")
        self.assertEqual(first.url, "https://www.amazon.eg/-/en/dp/B07BYPSDZV")
        self.assertTrue(first.image.startswith("https://images-eu.ssl-images-amazon.com/"))
        self.assertEqual(first.extra, {"rank": 1, "from_sellers": False})
        self.assertTrue(offers[2].extra["from_sellers"])  # "5 offers from EGP 246.88"

    def test_subcategories(self):
        self.assertEqual(amazon_eg.parse_subcategories(self.page, "grocery")[:2],
                         [("21840237031", "Baby Foods"), ("21840233031", "Bakery")])

    def fake(self, answer):
        asked = []

        class Fake(Fetcher):
            def get(self, url, headers=None):
                asked.append(url)
                return answer(url)

        return Fake(delay=0), asked

    def test_walks_department_and_sub_lists(self):
        f, asked = self.fake(lambda url: (200, self.page))
        offers = list(amazon_eg.collect_bestsellers(f, ["grocery"], log=lambda *_: None))
        self.assertEqual(len(asked), 2 * (1 + 3))  # the department + 3 sub-lists, 2 pages each
        self.assertEqual(asked[0], "https://www.amazon.eg/-/en/gp/bestsellers/grocery?ie=UTF8&pg=1")
        self.assertEqual(asked[2], "https://www.amazon.eg/-/en/gp/bestsellers/grocery/21840237031?ie=UTF8&pg=1")
        self.assertEqual(len(offers), 3)  # same products everywhere: kept once
        self.assertEqual(f.delay, 3.0)
        self.assertNotIn(503, f.retry_statuses)

    def test_every_level(self):
        level2 = (FIX / "amazon_bestsellers_level2.html").read_text()
        self.assertIn(("21840298031", "Formula"), amazon_eg.parse_subcategories(level2, "grocery", "21840237031"))
        self.assertIn(("21840299031", "Finger Food, Snacks & Rusks"),
                      amazon_eg.parse_subcategories(level2, "grocery", "21840237031"))
        self.assertEqual(len(amazon_eg.parse_subcategories(level2, "grocery", "21840237031")), 8)
        self.assertEqual(amazon_eg.parse_subcategories(level2, "grocery", "99999"), [])  # not this list's children
        self.assertEqual(len(amazon_eg.parse_subcategories(self.page, "grocery")), 3)  # department: level 1 only

        def answer(url):
            if "/grocery?" in url:
                return 200, self.page                     # the department: 3 sub-lists
            if "/grocery/21840237031?" in url:
                return 200, level2                        # Baby Foods: 8 sub-lists of its own
            return 200, "<html></html>"                   # everything below: no further lists
        f, asked = self.fake(answer)
        list(amazon_eg.collect_bestsellers(f, ["grocery"], log=lambda *_: None))
        lists = {u.split("?")[0] for u in asked}
        self.assertEqual(len(lists), 1 + 3 + 8)
        self.assertIn("https://www.amazon.eg/-/en/gp/bestsellers/grocery/21840298031", lists)  # Formula, level 2
        self.assertEqual(len(asked), 2 * len(lists))  # two pages of each list

    def test_all_departments_but_gift_cards(self):
        self.assertEqual(len(amazon_eg.DEFAULT_DEPARTMENTS), 17)
        self.assertNotIn("gift-cards", amazon_eg.DEFAULT_DEPARTMENTS)
        self.assertTrue(all(d in amazon_eg.DEPARTMENT_NAMES for d in amazon_eg.DEFAULT_DEPARTMENTS))

    def test_bot_check_stops_the_run(self):
        f, asked = self.fake(lambda url: (503, "<html>Enter the characters you see below (captcha)</html>"))
        with self.assertRaises(Blocked):
            list(amazon_eg.collect_bestsellers(f, ["grocery"], log=lambda *_: None))
        self.assertEqual(len(asked), 1)

    def test_api_only_with_keys(self):
        f, asked = self.fake(lambda url: (200, self.page))
        with mock.patch.dict(os.environ, {"AMAZON_ACCESS_KEY": "", "AMAZON_SECRET_KEY": "", "AMAZON_PARTNER_TAG": ""}):
            list(amazon_eg.collect(f, departments=["grocery"], max_pages=1, use_browser=False, log=lambda *_: None))
        self.assertIn("/gp/bestsellers/", asked[0])


class AmazonTodaysDeals(unittest.TestCase):
    def test_parse_real_cards(self):
        offers = amazon_eg.parse_deals((FIX / "amazon_deals.html").read_text())
        self.assertEqual([(o.sku, o.price, o.original_price) for o in offers[:3]],
                         [("B0DC5F7P3N", 273.98, 395.0), ("B0B3DQ2655", 348.98, 465.85), ("B0F1D312ML", 1365.0, 2184.0)])
        self.assertEqual(offers[0].extra, {"deal_label": "11.11 Deal", "deal_pct": 31})
        self.assertEqual(offers[2].extra["deal_label"], "Limited time deal")  # "Ends in ..." countdown
        self.assertIsNone(offers[3].original_price)  # a card with no list price is not a discount
        self.assertEqual(offers[0].name, "Capixy Lashes Treatment Serum 10Ml")

    def test_list_price_survives_a_best_seller_sighting(self):
        con = db.connect(":memory:")
        deal = Offer("amazon_eg", "B0X", "x", 80, 100, extra={"deal_label": "Deal"})
        best = Offer("amazon_eg", "B0X", "x", 80, None, extra={"rank": 3}, original_known=False)
        self.assertEqual(db.save_offers(con, [deal], 1000), (1, 1))
        self.assertEqual(db.save_offers(con, [best], 2000), (1, 0))  # same price: nothing moved
        row = con.execute("SELECT original_price, extra FROM products").fetchone()
        self.assertEqual(row["original_price"], 100)
        self.assertEqual(json.loads(row["extra"]), {"deal_label": "Deal", "rank": 3})
        db.save_offers(con, [Offer("amazon_eg", "B0X", "x", 95, None, original_known=False)], 3000)
        self.assertIsNone(con.execute("SELECT original_price FROM products").fetchone()[0])  # price moved: deal over
        con.close()


class Jumia(unittest.TestCase):
    page = (FIX / "jumia_flash_sales.html").read_text()

    def test_parse_real_page(self):
        from deals.stores import jumia
        offers = jumia.parse_page(self.page, "Flash Sales")
        self.assertEqual(len(offers), 2)
        o = offers[0]
        self.assertEqual((o.sku, o.price, o.original_price), ("SO316HA240M0PNAFAMZ", 1789.0, 2100.0))
        self.assertEqual(o.url, "https://www.jumia.com.eg/sokany-blender-2.5-liters-5000-watts-sk-666-135397672.html")
        self.assertEqual(o.brand, "Sokany")
        self.assertTrue(o.category.startswith("Home & Office / Home & Kitchen"))
        self.assertEqual(o.extra, {"deal_label": "Flash sale", "stock_text": "3 items left"})
        self.assertEqual(jumia.parse_page("<html>no data</html>"), [])

    def test_paging_and_cloudflare(self):
        from deals.stores import jumia
        asked = []

        class Fake(Fetcher):
            def get(self, url, headers=None):
                asked.append(url)
                return 200, self_page if "page=3" not in url else "<html></html>"
        self_page = self.page
        offers = list(jumia.collect(Fake(delay=0), max_pages=6, log=lambda *_: None))
        # page 2 repeats page 1, so Flash Sales stops there (Jumia repeats its last page) and Groceries is next
        self.assertEqual(asked[:3], ["https://www.jumia.com.eg/flash-sales/", "https://www.jumia.com.eg/flash-sales/?page=2",
                                     "https://www.jumia.com.eg/groceries/"])
        self.assertNotIn("https://www.jumia.com.eg/flash-sales/?page=3", asked)
        self.assertEqual(len(offers), 2)

        class Challenge(Fetcher):
            def get(self, url, headers=None):
                return 403, "<title>Just a moment...</title>"
        with self.assertRaises(Blocked):
            list(jumia.collect(Challenge(delay=0), log=lambda *_: None))


# Breadfast's real JSON layout is only known once you capture the app. These shapes are invented to
# cover the common ways shop APIs write products.
BF_LIST = {"data": {"category": {"name": "Dairy"}, "products": [
    {"id": 101, "name": {"en": "Juhayna Full Cream Milk 1L", "ar": "جهينة لبن"}, "price": 42.5,
     "old_price": 47, "image": {"url": "https://cdn.example/milk.jpg"}, "stock": 12},
    {"id": 102, "name": "Breadfast Croissant", "price": "25.00", "is_available": False},
    {"id": 103, "title": "Lurpak Butter 200g", "final_price": 180, "price": 210, "images": ["https://cdn.example/b.jpg"]},
    {"id": 9, "name": "Dairy", "slug": "dairy"},  # a category, not a product
]}, "meta": {"page": 1}}


def har(entries):
    return {"log": {"entries": [
        {"request": {"method": m, "url": u, "headers": [{"name": ":authority", "value": "x"},
                                                       {"name": "Authorization", "value": "Bearer T"},
                                                       {"name": "Accept-Encoding", "value": "br"}]},
         "response": {"status": 200, "content": {"mimeType": "application/json", "text": json.dumps(body)}}}
        for m, u, body in entries]}}


class Breadfast(unittest.TestCase):
    def test_recognises_products(self):
        offers = breadfast.products_in(BF_LIST)
        self.assertEqual([o.sku for o in offers], ["101", "102", "103"])
        milk, croissant, butter = offers
        self.assertEqual((milk.name, milk.price, milk.original_price, milk.image, milk.in_stock),
                         ("Juhayna Full Cream Milk 1L", 42.5, 47, "https://cdn.example/milk.jpg", True))
        self.assertEqual((croissant.price, croissant.in_stock), (25.0, False))
        self.assertEqual((butter.price, butter.original_price), (180, 210))  # final price, then the list price
        self.assertEqual(breadfast.products_in(BF_LIST, divisor=100)[0].price, 0.42)

    def test_read_har(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.har")
            with open(path, "w") as f:
                json.dump(har([
                    ("GET", "https://api.example/v3/categories/5/products?page=1", BF_LIST),
                    ("GET", "https://api.example/v3/categories/5/products?page=2", BF_LIST),  # same list
                    ("GET", "https://api.example/v3/cart", BF_LIST),  # products, but never replayed
                    ("POST", "https://api.example/v3/search", BF_LIST),  # not a GET
                    ("GET", "https://api.example/v3/banners", {"banners": [{"id": 1, "image": "x"}]}),
                ]), f)
            offers, requests = breadfast.read_har(path)
        self.assertEqual(len(offers), 9)  # three GET responses with products
        self.assertEqual([r["url"] for r in requests], ["https://api.example/v3/categories/5/products?page=1"])
        self.assertEqual(requests[0]["headers"], {"Authorization": "Bearer T"})

    def test_refresh_pages_until_nothing_new(self):
        def page_of(n):
            return {"products": [{"id": f"{n}-{i}", "name": f"p{n}{i}", "price": 10 + i} for i in range(2)]}

        asked = []

        class Fake(Fetcher):
            def request(self, url, data=None, headers=None, method=None):
                asked.append((url, headers))
                n = int(url.rsplit("=", 1)[1])
                return 200, json.dumps(page_of(n) if n <= 3 else {"products": []})

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "r.json")
            breadfast.save_requests(path, [{"url": "https://api.example/p?cat=5&page=2", "headers": {"A": "1"}}])
            offers = list(breadfast.collect(Fake(delay=0), path, log=lambda *_: None))
        self.assertEqual(len(offers), 6)
        self.assertEqual([u for u, _ in asked], [f"https://api.example/p?cat=5&page={n}" for n in (1, 2, 3, 4)])
        self.assertEqual(asked[0][1], {"A": "1"})

    def test_expired_session(self):
        class Fake(Fetcher):
            def request(self, url, data=None, headers=None, method=None):
                return 401, "{}"

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "r.json")
            breadfast.save_requests(path, [{"url": "https://api.example/p", "headers": {}}])
            with self.assertRaisesRegex(RuntimeError, "capture again"):
                list(breadfast.collect(Fake(delay=0), path, log=lambda *_: None))

    def test_not_set_up(self):
        with self.assertRaises(NotConfigured):
            list(breadfast.collect(Fetcher(), "/nonexistent/requests.json"))


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class Scheduler(unittest.TestCase):
    """The continuous checker, on a fake clock: each page 'takes' 2 seconds."""

    def make(self, pages, prices, plan_calls=None):
        clock = Clock()
        visits = []

        def page(key):
            def run():
                clock.t += 2
                visits.append(key)
                follow = [page(f"{key}+")] if key == "a" and f"{key}+" not in visited_keys else []
                visited_keys.update(f.key for f in follow)
                return [Offer("talabat", key, key, prices(key, clock.t))], follow
            return Task(key, run)

        visited_keys = set()

        def plan(fetcher):
            if plan_calls is not None:
                plan_calls.append(clock.t)
            return [page(k) for k in pages]

        loop = StoreLoop("talabat", plan, ":memory:", delay=0, hot_every=60, clock=clock, sleep=clock.sleep,
                         log=lambda *_: None)
        loop.con = db.connect(":memory:")
        return loop, clock, visits

    def test_round_robin_with_follow_ups(self):
        loop, clock, visits = self.make(["a", "b", "c"], lambda k, t: 10)
        for _ in range(8):
            loop.step()
        self.assertEqual(visits[:4], ["a", "b", "c", "a+"])  # the discovered page joins the round
        self.assertEqual(visits[4:8], ["a", "b", "c", "a+"])
        self.assertIsNotNone(loop.cycle_secs)
        row = loop.con.execute("SELECT * FROM live_status").fetchone()
        self.assertEqual((row["pages"], row["tasks"]), (8, 4))

    def test_hot_page_is_rechecked_sooner(self):
        many = [f"p{i}" for i in range(60)]
        # p0's price changes on every visit; the others never do
        loop, clock, visits = self.make(many, lambda k, t: t if k == "p0" else 10)
        for _ in range(300):
            loop.step()
        gaps = []
        last = None
        for i, k in enumerate(visits):
            if k == "p0":
                if last is not None:
                    gaps.append(i - last)
                last = i
        # without the hot rule p0 would come round once per 60 pages; hot, about every 30 pages (60 s at 2 s each)
        self.assertLess(max(gaps[1:]), 45)
        # and the cold pages still all get their turn
        self.assertEqual(set(many) - set(visits), set())

    def test_bot_check_pauses_the_store(self):
        clock = Clock()

        def blocked():
            raise Blocked("bot check")

        loop = StoreLoop("amazon_eg", lambda f: [Task("x", blocked)], ":memory:", delay=0, clock=clock,
                         sleep=clock.sleep, log=lambda *_: None)
        loop.con = db.connect(":memory:")
        loop.step()
        self.assertEqual(loop.paused_until, clock.t + 30 * 60)
        loop.step()  # paused: only waits
        self.assertEqual(loop.con.execute("SELECT error FROM live_status").fetchone()[0], "bot check")
        pauses = []
        for _ in range(5):  # each repeat doubles the pause, up to 4 hours
            clock.t = loop.paused_until
            loop.step()
            pauses.append(round((loop.paused_until - clock.t) / 60))
        self.assertEqual(pauses, [60, 120, 240, 240, 240])

    def test_not_configured_waits_quietly(self):
        clock = Clock()

        def plan(f):
            raise NotConfigured("no capture")

        loop = StoreLoop("breadfast", plan, ":memory:", delay=0, clock=clock, sleep=clock.sleep, log=lambda *_: None)
        loop.con = db.connect(":memory:")
        loop.step()
        self.assertGreater(loop.paused_until, clock.t)


class AppLinks(unittest.TestCase):
    def test_android_opens_the_app_at_the_item(self):
        from deals.applinks import links
        l = links("amazon_eg", "https://www.amazon.eg/-/en/dp/B0DC5F7P3N")
        # the app gets the plain /dp/ address; the fallback stays the English web page
        self.assertTrue(l["android"].startswith("intent://www.amazon.eg/dp/B0DC5F7P3N#Intent;scheme=https;"
                                                "package=com.amazon.mShop.android.shopping;"))
        self.assertIn("S.browser_fallback_url=https%3A%2F%2Fwww.amazon.eg%2F-%2Fen%2Fdp%2FB0DC5F7P3N", l["android"])
        self.assertEqual(l["ios"], "https://www.amazon.eg/dp/B0DC5F7P3N")
        self.assertIn("package=com.android.chrome;", l["browser"])
        # talabat: the short product address, which its app (com.talabat) opens at the user's own address,
        # as a plain link everywhere; the hub address (grocery/<hub>/...?aid=) made the app fail
        t = links("talabat", "https://www.talabat.com/egypt/grocery/650270/talabat-mart/product/kitkat-42g/s/904059?aid=8025")
        short = "https://www.talabat.com/egypt/talabat-mart/product/kitkat-42g/s/904059"
        self.assertEqual((t["web"], t["android"], t["ios"]), (short, short, short))
        self.assertIn("package=com.android.chrome;", t["browser"])
        self.assertIn("package=com.jumia.android;", links("jumia", "https://www.jumia.com.eg/x-1.html")["android"])

    def test_breadfast_uses_the_apps_own_link(self):
        from deals.applinks import links
        raw = {"id": 7, "name": "Milk", "price": 40, "share_url": "https://app.breadfast.com/product/7",
               "url": "https://cdn.example/7.jpg"}
        o = breadfast.to_offer(raw)
        self.assertEqual(o.extra, {"app_link": "https://app.breadfast.com/product/7"})
        self.assertIn("intent://app.breadfast.com/product/7#Intent;scheme=https;package=com.breadfast;",
                      links("breadfast", "", o.extra)["android"])
        self.assertEqual(breadfast.to_offer({"id": 8, "name": "Tea", "price": 9, "url": "https://cdn.x/8.png"}).extra, {})
        self.assertIn("package=com.breadfast;", links("breadfast", "", {})["android"])  # no link: the app's home


TEA_URL = "https://www.talabat.com/egypt/grocery/1/talabat-mart/product/tea/s/T2?aid=1"


class CatalogAndFilters(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(":memory:")
        t0 = 1_760_000_000
        self.now = t0 + 10 * DAY
        db.save_offers(self.con, [
            Offer("talabat", "t1", "Juhayna Full Cream Milk 1L", 50, 60, category="Dairy & Eggs / Milk"),
            Offer("amazon_eg", "a1", "Juhayna Full Cream Milk, 1 Liter", 45, category="Grocery",
                  extra={"deal_label": "11.11 Deal"}),
            Offer("talabat", "t2", "Lipton Black Tea, 500 gm", 100, category="Coffee & Tea / Tea", url=TEA_URL),
            Offer("talabat", "t3", "Chipsy Salt 10 pcs", 30, 40, in_stock=False, category="Snacks"),
            Offer("amazon_eg", "a2", "Steady Soap 125g", 90, 100, category="Beauty"),
        ], t0)
        db.save_offers(self.con, [Offer("talabat", "t2", "Lipton Black Tea, 500 gm", 80, category="Coffee & Tea / Tea",
                                        url=TEA_URL)],
                       self.now - 3600)  # a real price drop an hour ago
        self.items = finder.catalog(self.con, self.now)
        self.by = {d["sku"]: d for d in self.items}

    def tearDown(self):
        self.con.close()

    def test_enriched_fields(self):
        tea = self.by["t2"]
        self.assertEqual((tea["kind"], tea["drop_pct"], tea["usual_price"]), ("drop", 20.0, 100))
        self.assertIn("new", tea["flags"])
        self.assertIn("lowest", tea["flags"])
        self.assertEqual((tea["unit_price"], tea["unit"]), (160.0, "kg"))
        self.assertEqual(tea["links"]["android"], "https://www.talabat.com/egypt/talabat-mart/product/tea/s/T2")
        self.assertEqual(tea["links"]["web"], "https://www.talabat.com/egypt/talabat-mart/product/tea/s/T2")
        self.assertTrue(len(tea["spark"]) >= 2)
        self.assertIn("20% under its usual 100 EGP", tea["why"])
        self.assertNotIn("steady", self.by["a2"]["flags"])  # 10 days unchanged: not yet 14
        milk = self.by["a1"]
        self.assertEqual(milk["deal_label"], "11.11 Deal")



    def test_verdicts(self):
        self.assertEqual(self.by["t2"]["verdict"]["label"], "Buy now")  # lowest in 90 days
        self.assertEqual(self.by["t1"]["verdict"]["label"], "Cheaper elsewhere")  # 45 at Amazon vs 50
        self.assertEqual(self.by["t3"]["verdict"]["label"], "Out of stock")
        later = {d["sku"]: d for d in finder.catalog(self.con, self.now + 10 * DAY)}
        self.assertEqual(later["a2"]["verdict"]["label"], "Fair price")  # a 'discount' that never moves

    def test_cross_store_match(self):
        t1, a1 = self.by["t1"], self.by["a1"]
        self.assertEqual([m["id"] for m in t1["matches"]], [a1["id"]])
        self.assertTrue(a1["cheapest"])
        self.assertFalse(t1["cheapest"])
        self.assertEqual(self.by["t2"]["matches"], [])

    def test_filters(self):
        q = lambda **kw: [d["sku"] for d in finder.query(self.items, **kw)["items"]]
        self.assertEqual(set(q()), {"t1", "t2", "a2"})  # deals in stock
        self.assertEqual(set(q(stock="all")), {"t1", "t2", "a2", "t3"})
        self.assertEqual(q(stock="out"), ["t3"])
        self.assertEqual(set(q(kind="platform")), {"t1", "a2"})
        self.assertEqual(q(kind="drop"), ["t2"])
        self.assertEqual(q(min_pct=15), ["t2", "t1"])
        self.assertEqual(q(max_pct=12), ["a2"])
        self.assertEqual(q(stores=["amazon_eg"]), ["a2"])
        self.assertEqual(q(status=["new"]), ["t2"])
        self.assertEqual(q(q="juhayna milk", deals_only=False, sort="price"), ["a1", "t1"])
        self.assertEqual(q(deals_only=False, best=True), ["a1"])
        self.assertEqual(q(category="Coffee & Tea"), ["t2"])
        self.assertEqual(q(price_max=60), ["t1"])
        self.assertEqual(q(ids=[self.by["t3"]["id"]]), ["t3"])
        self.assertEqual(q(sort="unit", deals_only=False, stock="all")[:2], ["a1", "t1"])

    def test_facets_ignore_their_own_filter(self):
        r = finder.query(self.items, stores=["amazon_eg"])
        self.assertEqual(r["facets"]["store"], {"talabat": 2, "amazon_eg": 1})
        self.assertEqual(r["total"], 1)

    def test_steady_discount_scores_low(self):
        later = finder.catalog(self.con, self.now + 10 * DAY)
        soap = next(d for d in later if d["sku"] == "a2")
        self.assertIn("steady", soap["flags"])
        self.assertTrue(any("may never be charged" in w for w in soap["why"]))
        self.assertEqual(finder.query(later, status=["real"], stores=["amazon_eg"])["items"], [])


class Matching(unittest.TestCase):
    def test_brand_and_size_must_agree(self):
        from deals import matching
        items = [{"id": 1, "store": "talabat", "name": "Pepsi Can 330ml"},
                 {"id": 2, "store": "amazon_eg", "name": "Pepsi Soft Drink Can, 330 ml"},
                 {"id": 3, "store": "amazon_eg", "name": "Pepsi Can 250ml"},       # other size
                 {"id": 4, "store": "amazon_eg", "name": "Coca Cola Can 330ml"},   # other brand
                 {"id": 5, "store": "talabat", "name": "Pepsi Diet Can 330ml"}]     # same store as 1
        m = matching.match(items)
        self.assertEqual(m[1], [2])
        self.assertNotIn(3, m.get(2, []))
        self.assertNotIn(4, m.get(1, []))


class PackSizes(unittest.TestCase):
    def test_real_names(self):
        from deals.units import pack_size
        cases = {"Coca Cola Soft Drink Pet Bottle, 1.45L x2": (2.9, "L"), "Suntop Mango Fruit Drink 250 ml X3": (0.75, "L"),
                 "V7 Vitamin Sparkling Drinks New Artwork Pack, 6x300ml": (1.8, "L"), "Lipton Black Tea Soft , 500 gm": (0.5, "kg"),
                 "جهينة لبن ١ لتر": (1.0, "L"), "Elano water 330ml - 20 Pieces": (6.6, "L"),
                 "Nescafe Latte Iced Coffee, 220ml X 3 + 1 Free": (0.88, "L"),
                 "Gorilla Ultimate Mango Coconut 250ml, 2 + 1 Free Ultimate Bundle": (0.75, "L"),
                 "Pampers Baby Dry Size 4, 9-14kg, 64 Diapers": (64.0, "pc"), "Chipsy 10 pcs": (10.0, "pc")}
        for name, want in cases.items():
            got = pack_size(name)
            self.assertEqual((round(got[0], 4), got[1]), want, name)
        self.assertIsNone(pack_size("Granzia Pionizer Compressor Nebulizer"))


class BreadfastPastedRequest(unittest.TestCase):
    CURL = ("curl -X GET 'https://api.breadfast.com/v4/categories/12/products?page=1' "
            "-H 'authorization: Bearer abc' -H 'accept-encoding: br' -H 'x-app-version: 9.1'")

    def test_parse(self):
        r = breadfast.parse_curl(self.CURL)
        self.assertEqual(r, {"url": "https://api.breadfast.com/v4/categories/12/products?page=1",
                             "headers": {"authorization": "Bearer abc", "x-app-version": "9.1"}})
        r = breadfast.parse_curl("curl 'https://api.breadfast.com/p?page=2' \\\n  -H 'accept: application/json' \\\n  -b 'sid=1' --compressed")
        self.assertEqual(r["headers"], {"accept": "application/json", "Cookie": "sid=1"})

    def test_refusals(self):
        for bad in ["curl https://evil.example/x", "curl -X POST https://api.breadfast.com/v1/cart/add",
                    "curl -d 'a=1' https://api.breadfast.com/p", "curl https://api.breadfast.com/v1/user/profile",
                    "curl http://api.breadfast.com/p", "wget https://api.breadfast.com/p", "curl 'unclosed"]:
            with self.assertRaises(breadfast.BadRequest, msg=bad):
                breadfast.parse_curl(bad)
        with mock.patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.5", 443))]):
            with self.assertRaisesRegex(breadfast.BadRequest, "public"):
                breadfast.parse_curl("curl https://breadfast.internal.example/p")

    def fetcher(self, status=200, body=None):
        asked = []

        class Fake(Fetcher):
            def request(self, url, data=None, headers=None, method=None):
                asked.append((url, headers))
                return status, json.dumps(BF_LIST if body is None else body)
        return Fake(delay=0), asked

    def test_import_and_merge(self):
        with tempfile.TemporaryDirectory() as d:
            dbp, req = os.path.join(d, "d.sqlite3"), os.path.join(d, "r.json")
            f, asked = self.fetcher()
            r = breadfast.import_request(dbp, self.CURL, req, 1, f)
            self.assertEqual((r["products"], r["requests"]), (3, 1))
            self.assertEqual(asked[0][1], {"authorization": "Bearer abc", "x-app-version": "9.1"})
            other = self.CURL.replace("categories/12", "categories/13")
            self.assertEqual(breadfast.import_request(dbp, other, req, 1, f)["requests"], 2)
            page2 = self.CURL.replace("page=1", "page=2")  # same list: replaces, doesn't add
            self.assertEqual(breadfast.import_request(dbp, page2, req, 1, f)["requests"], 2)
            requests, _ = breadfast.load_requests(req)
            self.assertEqual(len(requests), 2)

    def test_expired_or_empty(self):
        with tempfile.TemporaryDirectory() as d:
            dbp, req = os.path.join(d, "d.sqlite3"), os.path.join(d, "r.json")
            with self.assertRaisesRegex(breadfast.BadRequest, "expired"):
                breadfast.import_request(dbp, self.CURL, req, 1, self.fetcher(401)[0])
            with self.assertRaisesRegex(breadfast.BadRequest, "no products"):
                breadfast.import_request(dbp, self.CURL, req, 1, self.fetcher(body={"banners": []})[0])
            self.assertFalse(os.path.exists(req))


class BreadfastUpload(unittest.TestCase):
    """The owner's upload page, on a real server in a thread."""

    def setUp(self):
        import threading
        from http.server import ThreadingHTTPServer
        from deals import web
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, "d.sqlite3")
        self.req = os.path.join(self.dir.name, "breadfast_requests.json")
        db.connect(self.db).close()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(self.db, self.req))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.har = json.dumps(har([("GET", "https://api.example/v3/categories/5/products?page=1", BF_LIST)])).encode()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.dir.cleanup()

    def post(self, body, token=None, divisor="1"):
        import urllib.error
        import urllib.request
        req = urllib.request.Request(f"{self.base}/admin/breadfast?divisor={divisor}", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {token}"} if token else {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_off_until_a_token_is_set(self):
        with mock.patch.dict(os.environ, {"DEALS_ADMIN_TOKEN": ""}):
            self.assertEqual(self.post(self.har, "x")[0], 403)

    def test_upload(self):
        token = "t" * 24
        with mock.patch.dict(os.environ, {"DEALS_ADMIN_TOKEN": token}), mock.patch("time.sleep"):
            self.assertEqual(self.post(self.har, "wrong-token-123456")[0], 401)
            self.assertEqual(self.post(b"not json at all", token)[0], 400)
            status, r = self.post(self.har, token)
        self.assertEqual(status, 200)
        self.assertEqual((r["products"], r["requests"]), (3, 1))
        self.assertEqual(r["samples"][0]["name"], "Juhayna Full Cream Milk 1L")
        self.assertTrue(os.path.exists(self.req))  # what's needed to refresh, kept privately
        self.assertEqual([f for f in os.listdir(self.dir.name) if f.startswith(".capture-")], [])  # capture deleted
        items = finder.query(finder.catalog(db.connect(self.db)), deals_only=False, stock="all")["items"]
        self.assertEqual({d["store"] for d in items}, {"breadfast"})
        self.assertEqual(len(items), 3)

    def test_paste_endpoint(self):
        import urllib.error
        import urllib.request
        token = "t" * 24
        body = json.dumps({"curl": BreadfastPastedRequest.CURL, "divisor": "1"}).encode()

        def send(b, tok=token):
            req = urllib.request.Request(f"{self.base}/admin/breadfast-request", data=b, method="POST",
                                         headers={"Authorization": f"Bearer {tok}"})
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())
        with mock.patch.dict(os.environ, {"DEALS_ADMIN_TOKEN": token}), mock.patch("time.sleep"), \
                mock.patch.object(breadfast.Fetcher, "request", return_value=(200, json.dumps(BF_LIST))):
            self.assertEqual(send(body, "wrong-token-123456")[0], 401)
            self.assertEqual(send(json.dumps({"curl": "curl https://evil.example/x"}).encode())[0], 422)
            status, r = send(body)
        self.assertEqual((status, r["products"], r["requests"]), (200, 3, 1))

    def call(self, path, body, token=None, method="POST"):
        import urllib.error
        import urllib.request
        req = urllib.request.Request(f"{self.base}{path}", data=None if body is None else json.dumps(body).encode(),
                                     method=method, headers={"Authorization": f"Bearer {token}"} if token else {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_push_endpoints(self):
        sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "x", "auth": "y"}}
        self.assertEqual(self.call("/api/push/key", None, method="GET")[0], 200)
        bad = {**sub, "endpoint": "https://127.0.0.1/steal"}
        self.assertEqual(self.call("/api/push/subscribe", {"subscription": bad})[0], 422)  # only browser push services
        status, r = self.call("/api/push/subscribe", {"subscription": sub, "prefs": {"watch": ["5"], "all_drops": 1, "min_drop": "20"}})
        self.assertEqual((status, r["prefs"]["watch"], r["prefs"]["min_drop"]), (200, [5], 20.0))
        self.call("/api/push/subscribe", {"subscription": sub, "prefs": {"watch": [6]}})  # updates, doesn't duplicate
        con = db.connect(self.db)
        rows = con.execute("SELECT prefs FROM push_subs").fetchall()
        self.assertEqual([json.loads(r[0])["watch"] for r in rows], [[6]])
        con.close()
        self.assertEqual(self.call("/api/push/unsubscribe", {"endpoint": sub["endpoint"]})[0], 200)
        self.assertEqual(self.call("/api/push/test", {"endpoint": sub["endpoint"]})[0], 404)

    def test_hub_settings(self):
        status, r = self.call("/api/talabat/hubs", None, method="GET")
        self.assertEqual(r["selected"], [619848])
        self.assertTrue(any(h["name"] == "El Obour Industrial Area" for h in r["hubs"]))  # the shipped list
        token = "t" * 24
        with mock.patch.dict(os.environ, {"DEALS_ADMIN_TOKEN": token}), mock.patch("time.sleep"):
            self.assertEqual(self.call("/admin/settings", {"talabat_hubs": [776372]}, "wrong-token-123456")[0], 401)
            self.assertEqual(self.call("/admin/settings", {"talabat_hubs": [123]}, token)[0], 422)  # unknown hub
            self.assertEqual(self.call("/admin/settings", {"talabat_hubs": []}, token)[0], 422)
            status, r = self.call("/admin/settings", {"talabat_hubs": [619848, 776372],
                                                      "hub_link": "https://www.talabat.com/egypt/grocery/619849/talabat-mart?aid=77"}, token)
        self.assertEqual((status, r["selected"]), (200, [619848, 776372, 619849]))
        from deals import settings
        saved = settings.load(os.path.join(self.dir.name, "settings.json"))["talabat_hubs"]
        self.assertEqual([h["id"] for h in saved], [619848, 776372, 619849])
        self.assertEqual(saved[2]["aid"], 77)  # the area from the pasted link
        self.assertEqual(saved[1]["name"], "El Obour Industrial Area")

    def test_piasters(self):
        with mock.patch.dict(os.environ, {"DEALS_ADMIN_TOKEN": "t" * 24}):
            status, r = self.post(self.har, "t" * 24, divisor="100")
        self.assertEqual((status, r["samples"][0]["price"]), (200, 0.42))


class Hubs(unittest.TestCase):
    def test_hub_names(self):
        self.assertEqual(talabat.hub_name("Tmart, El Obour Industrial Area BG"), "El Obour Industrial Area")
        self.assertEqual(talabat.hub_name("talabat mart, Helwan"), "Helwan")
        self.assertEqual(talabat.hub_name("طلبات مارت, المعادي القديمة"), "المعادي القديمة")

    def test_old_rows_become_maadi_old(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "d.sqlite3")
            con = db.connect(path)
            con.execute("INSERT INTO products (store, sku, name, first_seen, last_seen, price) VALUES"
                        " ('talabat', 'S6WENO', 'cola', 1, 1, 108), ('amazon_eg', 'B0X', 'x', 1, 1, 5)")
            con.commit(); con.close()
            con = db.connect(path)  # the migration runs on connect
            rows = dict(con.execute("SELECT store, sku FROM products").fetchall())
            self.assertEqual(rows, {"talabat": "619848:S6WENO", "amazon_eg": "B0X"})
            extra = json.loads(con.execute("SELECT extra FROM products WHERE store='talabat'").fetchone()[0])
            self.assertEqual(extra, {"hub": 619848, "hub_name": "Maadi Old"})
            con.close()

    def test_area_lookup(self):
        areas = talabat.parse_areas((FIX / "talabat_all_areas.html").read_text())
        self.assertEqual(areas[0]["city"], "Cairo")  # Cairo and Giza first
        self.assertIn({"id": 8025, "name": "El Obour - El Hay 5", "slug": "el-obour-el-hay-5", "city": "Cairo"}, areas)
        self.assertEqual(talabat.parse_area_hub((FIX / "talabat_area_groceries.html").read_text()),
                         {"id": 776372, "slug": "tmart-el-obour-industrial-area-bg"})
        self.assertIsNone(talabat.parse_area_hub(
            '<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"vendors":[]}}}</script>'))

    def test_discover_hubs(self):
        all_areas, area = (FIX / "talabat_all_areas.html").read_text(), (FIX / "talabat_area_groceries.html").read_text()
        asked = []

        class Fake(Fetcher):
            def get(self, url, headers=None):
                asked.append(url)
                return 200, all_areas if "all-areas" in url else area
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "talabat_hubs.json")
            talabat.discover_hubs(Fake(delay=0), path, log=lambda *_: None)
            data = json.load(open(path))
            self.assertEqual(list(data["hubs"]), ["776372"])
            hub = data["hubs"]["776372"]
            self.assertEqual(hub["name"], "El Obour Industrial Area")
            self.assertEqual(len(hub["areas"]), 4)
            n = len(asked)
            talabat.discover_hubs(Fake(delay=0), path, log=lambda *_: None)  # carries on: nothing left to look up
            self.assertEqual(len(asked), n + 1)
            from deals import web
            with mock.patch.object(web, "HUBS_FILE", Path(d) / "none.json"):
                known = web.known_hubs(os.path.join(d, "x.sqlite3"))
            self.assertEqual(known[776372]["name"], "El Obour Industrial Area")
            self.assertIn(known[776372]["aid"], {a["id"] for a in hub["areas"]})

    def test_migration_survives_both_names(self):
        # an old and a new version ran side by side: the same product exists as "SKU" and "619848:SKU"
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "d.sqlite3")
            con = db.connect(path)
            con.execute("INSERT INTO products (id, store, sku, name, first_seen, last_seen, price) VALUES"
                        " (1, 'talabat', '619848:S6WENO', 'cola', 1, 5, 108), (2, 'talabat', 'S6WENO', 'cola', 3, 9, 100),"
                        " (3, 'talabat', 'ONLYOLD', 'tea', 1, 1, 20)")
            con.executemany("INSERT INTO prices (product_id, at, price, in_stock) VALUES (?,?,?,1)",
                            [(1, 1, 108), (2, 3, 100), (3, 1, 20)])
            con.commit(); con.close()
            con = db.connect(path)  # must not crash
            rows = con.execute("SELECT id, sku FROM products WHERE store='talabat' ORDER BY id").fetchall()
            self.assertEqual([tuple(r) for r in rows], [(1, "619848:S6WENO"), (3, "619848:ONLYOLD")])
            self.assertEqual([r["price"] for r in db.history(con, 1)], [108, 100])  # the old copy's history kept
            con.close()

    def test_settings(self):
        from deals import settings
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            self.assertEqual(settings.load(path)["talabat_hubs"], settings.DEFAULT_HUBS)
            settings.save(path, {"talabat_hubs": [{"id": "776372", "aid": 8032, "name": "Obour"}, {"id": "x"}]})
            self.assertEqual(settings.load(path)["talabat_hubs"], [{"id": 776372, "aid": 8032, "name": "Obour"}])

    def test_scheduler_relists_when_settings_change(self):
        from deals import settings
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            settings.save(path, {"talabat_hubs": settings.DEFAULT_HUBS})
            clock, plans = Clock(), []

            def plan(f):
                hubs = settings.load(path)["talabat_hubs"]
                plans.append([h["id"] for h in hubs])
                return [Task(f"{h['id']}/x", lambda: ([], [])) for h in hubs]
            loop = StoreLoop("talabat", plan, ":memory:", delay=0, replan_file=path, clock=clock,
                             sleep=clock.sleep, log=lambda *_: None)
            loop.con = db.connect(":memory:")
            loop.step()
            settings.save(path, {"talabat_hubs": settings.DEFAULT_HUBS + [{"id": 776372, "aid": 8032, "name": "O"}]})
            os.utime(path, (time_now := os.path.getmtime(path) + 5, time_now))
            loop.step()
            self.assertEqual(plans, [[619848], [619848, 776372]])
            self.assertEqual(set(loop.tasks), {"619848/x", "776372/x"})


class Push(unittest.TestCase):
    def decrypt(self, body, ua_key, auth):
        """What the browser does with an aes128gcm push message (RFC 8291), to check our side."""
        import hashlib as hl, hmac as hm
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        H = lambda k, d: hm.new(k, d, hl.sha256).digest()
        salt, rs, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
        as_public, record = body[21:21 + idlen], body[21 + idlen:]
        ua_public = ua_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        shared = ua_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
        ikm = H(H(auth, shared), b"WebPush: info\x00" + ua_public + as_public + b"\x01")
        prk = H(salt, ikm)
        plain = AESGCM(H(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]).decrypt(
            H(prk, b"Content-Encoding: nonce\x00\x01")[:12], record, None)
        self.assertEqual((rs, plain[-1:]), (4096, b"\x02"))
        return plain[:-1]

    def test_round_trip(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from deals import push
        ua = ec.generate_private_key(ec.SECP256R1())
        auth = os.urandom(16)
        p256dh = push.b64u(ua.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
        msg = json.dumps({"title": "↓ 20%  حليب جهينة", "body": "50 → 40 EGP"}, ensure_ascii=False).encode()
        self.assertEqual(self.decrypt(push.encrypt(msg, p256dh, push.b64u(auth)), ua, auth), msg)

    def test_vapid_header_verifies(self):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
        from deals import push
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "vapid.json")
            v = push.Vapid.load_or_create(path)
            self.assertEqual(push.Vapid.load_or_create(path).public_b64, v.public_b64)  # kept, not remade
            self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
            h = v.header("https://fcm.googleapis.com/fcm/send/abc", now=1_800_000_000)
            jwt, k = h.removeprefix("vapid t=").split(", k=")
            self.assertEqual(k, v.public_b64)
            head, claims, sig = jwt.split(".")
            self.assertEqual(json.loads(push.unb64u(claims))["aud"], "https://fcm.googleapis.com")
            raw = push.unb64u(sig)
            der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), push.unb64u(k)).verify(
                der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))  # raises if wrong

    def test_wants(self):
        from deals import push
        d = {"id": 5, "store": "talabat", "hub": 776372, "top_category": "Dairy", "name": "Juhayna Milk 1L",
             "before": 50, "price": 40, "drop_pct": 20.0, "discount_pct": 0}
        self.assertTrue(push.wants({"watch": [5]}, d))
        self.assertFalse(push.wants({"watch": [5], "targets": {"5": 35}}, d))  # target not reached yet
        self.assertFalse(push.wants({"watch": [6]}, d))
        self.assertTrue(push.wants({"all_drops": True, "min_drop": 15}, d))
        self.assertFalse(push.wants({"all_drops": True, "min_drop": 25}, d))
        self.assertFalse(push.wants({"all_drops": True, "stores": ["amazon_eg"]}, d))
        self.assertTrue(push.wants({"all_drops": True, "hubs": [776372], "words": ["milk"]}, d))
        self.assertFalse(push.wants({"all_drops": True, "words": ["coffee"]}, d))

    def test_notifier_batches_and_forgets_dead_subscriptions(self):
        from deals import push
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "d.sqlite3")
            con = db.connect(path)
            db.save_offers(con, [Offer("talabat", "a", "Milk", 50), Offer("talabat", "b", "Tea", 100)], 1000)
            db.save_offers(con, [Offer("talabat", "a", "Milk", 40), Offer("talabat", "b", "Tea", 90)], 2000)
            for ep, prefs in (("https://fcm.googleapis.com/x", {"all_drops": True}), ("https://fcm.googleapis.com/gone", {"all_drops": True})):
                con.execute("INSERT INTO push_subs (endpoint, subscription, prefs, created) VALUES (?,?,?,0)",
                            (ep, json.dumps({"endpoint": ep, "keys": {}}), json.dumps(prefs)))
            con.commit(); con.close()
            n = push.Notifier(path, {"talabat": "talabat mart"}, log=lambda *_: None)
            n.last = 1500
            sent = []
            n.tick(now=2100, sender=lambda v, sub, msg: (sent.append((sub["endpoint"], msg)), 410 if sub["endpoint"].endswith("gone") else 201)[1])
            self.assertEqual(len(sent), 2)
            self.assertEqual(sent[0][1]["title"], "2 price drops")
            con = db.connect(path)
            self.assertEqual([r[0] for r in con.execute("SELECT endpoint FROM push_subs")], ["https://fcm.googleapis.com/x"])
            con.close()


def offer(sku, price, original=None, in_stock=True, store="talabat"):
    return Offer(store=store, sku=sku, name=f"item {sku}", price=price, original_price=original, in_stock=in_stock)


class HistoryAndDeals(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(":memory:")
        self.t0 = 1_760_000_000

    def tearDown(self):
        self.con.close()

    def at(self, days):
        return self.t0 + int(days * DAY)

    def test_price_rows_only_on_change(self):
        self.assertEqual(db.save_offers(self.con, [offer("a", 10)], self.at(0)), (1, 1))
        self.assertEqual(db.save_offers(self.con, [offer("a", 10)], self.at(1)), (1, 0))
        self.assertEqual(db.save_offers(self.con, [offer("a", 8)], self.at(2)), (1, 1))
        self.assertEqual(db.save_offers(self.con, [offer("a", 8, in_stock=False)], self.at(3)), (1, 1))
        self.assertEqual([r["price"] for r in db.history(self.con, 1)], [10, 8, 8])
        stats = {}
        db.save_offers(self.con, [offer("a", 9), offer("b", 5), offer("c", 5)], self.at(4), stats)
        self.assertEqual(stats, {"new": 2, "moved": 1, "held": 0})

    def test_one_off_wild_price_is_not_recorded(self):
        # talabat once showed a 75 EGP razor at 1 EGP for one check
        db.save_offers(self.con, [offer("r", 75, 100)], self.at(0))
        stats = {}
        self.assertEqual(db.save_offers(self.con, [offer("r", 1, 100)], self.at(1), stats), (1, 0))
        self.assertEqual(stats["held"], 1)
        self.assertEqual(db.save_offers(self.con, [offer("r", 75, 100)], self.at(2)), (1, 0))  # back: nothing moved
        self.assertEqual([r["price"] for r in db.history(self.con, 1)], [75])
        # a real big drop shows up twice in a row, and is recorded on the second sighting
        db.save_offers(self.con, [offer("r", 20, 100)], self.at(3))
        db.save_offers(self.con, [offer("r", 20, 100)], self.at(4))
        self.assertEqual([r["price"] for r in db.history(self.con, 1)], [75, 20])
        # ordinary moves are recorded at once
        db.save_offers(self.con, [offer("r", 18, 100)], self.at(5))
        self.assertEqual([r["price"] for r in db.history(self.con, 1)], [75, 20, 18])

    def test_store_discount(self):
        db.save_offers(self.con, [offer("a", 90, 100), offer("b", 98, 100), offer("c", 50)], self.at(0))
        deals = finder.find_deals(self.con, min_pct=5, now=self.at(0.1))
        self.assertEqual([d["sku"] for d in deals], ["a"])
        self.assertEqual((deals[0]["discount_pct"], deals[0]["saving"]), (10.0, 10))

    def test_drop_below_usual_price(self):
        db.save_offers(self.con, [offer("a", 100)], self.at(0))
        db.save_offers(self.con, [offer("a", 80)], self.at(10))  # no struck-through price shown
        deals = finder.find_deals(self.con, min_pct=5, now=self.at(10.5))
        self.assertEqual(len(deals), 1)
        d = deals[0]
        self.assertEqual((d["usual_price"], d["drop_pct"], d["saving"]), (100, 20.0, 20))
        self.assertIn("lowest", d["flags"])
        self.assertIn("new", d["flags"])

    def test_too_new_to_judge(self):
        db.save_offers(self.con, [offer("a", 100)], self.at(0))
        db.save_offers(self.con, [offer("a", 80)], self.at(1))
        self.assertEqual(finder.find_deals(self.con, now=self.at(1.5)), [])  # under 3 days of history

    def test_steady_discount_is_flagged(self):
        db.save_offers(self.con, [offer("a", 90, 100)], self.at(0))
        d = finder.find_deals(self.con, now=self.at(20))[0]
        self.assertIn("steady", d["flags"])

    def test_out_of_stock_is_not_a_deal(self):
        db.save_offers(self.con, [offer("a", 50, 100, in_stock=False)], self.at(0))
        self.assertEqual(finder.find_deals(self.con, now=self.at(1)), [])

    def test_filters_and_sort(self):
        db.save_offers(self.con, [offer("a", 90, 100), offer("b", 50, 100),
                                  offer("c", 700, 1000, store="amazon_eg")], self.at(0))
        now = self.at(0.1)
        self.assertEqual([d["sku"] for d in finder.find_deals(self.con, now=now)], ["b", "c", "a"])
        self.assertEqual([d["sku"] for d in finder.find_deals(self.con, sort="saving", now=now)], ["c", "b", "a"])
        self.assertEqual([d["sku"] for d in finder.find_deals(self.con, store="amazon_eg", now=now)], ["c"])
        self.assertEqual([d["sku"] for d in finder.find_deals(self.con, min_pct=40, now=now)], ["b"])


if __name__ == "__main__":
    unittest.main()


class Pages(unittest.TestCase):
    """The product page, coupons, the Telegram channel and the Amazon tag."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "d.sqlite3")
        con = db.connect(self.path)
        self.t0 = 1_760_000_000
        db.save_offers(con, [Offer("talabat", "650270:T2", "Lipton Black Tea <500 gm>", 100, category="Coffee & Tea / Tea",
                                   url=TEA_URL, image="https://img/t.jpg", extra={"hub": 650270, "hub_name": "El Obour El Hay 9"}),
                             Offer("amazon_eg", "B0DC5F7P3N", "Soap", 90, 100, url="https://www.amazon.eg/-/en/dp/B0DC5F7P3N")],
                       self.t0)
        db.save_offers(con, [Offer("talabat", "650270:T2", "Lipton Black Tea <500 gm>", 50, 100, category="Coffee & Tea / Tea",
                                   url=TEA_URL, image="https://img/t.jpg", extra={"hub": 650270, "hub_name": "El Obour El Hay 9"})],
                       self.t0 + 5 * DAY)
        self.now = self.t0 + 5 * DAY + 60
        self.con = con

    def tearDown(self):
        self.con.close()

    def test_product_page_has_preview_tags_and_history(self):
        from deals import pages
        self.con.row_factory = __import__("sqlite3").Row
        p = dict(self.con.execute("SELECT * FROM products WHERE sku='650270:T2'").fetchone())
        item = next(d for d in finder.catalog(self.con, self.now) if d["id"] == p["id"])
        page = pages.product_page(p, item, db.history(self.con, p["id"]), [], [], "https://deals.example", self.now).decode()
        self.assertIn('<meta property="og:title" content="Lipton Black Tea &lt;500 gm&gt; · 50 ج.م">', page)  # escaped
        self.assertIn('og:image" content="https://img/t.jpg"', page)
        self.assertIn("خصم 50%", page)
        self.assertIn("<svg class=\"chart\"", page)
        self.assertIn("📍 El Obour El Hay 9", page)
        self.assertIn('data-android="https://www.talabat.com/egypt/talabat-mart/product/tea/s/T2"', page)
        self.assertNotIn("<500", page)

    def test_coupons_lines(self):
        from deals import pages
        c = pages.parse_coupons("# mine\ntalabat | MART50 | 50 off | 2027-01-31\nAmazon | A1\nbad line\n"
                                "jumia | J | x | | javascript:alert(1)")
        self.assertEqual([x["code"] for x in c], ["MART50", "A1", "J"])
        self.assertEqual((c[0]["expires"], c[1]["store"]), ("2027-01-31", "amazon_eg"))
        self.assertNotIn("url", c[2])  # only https links
        html_ = pages.coupons_page(c + [{"store": "jumia", "code": "OLD", "expires": "2020-01-01"}]).decode()
        self.assertIn('data-copy="MART50"', html_)
        self.assertNotIn("OLD", html_)

    def test_telegram_posts_fresh_big_deals_once(self):
        from deals import telegram
        sent = []
        p = telegram.Poster(self.path, "t", "@ch", "https://deals.example", send=lambda m, b: sent.append((m, b)))
        p.since = self.t0 + 5 * DAY - 10
        self.assertEqual(p.once(self.now), 1)  # the tea at half price; the soap's 10 % isn't enough
        method, body = sent[0]
        self.assertEqual((method, body["chat_id"], body["photo"]), ("sendPhoto", "@ch", "https://img/t.jpg"))
        self.assertIn("https://deals.example/p/", body["caption"])
        self.assertIn("&lt;500 gm&gt;", body["caption"])
        p.since = 0
        self.assertEqual(p.once(self.now + 120), 0)  # never the same price twice

    def test_amazon_tag(self):
        from deals.applinks import links
        with unittest.mock.patch.dict(os.environ, {"AMAZON_TAG": "mytag-21"}):
            l = links("amazon_eg", "https://www.amazon.eg/-/en/dp/B0DC5F7P3N")
            self.assertTrue(l["web"].endswith("/dp/B0DC5F7P3N?tag=mytag-21"))
            self.assertIn("amazon.eg/dp/B0DC5F7P3N?tag=mytag-21#Intent", l["android"])
            self.assertNotIn("tag=", links("jumia", "https://www.jumia.com.eg/x.html")["web"])
        with unittest.mock.patch.dict(os.environ, {"AMAZON_TAG": "bad tag&x=1"}):
            self.assertNotIn("tag=", links("amazon_eg", "https://www.amazon.eg/dp/B0DC5F7P3N")["web"])


class SmallStores(unittest.TestCase):
    def test_dream2000_shopify_catalogue(self):
        from deals.stores import dream2000
        offers = dream2000.parse_products(json.loads((FIX / "dream2000_products.json").read_text()))
        self.assertEqual(len(offers), 3)
        o = offers[0]
        self.assertEqual((o.store, o.sku, o.price, o.original_price, o.in_stock), ("dream2000", "10411890016395",
                                                                                   13730.0, 15110.0, True))
        self.assertTrue(o.url.startswith("https://dream2000.com/products/"))
        self.assertEqual((o.brand, o.category), ("LG", "Appliances & electronics"))  # no type: the store's line

    def test_dream2000_cheapest_buyable_variant(self):
        from deals.stores import dream2000
        data = {"products": [{"id": 1, "handle": "tv", "title": "TV", "images": [], "variants": [
            {"price": "900", "compare_at_price": "1000", "available": False},
            {"price": "950", "compare_at_price": "1200", "available": True}]}]}
        o = dream2000.parse_products(data)[0]
        self.assertEqual((o.price, o.original_price, o.in_stock), (950.0, 1200.0, True))

    def test_kenzz_page_and_its_codes(self):
        from deals.stores import kenzz
        offers = kenzz.parse_page((FIX / "kenzz_category.html").read_text(), "ألعاب")
        self.assertEqual(len(offers), 4)
        o = offers[0]
        self.assertEqual((o.sku, o.price, o.original_price), ("F7S75J8UWACZ", 654.0, 854.0))
        self.assertEqual((o.extra["promo_code"], o.extra["promo_price"]), ("KBF26", 543.0))
        self.assertTrue(o.url.startswith("https://kenzz.com/product/F7S75J8UWACZ/"))
        self.assertEqual(kenzz.category_name("https://kenzz.com/category/A-B/%D9%83%D9%84-%D8%AD%D8%A7%D8%AC%D8%A9/"
                                             "%D8%A3%D9%84%D8%B9%D8%A7%D8%A8"), "ألعاب")
        self.assertEqual(kenzz.parse_page("<html>new design</html>"), [])


class Forecast(unittest.TestCase):
    def rows(self, prices_by_day):
        t0 = 1_760_000_000
        return [{"at": t0 + d * DAY, "price": p, "original_price": None, "in_stock": True} for d, p in prices_by_day]

    def test_a_weekly_sale_is_predicted(self):
        from deals import forecast
        # 100 EGP, on sale at 80 for 2 days every 7 days, last sale ended on day 16
        rows = self.rows([(0, 100), (7, 80), (9, 100), (14, 80), (16, 100)])
        now = rows[0]["at"] + 18 * DAY
        r = forecast.rhythm(rows, now)
        self.assertEqual((r["regular"], r["sales"], r["every_days"], r["lasts_days"], r["typical_sale"]),
                         (100, 2, 7.0, 2.0, 80))
        self.assertFalse(r["on_sale"])
        self.assertEqual(r["next_in_days"], 3.0)  # day 14 + 7 = day 21, now day 18
        self.assertIn("about every 7 days, usually down to 80 EGP", r["text"])
        # and the advice is to wait for it
        d = {"price": 100, "usual_price": 100, "matches": [], "in_stock": True, "flags": [], "discount_pct": 0,
             "kind": None, "rhythm": r}
        self.assertEqual(finder.verdict(d)["label"], "Wait")

    def test_on_sale_now_and_too_little_history(self):
        from deals import forecast
        rows = self.rows([(0, 100), (7, 80), (9, 100), (14, 80)])
        r = forecast.rhythm(rows, rows[0]["at"] + 15 * DAY)
        self.assertTrue(r["on_sale"])
        self.assertEqual(r["ends_in_days"], 1.0)
        self.assertIsNone(forecast.rhythm(self.rows([(0, 100), (7, 80), (9, 100)]), rows[0]["at"] + 10 * DAY))


class Brief(unittest.TestCase):
    def test_counts(self):
        con = db.connect(":memory:")
        t0 = 1_760_000_000
        db.save_offers(con, [Offer("talabat", "a", "Steady 1", 90, 100), Offer("talabat", "b", "Tea", 100)], t0)
        db.save_offers(con, [Offer("talabat", "b", "Tea", 70, 100)], t0 + 20 * DAY)
        b = finder.brief(finder.catalog(con, t0 + 20 * DAY + 60), t0 + 20 * DAY + 60)
        self.assertEqual((b["deals"], b["dropped_24h"], b["fake"]), (1, 1, 1))
        self.assertEqual(b["top"][0]["name"], "Tea")
        self.assertEqual(b["biggest_saving"]["name"], "Tea")
        con.close()

    def test_trust_pages(self):
        from deals import pages
        self.assertIn("As an Amazon Associate we earn from qualifying purchases", pages.about_page(["طلبات مارت"]).decode())
        self.assertIn("mailto:hi@example.com", pages.contact_page("hi@example.com").decode())
        self.assertIn("No accounts", pages.privacy_page().decode())


class InflatedWasPrice(unittest.TestCase):
    def test_a_huge_never_charged_was_price_is_not_a_deal(self):
        con = db.connect(":memory:")
        t0 = 1_760_000_000
        db.save_offers(con, [Offer("kenzz", "w", "Washer", 19647, 102548), Offer("jumia", "j", "Flash", 300, 1000)], t0)
        for t in (t0 + DAY, t0 + DAY + 60):  # the 1000 was really charged (a jump this big counts when seen twice)
            db.save_offers(con, [Offer("jumia", "j", "Flash", 1000)], t)
        for t in (t0 + 2 * DAY, t0 + 2 * DAY + 30):
            db.save_offers(con, [Offer("jumia", "j", "Flash", 300, 1000)], t)
        by = {d["sku"]: d for d in finder.catalog(con, t0 + 2 * DAY + 60)}
        self.assertIn("inflated", by["w"]["flags"])
        self.assertEqual(by["w"]["verdict"]["label"], "Check the price")
        self.assertNotIn("inflated", by["j"]["flags"])
        r = finder.query(list(by.values()), status=["real"])
        self.assertEqual([d["sku"] for d in r["items"]], ["j"])
        self.assertEqual(finder.brief(list(by.values()), t0 + 2 * DAY + 60)["fake"], 1)
        con.close()


class PriceWentUp(unittest.TestCase):
    def test_a_struck_through_price_after_a_rise_is_not_buy_now(self):
        # seen on the live site: Amazon showed 6,999, then 13,998.01 "30 % off 19,998.01" nine hours later
        con = db.connect(":memory:")
        t0 = 1_794_490_654
        db.save_offers(con, [Offer("amazon_eg", "B0FN7FVX8N", "Nespresso Inissia", 6999)], t0)
        for t in (t0 + 30000, t0 + 30859):  # a jump this big counts when seen twice
            db.save_offers(con, [Offer("amazon_eg", "B0FN7FVX8N", "B0FN7FVX8N", 13998.01, 19998.01)], t)
        d = finder.catalog(con, t0 + 33000)[0]
        self.assertEqual(d["name"], "Nespresso Inissia")  # a bare code never replaces the name
        self.assertIn("rose", d["flags"])
        self.assertEqual((d["verdict"]["label"], d["prev_price"], d["recent_low"]), ("Wait", 6999, 6999))
        self.assertIn("100% dearer", d["verdict"]["why"])
        self.assertLess(d["score"], 20)
        self.assertEqual(finder.query([d], status=["real"])["total"], 0)
        self.assertEqual(finder.brief([d])["deals"], 0)
        con.close()
