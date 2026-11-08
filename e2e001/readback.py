"""WO-COM-E2E-001 readback, run as a separate process with no shared memory with the orchestrator.

MECHANISM_ONLY: the provider is the synthetic FakePayPal, whose final state the orchestrator exported. A native run
would query PayPal Sandbox GET /v2/checkout/orders/{id} and /v2/payments/captures/{id} with read-only credentials.
"""
import json
import sqlite3
import sys
from pathlib import Path


def main():
    run = Path(sys.argv[1])
    prov = json.loads((run / "PROVIDER_STATE_MECHANISM_ONLY.json").read_text())
    con = sqlite3.connect(f"file:{run / 'ledger.db'}?mode=ro", uri=True); con.row_factory = sqlite3.Row
    effects = [dict(r) for r in con.execute("SELECT id,amount,state,order_id,capture_id FROM effects")]
    con.close()
    out = {"evidence_mode": "MECHANISM_ONLY", "provider": "FakePayPal (synthetic)", "effects": []}
    for e in effects:
        order = prov["orders"].get(e["order_id"] or "")
        cap = prov["captures"].get(e["capture_id"] or "")
        amount = cap and cap["amount"]
        out["effects"].append({"effect_id": e["id"], "ledger_state": e["state"], "ledger_amount_cents": e["amount"],
                               "order_id": e["order_id"], "order_status": order and order["status"],
                               "capture_id": e["capture_id"], "capture_status": cap and cap["status"], "capture_amount": amount,
                               "capture_order_link": cap and cap["supplementary_data"]["related_ids"]["order_id"],
                               "match": bool(order and cap and order["status"] == "COMPLETED" and cap["status"] == "COMPLETED"
                                             and amount["currency_code"] == "USD" and round(float(amount["value"]) * 100) == e["amount"]
                                             and cap["supplementary_data"]["related_ids"]["order_id"] == e["order_id"])})
    out["capture_posts"] = sum(1 for m, p, _ in prov["calls"] if m == "POST" and p.endswith("/capture"))
    out["create_posts"] = sum(1 for m, p, _ in prov["calls"] if m == "POST" and p == "/v2/checkout/orders")
    (run / "READBACK.json").write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"readback": "written", "match": all(e["match"] for e in out["effects"])}))


if __name__ == "__main__":
    main()
