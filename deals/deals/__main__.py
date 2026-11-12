"""python -m deals collect | serve | run   (python -m deals -h for the options)"""

import argparse
import sys
import threading
import time
import traceback

from . import db, web
from .http import Fetcher
from .stores import STORES, amazon_eg


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
        else:
            gen = mod.collect(fetcher, pages=args.amazon_pages)
        for offer in gen:
            batch.append(offer)
            if len(batch) >= 200:
                s, c = db.save_offers(con, batch); seen += s; changed += c; batch = []
        s, c = db.save_offers(con, batch); seen += s; changed += c
        db.finish_run(con, run, seen, changed)
        print(f"{store}: {seen} products read, {changed} new or changed")
    except amazon_eg.NotConfigured as e:
        with con:  # not set up is not a failed run: leave no trace on the page
            con.execute("DELETE FROM runs WHERE id=?", (run,))
        print(f"{store}: skipped, {e}")
    except Exception as e:
        s, c = db.save_offers(con, batch); seen += s; changed += c
        db.finish_run(con, run, seen, changed, f"{type(e).__name__}: {e}")
        print(f"{store}: failed after {seen} products: {e}", file=sys.stderr)
        traceback.print_exc()
    finally:
        con.close()
    return seen, changed


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m deals")
    p.add_argument("command", choices=["collect", "serve", "run"],
                   help="collect once; serve the live page; or run = serve and collect every --every minutes")
    p.add_argument("--db", default="deals.sqlite3")
    p.add_argument("--store", action="append", choices=sorted(STORES), help="default: all stores")
    p.add_argument("--area", type=int, default=9838, help="talabat area id (picks the talabat mart branch)")
    p.add_argument("--category", action="append", help="talabat: only these top-level categories")
    p.add_argument("--max-pages", type=int, help="talabat: stop after this many pages (for trying it out)")
    p.add_argument("--amazon-pages", type=int, default=3, help="amazon: result pages per search (max 10)")
    p.add_argument("--delay", type=float, default=1.5, help="seconds between requests to a store")
    p.add_argument("--every", type=int, default=180, help="run: minutes between collections")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8800)
    args = p.parse_args(argv)
    stores = args.store or sorted(STORES)

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
    main()
