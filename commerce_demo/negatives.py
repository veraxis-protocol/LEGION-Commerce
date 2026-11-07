"""DEMO-006 WP3: sandbox-demo-v1 admission tests on the qualified BCh runtime with the UNCHANGED A04 Gateway
and a FakePayPal provider. TEST keys only (owner key is a test key here; the real Control Key is never used).

Positive control: Lean T -> reserve + create dispatch. Negatives: F verdict, Z (decider unavailable),
changed amount, revoked authorization, overspend, stale observation, expired permit, forged/unverifiable
permit, wrong payee, epoch change, inactive control -> ZERO provider calls each.

Usage: python -m commerce_demo.negatives <out.json>
"""
import base64
import copy
import json
import sys
import tempfile
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from commerce_adapter.contracts import Denied, digest
from commerce_adapter.gateway import Gateway
from commerce_authority.custody import sign as raw_sign
from commerce_bridge.consolidated import ConsolidatedSession, install
from commerce_bridge.qualified import QualifiedSources
from tests.helpers import FakePayPal
from . import sandbox_admission as S
from .mission import RUNTIME_ROLES

ROOT = Path(__file__).resolve().parent.parent
MERCHANT = "TESTMERCHANT1"
LINES = [{"service_id": "SVC-1", "supplier_id": "SUP-A1", "quote_id": "Q-SVC-1-SUP-A1-v1", "price_cents": 14500},
         {"service_id": "SVC-2", "supplier_id": "SUP-B1", "quote_id": "Q-SVC-2-SUP-B1-v1", "price_cents": 11900},
         {"service_id": "SVC-3", "supplier_id": "SUP-C2", "quote_id": "Q-SVC-3-SUP-C2-v1", "price_cents": 15500}]
TOTAL = 41900


def setup(now):
    owner = Ed25519PrivateKey.generate()
    rt = {r: Ed25519PrivateKey.generate() for r in RUNTIME_ROLES}
    rt_pub = {r: base64.b64encode(k.public_key().public_bytes_raw()).decode() for r, k in rt.items()}
    mission = {"schema": S.MISSION_DOMAIN, "profile": "sandbox-demo-v1", "mission_id": "DEMO-006-TEST", "contract_version": "1",
               "currency": "USD", "cap_cents": 50000, "merchant_ids": [MERCHANT], "actor": "agent:procurement-executor", "epoch": 1,
               "deadline": now + 3600, "obligation_ids": ["SVC-1", "SVC-2", "SVC-3"], "runtime_public_keys": rt_pub}
    control = {"schema": S.CONTROL_DOMAIN, "sequence": 1, "mission_id": mission["mission_id"], "mission_hash": digest(S.MISSION_DOMAIN, mission),
               "epoch": 1, "active": True, "expires_at": now + 3600, "revoked_permits": [], "revoked_actors": []}
    return owner, rt, rt_pub, mission, control


def main():
    out = Path(sys.argv[1]); results = []
    sources = QualifiedSources(ROOT / "runtime_sources", immutable=True); install(sources)
    try:
        def case(name, expect, mutate):
            now = int(time.time())
            owner, rt, rt_pub, mission, control = setup(now)
            ctx = {"now": now, "mission": mission, "control": control, "lines": copy.deepcopy(LINES), "total": TOTAL, "rt": rt}
            mutate("before", ctx)
            owner_pub = base64.b64encode(owner.public_key().public_bytes_raw()).decode()
            obs = ctx.get("observer") or S.fresh_observer(ctx["rt"], sources)   # per-gate re-observation (mission-1 fix)
            adm = S.SandboxDemoAdmission(owner_pub, rt_pub, sources, clock=lambda: ctx["now"], observer=obs)
            m_env = raw_sign(owner, S.MISSION_DOMAIN, ctx["mission"])
            provider = FakePayPal()
            with tempfile.TemporaryDirectory() as tmp:
                g = Gateway(Path(tmp) / "ledger.db", adm, provider)
                g.apply_control(mission["mission_id"], raw_sign(owner, S.CONTROL_DOMAIN, ctx["control"]))
                stage, actual = "issue", None
                try:
                    p_env, decision, _ = S.issue_permit(adm, m_env, ctx["rt"], ctx["lines"], ctx["total"], sources, now=ctx["now"])
                    ctx.update(p_env=p_env, owner=owner, g=g, m_env=m_env, mission_id=mission["mission_id"])
                    mutate("after_issue", ctx)
                    stage = "reserve"; eid = g.reserve(ctx["m_env"], ctx["p_env"])
                    mutate("after_reserve", ctx)
                    stage = "create"; g.create(eid)
                    actual = "DISPATCHED"
                except Denied as e:
                    actual = f"DENY@{stage}:{e}"
                except RuntimeError as e:
                    actual = f"ERROR@{stage}:{e}"
                calls = len(provider.calls)
            ok = (actual == "DISPATCHED" and calls >= 1) if expect == "DISPATCH" else (actual.startswith("DENY") and calls == 0 and expect in actual)
            results.append({"test": name, "expected": expect, "actual": actual, "provider_calls": calls, "pass": ok})
            print(("PASS " if ok else "FAIL ") + name, actual, "calls", calls, flush=True)

        nop = lambda stage, c: None
        case("positive_lean_T_dispatches", "DISPATCH", nop)

        def f_verdict(stage, c):
            if stage == "before": c["unknown"] = True
        def f_issue(stage, c):  # F: core_permit_not_verified -> qualified Lean verdict F
            pass
        # F verdict: Core permit premise false -> Lean F
        orig_build = S.build_core_inputs
        def build_F(*a, **k):
            w, r, i, o, p = orig_build(*a, **k); o["core_boundary"]["permit_verified"] = False; return w, r, i, o, p
        S.build_core_inputs = build_F
        case("lean_F_verdict_blocks", "QUALIFIED_LEAN_NOT_T", nop)
        S.build_core_inputs = orig_build
        # Z: decider unavailable
        real = sources.session
        sources.session = ConsolidatedSession(sources, decider=str(Path(tempfile.gettempdir()) / "no-decider.exe"), child_timeout=.3)
        case("lean_Z_decider_unavailable_blocks", "QUALIFIED_WORKER", nop)
        sources.session.close(); sources.session = real
        # unknown Core state
        def build_U(*a, **k):
            w, r, i, o, p = orig_build(*a, **k); o["controller_state"]["pay"]["unknown"] = True; return w, r, i, o, p
        S.build_core_inputs = build_U
        case("unknown_core_state_blocks", "CORE_", nop)
        S.build_core_inputs = orig_build

        def changed_amount(stage, c):
            if stage == "after_issue":
                p = json.loads(json.dumps(c["p_env"]["payload"])); p["intent"]["amount_cents"] = c["total"] - 1000
                c["p_env"] = raw_sign(c["rt"]["sandbox_admission"], S.PERMIT_DOMAIN, p)
        case("changed_amount_after_lean_blocks", "CORE_OBSERVATION_BINDING", changed_amount)

        def overspend(stage, c):
            if stage == "before":
                c["lines"][2] = {**c["lines"][2], "price_cents": 30000}; c["total"] = 14500 + 11900 + 30000
        case("overspend_56400_blocked_by_lean_budget", "CORE_TOOL_PRECONDITIONS", overspend)

        def overspend_forged(stage, c):
            if stage == "after_issue":
                p = json.loads(json.dumps(c["p_env"]["payload"])); p["intent"]["amount_cents"] = 60000
                c["p_env"] = raw_sign(c["rt"]["sandbox_admission"], S.PERMIT_DOMAIN, p)
        case("overspend_forged_permit_60000_blocked", "INVALID_CENTS", overspend_forged)

        def revoked(stage, c):
            if stage == "after_issue":
                ctl = dict(c["control"], sequence=2, revoked_permits=[c["p_env"]["payload"]["permit_id"]])
                c["g"].apply_control(c["mission_id"], raw_sign(c["owner"], S.CONTROL_DOMAIN, ctl))
        case("revoked_authorization_blocks", "REVOKED_PERMIT", revoked)

        def revoked_after_reserve(stage, c):
            if stage == "after_reserve":
                ctl = dict(c["control"], sequence=2, active=False)
                c["g"].apply_control(c["mission_id"], raw_sign(c["owner"], S.CONTROL_DOMAIN, ctl))
        case("authorization_withdrawn_after_reserve_blocks_dispatch", "CONTROL_INACTIVE", revoked_after_reserve)

        def epoch(stage, c):
            if stage == "after_issue":
                ctl = dict(c["control"], sequence=2, epoch=2)
                c["g"].apply_control(c["mission_id"], raw_sign(c["owner"], S.CONTROL_DOMAIN, ctl))
        case("epoch_change_blocks", "STALE_EPOCH", epoch)

        def stale(stage, c):
            if stage == "after_issue": c["now"] += S.MAX_AGE + 5
        # Since the mission-1 fix every gate RE-OBSERVES, so an old issue-time observation is replaced by a fresh one and the
        # action proceeds (within permit TTL). Staleness of the observation actually used is tested by stale_reobservation_blocks.
        case("old_issue_time_observation_replaced_by_fresh_reobservation", "DISPATCH", stale)

        def expired(stage, c):
            if stage == "after_issue":
                p = json.loads(json.dumps(c["p_env"]["payload"])); p["expires_at"] = c["now"] - 1
                c["p_env"] = raw_sign(c["rt"]["sandbox_admission"], S.PERMIT_DOMAIN, p)
        case("expired_permit_blocks", "EXPIRED_PERMIT", expired)

        def forged(stage, c):
            if stage == "after_issue":
                s = bytearray(base64.b64decode(c["p_env"]["signature"])); s[3] ^= 1
                c["p_env"] = dict(c["p_env"], signature=base64.b64encode(bytes(s)).decode())
        case("forged_permit_unverifiable_blocks", "INVALID_SIGNATURE", forged)

        def agent_self_signed(stage, c):  # an agent signs its own permit with a non-admission key
            if stage == "after_issue":
                c["p_env"] = raw_sign(c["rt"]["core_observer"], S.PERMIT_DOMAIN, c["p_env"]["payload"])
        case("agent_self_authorized_permit_blocks", "INVALID_SIGNATURE", agent_self_signed)

        def mission_not_owner(stage, c):
            if stage == "after_issue":
                c["m_env"] = raw_sign(c["rt"]["sandbox_admission"], S.MISSION_DOMAIN, c["mission"])
        case("mission_not_signed_by_owner_blocks", "INVALID_SIGNATURE", mission_not_owner)

        def wrong_payee(stage, c):
            if stage == "after_issue":
                p = json.loads(json.dumps(c["p_env"]["payload"])); p["intent"]["merchant_id"] = "ATTACKER0001"
                c["p_env"] = raw_sign(c["rt"]["sandbox_admission"], S.PERMIT_DOMAIN, p)
        case("wrong_payee_blocks", "INTENT_SCOPE_OR_BUDGET", wrong_payee)

        # --- per-gate re-observation (DEMO-006 mission-1 fix) ---
        def approval_delay_within_permit(stage, c):   # human takes 10 min to approve: still T on a FRESH observation
            if stage == "after_reserve": c["now"] += 600
        case("slow_buyer_approval_fresh_observation_dispatches", "DISPATCH", approval_delay_within_permit)

        def observer_down(stage, c):
            if stage == "before":
                def boom(m, p, now): raise S.Denied("CORE_OBSERVER_UNAVAILABLE")
                c["observer"] = boom
        case("core_observer_unavailable_blocks", "CORE_OBSERVER_UNAVAILABLE", observer_down)

        def observer_stale(stage, c):
            if stage == "before":
                fo = S.fresh_observer(c["rt"], sources)
                c["observer"] = lambda m, p, now: fo(m, p, now - S.MAX_AGE - 30)
        case("stale_reobservation_blocks", "STALE_CORE_OBSERVATION", observer_stale)

        def state_changed(stage, c):   # current state no longer matches the permit (e.g. amount changed upstream)
            if stage == "before":
                fo = S.fresh_observer(c["rt"], sources)
                def changed(m, p, now):
                    p2 = dict(p, intent=dict(p["intent"], amount_cents=p["intent"]["amount_cents"] + 100))
                    return fo(m, p2, now)
                c["observer"] = changed
        case("current_state_differs_from_permit_blocks", "CURRENT_STATE_DIFFERS_FROM_PERMIT", state_changed)

        # --- WO-LMF-007 payer-specific slots ---
        def payer_slot(stage, c):
            if stage == "before":
                c["mission"]["payer_obligation_ids"] = ["pay-b1"]
                c["control"]["mission_hash"] = digest(S.MISSION_DOMAIN, c["mission"])
                c["lines"] = [{"service_id": "LMF-B1", "supplier_id": "owner:f1", "quote_id": "contract-line-b1",
                               "price_cents": 9900, "effect_slot": "payer-b1", "obligation_id": "pay-b1"}]
                c["total"] = 9900
        case("payer_specific_slot_dispatches", "DISPATCH", payer_slot)

        def payer_slot_unlisted(stage, c):
            if stage == "before":
                c["mission"]["payer_obligation_ids"] = ["pay-b1"]
                c["control"]["mission_hash"] = digest(S.MISSION_DOMAIN, c["mission"])
                c["lines"] = [{"service_id": "LMF-B6", "supplier_id": "owner:f1", "quote_id": "contract-line-b6",
                               "price_cents": 9900, "effect_slot": "payer-b6", "obligation_id": "pay-b6"}]
                c["total"] = 9900
        case("payer_not_in_mission_blocks", "INTENT_SCOPE_OR_BUDGET", payer_slot_unlisted)
    finally:
        sources.close()
    summary = {"tests": len(results), "passed": sum(r["pass"] for r in results), "failed": [r["test"] for r in results if not r["pass"]],
               "unauthorized_dispatches": sum(r["provider_calls"] for r in results if r["expected"] != "DISPATCH"),
               "profile": "sandbox-demo-v1", "runtime": "QualifiedSources + ConsolidatedSession (qualified Core d9d38cd + Lean decider 99cda684)",
               "gateway": "A04 commerce_adapter.gateway.Gateway + FORMAL-001 Lean-attestation enforcement (MOCK_ONLY provider exempt)", "provider": "FakePayPal (no network)", "keys": "TEST keys only"}
    summary["verdict"] = "PASS" if not summary["failed"] else "FAIL"
    out.write_text(json.dumps({"summary": summary, "results": results}, indent=1) + "\n")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
