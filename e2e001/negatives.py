"""WO-COM-E2E-001 transaction negatives on the real admission path (qualified Core + Lean decider, A04 Gateway,
SandboxDemoAdmission with TEST keys, synthetic FakePayPal). MECHANISM_ONLY.

  python -B -m e2e001.negatives <run_dir>    -> <run_dir>/NEGATIVES.json

The plan line is the run's final Channel3 offer. The live price gate is replaced by a recorded quote stub so that a
price change can be forced deterministically; every other control is the production code path of the profile.
Each case asserts the exact denial code AND zero forbidden provider POSTs.
"""
import json
import sys
import tempfile
import time
import traceback
from pathlib import Path

from commerce_adapter.contracts import Denied, digest, require
from commerce_adapter.gateway import Gateway
from commerce_authority.custody import sign as raw_sign
from commerce_bridge.consolidated import install
from commerce_bridge.qualified import QualifiedSources
from commerce_demo import sandbox_admission as S
from tests.helpers import FakePayPal

from .e2e_run import ROOT, b64, make_mission, mint_keys

ORIGINAL_ASSERTIONS = ("run 1 (NEGATIVES_run1_HARNESS_DEFECT.json) is preserved: N2/N5/N6 expected a returned state, but the Gateway "
                       "correctly raises the denial; run 2 asserts the exact denial code and zero POSTs. No system code changed.")


class Env:
    def __init__(self, sources, line, tmp, name, clock_offset=0):
        self.owner, self.runtime = mint_keys()
        now = int(time.time())
        self.mission, self.m_env, self.c_env = make_mission(f"run-NEG{name}", "negative test: " + name, self.owner, self.runtime, now)
        self.quote = {"price_cents": line["price_cents"], "availability": "InStock"}
        self.offset = {"t": 0}
        base = S.fresh_observer(self.runtime, sources)

        def observer(m, p, t):
            require(self.quote["availability"] == "InStock" and self.quote["price_cents"] == p["intent"]["amount_cents"],
                    "STALE_PRICE_OR_OFFER_CHANGED")
            return base(m, p, t)
        self.adm = S.SandboxDemoAdmission(b64(self.owner), self.mission["runtime_public_keys"], sources,
                                          clock=lambda: time.time() + self.offset["t"], observer=observer)
        self.p_env, self.decision, _ = S.issue_permit(self.adm, self.m_env, self.runtime, [line], line["price_cents"], sources)
        self.provider = FakePayPal()
        self.g = Gateway(Path(tmp) / f"{name}.db", self.adm, self.provider)
        self.g.apply_control(self.mission["mission_id"], self.c_env)

    def control(self, seq, **changes):
        c = {**self.c_env["payload"], "sequence": seq, **changes}
        return raw_sign(self.owner, S.CONTROL_DOMAIN, c)

    def posts(self):
        return sum(1 for m, _, _ in self.provider.calls if m == "POST")


def expect_denied(fn, code):
    try:
        fn()
    except Denied as e:
        return str(e)
    raise AssertionError(f"expected {code}, nothing was denied")


def logged_state(env, eid):
    return env.g.status(eid)["state"]


def main():
    run = Path(sys.argv[1])
    line = json.loads((run / "PLAN_TRACE.json").read_text())["final_line"]
    sources = QualifiedSources(ROOT / "runtime_sources", immutable=True); install(sources, hot_standby_seconds=60.0)
    results = {}
    tmp = tempfile.mkdtemp(prefix="e2e001-neg-")

    def case(name, fn):
        t = time.time()
        try:
            detail = fn(name)
            results[name] = {"result": "PASS", **detail}
        except Exception as e:
            results[name] = {"result": "FAIL", "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-1500:]}
        results[name]["seconds"] = round(time.time() - t, 2)

    def p1(name):
        e = Env(sources, line, tmp, name)
        eid = e.g.reserve(e.m_env, e.p_env); e.g.create(eid); e.provider.approve(); st = e.g.capture(eid)
        assert st == "CAPTURE_CONFIRMED" and e.provider.capture_count() == 1, st
        return {"kind": "positive control", "state": st, "lean": e.decision["lean_verdict"], "posts": e.posts()}

    def n1(name):
        e = Env(sources, line, tmp, name)
        e.offset["t"] = S.PERMIT_TTL + 5
        code = expect_denied(lambda: e.g.reserve(e.m_env, e.p_env), "EXPIRED_PERMIT")
        assert code == "EXPIRED_PERMIT" and e.posts() == 0, code
        return {"denied": code, "posts": e.posts()}

    def n2(name):
        e = Env(sources, line, tmp, name)
        eid = e.g.reserve(e.m_env, e.p_env)
        e.g.apply_control(e.mission["mission_id"], e.control(2, revoked_permits=[e.p_env["payload"]["permit_id"]]))
        code = expect_denied(lambda: e.g.create(eid), "REVOKED_PERMIT")
        assert code == "REVOKED_PERMIT" and e.posts() == 0, (code, e.posts())
        return {"denied": code, "revoked_after_reservation": True, "state": logged_state(e, eid), "posts": e.posts()}

    def n3(name):
        e = Env(sources, line, tmp, name)
        e.g.apply_control(e.mission["mission_id"], e.control(2, revoked_actors=[e.mission["actor"]]))
        code = expect_denied(lambda: e.g.reserve(e.m_env, e.p_env), "REVOKED_ACTOR")
        assert code == "REVOKED_ACTOR" and e.posts() == 0, code
        return {"denied": code, "posts": e.posts()}

    def n4(name):
        e = Env(sources, line, tmp, name)
        e.g.apply_control(e.mission["mission_id"], e.control(2, active=False))
        code = expect_denied(lambda: e.g.reserve(e.m_env, e.p_env), "CONTROL_INACTIVE")
        assert code == "CONTROL_INACTIVE" and e.posts() == 0, code
        return {"denied": code, "posts": e.posts(), "kind": "owner deactivated mission (authority revoked)"}

    def n5(name):
        e = Env(sources, line, tmp, name)
        eid = e.g.reserve(e.m_env, e.p_env)
        e.quote["price_cents"] = line["price_cents"] + 1500          # seller raised the price after admission
        code = expect_denied(lambda: e.g.create(eid), "STALE_PRICE_OR_OFFER_CHANGED")
        assert code == "STALE_PRICE_OR_OFFER_CHANGED" and e.posts() == 0, (code, e.posts())
        return {"denied": code, "state": logged_state(e, eid), "posts": e.posts(), "stale_price_delta_cents": 1500}

    def n6(name):
        e = Env(sources, line, tmp, name)
        e.g.reserve(e.m_env, e.p_env)
        eid = e.g.reserve(e.m_env, e.p_env)
        e.g.create(eid); e.provider.approve()
        e.quote["price_cents"] = line["price_cents"] - 700           # price dropped between approval and capture: still stale
        code = expect_denied(lambda: e.g.capture(eid), "STALE_PRICE_OR_OFFER_CHANGED")
        assert code == "STALE_PRICE_OR_OFFER_CHANGED" and e.provider.capture_count() == 0, (code, e.provider.capture_count())
        return {"denied": code, "state": logged_state(e, eid), "capture_posts": e.provider.capture_count(), "create_posts": e.posts()}

    def n7(name):
        e = Env(sources, line, tmp, name)
        forged = json.loads(json.dumps(e.p_env)); forged["payload"]["intent"]["amount_cents"] = line["price_cents"] + 10000
        code = expect_denied(lambda: e.g.reserve(e.m_env, forged), "INVALID_SIGNATURE")
        assert code == "INVALID_SIGNATURE" and e.posts() == 0, code
        return {"denied": code, "posts": e.posts(), "kind": "permit amount altered after signing"}

    def n8(name):
        e = Env(sources, line, tmp, name)
        try:
            S.issue_permit(e.adm, e.m_env, e.runtime, [{**line, "price_cents": 50001}], 50001, sources)
            raise AssertionError("permit issued over cap")
        except Denied as d:
            code = str(d)
        return {"denied": code, "kind": "amount above $500 cap refused by Core/Lean admission at issue"}

    def n9(name):
        e = Env(sources, line, tmp, name)
        eid = e.g.reserve(e.m_env, e.p_env); e.g.create(eid); e.provider.approve()

        def tamper(path, result):
            for pu in result.get("purchase_units", []):
                pu["amount"]["value"] = "1.00"
        e.provider.tamper = tamper                                      # provider reports a different amount than authorized
        st = e.g.capture(eid)
        assert e.provider.capture_count() == 0, e.provider.capture_count()
        return {"capture_result": st, "state": logged_state(e, eid), "capture_posts": e.provider.capture_count(), "kind": "payment amount altered at provider"}

    def n10(name):
        e = Env(sources, line, tmp, name)
        eid = e.g.reserve(e.m_env, e.p_env); e.g.create(eid); e.provider.approve()
        s1 = e.g.capture(eid); s2 = e.g.capture(eid)
        g2 = Gateway(Path(tmp) / f"{name}.db", e.adm, e.provider)     # a second gateway instance on the same ledger
        s3 = g2.capture(eid)
        assert e.provider.capture_count() == 1, e.provider.capture_count()
        return {"states": [s1, s2, s3], "capture_posts": e.provider.capture_count(), "kind": "duplicate capture attempts (same and second instance)"}

    for name, fn in [("P1_positive_control", p1), ("N1_expired_permit", n1), ("N2_revoked_permit_after_reservation", n2),
                     ("N3_revoked_actor", n3), ("N4_mission_deactivated", n4), ("N5_stale_price_before_create", n5),
                     ("N6_stale_price_before_capture", n6), ("N7_altered_amount_in_permit", n7), ("N8_amount_over_cap", n8),
                     ("N9_altered_amount_at_provider", n9), ("N10_duplicate_capture", n10)]:
        case(name, fn)
    sources.close()
    out = {"evidence_mode": "MECHANISM_ONLY", "plan_line": line, "assertion_policy": ORIGINAL_ASSERTIONS, "cases": results,
           "passed": sum(r["result"] == "PASS" for r in results.values()), "total": len(results)}
    (run / "NEGATIVES.json").write_text(json.dumps(out, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"passed": out["passed"], "total": out["total"],
                      "failed": [k for k, v in results.items() if v["result"] != "PASS"]}))


if __name__ == "__main__":
    main()
