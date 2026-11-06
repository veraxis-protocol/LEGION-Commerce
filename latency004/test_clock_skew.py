"""WP4: speculative early dispatch under an ADVANCING clock (the equivalence harness uses a fixed clock).

The reports are observed at t=701 with max_age 300, so they are fresh iff the Core evaluation
instant <= 1001. The admission clock advances by STEP on every read and is started at a sweep of
offsets around 1001, so within one request the speculative dispatch instant (t0) and the instant
read by Bridge.assess (t1) straddle the boundary. Requirement for A and BC alike: the outcome must
be exactly the specification evaluated at the request's OWN assess instant:
    ALLOW  iff  t1 - 701 <= 300      else  DENY:CORE_NOT_EARNED_TRUE.
For BC we also record whether the speculative result was used; it may only be used when t0 and t1
classify every report identically.

Usage: python latency004/test_clock_skew.py <out.json>
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from arch import Fixture, build_gateway, outcome, sources_for  # noqa: E402
import commerce_bridge.prepared as prepared  # noqa: E402

STEP = 0.0001
USED = []
_take = prepared.PreparedAdmission._take_speculation


def take(self, observation, policy, now):
    t = _take(self, observation, policy, now)
    USED.append(t is not None)
    return t


prepared.PreparedAdmission._take_speculation = take


class Clock:
    def __init__(self, start): self.t = start; self.assess = []

    def __call__(self):
        self.t += STEP
        if sys._getframe(1).f_code.co_name == "assess": self.assess.append(self.t)
        return self.t


def main():
    out = Path(sys.argv[1]); rows = []
    under = sys.argv[2] if len(sys.argv) > 2 else "BCh"
    srcs = {"A": sources_for("A"), "BC": sources_for(under)}
    try:
        for k in range(-40, 41):
            start = 1001 + k * STEP / 2
            row = {"start": start}
            for a in ("A", "BC"):
                with tempfile.TemporaryDirectory() as tmp:
                    f = Fixture(tmp + "/fx", qualified_sources=srcs["A"])
                    f.set_reports([True, True, True], observed_at=701)
                    g, _, m, pe = build_gateway(f, tmp + "/g", a if a == "A" else under, srcs[a])
                    clock = Clock(start); g.admission.clock = clock
                    USED.clear()
                    def chk():
                        with g.transaction() as db: g.check(db, m, pe)
                    got = outcome(chk)
                    t1 = clock.assess[-1] if clock.assess else None
                    expected = "ALLOW" if t1 is not None and t1 - 701 <= 300 else "DENY:CORE_NOT_EARNED_TRUE"
                    row[a] = {"outcome": got, "assess_instant": t1, "expected": expected, "ok": got == expected,
                              "speculation_used": USED[-1] if (a == "BC" and USED) else None}
                    g.admission.close()
            rows.append(row)
    finally:
        for s in srcs.values(): s.close()
    res = {"cases": len(rows), "step_s": STEP, "arch_under_test": under,
           "A_ok": sum(r["A"]["ok"] for r in rows), "BC_ok": sum(r["BC"]["ok"] for r in rows),
           "BC_speculation_used": sum(bool(r["BC"]["speculation_used"]) for r in rows),
           "BC_speculation_discarded": sum(r["BC"]["speculation_used"] is False for r in rows),
           "allow_cases": sum(r["BC"]["outcome"] == "ALLOW" for r in rows), "rows": rows}
    res["pass"] = res["A_ok"] == res["BC_ok"] == len(rows) and res["BC_speculation_discarded"] > 0
    out.write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}, indent=1))


if __name__ == "__main__":
    main()

