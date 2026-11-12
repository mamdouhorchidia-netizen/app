"""Amazon.eg, from two sources:

1. Best-seller lists (default, no account needed). amazon.eg/gp/bestsellers/<department>[/<node>]
   is plain HTML that Amazon serves to ordinary requests, and robots.txt allows it. Each list shows
   ranks 1-30 on page 1 and 51-80 on page 2 with name, price and picture. They show no struck-through
   price, so Amazon deals come from the price history ("usually X"), starting a few days after the
   first run. Search pages answer automated requests with a bot check; when any page does that, the
   run stops (http.Blocked) and does not try to get past it.

2. The Product Advertising API (PA-API 5), when these are set in the environment:
     AMAZON_ACCESS_KEY, AMAZON_SECRET_KEY   from Associates Central, "Product Advertising API"
     AMAZON_PARTNER_TAG                     your associate tag (e.g. yourname-21)
   It returns Amazon's own savings and links that earn commission. New accounts get about 1 request
   per second and 8,640 per day; 10 items per request and 10 pages per search.
"""

import html
import json
import os
import re
from datetime import datetime, timezone
from typing import Iterator, Optional

from .. import sigv4
from ..http import Blocked, Fetcher
from ..models import NotConfigured, Offer

STORE = "amazon_eg"
HOST = "webservices.amazon.eg"
REGION = "eu-west-1"
SERVICE = "ProductAdvertisingAPI"
MARKETPLACE = "www.amazon.eg"
RESOURCES = [
    "ItemInfo.Title",
    "ItemInfo.ByLineInfo",
    "ItemInfo.Classifications",
    "Images.Primary.Medium",
    "Offers.Listings.Price",
    "Offers.Listings.SavingBasis",
    "Offers.Listings.Availability.Type",
]

# What to search when no list is given: everyday goods, only items Amazon itself shows a saving on.
DEFAULT_SEARCHES = [
    ("Grocery", "coffee"), ("Grocery", "tea"), ("Grocery", "chocolate"), ("Grocery", "oil"),
    ("Grocery", "rice"), ("Grocery", "pasta"), ("Grocery", "snacks"), ("Grocery", "juice"),
    ("HealthPersonalCare", "shampoo"), ("HealthPersonalCare", "diapers"), ("Beauty", "skin care"),
    ("HomeAndKitchen", "detergent"),
]


class Client:
    def __init__(self, fetcher: Fetcher, access_key: str = None, secret_key: str = None, partner_tag: str = None):
        self.fetcher = fetcher
        self.access_key = access_key or os.environ.get("AMAZON_ACCESS_KEY")
        self.secret_key = secret_key or os.environ.get("AMAZON_SECRET_KEY")
        self.partner_tag = partner_tag or os.environ.get("AMAZON_PARTNER_TAG")
        if not (self.access_key and self.secret_key and self.partner_tag):
            raise NotConfigured("set AMAZON_ACCESS_KEY, AMAZON_SECRET_KEY and AMAZON_PARTNER_TAG (see amazon_eg.py)")

    def build_request(self, operation: str, payload: dict, now: datetime = None) -> tuple[str, dict, bytes]:
        """Returns (url, headers, body) for one signed PA-API call."""
        body = json.dumps({**payload, "PartnerTag": self.partner_tag, "PartnerType": "Associates",
                           "Marketplace": MARKETPLACE, "Resources": RESOURCES}).encode()
        amz_date = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
        path = f"/paapi5/{operation.lower()}"
        headers = {
            "host": HOST,
            "content-encoding": "amz-1.0",
            "content-type": "application/json; charset=utf-8",
            "x-amz-date": amz_date,
            "x-amz-target": f"com.amazon.paapi5.v1.ProductAdvertisingAPIv1.{operation}",
        }
        headers = sigv4.sign("POST", HOST, path, {}, headers, body, self.access_key, self.secret_key,
                             REGION, SERVICE, amz_date)
        return f"https://{HOST}{path}", headers, body

    def call(self, operation: str, payload: dict) -> dict:
        url, headers, body = self.build_request(operation, payload)
        headers.pop("host")  # urllib sets it
        status, text = self.fetcher.request(url, data=body, headers=headers, method="POST")
        data = json.loads(text) if text.strip().startswith("{") else {}
        if status != 200:
            errs = data.get("Errors") or [{"Code": status, "Message": text[:200]}]
            raise RuntimeError(f"PA-API {operation}: {errs[0].get('Code')}: {errs[0].get('Message')}")
        return data

    def search(self, keywords: str, index: str = "All", page: int = 1, min_saving: int = None) -> list[dict]:
        payload = {"Keywords": keywords, "SearchIndex": index, "ItemCount": 10, "ItemPage": page}
        if min_saving:
            payload["MinSavingPercent"] = min_saving
        return (self.call("SearchItems", payload).get("SearchResult") or {}).get("Items") or []


def parse_item(item: dict, category: str = "") -> Optional[Offer]:
    """One PA-API item -> Offer. Reads Offers and, if Amazon sends it, the newer OffersV2 shape."""
    listing = ((item.get("Offers") or {}).get("Listings") or [None])[0]
    price = original = None
    availability = ""
    if listing:
        price = (listing.get("Price") or {}).get("Amount")
        original = (listing.get("SavingBasis") or {}).get("Amount")
        availability = (listing.get("Availability") or {}).get("Type") or ""
    else:
        v2 = ((item.get("OffersV2") or {}).get("Listings") or [None])[0]
        if v2:
            p = v2.get("Price") or {}
            price = (p.get("Money") or {}).get("Amount")
            original = ((p.get("SavingBasis") or {}).get("Money") or {}).get("Amount")
            availability = (v2.get("Availability") or {}).get("Type") or ""
    if price is None:
        return None
    info = item.get("ItemInfo") or {}
    return Offer(
        store=STORE,
        sku=item["ASIN"],
        name=((info.get("Title") or {}).get("DisplayValue") or "").strip(),
        price=round(float(price), 2),
        original_price=round(float(original), 2) if original and float(original) > float(price) else None,
        url=item.get("DetailPageURL") or f"https://www.amazon.eg/dp/{item['ASIN']}",
        image=(((item.get("Images") or {}).get("Primary") or {}).get("Medium") or {}).get("URL") or "",
        category=category or ((info.get("Classifications") or {}).get("ProductGroup") or {}).get("DisplayValue", ""),
        brand=(((info.get("ByLineInfo") or {}).get("Brand") or {}).get("DisplayValue") or ""),
        in_stock=availability.upper() != "OUT_OF_STOCK",
    )


def collect_api(fetcher: Fetcher, searches: list[tuple[str, str]] = None, pages: int = 3, min_saving: int = 5,
                log=print) -> Iterator[Offer]:
    client = Client(fetcher)
    fetcher.delay = max(fetcher.delay, 1.1)  # stay under 1 request/second
    for index, keywords in searches or DEFAULT_SEARCHES:
        for page in range(1, pages + 1):
            try:
                items = client.search(keywords, index, page, min_saving)
            except RuntimeError as e:
                log(f"amazon_eg: {index}/{keywords} page {page}: {e}")
                break
            for it in items:
                o = parse_item(it, category=f"{index} / {keywords}")
                if o:
                    yield o
            if len(items) < 10:
                break


# ---- best-seller lists -------------------------------------------------------------------------

SITE = "https://www.amazon.eg"
DEFAULT_DEPARTMENTS = ["grocery", "health", "beauty", "baby-products", "home", "pet-supplies", "electronics"]
DEPARTMENT_NAMES = {
    "grocery": "Grocery", "health": "Health & Household", "beauty": "Beauty", "baby-products": "Baby",
    "home": "Home & Kitchen", "pet-supplies": "Pet Supplies", "electronics": "Electronics",
    "office-products": "Office", "toys": "Toys", "sporting-goods": "Sports", "fashion": "Fashion",
    "automotive": "Automotive", "home-improvement": "Tools", "videogames": "Video Games",
}
_ITEM_SPLIT = re.compile(r'<div id="gridItemRoot"')
_SELLERS_PRICE = re.compile(r'a-price-whole">([\d,]+)<.*?a-price-fraction">(\d+)<', re.S)
_PRICE = re.compile(r'p13n-sc-price[^"]*">\s*EGP\s*([\d,]+(?:\.\d+)?)\s*<')


def _is_bot_check(status: int, page: str) -> bool:
    return status == 503 or "captcha" in page[:20000].lower() or "api-services-support@amazon.com" in page


def parse_bestsellers(page: str, category: str = "") -> list[Offer]:
    offers = []
    for block in _ITEM_SPLIT.split(page)[1:]:
        block = block.split("</li>")[0]
        asin = re.search(r'data-asin="([A-Z0-9]{10})"', block)
        price, from_sellers = _PRICE.search(block), False
        if not price:  # "N offers from EGP x": the lowest price among marketplace sellers
            price, from_sellers = _SELLERS_PRICE.search(block), True
        if not asin or not price:  # no price: currently unavailable
            continue
        amount = price.group(1).replace(",", "") + ("." + price.group(2) if price.lastindex == 2 else "")
        title = re.search(r'line-clamp[^"]*">([^<]+)<', block) or re.search(r'<img alt="([^"]+)"', block)
        img = re.search(r'<img [^>]*src="([^"]+)"', block)
        rank = re.search(r'zg-bdg-text">#(\d+)<', block)
        offers.append(Offer(
            store=STORE,
            sku=asin.group(1),
            name=html.unescape(title.group(1)).strip() if title else asin.group(1),
            price=round(float(amount), 2),
            url=f"{SITE}/-/en/dp/{asin.group(1)}",
            image=html.unescape(img.group(1)) if img else "",
            category=category,
            extra={"rank": int(rank.group(1)) if rank else None, "from_sellers": from_sellers},
        ))
    return offers


def parse_subcategories(page: str, department: str) -> list[tuple[str, str]]:
    """The child lists linked from a department's page: [(node id, name)]."""
    found = re.findall(r'href="/-/en/gp/bestsellers/%s/(\d+)/ref=zg_bs_nav_%s_1"[^>]*>([^<]+)<'
                       % (re.escape(department), re.escape(department)), page)
    seen, out = set(), []
    for node, name in found:
        if node not in seen:
            seen.add(node)
            out.append((node, html.unescape(name).strip()))
    return out


def collect_bestsellers(fetcher: Fetcher, departments: list[str] = None, subcategories: bool = True,
                        max_pages: int = None, log=print) -> Iterator[Offer]:
    fetcher.delay = max(fetcher.delay, 3.0)  # Amazon is the strictest of the three: go slower
    fetcher.retry_statuses = {429, 500, 502, 504}  # 503 is Amazon's bot check: never retry it
    asked, seen = 0, set()

    def fetch(path: str, page_no: int) -> str:
        nonlocal asked
        status, page = fetcher.get(f"{SITE}{path}?ie=UTF8&pg={page_no}")
        asked += 1
        if _is_bot_check(status, page):
            raise Blocked(f"Amazon answered {path} with a bot check after {asked} pages; stopped")
        return page if status == 200 else ""

    for dept in departments or DEFAULT_DEPARTMENTS:
        dept_name = DEPARTMENT_NAMES.get(dept, dept)
        lists = [(f"/-/en/gp/bestsellers/{dept}", dept_name)]
        i = 0
        while i < len(lists):
            path, name = lists[i]
            for page_no in (1, 2):
                if max_pages is not None and asked >= max_pages:
                    return
                page = fetch(path, page_no)
                if not page:
                    break
                if i == 0 and page_no == 1 and subcategories:
                    lists += [(f"/-/en/gp/bestsellers/{dept}/{node}", f"{dept_name} / {sub}")
                              for node, sub in parse_subcategories(page, dept)]
                    log(f"amazon_eg: {dept_name}: {len(lists) - 1} sub-lists")
                for o in parse_bestsellers(page, name):
                    if o.sku not in seen:
                        seen.add(o.sku)
                        yield o
            i += 1


def collect(fetcher: Fetcher, pages: int = 3, departments: list[str] = None, max_pages: int = None,
            log=print) -> Iterator[Offer]:
    """The API when its keys are set, the best-seller lists otherwise."""
    if all(os.environ.get(k) for k in ("AMAZON_ACCESS_KEY", "AMAZON_SECRET_KEY", "AMAZON_PARTNER_TAG")):
        log("amazon_eg: using the Product Advertising API")
        yield from collect_api(fetcher, pages=pages, log=log)
    else:
        yield from collect_bestsellers(fetcher, departments, max_pages=max_pages, log=log)
