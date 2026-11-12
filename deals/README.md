# Live deals

Collects prices from Egyptian stores, keeps every price change, and shows what is a real deal right now
on a live page. Plain Python 3.10+, no packages to install.

```
cd deals
python -m deals run                      # collect every 3 hours and serve http://127.0.0.1:8800/
python -m deals collect --store talabat  # one collection, then exit (e.g. from cron)
python -m deals serve                    # just the page
python -m unittest discover -s tests     # tests, from saved pages; no network
```

Useful options: `--area ID` (talabat area, which picks the branch), `--category Beverages` and
`--max-pages 20` (to try it out on a small part), `--every 60` (minutes between runs), `--db PATH`,
`--host 0.0.0.0 --port 80` (to put it on a server).

## Stores

| Store | Source | State |
|---|---|---|
| talabat mart | talabat.com's public grocery pages. Each page carries its data as JSON (`__NEXT_DATA__`): price, original price, stock. robots.txt allows it. | Works. ~10,400 products, a full run is ~550 pages, about 15 minutes at one request per 1.5 s. |
| Amazon.eg | Amazon's Product Advertising API (PA-API 5). The website answers automated requests with a bot check, so it is not scraped. | Built and tested offline; needs your keys (below). |
| Breadfast | Its prices exist only inside the phone app: breadfast.com is a marketing site and app.breadfast.com only says "download the app". | Not built. See below. |

### Amazon.eg keys
Join Amazon Associates for amazon.eg, then request Product Advertising API access in Associates Central.
Set three environment variables:

```
export AMAZON_ACCESS_KEY=...  AMAZON_SECRET_KEY=...  AMAZON_PARTNER_TAG=yourtag-21
```

Links on the page then carry your tag, so purchases through it pay you commission. New accounts get about
1 request per second and a daily limit that grows with sales. Amazon has been moving PA-API users to newer
offer fields ("OffersV2") and a newer "Creators API". The parser reads both offer shapes. If Amazon has
retired PA-API 5 for your account, `stores/amazon_eg.py` is the only file to change. What to search is in
`DEFAULT_SEARCHES` in that file.

### Breadfast
There is no public page with Breadfast prices. The options are:
1. **Ask Breadfast** for a product feed or a partnership. This is how comparison sites stay safe long-term.
2. **Read the app's traffic on your own phone**, with a tool like HTTP Toolkit, to see what it loads.
   Note that Breadfast's terms likely forbid automated collection. That decision, and the risk, is yours.
   Once you know the source, a new store is one file in `deals/stores/` with `STORE` and a `collect()`
   that yields `Offer`s.

## How deals are decided (`deals/finder.py`)
A product is a deal when it is in stock and at least N% (5 by default) under either:
- the struck-through price the store shows, or
- its **usual price**: the time-weighted median over the last 30 days, once it has been tracked for 3+ days.

Labels: *lowest in 90 days*, *dropped X ago*, and *same price for a while*. The last one means the store
shows a discount but the price hasn't moved in 14+ days, so the old price may never have been charged.

The database (`deals.sqlite3`) has `products` (latest state), `prices` (a row each time a price or stock
changes, so history stays small) and `runs` (each collection, and any error).

## Layout
```
deals/
  __main__.py   the command line: collect / serve / run
  stores/       talabat.py, amazon_eg.py: one module per store
  models.py     Offer: one product at one store at one moment
  http.py       slow, honest fetcher: names itself, waits between requests, backs off on errors
  db.py         SQLite storage and history
  finder.py     the deal rules
  sigv4.py      AWS request signing for the Amazon API
  web.py        /, /api/deals, /api/product/ID, /api/status
  live.html     the page; refreshes every minute and highlights new deals
tests/          parsing from saved real talabat pages, the deal rules, signing (AWS's own test vector)
```

## Being a good guest
One request at a time per store, 1.5 s apart, a clear user agent, and a few runs a day are plenty for
price tracking. If a store's layout changes, the run stops with "no __NEXT_DATA__", which is recorded in
`runs.error` and shown on the page, rather than saving wrong prices.
