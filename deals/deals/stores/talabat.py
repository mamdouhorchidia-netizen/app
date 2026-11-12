"""Talabat Mart (talabat's own grocery store) from the public talabat.com pages.

Every grocery page on talabat.com is rendered on the server and carries its data as JSON in a
<script id="__NEXT_DATA__"> tag, so no hidden API is needed:

  /egypt/talabat-mart/categories?aid=AREA          the branch serving that area, and its category tree
  /egypt/grocery/BRANCH/talabat-mart/CAT/SUB?aid=AREA&page=N   20 products per page, with
                                                                price, originalPrice and stockAmount

The site's robots.txt allows all of it. Prices are per branch, and the area picks the branch.
"""

import json
import os
import re
from typing import Iterator, Optional
from urllib.parse import quote

from ..http import Fetcher
from ..models import Offer
from ..tasks import Task, run_once

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


def hub_name(raw: str) -> str:
    """'Tmart, El Obour Industrial Area BG' / 'talabat mart, Helwan' -> 'El Obour Industrial Area' / 'Helwan'."""
    name = re.sub(r"^\s*(talabat mart|tmart|طلبات مارت)\s*,?\s*", "", raw or "", flags=re.I).strip()
    return re.sub(r"\s+BG$", "", name).strip() or (raw or "").strip()


def hub_from_slug(slug: str) -> str:
    """'tmart-el-obour-industrial-area-bg' -> 'El Obour Industrial Area'."""
    words = re.sub(r"^(tmart|talabat-mart)-", "", slug or "").removesuffix("-bg").split("-")
    return " ".join(w if w.isupper() else w.capitalize() for w in words if w) or slug


def parse_items(html: str, branch_id, area_id, category: str = "", hub: str = None) -> tuple[list[Offer], int]:
    """Returns (offers, page_count) for one products page. Each hub is its own product (sku "<hub>:<sku>"),
    because hubs stock different things."""
    state = next_data(html)["props"]["pageProps"].get("initialState") or {}
    data = state.get("itemsData")
    if data is None:
        raise PageError("products page has no itemsData")
    hub = hub or hub_name((state.get("groceryStore") or {}).get("branchName") or str(branch_id))
    base = f"{BASE}/egypt/grocery/{branch_id}/talabat-mart"
    offers = []
    for it in data.get("items") or []:
        price = _num(it.get("price"))
        if price is None or not it.get("sku"):
            continue
        original = _num(it.get("originalPrice"))
        offers.append(Offer(
            store=STORE,
            sku=f"{branch_id}:{it['sku']}",
            name=(it.get("title") or "").strip(),
            price=price,
            original_price=original if original and original > price else None,
            url=f"{base}/product/{quote(it.get('slug') or '')}/s/{quote(it['sku'])}?aid={area_id}",
            image=it.get("image") or "",
            category=category,
            in_stock=(it.get("stockAmount") or 0) > 0,
            extra={"stock": it.get("stockAmount"), "hub": int(branch_id), "hub_name": hub},
        ))
    return offers, int(data.get("pageCount") or 1)


def _num(v) -> Optional[float]:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def plan(fetcher: Fetcher, hubs: list[dict] = None, categories: list[str] = None, log=print) -> list[Task]:
    """For each hub, one task per subcategory (its page 1, which adds pages 2..n when it runs).
    hubs: [{"id": branch id, "aid": an area it serves, "name": ...}]; default Maadi Old. The category tree is
    the same for every hub; a shelf a hub doesn't carry is skipped."""
    hubs = hubs or [{"id": 619848, "aid": DEFAULT_AREA, "name": "Maadi Old"}]
    status, html = fetcher.get(f"{BASE}/egypt/talabat-mart/categories?aid={hubs[0]['aid']}")
    if status != 200:
        raise PageError(f"categories page answered {status}")
    _, subs = parse_categories(html)
    if categories:
        wanted = {c.lower() for c in categories}
        subs = [s for s in subs if s["parent"].lower() in wanted]
    log(f"talabat: {len(hubs)} hub(s) ({', '.join(h['name'] for h in hubs)}), {len(subs)} subcategories each")
    return [_page_task(fetcher, h, s, 1, log) for h in hubs for s in subs]


def _page_task(fetcher: Fetcher, hub: dict, sub: dict, page: int, log) -> Task:
    branch, area_id = hub["id"], hub["aid"]

    def run():
        url = (f"{BASE}/egypt/grocery/{branch}/talabat-mart/{sub['parent_slug']}/{sub['slug']}"
               f"?aid={area_id}&page={page}")
        status, html = fetcher.get(url)
        if status != 200:
            if status != 404:
                log(f"talabat: {status} for {hub['name']}: {sub['parent']} / {sub['name']} page {page}, skipped")
            return [], []
        name = hub.get("name")
        offers, page_count = parse_items(html, branch, area_id, f"{sub['parent']} / {sub['name']}",
                                         None if not name or str(name).isdigit() else name)  # a bare number: use the page's
        follow = [_page_task(fetcher, hub, sub, n, log) for n in range(2, page_count + 1)] if page == 1 else []
        return offers, follow

    return Task(f"{branch}/{sub['parent_slug']}/{sub['slug']}/{page}", run)


def collect(fetcher: Fetcher, hubs: list[dict] = None, max_pages: int = None,
            categories: list[str] = None, log=print) -> Iterator[Offer]:
    """Every subcategory of Talabat Mart in each hub, once. `max_pages` caps the requests after the
    categories page (for trying it out); `categories` keeps only those top-level categories."""
    yield from run_once(plan(fetcher, hubs, categories, log), max_pages)


# ---- which hub serves which area ---------------------------------------------------------------------

AREAS_URL = f"{BASE}/egypt/all-areas/groceries"
FIRST_CITIES = ("cairo", "giza", "october", "obour", "zayed", "shorouk", "madinaty", "ramadan")
TALABAT_MART_ID = 607445


def parse_areas(html: str) -> list[dict]:
    """Every delivery area: [{"id", "name", "slug", "city"}], Cairo and Giza first."""
    areas = [{"id": a["id"], "name": a["name"], "slug": a["slug"], "city": a.get("cityName") or ""}
             for city in (next_data(html)["props"]["pageProps"].get("areas") or {}).values() for a in city]
    return sorted(areas, key=lambda a: (not any(c in (a["city"] + " " + a["name"]).lower() for c in FIRST_CITIES), a["city"]))


def parse_area_hub(html: str) -> Optional[dict]:
    """The talabat mart hub serving one area, from its groceries page: {"id", "slug"} or None."""
    for v in next_data(html)["props"]["pageProps"].get("vendors") or []:
        if v.get("id") == TALABAT_MART_ID or (v.get("name") or "").strip().lower() == "talabat mart":
            return {"id": int(v["branchId"]), "slug": v.get("branchSlug") or ""}
    return None


def discover_hubs(fetcher: Fetcher, path: str, log=print, stop=None):
    """Goes through every area once and writes {hub id: {"id", "slug", "name", "areas": [...]}} to `path`,
    saving as it goes (a restart carries on where it stopped)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    hubs, done = data.get("hubs") or {}, set(data.get("done") or [])
    status, html = fetcher.get(AREAS_URL)
    if status != 200:
        raise PageError(f"areas page answered {status}")
    areas = [a for a in parse_areas(html) if a["id"] not in done]
    log(f"talabat: looking up hubs for {len(areas)} areas")

    def save():
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"hubs": hubs, "done": sorted(done)}, f, ensure_ascii=False)
        os.replace(tmp, path)

    for i, a in enumerate(areas):
        if stop and stop():
            break
        status, page = fetcher.get(f"{BASE}/egypt/groceries/{a['id']}/{a['slug']}")
        if status == 200:
            hub = parse_area_hub(page)
            if hub:
                h = hubs.setdefault(str(hub["id"]), {"id": hub["id"], "slug": hub["slug"],
                                                     "name": hub_from_slug(hub["slug"]), "areas": []})
                h["areas"].append({"id": a["id"], "name": a["name"], "city": a["city"]})
            done.add(a["id"])
        if i % 25 == 0:
            save()
    save()
    log(f"talabat: {len(hubs)} hubs known")
