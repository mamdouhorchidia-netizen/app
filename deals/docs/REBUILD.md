# Rebuild: faster, a smart 30-second refresh, a better interface

Baseline on the live site, 20 Nov 2026 (110,656 products in 6 stores):

| What | Today | Why |
|---|---|---|
| `/api/deals` when the catalogue is stale | up to **8 s**, sometimes an empty reply | every 15 s one visitor's request rebuilds the whole catalogue (all products, all price history, cross-store matching) while everyone waits behind a lock |
| `/api/deals?limit=60` warm | 0.4–0.9 s, **165 KB** of JSON (25 KB gzipped) | ~2.7 KB per card: long app links, 25-point sparklines, fields the card never shows |
| The page | asks for the full list every **10 s**, the ticker every 15 s, the brief every 60 s | nothing tells it whether anything changed |
| Every response | `Cache-Control: no-store`, no ETag, a new connection per request (HTTP/1.0) | |

## Targets

1. No request ever waits for a catalogue build. `/api/deals` answers in **under 50 ms** of server time, `/api/pulse` in under 5 ms, with 110k products.
2. A catalogue refresh after a few hundred price changes takes **under 1 s** (incremental); a full build runs in the background only.
3. The page refreshes **every 30 s**, and a refresh with nothing new costs **under 1 KB**. The 60-card list is at most half today's bytes.
4. Repeat visits paint from the device (service worker) before the network answers.
5. A calmer, more premium interface, Arabic first, that is the same on the live page and the server-written pages.

## Workstreams (one owner each; nobody edits another owner's files)

| | Owner of | Does |
|---|---|---|
| **Engine** | `deals/web.py`, `deals/finder.py`, `deals/db.py`, `deals/forecast.py`, `deals/matching.py`, `deals/__main__.py`, new `deals/catalog.py`, `tests/test_deals.py`, new `tests/test_engine.py`, new `tools/bench.py` | background + incremental catalogue, the API below, caching headers, HTTP/1.1 keep-alive, indexes, a benchmark with a synthetic 110k-product database |
| **Live page** | `deals/live.html`, `deals/i18n.js`, `deals/sw.js`, new `tests/test_live_page.py` | the 30 s pulse loop, patching only changed cards, the service worker shell, the interface |
| **Pages** | `deals/pages.py`, `deals/admin.html`, new `tests/test_pages.py` | the same interface on `/p/ID`, `/stores`, `/cats`, `/coupons`, About/Privacy/Contact, `/admin`; light pages |

Everything a visitor can do today must still work (filters, sorts, Arabic/English, watchlist, basket, alerts, hub bar, brief,
ticker, product dialog, share, install, coupons, admin). All existing tests stay green; a test changes only when the behaviour it
checks changes on purpose, with the reason next to it.

## API contract (Engine provides, Live page consumes)

Existing endpoints and parameters keep working unchanged. Added:

### `GET /api/pulse`
Tiny, from memory, never blocks.
```json
{"v": "k3f9a2", "now": 1795206700, "changes_last_hour": 412,
 "status": [ ...same rows as /api/status... ],
 "brief": {"deals": 9298, "dropped_24h": 441, "record_lows": 12, "fake": 47, "soon": 19, "promo": 360}}
```
`v` is the catalogue version: an opaque short string that changes when, and only when, a rebuild found something different
(a price, stock, a product added). The page compares it with the last one it saw.

### `GET /api/deals` additions
- `fields=card` returns each item with **only** these keys (the page tolerates extra ones):
  `id, store, name, image, price, original_price, usual_price, discount_pct, kind, saving, flags, deal_label, score, since,
  prev_price, recent_low, in_stock, unit_text, hub, hub_name, spark, links, verdict, matches, cheapest, promo_code, promo_price,
  rhythm, top_category`
  with `spark` at most 12 `[t, price]` points, `links` = `{web, android, ios}` (no `browser`), `verdict` = `{label, tone, why}`,
  `matches` = `[{id, store, price, in_stock}]`, `rhythm` = `null` or `{next_in_days, on_sale, text}`.
- The response carries `"v"` (the same value as `/api/pulse`) and `"now"`.
- `status`, `labels` and `changes_last_hour` stay in the response (the page reads them on first load).
- `ETag: W/"<v>-<hash of the query>"`; a request with a matching `If-None-Match` gets **304** and no body.
- `Cache-Control: no-cache` (revalidate, don't forbid storing).
- `since_v=<v>` is optional sugar: when it equals the current version the answer is `304`.

### `GET /api/product/ID`
Unchanged shape (full item, history, matches). ETag + 304 the same way.

### `GET /api/changes`, `GET /api/brief`
Unchanged shape; ETag + 304; served from memory.

### Static and pages
`/`, `/live`, `/i18n.js`, `/sw.js`, icons, manifest, `/p/ID`, `/stores`, `/cats`, `/coupons`, `/about`, `/privacy`, `/contact`:
`ETag` + `304`, `Cache-Control: no-cache` for HTML and `sw.js`, `public, max-age=86400` for icons. Never `no-store` except
`/admin` and POST answers.

If `/api/pulse` answers 404 (an old server), the page falls back to asking `/api/deals` every 30 s.

## The 30-second refresh (Live page)

- One loop. Every 30 s: `GET /api/pulse`. Same `v` → update the status line, the brief's numbers and the "updated N s ago"
  indicator; ask for nothing else. New `v` → refetch the current view (with `If-None-Match`), the ticker and the watchlist check.
- Patch, don't redraw: only cards whose data changed are replaced; new ones slide in and are highlighted; scroll position and
  open dialogs are never disturbed. If the user has scrolled down, new deals wait behind a "N new deals ↑" pill.
- Paused while the tab is hidden or offline; one immediate pulse when it comes back.
- A filter change cancels the request in flight (AbortController) and shows skeletons at once.
- A visible, quiet indicator of freshness (a ring that fills over 30 s, or "updated 12 s ago"); tapping it refreshes now.
- Service worker: cache the page shell, `i18n.js` and icons (stale-while-revalidate), and the last `/api/deals` answer for the
  home view so a repeat visit shows cards instantly, then updates. Never cache `/admin`, POSTs or `/api/push/*`.

## Interface direction (Live page and Pages use the same one)

Keep it a deals site that feels fast and trustworthy, not a dashboard. Arabic first (`dir=rtl`), English by the toggle.

Tokens (same names in both files; light and dark):
```
--bg #f7f6f3  --panel #ffffff  --ink #17161a  --muted #6b6871  --faint #a3a0a8  --line #e7e4de
--accent #e0452c (actions, discounts)  --accent-ink #ffffff  --good #16794a  --good-bg #e2f4ea
--warn #9a6500  --warn-bg #fbf0d2  --chip #f0eee9
dark: --bg #0f0f11  --panel #19191d  --ink #f2f0ea  --muted #a09ca6  --faint #6a6770  --line #2a2930
      --accent #ff6a52  --accent-ink #1a0906  --good #5fd09a  --good-bg #14301f  --warn #e8bb52  --warn-bg #38300f  --chip #232228
store colours: --talabat #ff5a00 --amazon_eg #232f3e (dark: #8aa0bb) --breadfast #a80080 --jumia #f68b1e
               --dream2000 #0b4ea2 --kenzz #7b2cbf --raya #c8102e
radius: --r-s 8px  --r-m 12px  --r-l 18px        space: 4 8 12 16 24 32
type: system-ui stack with "Noto Sans Arabic"; sizes 12 / 13.5 / 15 / 17 / 21 / 28; numbers `tabular-nums`
elevation: cards flat with a 1px line at rest, a soft shadow and 2px lift on hover/focus; no heavy shadows
motion: 160–220 ms ease-out; none under prefers-reduced-motion
```
Rules: one accent colour; the price is the loudest thing on a card, then the discount, then the name; advice
(اشتري دلوقتي / استنى / اتأكد من السعر) is a small coloured pill, never a banner; every tap target at least 44 px; visible focus
rings; no layout shift when pictures or data arrive (reserve the space); the header is one compact row on phones with the
search always reachable; filters are a bottom sheet on phones; empty and error states say what to do next.

## Order of work

Engine, Live page and Pages build in parallel against this document. Then one integration pass: all three together, the
benchmark, the test suite, phone and desktop checks in a browser, and only then a pull request.
