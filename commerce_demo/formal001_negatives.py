"""FORMAL-001 bypass negatives for the external-effect boundary (no network: the provider is a stub that records
every request it would have sent; evidence_mode is NOT MOCK_ONLY, so the Gateway treats it as external).

B-COM-1  A04 base Admission: a correctly signed permit that merely CLAIMS lean_rule_earned/verdict T.
B-COM-2  v1 bridge assessment without the qualified decider (ZTL witness only): yields no Lean attestation.
G-1      attestation computed for a DIFFERENT intent is not accepted for this effect.
G-2      attestation from an earlier check is not reused: every POST needs the check of its own transaction.
P-1      positive control: the qualified sandbox admission (legion_decide T for the exact action) dispatches.

Usage: python -m commerce_demo.formal001_negatives <out.json>
"""
import base64
import json
import sys
import tempfile
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from commerce_adapter.contracts import Denied, digest
from commerce_adapter.gateway import Gateway
from commerce_authority.custody import sign as raw_sign
from tests.helpers import FakePayPal, Fixture

ROOT = Path(__file__).resolve().parent.parent


class ExternalStub(FakePayPal):
    """Behaves like FakePayPal but declares itself an external provider (the Gateway must enforce Lean)."""
    evidence_mode = "PAYPAL_SANDBOX_STUB_NO_NETWORK"


def mk(tmp, name):
    d = Path(tmp) / name
    d.mkdir()
    return d


def posts(p):
    return [c for c in p.calls if c[0] != "GET"]


def main():
    out = Path(sys.argv[1])
    results = []

    def rec(name, expected, actual, provider, ok, **kw):
        n = len(posts(provider)) if provider is not None else 0
        results.append(dict({"test": name, "expected": expected, "actual": actual, "external_posts": n, "pass": bool(ok)}, **kw))
        print(("PASS " if ok else "FAIL ") + name, actual, n, flush=True)

    with tempfile.TemporaryDirectory() as tmp:
        # ---- B-COM-1: base A04 admission, external provider
        f = Fixture(mk(tmp, "b1"))
        f.provider = ExternalStub()
        f.gateway = Gateway(f.root / "ledger_ext.db", f.admission, f.provider)
        f.apply()
        eid = f.reserve()
        try:
            st = f.gateway.create(eid)
            actual = f"STATE:{st}"
        except Denied as e:
            actual = f"DENY:{e}"
        state = f.gateway.status(eid)["state"]
        rec("B-COM-1_a04_signed_claim_without_lean_blocked", "DENY LEAN_ADMISSION_REQUIRED, 0 POST, state RESERVED",
            actual, f.provider, actual == "DENY:LEAN_ADMISSION_REQUIRED" and not posts(f.provider) and state == "RESERVED",
            state_after=state)
        # same path with the MOCK_ONLY double: the documented test-double exemption (A04 mechanism tests)
        f2 = Fixture(mk(tmp, "b1m"))
        st2 = f2.gateway.create(f2.reserve())
        rec("B-COM-1_control_mock_only_double_exempt", "APPROVAL_PENDING with MOCK_ONLY double (exemption is test-only)",
            f"STATE:{st2}", f2.provider, st2 == "APPROVAL_PENDING")

        # ---- B-COM-2: v1 assessment (no qualified section) yields no attestation
        try:
            from commerce_bridge.admission import lean_attestation
        except ImportError:  # pre-closure code: no attestation mechanism exists at all
            def lean_attestation(*_):
                return "NO_ATTESTATION_MECHANISM"
        v1 = {"core": {"directive": {"directive": "ACT"}, "all_facts_true": True}}
        q = {"core": {"qualified": {"lean": {"verdict": "T"}, "payload_hash": "x"}}}
        intent = f.permits[0]["intent"]
        a1, a2 = lean_attestation(v1, intent, 1), lean_attestation(q, intent, 1)
        rec("B-COM-2_v1_bridge_without_qualified_decider_no_attestation", "None for v1, T bound to intent for qualified",
            {"v1": a1, "qualified_verdict": (a2 if isinstance(a2, dict) else {}).get("lean_verdict")}, None,
            a1 is None and isinstance(a2, dict) and a2["lean_verdict"] == "T" and a2["action_digest"] == digest("commerce.intent.v1", intent))

        # ---- G-1: attestation for another intent
        class OtherIntent:
            def __init__(self, base, other):
                self.base, self.other, self.last_decision = base, other, None
                self.trust = base.trust

            def control(self, env):
                return self.base.control(env)

            def check(self, m, p, c):
                r = self.base.check(m, p, c)
                self.last_decision = {"lean_verdict": "T", "action_digest": digest("commerce.intent.v1", self.other)}
                return r
        f3 = Fixture(mk(tmp, "g1"))
        other = dict(f3.permits[0]["intent"], amount_cents=1)
        f3.provider = ExternalStub()
        f3.gateway = Gateway(f3.root / "ledger_ext.db", OtherIntent(f3.admission, other), f3.provider)
        f3.apply()
        eid = f3.reserve()
        try:
            actual = f"STATE:{f3.gateway.create(eid)}"
        except Denied as e:
            actual = f"DENY:{e}"
        rec("G-1_attestation_for_other_intent_rejected", "DENY LEAN_ADMISSION_REQUIRED, 0 POST", actual, f3.provider,
            actual == "DENY:LEAN_ADMISSION_REQUIRED" and not posts(f3.provider))

        # ---- G-2: attestation produced once, never again (cached verdict)
        class OnceOnly(OtherIntent):
            def __init__(self, base):
                super().__init__(base, None)
                self.n = 0

            def check(self, m, p, c):
                r = self.base.check(m, p, c)
                self.n += 1
                if self.n == 1:
                    self.last_decision = {"lean_verdict": "T", "action_digest": digest("commerce.intent.v1", r[2])}
                return r
        f4 = Fixture(mk(tmp, "g2"))
        f4.provider = ExternalStub()
        f4.gateway = Gateway(f4.root / "ledger_ext.db", OnceOnly(f4.admission), f4.provider)
        f4.apply()
        eid = f4.reserve()   # check #1 sets the only attestation; create re-checks and must not reuse it
        try:
            actual = f"STATE:{f4.gateway.create(eid)}"
        except Denied as e:
            actual = f"DENY:{e}"
        rec("G-2_stale_cached_attestation_not_reused", "DENY LEAN_ADMISSION_REQUIRED, 0 POST", actual, f4.provider,
            actual == "DENY:LEAN_ADMISSION_REQUIRED" and not posts(f4.provider))

        # ---- P-1: qualified sandbox admission (real legion_decide) dispatches create, then capture after approval
        from commerce_bridge.consolidated import install
        from commerce_bridge.qualified import QualifiedSources
        from . import sandbox_admission as S
        from .mission import RUNTIME_ROLES
        sources = QualifiedSources(ROOT / "runtime_sources", immutable=True)
        install(sources)
        try:
            now = int(time.time())
            owner = Ed25519PrivateKey.generate()
            rt = {r: Ed25519PrivateKey.generate() for r in RUNTIME_ROLES}
            rt_pub = {r: base64.b64encode(k.public_key().public_bytes_raw()).decode() for r, k in rt.items()}
            owner_pub = base64.b64encode(owner.public_key().public_bytes_raw()).decode()
            m = {"schema": S.MISSION_DOMAIN, "profile": "sandbox-demo-v1", "mission_id": "FORMAL001-P1", "contract_version": "1",
                 "currency": "USD", "cap_cents": 9900, "merchant_ids": ["TESTMERCHANT1"], "actor": "agent:procurement-executor",
                 "epoch": 1, "deadline": now + 3600, "obligation_ids": ["pay-b1"], "payer_obligation_ids": ["pay-b1"],
                 "federation_contract_hash": "c" * 64, "runtime_public_keys": rt_pub}
            c = {"schema": S.CONTROL_DOMAIN, "sequence": 1, "mission_id": m["mission_id"], "mission_hash": digest(S.MISSION_DOMAIN, m),
                 "epoch": 1, "active": True, "expires_at": now + 3600, "revoked_permits": [], "revoked_actors": []}
            m_env, c_env = raw_sign(owner, S.MISSION_DOMAIN, m), raw_sign(owner, S.CONTROL_DOMAIN, c)
            adm = S.SandboxDemoAdmission(owner_pub, rt_pub, sources, observer=S.fresh_observer(rt, sources))
            prov = ExternalStub()
            g = Gateway(Path(tmp) / "p1.db", adm, prov)
            g.apply_control(m["mission_id"], c_env)
            line = [{"service_id": "LMF-B1", "supplier_id": "owner:f1", "quote_id": "q-b1", "price_cents": 9900,
                     "effect_slot": "payer-b1", "obligation_id": "pay-b1"}]
            p_env, decision, _ = S.issue_permit(adm, m_env, rt, line, 9900, sources)
            eid = g.reserve(m_env, p_env)
            st = g.create(eid)
            prov.approve()
            cap = g.capture(eid)
            rec("P-1_qualified_lean_T_dispatches_create_and_capture", "APPROVAL_PENDING then CAPTURE_CONFIRMED, 2 POST",
                f"{st}->{cap}", prov, st == "APPROVAL_PENDING" and cap == "CAPTURE_CONFIRMED" and len(posts(prov)) == 2,
                lean_verdict=decision["lean_verdict"], decider_sha256=sources.manifest.get("legion_decide_sha256"))
        finally:
            sources.close()

    s = {"tests": len(results), "passed": sum(r["pass"] for r in results),
         "forbidden_posts": sum(r["external_posts"] for r in results if r["test"].startswith(("B-", "G-")) and "_control_" not in r["test"]),
         "verdict": "PASS" if all(r["pass"] for r in results) else "FAIL"}
    out.write_text(json.dumps({"summary": s, "results": results}, indent=1, default=str))
    print(json.dumps(s))


if __name__ == "__main__":
    main()
