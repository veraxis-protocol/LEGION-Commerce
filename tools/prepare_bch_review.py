"""AUTHORITY-005: emit an UNSIGNED v4 review request binding the qualified BCh source. Never signs.

It binds exactly what the independent acceptance reviewer must evaluate before signing
commerce.a03.acceptance.v1 / commerce.bridge.installation.v1: the current bridge and adapter
source hashes, the BCh hot-path files compared with the measured commit, the qualified Core and
decider, the profile and frozen interface, and the evidence hash sets.

Usage: python tools/prepare_bch_review.py <qualify-run-dir> <latency-deposit-dir> <adversarial-dir> <out.json>
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from commerce_bridge.sources import FROZEN_INTERFACE_SHA256, adapter_hashes, bridge_hashes
from commerce_bridge.qualified import QUALIFIED, QUALIFIED_DECIDE_SHA256, PROFILE

MEASURED = "ca897f7badeea4f2b66a52bc684df18008bc4f4d"
HOT_PATH = ["commerce_bridge/prepared.py", "commerce_bridge/consolidated.py", "commerce_bridge/consolidated_worker.py",
            "commerce_bridge/transport.py", "commerce_bridge/core_worker.py", "commerce_bridge/qualified_worker.py",
            "commerce_bridge/qualified.py", "commerce_bridge/admission.py", "commerce_bridge/runtime.py",
            "commerce_bridge/locked_readiness.py", "commerce_bridge/witness.py", "commerce_bridge/sources.py"]


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def git(*a): return subprocess.check_output(["git", *a], cwd=ROOT).decode()


def main():
    run, latency, adv, out = (Path(a) for a in sys.argv[1:5])
    summary = json.loads((run / "SUMMARY.json").read_text())
    adv_sum = json.loads((adv / "ADVERSARIAL_RESULTS.json").read_text())["summary"]
    assert summary["A03_passed"] == 13 and summary["model_mismatches"] == summary["alpha_mismatches"] == summary["false_authorizations"] == 0
    assert adv_sum["verdict"] == "PASS"
    hot = {}
    for f in HOT_PATH:
        # Compare git blob ids (EOL-normalized by .gitattributes); runtime hashes are of working-tree bytes.
        measured = git("rev-parse", f"{MEASURED}:{f}").strip()
        current = git("hash-object", f).strip()
        hot[f] = {"working_tree_sha256": sha(ROOT / f), "current_blob": current, "measured_ca897f7_blob": measured,
                  "identical": current == measured}
    request = {
        "schema": "commerce.bridge.review-request.v4", "status": "UNSIGNED_REVIEW_REQUIRED", "signature": None,
        "not_an_execution_permit": True, "native_payment_dispatches": 0,
        "source_commit": git("rev-parse", "HEAD").strip(), "measured_admission_commit": MEASURED,
        "admission_runtime": "BCh: QualifiedSources(immutable) + ConsolidatedSession + PreparedAdmission(overlap) [+ hot standby optional]",
        "hot_path_files": hot, "hot_path_identical_to_measured": all(v["identical"] for v in hot.values()),
        "qualified_core": QUALIFIED, "qualified_decider_sha256": QUALIFIED_DECIDE_SHA256,
        "qualification_profile": PROFILE, "qualification_profile_sha256": sha(ROOT / PROFILE),
        "frozen_interface_sha256": FROZEN_INTERFACE_SHA256,
        "acceptance_must_bind": {"runtime_source_hashes": adapter_hashes(), "bridge_source_hashes": bridge_hashes()},
        "results": {"qualification_BCh": summary, "authority_adversarial": adv_sum},
        "evidence_files": {
            "qualify_run": {p.name: sha(p) for p in sorted(run.iterdir()) if p.is_file()},
            "latency_004_deposit_manifest_sha256": sha(latency / "EVIDENCE_MANIFEST.sha256"),
            "authority_adversarial": {p.name: sha(p) for p in sorted(adv.iterdir()) if p.is_file()}},
        "reviewer_must_also_review": [
            "compiler and axiom transcripts of the qualified Formal Core (FORMAL_CORE_001)",
            "A01/A02 upstream acceptance evidence (not present on this host)",
            "actual Core/CAGE/VEIP source provenance and key custody (not provisioned)",
            "process and key custody (docs/AUTHORITY_CUSTODY.md, registry and enrollments)",
            "latency disposition: GO under unthrottled host conditions only (COMMERCE_LATENCY_004)"],
        "signers_required": {
            "control": "Veraxis operator: real mission, control, bridge policy (with real Core/CAGE/VEIP/reporter keys)",
            "admission": "designated admission issuer: commerce_authority sign-permit over a real eligible issuer request",
            "acceptance": "designated INDEPENDENT reviewer (not Claude, not the operator): A03 acceptance, A01/A02 upstream, installation manifest"},
    }
    Path(out).write_text(json.dumps(request, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"written": str(out), "hot_path_identical_to_measured": request["hot_path_identical_to_measured"]}))


if __name__ == "__main__":
    main()
