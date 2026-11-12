"""Links that open a product in its store's own app, at that product.

Android: an intent:// link naming the app's package and the product's web address. Android opens the app
when it handles that address (the item's page), and the web page (browser_fallback_url) when the app
isn't installed. iPhone: the product's https address, which iOS opens in the app when the app claims that
domain (universal links) and in Safari otherwise.
Breadfast has no web pages: its link comes from the app's own data (a share or deep link in the capture)
when there is one, and otherwise opens the app at its home screen.
"""

import re
from urllib.parse import quote, urlsplit

PACKAGES = {
    "talabat": "com.talabat",
    "amazon_eg": "com.amazon.mShop.android.shopping",
    "breadfast": "com.breadfast",
}
# Stores whose app can't open a product from its web address. On Android the talabat app claims every
# talabat.com link and then shows "We can't load this page" for grocery products, so on Android these open
# in Chrome explicitly (the page shows the exact item and has talabat's own "OPEN in app" button).
WEB_FIRST = {"talabat"}
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
    in_chrome = _intent(url, CHROME, url)
    if store in WEB_FIRST:
        return {"web": url, "android": in_chrome, "ios": url, "browser": in_chrome}
    app_url = _amazon_app_url(url) if store == "amazon_eg" else url
    return {"web": url, "android": _intent(app_url, package, url) if package else url, "ios": app_url,
            "browser": in_chrome}
