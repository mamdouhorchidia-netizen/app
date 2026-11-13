"""Raya Shop (rayashop.com), electronics and appliances, from its public category pages rendered in Chrome.

Raya builds its product lists with JavaScript, so each category page is rendered in headless Chrome (as for
Amazon's Today's Deals) and read from the page: name, price, struck-through price, picture. Its robots.txt
allows category pages and closes their page-2+ variants (?p=), search, cart and accounts, so this reads the
first page (about 20 products) of every category in Raya's own sitemap, deal lists first. One page every
10 s at least (`RAYA_DELAY`); a sweep of ~800 categories takes a few hours, busy pages come back sooner.
"""

import html
import re
from typing import Iterator, Optional

from .. import browser
from ..http import Fetcher
from ..models import Offer
from ..tasks import Task, run_once

STORE = "raya"
SITE = "https://www.rayashop.com"
# the deal lists and departments from Raya's own menu, before the sitemap's categories
LISTS = ["black-friday", "best-seller", "available-now", "free-shipping", "mobiles-and-tablets",
         "televisions-and-receivers", "large-appliances", "small-appliances", "kitchen-appliances", "laptops-pcs",
         "health-and-beauty", "electronics", "home"]
_CARD = re.compile(r'<article[^>]*class="ProductCard[A-Za-z]*\b.*?</article>', re.S)
_HCARD = re.compile(r'<a href="(/ar/[^"]+)"[^>]*class="HorizontalProductCard\b.*?</a>', re.S)
_PRICE = re.compile(r'<span[^>]*class="([^"]*\bcurrency\b[^"]*)"[^>]*>\s*([\d,]+(?:\.\d+)?)\s*<span>ج م</span>')


def _num(text: str) -> Optional[float]:
    try:
        return round(float(text.replace(",", "")), 2)
    except ValueError:
        return None


def _card(block: str, href: str, category: str) -> Optional[Offer]:
    name = re.search(r'class="[A-Za-z]*(?:__Name|__Title)\b[^"]*"[^>]*>([^<]+)<', block) \
        or re.search(r'<img[^>]*alt="([^"]{4,})"', block)
    prices = [(cls, _num(n)) for cls, n in _PRICE.findall(block)]
    now = next((p for cls, p in prices if "line-through" not in cls and p), None)
    was = next((p for cls, p in prices if "line-through" in cls and p), None)
    if not name or not now:
        return None
    img = re.search(r'<img[^>]*src="(https://[^"]+)"', block)
    slug = href.rstrip("/").rsplit("/", 1)[-1]
    return Offer(
        store=STORE,
        sku=slug,
        name=html.unescape(name.group(1)).strip(),
        price=now,
        original_price=was if was and was > now else None,
        url=SITE + href,
        image=html.unescape(img.group(1)) if img else "",
        category=category,
        in_stock="غير متوفر" not in block and "نفذت الكمية" not in block,
    )


def parse_page(page: str, category: str = "") -> list[Offer]:
    out, seen = [], set()
    for block in _CARD.findall(page):
        href = re.search(r'href="(/ar/[^"/]+)"', block)
        o = _card(block, href.group(1), category) if href else None
        if o and o.sku not in seen:
            seen.add(o.sku)
            out.append(o)
    for href, block in ((m.group(1), m.group(0)) for m in _HCARD.finditer(page)):
        o = _card(block, href, category)
        if o and o.sku not in seen:
            seen.add(o.sku)
            out.append(o)
    return out


def category_name(path: str) -> str:
    """'screen-protectors/mobiles-and-tablets/mobile-accessories' → 'Mobiles And Tablets / Screen Protectors'."""
    parts = [p.replace("-", " ").title() for p in path.strip("/").split("/")]
    return " / ".join(([parts[1]] if len(parts) > 1 else []) + [parts[0]])


def _page_task(path: str, render) -> Task:
    def run():
        page = render(f"{SITE}/ar/{path}")
        return parse_page(page, category_name(path)), []

    return Task(path, run)


def plan(fetcher: Fetcher, log=print, render=None) -> list[Task]:
    fetcher.delay = max(fetcher.delay, 10.0)
    render = render or (lambda url: (fetcher._wait(), browser.render(url, budget_ms=10000, timeout=120))[1])
    status, xml = fetcher.get(SITE + "/ar-sitemap.xml")
    paths = re.findall(r"<loc>https://www\.rayashop\.com/ar/([a-z0-9-]+(?:/[a-z0-9-]+)+)</loc>", xml) \
        if status == 200 else []
    paths = [p for p in dict.fromkeys(paths) if not p.startswith("brands/")]
    log(f"raya: {len(LISTS)} deal lists and departments, {len(paths)} categories")
    return [_page_task(p, render) for p in LISTS + [p for p in paths if p not in LISTS]]


def collect(fetcher: Fetcher, max_pages: int = None, log=print) -> Iterator[Offer]:
    yield from run_once(plan(fetcher, log), max_pages)
