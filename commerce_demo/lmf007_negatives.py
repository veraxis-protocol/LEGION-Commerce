"""WO-LMF-007 hard negatives on the qualified BCh runtime + UNCHANGED A04 Gateway + FakePayPal (no network, TEST keys).

Usage: python -m commerce_demo.lmf007_negatives <out.json>
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
from commerce_bridge.consolidated import install
from commerce_bridge.qualified import QualifiedSources
from tests.helpers import FakePayPal
from . import sandbox_admission as S
from .mission import RUNTIME_ROLES

ROOT = Path(__file__).resolve().parent.parent
M = "TESTMERCHANT1"
PAYERS = ["b1", "b2", "b3", "b4", "b5", "b6"]


def line(b, amt=9900):
    return [{"service_id": f"LMF-{b.upper()}", "supplier_id": "owner:f1", "quote_id": f"q-{b}", "price_cents": amt,
             "effect_slot": f"payer-{b}", "obligation_id": f"pay-{b}"}]


def main():
    out = Path(sys.argv[1])
    sources = QualifiedSources(ROOT / "runtime_sources", immutable=True)
    install(sources)
    results = []
    now = int(time.time())
    owner = Ed25519PrivateKey.generate()
    rt = {r: Ed25519PrivateKey.generate() for r in RUNTIME_ROLES}
    rt_pub = {r: base64.b64encode(k.public_key().public_bytes_raw()).decode() for r, k in rt.items()}
    owner_pub = base64.b64encode(owner.public_key().public_bytes_raw()).decode()

    def mission(cap=49500, payers=PAYERS[:5], contract="c" * 64):
        m = {"schema": S.MISSION_DOMAIN, "profile": "sandbox-demo-v1", "mission_id": f"LMF-007-TEST-{len(results)}",
             "contract_version": "1", "currency": "USD", "cap_cents": cap, "merchant_ids": [M], "actor": "agent:procurement-executor",
             "epoch": 1, "deadline": now + 3600, "obligation_ids": [f"pay-{b}" for b in payers],
             "payer_obligation_ids": [f"pay-{b}" for b in payers], "federation_contract_hash": contract, "runtime_public_keys": rt_pub}
        c = {"schema": S.CONTROL_DOMAIN, "sequence": 1, "mission_id": m["mission_id"], "mission_hash": digest(S.MISSION_DOMAIN, m),
             "epoch": 1, "active": True, "expires_at": now + 3600, "revoked_permits": [], "revoked_actors": []}
        return m, raw_sign(owner, S.MISSION_DOMAIN, m), raw_sign(owner, S.CONTROL_DOMAIN, c)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            def gw(m, c_env, name):
                adm = S.SandboxDemoAdmission(owner_pub, rt_pub, sources, observer=S.fresh_observer(rt, sources))
                prov = FakePayPal()
                g = Gateway(Path(tmp) / f"{name}.db", adm, prov)
                g.apply_control(m["mission_id"], c_env)
                return adm, g, prov

            def rec(name, expected, actual, posts, ok):
                results.append({"test": name, "expected": expected, "actual": actual, "provider_calls": posts, "pass": ok})
                print(("PASS " if ok else "FAIL ") + name, actual, posts, flush=True)

            # sixth capture beyond the $495/$500 envelope
            m, m_env, c_env = mission(cap=49500, payers=PAYERS)
            adm, g, prov = gw(m, c_env, "sixth")
            got = []
            for b in PAYERS:
                try:
                    p, _, _ = S.issue_permit(adm, m_env, rt, line(b), 9900, sources)
                    eid = g.reserve(m_env, p)
                    g.create(eid)
                    got.append("DISPATCHED")
                except Denied as e:
                    got.append(f"DENY:{e}")
            rec("sixth_99_payment_beyond_cap_blocked", "5 DISPATCHED then DENY BUDGET_EXCEEDED", got, len(prov.calls),
                got[:5] == ["DISPATCHED"] * 5 and got[5] == "DENY:BUDGET_EXCEEDED")
            calls_before = len(prov.calls)
            # duplicate effect id: same permit again -> idempotent lookup, no new POST
            p, _, _ = S.issue_permit(adm, m_env, rt, line("b1"), 9900, sources)
            try:
                g.reserve(m_env, p)
                actual = "RESERVED_AGAIN"
            except Denied as e:
                actual = f"DENY:{e}"
            rec("duplicate_effect_for_same_payer_no_new_post", "DENY or idempotent, 0 new POST", actual,
                len(prov.calls) - calls_before, len(prov.calls) == calls_before and actual != "RESERVED_AGAIN")

            # changed $99 amount (permit for 10000 against a 9900 line is a different action; cap per payer is the line)
            m, m_env, c_env = mission(cap=9900, payers=["b1"])
            adm, g, prov = gw(m, c_env, "amount")
            try:
                p, _, _ = S.issue_permit(adm, m_env, rt, line("b1", 10000), 10000, sources)
                g.create(g.reserve(m_env, p))
                actual = "DISPATCHED"
            except Denied as e:
                actual = f"DENY:{e}"
            rec("changed_amount_10000_over_payer_cap_blocked", "DENY", actual, len(prov.calls), actual.startswith("DENY") and not prov.calls)

            # wrong payer (not in the owner-signed mission)
            m, m_env, c_env = mission(payers=["b1"])
            adm, g, prov = gw(m, c_env, "payer")
            try:
                p, _, _ = S.issue_permit(adm, m_env, rt, line("b9"), 9900, sources)
                g.create(g.reserve(m_env, p))
                actual = "DISPATCHED"
            except Denied as e:
                actual = f"DENY:{e}"
            rec("wrong_payer_not_in_mission_blocked", "DENY INTENT_SCOPE_OR_BUDGET", actual, len(prov.calls),
                "INTENT_SCOPE_OR_BUDGET" in actual and not prov.calls)

            # stale contract: the owner revokes the mission control (e.g. the federation contract was superseded)
            m, m_env, c_env = mission(payers=["b1"])
            adm, g, prov = gw(m, c_env, "stale")
            p, _, _ = S.issue_permit(adm, m_env, rt, line("b1"), 9900, sources)
            ctl = {"schema": S.CONTROL_DOMAIN, "sequence": 2, "mission_id": m["mission_id"], "mission_hash": digest(S.MISSION_DOMAIN, m),
                   "epoch": 2, "active": True, "expires_at": now + 3600, "revoked_permits": [], "revoked_actors": []}
            g.apply_control(m["mission_id"], raw_sign(owner, S.CONTROL_DOMAIN, ctl))
            try:
                g.create(g.reserve(m_env, p))
                actual = "DISPATCHED"
            except Denied as e:
                actual = f"DENY:{e}"
            rec("stale_contract_epoch_advanced_blocked", "DENY STALE_EPOCH", actual, len(prov.calls), "STALE_EPOCH" in actual and not prov.calls)

            # missing buyer: permit for a payer whose line has no consent is never issued by lmf007.prepare (checked there);
            # here: mission without any payer lines cannot authorize a payer slot
            m, m_env, c_env = mission(payers=[])
            adm, g, prov = gw(m, c_env, "missing")
            try:
                p, _, _ = S.issue_permit(adm, m_env, rt, line("b1"), 9900, sources)
                g.create(g.reserve(m_env, p))
                actual = "DISPATCHED"
            except Denied as e:
                actual = f"DENY:{e}"
            rec("missing_buyer_consent_blocked", "DENY", actual, len(prov.calls), actual.startswith("DENY") and not prov.calls)
    finally:
        sources.close()
    s = {"tests": len(results), "passed": sum(r["pass"] for r in results),
         "forbidden_posts": sum(r["provider_calls"] for r in results if not r["test"].startswith("sixth")),
         "verdict": "PASS" if all(r["pass"] for r in results) else "FAIL",
         "not_covered_here": ["lost capture response / malicious return URL: covered by the unchanged A04 gateway test suite "
                              "(reconcile-only, callback state token) - see LEGION-Commerce tests/"]}
    out.write_text(json.dumps({"summary": s, "results": results}, indent=1))
    print(json.dumps(s))


if __name__ == "__main__":
    main()
