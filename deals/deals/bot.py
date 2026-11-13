"""A Telegram bot people talk to: send a product's name or a store link, get its price everywhere we track it, its
history, the advice (buy now, wait, cheaper elsewhere, fake discount) and when its next sale is likely.

  any text         search: the best matches, each with a button for the full picture
  a store link     that product (talabat, Amazon, Jumia, Breadfast, Dream 2000, Kenzz, Raya)
  /deals           today's best deals · /drops what just dropped · /mine what you follow
  🔔 button        follow a product: the bot writes when its price drops, and the day before a predicted sale

On with TELEGRAM_BOT_TOKEN (the same bot as the channel poster); it answers whoever writes to it. It asks
Telegram for new messages (long polling), so nothing needs to reach the server from outside.
"""

import html
import json
import re
import sqlite3
import threading
import time
import traceback
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlsplit

from . import finder

API = "https://api.telegram.org"
AR_STORE = {"talabat": "طلبات مارت", "amazon_eg": "أمازون مصر", "jumia": "جوميا", "breadfast": "بريدفاست",
            "dream2000": "دريم 2000", "kenzz": "كنز", "raya": "راية شوب"}
VERDICT_AR = {"Buy now": "✅ اشتري دلوقتي", "Good price": "👍 سعر كويس", "Fair price": "😐 سعره العادي",
              "Wait": "⏳ استنى", "Cheaper elsewhere": "↔️ أرخص في مكان تاني", "Out of stock": "🚫 خلصان",
              "Check the price": "⚠️ اتأكد من السعر"}
HELP = ("أهلًا! 👋 أنا بتابع أسعار طلبات مارت وأمازون وجوميا ودريم 2000 وكنز وراية لحظة بلحظة.\n\n"
        "• ابعتلي <b>اسم منتج</b> (مثلًا: نسكافيه، زيت، iphone 16) وأقولك سعره فين أرخص.\n"
        "• أو ابعتلي <b>لينك منتج</b> من أي متجر من دول وأقولك تاريخ سعره ولو الخصم حقيقي ولا لأ.\n"
        "• /deals أحسن عروض النهارده · /drops اللي نزل دلوقتي · /mine المنتجات اللي بتتابعها\n"
        "• دوس 🔔 على أي منتج وهبعتلك أول ما سعره ينزل، وقبل العرض المتوقع بيوم.")


def e(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=False)


def money(n) -> str:
    return f"{n:,.2f}".rstrip("0").rstrip(".") if n is not None else ""


def link_keys(text: str) -> list[tuple[str, str]]:
    """(store, key) pairs a pasted link can stand for; key is matched against the product's sku."""
    out = []
    for url in re.findall(r"https?://\S+", text):
        u = urlsplit(url)
        host, path = u.netloc.lower(), u.path
        if "talabat.com" in host and (m := re.search(r"/s/([^/?#]+)", path)):
            out.append(("talabat", m.group(1)))
        elif "amazon." in host and (m := re.search(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})", path)):
            out.append(("amazon_eg", m.group(1)))
        elif "breadfast.com" in host and parse_qs(u.query).get("id"):
            out.append(("breadfast", parse_qs(u.query)["id"][0]))
        elif "kenzz.com" in host and (m := re.search(r"/product/([A-Z0-9]+)", path)):
            out.append(("kenzz", m.group(1)))
        elif "rayashop.com" in host and (m := re.search(r"^/(?:ar|en)/([a-z0-9-]+)/?$", path)):
            out.append(("raya", m.group(1)))
        elif "jumia.com.eg" in host:
            out.append(("jumia", "path:" + path.rstrip("/")))
        elif "dream2000.com" in host and (m := re.search(r"/products/([^/?#]+)", path)):
            out.append(("dream2000", "path:/products/" + m.group(1)))
    return out


def find_by_link(items: list[dict], text: str) -> list[dict]:
    hits = []
    for store, key in link_keys(text):
        for d in items:
            if d["store"] != store:
                continue
            if key.startswith("path:"):
                if urlsplit(d["url"] or "").path.rstrip("/").endswith(key[5:]):
                    hits.append(d)
            elif d["sku"] == key or d["sku"].endswith(":" + key):
                hits.append(d)
    # one talabat hub's copy is enough: the cheapest in stock
    hits.sort(key=lambda d: (not d["in_stock"], d["price"]))
    return hits[:1]


def search(items: list[dict], text: str, limit: int = 6) -> list[dict]:
    words = [w for w in re.sub(r"[^\w\s.%-]", " ", text.lower()).split() if len(w) > 1][:6]
    if not words:
        return []
    found = [d for d in items if d["in_stock"] and all(w in (d["name"] + " " + (d["brand"] or "")).lower() for w in words)]
    found.sort(key=lambda d: (-(d["score"] if d["kind"] else 0), d["price"]))
    seen, out = set(), []
    for d in found:  # one line per product, not one per talabat hub
        k = (d["store"], d["sku"].split(":")[-1])
        if k not in seen:
            seen.add(k)
            out.append(d)
    return out[:limit]


def describe(d: dict, by_id: dict, base_url: str) -> str:
    lines = [f"<b>{e(d['name'][:200])}</b>",
             f"🏪 {AR_STORE.get(d['store'], d['store'])}" + (f" · 📍 {e(d['hub_name'])}" if d.get("hub_name") else "")]
    price = f"💰 <b>{money(d['price'])} ج.م</b>"
    if d.get("kind") == "platform" and d.get("original_price"):
        price += f" بدل <s>{money(d['original_price'])}</s>"
    if d.get("discount_pct"):
        price += f"  −{round(d['discount_pct'])}%"
    lines.append(price + ("" if d["in_stock"] else "  (خلصان)"))
    v = (d.get("verdict") or {}).get("label")
    if v:
        lines.append(f"🧭 {VERDICT_AR.get(v, v)}")
    flags = d.get("flags") or []
    if "lowest" in flags:
        lines.append("🔥 أقل سعر في آخر 90 يوم")
    if "rose" in flags and d.get("recent_low"):
        lines.append(f"⚠️ كان بـ {money(d['recent_low'])} ج.م من قريب")
    if "inflated" in flags:
        lines.append("🕵️ السعر القديم شكله متضخم: الخصم غالبًا مش حقيقي")
    if "steady" in flags:
        lines.append("🕵️ الخصم ثابت من أسابيع: ده سعره العادي")
    if d.get("usual_price"):
        lines.append(f"📊 سعره المعتاد {money(d['usual_price'])} ج.م" +
                     (f" · أقل سعر في 90 يوم {money(d['low90'])}" if d.get("low90") else ""))
    r = d.get("rhythm")
    if r:
        if r["on_sale"]:
            lines.append("⏳ عليه عرض دلوقتي" + (f"، وعروضه بتستمر حوالي {round(r['lasts_days'])} يوم" if r.get("lasts_days") else ""))
        else:
            lines.append(f"⏳ بيعمل عرض كل حوالي {round(r['every_days'])} يوم لحد {money(r['typical_sale'])} ج.م؛ "
                         f"الجاي غالبًا بعد {max(1, round(r['next_in_days']))} يوم")
    if d.get("promo_code"):
        lines.append(f"🎟️ بالكود <code>{e(d['promo_code'])}</code>: {money(d['promo_price'])} ج.م")
    others = [by_id[m["id"]] for m in d.get("matches") or [] if m["id"] in by_id and by_id[m["id"]]["in_stock"]]
    if others:
        lines.append("↔️ في متاجر تانية: " + " · ".join(f"{AR_STORE.get(o['store'], o['store'])} {money(o['price'])}"
                                                       for o in sorted(others, key=lambda o: o["price"])[:3]))
    lines.append(f"\n{base_url}/p/{d['id']}")
    return "\n".join(lines)


def buttons(d: dict, base_url: str) -> dict:
    store = (d.get("links") or {}).get("web") or d.get("url")
    row = [{"text": "🔔 تابع السعر", "callback_data": f"w:{d['id']}"}]
    if store:
        row.append({"text": "🛒 افتح في المتجر", "url": store})
    return {"inline_keyboard": [row, [{"text": "📄 صفحة المنتج", "url": f"{base_url}/p/{d['id']}"}]]}


def result_list(found: list[dict]) -> tuple[str, dict]:
    lines = []
    for i, d in enumerate(found, 1):
        pct = f" −{round(d['discount_pct'])}%" if d.get("discount_pct") and d.get("kind") else ""
        lines.append(f"{i}. {e(d['name'][:80])}\n    <b>{money(d['price'])} ج.م</b>{pct} · {AR_STORE.get(d['store'], d['store'])}")
    kb = {"inline_keyboard": [[{"text": str(i), "callback_data": f"p:{d['id']}"} for i, d in enumerate(found, 1)]]}
    return "\n".join(lines) + "\n\nدوس على رقم المنتج عشان تشوف تفاصيله 👇", kb


class Bot:
    def __init__(self, db_path: str, token: str, base_url: str, send=None, log=print):
        self.db_path, self.token, self.base_url, self.log = db_path, token, base_url.rstrip("/"), log
        self.call = send or self._call
        self.offset = None
        self._cat = (0.0, [], {})
        self.last_check = time.time()
        self.last_forecast = 0

    def _call(self, method: str, payload: dict, timeout: int = 20) -> dict:
        req = urllib.request.Request(f"{API}/bot{self.token}/{method}", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    def connect(self):
        con = sqlite3.connect(self.db_path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("""CREATE TABLE IF NOT EXISTS bot_watch (chat INTEGER, product_id INTEGER, price REAL, at INTEGER,
                       predicted INTEGER DEFAULT 0, PRIMARY KEY (chat, product_id))""")
        return con

    def catalog(self, con) -> tuple[list, dict]:
        at, items, by_id = self._cat
        if time.monotonic() - at > 60:
            items = finder.catalog(con)
            by_id = {d["id"]: d for d in items}
            self._cat = (time.monotonic(), items, by_id)
        return items, by_id

    def say(self, chat, text: str, kb: dict = None):
        payload = {"chat_id": chat, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": True}
        if kb:
            payload["reply_markup"] = kb
        self.call("sendMessage", payload)

    # ---- what people send ----
    def handle(self, update: dict):
        con = self.connect()
        try:
            if "callback_query" in update:
                return self.on_button(con, update["callback_query"])
            msg = update.get("message") or {}
            chat, text = (msg.get("chat") or {}).get("id"), (msg.get("text") or "").strip()
            if not chat or not text:
                return
            items, by_id = self.catalog(con)
            cmd = text.split()[0].split("@")[0].lower()
            if cmd in ("/start", "/help"):
                return self.say(chat, HELP)
            if cmd in ("/deals", "/drops"):
                b = finder.brief(items)
                pool = b["top"] if cmd == "/deals" else [d for d in items if "new" in d["flags"] and d["kind"]
                                                         and d["in_stock"] and d.get("prev_price") and d["price"] < d["prev_price"]]
                found = [by_id[x["id"]] if isinstance(x, dict) and x.get("id") in by_id else x for x in pool]
                found = sorted(found, key=lambda d: -d["score"])[:6]
                if not found:
                    return self.say(chat, "مفيش جديد دلوقتي، جرّب كمان شوية.")
                head = "🔥 أحسن عروض النهارده:\n\n" if cmd == "/deals" else "↓ اللي نزل دلوقتي:\n\n"
                text_, kb = result_list(found)
                return self.say(chat, head + text_, kb)
            if cmd == "/mine":
                rows = con.execute("SELECT product_id FROM bot_watch WHERE chat=?", (chat,)).fetchall()
                found = [by_id[r[0]] for r in rows if r[0] in by_id][:10]
                if not found:
                    return self.say(chat, "لسه مش بتتابع حاجة. دوس 🔔 على أي منتج.")
                text_, kb = result_list(found)
                return self.say(chat, "🔔 اللي بتتابعه:\n\n" + text_, kb)
            if cmd == "/stop":
                with con:
                    con.execute("DELETE FROM bot_watch WHERE chat=?", (chat,))
                return self.say(chat, "تمام، وقفت كل التنبيهات.")
            hit = find_by_link(items, text) if "http" in text else []
            if hit:
                return self.say(chat, describe(hit[0], by_id, self.base_url), buttons(hit[0], self.base_url))
            if "http" in text:
                return self.say(chat, "اللينك ده لمنتج مش بتابعه لسه. ابعتلي اسمه وأدورلك عليه.")
            found = search(items, text[:120])
            if not found:
                return self.say(chat, "ملقيتش حاجة بالاسم ده 🤔 جرّب كلمة أقل أو بالإنجليزي (مثلًا: nescafe).")
            if len(found) == 1:
                return self.say(chat, describe(found[0], by_id, self.base_url), buttons(found[0], self.base_url))
            text_, kb = result_list(found)
            self.say(chat, text_, kb)
        finally:
            con.close()

    def on_button(self, con, q: dict):
        chat = ((q.get("message") or {}).get("chat") or {}).get("id")
        kind, _, pid = (q.get("data") or "").partition(":")
        items, by_id = self.catalog(con)
        d = by_id.get(int(pid)) if pid.isdigit() else None
        note = ""
        if d and kind == "p":
            self.say(chat, describe(d, by_id, self.base_url), buttons(d, self.base_url))
        elif d and kind == "w":
            with con:
                con.execute("INSERT OR REPLACE INTO bot_watch (chat, product_id, price, at) VALUES (?, ?, ?, ?)",
                            (chat, d["id"], d["price"], int(time.time())))
            note = "🔔 هبعتلك أول ما سعره ينزل"
        self.call("answerCallbackQuery", {"callback_query_id": q.get("id"), "text": note})

    # ---- telling followers ----
    def check_watches(self, now: int = None) -> int:
        """Drops on followed products, and (hourly) sales predicted for tomorrow."""
        from . import push
        now = int(now or time.time())
        con = self.connect()
        sent = 0
        try:
            rows = con.execute("""SELECT w.chat, w.product_id, w.price AS was, w.predicted, p.price, p.in_stock, p.name
                                  FROM bot_watch w JOIN products p ON p.id = w.product_id""").fetchall()
            for r in rows:
                if r["in_stock"] and r["price"] < r["was"] - 0.009:
                    pct = round(100 * (r["was"] - r["price"]) / r["was"])
                    self.say(r["chat"], f"↓ <b>نزل {pct}%</b>: {e(r['name'][:120])}\n{money(r['was'])} ← "
                                        f"<b>{money(r['price'])} ج.م</b>\n{self.base_url}/p/{r['product_id']}")
                    sent += 1
                if r["price"] != r["was"]:
                    with con:
                        con.execute("UPDATE bot_watch SET price=? WHERE chat=? AND product_id=?",
                                    (r["price"], r["chat"], r["product_id"]))
            if now - self.last_forecast >= 3600:
                self.last_forecast = now
                due = push.due_sales(con, [r["product_id"] for r in rows], now)
                for r in rows:
                    if r["product_id"] in due and now - (r["predicted"] or 0) > 5 * 86400:
                        p, rh = due[r["product_id"]]
                        m = push.predicted_message(p, rh, "ar")
                        self.say(r["chat"], f"<b>{e(m['title'])}</b>\n{e(m['body'])}\n{self.base_url}/p/{p['id']}")
                        with con:
                            con.execute("UPDATE bot_watch SET predicted=? WHERE chat=? AND product_id=?",
                                        (now, r["chat"], r["product_id"]))
                        sent += 1
        finally:
            con.close()
        return sent

    def run_forever(self):
        try:  # start after what's already waiting, so a restart doesn't answer old messages twice
            r = self.call("getUpdates", {"offset": -1, "timeout": 0})
            if r.get("result"):
                self.offset = r["result"][-1]["update_id"] + 1
        except Exception as err:
            self.log(f"telegram bot: {err}")
        while True:
            try:
                r = self.call("getUpdates", {"offset": self.offset, "timeout": 50,
                                             "allowed_updates": ["message", "callback_query"]}, timeout=60)
                for u in r.get("result") or []:
                    self.offset = u["update_id"] + 1
                    try:
                        self.handle(u)
                    except Exception:
                        self.log("telegram bot: " + traceback.format_exc(limit=3))
                if time.time() - self.last_check > 300:
                    self.last_check = time.time()
                    self.check_watches()
            except urllib.error.HTTPError as err:
                self.log(f"telegram bot: {err.code} {err.reason}")
                time.sleep(30 if err.code != 409 else 120)  # 409: a webhook or another copy is reading the updates
            except Exception:
                self.log("telegram bot: " + traceback.format_exc(limit=2))
                time.sleep(10)


def start(db_path: str, base_url: str, token: str, log=print):
    if not token:
        return None
    b = Bot(db_path, token, base_url, log=log)
    threading.Thread(target=b.run_forever, name="telegram-bot", daemon=True).start()
    log("telegram bot: answering messages")
    return b
