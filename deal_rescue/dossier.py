"""LEGION Deal Rescue - build the merchant-facing Deal Dossier from ONE retained seven-owner run.

Every material figure is emitted as {"value", "ref"} where ref = {"file", "seq"|"line", "path"} points at the exact
retained record it was read from; deal_rescue/audit.py re-resolves every ref against the run files (no decorative
numbers). Nothing here writes to the run directory, calls a model, or contacts PayPal.

Usage: python -B -m deal_rescue.dossier --run <run_dir> --mode HISTORIC_REPLAY|FRESH_SYNTHETIC --run-id <id>
          [--paypal-state <PAUSED_STATE.json> --paypal-mission <MISSION_AUTHORIZED.json>] --out <dir>
"""
import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path

LETTERS = {"owner:b1": "A", "owner:b2": "B", "owner:b3": "C", "owner:b4": "D", "owner:b5": "E"}
BUYERS = list(LETTERS)
SOURCE_COMMIT = None  # filled in main()


def sha_file(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def load_jsonl(run, rel):
    p = run / rel
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def dig(obj, path):
    for k in path:
        obj = obj[k]
    return obj


class Ctx:
    def __init__(self, run, mode, run_id):
        self.run, self.mode, self.run_id = run, mode, run_id
        self.figures = {}

    def rec(self, rel, seq=None, line=None):
        rows = load_jsonl(self.run, rel)
        if seq is not None:
            return next(r for r in rows if r.get("seq") == seq)
        return rows[line]

    def fig(self, fid, rel, path, seq=None, line=None, label=None, semantic=None):
        r = self.rec(rel, seq=seq, line=line)
        v = dig(r, path)
        ref = {"file": rel, "path": path}
        ref.update({"seq": seq} if seq is not None else {"line": line})
        if "hash" in r:
            ref["record_hash"] = r["hash"]
        self.figures[fid] = {"value": v, "ref": ref, "label": label, "semantic": semantic,
                             "run_id": self.run_id, "evidence_mode": self.mode}
        return v

    def const(self, fid, value, basis, label=None, semantic=None):
        """A derived figure: value computed from other figure ids (basis), re-checked by the auditor."""
        self.figures[fid] = {"value": value, "derived_from": basis, "label": label, "semantic": semantic,
                             "run_id": self.run_id, "evidence_mode": self.mode}
        return value


def build(run, mode, run_id, paypal_state=None, paypal_mission=None):
    c = Ctx(run, mode, run_id)
    neg = load_jsonl(run, "NEGOTIATION_TRACE.jsonl")
    by_kind = lambda k: [r for r in neg if r["kind"] == k]
    pubs = load_jsonl(run, "marketplace/INTENT_PUBLICATIONS.jsonl")
    matches = load_jsonl(run, "marketplace/MATCHES.jsonl")
    commits = load_jsonl(run, "OWNER_COMMITMENTS.jsonl")
    versions = load_jsonl(run, "CONTRACT_VERSIONS.jsonl")
    receipts = load_jsonl(run, "RECEIPTS.jsonl")
    permits = load_jsonl(run, "ACTION_PERMITS.jsonl")
    trace = load_jsonl(run, "FULL_TRACE.jsonl")
    hub = json.loads((run / "HUB_RESULT.json").read_text())
    judge_full = json.loads((run / "JUDGE_RESULT.json").read_text())
    judge = judge_full["judgement"]["payload"]

    # ---------- Screen 1: unfulfilled demand (A0 oracle in this run + published intents)
    disc = by_kind("DISCOVERY")[0]
    a0 = disc["data"]["oracle"]["A0_unilateral"]
    proposal = by_kind("COALITION_PROPOSAL")[0]
    pbuyers = {b["owner_id"]: (i, b) for i, b in enumerate(proposal["data"]["proposal"]["buyers"])}
    requests = []
    for i, row in enumerate(a0):
        o = row["buyer"]
        L = LETTERS[o]
        pub = next(p for p in pubs if p["data"]["owner_id"] == o)
        pi, _ = pbuyers[o]
        requests.append({
            "customer": f"Customer {L}", "owner_id": o,
            "units": c.fig(f"req.{L}.units", "NEGOTIATION_TRACE.jsonl", ["data", "oracle", "A0_unilateral", i, "quantity"], seq=disc["seq"]),
            "max_cents": c.fig(f"req.{L}.cap", "NEGOTIATION_TRACE.jsonl", ["data", "proposal", "buyers", pi, "max_payment_cents"], seq=proposal["seq"]),
            "alone_feasible": c.fig(f"req.{L}.alone", "NEGOTIATION_TRACE.jsonl", ["data", "oracle", "A0_unilateral", i, "unilateral_feasible"], seq=disc["seq"]),
            "refusal_reason": c.fig(f"req.{L}.reason", "NEGOTIATION_TRACE.jsonl", ["data", "oracle", "A0_unilateral", i, "reason"], seq=disc["seq"]),
            "intent_id": c.fig(f"req.{L}.intent", "marketplace/INTENT_PUBLICATIONS.jsonl", ["data", "intent_id"], seq=pub["seq"]),
            "intent_sha256": pub["data"]["intent_sha256"], "published_at": pub["t"],
            "publication_ref": f"marketplace/INTENT_PUBLICATIONS.jsonl#{pub['seq']}",
        })
    requests.sort(key=lambda r: r["customer"])
    c.const("req.count_unfulfillable", sum(1 for r in requests if r["alone_feasible"] is False),
            [f"req.{L}.alone" for L in "ABCDE"], "requests that cannot be fulfilled alone", "SYNTHETIC")

    # ---------- Screen 2: opportunity
    m = matches[0]
    mprop = m["data"]["proposals"][0]
    chk = m["data"]["oracle"]["coalition_checks"][0]
    opp = {
        "match_ref": f"marketplace/MATCHES.jsonl#{m['seq']}",
        "sum_units": c.fig("opp.sum_units", "marketplace/MATCHES.jsonl", ["data", "oracle", "coalition_checks", 0, "sum_quantity"], seq=m["seq"]),
        "meets_moq": c.fig("opp.meets_moq", "marketplace/MATCHES.jsonl", ["data", "oracle", "coalition_checks", 0, "meets_moq"], seq=m["seq"]),
        "spec_match": chk["spec_match"], "distinct_owners": chk["distinct_owners"], "within_capacity": chk["within_capacity"],
        "proposal_authorization": c.fig("opp.authorization", "marketplace/MATCHES.jsonl", ["data", "proposals", 0, "authorization"], seq=m["seq"]),
        "buyers": [b["owner_id"] for b in mprop["buyers"]],
        "supplier": mprop.get("supplier", {}).get("owner_id") if isinstance(mprop.get("supplier"), dict) else mprop.get("supplier"),
        "engineer": mprop.get("engineer", {}).get("owner_id") if isinstance(mprop.get("engineer"), dict) else mprop.get("engineer"),
        "discovery_live_intents": len(load_jsonl(run, "marketplace/DISCOVERY_TRACE.jsonl")[0]["data"]["live_intents"]),
        "intents_by_kind": load_jsonl(run, "marketplace/DISCOVERY_TRACE.jsonl")[0]["data"]["by_kind"],
    }

    # ---------- Screen 3: terms versions
    states = by_kind("CONTRACT_STATE")
    tv = []
    for v in versions:
        d = v["data"]
        n = d["version"]
        h = d["contract_hash"]
        cs = [x for x in commits if x["data"]["commitment"]["payload"]["contract_version_hash"] == h]
        final_state = [s for s in states if s["data"]["contract_hash"] == h][-1]
        fail = next((s for s in states if s["data"]["contract_hash"] == h and s["data"]["state"] == "FAILED"), None)
        entry = {
            "version": c.fig(f"v{n}.version", "CONTRACT_VERSIONS.jsonl", ["data", "version"], seq=v["seq"]),
            "design": c.fig(f"v{n}.design", "CONTRACT_VERSIONS.jsonl", ["data", "design"], seq=v["seq"]),
            "contract_hash": h, "prev": d["prev"],
            "factory_cents": c.fig(f"v{n}.factory", "CONTRACT_VERSIONS.jsonl", ["data", "price_basis", "factory_cents"], seq=v["seq"]),
            "qualification_cents": c.fig(f"v{n}.qual", "CONTRACT_VERSIONS.jsonl", ["data", "price_basis", "qualification_cents"], seq=v["seq"]),
            "shipping_cents": c.fig(f"v{n}.ship", "CONTRACT_VERSIONS.jsonl", ["data", "price_basis", "shipping_cents"], seq=v["seq"]),
            "total_cents": c.fig(f"v{n}.total", "CONTRACT_VERSIONS.jsonl", ["data", "price_basis", "total_cents"], seq=v["seq"]),
            "per_buyer_cents": c.fig(f"v{n}.per_buyer", "CONTRACT_VERSIONS.jsonl", ["data", "price_basis", "per_buyer_cents"], seq=v["seq"]),
            "final_state": c.fig(f"v{n}.final_state", "NEGOTIATION_TRACE.jsonl", ["data", "state"], seq=final_state["seq"]),
            "failure": ({"reason": c.fig(f"v{n}.fail_reason", "NEGOTIATION_TRACE.jsonl", ["data", "reason"], seq=fail["seq"]),
                         "ref": f"NEGOTIATION_TRACE.jsonl#{fail['seq']}", "rejected_by": fail["data"].get("rejected_by"),
                         "inactive": fail["data"].get("inactive")} if fail else None),
            "commitments": [],
            "created_at": d["contract"]["created_at"],
            "ref": f"CONTRACT_VERSIONS.jsonl#{v['seq']}",
        }
        for x in cs:
            p = x["data"]["commitment"]["payload"]
            o = p["owner_id"]
            tag = f"v{n}.commit.{o}"
            entry["commitments"].append({
                "owner_id": o, "role": "buyer" if o in LETTERS else ("factory" if o == "owner:f1" else "engineer"),
                "decision": c.fig(tag + ".decision", "OWNER_COMMITMENTS.jsonl", ["data", "commitment", "payload", "decision"], seq=x["seq"]),
                "max_amount_cents": p.get("max_amount_cents"), "epoch": p["epoch"],
                "failed_checks": p["rationale"].split("failed checks: ")[-1].split(";")[0],
                "commitment_id": p["commitment_id"], "signer_key_id": x["data"]["commitment"]["signer"]["key_id"],
                "ref": f"OWNER_COMMITMENTS.jsonl#{x['seq']}", "record_hash": x["hash"], "t": x["t"],
            })
        c.const(f"v{n}.accepts", sum(1 for k in entry["commitments"] if k["decision"] == "ACCEPT"),
                [f"v{n}.commit.{k['owner_id']}.decision" for k in entry["commitments"]])
        tv.append(entry)
    # cheaper, nonconforming design and the engineer verdict that excluded it
    qual = by_kind("QUALIFICATION")
    offers2 = by_kind("FACTORY_OFFERS")[-1]
    rejected_designs = []
    for q in qual:
        for dsg, ok in q["data"]["results"].items():
            if ok is False:
                rejected_designs.append({
                    "design": dsg,
                    "engineer_pass": c.fig(f"qual.{dsg}.pass", "NEGOTIATION_TRACE.jsonl", ["data", "results", dsg], seq=q["seq"]),
                    "factory_cents": c.fig(f"qual.{dsg}.factory", "NEGOTIATION_TRACE.jsonl", ["data", "offers", dsg, "amount_cents"], seq=offers2["seq"]),
                    "ref": f"NEGOTIATION_TRACE.jsonl#{q['seq']}"})
    shock = next(t for t in trace if t["kind"] == "PRICE_SHOCK_INJECTED")
    shock_i = trace.index(shock)

    # ---------- Screen 4: verified execution (receipts are SIMULATED world effects)
    execu = []
    for r in receipts:
        d = r["data"]
        resp = d["response"]
        envs = [resp[k] for k in ("receipt", "ship_receipt", "buyer_receipt") if k in resp]
        for e in envs:
            p = e["payload"]
            execu.append({"action": p["action"], "issuer": p["issuer_owner_id"], "mode": p["mode"],
                          "obligation_id": p["obligation_id"], "effect_id": p["effect_id"], "receipt_id": p["receipt_id"],
                          "contract_version_hash": p["contract_version_hash"], "readback_sha256": p["readback_sha256"],
                          "issued_at": p["issued_at"], "signer_key_id": e["signer"]["key_id"],
                          "ref": f"RECEIPTS.jsonl#{r['seq']}", "record_hash": r["hash"], "verdict": d["verdict"]})
    execu.sort(key=lambda x: x["issued_at"])
    c.const("exec.simulated_receipts", len(execu), ["RECEIPTS.jsonl"], "signed world-effect receipts", "SIMULATED")
    c.const("exec.non_simulated_receipts", sum(1 for x in execu if x["mode"] != "SIMULATED"), ["RECEIPTS.jsonl"])
    jchecks = judge["checks"]
    judge_block = {
        "verdict": judge["verdict"], "judgement_id": judge["judgement_id"], "scope": "SYNTHETIC (owner world-engine state read back live by the Judge; no physical goods, no PayPal)",
        "checks_total": len(jchecks), "checks_passed": sum(1 for x in jchecks if x["pass"]),
        "tamper_mutants": judge_full.get("tamper_mutants"),
        "judge_key_id": judge_full["judgement"]["signer"]["key_id"] if "signer" in judge_full["judgement"] else None,
        "evidence_sha256": judge["evidence_sha256"], "file": "JUDGE_RESULT.json", "file_sha256": sha_file(run / "JUDGE_RESULT.json"),
        "facts": judge_full["facts"],
        "checks": [{"check": x["check"], "pass": x["pass"], **{k: v for k, v in x.items() if k in ("owner", "received", "paid", "cap", "line")}} for x in jchecks],
    }
    # judge evidence hashes must match the files we render from
    # FULL_TRACE.jsonl is appended by the e2e harness AFTER the Judge ran (one JUDGE_FINISHED line), so for that file the
    # judged hash must equal the hash of the file without its final JUDGE_FINISHED line. All other files: exact match.
    def judged_match(n, h):
        b = (run / n).read_bytes()
        if hashlib.sha256(b).hexdigest() == h:
            return "exact"
        lines = b.splitlines(keepends=True)
        if n == "FULL_TRACE.jsonl" and json.loads(lines[-1])["kind"] == "JUDGE_FINISHED" and hashlib.sha256(b"".join(lines[:-1])).hexdigest() == h:
            return "prefix (JUDGE_FINISHED appended after judging)"
        return "MISMATCH"
    judge_block["evidence_hash_check"] = {n: judged_match(n, h) for n, h in judge["evidence_sha256"].items()}
    judge_block["evidence_hashes_match_rendered_files"] = all(v != "MISMATCH" for v in judge_block["evidence_hash_check"].values())

    # ---------- Screen 5: economics + PayPal (orthogonal)
    v3 = tv[-1]
    econ = {
        "perspective": "merchant_of_record (pass-through) — synthetic scenario prices",
        "buyer_proceeds_cents": c.const("econ.proceeds", v3["per_buyer_cents"] * len(BUYERS), ["v3.per_buyer"], "5 x per-buyer price", "SYNTHETIC proposed buyer-side sale"),
        "payable_factory_cents": v3["factory_cents"], "payable_engineer_cents": v3["qualification_cents"], "shipping_cents": v3["shipping_cents"],
        "pass_through_contribution_cents": c.const("econ.contribution", v3["per_buyer_cents"] * len(BUYERS) - v3["total_cents"],
                                                   ["v3.per_buyer", "v3.total"], "contribution before other costs", "EXACT"),
        "max_gross_at_caps_cents": c.const("econ.max_at_caps", sum(r["max_cents"] for r in requests), [f"req.{L}.cap" for L in "ABCDE"]),
        "max_headroom_before_overhead_cents": c.const("econ.headroom", sum(r["max_cents"] for r in requests) - v3["total_cents"],
                                                      [f"req.{L}.cap" for L in "ABCDE"] + ["v3.total"]),
        "unknown_cost_lines": {k: "Z" for k in ["payment_fees_cents", "agent_inference_cents", "independent_verification_cents", "support_cents", "expected_loss_cents"]},
        "manufacturer_internal_margin": "UNKNOWN (factory price is a quote paid to the factory, not its cost)",
        "merchant_net_margin": "NOT ESTABLISHED (non-positive: contribution 0 before any overhead)",
        "verdict": "NOT YET PROFITABLE / NOT ECONOMICALLY VALIDATED",
        "incremental_real_tpv": "UNKNOWN",
    }
    slots = []
    pstate = json.loads(Path(paypal_state).read_text()) if paypal_state else None
    pmission = json.loads(Path(paypal_mission).read_text()) if paypal_mission else None
    bound = bool(pmission and pmission["payload"]["federation_contract_hash"] == v3["contract_hash"])
    for o in BUYERS:
        L = LETTERS[o]
        slot = {"customer": f"Customer {L}", "owner_id": o, "obligation_id": f"pay-{o.split(':')[1]}", "amount_cents": v3["per_buyer_cents"],
                "currency": "USD", "payee": "UNKNOWN (merchant of record not established)", "state": "NOT_AUTHORIZED",
                "evidence_mode": mode, "provider_confirmed_cents": 0, "status_source": "no PayPal mission bound to this run's contract"}
        if bound and pstate:
            eff = next((e for e in pstate["effects"] if e["obligation_id"] == slot["obligation_id"]), None)
            slot.update({"evidence_mode": "REAL_SANDBOX (historic 2026-11-07, LMF-007)", "payee": "Sandbox merchant (id redacted)",
                         "status_source": "PAUSED_STATE.json (last provider observation before owner pause; not re-read)"})
            if eff:
                slot.update({"state": "PAYER_ACTION_REQUIRED (approval window lapsed / paused) — HOLD open", "order_id_masked": eff["order_id"][:4] + "…" + eff["order_id"][-3:],
                             "last_provider_status": eff["provider_status"], "captures": eff["provider_captures"], "held": eff["held"]})
            else:
                slot.update({"state": "NOT_STARTED (paused before creation)"})
        slots.append(slot)
    paypal = {"slots": slots, "readback_confirmed_captures": 0, "slots_total": len(slots),
              "provider_confirmed_cents": 0, "new_provider_posts_this_wo": 0,
              "historic_mission_bound_to_this_contract": bound,
              "admission": "F — see PAYPAL_AUTHORITY_PREFLIGHT.json", "badge": "PAYPAL_NOT_AUTHORIZED / MECHANISM_ONLY"}

    # ---------- obligations / contract
    final_contract = versions[-1]["data"]["contract"]
    obligations = []
    for p in final_contract["parties"]:
        for ob in p["obligations"]:
            done = [x for x in execu if x["issuer"] == p["owner_id"] or x["obligation_id"].endswith(p["owner_id"])]
            obligations.append({"owner_id": p["owner_id"], "role": p["role"], **ob, "contract_version": v3["version"],
                                "contract_hash": v3["contract_hash"],
                                "world_effect": "SIMULATED" if done else "NONE",
                                "paypal_effect": "NONE (not authorized)" if ob["kind"] == "pay" else None})
    contract = {"deal_id": f"deal-{v3['contract_hash'][:12]}", "contract_id": final_contract["contract_id"],
                "merchant_role": "demonstration storefront / merchant-of-record CANDIDATE — not a signing owner",
                "merchant_of_record_status": "NOT ESTABLISHED", "currency": final_contract["currency"],
                "supplier_moq": c.fig("contract.moq", "marketplace/MATCHES.jsonl", ["data", "proposals", 0, "factory", "minimum_quantity"], seq=m["seq"]),
                "partial_failure_policy": final_contract["partial_failure_policy"], "parties": [
                    {"owner_id": p["owner_id"], "role": p["role"], "peer_id": p["peer_id"]} for p in final_contract["parties"]],
                "terms_versions": [{k: v for k, v in t.items() if k != "commitments"} for t in tv], "obligations": obligations,
                "payer_payee_map": [{"payer": s["owner_id"], "amount_cents": s["amount_cents"], "payee": s["payee"], "state": s["state"]} for s in slots]}

    # ---------- replay events (actual timestamps)
    events = []

    def ev(t, kind, label, phase, ref, actor=None, rh=None, scope="SYNTHETIC"):
        events.append({"t": t, "kind": kind, "label": label, "phase": phase, "ref": ref, "actor": actor,
                       "record_hash": rh, "run_id": run_id, "evidence_mode": mode, "scope": scope})
    for i, t in enumerate(trace):
        if t["kind"] == "OWNER_PUBLISHED":
            who = LETTERS.get(t["owner"], None)
            ev(t["t"], t["kind"], f"{'Customer ' + who if who else t['owner']} published an intent (marketplace verdict {t['marketplace_verdict']})", 1, f"FULL_TRACE.jsonl@{i}", t["owner"])
        elif t["kind"] == "PRICE_SHOCK_INJECTED":
            ev(t["t"], t["kind"], "INJECTED TEST EVENT: factory price shock", 3, f"FULL_TRACE.jsonl@{i}", "environment", scope="INJECTED/TEST")
    labels = {"DISCOVERY": "Marketplace discovery: coalition candidate found, unilateral orders infeasible",
              "COALITION_PROPOSAL": "Coalition proposed (proposal only, no authority)",
              "FACTORY_OFFERS": "Factory quotes received", "QUALIFICATION": "Engineer qualification results",
              "PLANNER_CHOICE": "Coordinator chose a design", "ENVIRONMENT_EVENT_OBSERVED": "Coordinator observed the price shock"}
    for r in neg:
        if r["kind"] == "CONTRACT_STATE":
            lab = f"Contract v{r['data'].get('version', '3')} {r['data']['state']}" + (f" — {r['data']['reason']}" if r["data"].get("reason") else "")
        else:
            lab = labels.get(r["kind"], r["kind"])
            if r["kind"] == "QUALIFICATION":
                lab += ": " + ", ".join(f"{k} {'PASS' if v else 'FAIL'}" for k, v in r["data"]["results"].items())
            if r["kind"] == "FACTORY_OFFERS":
                lab += ": " + ", ".join(f"{k} ${v['amount_cents'] / 100:.0f}" for k, v in r["data"]["offers"].items())
        ev(r["t"], r["kind"], lab, 2 if r["kind"] in ("DISCOVERY", "COALITION_PROPOSAL") else (4 if r["data"].get("state") in ("AUTHORIZED", "EXECUTING", "FULFILLED") else 3),
           f"NEGOTIATION_TRACE.jsonl#{r['seq']}", r["owner_id"], r["hash"])
    for x in commits:
        p = x["data"]["commitment"]["payload"]
        nm = f"Customer {LETTERS[p['owner_id']]}" if p["owner_id"] in LETTERS else p["owner_id"]
        ver = next(t["version"] for t in tv if t["contract_hash"] == p["contract_version_hash"])
        ev(x["t"], "OWNER_COMMITMENT", f"{nm} signed {p['decision']} on v{ver}" + ("" if p["decision"] == "ACCEPT" else f" (failed: {p['rationale'].split('failed checks: ')[-1].split(';')[0]})"),
           3, f"OWNER_COMMITMENTS.jsonl#{x['seq']}", p["owner_id"], x["hash"])
    for x in permits:
        ev(x["t"], "ACTION_PERMIT", f"{x['data']['owner']} issued action permit (verdict {x['data'].get('verdict')})", 4, f"ACTION_PERMITS.jsonl#{x['seq']}", x["data"]["owner"], x["hash"])
    nice = {"factory.manufacture": "Factory manufactured 100 units", "engineer.certify_lot": "Engineer certified the lot",
            "factory.ship": "Factory shipped 20 units", "buyer.receive": "Buyer confirmed receipt of 20 units", "buyer.pay": "Buyer paid its line (ledger)"}
    for x in execu:
        nm = f"Customer {LETTERS[x['issuer']]}" if x["issuer"] in LETTERS else x["issuer"]
        lab = nice.get(x["action"], x["action"])
        if x["action"] == "factory.ship":
            lab += " to " + "Customer " + LETTERS.get("owner:" + x["obligation_id"].split("owner:")[-1], "?")
        elif x["issuer"] in LETTERS:
            lab = lab.replace("Buyer", nm)
        ev(x["issued_at"], "RECEIPT", f"SIMULATED: {lab}", 4, x["ref"], x["issuer"], x["record_hash"], scope="SIMULATED")
    jf = next(t for t in trace if t["kind"] == "JUDGE_FINISHED")
    ev(jf["t"], "JUDGE_FINISHED", f"Independent Judge: {judge['verdict']} ({judge_block['checks_passed']}/{judge_block['checks_total']} checks, synthetic scope)", 4, f"FULL_TRACE.jsonl@{trace.index(jf)}", "judge")
    events.sort(key=lambda e: e["t"])
    for i, e in enumerate(events):
        e["event_id"] = f"{run_id}:{i:03d}"

    # ---------- Execution Passport (consequential events)
    passport = []
    perm_by_owner = {x["data"]["owner"]: x for x in permits}
    for x in execu:
        pm = perm_by_owner.get(x["issuer"])
        passport.append({"actor_id": x["issuer"], "owner_id": x["issuer"], "obligation_id": x["obligation_id"],
                         "action": x["action"], "admission_verdict": x["verdict"], "grounds": "owner policy + signed permit + contract binding",
                         "authorization_ref": (f"ACTION_PERMITS.jsonl#{pm['seq']}" if pm else "Z"),
                         "permit_id": pm["data"]["permit"]["payload"]["permit_id"] if pm else None,
                         "contract_hash": x["contract_version_hash"], "contract_version": v3["version"],
                         "effect_id": x["effect_id"], "receipt_id": x["receipt_id"], "receipt_record_hash": x["record_hash"],
                         "readback_sha256": x["readback_sha256"], "timestamp": x["issued_at"],
                         "epoch": pm["data"]["permit"]["payload"]["epoch"] if pm else None, "evidence_mode": mode,
                         "effect_scope": x["mode"], "observed_by": "independent Judge (live reader endpoints)", "ref": x["ref"]})
    denied = [{"actor_id": k["owner_id"], "contract_version": t["version"], "decision": k["decision"], "grounds": k["failed_checks"],
               "ref": k["ref"]} for t in tv for k in t["commitments"] if k["decision"] != "ACCEPT"]

    dossier = {
        "schema": "legion.deal-rescue.dossier.v1", "product": "LEGION Deal Rescue",
        "run_id": run_id, "run_dir_name": run.name, "evidence_mode": mode, "source_commit": SOURCE_COMMIT, "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run_files_sha256": {str(p.relative_to(run)).replace("\\", "/"): sha_file(p) for p in sorted(run.rglob("*.json*")) if p.is_file()},
        "badges": {"world": "SIMULATED — no physical manufacture or delivery", "requests": "DEMO REQUESTS — SYNTHETIC",
                   "replay": "HISTORICAL REPLAY — NOT A NEW FIRST-ATTEMPT RESULT" if mode == "HISTORIC_REPLAY" else "FRESH SYNTHETIC RUN (first attempt, scorer frozen before run)",
                   "paypal": paypal["badge"], "economics": econ["verdict"]},
        "requests": requests, "opportunity": opp, "terms_versions": tv, "rejected_designs": rejected_designs,
        "price_shock": {"label": "INJECTED / TEST environment event", "ref": f"FULL_TRACE.jsonl@{shock_i}", "t": shock["t"]},
        "execution": execu, "judge": judge_block, "economics": econ, "paypal": paypal, "contract": contract,
        "model_activity": {"per_owner_model_decisions": judge_full["facts"]["per_owner_model_decisions"],
                           "coordinator_model_decisions": judge_full["facts"]["coordinator_model_decisions"],
                           "registered_workers": judge_full["facts"]["registered_workers"],
                           "note": "700 workers are REGISTERED (100 per owner); model-active decisions are the counts above, not 700 concurrent models"},
        "hub_result": hub, "denied_actions": denied, "events": events, "passport": passport, "figures": c.figures,
    }
    return dossier


def main():
    global SOURCE_COMMIT
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--mode", required=True, choices=["HISTORIC_REPLAY", "FRESH_SYNTHETIC"])
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--paypal-state")
    ap.add_argument("--paypal-mission")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    SOURCE_COMMIT = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=Path(__file__).parent).stdout.strip()
    d = build(Path(a.run), a.mode, a.run_id, a.paypal_state, a.paypal_mission)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "DEAL_DOSSIER.json").write_text(json.dumps(d, indent=1))
    (out / "CONTRACT_AND_OBLIGATIONS.json").write_text(json.dumps(d["contract"], indent=1))
    (out / "QUOTE_VERSIONS.json").write_text(json.dumps({"versions": d["terms_versions"], "rejected_designs": d["rejected_designs"], "price_shock": d["price_shock"]}, indent=1))
    (out / "PAYER_PAYEE_MAP.json").write_text(json.dumps(d["contract"]["payer_payee_map"], indent=1))
    (out / "UNIT_ECONOMICS.json").write_text(json.dumps(d["economics"], indent=1))
    (out / "EXECUTION_PASSPORT.json").write_text(json.dumps({"consequential": d["passport"], "denied": d["denied_actions"]}, indent=1))
    (out / "E2E_EVENTS.jsonl").write_text("".join(json.dumps(e) + "\n" for e in d["events"]))
    (out / "JUDGE_RESULT_SUMMARY.json").write_text(json.dumps(d["judge"], indent=1))
    print(json.dumps({"run_id": a.run_id, "events": len(d["events"]), "figures": len(d["figures"]), "judge": d["judge"]["verdict"],
                      "judge_hashes_match": d["judge"]["evidence_hashes_match_rendered_files"], "paypal_bound": d["paypal"]["historic_mission_bound_to_this_contract"]}))


if __name__ == "__main__":
    main()
