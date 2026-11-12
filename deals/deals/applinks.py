"""Links that open a product in its store's own app, at that product.

Android: an intent:// link naming the app's package and the product's web address. Android opens the app
when it handles that address (the item's page), and the web page (browser_fallback_url) when the app
isn't installed. iPhone: the product's https address, which iOS opens in the app when the app claims that
domain (universal links) and in Safari otherwise.
Breadfast has no web pages: its link comes from the app's own data (a share or deep link in the capture)
when there is one, and otherwise opens the app at its home screen.
"""

import os
import re
from urllib.parse import quote, urlsplit

PACKAGES = {
    # talabat's Android app is com.talabat: it's the app talabat.com's /.well-known/assetlinks.json verifies for all
    # its addresses (com.6alabat.cuisineApp, in its pages' al:android:package, is the iPhone app's id)
    "talabat": "com.talabat",
    "amazon_eg": "com.amazon.mShop.android.shopping",
    "breadfast": "com.breadfast",
    "jumia": "com.jumia.android",
}
# Stores whose app can't open a product from its web address, so Android opens their web page in Chrome.
# talabat: its pages declare only "talabat://" for the app (al:android:url), so the app (com.6alabat.cuisineApp)
# opens a product link at an error page ("We can't load this page"). Its product page on the web works, when the
# link carries an area (aid) that the item's hub delivers to, which every link here does (checked on a phone
# browser, Nov 2026).
WEB_FIRST = set()
CHROME = "com.android.chrome"
STORE_PAGES = {
    "breadfast": ("https://play.google.com/store/apps/details?id=com.breadfast",
                  "https://apps.apple.com/app/breadfast/id1288436997"),
}


def _intent(url: str, package: str, fallback: str) -> str:
    u = urlsplit(url)
    target = (u.netloc + u.path + (("?" + u.query) if u.query else "")) if u.netloc else ""
    scheme = u.scheme or "https"
    return (f"intent://{target}#Intent;scheme={scheme};package={package};"
            f"S.browser_fallback_url={quote(fallback, safe='')};end")


def _amazon_app_url(url: str) -> str:
    """The plain product address the Amazon app understands (no /-/en/ language prefix, no tracking)."""
    m = re.search(r"/dp/([A-Z0-9]{10})", url)
    return f"https://www.amazon.eg/dp/{m.group(1)}" if m else url


def _tagged(url: str) -> str:
    """Amazon links carry the owner's Amazon Associates tag when AMAZON_TAG is set (like 3rrood's ?tag=)."""
    tag = os.environ.get("AMAZON_TAG", "").strip()
    if not tag or not re.fullmatch(r"[A-Za-z0-9_-]{3,40}", tag) or "tag=" in url:
        return url
    return url + ("&" if "?" in url else "?") + "tag=" + tag


def talabat_short(url: str) -> str:
    """talabat's hub product address → the short one its app opens."""
    m = re.search(r"talabat\.com/egypt/grocery/\d+/talabat-mart/product/([^/?#]+)/s/([^/?#]+)", url)
    return f"https://www.talabat.com/egypt/talabat-mart/product/{m.group(1)}/s/{m.group(2)}" if m else url


def links(store: str, url: str, extra: dict = None) -> dict:
    """{"web", "android", "ios", "browser"}: where a tap should go on each kind of device; "browser" opens the
    store's web page in Chrome on Android (a way round an app that can't show the item)."""
    extra = extra or {}
    package = PACKAGES.get(store)
    if store == "breadfast":
        play, appstore = STORE_PAGES["breadfast"]
        app_link = extra.get("app_link") or ""
        if app_link.startswith("breadfast://"):
            return {"web": play, "android": app_link, "ios": app_link, "browser": play}
        if app_link.startswith("http"):
            return {"web": app_link, "android": _intent(app_link, package, play), "ios": app_link,
                    "browser": _intent(app_link, CHROME, app_link)}
        home = f"intent://#Intent;package={package};S.browser_fallback_url={quote(play, safe='')};end"
        return {"web": play, "android": home, "ios": "breadfast://", "browser": play}
    if not url:
        return {"web": "", "android": "", "ios": "", "browser": ""}
    if store == "talabat":
        # talabat's app claims every talabat.com address (Android and iPhone). It opens a product from the short
        # address, /egypt/talabat-mart/product/<slug>/s/<sku>, at the user's own address (as 3rrood links), but not
        # from the hub one (/egypt/grocery/<hub>/talabat-mart/product/...?aid=...): "Sorry, we couldn't load this
        # page". A plain link, so the phone hands it to the app, or opens the web page where there's no app.
        url = talabat_short(url)
        return {"web": url, "android": url, "ios": url, "browser": _intent(url, CHROME, url)}
    if store == "amazon_eg":
        url = _tagged(url)
    in_chrome = _intent(url, CHROME, url)
    if store in WEB_FIRST:
        return {"web": url, "android": in_chrome, "ios": url, "browser": in_chrome}
    app_url = _tagged(_amazon_app_url(url)) if store == "amazon_eg" else url
    return {"web": url, "android": _intent(app_url, package, url) if package else url, "ios": app_url,
            "browser": in_chrome}
