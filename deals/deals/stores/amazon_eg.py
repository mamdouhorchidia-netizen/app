"""Amazon.eg through Amazon's Product Advertising API (PA-API 5), the approved way to read its prices.

Amazon.eg's website answers automated requests with a bot check, so this module does not touch the
website at all. To use it you need an Amazon Associates account for amazon.eg with PA-API access, and
three values in the environment:

  AMAZON_ACCESS_KEY, AMAZON_SECRET_KEY   from the Associates Central "Product Advertising API" page
  AMAZON_PARTNER_TAG                     your associate tag (e.g. yourname-21)

Product links carry your tag, so purchases through the deals page also earn you commission.
Limits: new accounts get about 1 request per second and 8,640 per day, rising with sales; 10 items
per request and 10 pages per search.
"""

import json
import os
from datetime import datetime, timezone
from typing import Iterator, Optional

from .. import sigv4
from ..http import Fetcher
from ..models import Offer

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


class NotConfigured(Exception):
    pass


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


def collect(fetcher: Fetcher, searches: list[tuple[str, str]] = None, pages: int = 3, min_saving: int = 5,
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
