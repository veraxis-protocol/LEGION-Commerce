"""WO-COM-E2E-001: Channel3 product discovery client (backend only).

The API key is read from the operator file on the Windows Desktop at call time and is never logged, returned,
stored or sent anywhere except the x-api-key header to api.trychannel3.com. Every call is preserved verbatim
(request body, HTTP status, raw response bytes, sha256) in the run's CHANNEL3_RAW directory. Discovery only:
nothing here buys anything; a Channel3 offer is a product listing, not a purchase.
"""
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://api.trychannel3.com"
KEY_FILE = os.environ.get("CHANNEL3_API_KEY_FILE", r"C:\Users\Administrator\Desktop\Channel3_API_key.txt")


class Channel3Error(Exception):
    pass


class Channel3:
    def __init__(self, raw_dir):
        self.raw_dir = Path(raw_dir)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.n = 0

    def _call(self, method, path, body=None, label="call"):
        key = open(KEY_FILE, encoding="utf-8").read().strip()
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(BASE + path, data=data, method=method,
                                     headers={"x-api-key": key, "Content-Type": "application/json", "Accept": "application/json",
                                              "User-Agent": "legion-commerce-e2e001/0.1"})
        key = None
        started, status, raw, err = time.time(), None, b"", None
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                status, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, raw, err = e.code, e.read(), "HTTP_ERROR"
        except (urllib.error.URLError, TimeoutError) as e:
            err = type(e).__name__
        self.n += 1
        name = f"{self.n:03d}_{label}"
        (self.raw_dir / f"{name}.response.raw").write_bytes(raw)
        meta = {"seq": self.n, "label": label, "method": method, "url": BASE + path, "request_body": body,
                "request_headers_sent": ["x-api-key: <REDACTED>", "Content-Type: application/json"],
                "http_status": status, "error": err, "latency_s": round(time.time() - started, 3),
                "fetched_at": int(started), "response_sha256": hashlib.sha256(raw).hexdigest(), "response_bytes": len(raw),
                "response_file": f"{name}.response.raw"}
        (self.raw_dir / f"{name}.meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8", newline="\n")
        if err or status != 200:
            raise Channel3Error(f"{label}: {err or 'HTTP'} {status}")
        return json.loads(raw), meta

    def search(self, query, max_price=None, limit=10, label="search"):
        body = {"query": query, "limit": limit,
                "filters": {"availability": ["InStock"], "conditions": ["new"],
                            **({"price": {"min_price": None, "max_price": max_price}} if max_price is not None else {})},
                "config": {"country": "US", "currency": "USD"}}
        return self._call("POST", "/v1/search", body, label)

    def product(self, product_id, label="product"):
        return self._call("GET", f"/v1/products/{product_id}?country=US&currency=USD", None, label)


def offers_of(product, meta):
    """Flatten a raw Channel3 product into offer rows, each traceable to the raw response that carried it."""
    rows = []
    brand = ", ".join(b.get("name", "") for b in product.get("brands") or [])
    for i, o in enumerate(product.get("offers") or []):
        price = o.get("price") or {}
        amount = price.get("price", price.get("amount"))
        rows.append({"offer_key": f"{product['id']}#{o.get('domain')}", "product_id": product["id"], "title": product.get("title"),
                     "brand": brand or None, "seller": o.get("domain"), "source": "Channel3 api.trychannel3.com",
                     "price": amount, "compare_at_price": price.get("compare_at_price"), "currency": price.get("currency"),
                     "availability": o.get("availability"), "condition": o.get("condition"), "url": o.get("url"),
                     "rating": (product.get("ratings") or {}).get("value"), "rating_count": (product.get("ratings") or {}).get("count"),
                     "raw_response_sha256": meta["response_sha256"], "raw_response_file": meta["response_file"], "offer_index": i})
    return rows
