"""Fresh provider reads from a separate CLI process; no POST and no stored receipt trust.

Uses the same merchant credential when a separate least-privilege credential is
unavailable. Process independence is not institutional/Judge independence (A05).
"""
import json
import sqlite3
import time
from .contracts import digest, provider_id, require, verify
from .gateway import Gateway


def independent_readback(database, eid, admission, provider):
    db = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        r = db.execute("SELECT * FROM effects WHERE id=?", (eid,)).fetchone()
        require(r is not None and r["order_id"] and r["capture_started"] == 1, "NO_CAPTURE_TO_VERIFY")
        mission = verify(json.loads(r["mission"]), admission.trust["control"], "commerce.mission.v1")
        permit = verify(json.loads(r["permit"]), admission.trust["admission"], "commerce.permit.v1")
        require(permit["mission_hash"] == digest("commerce.mission.v1", mission)
                and permit["intent"] == json.loads(r["intent"]) and permit["intent"] in mission["effects"], "READBACK_AUTHORITY_BINDING")
        order = provider.request("GET", f"/v2/checkout/orders/{provider_id(r['order_id'])}")[1]
        unit = Gateway.validate_order(None, r, order)
        captures = unit.get("payments", {}).get("captures", [])
        require(len(captures) == 1 and order["status"] == "COMPLETED", "NONFINAL_OR_DUPLICATE")
        capture = captures[0]
        cid = provider_id(capture["id"])
        detail = provider.request("GET", f"/v2/payments/captures/{cid}")[1]
        require(detail["id"] == cid and detail["status"] == capture["status"] == "COMPLETED"
                and detail["amount"] == capture["amount"] == unit["amount"]
                and detail["supplementary_data"]["related_ids"]["order_id"] == r["order_id"], "READBACK_MISMATCH")
        return {"effect_id": eid, "order_id": r["order_id"], "capture_id": cid,
                "amount_cents": r["amount"], "currency": "USD", "payment_status": "VERIFIED_COMPLETED",
                "source": provider.evidence_mode, "readback_at": int(time.time()),
                "order_digest": digest("paypal.order.readback.v1", order),
                "capture_digest": digest("paypal.capture.readback.v1", detail),
                "mission_complete": False, "a05_verdict": "REQUIRED_SEPARATELY"}
    finally:
        db.close()
