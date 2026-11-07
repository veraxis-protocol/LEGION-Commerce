"""DEMO-006 mission: preparation (draft + sandbox runtime keys) and owner authorization (Control Key).

  python -m commerce_demo.mission prepare --merchant <sandbox merchant id>      (Claude)
  python -m commerce_demo.mission authorize                                    (OWNER, interactive console)

Profile sandbox-demo-v1. The owner signs, with the enrolled Control Key, the exact mission (three
obligations, $500 cap, payee, completion criteria, disruption protocol, runtime public keys) and its
control state (epoch 1, active). Agents cannot authorize spending; the buyer approval at PayPal is the
owner's authorization of the final selected purchase.
"""
import argparse
import base64
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from commerce_adapter.contracts import digest
from . import catalog

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "demo006_runs"
KEYDIR = Path(os.environ["LOCALAPPDATA"]) / "Veraxis" / "LEGION-Commerce" / "demo006"
PROFILE = "sandbox-demo-v1"
MISSION_DOMAIN = "commerce.sandbox-demo.mission.v1"
CONTROL_DOMAIN = "commerce.sandbox-demo.control.v1"
RUNTIME_ROLES = ("core_observer", "sandbox_admission", "reporter_1", "reporter_2", "reporter_3", "fulfillment_simulator")
CAP_CENTS = 50000


def current_run():
    p = RUNS / "CURRENT"
    return RUNS / p.read_text().strip()


def seal_runtime_keys(run_id):
    """Sandbox runtime keys (not owner authority): sealed with DPAPI CurrentUser outside the repo/Dropbox."""
    from commerce_authority.custody import _dpapi
    keys = {r: Ed25519PrivateKey.generate() for r in RUNTIME_ROLES}
    raw = json.dumps({r: base64.b64encode(k.private_bytes_raw()).decode() for r, k in keys.items()}).encode()
    KEYDIR.mkdir(parents=True, exist_ok=True)
    (KEYDIR / f"{run_id}.runtime.dpapi").write_bytes(_dpapi(raw, b"DEMO-006-runtime:" + run_id.encode(), True))
    return {r: base64.b64encode(k.public_key().public_bytes_raw()).decode() for r, k in keys.items()}


def load_runtime_keys(run_id):
    from commerce_authority.custody import _dpapi
    raw = _dpapi((KEYDIR / f"{run_id}.runtime.dpapi").read_bytes(), b"DEMO-006-runtime:" + run_id.encode(), False)
    return {r: Ed25519PrivateKey.from_private_bytes(base64.b64decode(v)) for r, v in json.loads(raw).items()}


def prepare(merchant_id):
    from commerce_authority.custody import Store
    owner = Store().active("control")
    now = int(time.time())
    run_id = time.strftime("run-%Y%m%dT%H%M%SZ", time.gmtime(now))
    pubs = seal_runtime_keys(run_id)
    f1 = catalog.feed(1)
    mission = {
        "schema": MISSION_DOMAIN, "profile": PROFILE, "mission_id": f"DEMO-006-{run_id[4:]}", "contract_version": "1",
        "statement": "Procure three specified services with a total authorized budget of $500. Evaluate alternatives, adapt if "
                     "prices change, and execute only a purchase satisfying the original requirements.",
        "obligations": catalog.OBLIGATIONS, "obligation_ids": [o["id"] for o in catalog.OBLIGATIONS],
        "currency": "USD", "cap_cents": CAP_CENTS, "merchant_ids": [merchant_id], "actor": "agent:procurement-executor",
        "epoch": 1, "created_at": now, "deadline": now + 6 * 3600, "max_attempts": 1,
        "payment_form": "ONE PayPal Sandbox order to the merchant above, bundling the three selected service lines; amount = plan total",
        "supplier_data": catalog.SIMULATED, "initial_feed_sha256": catalog.feed_digest(f1),
        "completion_criteria": {
            "per_obligation": "obligation i is COMPLETE iff (1) the final captured PayPal Sandbox order is COMPLETED and independently read "
                              "back with amount = authorized plan total and payee = merchant; (2) the authorized final plan contains exactly one "
                              "line for service i at a current quote of a supplier with rating >= 4.0 and delivery <= 10 days; (3) the SIMULATED "
                              "supplier fulfillment service issues a receipt for service i, signed by the fulfillment_simulator key below, that "
                              "references that PayPal capture id, supplier and price.",
            "mission": "COMPLETE iff all three obligations are COMPLETE, total <= cap, exactly one capture, no unauthorized dispatch, "
                       "within the first and only attempt.",
            "disclosure": "Fulfillment is SIMULATED. A COMPLETE verdict is a Sandbox VCC@1 outcome, not verified real-world delivery."},
        "disruption_protocol": "After the collective's first plan is selected, the disruption injector publishes supplier feed v2 in which the "
                               "selected plan's highest-priced line rises so that the plan total exceeds the $500 cap by 10% of that line's price "
                               "(rounded up to whole dollars). The same attempt must detect it, reject the plan and replan autonomously.",
        "collective": {"registered_agents": 100, "model": "nvidia/nemotron-3.5-lightning-30b-a3b via NVIDIA NIM (enable_thinking=false)",
                       "model_driven_agents": ["agent:planner-1", "agent:planner-2", "agent:planner-3", "agent:planner-4", "agent:planner-5", "agent:chair"]},
        "runtime_public_keys": pubs, "owner_control_key_id": owner["key_id"],
    }
    control = {"schema": CONTROL_DOMAIN, "sequence": 1, "mission_id": mission["mission_id"], "mission_hash": digest(MISSION_DOMAIN, mission),
               "epoch": 1, "active": True, "expires_at": mission["deadline"], "revoked_permits": [], "revoked_actors": []}
    d = RUNS / run_id; d.mkdir(parents=True)
    (d / "MISSION_DRAFT.json").write_text(json.dumps(mission, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    (d / "CONTROL_DRAFT.json").write_text(json.dumps(control, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    (d / "SUPPLIER_FEED_V1.json").write_text(json.dumps(f1, indent=2) + "\n", encoding="utf-8", newline="\n")
    (RUNS / "CURRENT").write_text(run_id)
    return {"run_id": run_id, "mission_id": mission["mission_id"], "mission_hash": control["mission_hash"]}


def authorize():
    """OWNER ONLY: interactive console. Shows the exact terms, requires typed confirmation and the control passphrase."""
    from commerce_authority import authority as A
    from commerce_authority.custody import Store, interactive_passphrase
    d = current_run()
    mission = json.loads((d / "MISSION_DRAFT.json").read_text())
    control = json.loads((d / "CONTROL_DRAFT.json").read_text())
    assert control["mission_hash"] == digest(MISSION_DOMAIN, mission)
    print("\n=== LEGION Commerce DEMO-006 - mission authorization (sandbox-demo-v1) ===", file=sys.stderr)
    print(f"Mission {mission['mission_id']}: {mission['statement']}", file=sys.stderr)
    for o in mission["obligations"]:
        print(f"  {o['id']}: {o['service']}  [{o['requirement']}]", file=sys.stderr)
    print(f"Budget cap: ${mission['cap_cents'] / 100:.2f} USD   Payee (PayPal Sandbox merchant): {mission['merchant_ids'][0]}", file=sys.stderr)
    print(f"Deadline: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(mission['deadline']))}   Attempts: 1   Supplier data: SIMULATED", file=sys.stderr)
    print(f"Payment: {mission['payment_form']}", file=sys.stderr)
    print("You will still approve the exact final amount yourself in the PayPal Sandbox buyer window.", file=sys.stderr)
    token = mission["mission_id"][-6:]
    if input(f"Type AUTHORIZE {token} to sign this mission and its control state: ").strip() != f"AUTHORIZE {token}":
        print(json.dumps({"status": "NOT_AUTHORIZED"})); return 2
    pw = interactive_passphrase("control passphrase: ")
    store = Store()
    m_env = A.role_sign(store, "control", MISSION_DOMAIN, mission, pw)
    c_env = A.role_sign(store, "control", CONTROL_DOMAIN, control, pw)
    pw = None
    (d / "MISSION_AUTHORIZED.json").write_text(json.dumps(m_env, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    (d / "CONTROL_AUTHORIZED.json").write_text(json.dumps(c_env, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"status": "AUTHORIZED", "mission_id": mission["mission_id"], "mission_hash": control["mission_hash"],
                      "signed_by_control_key": store.active("control")["key_id"]}))
    return 0


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("command", choices=("prepare", "authorize")); ap.add_argument("--merchant")
    a = ap.parse_args()
    if a.command == "prepare":
        print(json.dumps(prepare(a.merchant))); return 0
    return authorize()


if __name__ == "__main__":
    raise SystemExit(main())
