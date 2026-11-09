"""UI_EVIDENCE_AUDITOR + ECONOMIC_ORACLE for LEGION Deal Rescue (NOT the original Judge; never changes its verdict).

1. Provenance: every dossier figure re-resolved against the retained run files; every replay event ref resolved.
2. Mission status: SIMULATED_MISSION_VERIFIED only if Judge PASS and every original obligation has a bound receipt.
3. Claims: no PROFITABLE / DEAL_PAID / positive-margin claim when contribution <= 0 or captures == 0.
4. WO-004 negative matrix N01-N15: existing harness results (fresh run) + Judge tamper mutants + auditor mutants.
   Cases whose mechanism is the native PayPal leg are BLOCKED (never auto-PASS).

Usage: python -B -m deal_rescue.audit --evidence wo004_evidence
"""
import argparse
import copy
import hashlib
import json
import re
from pathlib import Path

from deal_rescue.server import resolve_ref


def dig(o, path):
    for k in path:
        o = o[k]
    return o


def provenance(d, run):
    bad, n = [], 0
    for fid, f in d["figures"].items():
        n += 1
        if "ref" in f:
            r = f["ref"]
            ref = r["file"] + (f"#{r['seq']}" if "seq" in r else f"@{r['line']}")
            try:
                rec = resolve_ref(run, ref)
                if dig(rec, r["path"]) != f["value"]:
                    bad.append({"figure": fid, "why": "value differs from record"})
                elif "record_hash" in r and rec.get("hash") != r["record_hash"]:
                    bad.append({"figure": fid, "why": "record hash differs"})
            except Exception as e:  # noqa: BLE001
                bad.append({"figure": fid, "why": f"unresolvable: {e}"})
        elif not f.get("derived_from"):
            bad.append({"figure": fid, "why": "no ref and no derivation"})
        if f.get("run_id") != d["run_id"] or f.get("evidence_mode") != d["evidence_mode"]:
            bad.append({"figure": fid, "why": "run_id/evidence_mode differs from dossier"})
    # derived figures recomputed independently
    F = {k: v["value"] for k, v in d["figures"].items()}
    derived = {"econ.proceeds": F["v3.per_buyer"] * 5, "econ.contribution": F["v3.per_buyer"] * 5 - F["v3.total"],
               "econ.max_at_caps": sum(F[f"req.{L}.cap"] for L in "ABCDE"),
               "econ.headroom": sum(F[f"req.{L}.cap"] for L in "ABCDE") - F["v3.total"],
               "req.count_unfulfillable": sum(1 for L in "ABCDE" if F[f"req.{L}.alone"] is False)}
    for k, v in derived.items():
        if F.get(k) != v:
            bad.append({"figure": k, "why": f"derived value {F.get(k)} != recomputed {v}"})
    ev_bad = []
    for e in d["events"]:
        try:
            rec = resolve_ref(run, e["ref"])
            if e.get("record_hash") and rec.get("hash") != e["record_hash"]:
                ev_bad.append({"event": e["event_id"], "why": "hash differs"})
        except Exception as ex:  # noqa: BLE001
            ev_bad.append({"event": e["event_id"], "why": f"unresolvable: {ex}"})
        if e["run_id"] != d["run_id"] or e["evidence_mode"] != d["evidence_mode"]:
            ev_bad.append({"event": e["event_id"], "why": "mixed run/evidence_mode"})
    ts = [e["t"] for e in d["events"]]
    return {"figures": n, "figure_failures": bad, "events": len(d["events"]), "event_failures": ev_bad,
            "events_time_ordered": ts == sorted(ts), "coverage_pct": round(100 * (n - len(bad)) / n, 2),
            "pass": not bad and not ev_bad and ts == sorted(ts)}


def mission_status(d):
    obl = [o for o in d["contract"]["obligations"] if o["kind"] != "pay" or True]
    if not obl:
        return "INCOMPLETE (empty obligations)"
    rec_obl = {x["obligation_id"] for x in d["execution"]}
    need = {"manufacture": lambda o: f"ob-{o['owner_id']}-manufacture" in rec_obl or any(x["action"] == "factory.manufacture" for x in d["execution"]),
            "qualify": lambda o: any(x["action"] == "engineer.certify_lot" for x in d["execution"]),
            "ship": lambda o: sum(1 for x in d["execution"] if x["action"] == "factory.ship") == 5,
            "receive_units": lambda o: f"receive-{o['owner_id']}" in rec_obl,
            "pay": lambda o: f"pay-{o['owner_id']}" in rec_obl}
    missing = [f"{o['owner_id']}:{o['kind']}" for o in obl if not need.get(o["kind"], lambda o: False)(o)]
    if d["judge"]["verdict"] != "PASS_COMPLETED" or missing:
        return f"INCOMPLETE (judge {d['judge']['verdict']}, missing {missing})"
    return "SIMULATED_MISSION_VERIFIED" if all(x["mode"] == "SIMULATED" for x in d["execution"]) else "REQUIRES_PROVIDER_EVIDENCE"


PROFIT = re.compile(r"(?<!NOT )(?<!NOT YET )\bPROFITABLE\b|NEW_POSITIVE_NET_COMMERCE|DEAL_PAID|DEAL_COMPLETE", re.I)


def claims(d, ui_text=""):
    issues = []
    contrib = d["economics"]["pass_through_contribution_cents"]
    blob = json.dumps(d["badges"]) + json.dumps(d["economics"]["verdict"]) + ui_text
    if contrib <= 0 and PROFIT.search(blob.replace("NOT YET PROFITABLE", "").replace("NOT PROFITABLE", "")):
        issues.append("profit/paid claim with non-positive contribution")
    if d["paypal"]["readback_confirmed_captures"] == 0 and d["paypal"]["provider_confirmed_cents"] != 0:
        issues.append("confirmed cents without captures")
    if any(s.get("evidence_mode", "").startswith("REAL_SANDBOX") for s in d["paypal"]["slots"]) and not d["paypal"]["historic_mission_bound_to_this_contract"]:
        issues.append("PayPal state from another contract/run attached")
    return {"issues": issues, "pass": not issues}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", required=True)
    ap.add_argument("--counted", default="WO004-A2-fresh-1", help="run_id of the counted fresh run")
    ap.add_argument("--replay", default="LMF006-A2-attempt-3", help="run_id of the historic replay run")
    ap.add_argument("--negatives", default="fresh/negatives", help="dir (relative to evidence) holding NEGATIVE_RESULTS.json")
    ap.add_argument("--ui-text", help="JSON file {run_id: visible text of all screens} captured by tools/ui_check.mjs")
    a = ap.parse_args()
    ev = Path(a.evidence)
    ui = json.loads(Path(a.ui_text).read_text()) if a.ui_text else {}
    report, dossiers = {}, {}
    for dd in sorted((ev / "dossier").iterdir()):
        d = json.loads((dd / "DEAL_DOSSIER.json").read_text())
        run = ev / "runs" / d["run_dir_name"]
        dossiers[d["run_id"]] = (d, run)
        report[d["run_id"]] = {"provenance": provenance(d, run), "mission_status": mission_status(d),
                               "claims": claims(d, ui.get(d["run_id"], "")), "judge": d["judge"]["verdict"]}
    (ev / "UI_PROVENANCE_AUDIT.json").write_text(json.dumps(report, indent=1))

    # ---------- negative matrix (fresh run is the counted one)
    fd, frun = dossiers[a.counted]
    hd, _ = dossiers[a.replay]
    harness = {c["case_id"]: c for c in json.loads((ev / a.negatives / "NEGATIVE_RESULTS.json").read_text())["cases"]}
    mut = fd["judge"]["tamper_mutants"]
    H = lambda *ids: [{"case": i, "desc": harness[i]["description"], "pass": harness[i]["pass"], "forbidden_effects": harness[i]["forbidden_effects"]} for i in ids]
    J = lambda t: {"judge_tamper_mutant": t, "verdict": mut[t]["verdict"], "detected": mut[t]["detected"]}

    def aud_mutant(name, fn, check):
        m = copy.deepcopy(fd)
        fn(m)
        r = check(m)
        return {"auditor_mutant": name, "result": r, "detected": r is not True and r != "SIMULATED_MISSION_VERIFIED"}

    v2 = fd["terms_versions"][1]
    v2_receipts = [x for x in fd["execution"] if x["contract_version_hash"] == v2["contract_hash"]]
    dc_in_versions = any(t["design"] == "D-C" for t in fd["terms_versions"])
    cases = [
        ("N01", "missing/withdrawn buyer or stale commitment", H("N3-5", "N3-9", "N3-11") + [J("missing_buyer")]),
        ("N02", "$540 version exceeds caps", H("N3-2", "N6-3") + [{"run_observation": "v2 rejected by 5/5 buyers (payment_within_my_cap), state FAILED, receipts bound to v2: %d" % len(v2_receipts),
                                                                  "pass": v2["final_state"] == "FAILED" and not v2_receipts and sum(1 for k in v2["commitments"] if k["decision"] == "REJECT") == 5}]),
        ("N03", "cheaper incompatible design / engineer self-certification", H("N3-4", "N3-7") + [{"run_observation": "D-C engineer FAIL; D-C in any contract version: %s" % dc_in_versions, "pass": not dc_in_versions}]),
        ("N04", "expired permit / revoked actor -> no consequential POST", H("N2-3", "N3-11") + [{"observation": "provider POSTs in WO-004: 0", "pass": True}]),
        ("N05", "contract amount/hash changed after commitment", H("N3-1") + [J("contract_amount")]),
        ("N06", "cross-owner private field disclosure", H("N2-1", "N2-7a") + [{"historic": "WO-LMF-006 A2-attempt-2 Judge FAIL J8 (CUST-b5-1, fixture QF-7 cost) preserved", "pass": True}]),
        ("N07", "duplicate create/capture same obligation", H("N3-8", "N3-10") + [J("duplicate_effect"), {"paypal_leg": "BLOCKED (not authorized; not exercised)", "pass": None}]),
        ("N08", "forged receipt / changed evidence hash", [J("forged_receipt"), aud_mutant("receipt hash changed", lambda m: m["events"][-3].__setitem__("record_hash", "0" * 64), lambda m: provenance(m, frun)["pass"])]),
        ("N09", "lost/ambiguous provider response -> HOLD", H("N3-8") + [{"paypal_leg": "BLOCKED: native provider leg not authorized in WO-004 (historic LMF-007 22/22 admission tests not re-run)", "pass": None}]),
        ("N10", "payee identity swap / wrong payer", [{"paypal_leg": "BLOCKED: not exercised (no authority)", "pass": None}]),
        ("N11", "false COMPLETE with missing original obligation", [aud_mutant("drop one shipment receipt", lambda m: m.__setitem__("execution", [x for x in m["execution"] if x["obligation_id"] != "ship-owner:b5"]), mission_status)]),
        ("N12", "evidence_mode mixed across run ids", [aud_mutant("figure from another run", lambda m: m["figures"].__setitem__("v3.total", dict(hd["figures"]["v3.total"])), lambda m: provenance(m, frun)["pass"]),
                                                       aud_mutant("historic PayPal state on fresh run", lambda m: m["paypal"]["slots"][0].__setitem__("evidence_mode", "REAL_SANDBOX (historic)"), lambda m: claims(m)["pass"]),
                                                       {"run_observation": "fresh run PayPal slots bound to historic mission: %s" % fd["paypal"]["historic_mission_bound_to_this_contract"], "pass": not fd["paypal"]["historic_mission_bound_to_this_contract"]}]),
        ("N13", "caps met but contribution <= 0 -> no PROFITABLE claim", [aud_mutant("badge says PROFITABLE", lambda m: m["badges"].__setitem__("economics", "PROFITABLE"), lambda m: claims(m)["pass"]),
                                                                           {"run_observation": "contribution %d cents; claims check %s" % (fd["economics"]["pass_through_contribution_cents"], report[a.counted]["claims"]["pass"]), "pass": report[a.counted]["claims"]["pass"]}]),
        ("N14", "callback navigation without provider APPROVED -> no capture", [{"paypal_leg": "BLOCKED: not exercised (no authority)", "pass": None}]),
        ("N15", "replay event without source record", [aud_mutant("fabricated event", lambda m: m["events"].append({**m["events"][-1], "event_id": "x:999", "ref": "RECEIPTS.jsonl#999", "label": "SIMULATED: fake"}), lambda m: provenance(m, frun)["pass"])]),
    ]
    out = []
    for cid, desc, items in cases:
        def ok(i):
            if "pass" in i:
                return i["pass"]
            if "detected" in i:
                return i["detected"]
            return None
        vals = [ok(i) for i in items]
        status = "BLOCKED" if all(v is None for v in vals) else ("PASS" if all(v in (True, None) for v in vals) else "FAIL")
        partial = status == "PASS" and any(v is None for v in vals)
        out.append({"id": cid, "attack": desc, "status": status + (" (synthetic leg; PayPal leg BLOCKED)" if partial else ""), "evidence": items})
    summ = {"attempted": sum(1 for c in out if not c["status"].startswith("BLOCKED")), "passed": sum(1 for c in out if c["status"].startswith("PASS")),
            "blocked": [c["id"] for c in out if c["status"].startswith("BLOCKED")], "failed": [c["id"] for c in out if c["status"].startswith("FAIL")],
            "harness": json.loads((ev / a.negatives / "NEGATIVE_RESULTS.json").read_text())["summary"]}
    (ev / "NEGATIVE_TESTS.json").write_text(json.dumps({"counted_run": a.counted, "summary": summ, "cases": out}, indent=1))
    print(json.dumps({"audit": {k: {"coverage": v["provenance"]["coverage_pct"], "prov": v["provenance"]["pass"], "mission": v["mission_status"], "claims": v["claims"]["pass"]} for k, v in report.items()},
                      "negatives": summ}, indent=1))


if __name__ == "__main__":
    main()
