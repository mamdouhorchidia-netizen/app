"""The live page as plain static files: live.html, its stylesheets and its ES modules (deals/static/), served from
memory with gzip, an ETag and no-cache at /static/<build>/<name>, where the build is a hash of all of them."""

import gzip
import http.client
import os
import re
import shutil
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from deals import db, livedata, web
from test_live import seed

HERE = Path(web.__file__).parent
PAGE = web.PAGE.read_text(encoding="utf-8")
JS = {p.name: p.read_text(encoding="utf-8") for p in sorted(web.STATIC_DIR.glob("*.js"))}
IMPORT = re.compile(r'^import\s[^;]*?from\s+"\./([\w-]+\.js)";', re.M)


class Files(unittest.TestCase):
    def test_every_file_is_linked_from_the_page(self):
        for name in JS:
            self.assertIn(f'<link rel="modulepreload" href="/static/{{{{BUILD}}}}/{name}"', PAGE)  # no import waterfall
        for p in web.STATIC_DIR.glob("*.css"):
            self.assertIn(f'<link rel="stylesheet" href="/static/{{{{BUILD}}}}/{p.name}">', PAGE)
        self.assertIn('<link rel="stylesheet" href="/static/{{BUILD}}/tokens.css">', PAGE)
        self.assertLess(PAGE.index("tokens.css"), PAGE.index("app.css"))  # the tokens first, the page's own styles on top
        self.assertIn('<script src="/static/{{BUILD}}/i18n.js"></script>', PAGE)  # classic, in the head: dir before first paint
        self.assertIn('<script type="module" src="/static/{{BUILD}}/main.js"', PAGE)
        linked = set(re.findall(r'/static/\{\{BUILD\}\}/([\w-]+\.(?:js|css))', PAGE))
        self.assertEqual(linked, set(web.assets()))  # nothing linked that isn't served, nothing served that isn't linked

    def test_no_inline_styles_or_scripts_left(self):
        self.assertNotIn("<style", PAGE)
        self.assertNotRegex(PAGE, r"<script>(?!</script>)")
        self.assertIn("data-i18n-render", PAGE[:200])  # strings are written translated; i18n.js only does the markup

    def test_imports_resolve_and_have_no_cycles(self):
        graph = {name: IMPORT.findall(src) for name, src in JS.items()}
        for name, deps in graph.items():
            for d in deps:
                self.assertIn(d, JS, f"{name} imports {d}")
            self.assertNotIn("window.F", JS[name])
        state = {}

        def visit(n, path):
            if state.get(n) == "done":
                return
            self.assertNotEqual(state.get(n), "open", "cycle: " + " -> ".join(path + [n]))
            state[n] = "open"
            for d in graph[n]:
                visit(d, path + [n])
            state[n] = "done"
        for n in graph:
            visit(n, [])
        reach, todo = set(), ["main.js"]  # every module is part of the page
        while todo:
            n = todo.pop()
            if n not in reach:
                reach.add(n)
                todo += graph[n]
        self.assertEqual(reach, set(JS))

    def test_service_worker_keeps_every_file_of_the_page(self):
        sw = (HERE / "sw.js").read_text(encoding="utf-8")
        self.assertIn('const SHELL = "deals-shell-v4"', sw)
        # the worker finds a page's files with this pattern: it must see all of them in the page as served
        pattern = re.search(r'html\.matchAll\(/(.*?)/g\)', sw).group(1).replace("\\/", "/")
        page = web.live_page()[0].decode()
        found = {m.group(1) for m in re.finditer(pattern, page)}
        self.assertEqual(found, {f"/static/{web.build_id()}/{n}" for n in web.assets()})


class Serving(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "d.sqlite3")
        self.con = db.connect(self.path)
        seed(self.con, int(time.time()))
        self.keeper = livedata.CatalogKeeper(self.path, livedata.Version(self.path, every=0), poll=3600)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(self.path, None, self.keeper))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.http = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)

    def tearDown(self):
        self.http.close()
        self.server.shutdown()
        self.server.server_close()
        self.keeper.stop()
        self.con.close()
        self.dir.cleanup()

    def get(self, path, **headers):
        self.http.request("GET", path, headers=headers)
        r = self.http.getresponse()
        body = r.read()
        self.assertEqual(int(r.getheader("Content-Length")), len(body))
        return r, body

    def page_assets(self):
        r, body = self.get("/")
        return re.findall(r'(?:href|src)="(/static/[^"]+)"', body.decode())

    def test_page_names_the_current_build(self):
        r, body = self.get("/", **{"Accept-Encoding": "gzip"})
        self.assertEqual((r.status, r.getheader("Cache-Control"), r.getheader("Content-Encoding")), (200, "no-cache", "gzip"))
        page = gzip.decompress(body).decode()
        self.assertNotIn("{{BUILD}}", page)
        build = web.build_id()
        self.assertRegex(build, r"^[0-9a-f]{12}$")
        self.assertIn(f'src="/static/{build}/main.js"', page)
        r2, _ = self.get("/", **{"If-None-Match": r.getheader("ETag")})
        self.assertEqual(r2.status, 304)

    def test_every_asset_from_memory_gzipped_with_etag_and_no_cache(self):
        urls = self.page_assets()
        self.assertEqual(len(set(urls)), len(web.assets()))
        for url in set(urls):
            name = url.rsplit("/", 1)[1]
            r, body = self.get(url, **{"Accept-Encoding": "gzip"})
            self.assertEqual(r.status, 200, url)
            self.assertEqual(r.getheader("Cache-Control"), "no-cache")
            self.assertEqual(r.getheader("Content-Type"), "text/javascript" if name.endswith(".js") else "text/css; charset=utf-8")
            if len(web.assets()[name].read_bytes()) >= web.GZIP_MIN:
                self.assertEqual(r.getheader("Content-Encoding"), "gzip", url)
                body = gzip.decompress(body)
            self.assertEqual(body, web.assets()[name].read_bytes())
            etag = r.getheader("ETag")
            self.assertTrue(etag)
            r, body = self.get(url, **{"If-None-Match": etag})
            self.assertEqual((r.status, body), (304, b""))
        self.assertIs(web.static_file(web.assets()["main.js"])[0], web.static_file(web.assets()["main.js"])[0])  # memory

    def test_another_build_or_file_is_not_found(self):
        build = web.build_id()
        for path in (f"/static/{'0' * 12}/main.js", f"/static/{build}/nope.js", f"/static/{build}/../web.py",
                     f"/static/{build}/main.py", "/static/main.js"):
            r, _ = self.get(path)
            self.assertEqual((r.status, r.getheader("Cache-Control")), (404, "no-store"), path)

    def test_a_deploy_shows_at_once_and_old_addresses_go(self):
        tmp = Path(self.dir.name) / "static"
        shutil.copytree(web.STATIC_DIR, tmp)
        with mock.patch.object(web, "STATIC_DIR", tmp):
            old = web.build_id()
            r, _ = self.get(f"/static/{old}/main.js")
            self.assertEqual(r.status, 200)
            f = tmp / "card.js"
            f.write_text(f.read_text(encoding="utf-8") + "\n// a deploy\n", encoding="utf-8")
            new = web.build_id()
            self.assertNotEqual(new, old)
            self.assertTrue(all(f"/static/{new}/" in u for u in self.page_assets()))
            self.assertEqual(self.get(f"/static/{old}/main.js")[0].status, 404)  # an old page reloads
            r, body = self.get(f"/static/{new}/card.js")
            self.assertTrue(body.endswith(b"// a deploy\n") or r.getheader("Content-Encoding") == "gzip")


if __name__ == "__main__":
    unittest.main()
