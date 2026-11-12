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
an environment variable (`DEALS_DB`, `HOST`, `PORT`, …); the Dockerfile lists them.

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
| Amazon.eg | **Today's Deals**, rendered in headless Chrome: Amazon's own deal price, list price, "% off" and event label. Plus the public **best-seller lists** at every level (department › list › sub-list…) in 17 departments (all but gift cards), 60 products per list. robots.txt allows both. With API keys set, the official Product Advertising API is used instead. | Works without keys. ~70 live deals per render, and a few thousand list pages at 3 s apart: a full sweep takes hours, while busy shelves are rechecked every minute. |
| Jumia | Its public **Flash Sales** and main category pages (JSON in each page). Jumia's robots.txt allows bots that identify themselves with an owner address and stay under 200 requests a minute; this one sends 1 every 3 s (`JUMIA_DELAY`). Jumia sits behind Cloudflare: a "Just a moment…" check pauses it like Amazon's bot check. | Built; from this sandbox Cloudflare challenged it after a few pages, so whether it runs from the server shows on the site's status line. |
| Breadfast | A **capture of the app's own traffic** from your phone, imported once and then refreshed automatically. | Ready for your first capture (steps below). |

### Amazon.eg without the API
- **Today's Deals** is built by JavaScript, so it's opened in headless Chrome (`deals/browser.py`) once per
  sweep. Chrome runs as itself: its own headless identity, no disguise, TLS checks on. A bot check stops it
  like any other page. The Docker image includes Chromium. Elsewhere, set `CHROME_BIN` or install Chrome;
  without Chrome, this part is skipped.
- Best-seller lists show today's price but no struck-through price. Their deals come from the price
  history ("usually X EGP") and start about 3 days after the first run. A list price seen on Today's
  Deals is kept while the price stays the same.
- Some products show "N offers from EGP x". That's the lowest marketplace seller's price, marked
  `from_sellers` in the data.
- Amazon's search pages answer automated requests with a bot check, so search isn't used. If any page
  answers with one, the run stops, keeps what it already read, and the page shows why. It never tries to
  get past the check. Keep requests at least 3 s apart. Datacenter addresses (like a VPS) get bot checks
  more often than home connections, so watch the page's Amazon status for the first days.
- Departments: `--amazon-department grocery --amazon-department beauty …`. The default is all 17 except gift
  cards, every level of their lists (`MAX_DEPTH` in `stores/amazon_eg.py`). Fewer departments means faster
  sweeps and fewer bot checks.
- If you get API keys later (Amazon Associates → Product Advertising API), set `AMAZON_ACCESS_KEY`,
  `AMAZON_SECRET_KEY` and `AMAZON_PARTNER_TAG`. It then switches to the API, which includes Amazon's
  own discounts and affiliate links.

### Breadfast: capture once, then it refreshes itself
1. Install **HTTP Toolkit** on your computer (free) and connect your Android phone (the "Android device
   via ADB" option). It shows the phone's app traffic.
2. Open Breadfast and browse: open each category you care about and scroll a little in each.
3. In HTTP Toolkit, select all and **Export → HAR**.
4. `python -m deals import-har capture.har`, or upload it on the site's `/admin` page
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

## The page
- **Arabic first, right to left** (Egyptian wording: "نزل دلوقتي", "🔥 أقل سعر", "من 4 د"), with English one tap
  away (the ع / EN button, remembered per browser). `deals/i18n.js` translates what the pages show as it
  appears; product names are left as the store wrote them.
- **3rrood-style pages:** `/p/ID` is each product's own page (price history chart, lowest/highest in 90 days,
  the verdict and why, the same product in other stores, more deals like it, and a store button); shared on
  WhatsApp or Telegram it shows a preview card with the picture, price and discount in Arabic. `/stores`
  (deals per store, "خصومات لحد X%"), `/store/ID`, `/cats` (categories with deal counts), `/coupons`
  (tap a code to copy it; the owner keeps the list on /admin), and "كمّل من حيث وقفت" (recently viewed,
  kept in the browser).
- **Every listing opens at that product.** Amazon and Jumia: on Android the link names the app
  (`com.amazon.mShop.android.shopping`, `com.jumia.android`) and the product's address, and falls back to the
  web page if the app isn't installed; on iPhone the product's address opens in the app when the app claims
  it. **talabat** opens its product page in Chrome: its app can't open a product from a link (its pages
  declare only `talabat://`), so handing it one showed "We can't load this page". The web page works when
  the link's area (`aid`) is one the item's hub delivers to, which is how every link here is built.
  Breadfast uses the share or deep link from its own data when the capture has one, and otherwise opens
  the app's home screen.
- **Filters:** deals or all products; platform (with counts); discount type (platform discount or
  price drop); discount % range; availability (in stock, out, all); status (real deals only, changed in
  24 h, lowest in 90 days, store event deals, back in stock, sold in other stores too, cheapest across
  stores, my watchlist); category; price range; search (English or Arabic).
- **Sort:** best deals (deal score), biggest %, biggest saving, just changed, biggest drop vs usual,
  price either way, price per kg / L / piece, best sellers, name.
- **Deal score (0–100)** on each card, and in the product view a plain list of *why*: "31% off the store's
  own price", "lowest price in 90 days", "cheapest of the stores that sell it"… Discounts whose price
  never moves are marked and scored down.
- **Same product in other stores:** matched by pack size, brand and name (strict: a can isn't a bottle,
  Zero isn't regular, Latte isn't Mocha). Cards say "39.99 at Amazon.eg" or "cheapest of all stores", and
  the product view compares them side by side with an open button for each.
- **Price per kg / litre / piece**, read from names like "250ml X 3 + 1 Free" or "6x300ml".
- **Price history:** a sparkline on every card, and a full chart you can point at in the product view.
- **Watchlist and price alerts:** ★ any product, optionally with a target price. The page checks every
  minute, shows a toast, and sends a browser notification when the page is in the background. The
  watchlist is kept in that browser.
- **"Just dropped" ticker:** the latest price moves scroll under the header; tap one to open it.
- **Live:** refreshes every 10 s, redraws only what changed, and highlights new cards. Each store's last
  change and sweep time is in the header.
- **Shareable:** every filter is in the address, and `/?p=ID` opens a product. There's a Share button
  (the phone's share sheet, or copies the link).
- **Installs like an app** (manifest and icon), works on phones (filters become a bottom sheet), has a
  dark mode, and `/` jumps to search.

## talabat hubs
talabat mart is many small stores ("hubs"), each serving nearby areas, and **each stocks different products**:
on one shelf only 3 of the Obour hub's 20 products were also in Maadi's. talabat's website always shows the
Maadi Old hub, so the site follows the hubs chosen on **/admin › talabat mart hubs** (search by hub, area or
city, e.g. *Obour*). Each hub is tracked separately, and its name shows on the cards and in a "talabat hub"
filter. A hub that isn't listed can be added by pasting any talabat mart link from a browser where your
address is set (`talabat.com/egypt/grocery/<hub>/…`).
The list of hubs comes from talabat's own per-area pages (`/egypt/groceries/<area>/<slug>` names the hub
serving it). A list is shipped with the app, and the server looks every area up again once a week, slowly
(1 page every 3 s), saving `talabat_hubs.json` next to the database. Each hub adds about 15 minutes to a
talabat sweep; up to 10 can be chosen.

## Alerts, install and the smart parts
- **Install:** Android Chrome shows **⬇︎ Install** (or ⋮ › Install app). On iPhone: Safari › Share ›
  **Add to Home Screen**. The site has a manifest, icons and a service worker (`sw.js`).
- **Price-drop alerts (🔔):** real Web Push, so they arrive with the site closed. Choose what to hear about:
  your ★ watchlist (with target prices), and/or any drop of at least N %, optionally only some stores,
  hubs, categories or words. At most one bundle every 5 minutes. Notifications buzz in their own pattern.
  The custom **chime** plays while the site is open; browsers don't allow custom sounds on closed-site
  notifications. iPhone needs the installed app (iOS 16.4+). The signing key is `vapid.json` next to the
  database: keep it, or every device has to turn alerts on again.
- **Plain-language and voice search:** "milk under 50 at talabat", "cheapest pepsi per liter",
  "حفاضات أقل من ٣٠٠ من طلبات", "nescafe deals 30% off on amazon". What was understood shows under the box;
  🎤 searches by voice (Arabic or English, from the phone's language).
- **Buy now / Good price / Wait / Cheaper elsewhere:** a verdict on every product from its history and the
  other stores, with the reason in the product view.
- **Basket (🧺):** add products; it picks the cheapest store for each one (using the same product found in
  other stores) and shows the total and the saving.
- **Same-size pictures:** every product picture fills the same square, whatever size the store sent.
- **Unit prices** per L / kg / piece, and per 100 ml / 100 g for small packs.

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
  browser.py    headless Chrome for JavaScript-built pages (Amazon's Today's Deals)
  applinks.py   links that open a product in its store's app
  units.py      pack sizes from names, for price per kg / L / piece
  matching.py   the same product in different stores
  web.py        /, /api/deals (filters, sorts, counts), /api/changes, /api/product/ID, /api/status, /healthz,
                /admin (hubs, Breadfast), /api/push/* (alerts), /sw.js, icons
  push.py       Web Push: VAPID keys, RFC 8291 encryption, the minute-by-minute alert sender
  settings.py   the owner's settings (talabat hubs), kept next to the database
  live.html     the page
tests/          saved real talabat and amazon pages (best sellers and Today's Deals), breadfast captures,
                deal rules, filters, matching, app links, scheduler, signing
Dockerfile      the server image (port 8800, volume /data)
deploy/         add_deals.sh, update.sh, import_breadfast.sh: the Vorath server scripts
```

## On the Vorath server (deals.vorath.cloud)
It runs as one more Coolify app next to Vorath, on the same server. Like Vorath's apps, it gets HTTPS,
nightly encrypted backups (`backup.sh` covers every Coolify app) and Telegram alerts (`uptime.sh`). Vorath's
release poller ignores it, because it only deploys apps built from the vorath repository.

1. **DNS.** In hPanel › Domains › vorath.cloud › DNS, add an **A record**: name `deals`, pointing at the
   server's IP (the same as panel.vorath.cloud's).
2. **Key, code and app** (server console, as root). Paste the first line; it prints a key:
   ```bash
   K=/root/vorath/deals_deploy_key; [ -f $K ] || ssh-keygen -t ed25519 -N '' -C deals-deploy -f $K; cat $K.pub
   ```
   Add the printed line on GitHub › mamdouhorchidia-netizen/app › Settings › Deploy keys (title `deals`,
   read-only). This repository needs its own key; GitHub doesn't allow reusing Vorath's. Then paste:
   ```bash
   GIT_SSH_COMMAND="ssh -i /root/vorath/deals_deploy_key -o StrictHostKeyChecking=accept-new" \
     git clone --depth 1 git@github.com:mamdouhorchidia-netizen/app.git /root/vorath/deals-src \
     && bash /root/vorath/deals-src/deals/deploy/add_deals.sh deals.vorath.cloud
   ```
   This creates the Coolify app "deals" (port 8800, `/healthz`, volume `/data`, HTTPS), deploys it, and adds
   a 5-minute cron job that deploys every new commit on the repository's default branch (the one GitHub
   shows first; pass another branch name as the second argument to follow that one instead).
3. **Open https://deals.vorath.cloud.** The first prices appear within a minute, and a full pass over
   talabat and Amazon takes about 15 minutes. Price-drop deals need about 3 days of history; talabat's own
   discounts show right away.
4. **Breadfast.** In Coolify › deals › Environment variables, add `DEALS_ADMIN_TOKEN` with a long random value
   (mark it as a secret, then Redeploy). Then open **https://deals.vorath.cloud/admin** on your phone. It
   explains how to record the app (iPhone: Proxyman; Android: an emulator with HTTP Toolkit) and takes the
   recording. Breadfast joins the page within a minute. The recording is deleted as soon as it's read.
   On the free plan of HTTP Toolkit, where whole recordings may not export, copy single product-list requests
   **as cURL** and paste them in the page's second box, one category at a time. Only GET requests to a
   Breadfast address are accepted.
   From the server console instead: `bash /root/vorath/deals-src/deals/deploy/import_breadfast.sh /root/capture.har`.

Settings, all optional, in Coolify › deals › Environment variables (redeploy after a change):
`TALABAT_DELAY`, `AMAZON_DELAY`, `BREADFAST_DELAY`, `HOT_EVERY`, `DEALS_ADMIN_TOKEN` (turns on
`/admin`), and the three `AMAZON_*` API keys. Also:
- `PUBLIC_URL` the site's address, used in shared links and Telegram posts (`https://deals.vorath.cloud`).
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`: post the big fresh deals to a Telegram channel, like 3rrood's.
  Make a bot with @BotFather, add it to the channel as an admin, and use the channel's `@name`.
  `TELEGRAM_MIN_PCT` (40) and `TELEGRAM_PER_HOUR` (10) tune it; a product is posted again only at a lower price.
- `AMAZON_TAG`: your Amazon Associates tag, added to every Amazon link (`?tag=…`), as 3rrood does.

Day to day: merge into the default branch and it's live within ~10 minutes (log: `/root/vorath/deals/log`). The
container's logs are in Coolify. The data is in the `deals-data` volume.

## Being a good guest
One request at a time per store, at its own pace, with a clear user agent. If a store's layout changes,
its pages fail with "no __NEXT_DATA__". The error shows on the page, and nothing wrong is saved.
