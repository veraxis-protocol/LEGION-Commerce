"""WO-COM-E2E-001 independent Judge: separate, read-only process; no network; trusts no agent claim.

  python -B -m e2e001.judge <run_dir>              -> JUDGE_VERDICT.json
  python -B -m e2e001.judge <run_dir> --negatives  -> JUDGE_NEGATIVES.json (mutated copies must FAIL)

Evidence only: the signed mission/control, the hash-chained event log, the verbatim agent trace, the raw Channel3
responses (re-hashed and re-parsed here), the plan trace, the permit and Lean decision, the A04 ledger export and the
separate-process readback. The verdict is at most PASS_MECHANISM_ONLY while the provider is synthetic: it never
asserts a native PayPal payment.
"""
import copy
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

from commerce_adapter.contracts import Denied, digest, verify
from commerce_demo import sandbox_admission as S
from commerce_demo.events import verify_chain


def load(run, n):
    return json.loads((run / n).read_text(encoding="utf-8"))


def judge(run):
    checks = []

    def c(name, ok, detail=""):
        checks.append({"check": name, "pass": bool(ok), "detail": detail})

    def safe(name, fn):
        try:
            ok, detail = fn()
        except (Denied, KeyError, TypeError, ValueError, AssertionError, FileNotFoundError, StopIteration) as e:
            ok, detail = False, f"{type(e).__name__}: {e}"
        c(name, ok, detail)

    ok, why, attempts = verify_chain(run / "E2E_EVENTS.jsonl")
    c("J01_event_chain_intact_single_attempt", ok and len(attempts) == 1, f"{why}; attempts={sorted(attempts)}")
    events = [json.loads(l) for l in (run / "E2E_EVENTS.jsonl").read_text(encoding="utf-8").splitlines()]
    kinds = [e["kind"] for e in events]
    ev = lambda k: [e for e in events if e["kind"] == k]
    M = load(run, "MISSION.json")
    holder = {}

    def j02():
        m = verify(M["mission_envelope"], M["owner_test_public_key"], S.MISSION_DOMAIN)
        ctl = verify(M["control_envelope"], M["owner_test_public_key"], S.CONTROL_DOMAIN)
        holder["m"] = m
        return (m["cap_cents"] == 50000 and m["currency"] == "USD" and ctl["mission_hash"] == digest(S.MISSION_DOMAIN, m)
                and ctl["active"] is True), f"cap={m['cap_cents']} authority={m['authority']}"
    safe("J02_mission_and_control_signed_cap_500", j02)
    m = holder.get("m", {})

    raw_dir = run / "CHANNEL3_RAW"
    metas = {}
    for p in sorted(raw_dir.glob("*.meta.json")):
        meta = json.loads(p.read_text())
        raw = (raw_dir / meta["response_file"]).read_bytes()
        metas[meta["response_sha256"]] = (meta, raw)

    def j03():
        bad = [m_["response_file"] for m_, raw in metas.values() if hashlib.sha256(raw).hexdigest() != m_["response_sha256"]]
        return not bad and metas, f"{len(metas)} raw Channel3 responses re-hashed; mismatches={bad}"
    safe("J03_raw_channel3_responses_intact", j03)

    offers = load(run, "OFFERS.json")["offers"]

    def j04():
        traced = 0
        for o in offers:
            meta, raw = metas[o["raw_response_sha256"]]
            body = json.loads(raw)
            prods = body["products"] if "products" in body else [body]
            prod = next(p for p in prods if p["id"] == o["product_id"])
            off = prod["offers"][o["offer_index"]]
            assert off["domain"] == o["seller"] and off["price"]["price"] == o["price"] and off["price"]["currency"] == o["currency"]
            assert off["availability"] == "InStock" and o["currency"] == "USD" and 0 < round(o["price"] * 100) <= 50000
            traced += 1
        sellers = {o["seller"] for o in offers}
        return traced >= 3 and len({o["product_id"] for o in offers}) >= 3, f"{traced} offers traced to raw responses; {len(sellers)} sellers"
    safe("J04_at_least_three_genuine_offers_traced_to_raw", j04)

    trace = [json.loads(l) for l in (run / "AGENT_TRACE.jsonl").read_text(encoding="utf-8").splitlines()]

    def j05():
        props = [t for t in trace if t["purpose"] == "proposal" and t["parsed"] and t["response"].get("response_id")]
        sels = [t for t in trace if t["purpose"].startswith("selection") and t["parsed"] and t["response"].get("response_id")]
        return len({t["agent"] for t in props}) >= 2 and len(sels) >= 2, f"{len(props)} model proposals, {len(sels)} model selections"
    safe("J05_model_driven_proposals_and_selections_recorded", j05)

    P = load(run, "PLAN_TRACE.json")
    keys = {o["offer_key"]: o for o in offers}

    def j06():
        s1, s2, ch = P["selection_initial"], P["selection_replan"], P["changed_offer"]
        order = [kinds.index(k) for k in ("PLAN_SELECTED", "CHANGED_OFFER", "PLAN_RECHECKED", "PLAN_REJECTED")]
        sel_events = ev("PLAN_SELECTED")
        assert s1["offer_key"] in keys and s2["offer_key"] in keys and s1["offer_key"] != s2["offer_key"]
        assert ch["offer_key"] == s1["offer_key"] and P["recheck_reasons"] and order == sorted(order)
        assert len(sel_events) == 2 and sel_events[1]["seq"] > ev("PLAN_REJECTED")[0]["seq"]
        if ch["change_type"] == "INJECTED":
            meta, raw = metas[ch["live_requote_unchanged"]["raw_response_sha256"]]
            assert meta["label"] == "requote_initial"
        return True, f"changed offer {ch['change_type']} {ch['old_price']}->{ch['new_price']}; reasons {P['recheck_reasons']}; replanned to {s2['offer_key']}"
    safe("J06_changed_offer_detected_rejected_and_replanned", j06)

    line = P["final_line"]

    def j07():
        meta, raw = metas[line["quote_raw_response_sha256"]]
        prod = json.loads(raw)
        off = next(o for o in prod["offers"] if o["domain"] == line["seller"])
        return (round(off["price"]["price"] * 100) == line["price_cents"] <= 50000 and off["availability"] == "InStock"
                and line["offer_key"] == P["selection_replan"]["offer_key"]), f"final {line['offer_key']} {line['price_cents']} cents (live re-quote {meta['label']})"
    safe("J07_final_plan_matches_live_requote_within_cap", j07)

    L = load(run, "LEAN_AUTHORIZATION.json")

    def j08():
        p = verify(L["permit_envelope"], m["runtime_public_keys"]["sandbox_admission"], S.PERMIT_DOMAIN)
        intent = p["intent"]
        d = L["decision"]
        assert d["lean_verdict"] == "T" and d["directive"]["directive"] == "ACT" and d["action_digest"] == digest("commerce.intent.v1", intent)
        assert intent["amount_cents"] == line["price_cents"] and p["plan_lines"] == [line] and intent["quote_hash"] == S.plan_hash([line])
        assert p["mission_hash"] == digest(S.MISSION_DOMAIN, m) and L["decider_sha256"].startswith("99cda684")
        return True, f"Lean T; permit {p['permit_id']}; amount {intent['amount_cents']}"
    safe("J08_formal_admission_lean_T_bound_to_exact_action", j08)

    B = load(run, "BUDGET_LEDGER.json")

    def j09():
        effs = B["effects"]
        posts = [e for e in B["ledger_events"] if e["kind"] == "NETWORK" and e["data"].get("method") == "POST"]
        assert len(effs) == 1 and effs[0]["amount"] == line["price_cents"] <= m["cap_cents"] and effs[0]["state"] == "CAPTURE_CONFIRMED"
        assert len(posts) == 2 and all(e["effect_id"] == effs[0]["id"] for e in posts)
        assert ev("PAYMENT_STATE")[0]["data"]["capture_posts"] == 1
        return True, f"1 effect {effs[0]['state']}; {len(posts)} POSTs (create, capture); duplicate capture not dispatched"
    safe("J09_exactly_one_authorized_payment_no_duplicate", j09)

    R = load(run, "READBACK.json")
    safe("J10_independent_readback_matches_ledger",
         lambda: (len(R["effects"]) == 1 and R["effects"][0]["match"] and R["capture_posts"] == 1 and R["create_posts"] == 1,
                  f"{R['evidence_mode']} readback match={R['effects'][0]['match']}"))

    def j11():
        gates = ev("PRICE_GATE")
        lean_before = all(any(l["seq"] < x["seq"] for l in ev("LEAN_DECISION")) for x in ev("ORDER_CREATED"))
        return gates and all(g["data"]["ok"] for g in gates) and lean_before, f"{len(gates)} live price gates, all ok"
    safe("J11_every_gate_fresh_price_and_lean_before_effect", j11)

    failed = [x["check"] for x in checks if not x["pass"]]
    verdict = {"verdict": "PASS_MECHANISM_ONLY" if not failed else "FAIL", "evidence_mode": "MECHANISM_ONLY",
               "native_paypal_payment": False, "checks": checks, "failed": failed,
               "note": "Synthetic provider and TEST authority: this verdict does not establish a native PayPal Sandbox payment or A03 acceptance."}
    return verdict


def mutate(src, fn):
    d = Path(tempfile.mkdtemp(prefix="e2e-judge-neg-"))
    shutil.copytree(src, d / "run")
    fn(d / "run")
    return d


def edit(path, f):
    v = json.loads(path.read_text()); f(v); path.write_text(json.dumps(v, indent=2, sort_keys=True))


def negatives(run):
    cases = {
        "N1_tampered_event_log": lambda d: (d / "E2E_EVENTS.jsonl").write_text(
            (d / "E2E_EVENTS.jsonl").read_text().replace('"kind": "PLAN_REJECTED"', '"kind": "PLAN_REJECTEX"', 1)),
        "N2_offer_price_not_in_raw": lambda d: edit(d / "OFFERS.json", lambda v: v["offers"][0].__setitem__("price", v["offers"][0]["price"] + 1)),
        "N3_raw_response_altered": lambda d: [p.write_bytes(p.read_bytes() + b" ") for p in sorted((d / "CHANNEL3_RAW").glob("*.raw"))[:1]],
        "N4_no_replan": lambda d: edit(d / "PLAN_TRACE.json", lambda v: v.__setitem__("selection_replan", v["selection_initial"])),
        "N5_amount_altered_in_plan": lambda d: edit(d / "PLAN_TRACE.json", lambda v: v["final_line"].__setitem__("price_cents", v["final_line"]["price_cents"] - 100)),
        "N6_lean_not_T": lambda d: edit(d / "LEAN_AUTHORIZATION.json", lambda v: v["decision"].__setitem__("lean_verdict", "Z")),
        "N7_duplicate_capture_in_ledger": lambda d: edit(d / "BUDGET_LEDGER.json", lambda v: v["ledger_events"].append(
            {**[e for e in v["ledger_events"] if e["kind"] == "NETWORK" and e["data"].get("method") == "POST"][-1], "seq": 10 ** 6})),
        "N8_readback_mismatch": lambda d: edit(d / "READBACK.json", lambda v: v["effects"][0].__setitem__("match", False)),
        "N9_permit_signature_forged": lambda d: edit(d / "LEAN_AUTHORIZATION.json", lambda v: v["permit_envelope"]["payload"]["intent"].__setitem__("amount_cents", 1)),
    }
    out = {}
    for name, fn in cases.items():
        d = mutate(run, fn)
        try:
            v = judge(d / "run")
            out[name] = {"verdict": v["verdict"], "failed": v["failed"], "expected": "FAIL", "pass": v["verdict"] == "FAIL"}
        except Exception as e:     # a crash is not a PASS: record it
            out[name] = {"verdict": "ERROR", "error": f"{type(e).__name__}: {e}", "expected": "FAIL", "pass": True}
        shutil.rmtree(d, ignore_errors=True)
    return {"cases": out, "all_rejected": all(x["pass"] for x in out.values())}


def main():
    run = Path(sys.argv[1])
    if "--negatives" in sys.argv:
        r = negatives(run)
        (run / "JUDGE_NEGATIVES.json").write_text(json.dumps(r, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        print(json.dumps({"judge_negatives_all_rejected": r["all_rejected"]}))
        return
    v = judge(run)
    (run / "JUDGE_VERDICT.json").write_text(json.dumps(v, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"verdict": v["verdict"], "failed": v["failed"]}))


if __name__ == "__main__":
    main()
