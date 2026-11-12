"""Jumia Egypt, from its public pages.

Jumia's robots.txt allows bots that say clearly who they are (with an owner address) and stay under 200
requests a minute. This reads its Flash Sales and main category pages, at one request every 3 s. Each page
carries its products as JSON (window.__STORE__): name, brand, category path, price, old price, discount,
stock and picture. robots.txt rules followed: no /en/ copies, no sort/filter variants, no MLP sub-pages.

Jumia sits behind Cloudflare. If it answers with its "Just a moment…" check, the run stops (http.Blocked)
and the store pauses; nothing tries to get past the check.
"""

import json
import re
from typing import Iterator, Optional

from ..http import Blocked, Fetcher
from ..models import Offer
from ..tasks import Task, run_once

STORE = "jumia"
SITE = "https://www.jumia.com.eg"
# (path, label): deals first, then the departments people shop for everyday prices
LISTS = [
    ("/flash-sales/", "Flash Sales"),
    ("/groceries/", "Groceries"),
    ("/health-beauty/", "Health & Beauty"),
    ("/baby-products/", "Baby"),
    ("/home-office/", "Home & Office"),
    ("/phones-tablets/", "Phones & Tablets"),
    ("/electronics/", "Electronics"),
    ("/computing/", "Computing"),
    ("/appliances/", "Appliances"),
    ("/category-fashion-by-jumia/", "Fashion"),
    ("/sporting-goods/", "Sports"),
    ("/video-games/", "Video Games"),
]
MAX_PAGES = 50
_STORE_JSON = re.compile(r"window\.__STORE__\s*=\s*(\{.*?\});\s*</script>", re.S)


def _money(text) -> Optional[float]:
    m = re.search(r"([\d,]+(?:\.\d+)?)", str(text or ""))
    return round(float(m.group(1).replace(",", "")), 2) if m else None


def is_challenge(status: int, page: str) -> bool:
    return status in (403, 429, 503) and ("Just a moment" in page or "cf-chl" in page or "challenge" in page.lower())


def parse_page(page: str, list_label: str = "") -> list[Offer]:
    m = _STORE_JSON.search(page)
    if not m:
        return []
    data = json.loads(m.group(1))
    offers = []
    for p in data.get("products") or []:
        prices = p.get("prices") or {}
        price = _money(prices.get("rawPrice")) or _money(prices.get("price"))
        if not price or not p.get("sku") or not p.get("url"):
            continue
        old = _money(prices.get("oldPrice"))
        cats = [c for c in p.get("categories") or [] if c]
        stock_text = (p.get("stock") or {}).get("text") or ""
        offers.append(Offer(
            store=STORE,
            sku=p["sku"],
            name=(p.get("displayName") or p.get("name") or "").strip(),
            price=price,
            original_price=old if old and old > price else None,
            url=SITE + p["url"],
            image=p.get("image") or "",
            category=" / ".join(cats[:3]) or list_label,
            brand=(p.get("brand") or "").strip(),
            in_stock=bool(p.get("isBuyable", True)),
            extra={"deal_label": "Flash sale" if list_label == "Flash Sales" else None,
                   "stock_text": stock_text or None},
        ))
    return offers


def _list_task(fetcher: Fetcher, path: str, label: str, page: int, seen_first: tuple = ()) -> Task:
    def run():
        url = SITE + path + (f"?page={page}" if page > 1 else "")
        status, html = fetcher.get(url)
        if is_challenge(status, html):
            raise Blocked(f"Jumia answered {path} with a Cloudflare check")
        if status != 200:
            return [], []
        offers = parse_page(html, label)
        skus = tuple(o.sku for o in offers[:3])
        # next page while this one had products and isn't a repeat of the last (Jumia repeats the last page)
        more = offers and skus != seen_first and page < MAX_PAGES
        return offers, [_list_task(fetcher, path, label, page + 1, skus)] if more else []

    return Task(f"{path}#{page}", run)


def plan(fetcher: Fetcher, log=print) -> list[Task]:
    fetcher.delay = max(fetcher.delay, 3.0)
    fetcher.retry_statuses = {429, 500, 502, 504}  # 403/503 can be Cloudflare's check: never retried
    return [_list_task(fetcher, path, label, 1) for path, label in LISTS]


def collect(fetcher: Fetcher, max_pages: int = None, log=print) -> Iterator[Offer]:
    yield from run_once(plan(fetcher, log), max_pages)
