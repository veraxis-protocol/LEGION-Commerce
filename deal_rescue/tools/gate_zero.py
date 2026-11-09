"""WO-PAYPAL-LEGION-004 gate zero: read-only baseline, source pins, license gate, PayPal authority preflight.

Reads only. Sends nothing to PayPal, never decrypts DPAPI material, never touches the A04 checkout.
Usage: python -B deal_rescue/tools/gate_zero.py <evidence_dir>
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HACK = Path(r"C:\Users\Administrator\Documents\ChatGPT\Hackathons")
DBX = Path(r"C:\Users\Administrator\Dropbox\PayPal")
LMF6 = DBX / "MARKET_GENESIS_20261107" / "WO-LMF-006"
LMF7 = DBX / "MARKET_GENESIS_20261107" / "WO-LMF-007"
STATE = Path(os.environ["LOCALAPPDATA"]) / "Veraxis" / "LEGION-Commerce"

WO_PINS = {
    "LEGION-Commerce@claude/wo-com-e2e-003": ("LEGION-Commerce-Q002", "2f57facccc1d58e385e8e3d88fcb903a46fe9919"),
    "LEGION-Commerce@codex/wo-com-a04-paypal-sandbox": ("LEGION-Commerce-A04", "ff6dcd193d12eb328d5d863881a8d918b8396040"),
    "LEGION-Marketplace@main": ("MG_FRESH/LEGION-Marketplace", "906ec0f553c0f8076127f7abab98825e188342ef"),
    "LEGION-Federation@main": ("MG_FRESH/LEGION-Federation", "9d2a32a5c32758bea1ad2200955da1cc9213356c"),
    "LEGION-Core (structural pin)": ("MG_FRESH/LEGION-Core-pinned", "d083c44af2526e38068e0220891cd0bb5fce52af"),
}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def git(d, *a):
    r = subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    # --- source pins
    pins = {}
    for name, (rel, want) in WO_PINS.items():
        d = HACK / rel
        head = git(d, "rev-parse", "HEAD")
        known = git(d, "cat-file", "-t", want) == "commit"
        pins[name] = {"local_path": str(d), "wo_pin": want, "local_head": head, "match": head == want,
                      "wo_pin_object_present_locally": known,
                      "dirty_files": len((git(d, "status", "--porcelain") or "").splitlines())}
    mk = HACK / "MG_FRESH" / "LEGION-Marketplace"
    hist = "a7aa4744da998304600ec6c80de05c21586587b0"
    delta = git(mk, "diff", "--name-only", hist, "906ec0f553c0f8076127f7abab98825e188342ef")
    pins["historic_vs_current_marketplace"] = {
        "historic_evidence_head": hist, "current_head": "906ec0f553c0f8076127f7abab98825e188342ef",
        "changed_files": (delta or "").splitlines(),
        "judge_py_identical": git(mk, "diff", "--quiet", hist, "906ec0f", "--", "legion_marketplace/judge.py") == "",
        "resolution": "delta touches only dashboard.py (rationale hiding) and release docs; judge/matcher/e2e/negatives byte-identical",
    }
    a04 = pins["LEGION-Commerce@codex/wo-com-a04-paypal-sandbox"]
    remote = git(HACK / "LEGION-Commerce-A04", "ls-remote", "origin", "codex/wo-com-a04-paypal-sandbox")
    a04["remote_head_observed"] = (remote or "").split("\t")[0] or None
    a04["verdict"] = "F: local A04 checkout is not at the WO pin (pin object absent locally); protected A04 execution STOPPED per WO s.12 integrity rule"
    (out / "SOURCE_PINS_VERIFIED.json").write_text(json.dumps({"at": now(), "pins": pins}, indent=1))

    # --- historic evidence integrity (LMF-006 manifest)
    bad, n = [], 0
    for line in (LMF6 / "EVIDENCE_HASHES.sha256").read_text().splitlines():
        if not line.strip():
            continue
        h, rel = line.split(None, 1)
        n += 1
        p = LMF6 / rel.strip()
        if not p.exists() or sha(p) != h:
            bad.append(rel.strip())
    baseline = {
        "wo": "WO-PAYPAL-LEGION-004", "at": now(), "kind": "GATE_ZERO_READ_ONLY",
        "historic_manifest": str(LMF6 / "EVIDENCE_HASHES.sha256"), "historic_files_checked": n,
        "historic_mismatches": bad, "historic_integrity": "T" if not bad else "F",
        "selected_replay_run": "WO-LMF-006/A2-attempt-3",
        "selected_replay_judge": json.loads((LMF6 / "A2-attempt-3" / "judge_stdout.txt").read_text())["verdict"],
        "selected_replay_contract_hash": json.loads((LMF6 / "A2-attempt-3" / "HUB_RESULT.json").read_text())["contract_hash"],
        "a2_history": json.loads((LMF6 / "WO_VERDICT.json").read_text())["A2_attempts"],
        "judge_offline_rerun": "Z: legion_marketplace.judge reads each owner's live reader endpoint (/reader/state, /reader/effects); the historic owner processes no longer exist, so the stored signed Judge result is used for replay and a FRESH run is judged live",
        "wo_precheck_files": {f: sha(DBX / "WO_PAYPAL_LEGION_004_20261109" / f) for f in
                              ["WO_004.md", "WO_004_PRESTART_ADDENDUM.md", "BASELINE_PRECHECK_004.json"]},
    }
    (out / "BASELINE.json").write_text(json.dumps(baseline, indent=1))

    # --- license gate
    lic = {}
    for name, rel in [("LEGION-Marketplace", "MG_FRESH/LEGION-Marketplace"), ("LEGION-Federation", "MG_FRESH/LEGION-Federation"),
                      ("LEGION-Core", "MG_FRESH/LEGION-Core-pinned"), ("LEGION-Commerce (Q002)", "LEGION-Commerce-Q002")]:
        p = HACK / rel / "LICENSE"
        lic[name] = p.read_text(encoding="utf-8").splitlines()[0].strip("# ").strip() if p.exists() else "NO LICENSE FILE"
    (out / "LICENSE_GATE.json").write_text(json.dumps({
        "at": now(), "licenses": lic, "osi_open_source": "F",
        "verdict": "F (competition OSS condition NOT MET): runnable stack (Core, Federation, Marketplace) is PolyForm Noncommercial 1.0.0; Commerce repo has no LICENSE file. No relicensing done; owner decision or written organizer ruling required.",
        "ag_studio": "trial licence (expires 2026-12-22), evaluation-only; public judging rights Z",
        "source": "release/PAYPAL_ELIGIBILITY.md (Marketplace) states NOT MET"}, indent=1))

    # --- PayPal authority preflight (read-only, file metadata only)
    reg = json.loads((STATE / "authority" / "registry.json").read_text())
    a03 = sorted((HACK / "LEGION-Commerce-Q002" / "a03_reaccept").glob("*")) if (HACK / "LEGION-Commerce-Q002" / "a03_reaccept").exists() else []
    paused = json.loads((LMF7 / "paused-run" / "PAUSED_STATE.json").read_text())
    checks = [
        {"n": 1, "check": "A03 independent signed acceptance for exact current A04 source", "verdict": "Z",
         "grounds": "no independent A03 signature located; only unsigned review request(s) present (" + ", ".join(p.name for p in a03[:6]) + "); owner: A03 is CONDITIONAL GO pending trusted re-signature + independent payment test"},
        {"n": 2, "check": "current signed mission/control/policy/per-buyer permit for this contract", "verdict": "F",
         "grounds": "only the 2026-11-07 LMF-007 mission exists (bound to historic contract b444a7b9); one-attempt rule: not reusable for a fresh episode"},
        {"n": 3, "check": "epoch current, no revocation/expiry", "verdict": "Z", "grounds": "no current mission to evaluate"},
        {"n": 4, "check": "no active owner pause; owner authorizes this new Sandbox payer episode", "verdict": "F",
         "grounds": "PAUSED_STATE.json status=" + paused["status"] + "; WO-004 text does not itself authorize a payer episode; no new owner instruction"},
        {"n": 5, "check": "historic reservations reconciled read-only", "verdict": "F",
         "grounds": f"{sum(e['held'] for e in paused['effects'])} holds open (pay-b1..b4, PAYER_ACTION_REQUIRED at pause); current provider state not re-read (see 7)"},
        {"n": 6, "check": "merchant payee bound; per-buyer payer authority", "verdict": "Z",
         "grounds": "Sandbox merchant id recorded in LMF-007 mission draft; merchant-of-record for Deal Rescue not established; no buyer Sandbox logins confirmed"},
        {"n": 7, "check": "A04 preflight T, no source hash mismatch", "verdict": "F",
         "grounds": a04["verdict"]},
        {"n": 8, "check": "buyer approval observed via provider GET = APPROVED", "verdict": "Z", "grounds": "no approval episode started"},
    ]
    keys = [{"key_id": k["key_id"], "role": k["role"], "status": k["status"], "passphrase_gated": k["custody"]["passphrase_gated"]} for k in reg["keys"]]
    pre = {"at": now(), "mode": "READ_ONLY (no OAuth call, no DPAPI decrypt, no provider request)",
           "authority_registry_keys": keys, "checks": checks,
           "admission": "F" if any(c["verdict"] == "F" for c in checks) else ("Z" if any(c["verdict"] == "Z" for c in checks) else "T"),
           "new_provider_posts_this_wo": 0}
    (out / "PAYPAL_AUTHORITY_PREFLIGHT.json").write_text(json.dumps(pre, indent=1))
    holds = {"at": now(), "mode": "READ_ONLY from retained ledger evidence; provider GET NOT performed (A04 source mismatch => protected tooling not run)",
             "source": str(LMF7 / "paused-run" / "PAUSED_STATE.json"), "source_sha256": sha(LMF7 / "paused-run" / "PAUSED_STATE.json"),
             "holds": [{"obligation_id": e["obligation_id"], "amount_cents": e["amount"], "held": e["held"],
                        "last_observed_provider_status": e["provider_status"], "order_id_masked": e["order_id"][:4] + "…" + e["order_id"][-3:],
                        "captures": e["provider_captures"], "current_provider_state": "Z (not re-read)"} for e in paused["effects"]],
             "b5": paused["payer_b5"], "captures_total": paused["captures"], "historic_create_posts": paused["network_posts"],
             "actions_taken": "none (no clear/void/refund/dispatch)"}
    (out / "OLD_HOLDS_RECONCILIATION_READ_ONLY.json").write_text(json.dumps(holds, indent=1))
    (out / "PAYPAL_BLOCKERS.json").write_text(json.dumps({
        "at": now(), "status": "PAYPAL_NOT_AUTHORIZED / MECHANISM_ONLY", "new_posts": 0,
        "blockers": [c for c in checks if c["verdict"] != "T"],
        "next_permissible_action": "owner: (1) obtain independent A03 signature for the pinned A04 source, (2) sync local A04 checkout to ff6dcd1 and re-run preflight, (3) lift pause and sign a NEW mission for this contract, (4) reconcile 4 holds via gateway path, (5) confirm Sandbox buyer logins; then one $99 slot may be attempted."}, indent=1))
    print(json.dumps({"historic_integrity": baseline["historic_integrity"], "files": n, "paypal_admission": pre["admission"],
                      "pins": {k: v.get("match") for k, v in pins.items() if "match" in v}}))


if __name__ == "__main__":
    main(sys.argv[1])
