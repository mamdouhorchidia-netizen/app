"""browser.py: the DevTools pipe framing, the restart policy, and (when Chrome is here) real renders."""

import functools
import http.server
import json
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deals import browser  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


class Framing(unittest.TestCase):
    def test_encode_is_compact_json_and_a_nul(self):
        self.assertEqual(browser.encode({"id": 1, "method": "Browser.getVersion", "params": {}}),
                         b'{"id":1,"method":"Browser.getVersion","params":{}}\0')

    def test_messages_cut_anywhere_and_several_per_read(self):
        f = browser.Framer()
        data = browser.encode({"id": 1, "result": {"a": "é"}}) + browser.encode({"method": "X.y", "params": {}})
        got = []
        for i in range(0, len(data), 3):
            got += f.feed(data[i:i + 3])
        self.assertEqual(got, [{"id": 1, "result": {"a": "é"}}, {"method": "X.y", "params": {}}])
        self.assertEqual(f.feed(data + data[:5]), got)  # two whole ones; the partial third waits
        self.assertEqual(f.buf, data[:5])

    def test_bad_or_endless_messages_are_protocol_errors(self):
        with self.assertRaises(browser.ProtocolError):
            browser.Framer().feed(b"{not json\0")
        with self.assertRaises(browser.ProtocolError):
            browser.Framer(limit=10).feed(b"x" * 11)


class FakeChrome:
    """The far end of a Session's pipe: answers each command with `answer(msg)` (a list of messages)."""

    def __init__(self, answer):
        a_r, a_w = os.pipe()  # Session -> fake
        b_r, b_w = os.pipe()  # fake -> Session
        self.s = browser.Session.__new__(browser.Session)
        self.s.pid, self.s.next_id, self.s.framer, self.s.events = None, 0, browser.Framer(), []
        self.s.wfd, self.s.rfd = a_w, b_r
        os.set_blocking(b_r, False)
        self.s.alive = lambda: True
        self.answer, self.r, self.w = answer, a_r, b_w
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        f = browser.Framer()
        while True:
            data = os.read(self.r, 65536)
            if not data:
                return
            for m in f.feed(data):
                for out in self.answer(m):
                    os.write(self.w, browser.encode(out))


class Calls(unittest.TestCase):
    def test_call_waits_for_its_answer_and_keeps_events(self):
        def answer(m):
            return [{"method": "Page.loadEventFired", "sessionId": "S", "params": {"t": 1}},
                    {"id": m["id"], "result": {"echo": m["method"]}}]
        s = FakeChrome(answer).s
        self.assertEqual(s.call("A.b", deadline=time.monotonic() + 5), {"echo": "A.b"})
        self.assertEqual(s.wait_event("Page.loadEventFired", "S", time.monotonic() + 5), {"t": 1})

    def test_error_answer_raises(self):
        s = FakeChrome(lambda m: [{"id": m["id"], "error": {"message": "nope"}}]).s
        with self.assertRaisesRegex(browser.ProtocolError, "nope"):
            s.call("A.b", deadline=time.monotonic() + 5)

    def test_silence_is_a_hang_at_the_deadline(self):
        s = FakeChrome(lambda m: []).s
        t = time.monotonic()
        with self.assertRaises(browser.Hang):
            s.call("A.b", deadline=t + 0.3)
        self.assertLess(time.monotonic() - t, 2)

    def test_a_page_that_goes_away_is_a_protocol_error(self):
        s = FakeChrome(lambda m: [{"method": "Target.detachedFromTarget", "params": {"sessionId": "S"}}]).s
        s.send("A.b")
        with self.assertRaises(browser.ProtocolError):
            s.wait_event("Emulation.virtualTimeBudgetExpired", "S", time.monotonic() + 5)


class FakeSession:
    made = []

    def __init__(self, chrome, fail_start=False, behave=None):
        self.pages, self.mb, self.closed, self.fail_start = 0, 300.0, False, fail_start
        self.behave = behave or (lambda url: f"<html>{url}</html>")
        FakeSession.made.append(self)

    def start(self, deadline):
        if self.fail_start:
            raise browser.ProtocolError("no pipe")

    def alive(self):
        return not self.closed

    def render(self, url, budget_ms, deadline):
        out = self.behave(url)
        self.pages += 1
        return out

    def memory_mb(self):
        return self.mb

    def kill(self):
        pass

    def close(self, gently=True):
        self.closed = True


class Restarts(unittest.TestCase):
    def setUp(self):
        FakeSession.made = []
        self.fallbacks = []
        self.now = [1000.0]
        p = mock.patch.object(browser, "find_chrome", return_value="/bin/chrome")
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("CHROME_PIPE", None)

    def keeper(self, factory=FakeSession, **policy):
        k = browser.Keeper(browser.Policy(**policy), log=lambda *_: None, session_factory=factory,
                           fallback=lambda url, b, t: (self.fallbacks.append(url), "old")[1], clock=lambda: self.now[0])
        k._start_reaper = lambda: None
        return k

    def test_policy(self):
        p = browser.Policy(max_pages=200, max_mb=700)
        self.assertIsNone(p.restart_reason(199, 699))
        self.assertEqual(p.restart_reason(200, 10), "pages")
        self.assertEqual(p.restart_reason(5, 701), "memory")

    def test_one_chrome_for_many_pages_then_a_fresh_one(self):
        k = self.keeper(max_pages=3)
        for i in range(7):
            self.assertEqual(k.render(f"u{i}", 10000, 90), f"<html>u{i}</html>")
        self.assertEqual(len(FakeSession.made), 3)
        self.assertTrue(FakeSession.made[0].closed and FakeSession.made[1].closed)
        self.assertEqual(k.restarts, {"pages": 2})

    def test_memory_past_the_bound_restarts(self):
        k = self.keeper(max_mb=500)
        k.render("a", 1, 1)
        FakeSession.made[0].mb = 900
        k.render("b", 1, 1)
        self.assertTrue(FakeSession.made[0].closed)
        k.render("c", 1, 1)
        self.assertEqual((len(FakeSession.made), k.restarts), (2, {"memory": 1}))

    def test_a_hang_kills_chrome_and_the_next_page_gets_a_new_one(self):
        def behave(url):
            if url == "slow":
                raise browser.Hang("x")
            return "ok"
        k = self.keeper(factory=functools.partial(FakeSession, behave=behave))
        with self.assertRaisesRegex(RuntimeError, "took longer than 90 s on slow"):
            k.render("slow", 10000, 90)
        self.assertIsNone(k.session)
        self.assertEqual(k.render("fine", 10000, 90), "ok")
        self.assertEqual((len(FakeSession.made), k.restarts), (2, {"hang": 1}))

    def test_a_protocol_error_restarts_too(self):
        def behave(url):
            raise browser.ProtocolError("bad")
        k = self.keeper(factory=functools.partial(FakeSession, behave=behave))
        with self.assertRaisesRegex(RuntimeError, "Chrome failed on u: bad"):
            k.render("u", 1, 1)
        self.assertEqual(k.restarts, {"protocol error": 1})
        self.assertEqual(self.fallbacks, [])  # the page isn't asked for a second time

    def test_a_dead_chrome_is_replaced_before_the_page(self):
        k = self.keeper()
        k.render("a", 1, 1)
        FakeSession.made[0].closed = True
        k.render("b", 1, 1)
        self.assertEqual((len(FakeSession.made), k.restarts), (2, {"died": 1}))

    def test_no_pipe_falls_back_to_a_launch_per_page_and_tries_again_later(self):
        k = self.keeper(factory=functools.partial(FakeSession, fail_start=True), retry_pipe_after=1800)
        self.assertEqual(k.render("a", 1, 1), "old")
        self.assertEqual(k.render("b", 1, 1), "old")
        self.assertEqual(len(FakeSession.made), 1)  # not tried again on every page
        self.now[0] += 1800
        k.session_factory = FakeSession
        self.assertEqual(k.render("c", 1, 1), "<html>c</html>")
        self.assertEqual(self.fallbacks, ["a", "b"])

    def test_chrome_pipe_0_keeps_the_old_way(self):
        k = self.keeper()
        with mock.patch.dict(os.environ, {"CHROME_PIPE": "0"}):
            self.assertEqual(k.render("a", 1, 1), "old")
        self.assertEqual(FakeSession.made, [])

    def test_no_chrome_is_still_browser_unavailable(self):
        k = self.keeper()
        with mock.patch.object(browser, "find_chrome", side_effect=browser.BrowserUnavailable("none")):
            with self.assertRaises(browser.BrowserUnavailable):
                k.render("a", 1, 1)

    def test_idle_chrome_is_shut_down(self):
        k = self.keeper(idle=120)
        k.render("a", 1, 1)
        self.now[0] += 60
        self.assertFalse(k.reap())
        self.now[0] += 61
        self.assertTrue(k.reap())
        self.assertTrue(FakeSession.made[0].closed)
        self.assertEqual(k.restarts, {"idle": 1})


try:
    CHROME = browser.find_chrome()
except browser.BrowserUnavailable:
    CHROME = None


class _Pages(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/never":
            time.sleep(30)
            return
        if self.path == "/hang":
            body = b"<html><body><img src='/never'></body></html>"
        elif self.path == "/js":
            body = (b"<!doctype html><html><body><div id=app></div><script>setTimeout(function(){"
                    b"document.getElementById('app').innerHTML='<p class=item>built</p>'}, 2000)</script></body></html>")
        elif (FIX / self.path.lstrip("/")).is_file():
            body = (FIX / self.path.lstrip("/")).read_bytes()
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@unittest.skipUnless(CHROME, "no Chrome/Chromium here")
class RealChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Pages)
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.keeper = browser.Keeper(browser.Policy(max_pages=3), log=lambda *_: None)
        cls.keeper._start_reaper = lambda: None

    @classmethod
    def tearDownClass(cls):
        cls.keeper.stop()
        cls.server.shutdown()

    def test_renders_like_a_launch_per_page_and_parses_the_same(self):
        from deals.stores import amazon_eg, raya
        for name, parse in (("raya_category.html", lambda h: raya.parse_page(h, "C")),
                            ("amazon_deals.html", amazon_eg.parse_deals)):
            new = self.keeper.render(f"{self.base}/{name}", 10000, 60)
            self.assertIsNotNone(self.keeper.session, "the pipe session should have started")
            self.assertEqual(new, browser._render_once(f"{self.base}/{name}", 10000, 60))
            self.assertEqual([o.__dict__ for o in parse(new)], [o.__dict__ for o in parse((FIX / name).read_text())])
            self.assertTrue(parse(new))

    def test_page_scripts_run_for_the_virtual_time_budget(self):
        self.assertIn('<p class="item">built</p>', self.keeper.render(f"{self.base}/js", 10000, 60))
        self.assertNotIn('<p class="item">', self.keeper.render(f"{self.base}/js", 500, 60))  # 2 s timer, 0.5 s budget

    def test_a_page_that_never_loads_restarts_chrome(self):
        self.keeper.render(f"{self.base}/js", 10000, 60)
        old = self.keeper.session
        t = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "took longer than 3 s"):
            self.keeper.render(f"{self.base}/hang", 10000, 3)
        self.assertLess(time.monotonic() - t, 10)
        self.assertIsNone(self.keeper.session)
        self.assertFalse(os.path.exists(old.profile))
        self.assertIn('<p class="item">', self.keeper.render(f"{self.base}/js", 10000, 60))


if __name__ == "__main__":
    unittest.main()
