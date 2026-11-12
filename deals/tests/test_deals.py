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
from deals.stores import amazon_eg, breadfast, talabat

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
        self.assertTrue(cola.url.endswith(f"/s/{cola.sku}?aid=9838"))
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

        offers = list(talabat.collect(Fake(delay=0), area_id=7882, log=lambda *_: None))
        self.assertEqual(len(asked), 1 + 4 * 2)  # categories + 4 subcategories x 2 pages
        self.assertIn("/egypt/grocery/619848/talabat-mart/dairy-eggs/", asked[1])
        self.assertTrue(asked[2].endswith("aid=7882&page=2"))
        self.assertEqual(len(offers), 5)  # every fake page holds the same 5 products: each is kept once
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

    def test_bot_check_stops_the_run(self):
        f, asked = self.fake(lambda url: (503, "<html>Enter the characters you see below (captcha)</html>"))
        with self.assertRaises(Blocked):
            list(amazon_eg.collect_bestsellers(f, ["grocery"], log=lambda *_: None))
        self.assertEqual(len(asked), 1)

    def test_api_only_with_keys(self):
        f, asked = self.fake(lambda url: (200, self.page))
        with mock.patch.dict(os.environ, {"AMAZON_ACCESS_KEY": "", "AMAZON_SECRET_KEY": "", "AMAZON_PARTNER_TAG": ""}):
            list(amazon_eg.collect(f, departments=["grocery"], max_pages=1, log=lambda *_: None))
        self.assertIn("/gp/bestsellers/", asked[0])


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
