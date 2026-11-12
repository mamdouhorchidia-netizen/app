"""Posts the biggest fresh deals to a Telegram channel, like 3rrood's channel.

Off unless TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set (make a bot with @BotFather, add it to your channel
as an admin, and use the channel's @name or its numeric id). Every 2 minutes it looks at what changed since the
last look and posts the deals that are worth it: in stock, at least TELEGRAM_MIN_PCT off (40) — or at least 20 %
off and the lowest price in 90 days — and not a "discount" that never moves. At most TELEGRAM_PER_HOUR posts an
hour (10), and a product is posted again only when its price is lower than when it was last posted.
Each post links to the product's page here (/p/<id>), which has the button to the store.
"""

import html
import json
import os
import sqlite3
import threading
import time
import traceback
import urllib.error
import urllib.request

from . import finder

API = "https://api.telegram.org"
AR_STORE = {"talabat": "طلبات مارت", "amazon_eg": "أمازون مصر", "jumia": "جوميا", "breadfast": "بريدفاست", "dream2000": "دريم 2000",
            "kenzz": "كنز"}


def _money(n) -> str:
    return f"{n:,.2f}".rstrip("0").rstrip(".")


def caption(d: dict, base_url: str) -> str:
    e = lambda s: html.escape(str(s or ""), quote=False)
    lines = [f"🔥 <b>{e(d['name'][:180])}</b>"]
    price = f"💰 <b>{_money(d['price'])} ج.م</b>"
    if d.get("kind") == "platform" and d.get("original_price"):
        price += f" بدل <s>{_money(d['original_price'])}</s>"
    elif d.get("kind") == "drop" and d.get("usual_price"):
        price += f" (سعره المعتاد {_money(d['usual_price'])})"
    price += f"  −{round(d['discount_pct'])}%"
    lines.append(price)
    where = f"🏪 {AR_STORE.get(d['store'], d['store'])}"
    if d.get("hub_name"):
        where += f" · 📍 {e(d['hub_name'])}"
    lines.append(where)
    if "lowest" in d["flags"]:
        lines.append("✅ أقل سعر في آخر 90 يوم")
    if d.get("cheapest") and d.get("matches"):
        lines.append("✅ الأرخص بين المتاجر")
    if d.get("deal_label"):
        lines.append(f"⏰ {e(d['deal_label'])}")
    lines.append(f"\n{base_url}/p/{d['id']}")
    return "\n".join(lines)


def worth_posting(d: dict, min_pct: float) -> bool:
    if not d["in_stock"] or not d.get("discount_pct") or {"steady", "inflated"} & set(d["flags"]):
        return False
    return d["discount_pct"] >= min_pct or ("lowest" in d["flags"] and d["discount_pct"] >= 20)


class Poster:
    def __init__(self, db_path: str, token: str, chat: str, base_url: str, min_pct: float = 40, per_hour: int = 10,
                 every: int = 120, send=None, log=print):
        self.db_path, self.token, self.chat, self.base_url = db_path, token, chat, base_url.rstrip("/")
        self.min_pct, self.per_hour, self.every, self.log = min_pct, per_hour, every, log
        self.send = send or self._send
        self.since = time.time() - 600  # on start: what moved in the last 10 minutes

    def _send(self, method: str, payload: dict) -> dict:
        req = urllib.request.Request(f"{API}/bot{self.token}/{method}", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())

    def once(self, now: float = None) -> int:
        now = now or time.time()
        con = sqlite3.connect(self.db_path, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            con.execute("CREATE TABLE IF NOT EXISTS telegram_posts (product_id INTEGER, price REAL, at INTEGER)")
            sent_hour = con.execute("SELECT COUNT(*) FROM telegram_posts WHERE at > ?", (now - 3600,)).fetchone()[0]
            room = self.per_hour - sent_hour
            if room <= 0:
                return 0
            last = {r["product_id"]: r["price"] for r in con.execute(
                "SELECT product_id, MIN(price) price FROM telegram_posts WHERE at > ? GROUP BY product_id",
                (now - 30 * 86400,))}
            fresh = [d for d in finder.catalog(con, int(now))
                     if d["since"] >= self.since and worth_posting(d, self.min_pct)
                     and (d["id"] not in last or d["price"] < last[d["id"]] - 0.01)]
            fresh.sort(key=lambda d: (-d["score"], -d["discount_pct"]))
            posted = 0
            for d in fresh[:min(room, 3)]:  # three a round at most: a channel isn't a firehose
                text = caption(d, self.base_url)
                button = {"inline_keyboard": [[{"text": "افتح العرض", "url": f"{self.base_url}/p/{d['id']}"}]]}
                try:
                    if d.get("image"):
                        self.send("sendPhoto", {"chat_id": self.chat, "photo": d["image"], "caption": text,
                                                "parse_mode": "HTML", "reply_markup": button})
                    else:
                        self.send("sendMessage", {"chat_id": self.chat, "text": text, "parse_mode": "HTML",
                                                  "reply_markup": button})
                except urllib.error.HTTPError as err:
                    if err.code == 400 and d.get("image"):  # Telegram couldn't fetch the picture: send it as text
                        self.send("sendMessage", {"chat_id": self.chat, "text": text, "parse_mode": "HTML",
                                                  "reply_markup": button})
                    else:
                        raise
                with con:
                    con.execute("INSERT INTO telegram_posts VALUES (?, ?, ?)", (d["id"], d["price"], int(now)))
                posted += 1
            self.since = now
            return posted
        finally:
            con.close()

    def run_forever(self):
        while True:
            try:
                n = self.once()
                if n:
                    self.log(f"telegram: posted {n} deal(s)")
            except urllib.error.HTTPError as err:
                self.log(f"telegram: {err.code} {err.reason}; check TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
                time.sleep(600)
            except Exception:
                self.log("telegram: " + traceback.format_exc(limit=2))
            time.sleep(self.every)


def start(db_path: str, base_url: str, log=print):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(), os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return None
    p = Poster(db_path, token, chat, base_url, float(os.environ.get("TELEGRAM_MIN_PCT", 40)),
               int(os.environ.get("TELEGRAM_PER_HOUR", 10)), log=log)
    threading.Thread(target=p.run_forever, name="telegram", daemon=True).start()
    log(f"telegram: posting deals of {p.min_pct:g}%+ to {chat}, up to {p.per_hour} an hour")
    return p
