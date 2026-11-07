"""DEMO-006 independent provider readback: a SEPARATE process, read-only ledger, fresh PayPal GETs only.

Usage: python -m commerce_demo.readback <run_dir> <effect_id>
Writes PAYPAL_NATIVE_READBACK.json. Buyer identifiers (payer name/email/id) are redacted.
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

from commerce_adapter.contracts import digest, provider_id, require, verify
from commerce_adapter.gateway import Gateway
from commerce_adapter.paypal import PayPalSandbox
from . import sandbox_admission as S


def main():
    run, eid = Path(sys.argv[1]), sys.argv[2]
    mission_env = json.loads((run / "MISSION_AUTHORIZED.json").read_text())
    owner = json.loads((run / "OWNER_CONTROL_PUBLIC.json").read_text())["public_key"]
    db = sqlite3.connect((run / "ledger.db").resolve().as_uri() + "?mode=ro", uri=True); db.row_factory = sqlite3.Row
    try:
        r = db.execute("SELECT * FROM effects WHERE id=?", (eid,)).fetchone()
        require(r is not None and r["order_id"] and r["capture_started"] == 1, "NO_CAPTURE_TO_VERIFY")
        n_effects = db.execute("SELECT COUNT(*) FROM effects").fetchone()[0]
        mission = verify(mission_env, owner, S.MISSION_DOMAIN)
        permit = verify(json.loads(r["permit"]), mission["runtime_public_keys"]["sandbox_admission"], S.PERMIT_DOMAIN)
        require(permit["mission_hash"] == digest(S.MISSION_DOMAIN, mission) and permit["intent"] == json.loads(r["intent"]), "READBACK_AUTHORITY_BINDING")
        provider = PayPalSandbox(read_only=True)
        order = provider.request("GET", f"/v2/checkout/orders/{provider_id(r['order_id'])}")[1]
        unit = Gateway.validate_order(None, r, order)
        captures = unit.get("payments", {}).get("captures", [])
        require(len(captures) == 1 and order["status"] == "COMPLETED", "NONFINAL_OR_DUPLICATE")
        cid = provider_id(captures[0]["id"])
        detail = provider.request("GET", f"/v2/payments/captures/{cid}")[1]
        require(detail["id"] == cid and detail["status"] == captures[0]["status"] == "COMPLETED"
                and detail["amount"] == captures[0]["amount"] == unit["amount"]
                and detail["supplementary_data"]["related_ids"]["order_id"] == r["order_id"], "READBACK_MISMATCH")
        out = {"schema": "commerce.demo.paypal-readback.v1", "process": "separate (python -m commerce_demo.readback)", "source": provider.evidence_mode,
               "environment": "PAYPAL_SANDBOX (no real money)", "effect_id": eid, "ledger_effects": n_effects,
               "order": {"id": order["id"], "status": order["status"], "intent": order["intent"], "create_time": order.get("create_time"),
                         "update_time": order.get("update_time"), "payee_merchant_id": unit["payee"]["merchant_id"],
                         "amount": unit["amount"], "description": unit.get("description"), "invoice_id": unit.get("invoice_id"),
                         "payer": "REDACTED (sandbox buyer identifiers not retained)"},
               "capture": {"id": cid, "status": detail["status"], "amount": detail["amount"], "final_capture": detail.get("final_capture"),
                           "create_time": detail.get("create_time"), "update_time": detail.get("update_time"),
                           "related_order_id": detail["supplementary_data"]["related_ids"]["order_id"]},
               "captures_on_order": len(captures), "order_digest": digest("paypal.order.readback.v1", order),
               "capture_digest": digest("paypal.capture.readback.v1", detail), "readback_at": int(time.time()),
               "payment_status": "VERIFIED_COMPLETED"}
    finally:
        db.close()
    (run / "PAYPAL_NATIVE_READBACK.json").write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"readback": "VERIFIED_COMPLETED", "order_id": out["order"]["id"], "capture_id": cid}))


if __name__ == "__main__":
    main()
