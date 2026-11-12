import unittest
from datetime import datetime, timezone
from pathlib import Path

from deals import db, finder, sigv4
from deals.http import Fetcher
from deals.models import Offer
from deals.stores import amazon_eg, talabat

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
