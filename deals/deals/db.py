"""SQLite storage: one row per product, and a price row each time a product's price or stock changes."""

import json
import sqlite3
import time
from typing import Iterable

from .models import Offer

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    store TEXT NOT NULL,
    sku TEXT NOT NULL,
    name TEXT NOT NULL,
    brand TEXT DEFAULT '',
    category TEXT DEFAULT '',
    url TEXT DEFAULT '',
    image TEXT DEFAULT '',
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    price REAL,             -- latest price
    original_price REAL,    -- latest struck-through price
    in_stock INTEGER DEFAULT 1,
    extra TEXT DEFAULT '{}',  -- JSON: rank, deal label, app link, ...
    pending_price REAL,       -- a jump of more than JUMP seen once; recorded only if the next check agrees
    UNIQUE (store, sku)
);
CREATE TABLE IF NOT EXISTS prices (
    product_id INTEGER NOT NULL REFERENCES products(id),
    at INTEGER NOT NULL,
    price REAL NOT NULL,
    original_price REAL,
    in_stock INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS prices_by_product ON prices (product_id, at);
CREATE TABLE IF NOT EXISTS live_status (
    store TEXT PRIMARY KEY,
    updated INTEGER,        -- last page checked
    last_change INTEGER,    -- last price/stock change found
    pages INTEGER,          -- pages checked since start
    changes INTEGER,        -- changes found since start
    tasks INTEGER,          -- pages in one full cycle
    cycle_secs REAL,        -- how long the last full cycle took
    error TEXT,
    paused_until INTEGER
);
CREATE TABLE IF NOT EXISTS push_subs (
    endpoint TEXT PRIMARY KEY,   -- where the browser's push service takes messages for this device
    subscription TEXT NOT NULL,  -- JSON: endpoint + keys (p256dh, auth)
    prefs TEXT NOT NULL,         -- JSON: what to be told about (push.wants)
    created INTEGER,
    last_sent INTEGER,
    fails INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    store TEXT NOT NULL,
    started INTEGER NOT NULL,
    finished INTEGER,
    offers INTEGER DEFAULT 0,
    changed INTEGER DEFAULT 0,
    error TEXT
);
"""


def connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    cols = {r["name"] for r in con.execute("PRAGMA table_info(products)")}
    if "extra" not in cols:  # databases made before the column existed
        con.execute("ALTER TABLE products ADD COLUMN extra TEXT DEFAULT '{}'")
    if "pending_price" not in cols:
        con.execute("ALTER TABLE products ADD COLUMN pending_price REAL")
    # talabat products from before hubs were all Maadi Old's: give them that hub (sku "<hub>:<sku>")
    with con:
        con.execute("""UPDATE products SET sku = '619848:' || sku,
                         extra = json_set(COALESCE(NULLIF(extra, ''), '{}'), '$.hub', 619848, '$.hub_name', 'Maadi Old')
                       WHERE store = 'talabat' AND instr(sku, ':') = 0""")
    return con


JUMP = 0.6  # a price moving by more than 60% at once must be seen twice in a row before it's recorded


def save_offers(con: sqlite3.Connection, offers: Iterable[Offer], now: int = None, stats: dict = None) -> tuple[int, int]:
    """Stores offers. Returns (seen, changed): a price row is only written when something moved, or the product
    is new. `stats`, if given, also gets "new", "moved" (an existing product's price or stock changed) and "held"
    (a jump of more than JUMP seen for the first time: stores sometimes show a wrong price for a moment, such as
    1 EGP, and one sighting isn't enough to put it in the history)."""
    now = int(now or time.time())
    seen = changed = new = held = 0
    with con:
        for o in offers:
            seen += 1
            row = con.execute("SELECT id, price, original_price, in_stock, category, brand, image, extra, pending_price"
                              " FROM products WHERE store=? AND sku=?", (o.store, o.sku)).fetchone()
            if (row is not None and row["price"] and o.in_stock and row["in_stock"]
                    and abs(o.price - row["price"]) / row["price"] > JUMP and row["pending_price"] != o.price):
                con.execute("UPDATE products SET pending_price=?, last_seen=? WHERE id=?", (o.price, now, row["id"]))
                held += 1
                continue
            original = o.original_price
            if not o.original_known:  # keep a list price another source saw, while the price hasn't moved
                original = row["original_price"] if row is not None and row["price"] == o.price else None
            if row is None:
                cur = con.execute(
                    "INSERT INTO products (store, sku, name, brand, category, url, image, first_seen, last_seen,"
                    " price, original_price, in_stock, extra) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (o.store, o.sku, o.name, o.brand, o.category, o.url, o.image, now, now,
                     o.price, original, int(o.in_stock), json.dumps(o.extra, ensure_ascii=False)))
                pid, moved = cur.lastrowid, True
                new += 1
            else:
                pid = row["id"]
                moved = (row["price"] != o.price or row["original_price"] != original
                         or bool(row["in_stock"]) != o.in_stock)
                extra = {**json.loads(row["extra"] or "{}"), **o.extra}
                con.execute(
                    "UPDATE products SET name=?, brand=?, category=?, url=?, image=?, last_seen=?,"
                    " price=?, original_price=?, in_stock=?, extra=?, pending_price=NULL WHERE id=?",
                    (o.name, o.brand or row["brand"], o.category or row["category"], o.url, o.image or row["image"],
                     now, o.price, original, int(o.in_stock), json.dumps(extra, ensure_ascii=False), pid))
            if moved:
                changed += 1
                con.execute("INSERT INTO prices (product_id, at, price, original_price, in_stock) VALUES (?,?,?,?,?)",
                            (pid, now, o.price, original, int(o.in_stock)))
    if stats is not None:
        stats.update(new=new, moved=changed - new, held=held)
    return seen, changed


def start_run(con, store: str) -> int:
    with con:
        return con.execute("INSERT INTO runs (store, started) VALUES (?, ?)", (store, int(time.time()))).lastrowid


def finish_run(con, run_id: int, offers: int, changed: int, error: str = None):
    with con:
        con.execute("UPDATE runs SET finished=?, offers=?, changed=?, error=? WHERE id=?",
                    (int(time.time()), offers, changed, error, run_id))


def set_live_status(con, store: str, **fields):
    cols = ["store"] + list(fields)
    with con:
        con.execute(f"INSERT OR REPLACE INTO live_status ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    [store] + list(fields.values()))


def history(con, product_id: int) -> list[dict]:
    rows = con.execute("SELECT at, price, original_price, in_stock FROM prices WHERE product_id=? ORDER BY at",
                       (product_id,))
    return [dict(r) for r in rows]
