"""WO-COM-LATENCY-004: build the same CONFORMANCE-ONLY gateway under each architecture.

A  : existing  - BridgeAdmission + QualifiedSession (3 parent-driven isolated round trips)
B  : consolidated worker - BridgeAdmission (unchanged parent code) + ConsolidatedSession
C  : prepared context - PreparedAdmission + QualifiedSession (A's processes)
BC : PreparedAdmission(overlap) + ConsolidatedSession

The permit/bundle are produced exactly as bridge_tests.fixtures.Fixture.gateway does
(the reference assessment is computed by the ORIGINAL Bridge); if that reference
assessment is denied, the permit carries an unrelated assessment hash so the gateway
reaches the same denial. Test keys only; no network.
"""
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True
if not getattr(sys, "pycache_prefix", None):
    sys.pycache_prefix = str(Path(tempfile.gettempdir()) / ("legion-nopyc-" + str(time.time_ns())))
sys.path.insert(0, str(ROOT))
from commerce_adapter.contracts import Denied, digest  # noqa: E402
from commerce_adapter.gateway import Gateway  # noqa: E402
from commerce_bridge.admission import BridgeAdmission  # noqa: E402
from commerce_bridge.prepared import PreparedAdmission  # noqa: E402
from commerce_bridge.qualified import QualifiedSources  # noqa: E402
from commerce_bridge.consolidated import install as install_consolidated  # noqa: E402
from commerce_bridge.sources import adapter_hashes, bridge_hashes  # noqa: E402
from bridge_tests.fixtures import Fixture  # noqa: E402
from tests.helpers import FakePayPal  # noqa: E402

ARCHS = ("A", "B", "C", "BC")
SPIN_SECONDS = 0.02   # variant suffix "s": bounded spin-wait for answers (experiment E1)
HOT_SECONDS = 60.0    # variant suffix "h": worker hot standby, busy-polls its request pipe (experiment E1g)


def base(arch):
    return arch[:-1] if arch.endswith(("s", "h")) else arch


def sources_for(arch, **session_kwargs):
    src = QualifiedSources(ROOT / "runtime_sources", immutable=True)
    if base(arch) in ("B", "BC"):
        if arch.endswith("s"): session_kwargs.setdefault("spin_seconds", SPIN_SECONDS)
        if arch.endswith("h"): session_kwargs.setdefault("hot_standby_seconds", HOT_SECONDS)
        install_consolidated(src, **session_kwargs)
    return src


def admission_class(arch):
    arch = base(arch)
    if arch in ("A", "B"):
        return BridgeAdmission, {}
    return PreparedAdmission, {"overlap": arch == "BC"}


def build_gateway(f, root, arch, sources, permit_mutator=None, acceptance_mutator=None, bundle=None, intent=None):
    """Mirror of Fixture.gateway() for an arbitrary architecture (f supplies keys/state)."""
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    bundle = bundle if bundle is not None else f.bundle()
    try:
        assessment_hash = digest("commerce.bridge.assessment.v1", f.assess(bundle, intent))
        oh = f.last["observation_hash"]
    except Denied:
        assessment_hash = "0" * 64
        oh = digest("commerce.core.observation.v1", f.observation)
    evidence = root / "accepted"; evidence.mkdir()
    required = ["PROOF_MAP.json", "LEAN_BUILD.txt", "AXIOM_AUDIT.txt", "FORMAL_INPUTS.jsonl", "LEAN_VERDICTS.jsonl",
                "RUNTIME_VERDICTS.jsonl", "MISMATCHES.jsonl", "PERMIT_TRACE.jsonl", "NEGATIVE_RESULTS.json"]
    for name in required: (evidence / name).write_text("CONFORMANCE_ONLY; NOT INDEPENDENT ACCEPTANCE")
    pins = json.loads((ROOT / "design/SOURCE_PINS.json").read_text())
    pins["source_pins"] = f.sources.policy_pins
    for order, tdd in (("A01", "eac9c274f62ba0ca7c7b4c1b109fb5e69ecbc53a09d29efb96cc98af3378551d"),
                       ("A02", "6ceacae3da2e6acb4bc010db67a0cfc03ac50c6236a77c9f8e66f9eacc93782f")):
        result_name = f"{order}_RESULT.json"
        (evidence / result_name).write_text("CONFORMANCE_ONLY; NOT UPSTREAM EVIDENCE")
        payload = {"schema": "commerce.upstream.acceptance.v1", "work_order": order, "status": "PASS", "tdd_sha256": tdd,
                   "source_pins": pins["source_pins"], "mission_hash": f.mh, "expires_at": f.now + 800,
                   "evidence_hashes": {result_name: hashlib.sha256((evidence / result_name).read_bytes()).hexdigest()}}
        name = f"UPSTREAM_{order}_ACCEPTANCE.json"
        (evidence / name).write_text(json.dumps(f.sign("acceptance", "commerce.upstream.acceptance.v1", payload)))
        required.extend([result_name, name])
    acceptance = {"schema": "commerce.a03.acceptance.v1", "status": "PASS", "tdd_sha256": pins["a03_tdd_sha256"], "source_pins": pins["source_pins"],
                  "expires_at": f.now + 800, "tests": {f"A03-T{i:02}": "PASS" for i in range(1, 14)}, "adapter_schema": "commerce.a04.adapter.v1",
                  "runtime_source_hashes": adapter_hashes(), "bridge_source_hashes": bridge_hashes(),
                  "native_source_manifest_hash": digest("commerce.bridge.sources.v1", sources.verify()),
                  "frozen_interface_sha256": hashlib.sha256((ROOT / "docs/ADMISSION_INTERFACE.md").read_bytes()).hexdigest(),
                  "bridge_policy_hash": digest("commerce.bridge.policy.v1", f.policy), "upstream_acceptance": {"A01": "PASS", "A02": "PASS"},
                  "evidence_hashes": {n: hashlib.sha256((evidence / n).read_bytes()).hexdigest() for n in required}, "fixture": "CONFORMANCE_ONLY"}
    if acceptance_mutator: acceptance_mutator(acceptance)
    acceptance_envelope = f.sign("acceptance", "commerce.a03.acceptance.v1", acceptance)
    permit = {"schema": "commerce.permit.v1", "permit_id": "CONFORMANCE-PERMIT", "issued_at": f.now - 100, "expires_at": f.now + 400,
              "quote_expires_at": f.now + 500, "mission_hash": f.mh, "acceptance_hash": digest("commerce.a03.acceptance.v1", acceptance), "epoch": 1,
              "intent": intent or f.intent, "scope": "paypal.sandbox.orders.create_capture", "verdict": "T", "lean_rule_earned": True, "controller_act": True,
              "cage_allow": True, "veip_eligible": True, "prerequisites_satisfied": True,
              "bridge_evidence_hash": digest("commerce.bridge.bundle.v1", bundle), "bridge_assessment_hash": assessment_hash,
              "core_observation_hash": oh}
    if permit_mutator: permit_mutator(permit)
    bundles = root / "bundles"; bundles.mkdir()
    (bundles / (digest("commerce.permit.locator.v1", permit["permit_id"]) + ".json")).write_text(json.dumps(bundle))
    cls, extra = admission_class(arch)
    adm = cls(f.trust, acceptance_envelope, evidence, pins["a03_tdd_sha256"], pins["source_pins"], clock=lambda: f.now,
              sources=sources, policy_envelope=bundle["policy"], bundle_dir=bundles, **extra)
    provider = FakePayPal()
    g = Gateway(root / "ledger.db", adm, provider)
    g.apply_control(f.mission["mission_id"], f.sign("control", "commerce.control.v1", f.control))
    return g, provider, f.sign("control", "commerce.mission.v1", f.mission), f.sign("admission", "commerce.permit.v1", permit)


def outcome(fn):
    try:
        fn(); return "ALLOW"
    except Denied as e:
        return "DENY:" + str(e)
    except Exception as e:  # non-Denied failures are still fail-closed; recorded by type
        return "ERROR:" + type(e).__name__


def check_once(g, mission, permit):
    with g.transaction() as db:
        return g.check(db, mission, permit)



