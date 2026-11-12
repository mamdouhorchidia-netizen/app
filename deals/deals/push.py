"""Web Push: price-drop notifications that reach a phone or computer even when the site is closed.

- VAPID keys (the site's identity to the push services) are made on first start and kept next to the
  database (vapid.json). Browsers subscribe with the public key.
- Each subscription stores what its owner wants (see `wants`): their watchlist, and/or any drop of at least
  N %, optionally only some stores, hubs, categories or words.
- `Notifier` looks at the price changes every minute and sends each subscriber one notification per batch
  (at most one every `min_gap` seconds), so a busy minute doesn't buzz a phone ten times.
- Encryption is RFC 8291 (aes128gcm) and the authorization header RFC 8292 (VAPID, ES256), on the
  `cryptography` package. Push services answer 404/410 for a subscription that is gone; it is then deleted.
"""

import base64
import hashlib
import hmac
import json
import os
import struct
import threading
import time
import traceback
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import db as dbm

SUBJECT = "https://deals.vorath.cloud"  # who to contact about this sender (RFC 8292); a site, not a person


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


# ---- keys ------------------------------------------------------------------------------------------

class Vapid:
    def __init__(self, private_key: ec.EllipticCurvePrivateKey):
        self.key = private_key
        self.public_raw = private_key.public_key().public_bytes(serialization.Encoding.X962,
                                                                serialization.PublicFormat.UncompressedPoint)

    @property
    def public_b64(self) -> str:
        return b64u(self.public_raw)

    @classmethod
    def load_or_create(cls, path: str) -> "Vapid":
        try:
            with open(path, encoding="utf-8") as f:
                d = int.from_bytes(unb64u(json.load(f)["private"]), "big")
            return cls(ec.derive_private_key(d, ec.SECP256R1()))
        except (OSError, ValueError, KeyError):
            key = ec.generate_private_key(ec.SECP256R1())
            d = key.private_numbers().private_value.to_bytes(32, "big")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"private": b64u(d)}, f)
            return cls(key)

    def header(self, endpoint: str, now: float = None) -> str:
        """Authorization: vapid t=<JWT signed ES256>, k=<public key>."""
        u = urlsplit(endpoint)
        claims = {"aud": f"{u.scheme}://{u.netloc}", "exp": int((now or time.time()) + 12 * 3600), "sub": SUBJECT}
        signing = (b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode()) + "." +
                   b64u(json.dumps(claims, separators=(",", ":")).encode()))
        r, s = decode_dss_signature(self.key.sign(signing.encode(), ec.ECDSA(hashes.SHA256())))
        jwt = signing + "." + b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        return f"vapid t={jwt}, k={self.public_b64}"


# ---- RFC 8291 --------------------------------------------------------------------------------------

def encrypt(payload: bytes, p256dh: str, auth: str, salt: bytes = None,
            sender: ec.EllipticCurvePrivateKey = None) -> bytes:
    """The aes128gcm body for one push message to one subscription."""
    ua_public = unb64u(p256dh)
    auth_secret = unb64u(auth)
    sender = sender or ec.generate_private_key(ec.SECP256R1())
    as_public = sender.public_key().public_bytes(serialization.Encoding.X962,
                                                 serialization.PublicFormat.UncompressedPoint)
    shared = sender.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public))
    salt = salt or os.urandom(16)
    prk_key = _hmac(auth_secret, shared)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    record = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # one record, the last one
    return salt + struct.pack("!IB", 4096, len(as_public)) + as_public + record


def send(vapid: Vapid, subscription: dict, payload: dict, ttl: int = 6 * 3600) -> int:
    """Sends one message; returns the push service's status (201 = accepted, 404/410 = subscription gone)."""
    body = encrypt(json.dumps(payload, ensure_ascii=False).encode(), subscription["keys"]["p256dh"],
                   subscription["keys"]["auth"])
    req = urllib.request.Request(subscription["endpoint"], data=body, method="POST", headers={
        "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": str(ttl),
        "Urgency": "high", "Authorization": vapid.header(subscription["endpoint"])})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


# ---- who wants what --------------------------------------------------------------------------------

def wants(prefs: dict, drop: dict) -> bool:
    """drop: {"id", "store", "hub", "top_category", "name", "before", "price", "drop_pct", "discount_pct"}."""
    if drop["id"] in set(prefs.get("watch") or []):
        target = (prefs.get("targets") or {}).get(str(drop["id"]))
        return not target or drop["price"] <= float(target)
    if not prefs.get("all_drops"):
        return False
    if drop["drop_pct"] < float(prefs.get("min_drop") or 0):
        return False
    for key, field in (("stores", "store"), ("hubs", "hub"), ("categories", "top_category")):
        allowed = prefs.get(key) or []
        if allowed and drop.get(field) not in allowed:
            return False
    words = [w.lower() for w in prefs.get("words") or [] if w.strip()]
    return not words or any(w in drop["name"].lower() for w in words)


def message(drops: list[dict]) -> dict:
    top = max(drops, key=lambda d: d["drop_pct"])
    if len(drops) == 1:
        return {"title": f"↓ {top['drop_pct']:g}%  {top['name'][:60]}",
                "body": f"{top['before']:g} → {top['price']:g} EGP at {top['label']}",
                "url": f"/?p={top['id']}", "tag": f"deal-{top['id']}", "image": top.get("image") or ""}
    return {"title": f"{len(drops)} price drops",
            "body": "; ".join(f"{d['name'][:28]} {d['price']:g}" for d in sorted(drops, key=lambda d: -d['drop_pct'])[:4]),
            "url": "/?sort=newest&status=new", "tag": "deals-batch", "image": top.get("image") or ""}


# ---- the minute-by-minute check --------------------------------------------------------------------

class Notifier:
    def __init__(self, db_path: str, labels: dict, min_gap: int = 300, log=print):
        self.db_path, self.labels, self.min_gap, self.log = db_path, labels, min_gap, log
        self.vapid = Vapid.load_or_create(os.path.join(os.path.dirname(os.path.abspath(db_path)), "vapid.json"))
        self.last = int(time.time())
        self.pending = {}  # endpoint -> drops waiting for that subscriber's gap

    def drops_since(self, con, since: int) -> list[dict]:
        rows = con.execute("""
            SELECT x.at, x.price, p.id, p.store, p.name, p.image, p.category, p.original_price, p.extra,
                   (SELECT y.price FROM prices y WHERE y.product_id = x.product_id AND y.at < x.at
                    ORDER BY y.at DESC LIMIT 1) AS before
            FROM prices x JOIN products p ON p.id = x.product_id
            WHERE x.at > ? AND x.at > p.first_seen AND x.in_stock = 1""", (since,))
        out = []
        for r in rows:
            if r["before"] and r["price"] < r["before"]:
                extra = json.loads(r["extra"] or "{}")
                orig = r["original_price"]
                out.append({"id": r["id"], "store": r["store"], "label": self.labels.get(r["store"], r["store"]),
                            "hub": extra.get("hub"), "top_category": (r["category"] or "Other").split(" / ")[0],
                            "name": r["name"], "image": r["image"], "before": r["before"], "price": r["price"],
                            "drop_pct": round(100 * (r["before"] - r["price"]) / r["before"], 1),
                            "discount_pct": round(100 * (orig - r["price"]) / orig, 1) if orig and orig > r["price"] else 0})
        return out

    def tick(self, now: int = None, sender=send):
        now = int(now or time.time())
        con = dbm.connect(self.db_path)
        try:
            drops = self.drops_since(con, self.last)
            self.last = now
            for sub in con.execute("SELECT * FROM push_subs").fetchall():
                prefs = json.loads(sub["prefs"] or "{}")
                mine = [d for d in drops if wants(prefs, d)]
                if mine:
                    self.pending.setdefault(sub["endpoint"], []).extend(mine)
                waiting = self.pending.get(sub["endpoint"])
                if not waiting or now - (sub["last_sent"] or 0) < self.min_gap:
                    continue
                unique = list({d["id"]: d for d in waiting}.values())
                status = sender(self.vapid, json.loads(sub["subscription"]), message(unique))
                self.pending.pop(sub["endpoint"], None)
                with con:
                    if status in (404, 410):
                        con.execute("DELETE FROM push_subs WHERE endpoint=?", (sub["endpoint"],))
                    elif 200 <= status < 300:
                        con.execute("UPDATE push_subs SET last_sent=?, fails=0 WHERE endpoint=?", (now, sub["endpoint"]))
                    else:
                        con.execute("UPDATE push_subs SET fails=fails+1 WHERE endpoint=?", (sub["endpoint"],))
                        self.log(f"push: {status} from {urlsplit(sub['endpoint']).netloc}")
        finally:
            con.close()

    def run_forever(self):
        while True:
            time.sleep(60)
            try:
                self.tick()
            except Exception:
                traceback.print_exc()


def start(db_path: str, labels: dict) -> Notifier:
    n = Notifier(db_path, labels)
    threading.Thread(target=n.run_forever, name="push", daemon=True).start()
    return n
