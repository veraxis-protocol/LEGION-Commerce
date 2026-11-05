"""WP3: locked readiness (snapshot) vs original disk readiness, same inputs.

For each mutation a fresh CONFORMANCE-ONLY fixture is built, the mutation is
applied to the acceptance envelope / evidence files / clock, then two
BridgeAdmission objects are built over identical inputs: one evaluating the
original disk path (A04 Admission.ready + BridgeAdmission.ready), one the OS-locked
snapshot. Their outcomes must be identical. Also checks the OS denies writes.

Usage: python tools/ready_equivalence.py <out.json>
"""
import copy
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True
sys.pycache_prefix = str(Path(tempfile.gettempdir()) / ("legion-nopyc-" + str(time.time_ns())))
sys.path.insert(0, str(ROOT))
from commerce_adapter.contracts import Denied
from commerce_bridge.admission import BridgeAdmission
from commerce_bridge.qualified import QualifiedSources
from bridge_tests.fixtures import Fixture


def sha(b): return hashlib.sha256(b).hexdigest()


def outcome(fn):
    try:
        fn(); return "PASS"
    except Denied as e:
        return "DENY:" + str(e)
    except Exception as e:  # the disk path may raise non-Denied (e.g. JSON); must match too
        return "EXC:" + type(e).__name__


def build(adm, acceptance, locked):
    a = BridgeAdmission(adm.trust, acceptance, adm.evidence_dir, adm.a03_hash, adm.source_pins, clock=adm.clock,
                        sources=adm.sources, policy_envelope=adm.policy_envelope, bundle_dir=adm.bundle_dir)
    if not locked:
        a.close(); a.locked = None
    return a


def mutate(f, adm, name):
    """Returns the acceptance envelope to use; may edit files / clock."""
    p = copy.deepcopy(adm.acceptance["payload"]); ev = adm.evidence_dir
    def resign(payload): return f.sign("acceptance", "commerce.a03.acceptance.v1", payload)
    def upstream(order, edit):
        path = ev / f"UPSTREAM_{order}_ACCEPTANCE.json"
        env = json.loads(path.read_text()); up = env["payload"]; edit(up)
        path.write_text(json.dumps(f.sign("acceptance", "commerce.upstream.acceptance.v1", up)))
        p["evidence_hashes"][path.name] = sha(path.read_bytes())
    if name == "baseline": return adm.acceptance
    if name == "acceptance_signature":
        env = copy.deepcopy(adm.acceptance); env["signature"] = env["signature"][:-4] + ("AAAA" if not env["signature"].endswith("AAAA") else "BBBB"); return env
    if name == "status_fail": p["status"] = "FAIL"
    if name == "tdd_mismatch": p["tdd_sha256"] = "0" * 64
    if name == "source_pins_changed": p["source_pins"] = dict(p["source_pins"], LEGION="0" * 40)
    if name == "tests_missing": p["tests"].pop("A03-T13")
    if name == "acceptance_expired": f.now = 1000 + 900
    if name == "adapter_schema": p["adapter_schema"] = "other"
    if name == "adapter_source_hash": k = next(iter(p["runtime_source_hashes"])); p["runtime_source_hashes"][k] = "0" * 64
    if name == "evidence_hash_wrong": p["evidence_hashes"]["PROOF_MAP.json"] = "0" * 64
    if name == "required_evidence_absent": p["evidence_hashes"].pop("LEAN_BUILD.txt")
    if name == "evidence_path_escape":
        (ev.parent / "outside.txt").write_text("x"); p["evidence_hashes"]["../outside.txt"] = sha(b"x")
    if name == "evidence_file_missing": p["evidence_hashes"]["NOT_THERE.json"] = "0" * 64
    if name == "evidence_tampered_on_disk": (ev / "AXIOM_AUDIT.txt").write_text("tampered")
    if name == "interface_hash": p["frozen_interface_sha256"] = "0" * 64
    if name == "bridge_source_hash": k = next(iter(p["bridge_source_hashes"])); p["bridge_source_hashes"][k] = "0" * 64
    if name == "native_manifest_hash": p["native_source_manifest_hash"] = "0" * 64
    if name == "policy_hash": p["bridge_policy_hash"] = "0" * 64
    if name == "upstream_flag_fail": p["upstream_acceptance"]["A01"] = "FAIL"
    if name == "upstream_malformed_json":
        path = ev / "UPSTREAM_A02_ACCEPTANCE.json"; path.write_text("{not json"); p["evidence_hashes"][path.name] = sha(path.read_bytes())
    if name == "upstream_signature":
        path = ev / "UPSTREAM_A01_ACCEPTANCE.json"; env = json.loads(path.read_text())
        env["payload"]["status"] = "PASS "; path.write_text(json.dumps(env)); p["evidence_hashes"][path.name] = sha(path.read_bytes())
    if name == "upstream_status_fail": upstream("A01", lambda u: u.update(status="FAIL"))
    if name == "upstream_expired":
        upstream("A02", lambda u: u.update(expires_at=1010)); f.now = 1020
    if name == "upstream_evidence_unbound":
        upstream("A01", lambda u: u["evidence_hashes"].update({"A01_RESULT.json": "0" * 64}))
    return resign(p)


MUTATIONS = ["baseline", "acceptance_signature", "status_fail", "tdd_mismatch", "source_pins_changed", "tests_missing",
             "acceptance_expired", "adapter_schema", "adapter_source_hash", "evidence_hash_wrong", "required_evidence_absent",
             "evidence_path_escape", "evidence_file_missing", "evidence_tampered_on_disk", "interface_hash",
             "bridge_source_hash", "native_manifest_hash", "policy_hash", "upstream_flag_fail", "upstream_malformed_json",
             "upstream_signature", "upstream_status_fail", "upstream_expired", "upstream_evidence_unbound"]


def main():
    sources = QualifiedSources(ROOT / "runtime_sources", immutable=True)
    rows = []
    try:
        for name in MUTATIONS:
            with tempfile.TemporaryDirectory() as tmp:
                f = Fixture(tmp, qualified_sources=sources)
                gateway, provider, mission, permit = f.gateway()
                base = gateway.admission; base.close()   # release locks so the mutation can edit files
                acceptance = mutate(f, base, name)
                disk = build(base, acceptance, locked=False)
                locked_adm = None
                try:
                    locked_adm = build(base, acceptance, locked=True)
                    lo = outcome(locked_adm.ready)
                except Denied as e:  # lock-time denial (e.g. file vanished) still a denial
                    lo = "DENY:" + str(e)
                do = outcome(disk.ready)
                write_denied = None
                if locked_adm is not None and locked_adm.locked is not None and name == "baseline":
                    try:
                        with open(base.evidence_dir / "PROOF_MAP.json", "ab"): pass
                        write_denied = False
                    except PermissionError:
                        write_denied = True
                if locked_adm is not None: locked_adm.close()
                rows.append({"mutation": name, "disk": do, "locked": lo, "equal": do == lo, "write_denied_under_lock": write_denied})
    finally:
        sources.close()
    out = {"cases": len(rows), "equal": sum(r["equal"] for r in rows), "rows": rows,
           "baseline_pass_both": rows[0]["disk"] == rows[0]["locked"] == "PASS",
           "denials_both": sum(r["disk"] != "PASS" and r["equal"] for r in rows[1:]),
           "os_write_denied": rows[0]["write_denied_under_lock"]}
    Path(sys.argv[1]).write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    for r in rows: print(f"{r['mutation']:28} disk={r['disk']:42} locked={r['locked']:42} {'OK' if r['equal'] else 'DIFF'}")
    print(json.dumps({k: out[k] for k in ("cases", "equal", "baseline_pass_both", "denials_both", "os_write_denied")}))


if __name__ == "__main__":
    main()
