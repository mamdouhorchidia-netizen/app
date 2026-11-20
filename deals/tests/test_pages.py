"""The server-written pages (deals/pages.py): structured data, warnings, the chart's edge cases, escaping of hostile
names everywhere a name appears, coupons, and the parts every page must carry (nav, footer, disclosure)."""

import json
import re
import unittest
from datetime import datetime, timedelta

from deals import pages

DAY = 86400
NOW = 1_795_000_000
HOSTILE = '</script><script>alert(1)</script><img src=x onerror=alert(2)> "quote" & \'apos\''


def product(**kw):
    p = {"id": 7, "store": "talabat", "sku": "1:ABC", "name": "Lipton Tea 100 bags", "brand": "Lipton",
         "category": "Coffee & Tea / Tea", "url": "https://www.talabat.com/egypt/x", "image": "https://img/t.jpg",
         "price": 50.0, "original_price": 100.0, "in_stock": 1, "first_seen": NOW - 120 * DAY, "last_seen": NOW,
         "extra": json.dumps({"hub_name": "Maadi"})}
    p.update(kw)
    return p


def item(**kw):
    d = {"id": 7, "store": "talabat", "name": "Lipton Tea 100 bags", "price": 50.0, "original_price": 100.0,
         "usual_price": 80.0, "discount_pct": 50.0, "kind": "platform", "saving": 50.0, "flags": [], "in_stock": True,
         "links": {"web": "https://w", "android": "intent://a", "ios": "talabat://i"}, "hub_name": "Maadi",
         "top_category": "Coffee & Tea", "verdict": {"label": "Buy now", "tone": "good", "why": "50% off."},
         "matches": [], "cheapest": False, "recent_low": None, "days_at_price": 3, "rhythm": None,
         "promo_code": None, "promo_price": None, "unit_text": None, "deal_label": None}
    d.update(kw)
    return d


def history(prices, start=NOW - 60 * DAY, step=5 * DAY):
    return [{"at": start + i * step, "price": x, "original_price": None, "in_stock": 1} for i, x in enumerate(prices)]


def render(p=None, d=None, h=None, matches=(), related=(), now=NOW):
    return pages.product_page(p or product(), item() if d is None else d, history([90, 50]) if h is None else h,
                              list(matches), list(related), "https://deals.example", now).decode()


def ld_of(page):
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)
    return json.loads(m.group(1))


class ProductPage(unittest.TestCase):
    def test_json_ld_is_a_product_with_an_egp_offer(self):
        ld = ld_of(render())
        self.assertEqual((ld["@type"], ld["name"], ld["offers"]["@type"]), ("Product", "Lipton Tea 100 bags", "Offer"))
        self.assertEqual((ld["offers"]["price"], ld["offers"]["priceCurrency"]), (50.0, "EGP"))
        self.assertEqual(ld["offers"]["availability"], "https://schema.org/InStock")
        self.assertEqual(ld["url"], "https://deals.example/p/7")
        out = ld_of(render(p=product(in_stock=0)))
        self.assertEqual(out["offers"]["availability"], "https://schema.org/OutOfStock")

    def test_preview_tags_stay_arabic(self):
        page = render()
        self.assertIn('<meta property="og:title" content="Lipton Tea 100 bags · 50 ج.م">', page)
        self.assertIn("خصم 50%", page)
        self.assertIn("بدل 100 ج.م", page)
        self.assertIn('og:image" content="https://img/t.jpg"', page)
        self.assertIn('twitter:card', page)

    def test_hostile_names_are_escaped_everywhere(self):
        page = render(p=product(name=HOSTILE, brand=HOSTILE, category=HOSTILE + " / x"),
                      d=item(name=HOSTILE, hub_name=HOSTILE, top_category=HOSTILE, deal_label=HOSTILE,
                             unit_text=HOSTILE, promo_code=HOSTILE, promo_price=40,
                             verdict={"label": HOSTILE, "tone": '"><x', "why": HOSTILE}),
                      matches=[item(id=8, store="amazon_eg", name=HOSTILE, price=60, links={"web": 'javascript:"x'})],
                      related=[item(id=9, name=HOSTILE, image='x" onload="alert(3)')])
        # nothing hostile survives as markup: only our own scripts, no injected tags or attributes
        self.assertNotIn("<script>alert", page)
        self.assertNotIn("<img src=x", page)
        self.assertNotIn('onload="alert', page)
        self.assertNotIn('"><x', page)
        self.assertEqual(page.count("</script>"), page.count("<script"))
        raw_ld = re.search(r'application/ld\+json">(.*?)</script>', page, re.S).group(1)
        self.assertNotIn("<", raw_ld)
        self.assertEqual(ld_of(page)["name"], HOSTILE)  # still the real name once parsed
        self.assertEqual(ld_of(page)["brand"]["name"], HOSTILE)
        for attr in re.findall(r'(?:alt|data-title|data-copy|content|title)="([^"]*)"', page):
            self.assertNotIn("<", attr)

    def test_warnings_follow_the_flags(self):
        plain = render()
        self.assertNotIn('class="alert', plain)
        steady = render(d=item(flags=["steady"], days_at_price=21))
        self.assertIn("same 'discounted' price for 21 days: the old price may never be charged", steady)
        self.assertIn("discount never moves", steady)
        inflated = render(d=item(flags=["inflated"], original_price=492))
        self.assertIn('"was" price looks inflated', inflated)
        self.assertIn("the 'was' price (492 EGP) has never been charged here: likely inflated", inflated)
        rose = render(p=product(price=95), d=item(price=95, flags=["rose"], recent_low=90,
                                                    verdict={"label": "Wait", "tone": "warn",
                                                             "why": "It was 90 EGP lately: it's 6% dearer now."}))
        self.assertIn("it was 90 EGP lately: 6% dearer now", rose)
        self.assertNotIn("It was 90 EGP lately", rose)  # said once, in the warning
        self.assertIn('class="pill warn">Wait<', rose)

    def test_buttons_rhythm_and_promo(self):
        page = render(d=item(promo_code="KBF26", promo_price=40,
                             rhythm={"text": "On sale now.", "on_sale": True, "next_in_days": None}))
        self.assertIn('data-android="intent://a"', page)
        self.assertIn('data-ios="talabat://i"', page)
        self.assertIn(">Open in talabat ↗<", page)
        self.assertIn('href="/?p=7"', page)
        self.assertIn('data-share="https://deals.example/p/7"', page)
        self.assertIn("https://wa.me/?text=", page)  # sharing works without JavaScript too
        self.assertIn('data-copy="KBF26"', page)
        self.assertIn("at checkout: 40 EGP", page)
        self.assertIn("Sale rhythm", page)
        self.assertIn("📍 Maadi", page)

    def test_stats_and_other_stores(self):
        page = render(h=history([90, 70, 70, 60, 80]), matches=[item(id=8, store="amazon_eg", name="Lipton 100", price=45)])
        self.assertIn("Lowest in 90 days", page)
        stats = re.search(r'<div class="stats">(.*?)</div><details', page, re.S).group(1)
        self.assertIn("<b>60 <small>EGP</small></b>", stats)
        self.assertIn("<b>90 <small>EGP</small></b>", stats)
        self.assertIn("<b>3</b>", stats)  # 90→70→60→80: three changes (70→70 isn't one)
        self.assertIn("Same product in other stores", page)
        self.assertLess(page.index("Amazon.eg"), page.index('class=me'))  # cheapest first
        self.assertIn('<span class="num">−5</span>', page)

    def test_not_in_the_catalogue_still_renders(self):
        page = pages.product_page(product(), {}, [], [], [], "https://x", NOW).decode()
        self.assertIn("<svg class=\"chart\"", page)
        self.assertIn("Open in talabat ↗", page)


class Chart(unittest.TestCase):
    def check(self, svg):
        self.assertIn('<svg class="chart"', svg)
        self.assertNotRegex(svg, r"nan|inf")
        for v in re.findall(r"(?:top|left):(-?[\d.]+)%", svg):
            self.assertTrue(-0.01 <= float(v) <= 100.01, v)

    def test_shapes(self):
        self.assertEqual(pages.chart([], NOW), "")
        self.check(pages.chart([], NOW, current=50))                                   # nothing stored yet
        self.check(pages.chart(history([50], start=NOW), NOW))                          # one point, right now
        self.check(pages.chart(history([50]), NOW, usual=50))                           # one point, flat
        self.check(pages.chart(history([50, 40]), NOW, usual=45))                       # two points
        self.check(pages.chart(history([7] * 30, step=DAY), NOW))                       # flat for a month
        self.check(pages.chart(history([0, 0]), NOW))                                   # zero prices
        many = history([100 + (i % 7) * 3.5 for i in range(400)], start=NOW - 89 * DAY, step=DAY // 5)
        self.check(pages.chart(many, NOW, usual=110))

    def test_window_markers_and_reference(self):
        # 300 was charged before the window opened; 100 was the price when it opened (carried in, so it shows)
        h = history([300, 100], start=NOW - 100 * DAY) + history([90, 60, 90], start=NOW - 30 * DAY)
        svg = pages.chart(h, NOW, usual=75, days=40)
        self.assertNotIn(">300<", svg)
        self.assertIn('class="mt hi', svg)
        self.assertIn(">100<", svg)
        self.assertIn('class="mt lo', svg)
        self.assertIn(">60<", svg)
        self.assertIn('class="u"', svg)  # the usual price as a dashed line
        self.assertIn("usually 75", svg)
        self.assertIn(">now<", svg)

    def test_out_of_stock_is_shaded(self):
        h = history([50, 60, 55])
        h[1]["in_stock"] = 0
        svg = pages.chart(h, NOW)
        self.assertIn('class="oos"', svg)
        self.assertIn("out of stock", svg)


class Lists(unittest.TestCase):
    def test_coupons_hide_expired_and_flag_the_last_days(self):
        today = datetime.now(pages.CAIRO).date()
        c = [{"store": "talabat", "code": "LIVE", "title": "x", "expires": str(today + timedelta(days=2))},
             {"store": "jumia", "code": "GONE1", "expires": str(today - timedelta(days=1))},
             {"store": "jumia", "code": "TODAY", "expires": str(today)},
             {"store": "kenzz", "code": "AUTO", "auto": True, "title": "Extra off on 3 products, up to 5 EGP",
              "url": "https://deals.example/?x"},
             {"store": "jumia", "code": HOSTILE, "title": HOSTILE}]
        page = pages.coupons_page(c).decode()
        self.assertIn('data-copy="LIVE"', page)
        self.assertNotIn("GONE1", page)
        self.assertIn("2 days left", page)
        self.assertIn("last day", page)
        self.assertIn('data-go="https://deals.example/?x"', page)
        self.assertIn('href="https://deals.example/?x"', page)  # the store link works without JavaScript
        self.assertLess(page.index("LIVE"), page.index("AUTO"))  # the owner's coupons before found ones
        self.assertNotIn("<script>alert", page)
        self.assertIn("No coupons yet.", pages.coupons_page([]).decode())

    def test_categories_are_light_and_linked(self):
        cats = [(f"Category {i} & co", 1000 - i, 40.0 if i % 2 else 0) for i in range(300)] + [("ألبان", 3, 10.0)]
        page = pages.cats_page(cats)
        self.assertLess(len(page), 60_000)  # 300 categories; tiles used to cost ~250 bytes each
        html_ = page.decode()
        self.assertIn('href="/?deals=1&amp;category=Category%200%20%26%20co"', html_)
        self.assertIn('href="/?deals=1&amp;category=ألبان"', html_)
        self.assertIn("<i>−40%</i>", html_)
        self.assertIn('id="cf"', html_)  # the filter, a search form without JavaScript
        self.assertNotIn('id="cf"', pages.cats_page(cats[:3]).decode())
        self.assertIn('class="empty"', pages.cats_page([]).decode())
        self.assertNotIn("<script>alert", pages.cats_page([(HOSTILE, 1, 0)]).decode())

    def test_stores(self):
        rows = [{"store": "talabat", "label": "talabat mart", "products": 1000, "live": True, "paused_until": None,
                 "last_change": NOW - 60},
                {"store": "jumia", "label": "Jumia", "products": 0, "live": False}]
        page = pages.stores_page(rows, {}, {"talabat": 300}, {"talabat": 45.4}, now=NOW).decode()
        self.assertIn("deals · 1,000 products", page)
        self.assertIn("up to 45% off", page)
        self.assertIn('class="state live">live<', page)
        self.assertIn('class="state not">not running<', page)
        self.assertIn('href="/store/talabat"', page)


class EveryPage(unittest.TestCase):
    def all_pages(self):
        return {
            "product": render(),
            "stores": pages.stores_page([], {}, {}, {}).decode(),
            "cats": pages.cats_page([("Tea", 3, 10.0)]).decode(),
            "coupons": pages.coupons_page([]).decode(),
            "about": pages.about_page(["طلبات مارت"]).decode(),
            "privacy": pages.privacy_page().decode(),
            "contact": pages.contact_page("hi@example.com").decode(),
            "404": pages.layout("Not found", '<div class="empty">This product isn\'t tracked (any more).</div>').decode(),
        }

    def test_nav_footer_and_disclosure(self):
        for name, page in self.all_pages().items():
            with self.subTest(name):
                for href, label in pages.NAV:
                    self.assertIn(f'href="{pages.e(href)}"', page)
                    self.assertIn(f">{label}</a>", page)
                self.assertIn("As an Amazon Associate we earn from qualifying purchases.", page)
                self.assertIn('href="/about"', page)
                self.assertIn('href="/privacy"', page)
                self.assertIn('href="/contact"', page)
                self.assertIn('id="langBtn"', page)
                self.assertIn('class="pulse"', page)
                self.assertIn('<script src="/i18n.js"></script>', page)
                self.assertIn('<form class="search" action="/"', page)  # search works without JavaScript
                self.assertTrue(page.startswith('<!doctype html>\n<html lang="ar" dir="rtl">'))
                self.assertNotIn("http://", page.replace("http://www.w3.org", ""))

    def test_active_tab_and_not_found(self):
        p = self.all_pages()
        self.assertIn('<a href="/stores" class=on aria-current=page>Stores</a>', p["stores"])
        self.assertIn('href="/?deals=1&amp;sort=newest&amp;status=new">Just dropped</a>', p["404"])
        self.assertIn('class="next"', p["404"])
        self.assertNotIn('class="next"', p["about"])

    def test_pages_without_javascript_need_none(self):
        p = self.all_pages()
        for name in ("about", "privacy", "contact", "stores", "404"):
            self.assertEqual(p[name].count("<script"), 1, name)  # only i18n.js

    def test_sizes(self):
        for name, page in self.all_pages().items():
            self.assertLess(len(page.encode()), 26_000, name)


if __name__ == "__main__":
    unittest.main()
