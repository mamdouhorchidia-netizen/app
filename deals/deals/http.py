"""A slow, honest HTTP client: it says who it is, waits between requests and backs off on errors."""

import gzip
import time
import urllib.error
import urllib.request

USER_AGENT = "deals-tracker/0.1 (+https://deals.vorath.cloud; price comparison)"  # who we are, as Jumia asks


class Blocked(Exception):
    """The site answered with a bot check. Collection stops; we do not try to get past it."""


class Fetcher:
    def __init__(self, delay: float = 1.5, retries: int = 3, timeout: float = 30, user_agent: str = USER_AGENT):
        self.delay = delay
        self.retries = retries
        self.timeout = timeout
        self.user_agent = user_agent
        self.retry_statuses = {429, 500, 502, 503, 504}
        self._last = 0.0

    def _wait(self):
        gap = time.monotonic() - self._last
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last = time.monotonic()

    def request(self, url: str, data: bytes = None, headers: dict = None, method: str = None) -> tuple[int, str]:
        """Returns (status, body). Statuses in retry_statuses and network errors are retried, then raised;
        any other error status is returned."""
        hdrs = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip", "Accept-Language": "en"}
        hdrs.update(headers or {})
        err = None
        for attempt in range(self.retries):
            self._wait()
            req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return r.status, _body(r.read(), r.headers.get("Content-Encoding"))
            except urllib.error.HTTPError as e:
                body = _body(e.read(), e.headers.get("Content-Encoding"))
                if e.code not in self.retry_statuses:
                    return e.code, body
                err = e
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                err = e
            time.sleep(min(60, 5 * 2**attempt))  # 5s, 10s, 20s
        raise RuntimeError(f"{url}: {err}")

    def get(self, url: str, headers: dict = None) -> tuple[int, str]:
        return self.request(url, headers=headers)


def _body(raw: bytes, encoding: str) -> str:
    if encoding == "gzip":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", errors="replace")
