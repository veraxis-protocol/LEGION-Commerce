"""Assemble the WO-004 evidence passport: WO-named copies of run journals, RUN_MANIFEST, REPLAY_INDEX, cost metrics,
MISSING_EVIDENCE, SHA256SUMS. Copies only; never modifies run files.  Usage: python -B deal_rescue/tools/package.py <evidence_dir>"""
import hashlib
import json
import re
import shutil
import sys
import time
from pathlib import Path


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def usage(run):
    calls, pt, ct, lat, failed = 0, 0, 0, 0.0, 0
    for f in list(run.glob("owners/*/MODEL_DECISIONS.jsonl")) + [run / "MODEL_DECISIONS.jsonl"]:
        for l in f.read_text(encoding="utf-8").splitlines():
            d = json.loads(l)["data"]
            calls += 1
            lat += d.get("latency_s") or 0
            failed += bool(d.get("failed"))
            m = re.search(r'"prompt_tokens": (\d+)', l)
            pt += int(m.group(1)) if m else 0
            m = re.search(r'"completion_tokens": (\d+)', l)
            ct += int(m.group(1)) if m else 0
    return {"model_calls": calls, "failed_calls": failed, "prompt_tokens": pt, "completion_tokens": ct, "sum_model_latency_s": round(lat, 2),
            "nim_price_per_token": "Z (NIM trial endpoint; no invoice price)", "cost_usd": "Z"}


def main(ev):
    ev = Path(ev)
    counted = ev / "runs" / "A2-fresh-1"
    pas = ev / "passport_files"
    pas.mkdir(exist_ok=True)
    for src, dst in [("marketplace/INTENT_PUBLICATIONS.jsonl", "INTENT_PUBLICATIONS.jsonl"), ("marketplace/DISCOVERY_TRACE.jsonl", "DISCOVERY_TRACE.jsonl"),
                     ("marketplace/MATCHES.jsonl", "COALITION_MATCHES.jsonl"), ("OWNER_COMMITMENTS.jsonl", "OWNER_COMMITMENTS.jsonl"),
                     ("ACTION_PERMITS.jsonl", "ADMISSION_VERDICTS.jsonl"), ("NEGOTIATION_TRACE.jsonl", "FEDERATION_TRACE.jsonl"),
                     ("JUDGE_RESULT.json", "JUDGE_RESULT.json"), ("RECEIPTS.jsonl", "INDEPENDENT_READBACK_RECEIPTS.jsonl")]:
        for run in ["A2-fresh-1", "A2-attempt-3"]:
            d = pas / run
            d.mkdir(exist_ok=True)
            shutil.copy2(ev / "runs" / run / src, d / dst)
    for run, rid in [("A2-fresh-1", "WO004-A2-fresh-1"), ("A2-attempt-3", "LMF006-A2-attempt-3")]:
        for n in ["DEAL_DOSSIER.json", "CONTRACT_AND_OBLIGATIONS.json", "QUOTE_VERSIONS.json", "PAYER_PAYEE_MAP.json", "UNIT_ECONOMICS.json",
                  "EXECUTION_PASSPORT.json", "E2E_EVENTS.jsonl"]:
            shutil.copy2(ev / "dossier" / rid / n, pas / run / n)
        # owner-world readback: what each owner's world shows at the end, as observed in the Judge's checks
        j = json.loads((ev / "runs" / run / "JUDGE_RESULT.json").read_text())["judgement"]["payload"]
        (pas / run / "OWNER_WORLD_READBACK.json").write_text(json.dumps({"source": "Judge checks (live /reader/state of each owner at judging time)",
                                                                          "checks": [c for c in j["checks"] if c["check"].startswith(("J2", "J3", "J4", "J5"))]}, indent=1))
    hist_runs = {"A2-attempt-3": "HISTORIC_REPLAY (WO-LMF-006, 2026-11-07; Marketplace a7aa474 == 906ec0f for judge/e2e/matcher)",
                 "A2-fresh-1": "FRESH_SYNTHETIC (WO-004, first attempt, scorer frozen before run)"}
    manifest = {"wo": "WO-PAYPAL-LEGION-004", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "counted_fresh_run": "WO004-A2-fresh-1", "replay_run": "LMF006-A2-attempt-3",
                "runs": {r: {"mode": m, "files": {str(p.relative_to(ev / "runs" / r)).replace("\\", "/"): sha(p) for p in sorted((ev / "runs" / r).rglob("*")) if p.is_file()},
                             "hub_result": json.loads((ev / "runs" / r / "HUB_RESULT.json").read_text()),
                             "inference": usage(ev / "runs" / r)} for r, m in hist_runs.items()},
                "freeze": json.loads((ev / "FREEZE_BEFORE_FRESH_RUN.json").read_text()),
                "fresh_negatives_harness": json.loads((ev / "fresh" / "negatives" / "NEGATIVE_RESULTS.json").read_text())["summary"]}
    (ev / "RUN_MANIFEST.json").write_text(json.dumps(manifest, indent=1))
    ri = {}
    for rid in ["WO004-A2-fresh-1", "LMF006-A2-attempt-3"]:
        d = json.loads((ev / "dossier" / rid / "DEAL_DOSSIER.json").read_text())
        ri[rid] = {"evidence_mode": d["evidence_mode"], "events": len(d["events"]), "first_t": d["events"][0]["t"], "last_t": d["events"][-1]["t"],
                   "event_ids": [e["event_id"] + " " + e["ref"] for e in d["events"]]}
    (ev / "REPLAY_INDEX.json").write_text(json.dumps(ri, indent=1))
    (ev / "MISSING_EVIDENCE.json").write_text(json.dumps([
        {"name": "PAYPAL_PROVIDER_EVIDENCE.json", "status": "NOT_RUN", "blocking_prerequisite": "PayPal admission F (A03 Z, pause active, A04 pin mismatch, 4 holds open, no new mission)", "remedy": "owner authority path in PAYPAL_BLOCKERS.json"},
        {"name": "DEMO_CAPTURE.mp4", "status": "NOT_RUN", "blocking_prerequisite": "no screen recording performed in this sprint", "remedy": "record per DEMO_SCRIPT.md (<180 s) after owner review"},
        {"name": "UX_TEST (independent human)", "status": "Z", "blocking_prerequisite": "no unaided external viewer", "remedy": "5-minute unaided test with 8 questions"},
        {"name": "ECONOMIC_COUNTERFACTUAL_A4", "status": "Z", "blocking_prerequisite": "no sourced incumbent quote", "remedy": "obtain dated aggregator/distributor quote"},
        {"name": "Historic Judge offline re-run", "status": "Z", "blocking_prerequisite": "Judge reads live owner reader endpoints; historic processes gone", "remedy": "fresh run judged live (done: WO004-A2-fresh-1)"},
        {"name": "Current provider state of 4 historic holds", "status": "Z", "blocking_prerequisite": "A04 local checkout 4c7e6b7 != pin ff6dcd1; protected tooling not run", "remedy": "sync A04 to pin, re-accept, read-only GET"}], indent=1))
    lines = []
    for p in sorted(ev.rglob("*")):
        if p.is_file() and p.name != "SHA256SUMS":
            lines.append(f"{sha(p)}  {str(p.relative_to(ev)).replace(chr(92), '/')}")
    (ev / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    print(json.dumps({"files": len(lines), "fresh_inference": manifest["runs"]["A2-fresh-1"]["inference"]}))


if __name__ == "__main__":
    main(sys.argv[1])
