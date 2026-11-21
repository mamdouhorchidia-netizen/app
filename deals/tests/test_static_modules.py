"""The live page as plain static files: live.html, its stylesheets and its ES modules (deals/static/). The page is sent
as one document with all of them written in (the modules joined into one by web.bundle); each file is also served
from memory with gzip and an ETag at /static/<build>/<name>, where the build is a hash of all of them, cached for good."""

import gzip
import http.client
import os
import re
import shutil
import subprocess
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
        page = web.live_page("split")[0].decode()
        found = {m.group(1) for m in re.finditer(pattern, page)}
        self.assertEqual(found, {f"/static/{web.build_id()}/{n}" for n in web.assets()})
        # the page as normally sent links none: it is kept on its own, and the files of a page kept before are dropped
        self.assertEqual([m.group(1) for m in re.finditer(pattern, web.live_page("inline")[0].decode())], [])
        self.assertNotIn("if (!want.length) return", sw)
        self.assertIn('startsWith("text/html")', sw)


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
        with mock.patch.object(web, "PAGE_MODE", "split"):
            r, body = self.get("/")
        return re.findall(r'(?:href|src)="(/static/[^"]+)"', body.decode())

    def test_page_names_the_current_build(self):
        with mock.patch.object(web, "PAGE_MODE", "split"):
            r, body = self.get("/", **{"Accept-Encoding": "gzip"})
            self.assertEqual((r.status, r.getheader("Cache-Control"), r.getheader("Content-Encoding")), (200, "no-cache", "gzip"))
            page = gzip.decompress(body).decode()
            self.assertNotIn("{{BUILD}}", page)
            build = web.build_id()
            self.assertRegex(build, r"^[0-9a-f]{12}$")
            self.assertIn(f'src="/static/{build}/main.js"', page)
            r2, _ = self.get("/", **{"If-None-Match": r.getheader("ETag")})
            self.assertEqual(r2.status, 304)

    def test_every_asset_from_memory_gzipped_with_etag_and_cached_for_good(self):
        urls = self.page_assets()
        self.assertEqual(len(set(urls)), len(web.assets()))
        for url in set(urls):
            name = url.rsplit("/", 1)[1]
            r, body = self.get(url, **{"Accept-Encoding": "gzip"})
            self.assertEqual(r.status, 200, url)
            self.assertEqual(r.getheader("Cache-Control"), "public, max-age=31536000, immutable")
            self.assertEqual(r.getheader("Content-Type"), "text/javascript" if name.endswith(".js") else "text/css; charset=utf-8")
            if len(web.assets()[name].read_bytes()) >= web.GZIP_MIN:
                self.assertEqual(r.getheader("Content-Encoding"), "gzip", url)
                body = gzip.decompress(body)
            self.assertEqual(body, web.assets()[name].read_bytes())
            etag = r.getheader("ETag")
            self.assertTrue(etag)
            r, body = self.get(url, **{"If-None-Match": etag})
            self.assertEqual((r.status, body), (304, b""))
            self.assertEqual(r.getheader("Cache-Control"), "public, max-age=31536000, immutable")
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

    def test_what_is_never_cached_for_good(self):
        # the addresses that stay the same across a deploy are revalidated every time; a miss is never kept
        for path in ("/", "/live", "/sw.js", "/tokens.css", "/i18n.js", "/manifest.webmanifest", "/icon.svg"):
            r, _ = self.get(path)
            self.assertEqual((r.status, r.getheader("Cache-Control")), (200, "no-cache"), path)
        r, _ = self.get(f"/static/{web.build_id()}/nope.js")
        self.assertEqual(r.getheader("Cache-Control"), "no-store")

    def test_the_page_is_one_request_and_a_repeat_visit_one_304(self):
        r, body = self.get("/", **{"Accept-Encoding": "gzip"})
        page = gzip.decompress(body).decode()
        self.assertNotRegex(page, r'(?:href|src)="/static/')  # nothing else to fetch before the page runs
        self.assertLess(len(body), 48_000)  # no more than the old single-file page (48,320 B with its two files)
        r, body = self.get("/", **{"If-None-Match": r.getheader("ETag"), "Accept-Encoding": "gzip"})
        self.assertEqual((r.status, body), (304, b""))

    def test_a_deploy_changes_the_page_at_once(self):
        tmp = Path(self.dir.name) / "static"
        shutil.copytree(web.STATIC_DIR, tmp)
        with mock.patch.object(web, "STATIC_DIR", tmp):
            r, body = self.get("/")
            etag = r.getheader("ETag")
            f = tmp / "card.js"
            f.write_text(f.read_text(encoding="utf-8") + '\nexport const deployMark = "deploy-2";\n', encoding="utf-8")
            r, body = self.get("/", **{"If-None-Match": etag})
            self.assertEqual(r.status, 200)  # the new page, not a 304
            self.assertIn(b'"deploy-2"', body)


class Bundled(unittest.TestCase):
    """The page as sent: live.html with its files written in and its modules joined into one."""
    def setUp(self):
        self.page = web.live_page("inline")[0].decode()

    def module_script(self):
        scripts = re.findall(r'<script type="module">\n(.*?)</script>', self.page, re.S)
        self.assertEqual(len(scripts), 1)
        return scripts[0]

    def test_everything_is_written_in(self):
        page = self.page
        self.assertNotIn("{{BUILD}}", page)
        self.assertNotRegex(page, r'(?:href|src)="/static/')
        self.assertNotIn("modulepreload", page)
        head, body = page.split("<body>")
        styles = re.findall(r"<style>\n(.*?)</style>", head, re.S)
        self.assertEqual(styles, [web.strip_css_comments(web._text("tokens.css")), web.strip_css_comments(web._text("app.css"))])
        self.assertNotIn("/*", "".join(styles))
        # the language and direction are set in the head, before the markup is read (Arabic on the first paint)
        self.assertIn(f"<script>\n{web.strip_js_comments(web._text('i18n.js'))}</script>", head)
        self.assertLess(head.index("<style>"), head.index("<script>"))
        # the modules last, after the markup (they run once it is parsed, as a module script does anywhere)
        self.assertTrue(body.rstrip().endswith("</script>\n</body>\n</html>"))
        self.assertIn(self.module_script(), body)
        # the markup itself is live.html's, without its notes
        markup = lambda html: re.sub(r"<!--.*?-->\n?", "", html[html.index("<body>"):html.index("</body>")], flags=re.S)
        self.assertEqual(markup(page).replace(f'<script type="module">\n{self.module_script()}</script>\n', ""), markup(PAGE))

    def test_modules_run_in_the_order_the_browser_runs_them(self):
        order, seen = [], set()

        def visit(n):  # depth first from main.js, imports in the order written, a module after its imports
            if n in seen:
                return
            seen.add(n)
            for d in IMPORT.findall(JS[n]):
                visit(d)
            order.append(n)
        visit("main.js")
        self.assertEqual(re.findall(r"^// ---- ([\w-]+\.js) ----$", self.module_script(), re.M), order)
        self.assertEqual(set(order), set(JS))

    def test_no_import_or_export_is_left_and_each_module_has_its_own_scope(self):
        js = self.module_script()
        self.assertNotRegex(js, r"(?m)^\s*(?:import|export)\b")
        self.assertEqual(len(re.findall(r"^const \$mod_\w+ = \(\(\) => \{$", js, re.M)), len(JS))
        self.assertIn("const { initLive, poll, refreshSeen } = $mod_live;", js)
        self.assertIn("const { clearETags, pollDelay, pulse: getPulse, seen } = $mod_api;", js)  # `pulse as getPulse`
        self.assertRegex(js, r"return \{[^}]*\bisIOS\b[^}]*\};")  # the second name of `export const a = …, b = …`

    def test_each_module_still_has_its_code(self):
        js = self.module_script()
        for name, src in JS.items():
            body = web.EXPORT_DECL.sub("", web.IMPORT_LINE.sub("", web.strip_js_comments(src))).strip("\n")
            self.assertIn(body, js, name)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_the_scripts_parse(self):
        with tempfile.TemporaryDirectory() as d:
            for i, (kind, src) in enumerate(re.findall(r'<script( type="module")?>\n(.*?)</script>', self.page, re.S)):
                f = Path(d) / (f"m{i}.mjs" if kind else f"c{i}.js")
                f.write_text(src, encoding="utf-8")
                r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_what_the_joiner_refuses_sends_one_file_per_module(self):
        for bad in ("export let n = 1;\n", "export default function () {}\n", 'export { $ } from "./util.js";\n',
                    'import * as u from "./util.js";\n', 'import { x } from "./nope.js";\n', 'import { L } from "./t.js";\n'):
            with tempfile.TemporaryDirectory() as d:
                tmp = Path(d) / "static"
                shutil.copytree(web.STATIC_DIR, tmp)
                f = tmp / "util.js"
                f.write_text(f.read_text(encoding="utf-8") + bad, encoding="utf-8")  # t.js imports util.js: a cycle
                with mock.patch.object(web, "STATIC_DIR", tmp), mock.patch("builtins.print"):
                    page, build = web.live_page("inline")[0].decode(), web.build_id()
                self.assertTrue(f'<script type="module" src="/static/{build}/main.js"' in page, bad)
        with self.assertRaises(web.NotBundled):
            web.bundle({"main.js": 'import { a } from "./a.js";\n', "a.js": "export const b = 1;\n"})  # not exported

    def test_a_closing_tag_in_a_file_is_not_written_in(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d) / "static"
            shutil.copytree(web.STATIC_DIR, tmp)
            f = tmp / "card.js"
            f.write_text(f.read_text(encoding="utf-8") + 'export const x = "</script>";\n', encoding="utf-8")
            with mock.patch.object(web, "STATIC_DIR", tmp), mock.patch("builtins.print"):
                page = web.live_page("inline")[0].decode()
            self.assertTrue('src="/static/' in page)


class Comments(unittest.TestCase):
    def test_only_whole_line_comments_in_code_go(self):
        src = ("// a note\nconst a = 1; // kept: after code\n  // indented note\nconst t = `line\n// in a template\n${ {k: `x\n// nested`}.k }\n`;\n"
               "const r = /\\/\\//g, q = '// a string', d = a / 2 / 1;\n// after a regex\nconst s = \"x\\\"// y\";\n"
               "if (a) { const o = { b: 1 }; }\n  // after a block\nconst f = x => x\n/* a block */\n")
        out = web.strip_js_comments(src)
        for kept in ("// kept: after code", "// in a template", "// nested", "/\\/\\//g", "'// a string'", '"x\\"// y"', "/* a block */"):
            self.assertIn(kept, out)
        for gone in ("// a note", "// indented note", "// after a regex", "// after a block"):
            self.assertNotIn(gone, out)

    def test_what_cant_be_followed_is_left_alone(self):
        for src in ("const s = 'never ends\n// x\n", "const t = `never ends\n// x\n", "const r = /never ends\n// x\n"):
            self.assertEqual(web.strip_js_comments(src), src)

    def test_css(self):
        self.assertEqual(web.strip_css_comments('/* x */a { content: "/* kept */"; }/* y */'), 'a { content: "/* kept */"; }')

    def test_every_module_is_followed_to_the_end(self):
        for name, src in {**JS, "i18n.js": web._text("i18n.js")}.items():
            out = web.strip_js_comments(src)
            self.assertNotRegex(out, r"(?m)^[ \t]*//", name)  # every whole-line comment found, so nothing was lost track of
            removed = [l for l in src.splitlines() if l not in set(out.splitlines())]
            self.assertTrue(all(l.lstrip().startswith("//") for l in removed), name)


if __name__ == "__main__":
    unittest.main()
