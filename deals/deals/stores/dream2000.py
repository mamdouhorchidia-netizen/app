"""Dream 2000 (dream2000.com), electronics and home appliances, from its public Shopify catalogue.

Shopify stores publish their catalogue at /products.json (250 products a page): name, type, brand, every
variant's price and its compare-at ("was") price, stock and pictures. robots.txt allows it (it closes only
/admin, /cart, /checkout and accounts). One request every 3 s; a sweep is about 20 pages.
"""

import json
from typing import Iterator, Optional

from ..http import Fetcher
from ..models import Offer
from ..tasks import Task, run_once

STORE = "dream2000"
SITE = "https://dream2000.com"
MAX_PAGES = 200


def _num(v) -> Optional[float]:
    try:
        return round(float(v), 2) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_products(data: dict, store: str = STORE, site: str = SITE) -> list[Offer]:
    offers = []
    for p in data.get("products") or []:
        variants = [v for v in p.get("variants") or [] if _num(v.get("price"))]
        if not variants or not p.get("handle"):
            continue
        live = [v for v in variants if v.get("available")] or variants
        v = min(live, key=lambda x: _num(x["price"]))  # the cheapest option you can buy
        price, was = _num(v["price"]), _num(v.get("compare_at_price"))
        images = p.get("images") or []
        offers.append(Offer(
            store=store,
            sku=str(p["id"]),
            name=(p.get("title") or "").strip(),
            price=price,
            original_price=was if was and was > price else None,
            url=f"{site}/products/{p['handle']}",
            image=(images[0].get("src") or "") if images else "",
            category=(p.get("product_type") or "").strip() or "Appliances & electronics",
            brand=(p.get("vendor") or "").strip(),
            in_stock=any(x.get("available") for x in variants),
        ))
    return offers


def _page_task(fetcher: Fetcher, page: int, store: str, site: str) -> Task:
    def run():
        status, body = fetcher.get(f"{site}/products.json?limit=250&page={page}")
        if status != 200:
            return [], []
        offers = parse_products(json.loads(body), store, site)
        more = len(offers) > 0 and page < MAX_PAGES
        return offers, [_page_task(fetcher, page + 1, store, site)] if more else []

    return Task(f"{store}/products/{page}", run)


def plan(fetcher: Fetcher, log=print, store: str = STORE, site: str = SITE) -> list[Task]:
    fetcher.delay = max(fetcher.delay, 3.0)
    return [_page_task(fetcher, 1, store, site)]


def collect(fetcher: Fetcher, max_pages: int = None, log=print) -> Iterator[Offer]:
    yield from run_once(plan(fetcher, log), max_pages)
