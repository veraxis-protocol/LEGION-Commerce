"""WO-LMF-007 independent commerce Judge (separate, read-only process).

Binds every PayPal Sandbox capture to exactly one original buyer obligation of the verified federation contract.
Inputs (read-only): owner-signed mission, read-only SQLite ledger, per-payer independent provider readbacks,
the federation run's contract, commitments and its own Judge result. Self-reported states never count.
Usage: python -m commerce_demo.lmf007_judge <lmf007 run dir>
Writes PAYPAL_JUDGE_RESULT.json with the verdict PAYPAL_FULL_GO | PAYPAL_PARTIAL | PAYPAL_BLOCKED, plus mutants.
"""
import copy
import json
import sqlite3
import sys
from pathlib import Path

from commerce_adapter.contracts import digest, verify
from . import sandbox_admission as S


def evaluate(run, mutate=None):
    run = Path(run)
    checks = []

    def add(name, ok, **kw):
        checks.append(dict({"check": name, "pass": bool(ok)}, **kw))

    owner = json.loads((run / "OWNER_CONTROL_PUBLIC.json").read_text())["public_key"]
    m = verify(json.loads((run / "MISSION_AUTHORIZED.json").read_text()), owner, S.MISSION_DOMAIN)
    add("P1_mission_signed_by_owner_control_key", True, mission_id=m["mission_id"])
    fed = json.loads((run / "FEDERATION_CONTRACT.json").read_text())
    add("P1_mission_bound_to_federation_contract", m["federation_contract_hash"] == fed["contract_hash"])
    src = Path(fed["source_run"])
    fj = json.loads((src / "JUDGE_RESULT.json").read_text())["judgement"]["payload"]["verdict"] if (src / "JUDGE_RESULT.json").exists() else None
    add("P1_synthetic_obligations_independently_verified", fj == "PASS_COMPLETED", federation_judge=fj)
    db = sqlite3.connect((run / "ledger.db").resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    effects = [dict(r) for r in db.execute("SELECT * FROM effects")]
    events = [dict(r) for r in db.execute("SELECT * FROM events ORDER BY seq")]
    db.close()
    if mutate == "duplicate_capture" and effects:
        effects.append(dict(effects[0], id="dup"))
    lines = {l["obligation_id"]: l for l in m["payer_lines"]}
    by_ob = {}
    for e in effects:
        by_ob.setdefault(e["obligation_id"], []).append(e)
    add("P2_no_effect_outside_payer_obligations", set(by_ob) <= set(lines), extra=sorted(set(by_ob) - set(lines)))
    add("P2_at_most_one_effect_per_payer", all(len(v) == 1 for v in by_ob.values()))
    captured = []
    for ob, line in lines.items():
        es = by_ob.get(ob, [])
        if not es:
            continue
        e = es[0]
        if e["state"] != "CAPTURE_CONFIRMED":
            add("P3_uncaptured_payer_has_no_capture_id", not e["capture_id"], payer=line["payer"], state=e["state"])
            continue
        payer = line["payer"].split(":")[1]
        rbp = run / f"PAYPAL_NATIVE_READBACK_{payer}.json"
        if mutate == "missing_readback" and not captured:
            rbp = run / "nonexistent.json"
        if not rbp.exists():
            add("P3_independent_readback_present", False, payer=line["payer"])
            continue
        rb = json.loads(rbp.read_text())
        if mutate == "forged_amount" and not captured:
            rb = copy.deepcopy(rb)
            rb["capture"]["amount"]["value"] = "98.00"
        if mutate == "wrong_payee" and not captured:
            rb = copy.deepcopy(rb)
            rb["order"]["payee_merchant_id"] = "ATTACKER0001"
        ok = (rb["process"].startswith("separate") and rb["environment"].startswith("PAYPAL_SANDBOX")
              and rb["order"]["status"] == "COMPLETED" and rb["capture"]["status"] == "COMPLETED"
              and rb["captures_on_order"] == 1 and rb["order"]["id"] == e["order_id"] and rb["capture"]["id"] == e["capture_id"]
              and rb["capture"]["related_order_id"] == e["order_id"] and rb["order"]["payee_merchant_id"] in m["merchant_ids"]
              and rb["capture"]["amount"] == {"currency_code": "USD", "value": f"{line['amount_cents'] / 100:.2f}"}
              and rb["effect_id"] == e["id"])
        add("P3_capture_bound_to_buyer_obligation_by_provider_readback", ok, payer=line["payer"], order_id=e["order_id"],
            capture_id=e["capture_id"])
        if ok:
            captured.append({"payer": line["payer"], "obligation_id": ob, "order_id": e["order_id"], "capture_id": e["capture_id"],
                             "amount": rb["capture"]["amount"], "payer_consent": line["payer_commitment"]["commitment_id"]})
    total = sum(int(round(float(c["amount"]["value"]) * 100)) for c in captured)
    add("P4_total_captured_within_owner_cap", total <= m["cap_cents"], total_cents=total, cap_cents=m["cap_cents"])
    posts = [json.loads(e["data"]) for e in events if e["kind"] == "NETWORK" and json.loads(e["data"]).get("method") == "POST"]
    add("P4_post_count_consistent", len(posts) <= 2 * len(effects), posts=len(posts), effects=len(effects))
    add("P4_no_safety_stop", not any(e["kind"] == "SAFETY_STOP" for e in events))
    ok_all = all(c["pass"] for c in checks)
    n = len(captured)
    verdict = ("PAYPAL_FULL_GO" if ok_all and n == len(lines) == 5 else "PAYPAL_PARTIAL" if ok_all and n >= 1
               else "PAYPAL_BLOCKED" if ok_all else "FAIL")
    return {"verdict": verdict, "captures_verified": n, "captured": captured, "checks": checks}


def main():
    run = Path(sys.argv[1])
    res = evaluate(run)
    mutants = {}
    for mu in ["forged_amount", "wrong_payee", "missing_readback", "duplicate_capture"]:
        r = evaluate(run, mu)
        mutants[mu] = {"verdict": r["verdict"], "detected": r["verdict"] != res["verdict"] or res["captures_verified"] == 0}
    res["tamper_mutants"] = mutants
    res["judge"] = "separate read-only process; ledger opened mode=ro; provider data only from independent readback files"
    (run / "PAYPAL_JUDGE_RESULT.json").write_text(json.dumps(res, indent=1, sort_keys=True))
    print(json.dumps({"verdict": res["verdict"], "captures_verified": res["captures_verified"],
                      "failed": [c for c in res["checks"] if not c["pass"]], "mutants": mutants}))


if __name__ == "__main__":
    main()
