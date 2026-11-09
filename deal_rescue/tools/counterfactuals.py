"""Collect A0/A1/A2/A3 counterfactuals from retained evidence (no re-scoring) + the fresh WO-004 A2 attempt.
Usage: python -B deal_rescue/tools/counterfactuals.py <evidence_dir>"""
import hashlib
import json
import sys
from pathlib import Path

LMF6 = Path(r"C:\Users\Administrator\Dropbox\PayPal\MARKET_GENESIS_20261107\WO-LMF-006")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def judge_of(d):
    j = json.loads((d / "JUDGE_RESULT.json").read_text())
    p = j["judgement"]["payload"]
    return {"verdict": p["verdict"], "failed": [c for c in p["checks"] if not c["pass"]],
            "file": str(d / "JUDGE_RESULT.json"), "sha256": sha(d / "JUDGE_RESULT.json")}


def main(ev):
    ev = Path(ev)
    b = json.loads((LMF6 / "comparators" / "BASELINE_RESULTS.json").read_text())
    fresh_runs = sorted(p for p in (ev / "runs").glob("A2-fresh-*") if p.is_dir())
    out = {
        "source": {"file": str(LMF6 / "comparators" / "BASELINE_RESULTS.json"), "sha256": sha(LMF6 / "comparators" / "BASELINE_RESULTS.json")},
        "A0": {"label": "five isolated 20-unit buyers, direct unilateral factory probes", "verdict": "T: NO_DEAL, 5/5 refused (quantity_meets_moq)",
               "probes": b["arms"]["A0"]["direct_unilateral_probes"]},
        "A1": {"label": "owner peers known by address (no discovery claim)", **b["arms"]["A1"], "judge": judge_of(LMF6 / "A1-known-peers")},
        "A2_historic": {"label": "marketplace discovery + federation (WO-LMF-006 campaign)",
                        "attempts": [{"run": "A2-first-attempt", "outcome": "NO_DEAL", "cause": "owner NIM call failed (HTTPError) -> owner failed closed; 0 effects", "judge": judge_of(LMF6 / "A2-first-attempt")},
                                     {"run": "A2-attempt-2", "outcome": "FULFILLED", "cause": "owner agents echoed private data in commitment rationale (disclosure leak)", "judge": judge_of(LMF6 / "A2-attempt-2")},
                                     {"run": "A2-attempt-3", "outcome": "FULFILLED", "judge": judge_of(LMF6 / "A2-attempt-3")}],
                        "VCC_at_1": 0, "passes_over_attempts": "1/3"},
        "A2_fresh_WO004_runs": [{"run": f.name, "outcome": json.loads((f / "HUB_RESULT.json").read_text())["outcome"], "judge": judge_of(f),
                                 "first_attempt_pass": judge_of(f)["verdict"] == "PASS_COMPLETED"} for f in fresh_runs],
        "A2_fresh_WO004": {"label": "fresh first attempts in WO-004 (each its own preregistered first attempt, same frozen scenario)",
                           "outcome": json.loads((fresh_runs[-1] / "HUB_RESULT.json").read_text())["outcome"], "judge": judge_of(fresh_runs[-1]),
                           "first_attempt_pass": judge_of(fresh_runs[-1])["verdict"] == "PASS_COMPLETED",
                           "VCC_at_1": f"{sum(judge_of(f)['verdict'] == 'PASS_COMPLETED' for f in fresh_runs)}/{len(fresh_runs)}"},
        "A3": {"label": "centralized matcher, identical public intents", **b["arms"]["A3"], "claim": "PARITY on matching; LEGION claims no matching uplift"},
        "A4": {"label": "realistic incumbent group-purchasing intermediary / distributor", "status": "Z — not sourced or priced; market superiority UNPROVEN",
               "candidates_named_in_MARKET_ORIGIN_001": "Candex, Upwork, Fairmarkit, ORO (named, not priced for this request)"},
    }
    (ev / "COUNTERFACTUAL_A0_A1_A2_A3.json").write_text(json.dumps(out, indent=1))
    (ev / "ECONOMIC_COUNTERFACTUAL_A4.json").write_text(json.dumps({"status": "Z", "name": "A4 incumbent intermediary", "blocking_prerequisite": "sourced, dated quote from a real aggregator/distributor for 5x20 SM-2 units incl. fees", "remedy": "obtain and preregister before claiming economic advantage"}, indent=1))
    print(json.dumps({"A2_fresh_first_attempt_pass": out["A2_fresh_WO004"]["first_attempt_pass"], "A2_hist": out["A2_historic"]["passes_over_attempts"]}))


if __name__ == "__main__":
    main(sys.argv[1])
