"""Talabat Mart (talabat's own grocery store) from the public talabat.com pages.

Every grocery page on talabat.com is rendered on the server and carries its data as JSON in a
<script id="__NEXT_DATA__"> tag, so no hidden API is needed:

  /egypt/talabat-mart/categories?aid=AREA          the branch serving that area, and its category tree
  /egypt/grocery/BRANCH/talabat-mart/CAT/SUB?aid=AREA&page=N   20 products per page, with
                                                                price, originalPrice and stockAmount

The site's robots.txt allows all of it. Prices are per branch, and the area picks the branch.
"""

import json
import re
from typing import Iterator, Optional
from urllib.parse import quote

from ..http import Fetcher
from ..models import Offer

BASE = "https://www.talabat.com"
STORE = "talabat"
DEFAULT_AREA = 9838  # Maadi; any talabat area id works

_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


class PageError(Exception):
    pass


def next_data(html: str) -> dict:
    m = _NEXT_DATA.search(html)
    if not m:
        raise PageError("no __NEXT_DATA__ on the page (the site layout may have changed)")
    return json.loads(m.group(1))


def parse_categories(html: str) -> tuple[dict, list[dict]]:
    """Returns (store, subcategories). Each subcategory carries its parent's name and slug."""
    state = next_data(html)["props"]["pageProps"].get("initialState") or {}
    store, cats = state.get("groceryStore"), state.get("categories")
    if not store or cats is None:
        raise PageError("categories page has no groceryStore/categories")
    subs = []
    for c in cats:
        for s in c.get("subCategories") or []:
            subs.append({"parent": c["name"], "parent_slug": c["slug"], "name": s["name"], "slug": s["slug"],
                         "count": s.get("count", 0)})
    return store, subs


def parse_items(html: str, branch_id, area_id, category: str = "") -> tuple[list[Offer], int]:
    """Returns (offers, page_count) for one products page."""
    state = next_data(html)["props"]["pageProps"].get("initialState") or {}
    data = state.get("itemsData")
    if data is None:
        raise PageError("products page has no itemsData")
    base = f"{BASE}/egypt/grocery/{branch_id}/talabat-mart"
    offers = []
    for it in data.get("items") or []:
        price = _num(it.get("price"))
        if price is None or not it.get("sku"):
            continue
        original = _num(it.get("originalPrice"))
        offers.append(Offer(
            store=STORE,
            sku=it["sku"],
            name=(it.get("title") or "").strip(),
            price=price,
            original_price=original if original and original > price else None,
            url=f"{base}/product/{quote(it.get('slug') or '')}/s/{quote(it['sku'])}?aid={area_id}",
            image=it.get("image") or "",
            category=category,
            in_stock=(it.get("stockAmount") or 0) > 0,
            extra={"stock": it.get("stockAmount")},
        ))
    return offers, int(data.get("pageCount") or 1)


def _num(v) -> Optional[float]:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def collect(fetcher: Fetcher, area_id: int = DEFAULT_AREA, max_pages: int = None,
            categories: list[str] = None, log=print) -> Iterator[Offer]:
    """Walks every subcategory of Talabat Mart for one area. `max_pages` caps the total page count
    (for trying it out); `categories` keeps only the top-level categories with those names."""
    status, html = fetcher.get(f"{BASE}/egypt/talabat-mart/categories?aid={area_id}")
    if status != 200:
        raise PageError(f"categories page answered {status}")
    store, subs = parse_categories(html)
    branch = store["branchId"]
    if categories:
        wanted = {c.lower() for c in categories}
        subs = [s for s in subs if s["parent"].lower() in wanted]
    log(f"talabat: branch {branch} ({store.get('branchName')}), {len(subs)} subcategories,"
        f" ~{sum(s['count'] for s in subs)} products")

    pages, seen = 0, set()
    for s in subs:
        page, page_count = 1, 1
        while page <= page_count:
            if max_pages is not None and pages >= max_pages:
                return
            url = (f"{BASE}/egypt/grocery/{branch}/talabat-mart/{s['parent_slug']}/{s['slug']}"
                   f"?aid={area_id}&page={page}")
            status, html = fetcher.get(url)
            pages += 1
            if status != 200:
                log(f"talabat: {status} for {s['parent']} / {s['name']} page {page}, skipped")
                break
            offers, page_count = parse_items(html, branch, area_id, f"{s['parent']} / {s['name']}")
            for o in offers:  # a product can sit in several subcategories; keep its first one
                if o.sku not in seen:
                    seen.add(o.sku)
                    yield o
            page += 1
