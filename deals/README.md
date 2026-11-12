# Live deals

Collects prices from Egyptian stores, keeps every price change, and shows what is a real deal right now
on a live page. Plain Python 3.10+, no packages to install.

```
cd deals
python -m deals run                      # collect every 3 hours and serve http://127.0.0.1:8800/
python -m deals collect --store talabat  # one collection, then exit (e.g. from cron)
python -m deals serve                    # just the page
python -m deals import-har capture.har   # read a Breadfast app capture (see below)
python -m unittest discover -s tests     # tests, from saved pages; no network
```

Useful options: `--area ID` (talabat area, which picks the branch), `--category Beverages` and
`--max-pages 20` (to try it out on a small part; talabat and amazon), `--every 60` (minutes between runs), `--db PATH`,
`--host 0.0.0.0 --port 80` (to put it on a server).

## Stores

| Store | Source | State |
|---|---|---|
| talabat mart | talabat.com's public grocery pages. Each page carries its data as JSON (`__NEXT_DATA__`): price, original price, stock. robots.txt allows it. | Works. ~10,400 products; a full run is ~550 pages, about 15 minutes. |
| Amazon.eg | Its public **best-seller lists**: 7 everyday departments by default, each with ~20 sub-lists, 60 products per list. robots.txt allows them. With API keys set, the official Product Advertising API is used instead. | Works without keys. ~300 pages at 3 s apart, about 15 minutes. |
| Breadfast | A **capture of the app's own traffic** from your phone, imported once and then refreshed automatically. | Ready for your first capture (steps below). |

### Amazon.eg without the API
- Best-seller lists show today's price but no struck-through price. So Amazon deals come from the price
  history ("usually X EGP"), and start appearing about 3 days after the first run.
- Some products show "N offers from EGP x". That's the lowest marketplace seller's price, marked
  `from_sellers` in the data.
- Amazon's search pages answer automated requests with a bot check, so search isn't used. If any page
  answers with one, the run stops, keeps what it already read, and the page shows why. It never tries to
  get past the check. Spacing requests 3 s apart and running a few times a day keeps this rare.
- Departments: `--amazon-department grocery --amazon-department beauty …`. The default is grocery,
  health, beauty, baby-products, home, pet-supplies and electronics. All 18 are listed at
  amazon.eg/gp/bestsellers.
- If you get API keys later (Amazon Associates → Product Advertising API), set `AMAZON_ACCESS_KEY`,
  `AMAZON_SECRET_KEY` and `AMAZON_PARTNER_TAG`. It then switches to the API, which includes Amazon's
  own discounts and affiliate links.

### Breadfast: capture once, then it refreshes itself
1. Install **HTTP Toolkit** on your computer (free) and connect your Android phone (the "Android device
   via ADB" option). It shows the phone's app traffic.
2. Open Breadfast and browse: open each category you care about and scroll a little in each.
3. In HTTP Toolkit, select all and **Export → HAR**.
4. `python -m deals import-har capture.har`
   It prints the products it found with their prices so you can check them. If prices are 100x too
   big, run it again with `--price-divisor 100`.
5. From then on, `collect`/`run` refresh Breadfast like the other stores. When the app session expires,
   the page shows "capture again". Repeat steps 2–4.

Notes:
- `breadfast_requests.json` holds your app session (sign-in token). It's created private, is ignored by
  git, and should be treated like a password.
- Only GET requests that returned products are repeated. Nothing whose address mentions a cart, order,
  checkout, payment, address, wallet or account is ever replayed.
- Products are recognised by shape (an id, a name and a price), since the app's layout isn't public. If
  the import finds nothing, send the HAR's product response (with the tokens removed) so the parser can
  be fitted to it.
- Breadfast's terms don't mention automated collection either way. Using your own capture is your call.

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
  stores/       talabat.py, amazon_eg.py, breadfast.py: one module per store
  models.py     Offer: one product at one store at one moment
  http.py       slow, honest fetcher: names itself, waits between requests, backs off on errors
  db.py         SQLite storage and history
  finder.py     the deal rules
  sigv4.py      AWS request signing for the Amazon API
  web.py        /, /api/deals, /api/product/ID, /api/status
  live.html     the page; refreshes every minute and highlights new deals
tests/          parsing saved real talabat and amazon pages, breadfast captures, the deal rules, signing
```

## Being a good guest
One request at a time per store, 1.5 s apart, a clear user agent, and a few runs a day are plenty for
price tracking. If a store's layout changes, the run stops with "no __NEXT_DATA__", which is recorded in
`runs.error` and shown on the page, rather than saving wrong prices.
