"""Headless Chrome, for pages whose content is built by JavaScript (Amazon's Today's Deals, Raya's categories).

It runs Chrome as it is: its own headless identity, no disguise, TLS checks on.
The caller checks the rendered page for a bot check and stops if there is one; this module never tries to
get past one.

Chrome is found at $CHROME_BIN, then chromium / google-chrome on the PATH, then Playwright's copy.

One Chrome stays running and is driven over the DevTools protocol on a pipe (--remote-debugging-pipe:
JSON messages, each ended by a NUL byte, on fds 3 and 4), so a page no longer pays for a browser launch.
Each render gets a fresh browser context (no cookies, cache or storage from earlier pages, as with the old
throwaway profile), runs the page's scripts for `budget_ms` of virtual page time exactly as
--virtual-time-budget did, and reads the DOM. Chrome is restarted after CHROME_MAX_PAGES renders (200),
when its memory passes CHROME_MAX_MB (700), after any protocol error, and after a page outruns its time
limit (then it is killed outright); it is shut down after CHROME_IDLE seconds without a render (120). Its
profile directory is removed every time it stops and when the app exits. If the pipe session can't start,
render falls back to one Chrome launch per page (--dump-dom) for a while; CHROME_PIPE=0 always does that.
"""

import atexit
import glob
import json
import os
import select
import shutil
import signal
import subprocess
import tempfile
import threading
import time

from .models import NotConfigured


# One Chrome at a time across all store threads: two at once plus the app went past the container's
# 1.5 GB memory limit and the kernel OOM-killed the app (41 restarts).
_one_chrome = threading.Lock()


class BrowserUnavailable(NotConfigured):
    pass


class ProtocolError(RuntimeError):
    """The DevTools session broke: an error answer, bad framing, or Chrome went away."""


class Hang(RuntimeError):
    """A page outran its time limit."""


def find_chrome() -> str:
    for c in [os.environ.get("CHROME_BIN"), shutil.which("chromium"), shutil.which("chromium-browser"),
              shutil.which("google-chrome"), shutil.which("google-chrome-stable"),
              *sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome"), reverse=True)]:
        if c and os.path.exists(c):
            return c
    raise BrowserUnavailable("no Chrome/Chromium found (set CHROME_BIN)")


def _base_args(chrome: str, profile: str) -> list[str]:
    args = [chrome, "--headless=new", f"--user-data-dir={profile}", f"--disk-cache-dir={profile}/cache", "--no-sandbox",
            "--disable-gpu", "--disable-dev-shm-usage", "--no-first-run", "--no-default-browser-check",
            "--mute-audio", "--hide-scrollbars", "--window-size=1366,2000"]
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        args.insert(1, f"--proxy-server={proxy}")
    return args


def render(url: str, budget_ms: int = 15000, timeout: float = 90) -> str:
    """The page's DOM after its scripts ran for up to `budget_ms` of page time."""
    with _one_chrome:
        return _keeper.render(url, budget_ms, timeout)


# ---- the old way: one Chrome launch per page ------------------------------------------------------

def _render_once(url: str, budget_ms: int, timeout: float) -> str:
    chrome = find_chrome()
    # A throwaway profile per run, removed below. Without it Chrome leaves a ~100 MB scoped_dir under
    # ~/.config and ~/.cache on every run (the SIGKILL below stops it cleaning up), which filled the disk.
    profile = tempfile.mkdtemp(prefix="deals-chrome-")
    args = _base_args(chrome, profile) + [f"--virtual-time-budget={budget_ms}", "--dump-dom", url]
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
        shutil.rmtree(profile, ignore_errors=True)
    if proc.returncode != 0 and not out:
        raise RuntimeError(f"Chrome failed ({proc.returncode}): {err.decode(errors='replace')[-300:]}")
    return out.decode("utf-8", errors="replace")


# ---- DevTools protocol over a pipe ----------------------------------------------------------------

def encode(message: dict) -> bytes:
    """One message as Chrome's pipe wants it: compact JSON and a NUL byte."""
    return json.dumps(message, separators=(",", ":")).encode() + b"\0"


class Framer:
    """Splits the bytes read from Chrome into messages (each ends with a NUL; reads cut anywhere)."""

    def __init__(self, limit: int = 256 << 20):
        self.buf, self.limit = b"", limit

    def feed(self, data: bytes) -> list[dict]:
        self.buf += data
        *done, self.buf = self.buf.split(b"\0")
        if len(self.buf) > self.limit:
            raise ProtocolError("a message from Chrome too large or never ended")
        out = []
        for raw in done:
            try:
                out.append(json.loads(raw))
            except ValueError as e:
                raise ProtocolError(f"bad message from Chrome: {raw[:80]!r}") from e
        return out


DOM = ("(document.doctype ? new XMLSerializer().serializeToString(document.doctype) + '\\n' : '')"
       " + (document.documentElement ? document.documentElement.outerHTML : '')")


class Session:
    """One running Chrome, its pipe, and its profile directory."""

    def __init__(self, chrome: str, spawn=None):
        self.chrome, self.pages, self.next_id = chrome, 0, 0
        self.framer, self.events = Framer(), []
        self.pid = self.rfd = self.wfd = None
        self.profile = tempfile.mkdtemp(prefix=f"deals-chrome-pipe-{os.getpid()}-")
        self._spawn = spawn or self._posix_spawn

    # process

    def _posix_spawn(self, args, to_chrome_r, from_chrome_w) -> int:
        devnull = os.open(os.devnull, os.O_RDWR)
        try:
            return os.posix_spawn(args[0], args, os.environ, setpgroup=0, file_actions=[
                (os.POSIX_SPAWN_DUP2, devnull, 0), (os.POSIX_SPAWN_DUP2, devnull, 1), (os.POSIX_SPAWN_DUP2, devnull, 2),
                (os.POSIX_SPAWN_DUP2, to_chrome_r, 3), (os.POSIX_SPAWN_DUP2, from_chrome_w, 4)])
        finally:
            os.close(devnull)

    def start(self, deadline: float):
        # Chrome reads commands on fd 3 and writes answers on fd 4. The pipe ends are first moved above fd 9,
        # so the dup2s onto 3 and 4 can't overwrite one another (posix_spawn: no preexec_fn, thread-safe).
        a_r, a_w = os.pipe()
        b_r, b_w = os.pipe()
        hi = [_dup_high(fd) for fd in (a_r, b_w)]
        for fd in (a_r, b_w):
            os.close(fd)
        try:
            self.pid = self._spawn(_base_args(self.chrome, self.profile) + ["--remote-debugging-pipe", "about:blank"],
                                   hi[0], hi[1])
        except OSError:
            for fd in hi + [a_w, b_r]:
                os.close(fd)
            raise
        for fd in hi:
            os.close(fd)
        self.wfd, self.rfd = a_w, b_r
        os.set_blocking(self.rfd, False)
        self.call("Browser.getVersion", deadline=deadline)  # answers once the browser is up

    def alive(self) -> bool:
        if self.pid is None:
            return False
        try:
            pid, _ = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            return False
        return pid == 0

    def close(self, gently: bool = True):
        """Stops Chrome (asks first, then SIGKILLs the whole group) and removes its profile."""
        if self.pid is not None:
            if gently and self.alive():
                try:
                    self.send("Browser.close")
                    end = time.monotonic() + 3
                    while time.monotonic() < end and self.alive():
                        time.sleep(0.05)
                except (OSError, ProtocolError):
                    pass
            try:
                os.killpg(self.pid, signal.SIGKILL)  # helpers too
            except (ProcessLookupError, PermissionError):
                pass
            try:
                os.waitpid(self.pid, 0)
            except ChildProcessError:
                pass
            self.pid = None
        for fd in (self.rfd, self.wfd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self.rfd = self.wfd = None
        shutil.rmtree(self.profile, ignore_errors=True)

    def kill(self):
        """SIGKILLs Chrome's whole process group; safe from another thread (close() then reaps and cleans)."""
        if self.pid is not None:
            try:
                os.killpg(self.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    def memory_mb(self) -> float:
        """Chrome's memory: the PSS (shared pages counted once) of every process in its group, in MB."""
        return _group_memory_kb(self.pid) / 1024 if self.pid else 0.0

    # protocol

    def send(self, method: str, params: dict = None, session_id: str = None) -> int:
        self.next_id += 1
        msg = {"id": self.next_id, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        data = encode(msg)
        while data:
            try:
                data = data[os.write(self.wfd, data):]
            except BrokenPipeError as e:
                raise ProtocolError("Chrome closed its pipe") from e
        return self.next_id

    def _read(self, deadline: float):
        left = deadline - time.monotonic()
        if left <= 0:
            raise Hang("no answer from Chrome in time")
        poller = select.poll()  # not select(): the app may hold more than 1024 descriptors
        poller.register(self.rfd, select.POLLIN)
        if not poller.poll(min(left, 1.0) * 1000):
            if not self.alive():
                raise ProtocolError("Chrome exited")
            return
        try:
            data = os.read(self.rfd, 1 << 20)
        except BlockingIOError:
            return
        if not data:
            raise ProtocolError("Chrome closed its pipe")
        self.events.extend(self.framer.feed(data))

    def call(self, method: str, params: dict = None, session_id: str = None, deadline: float = None) -> dict:
        deadline = deadline or time.monotonic() + 30
        want = self.send(method, params, session_id)
        while True:
            for i, m in enumerate(self.events):
                if m.get("id") == want:
                    del self.events[i]
                    if "error" in m:
                        raise ProtocolError(f"{method}: {m['error'].get('message')}")
                    return m.get("result") or {}
            self._read(deadline)

    def wait_event(self, method: str, session_id: str, deadline: float) -> dict:
        while True:
            for i, m in enumerate(self.events):
                if m.get("method") == method and m.get("sessionId") == session_id:
                    del self.events[i]
                    return m.get("params") or {}
                if m.get("method") == "Target.detachedFromTarget" and \
                        (m.get("params") or {}).get("sessionId") == session_id:
                    raise ProtocolError("the page went away (crashed?)")
            self._read(deadline)

    def render(self, url: str, budget_ms: int, deadline: float) -> str:
        """Like `chrome --virtual-time-budget=N --dump-dom URL`, in a fresh browser context."""
        ctx = self.call("Target.createBrowserContext", {"disposeOnDetach": True}, deadline=deadline)["browserContextId"]
        target = None
        try:
            target = self.call("Target.createTarget", {"url": "about:blank", "browserContextId": ctx,
                                                       "width": 1366, "height": 2000}, deadline=deadline)["targetId"]
            sid = self.call("Target.attachToTarget", {"targetId": target, "flatten": True}, deadline=deadline)["sessionId"]
            self.call("Page.enable", session_id=sid, deadline=deadline)
            if budget_ms:  # page time stands still until the page is there (or the blank page uses the budget)
                self.call("Emulation.setVirtualTimePolicy", {"policy": "pause"}, session_id=sid, deadline=deadline)
            nav = self.call("Page.navigate", {"url": url}, session_id=sid, deadline=deadline)
            if budget_ms:  # then what --virtual-time-budget does: page time runs fast when idle, waits for fetches
                self.call("Emulation.setVirtualTimePolicy", {"policy": "pauseIfNetworkFetchesPending",
                                                             "budget": budget_ms}, session_id=sid, deadline=deadline)
                self.wait_event("Emulation.virtualTimeBudgetExpired", sid, deadline)
            elif not nav.get("errorText"):
                self.wait_event("Page.loadEventFired", sid, deadline)
            out = self.call("Runtime.evaluate", {"expression": DOM, "returnByValue": True},
                            session_id=sid, deadline=deadline)
            if "exceptionDetails" in out:
                raise ProtocolError(f"couldn't read the page: {out['exceptionDetails'].get('text')}")
            html = (out["result"].get("value") or "") + "\n"  # as --dump-dom prints it
        finally:
            self.events = [m for m in self.events if "id" in m]  # this page's events are done with
        self.call("Target.closeTarget", {"targetId": target}, deadline=deadline)
        self.call("Target.disposeBrowserContext", {"browserContextId": ctx}, deadline=deadline)
        self.events.clear()
        self.pages += 1
        return html


def _dup_high(fd: int) -> int:
    import fcntl
    return fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 10)


def _group_memory_kb(pgid: int) -> int:
    total = 0
    for stat in glob.glob("/proc/[0-9]*/stat"):
        try:
            with open(stat) as f:
                fields = f.read().rsplit(")", 1)[1].split()
            if int(fields[2]) != pgid:  # fields after the name: state, ppid, pgrp
                continue
            base = stat[:-4]
            try:
                with open(base + "smaps_rollup") as f:
                    total += next(int(line.split()[1]) for line in f if line.startswith("Pss:"))
            except (OSError, StopIteration):
                with open(base + "status") as f:
                    total += next((int(line.split()[1]) for line in f if line.startswith("VmRSS:")), 0)
        except (OSError, ValueError, IndexError):
            continue
    return total


# ---- when to restart -------------------------------------------------------------------------------

class Policy:
    def __init__(self, max_pages: int = 200, max_mb: float = 700, idle: float = 120, retry_pipe_after: float = 1800):
        self.max_pages, self.max_mb, self.idle, self.retry_pipe_after = max_pages, max_mb, idle, retry_pipe_after

    @classmethod
    def from_env(cls) -> "Policy":
        e = os.environ.get
        return cls(int(e("CHROME_MAX_PAGES", 200)), float(e("CHROME_MAX_MB", 700)), float(e("CHROME_IDLE", 120)))

    def restart_reason(self, pages: int, mb: float):
        """Why a Chrome that has done `pages` renders and uses `mb` should be replaced now: "pages", "memory"
        or None."""
        if pages >= self.max_pages:
            return "pages"
        if mb > self.max_mb:
            return "memory"
        return None


class Keeper:
    """Keeps the one Chrome: starts it on demand, replaces it by the policy, falls back to a launch per page.
    Every method but `reap` runs under _one_chrome."""

    def __init__(self, policy: Policy = None, log=print, session_factory=Session, fallback=None, clock=time.monotonic):
        self.policy, self.log, self.session_factory, self.clock = policy or Policy.from_env(), log, session_factory, clock
        self.fallback = fallback or (lambda url, budget_ms, timeout: _render_once(url, budget_ms, timeout))
        self.session, self.pipe_failed_at, self.last_used = None, None, 0.0
        self.restarts, self.renders = {}, 0
        self._reaper = None

    def _count(self, why: str):
        self.restarts[why] = self.restarts.get(why, 0) + 1

    def stop(self, why: str = "exit", gently: bool = True):
        if self.session:
            self.session.close(gently)
            self.session = None
            self._count(why)

    def _use_pipe(self) -> bool:
        if os.environ.get("CHROME_PIPE", "1") == "0":
            return False
        return self.pipe_failed_at is None or self.clock() - self.pipe_failed_at >= self.policy.retry_pipe_after

    def render(self, url: str, budget_ms: int, timeout: float) -> str:
        start = self.clock()
        deadline = time.monotonic() + timeout
        self.last_used = start
        if not self._use_pipe():
            return self.fallback(url, budget_ms, timeout)
        if self.session and not self.session.alive():
            self.stop("died", gently=False)
        if not self.session:
            chrome = find_chrome()  # BrowserUnavailable as before when there's none
            s = self.session_factory(chrome)
            try:
                s.start(min(deadline, time.monotonic() + 30))
            except Exception as e:  # no request was made yet: the old way is safe to use for this page
                s.close(gently=False)
                self.pipe_failed_at = self.clock()
                self.log(f"browser: Chrome's DevTools pipe didn't start ({e}); one launch per page for now")
                return self.fallback(url, budget_ms, max(1.0, deadline - time.monotonic()))
            self.session, self.pipe_failed_at = s, None
            self._start_reaper()
        # The watchdog: whatever the render is blocked on, Chrome is SIGKILLed a little after the page's time
        # is up, which ends the wait with an error (the reads also stop at the deadline on their own).
        session = self.session
        dog = threading.Timer(max(0.0, deadline - time.monotonic()) + 5, lambda: session.kill())
        dog.daemon = True
        dog.start()
        try:
            try:
                html = session.render(url, budget_ms, deadline)
            finally:
                dog.cancel()
        except Hang:
            self.stop("hang", gently=False)
            raise RuntimeError(f"Chrome took longer than {timeout:.0f} s on {url}")
        except (ProtocolError, OSError, KeyError, TypeError) as e:
            self.stop("protocol error", gently=False)
            raise RuntimeError(f"Chrome failed on {url}: {e}")
        finally:
            self.last_used = self.clock()
        self.renders += 1
        pages, mb = self.session.pages, self.session.memory_mb()
        why = self.policy.restart_reason(pages, mb)
        if why:
            self.log(f"browser: restarting Chrome after {pages} pages, at {mb:.0f} MB")
            self.stop(why)
        return html

    def reap(self) -> bool:
        """Shuts an idle Chrome down (memory back to the app between Amazon's once-a-sweep renders)."""
        if not _one_chrome.acquire(blocking=False):
            return False
        try:
            if self.session and self.clock() - self.last_used >= self.policy.idle:
                self.stop("idle")
                return True
            return False
        finally:
            _one_chrome.release()

    def _start_reaper(self):
        if self._reaper and self._reaper.is_alive():
            return

        def loop():
            while self.session:
                time.sleep(min(10.0, max(1.0, self.policy.idle / 4)))
                self.reap()

        self._reaper = threading.Thread(target=loop, name="chrome-reaper", daemon=True)
        self._reaper.start()


def _sweep_stale_profiles():
    """Profiles left by an app that was killed before it could remove its Chrome's (its pid is gone)."""
    for d in glob.glob(os.path.join(tempfile.gettempdir(), "deals-chrome-pipe-*")):
        try:
            pid = int(os.path.basename(d).split("-")[3])
        except (IndexError, ValueError):
            continue
        if pid == os.getpid():
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            shutil.rmtree(d, ignore_errors=True)
        except PermissionError:
            pass


_keeper = Keeper()


@atexit.register
def _shutdown():
    got = _one_chrome.acquire(timeout=5)  # a render in progress gets a moment; then Chrome goes regardless
    try:
        _keeper.stop("exit", gently=False)
    finally:
        if got:
            _one_chrome.release()


_sweep_stale_profiles()
