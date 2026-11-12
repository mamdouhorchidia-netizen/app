"""Kenzz (kenzz.com), from its public pages.

Each page carries its products as JSON (Next.js __NEXT_DATA__): name, brand, price, market ("was") price,
stock, picture, tags such as "عرض البلاك فرايداي", and often a promo code with the price it gives
("MOB13" → 9,262 EGP), which the coupons page picks up. A category page shows its top 20 products and
loads the rest by script, so this reads every category page in Kenzz's own sitemap (about 2,000) plus the
home page's deal sections, one request every 3 s. robots.txt allows everything.
"""

import json
import re
from typing import Iterator, Optional
from urllib.parse import unquote

from ..http import Fetcher
from ..models import Offer
from ..tasks import Task, run_once

STORE = "kenzz"
SITE = "https://kenzz.com"
_NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _num(v) -> Optional[float]:
    try:
        n = float(v)
        return round(n, 2) if n > 0 else None
    except (TypeError, ValueError):
        return None


def _products(o, out: list):
    """Every product object anywhere in the page's data (home sections, category lists)."""
    if isinstance(o, dict):
        if "individualPrice" in o and o.get("id") and o.get("name"):
            out.append(o)
            return
        for v in o.values():
            _products(v, out)
    elif isinstance(o, list):
        for v in o:
            _products(v, out)


def offer(p: dict, category: str = "") -> Optional[Offer]:
    price = _num(p.get("specialPrice")) or _num(p.get("individualPrice"))
    if not price:
        return None
    market = _num(p.get("marketPrice")) if p.get("marketPriceVisibility", True) else None
    promo = p.get("promotion") or {}
    promo_price = _num(promo.get("price"))
    tags = [t.get("tagName") for t in p.get("tags") or [] if t.get("tagName")]
    return Offer(
        store=STORE,
        sku=str(p["id"]),
        name=str(p["name"]).strip(),
        price=price,
        original_price=market if market and market > price else None,
        url=f"{SITE}/{p['relativeUrl']}" if p.get("relativeUrl") else f"{SITE}/product/{p['id']}",
        image=p.get("imageUrl") or "",
        category=category,
        brand=(p.get("brand") or "").strip(),
        in_stock=bool(p.get("hasStock", True)),
        extra={"deal_label": tags[0] if tags else None,
               "promo_code": promo.get("code") if promo_price and promo_price < price else None,
               "promo_price": promo_price if promo_price and promo_price < price else None},
    )


def parse_page(html: str, category: str = "") -> list[Offer]:
    m = _NEXT.search(html)
    if not m:
        return []
    found = []
    _products(json.loads(m.group(1)).get("props", {}).get("pageProps", {}), found)
    seen, out = set(), []
    for p in found:
        o = offer(p, category)
        if o and o.sku not in seen:
            seen.add(o.sku)
            out.append(o)
    return out


def category_name(url: str) -> str:
    """The readable trail from a category address: …/category/<ids>/كل-حاجة/ألعاب → ألعاب."""
    parts = [unquote(x).replace("-", " ") for x in url.split("/category/", 1)[-1].split("/")[1:] if x]
    parts = [x for x in parts if x != "كل حاجة"]
    return " / ".join(parts[:2])


def _page_task(fetcher: Fetcher, url: str) -> Task:
    def run():
        status, html = fetcher.get(url)
        if status != 200:
            return [], []
        return parse_page(html, category_name(url) if "/category/" in url else "Kenzz deals"), []

    return Task(url, run)


def plan(fetcher: Fetcher, log=print) -> list[Task]:
    fetcher.delay = max(fetcher.delay, 3.0)
    tasks = [_page_task(fetcher, SITE + "/")]
    status, xml = fetcher.get(SITE + "/sitemap-categories.xml")
    urls = re.findall(r"<loc>(https://kenzz\.com/category/[^<]+)</loc>", xml) if status == 200 else []
    # parents before their children: the broad pages hold each department's best sellers
    urls.sort(key=lambda u: (u.split("/category/", 1)[-1].split("/")[0].count("-"), u))
    log(f"kenzz: home page and {len(urls)} category pages")
    return tasks + [_page_task(fetcher, u) for u in urls]


def collect(fetcher: Fetcher, max_pages: int = None, log=print) -> Iterator[Offer]:
    yield from run_once(plan(fetcher, log), max_pages)
