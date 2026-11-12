"""Links that open a product in its store's own app, at that product.

Android: an intent:// link naming the app's package and the product's web address. Android opens the app
when it handles that address (the item's page), and the web page (browser_fallback_url) when the app
isn't installed. iPhone: the product's https address, which iOS opens in the app when the app claims that
domain (universal links) and in Safari otherwise.
Breadfast has no web pages: its link comes from the app's own data (a share or deep link in the capture)
when there is one, and otherwise opens the app at its home screen.
"""

from urllib.parse import quote, urlsplit

PACKAGES = {
    "talabat": "com.talabat",
    "amazon_eg": "com.amazon.mShop.android.shopping",
    "breadfast": "com.breadfast",
}
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


def links(store: str, url: str, extra: dict = None) -> dict:
    """{"web", "android", "ios"}: where a tap should go on each kind of device."""
    extra = extra or {}
    package = PACKAGES.get(store)
    if store == "breadfast":
        play, appstore = STORE_PAGES["breadfast"]
        app_link = extra.get("app_link") or ""
        if app_link.startswith("breadfast://"):
            return {"web": play, "android": app_link, "ios": app_link}
        if app_link.startswith("http"):
            return {"web": app_link, "android": _intent(app_link, package, play), "ios": app_link}
        home = f"intent://#Intent;package={package};S.browser_fallback_url={quote(play, safe='')};end"
        return {"web": play, "android": home, "ios": "breadfast://"}
    if not url:
        return {"web": "", "android": "", "ios": ""}
    return {"web": url, "android": _intent(url, package, url) if package else url, "ios": url}
