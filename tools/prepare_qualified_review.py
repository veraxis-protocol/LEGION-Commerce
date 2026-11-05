"""Emit an UNSIGNED v3 review request. Never signs, never creates authority.

Usage: python tools/prepare_qualified_review.py <run-dir> <replay-dir> <bridge-diff-dir> <out-dir>
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


def files(d):
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(d).iterdir()) if p.is_file()}


def main():
    run, replay, diff, out = (Path(a).resolve() for a in sys.argv[1:5])
    summary = json.loads((run / "SUMMARY.json").read_text())
    dsum = json.loads((diff / "SUMMARY.json").read_text())
    assert summary["A03_passed"] == 13 and summary["model_mismatches"] == 0 and summary["alpha_mismatches"] == 0
    assert summary["false_authorizations"] == 0 and dsum["mismatches"] == 0 and dsum["false_admissions"] == 0
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    manifest = {
        "schema": "commerce.bridge.review-request.v3", "status": "UNSIGNED_REVIEW_REQUIRED", "signature": None,
        "not_an_execution_permit": True, "native_payment_dispatches": 0,
        "source_commit": head, "qualified_core": QUALIFIED, "qualified_decider_sha256": QUALIFIED_DECIDE_SHA256,
        "qualification_profile": PROFILE, "qualification_profile_sha256": hashlib.sha256((ROOT / PROFILE).read_bytes()).hexdigest(),
        "frozen_interface_sha256": FROZEN_INTERFACE_SHA256, "runtime_source_hashes": adapter_hashes(), "bridge_source_hashes": bridge_hashes(),
        "results": {"qualification": summary, "bridge_differential": {k: dsum[k] for k in ("cases", "matched", "mismatches", "false_admissions", "categories")}},
        "evidence_files": {**files(run), **files(replay), **files(diff)},
        "performance_disposition": summary["performance"],
        "signers_required": {
            "control": "control owner: sign commerce.mission.v1, commerce.control.v1, commerce.bridge.policy.v1 (real mission terms)",
            "admission": "admission issuer: sign commerce.permit.v1 over a real ALLOW_ISSUER_REQUEST_ONLY assessment",
            "acceptance": "independent technical acceptance owner (not the developer): review this package, sign commerce.a03.acceptance.v1, A01/A02 upstream acceptances, commerce.bridge.installation.v1",
            "core": "authorized Core observer: sign commerce.core.observation.v1",
            "cage": "CAGE authority: sign commerce.cage.authorization.v1 (ALLOW)",
            "veip": "VEIP authority: sign commerce.veip.authorization.v1 (ELIGIBLE)",
            "reporters": ">= f+1 enrolled reporters with distinct, reviewed independence bases"},
        "required_review": ["Profile v3 premises and model limits", "Actual Core/CAGE/VEIP input provenance",
                            "Existing key lifecycle and role scope", "A01/A02 and A03 independent acceptance",
                            "Latency disposition FUNCTIONAL_PASS_PERFORMANCE_FAIL", "Agent/credential custody boundary",
                            "Authenticated buyer approval and native effect verification"]}
    out.mkdir(parents=True, exist_ok=False)
    (out / "EVIDENCE_MANIFEST_UNSIGNED.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"review_request": str(out), "signed": False, "files": len(manifest["evidence_files"])}))


if __name__ == "__main__":
    main()
