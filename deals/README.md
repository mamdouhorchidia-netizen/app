# Live deals

Collects prices from Egyptian stores, keeps every price change, and shows what is a real deal right now
on a live page. Plain Python 3.10+, no packages to install.

```
cd deals
python -m deals run                      # check every store continuously and serve http://127.0.0.1:8800/
python -m deals collect --store talabat  # one pass, then exit
python -m deals serve                    # just the page
python -m deals import-har capture.har   # read a Breadfast app capture (see below)
python -m unittest discover -s tests     # tests, from saved pages; no network
```

Useful options: `--area ID` (talabat area, which picks the branch), `--category Beverages` and
`--max-pages 20` (to try it out on a small part), `--db PATH`, `--host 0.0.0.0 --port 80`. Each also reads
an environment variable (`DEALS_DB`, `TALABAT_AREA`, `HOST`, `PORT`, …); the Dockerfile lists them.

## How "live" works
Each store is checked **continuously** on its own thread, one request at a time at its own pace:
talabat every 1.5 s, Amazon every 3 s, Breadfast every 5 s.
- **Every shelf** (one list page of ~20–60 products) comes round once per pass. That's about 14 minutes
  for talabat's ~550 pages and about 15 for Amazon's ~300.
- **Hot shelves**, where a price moved in the last 30 minutes, are rechecked **every 60 seconds**
  (`HOT_EVERY`). Hot and normal shelves take turns, so a busy day slows a pass down but never stalls it.
- The **page** asks for news every 10 seconds, redraws only when something changed, and shows each store's
  last change ("last change 8 s ago"), its pass time and the price changes in the last hour.
- A **bot check** (Amazon) pauses that store for 30 minutes. An **expired Breadfast session** pauses it until
  a new capture is imported.

Why not recheck every product every 10–30 seconds? One full pass of all three stores is ~900 pages. Doing
that every 30 s would be ~30 requests a second, nonstop, which gets the server's address blocked by
talabat and Amazon and your Breadfast account flagged within hours. The delays can be lowered
(`TALABAT_DELAY` etc.), but every halving doubles that risk.

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
  get past the check. Keep requests at least 3 s apart. Datacenter addresses (like a VPS) get bot checks
  more often than home connections, so watch the page's Amazon status for the first days.
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
5. From then on, `run` refreshes Breadfast like the other stores. When the app session expires, the page
   shows "capture again". Repeat steps 2–4. On the server, step 4 is `import_breadfast.sh` (below).

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
changes, so history stays small), `live_status` (each store's continuous checking) and `runs` (one-off
collections).

## Layout
```
deals/
  __main__.py   the command line: run / collect / serve / import-har
  scheduler.py  continuous checking: round robin, hot shelves, pauses
  tasks.py      a store as pages to check (a page can discover more pages)
  stores/       talabat.py, amazon_eg.py, breadfast.py: one module per store
  models.py     Offer: one product at one store at one moment
  http.py       slow, honest fetcher: names itself, waits between requests, backs off on errors
  db.py         SQLite storage and history
  finder.py     the deal rules
  sigv4.py      AWS request signing for the Amazon API
  web.py        /, /api/deals, /api/product/ID, /api/status, /healthz
  live.html     the page; refreshes every 10 s and highlights new deals
tests/          saved real talabat and amazon pages, breadfast captures, deal rules, scheduler, signing
Dockerfile      the server image (port 8800, volume /data)
deploy/         add_deals.sh, update.sh, import_breadfast.sh: the Vorath server scripts
```

## On the Vorath server (deals.vorath.cloud)
It runs as one more Coolify app next to Vorath, on the same server. Like Vorath's apps, it gets HTTPS,
nightly encrypted backups (`backup.sh` covers every Coolify app) and Telegram alerts (`uptime.sh`). Vorath's
release poller ignores it, because it only deploys apps built from the vorath repository.

1. **DNS.** In hPanel › Domains › vorath.cloud › DNS, add an **A record**: name `deals`, pointing at the
   server's IP (the same as panel.vorath.cloud's).
2. **Deploy key** (server console, as root). This repository needs its own key; GitHub doesn't allow
   reusing Vorath's.
   ```bash
   ssh-keygen -t ed25519 -N '' -C deals-deploy -f /root/vorath/deals_deploy_key && cat /root/vorath/deals_deploy_key.pub
   ```
   Add the printed line on GitHub › this repository › Settings › Deploy keys (read-only).
3. **Get the code and create the app.**
   ```bash
   GIT_SSH_COMMAND="ssh -i /root/vorath/deals_deploy_key -o StrictHostKeyChecking=accept-new" \
     git clone --depth 1 git@github.com:mamdouhorchidia-netizen/app.git /root/vorath/deals-src
   bash /root/vorath/deals-src/deals/deploy/add_deals.sh deals.vorath.cloud main
   ```
   This creates the Coolify app "deals" (port 8800, `/healthz`, volume `/data`, HTTPS), deploys it, and
   adds a 5-minute cron job that deploys every new commit on `main`.
4. **Open https://deals.vorath.cloud.** The first prices appear within a minute, and a full pass over
   talabat and Amazon takes about 15 minutes. Price-drop deals need about 3 days of history; talabat's own
   discounts show right away.
5. **Breadfast.** Capture on your phone (above), copy the file to the server
   (`scp capture.har root@<server>:/root/`), then:
   ```bash
   bash /root/vorath/deals-src/deals/deploy/import_breadfast.sh /root/capture.har
   ```
   Breadfast joins the page within a minute, and the capture file is deleted after the import.

Settings, all optional, in Coolify › deals › Environment variables (redeploy after a change):
`TALABAT_AREA`, `TALABAT_DELAY`, `AMAZON_DELAY`, `BREADFAST_DELAY`, `HOT_EVERY`, and the three
`AMAZON_*` API keys.

Day to day: push to `main` and it's live within ~10 minutes (log: `/root/vorath/deals/log`). The
container's logs are in Coolify. The data is in the `deals-data` volume.

## Being a good guest
One request at a time per store, at its own pace, with a clear user agent. If a store's layout changes,
its pages fail with "no __NEXT_DATA__". The error shows on the page, and nothing wrong is saved.
