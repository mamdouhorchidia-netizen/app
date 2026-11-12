"""python -m deals collect | serve | run | import-har FILE   (python -m deals -h for the options)"""

import argparse
import sys
import threading
import time
import traceback

from . import db, web
from .http import Blocked, Fetcher
from .models import NotConfigured
from .stores import STORES, breadfast


def _store_offers(con, offers) -> tuple[int, int]:
    seen = changed = 0
    batch = []
    for offer in offers:
        batch.append(offer)
        if len(batch) >= 200:
            s, c = db.save_offers(con, batch); seen += s; changed += c; batch = []
    s, c = db.save_offers(con, batch)
    return seen + s, changed + c


def collect_store(db_path: str, store: str, args) -> tuple[int, int]:
    con = db.connect(db_path)
    run = db.start_run(con, store)
    seen = changed = 0
    batch = []
    fetcher = Fetcher(delay=args.delay)
    try:
        mod = STORES[store]
        if store == "talabat":
            gen = mod.collect(fetcher, area_id=args.area, max_pages=args.max_pages, categories=args.category)
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
    divisor = args.price_divisor
    offers, requests = breadfast.read_har(args.har, divisor)
    if not offers:
        print("No products found in that capture. Browse a few Breadfast categories with the capture running,"
              " then export again.")
        return 1
    con = db.connect(args.db)
    run = db.start_run(con, breadfast.STORE)
    unique = list({o.sku: o for o in offers}.values())
    seen, changed = _store_offers(con, unique)
    db.finish_run(con, run, seen, changed)
    con.close()
    out = args.breadfast_requests or breadfast.DEFAULT_REQUESTS_FILE
    breadfast.save_requests(out, requests, divisor)
    print(f"breadfast: {len(unique)} products saved; {len(requests)} product requests kept in {out}"
          " (it holds your app session: keep it private)")
    print("Check that these prices look right (if they are 100x too big, import again with --price-divisor 100):")
    for o in unique[:8]:
        was = f" (was {o.original_price:g})" if o.original_price else ""
        print(f"  {o.price:>9g} EGP{was}  {o.name}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m deals")
    p.add_argument("command", choices=["collect", "serve", "run", "import-har"],
                   help="collect once; serve the live page; run = serve and collect every --every minutes;"
                        " import-har FILE = read a Breadfast app capture")
    p.add_argument("har", nargs="?", help="import-har: the .har file exported from your capture tool")
    p.add_argument("--db", default="deals.sqlite3")
    p.add_argument("--store", action="append", choices=sorted(STORES), help="default: all stores")
    p.add_argument("--area", type=int, default=9838, help="talabat area id (picks the talabat mart branch)")
    p.add_argument("--category", action="append", help="talabat: only these top-level categories")
    p.add_argument("--max-pages", type=int, help="talabat/amazon: stop after this many pages (for trying it out)")
    p.add_argument("--amazon-department", action="append",
                   help="amazon best-seller departments, e.g. grocery, beauty (default: everyday ones)")
    p.add_argument("--amazon-pages", type=int, default=3, help="amazon API: result pages per search (max 10)")
    p.add_argument("--breadfast-requests", help=f"default: {breadfast.DEFAULT_REQUESTS_FILE}")
    p.add_argument("--price-divisor", type=float, default=1, help="import-har: 100 if the app's prices are in piasters")
    p.add_argument("--delay", type=float, default=1.5, help="seconds between requests to a store")
    p.add_argument("--every", type=int, default=180, help="run: minutes between collections")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8800)
    args = p.parse_args(argv)
    stores = args.store or sorted(STORES)

    if args.command == "import-har":
        if not args.har:
            p.error("import-har needs the .har file")
        return import_har(args)
    if args.command == "collect":
        for s in stores:
            collect_store(args.db, s, args)
    elif args.command == "serve":
        web.serve(args.db, args.host, args.port)
    else:
        def loop(store):
            while True:
                started = time.time()
                collect_store(args.db, store, args)
                time.sleep(max(60, args.every * 60 - (time.time() - started)))
        for s in stores:  # each store on its own thread, so a slow one doesn't hold the others back
            threading.Thread(target=loop, args=(s,), daemon=True).start()
        web.serve(args.db, args.host, args.port)


if __name__ == "__main__":
    sys.exit(main())
