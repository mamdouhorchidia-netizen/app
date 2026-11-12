"""AWS Signature Version 4, which Amazon's Product Advertising API requires on every request."""

import hashlib
import hmac
from urllib.parse import quote


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def sign(method: str, host: str, path: str, query: dict, headers: dict, body: bytes,
         access_key: str, secret_key: str, region: str, service: str, amz_date: str) -> dict:
    """Returns `headers` plus Authorization. `headers` must already hold host and x-amz-date
    (amz_date is YYYYMMDD'T'HHMMSS'Z')."""
    date = amz_date[:8]
    canon_headers = {k.lower().strip(): " ".join(str(v).split()) for k, v in headers.items()}
    signed = ";".join(sorted(canon_headers))
    canon_query = "&".join(f"{quote(k, safe='-_.~')}={quote(str(v), safe='-_.~')}" for k, v in sorted(query.items()))
    canonical = "\n".join([
        method,
        quote(path, safe="/-_.~"),
        canon_query,
        "".join(f"{k}:{canon_headers[k]}\n" for k in sorted(canon_headers)),
        signed,
        hashlib.sha256(body).hexdigest(),
    ])
    scope = f"{date}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret_key).encode(), date), region), service), "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    out = dict(headers)
    out["Authorization"] = f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, Signature={signature}"
    return out
