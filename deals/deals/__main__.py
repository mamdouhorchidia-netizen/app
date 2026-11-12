"""python -m deals run | collect | serve | import-har FILE   (python -m deals -h for the options)"""

import argparse
import json
import os
import sys
import threading
import time
import traceback

from . import db, settings, web
from .http import Blocked, Fetcher
from .models import NotConfigured
from .scheduler import StoreLoop
from .stores import LABELS, STORES, amazon_eg, breadfast, talabat


def collect_store(db_path: str, store: str, args) -> tuple[int, int]:
    con = db.connect(db_path)
    run = db.start_run(con, store)
    seen = changed = 0
    batch = []
    fetcher = Fetcher(delay=args.delay)
    try:
        mod = STORES[store]
        if store == "talabat":
            gen = mod.collect(fetcher, hubs=settings.load(settings.path_for(db_path))["talabat_hubs"],
                              max_pages=args.max_pages, categories=args.category)
        elif store == "amazon_eg":
            gen = mod.collect(fetcher, pages=args.amazon_pages, departments=args.amazon_department,
                              max_pages=args.max_pages)
        else:
            gen = mod.collect(fetcher, requests_file=args.breadfast_requests)
        for offer in gen:
            batch.append(offer)
            if len(batch) >= 200:
                s, c = db.save_offers(con, batch); seen += s; changed += c; batch = []
        s, c = db.save_offers(con, batch); seen += s; changed += c
        db.finish_run(con, run, seen, changed)
        print(f"{store}: {seen} products read, {changed} new or changed")
    except NotConfigured as e:
        with con:  # not set up is not a failed run: leave no trace on the page
            con.execute("DELETE FROM runs WHERE id=?", (run,))
        print(f"{store}: skipped, {e}")
    except Blocked as e:  # keep what was read before the bot check, and say so on the page
        s, c = db.save_offers(con, batch); seen += s; changed += c
        db.finish_run(con, run, seen, changed, str(e))
        print(f"{store}: {e} ({seen} products read before that)", file=sys.stderr)
    except Exception as e:
        s, c = db.save_offers(con, batch); seen += s; changed += c
        db.finish_run(con, run, seen, changed, f"{type(e).__name__}: {e}")
        print(f"{store}: failed after {seen} products: {e}", file=sys.stderr)
        traceback.print_exc()
    finally:
        con.close()
    return seen, changed


def import_har(args):
    r = breadfast.import_capture(args.db, args.har, args.breadfast_requests, args.price_divisor)
    if not r["products"]:
        print("No products found in that capture. Browse a few Breadfast categories with the capture running,"
              " then export again.")
        return 1
    print(f"breadfast: {r['products']} products saved; {r['requests']} product requests kept in"
          f" {args.breadfast_requests} (it holds your app session: keep it private)")
    if not r["requests"]:
        print("None of the product responses can be refreshed (they were cart/account requests): prices won't update.")
    print("Check that these prices look right (if they are 100x too big, import again with --price-divisor 100):")
    for o in r["samples"]:
        was = f" (was {o['original_price']:g})" if o["original_price"] else ""
        print(f"  {o['price']:>9g} EGP{was}  {o['name']}")
    return 0


def live(args, stores):
    """Every store checked continuously on its own thread, and the page served."""
    def plans(store):
        if store == "talabat":  # the hubs chosen on /admin, read again whenever the settings change
            return lambda f: talabat.plan(f, settings.load(settings_file)["talabat_hubs"], args.category)
        if store == "amazon_eg":
            return lambda f: amazon_eg.plan(f, args.amazon_department, args.amazon_pages)
        return lambda f: breadfast.plan(f, args.breadfast_requests)

    settings_file = settings.path_for(args.db)
    delays = {"talabat": args.talabat_delay, "amazon_eg": args.amazon_delay, "breadfast": args.breadfast_delay}
    db.connect(args.db).close()
    for store in stores:
        loop = StoreLoop(store, plans(store), args.db, delay=delays[store], hot_every=args.hot_every,
                         watch_file=_requests_file(args) if store == "breadfast" else None,
                         replan_file=settings_file if store == "talabat" else None)
        threading.Thread(target=loop.run_forever, name=store, daemon=True).start()
    if "talabat" in stores:  # which hub serves which area: looked up slowly, once a week
        def hubs_job():
            path = os.path.join(os.path.dirname(os.path.abspath(args.db)), "talabat_hubs.json")
            while True:
                try:
                    if os.path.exists(path) and time.time() - os.path.getmtime(path) > 7 * 86400:
                        os.replace(path, path + ".old")  # a week old: look every area up again
                    if not os.path.exists(path) or len(json.load(open(path, encoding="utf-8")).get("done") or []) < 1500:
                        talabat.discover_hubs(Fetcher(delay=3), path)
                except Exception as e:
                    print(f"talabat hubs: {type(e).__name__}: {e}")
                time.sleep(6 * 3600)
        threading.Thread(target=hubs_job, name="talabat-hubs", daemon=True).start()
    try:
        from . import push
        push.start(args.db, LABELS)  # price-drop notifications, checked every minute
    except ImportError:
        print("push notifications off: the cryptography package isn't installed")
    web.serve(args.db, args.host, args.port, args.breadfast_requests)


def _requests_file(args) -> str:
    return args.breadfast_requests or os.environ.get("BREADFAST_REQUESTS") or breadfast.DEFAULT_REQUESTS_FILE


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m deals")
    p.add_argument("command", choices=["run", "collect", "serve", "import-har"],
                   help="run = check every store continuously and serve the live page; collect = one pass and exit;"
                        " serve = only the page; import-har FILE = read a Breadfast app capture")
    p.add_argument("har", nargs="?", help="import-har: the .har file exported from your capture tool")
    p.add_argument("--db", default=os.environ.get("DEALS_DB", "deals.sqlite3"))
    p.add_argument("--store", action="append", choices=sorted(STORES), help="default: all stores")
    p.add_argument("--category", action="append", help="talabat: only these top-level categories")
    p.add_argument("--max-pages", type=int, help="collect: stop after this many pages (for trying it out)")
    p.add_argument("--amazon-department", action="append",
                   help="amazon best-seller departments, e.g. grocery, beauty (default: everyday ones)")
    p.add_argument("--amazon-pages", type=int, default=3, help="amazon API: result pages per search (max 10)")
    p.add_argument("--breadfast-requests", help=f"default: $BREADFAST_REQUESTS or {breadfast.DEFAULT_REQUESTS_FILE}")
    p.add_argument("--price-divisor", type=float, default=1, help="import-har: 100 if the app's prices are in piasters")
    p.add_argument("--delay", type=float, default=1.5, help="collect: seconds between requests to a store")
    p.add_argument("--talabat-delay", type=float, default=float(os.environ.get("TALABAT_DELAY", 1.5)),
                   help="run: seconds between talabat requests (default 1.5)")
    p.add_argument("--amazon-delay", type=float, default=float(os.environ.get("AMAZON_DELAY", 8)),
                   help="run: seconds between Amazon requests (default 8; Amazon limits busy addresses)")
    p.add_argument("--breadfast-delay", type=float, default=float(os.environ.get("BREADFAST_DELAY", 5)),
                   help="run: seconds between Breadfast requests (your own account: keep it gentle)")
    p.add_argument("--hot-every", type=float, default=float(os.environ.get("HOT_EVERY", 60)),
                   help="run: recheck a page whose prices moved in the last 30 min every N seconds")
    p.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8800)))
    args = p.parse_args(argv)
    args.breadfast_requests = _requests_file(args)
    stores = args.store or sorted(STORES)

    if args.command == "import-har":
        if not args.har:
            p.error("import-har needs the .har file")
        return import_har(args)
    if args.command == "collect":
        for s in stores:
            collect_store(args.db, s, args)
    elif args.command == "serve":
        web.serve(args.db, args.host, args.port, args.breadfast_requests)
    else:
        live(args, stores)


if __name__ == "__main__":
    sys.exit(main())
