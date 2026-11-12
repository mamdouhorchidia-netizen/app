"""Headless Chrome, for pages whose content is built by JavaScript (Amazon's Today's Deals).

It runs Chrome as it is: its own headless identity, no disguise, TLS checks on.
The caller checks the rendered page for a bot check and stops if there is one; this module never tries to
get past one.

Chrome is found at $CHROME_BIN, then chromium / google-chrome on the PATH, then Playwright's copy.
"""

import glob
import os
import shutil
import signal
import subprocess

from .models import NotConfigured


class BrowserUnavailable(NotConfigured):
    pass


def find_chrome() -> str:
    for c in [os.environ.get("CHROME_BIN"), shutil.which("chromium"), shutil.which("chromium-browser"),
              shutil.which("google-chrome"), shutil.which("google-chrome-stable"),
              *sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome"), reverse=True)]:
        if c and os.path.exists(c):
            return c
    raise BrowserUnavailable("no Chrome/Chromium found (set CHROME_BIN)")


def render(url: str, budget_ms: int = 15000, timeout: float = 90) -> str:
    """The page's DOM after its scripts ran for up to `budget_ms` of page time."""
    chrome = find_chrome()
    args = [chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
            "--no-first-run", "--no-default-browser-check", "--mute-audio", "--hide-scrollbars",
            "--window-size=1366,2000", f"--virtual-time-budget={budget_ms}", "--dump-dom", url]
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        args.insert(1, f"--proxy-server={proxy}")
    # Its own process group, so a timeout ends Chrome's helper processes too (they hold the output open).
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise RuntimeError(f"Chrome took longer than {timeout:.0f} s on {url}")
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # no stray helpers left behind
        except ProcessLookupError:
            pass
    if proc.returncode != 0 and not out:
        raise RuntimeError(f"Chrome failed ({proc.returncode}): {err.decode(errors='replace')[-300:]}")
    return out.decode("utf-8", errors="replace")
