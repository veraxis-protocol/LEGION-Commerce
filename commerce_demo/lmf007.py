"""WO-LMF-007: payer-specific PayPal Sandbox captures for the five buyer lines of a verified federation contract.

prepare --merchant M --contract-run <WO-LMF-006 run> [--payers b1,b2,...]
    Verifies the federation contract and each buyer's OWN signed ACCEPT commitment for its exact $99.00 line, then
    writes a sandbox-demo-v1 mission draft (one obligation per payer) for the HUMAN owner to sign with the Control Key
    (python -m commerce_demo.mission authorize). Nothing is paid here.
run
    For each payer, in order: fresh qualified Lean T for the exact (payer slot, amount, merchant) action, A04 reserve ->
    create (PayPal Sandbox order) -> the Sandbox buyer approves on PayPal's own page -> capture (fresh Lean T again) ->
    independent separate-process readback. Never more than one capture per payer; budget <= mission cap ($495).
    A payer that is not approved within its window ends that payer's effect safely; the others are unaffected.
"""
import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

from commerce_adapter.contracts import digest, verify
from commerce_adapter.gateway import Gateway
from commerce_adapter.paypal import PayPalSandbox
from commerce_bridge.consolidated import install
from commerce_bridge.qualified import QualifiedSources
from commerce_authority.custody import Store

from . import sandbox_admission as S
from .events import Log
from .mission import RUNS, current_run, load_runtime_keys, seal_runtime_keys

ROOT = Path(__file__).resolve().parent.parent
CALLBACK_PORT = 8765
sys.path.insert(0, str(ROOT.parent / "MARKET_GENESIS" / "LEGION-Federation"))


def load_contract(run):
    from legion_federation import crypto
    from legion_federation.canonical import sha256_hex
    run = Path(run)
    hub = json.loads((run / "HUB_RESULT.json").read_text())
    judge = json.loads((run / "JUDGE_RESULT.json").read_text())
    if judge["judgement"]["payload"]["verdict"] != "PASS_COMPLETED":
        raise SystemExit("federation run was not PASS_COMPLETED by its Judge")
    chash = hub["contract_hash"]
    contract = next(json.loads(l)["data"]["contract"] for l in open(run / "CONTRACT_VERSIONS.jsonl")
                    if json.loads(l)["data"]["contract_hash"] == chash)
    if sha256_hex(contract) != chash:
        raise SystemExit("contract hash mismatch")
    trust = json.loads((run / "TRUST_ROOTS.json").read_text())["keys"]
    keys = {crypto.key_id(p): p for p in trust.values()}
    owner_of = {crypto.key_id(p): o for o, p in trust.items()}
    consents = {}
    for l in open(run / "OWNER_COMMITMENTS.jsonl"):
        d = json.loads(l)["data"]
        env = d.get("commitment")
        if d["contract_hash"] != chash or not env:
            continue
        p = crypto.verify(env, crypto.DOMAINS["commitment"], trusted=keys)
        if owner_of[env["signer"]["key_id"]] == p["owner_id"] and p["decision"] == "ACCEPT":
            consents[p["owner_id"]] = {"commitment_id": p["commitment_id"], "max_amount_cents": p["max_amount_cents"],
                                       "envelope_sha256": sha256_hex(env), "signer_key_id": env["signer"]["key_id"]}
    return chash, contract, consents


def prepare(merchant, contract_run, payers):
    chash, contract, consents = load_contract(contract_run)
    owner = Store().active("control")
    now = int(time.time())
    run_id = time.strftime("lmf007-%Y%m%dT%H%M%SZ", time.gmtime(now))
    pubs = seal_runtime_keys(run_id)
    lines = []
    for b in payers:
        oid = f"owner:{b}"
        amt = sum(p["amount_cents"] for p in contract["payments"] if p["payer"] == oid)
        c = consents.get(oid)
        if not c or c["max_amount_cents"] != amt:
            raise SystemExit(f"{oid}: no valid own ACCEPT commitment for exactly {amt} cents")
        lines.append({"payer": oid, "obligation_id": f"pay-{b}", "effect_slot": f"payer-{b}", "amount_cents": amt,
                      "payer_commitment": c})
    cap = sum(l["amount_cents"] for l in lines)
    mission = {
        "schema": S.MISSION_DOMAIN, "profile": "sandbox-demo-v1", "mission_id": f"LMF-007-{run_id[7:]}", "contract_version": "1",
        "statement": f"Settle {len(lines)} buyer line(s) of federation contract {chash[:16]} as separate PayPal Sandbox "
                     f"payments of ${lines[0]['amount_cents'] / 100:.2f} each to the merchant below, each approved by its own "
                     "Sandbox buyer. Production and delivery remain SIMULATED.",
        "obligations": [{"id": l["obligation_id"], "service": f"payment line of {l['payer']}",
                         "requirement": f"${l['amount_cents'] / 100:.2f}; payer consent {l['payer_commitment']['commitment_id']}"}
                        for l in lines],
        "obligation_ids": [l["obligation_id"] for l in lines], "payer_obligation_ids": [l["obligation_id"] for l in lines],
        "payer_lines": lines, "federation_contract_hash": chash,
        "currency": "USD", "cap_cents": cap, "merchant_ids": [merchant], "actor": "agent:procurement-executor",
        "epoch": 1, "created_at": now, "deadline": now + 6 * 3600, "max_attempts": 1,
        "payment_form": f"{len(lines)} separate PayPal Sandbox orders (one per buyer line), each captured at most once",
        "runtime_public_keys": pubs, "owner_control_key_id": owner["key_id"]}
    control = {"schema": S.CONTROL_DOMAIN, "sequence": 1, "mission_id": mission["mission_id"],
               "mission_hash": digest(S.MISSION_DOMAIN, mission), "epoch": 1, "active": True, "expires_at": mission["deadline"],
               "revoked_permits": [], "revoked_actors": []}
    d = RUNS / run_id
    d.mkdir(parents=True)
    (d / "MISSION_DRAFT.json").write_text(json.dumps(mission, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    (d / "CONTROL_DRAFT.json").write_text(json.dumps(control, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    (d / "FEDERATION_CONTRACT.json").write_text(json.dumps({"contract_hash": chash, "contract": contract, "consents": consents,
                                                           "source_run": str(contract_run)}, indent=1))
    (RUNS / "CURRENT").write_text(run_id)
    print(json.dumps({"run_id": run_id, "mission_id": mission["mission_id"], "cap_cents": cap, "payers": payers}))


def run(window_s=900):
    d = current_run()
    m_env = json.loads((d / "MISSION_AUTHORIZED.json").read_text())
    c_env = json.loads((d / "CONTROL_AUTHORIZED.json").read_text())
    owner = Store().active("control")
    mission = verify(m_env, owner["public_key"], S.MISSION_DOMAIN)
    log = Log(d / "END_TO_END_EVENTS.jsonl", mission["mission_id"] + "-ATTEMPT-1")
    sources = QualifiedSources(ROOT / "runtime_sources", immutable=True)
    install(sources, hot_standby_seconds=60.0)
    runtime = load_runtime_keys(d.name)
    adm = S.SandboxDemoAdmission(owner["public_key"], mission["runtime_public_keys"], sources,
                                 observer=S.fresh_observer(runtime, sources))
    g = Gateway(d / "ledger.db", adm, PayPalSandbox(read_only=False))
    g.apply_control(mission["mission_id"], c_env)
    results = []
    from commerce_adapter.approval import make_server
    try:
        for line in mission["payer_lines"]:
            plan = [{"service_id": f"LMF-{line['payer'].split(':')[1].upper()}", "supplier_id": "owner:f1",
                     "quote_id": f"{mission['federation_contract_hash'][:16]}-{line['obligation_id']}",
                     "price_cents": line["amount_cents"], "effect_slot": line["effect_slot"], "obligation_id": line["obligation_id"]}]
            p_env, decision, _ = S.issue_permit(adm, m_env, runtime, plan, line["amount_cents"], sources)
            log.emit("LEAN_DECISION", "qualified-core", payer=line["payer"], verdict=decision["lean_verdict"],
                     permit_id=p_env["payload"]["permit_id"], amount_cents=line["amount_cents"])
            eid = g.reserve(m_env, p_env)
            g.create(eid, CALLBACK_PORT)
            st = g.status(eid)
            log.emit("ORDER_CREATED", "a04-gateway", payer=line["payer"], effect_id=eid, order_id=st.get("order_id"))
            (d / "CURRENT_APPROVAL.json").write_text(json.dumps({"payer": line["payer"], "approval_url": st.get("approval_url"),
                                                                 "amount": line["amount_cents"]}))
            print(json.dumps({"payer": line["payer"], "approve_here": st.get("approval_url")}), flush=True)
            cb = make_server(g, eid, CALLBACK_PORT)
            threading.Thread(target=cb.serve_forever, daemon=True).start()
            end = time.time() + window_s
            while time.time() < end:
                s = g.status(eid)["state"]
                if s in ("CAPTURE_CONFIRMED", "FAILED_RELEASED", "RECONCILIATION_PENDING"):
                    break
                time.sleep(2)
            cb.shutdown()
            cb.server_close()
            state = g.status(eid)["state"]
            r = {"payer": line["payer"], "effect_id": eid, "state": state, "order_id": g.status(eid).get("order_id")}
            if state == "CAPTURE_CONFIRMED":
                subprocess.run([sys.executable, "-B", "-m", "commerce_demo.readback", str(d), eid], cwd=ROOT, check=True)
                rb = d / "PAYPAL_NATIVE_READBACK.json"
                shutil.copy(rb, d / f"PAYPAL_NATIVE_READBACK_{line['payer'].split(':')[1]}.json")
                r["readback"] = json.loads(rb.read_text())
            log.emit("PAYER_RESULT", "a04-gateway", **{k: v for k, v in r.items() if k != "readback"})
            results.append(r)
            print(json.dumps({"payer": line["payer"], "state": state}), flush=True)
    finally:
        sources.close()
    con = sqlite3.connect(d / "ledger.db")
    con.row_factory = sqlite3.Row
    effects = [dict(x) for x in con.execute("SELECT id,mission_id,obligation_id,permit_id,amount,held,state,order_id,capture_id FROM effects")]
    evs = [dict(x) for x in con.execute("SELECT seq,at,effect_id,kind,data FROM events ORDER BY seq")]
    con.close()
    (d / "BUDGET_LEDGER.json").write_text(json.dumps({"cap_cents": mission["cap_cents"], "effects": effects, "events": evs,
                                                      "network_posts": sum(1 for e in evs if e["kind"] == "NETWORK"
                                                                           and json.loads(e["data"]).get("method") == "POST")}, indent=1))
    captured = [r for r in results if r["state"] == "CAPTURE_CONFIRMED"]
    status = "PAYPAL_FULL_GO_CANDIDATE" if len(captured) == 5 else "PAYPAL_PARTIAL_CANDIDATE" if captured else "PAYPAL_BLOCKED"
    (d / "PAYMENT_RESULTS.json").write_text(json.dumps({"status_before_judge": status, "results": results}, indent=1, default=str))
    print(json.dumps({"captured": len(captured), "status_before_judge": status}))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--merchant", required=True)
    p.add_argument("--contract-run", required=True)
    p.add_argument("--payers", default="b1,b2,b3,b4,b5")
    r = sub.add_parser("run")
    r.add_argument("--window", type=int, default=900)
    a = ap.parse_args()
    if a.cmd == "prepare":
        prepare(a.merchant, a.contract_run, a.payers.split(","))
    else:
        run(a.window)


if __name__ == "__main__":
    main()
