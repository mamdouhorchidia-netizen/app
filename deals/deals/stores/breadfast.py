"""Breadfast, from a capture of its app's own traffic.

Breadfast's prices exist only inside its phone app; there is no public page with them. So this module
starts from a HAR file: a recording of what the app loaded while you browsed it on your own phone
(HTTP Toolkit, Charles, Proxyman and mitmproxy all export HAR). Two steps:

1. python -m deals import-har capture.har
   Finds every response that lists products, saves those prices, and remembers the requests that
   produced them in breadfast_requests.json.
2. python -m deals collect --store breadfast   (or `run`, which does it on a schedule)
   Sends those same requests again to refresh the prices. Only GET requests are kept, and nothing
   whose address mentions a cart, order, checkout, payment, address, wallet or account.

The requests file holds your app session's headers (sign-in tokens included), so it stays out of git
and should be treated like a password. When the session expires the run stops with "capture again".

The app's JSON layout isn't public, so products are recognised by shape: any object with an id, a
name and a price. If prices come out 100x too big (piasters), set "price_divisor": 100 in the
requests file. Whether Breadfast's terms allow this is for you to judge.
"""

import base64
import json
import os
import re
from typing import Iterator, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..http import Fetcher
from ..models import NotConfigured, Offer

STORE = "breadfast"
DEFAULT_REQUESTS_FILE = "breadfast_requests.json"

ID_KEYS = ("id", "product_id", "productid", "sku", "item_id", "itemid", "barcode")
NAME_KEYS = ("name", "title", "name_en", "product_name", "productname", "display_name", "displayname")
PRICE_KEYS = ("final_price", "finalprice", "price_after_discount", "priceafterdiscount", "sale_price",
              "saleprice", "discounted_price", "discountedprice", "special_price", "specialprice",
              "selling_price", "sellingprice", "current_price", "currentprice", "price")
ORIGINAL_KEYS = ("original_price", "originalprice", "old_price", "oldprice", "price_before_discount",
                 "pricebeforediscount", "regular_price", "regularprice", "compare_at_price",
                 "compareatprice", "list_price", "listprice", "strike_price", "strikeprice", "was_price",
                 "base_price", "baseprice")
IMAGE_KEYS = ("image", "image_url", "imageurl", "thumbnail", "thumbnail_url", "img", "picture", "photo",
              "images", "image_urls")
CATEGORY_KEYS = ("category_name", "categoryname", "category")
SKIP_URL = re.compile(r"cart|basket|order|checkout|payment|address|wallet|account|profile|user|login|auth"
                      r"|token|otp|notification", re.I)
DROP_HEADERS = {"host", "content-length", "accept-encoding", "connection", "transfer-encoding", "te",
                "keep-alive", "upgrade", "proxy-connection", "if-none-match", "if-modified-since"}
PAGE_PARAMS = ("page", "page_number", "pagenumber", "pageno", "p")
MAX_PAGES = 50


# ---- recognising products in unknown JSON ----------------------------------------------------------

def _lower(d: dict) -> dict:
    return {str(k).lower(): v for k, v in d.items()}


def _first(d: dict, keys) -> object:
    for k in keys:
        if d.get(k) not in (None, "", [], {}):
            return d[k]
    return None


def _number(v) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.fullmatch(r"\s*(?:EGP|LE|ج\.?م)?\s*([\d,]+(?:\.\d+)?)\s*(?:EGP|LE|ج\.?م)?\s*", v)
        return float(m.group(1).replace(",", "")) if m else None
    if isinstance(v, dict):
        return _number(_first(_lower(v), ("amount", "value", "price", "final", "current")))
    return None


def _text(v) -> str:
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        v = _lower(v)
        return _text(_first(v, ("en", "english", "value", "text", "name", "ar", "arabic")))
    return ""


def _image(v) -> str:
    if isinstance(v, list) and v:
        return _image(v[0])
    if isinstance(v, dict):
        return _image(_first(_lower(v), ("url", "src", "original", "large", "medium", "small", "thumbnail")))
    return v if isinstance(v, str) and v.startswith("http") else ""


def _in_stock(d: dict) -> bool:
    for k in ("out_of_stock", "outofstock", "is_out_of_stock", "isoutofstock", "sold_out", "soldout"):
        if isinstance(d.get(k), bool):
            return not d[k]
    for k in ("in_stock", "instock", "is_in_stock", "isinstock", "is_available", "isavailable", "available"):
        if isinstance(d.get(k), bool):
            return d[k]
    for k in ("stock", "stock_quantity", "stockquantity", "quantity", "available_quantity", "max_quantity"):
        if isinstance(d.get(k), (int, float)) and not isinstance(d.get(k), bool):
            return d[k] > 0
    return True


def to_offer(raw: dict, divisor: float = 1, category: str = "") -> Optional[Offer]:
    d = _lower(raw)
    pid, name = _first(d, ID_KEYS), _text(_first(d, NAME_KEYS))
    if pid is None or isinstance(pid, (dict, list)) or not name:
        return None
    price = None
    for k in PRICE_KEYS:  # the first price-like key holding a positive number is what you pay
        n = _number(d.get(k))
        if n and n > 0:
            price = n
            break
    if price is None:
        return None
    original = None
    for k in ORIGINAL_KEYS + ("price",):
        n = _number(d.get(k))
        if n and n > price:
            original = n
            break
    cat = _first(d, CATEGORY_KEYS)
    return Offer(
        store=STORE,
        sku=str(pid),
        name=name,
        price=round(price / divisor, 2),
        original_price=round(original / divisor, 2) if original else None,
        image=_image(_first(d, IMAGE_KEYS)),
        category=_text(cat) if cat else category,
        brand=_text(_first(d, ("brand", "brand_name", "brandname", "manufacturer"))),
        in_stock=_in_stock(d),
    )


def products_in(data, divisor: float = 1) -> list[Offer]:
    """Every product-shaped object in a JSON document (not looking inside a product once found)."""
    out = []

    def walk(o):
        if isinstance(o, dict):
            offer = to_offer(o, divisor)
            if offer:
                out.append(offer)
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return out


# ---- HAR import ------------------------------------------------------------------------------------

def _entry_json(entry: dict):
    content = (entry.get("response") or {}).get("content") or {}
    text = content.get("text")
    if not text:
        return None
    if content.get("encoding") == "base64":
        try:
            text = base64.b64decode(text).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
    text = text.strip()
    if not text.startswith(("{", "[")):
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def read_har(path: str, divisor: float = 1) -> tuple[list[Offer], list[dict]]:
    """Returns (offers found, replayable requests)."""
    with open(path, encoding="utf-8") as f:
        har = json.load(f)
    offers, requests, seen_urls = [], [], set()
    for entry in (har.get("log") or {}).get("entries") or []:
        req = entry.get("request") or {}
        if req.get("method", "GET").upper() != "GET" or (entry.get("response") or {}).get("status") != 200:
            continue
        data = _entry_json(entry)
        found = products_in(data, divisor) if data is not None else []
        if not found:
            continue
        offers += found
        url = req.get("url", "")
        param = _page_param(url)
        key = _with_page(url, param, 1) if param else url  # page 2 of a list is the same request as page 1
        if SKIP_URL.search(urlsplit(url).path) or key in seen_urls:
            continue
        seen_urls.add(key)
        headers = {h["name"]: h["value"] for h in req.get("headers") or []
                   if not h["name"].startswith(":") and h["name"].lower() not in DROP_HEADERS}
        requests.append({"url": url, "headers": headers})
    return offers, requests


def save_requests(path: str, requests: list[dict], divisor: float = 1):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"note": "Breadfast app requests captured from your phone. Contains your session: keep private.",
                   "price_divisor": divisor, "requests": requests}, f, ensure_ascii=False, indent=1)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_requests(path: str = None) -> tuple[list[dict], float]:
    path = path or os.environ.get("BREADFAST_REQUESTS") or DEFAULT_REQUESTS_FILE
    if not os.path.exists(path):
        raise NotConfigured(f"no {path}: capture the Breadfast app and run `python -m deals import-har FILE`"
                            " (see stores/breadfast.py)")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("requests") or [], float(data.get("price_divisor") or 1)


# ---- refreshing ------------------------------------------------------------------------------------

def _page_param(url: str) -> Optional[str]:
    for k, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True):
        if k.lower() in PAGE_PARAMS:
            return k
    return None


def _with_page(url: str, param: str, n: int) -> str:
    parts = urlsplit(url)
    q = [(k, str(n) if k == param else v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    return urlunsplit(parts._replace(query=urlencode(q)))


def collect(fetcher: Fetcher, requests_file: str = None, log=print) -> Iterator[Offer]:
    requests, divisor = load_requests(requests_file)
    if not requests:
        raise NotConfigured("the requests file lists no product requests; capture again while browsing categories")
    log(f"breadfast: refreshing {len(requests)} captured requests")
    seen = set()
    for r in requests:
        param = _page_param(r["url"])
        page = 1
        while True:
            url = _with_page(r["url"], param, page) if param else r["url"]
            status, body = fetcher.request(url, headers=r["headers"])
            if status in (401, 403):
                raise RuntimeError(f"Breadfast answered {status}: the app session has expired, capture again")
            if status != 200:
                log(f"breadfast: {status} for {url}, skipped")
                break
            try:
                found = products_in(json.loads(body), divisor)
            except ValueError:
                log(f"breadfast: {url} did not answer JSON, skipped")
                break
            new = [o for o in found if o.sku not in seen]
            seen.update(o.sku for o in new)
            yield from new
            if not param or not new or page >= MAX_PAGES:
                break
            page += 1
