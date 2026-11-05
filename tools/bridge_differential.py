"""WP2: >=1,000 frozen full-bridge cases on the qualified v3 profile.

Every case runs the complete Bridge.assess (signatures, current authority,
original intent, Core alpha, qualified Lean decision, zverdict witness, budget)
or the A04 Gateway (reserve/revoke/capture), with a fresh CONFORMANCE-ONLY
fixture. The expected outcome of every category is the specification below,
frozen to EXPECTED_SPEC.json before the first execution. A mismatch is any
actual outcome != expected outcome. No PayPal network, no operational keys.

Usage:
  python tools/bridge_differential.py run <out-dir>
  python tools/bridge_differential.py replay <out-dir>   # separate process; recomputes and compares
"""
import copy
import hashlib
import json
import random
import sys
import sys as _sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_sys.dont_write_bytecode = True
_sys.pycache_prefix = str(Path(tempfile.gettempdir()) / ("legion-nopyc-" + str(time.time_ns())))
sys.path.insert(0, str(ROOT))
from commerce_adapter.contracts import Denied
from commerce_bridge.qualified import QualifiedSources, JsonProcess
from commerce_bridge.runtime import CoreRuntime
from bridge_tests.fixtures import Fixture

SEED = 20261106
PER_CATEGORY = 40

# Specification: category -> expected outcome. ALLOW means an issuer request
# with Lean verdict T; DENY:<code> the exact fail-closed code.
EXPECTED = {
    "valid": "ALLOW",
    "reports_permuted": "ALLOW",
    "missing_evidence": "DENY:CORE_NOT_EARNED_TRUE",
    "contradictory_reports": "DENY:CORE_NOT_EARNED_TRUE",
    "equivocation": "DENY:REPORTER_EQUIVOCATION",
    "sybil_shared_source": "DENY:SHARED_SOURCE_FAKE_INDEPENDENCE",
    "unknown_state": "DENY:UNKNOWN_OR_MALFORMED_CORE_STATE",
    "stale_observation": "DENY:STALE_CORE_OBSERVATION",
    "stale_reports": "DENY:CORE_NOT_EARNED_TRUE",
    "forged_observation_envelope": "DENY:SIGNATURE",
    "forged_report_signature": "DENY:CORE_REPORT_SIGNATURE_OR_IDENTITY",
    "spoofed_report_root": "DENY:CORE_REPORT_SIGNATURE_OR_IDENTITY",
    "authority_epoch_changed": "DENY:CURRENT_AUTHORITY_DENIED",
    "authority_inactive": "DENY:CURRENT_AUTHORITY_DENIED",
    "changed_intent_amount": "DENY:ORIGINAL_INTENT_MISMATCH",
    "changed_intent_merchant": "DENY:ORIGINAL_INTENT_MISMATCH",
    "changed_action_amount": "DENY:CORE_ACTION_BINDING",
    "budget_violation": "DENY:CORE_NUMERICAL_BUDGET",
    "empty_obligations": "DENY:EMPTY_OR_DUPLICATE_OBLIGATIONS",
    "missing_prerequisite_record": "DENY:PREREQUISITE_RECORDS_MISMATCH",
    "core_permit_not_verified": "DENY:QUALIFIED_CORE_NOT_T",
    "core_stale_permit_epoch": "DENY:QUALIFIED_CORE_NOT_T",
    "core_replayed_nonce": "DENY:QUALIFIED_CORE_NOT_T",
    "core_fenced_command": "DENY:QUALIFIED_CORE_NOT_T",
    "witness_timeout_or_unavailable": "DENY:LEAN_WITNESS_FAILURE",
    "decider_unavailable_or_timeout": "DENY:QUALIFIED_DECIDER_FAILURE",
    "revoked_after_reserve": "DENY:REVOKED_PERMIT;PROVIDER_CALLS=0",
    "duplicate_financial_effect": "ONE_EFFECT;CAPTURES=1",
}
# Families: several exact codes are acceptable only where the spec names a family.
FAMILIES = {
    "DENY:SIGNATURE": lambda c: "SIGNATURE" in c or c in ("BAD_SIGNATURE", "INVALID_SIGNATURE"),
    "DENY:LEAN_WITNESS_FAILURE": lambda c: c in ("LEAN_TIMEOUT", "LEAN_UNAVAILABLE"),
    "DENY:QUALIFIED_DECIDER_FAILURE": lambda c: c in ("QUALIFIED_WORKER_UNAVAILABLE", "QUALIFIED_WORKER_TIMEOUT",
                                                      "QUALIFIED_WORKER_TIMEOUT_OR_PROTOCOL"),
}


def matches(expected, actual):
    if expected == actual:
        return True
    head, _, code = actual.partition(":")
    return head == "DENY" and expected in FAMILIES and FAMILIES[expected](code)


def cases():
    rng = random.Random(SEED)
    rows = []
    for category in EXPECTED:
        for k in range(PER_CATEGORY):
            rows.append({"id": len(rows), "category": category, "expected": EXPECTED[category],
                         "param": rng.randrange(1 << 30)})
    return rows


class Broken:
    def __init__(self, code): self.code = code
    def evaluate(self, *a): raise Denied(self.code)


def run_case(row, sources):
    rng = random.Random(row["param"])
    c = row["category"]
    with tempfile.TemporaryDirectory() as tmp:
        f = Fixture(tmp, qualified_sources=sources)
        intent = None
        bundle = None
        try:
            if c == "reports_permuted":
                f.set_reports([True, True, True]); rng.shuffle(f.observation["reports"])
            elif c == "missing_evidence":
                f.set_reports([] if rng.random() < .5 else [True])
            elif c == "contradictory_reports":
                v = [True, False, False]; rng.shuffle(v); f.set_reports(v)
            elif c == "equivocation":
                i = rng.randrange(3); other = copy.deepcopy(f.observation["reports"][i]); other["value"] = False
                f.observation["reports"].insert(rng.randrange(4), other)
            elif c == "sybil_shared_source":
                i, j = rng.sample(range(3), 2); f.policy["reporters"][j]["source_id"] = f.policy["reporters"][i]["source_id"]
            elif c == "unknown_state":
                if rng.random() < .5: f.observation["controller_state"]["pay"]["status"] = "UNKNOWN"
                else: f.observation["controller_state"]["pay"]["unknown"] = True
            elif c == "stale_observation":
                f.observation["observed_at"] = 1000 - 301 - rng.randrange(600)
            elif c == "stale_reports":
                f.set_reports([True, True, True], observed_at=1000 - 301 - rng.randrange(600))
            elif c == "forged_observation_envelope":
                bundle = f.bundle(); sig = bytearray(__import__("base64").b64decode(bundle["observation"]["signature"]))
                sig[rng.randrange(len(sig))] ^= 1 << rng.randrange(8)
                bundle["observation"]["signature"] = __import__("base64").b64encode(bytes(sig)).decode()
            elif c == "forged_report_signature":
                i = rng.randrange(3); s = bytearray.fromhex(f.observation["reports"][i]["sig"])
                s[rng.randrange(len(s))] ^= 1 << rng.randrange(8); f.observation["reports"][i]["sig"] = s.hex()
            elif c == "spoofed_report_root":
                f.observation["reports"][rng.randrange(3)]["root"] = "spoof-%d" % rng.randrange(1000)
            elif c == "authority_epoch_changed":
                f.control["epoch"] = 2 + rng.randrange(5)
            elif c == "authority_inactive":
                f.control["active"] = False
            elif c == "changed_intent_amount":
                intent = copy.deepcopy(f.intent); intent["amount_cents"] = 100 + 1 + rng.randrange(1000)
            elif c == "changed_intent_merchant":
                intent = copy.deepcopy(f.intent); intent["merchant_id"] = "OTHER-%d" % rng.randrange(1000)
            elif c == "changed_action_amount":
                f.observation["action"]["params"]["amount_cents"] = 100 + 1 + rng.randrange(1000)
            elif c == "budget_violation":
                f.observation["world_state"]["budget"]["available_cents"] = rng.randrange(100)
            elif c == "empty_obligations":
                f.mission["obligations"] = []
            elif c == "missing_prerequisite_record":
                f.records.pop(rng.randrange(len(f.records)))
            elif c == "core_permit_not_verified":
                f.observation["core_boundary"]["permit_verified"] = False
            elif c == "core_stale_permit_epoch":
                f.observation["core_boundary"]["permit"]["epoch"] = 0
            elif c == "core_replayed_nonce":
                b = f.observation["core_boundary"]; b["used_nonces"] = [b["permit"]["nonce"]]
            elif c == "core_fenced_command":
                b = f.observation["core_boundary"]; b["fenced"] = [b["permit"]["command_id"]]
            elif c == "witness_timeout_or_unavailable":
                f.bridge.runtime = CoreRuntime(f.sources, Broken(rng.choice(["LEAN_TIMEOUT", "LEAN_UNAVAILABLE"])))
            elif c == "decider_unavailable_or_timeout":
                session = sources.session; real = session.lean
                if rng.random() < .5:
                    dead = JsonProcess([sources.decider]); dead.close(); session.lean = dead
                else:
                    session.lean = JsonProcess([sys.executable, "-c", "import time; time.sleep(30)"], timeout=.2)
                try:
                    f.assess(intent=intent); return "ALLOW"
                except Denied as e:
                    return "DENY:" + str(e)
                finally:
                    if session.lean is not real:
                        try: session.lean.close()
                        except Exception: pass
                    session.lean = real
            elif c == "revoked_after_reserve":
                gateway, provider, mission, permit = f.gateway()
                eid = gateway.reserve(mission, permit)
                f.control["sequence"] = 2; f.control["revoked_permits"] = ["CONFORMANCE-PERMIT"]
                gateway.apply_control(f.mission["mission_id"], f.sign("control", "commerce.control.v1", f.control))
                try:
                    gateway.create(eid); return "CREATED;PROVIDER_CALLS=%d" % len(provider.calls)
                except Denied as e:
                    return "DENY:%s;PROVIDER_CALLS=%d" % (e, len(provider.calls))
            elif c == "duplicate_financial_effect":
                gateway, provider, mission, permit = f.gateway()
                ids = {gateway.reserve(mission, permit) for _ in range(2 + rng.randrange(3))}
                eid = next(iter(ids)); gateway.create(eid); provider.approve()
                states = [gateway.capture(eid) for _ in range(2 + rng.randrange(3))]
                ok = states[0] == "CAPTURE_CONFIRMED"
                return "%s;CAPTURES=%d" % ("ONE_EFFECT" if len(ids) == 1 and ok else "EFFECTS=%d" % len(ids), provider.capture_count())
            result = f.assess(bundle=bundle, intent=intent)
            verdict = result["core"]["qualified"]["lean"]["verdict"]
            return "ALLOW" if result["decision"] == "ALLOW_ISSUER_REQUEST_ONLY" and verdict == "T" else "UNEXPECTED_ALLOW_SHAPE:" + verdict
        except Denied as e:
            return "DENY:" + str(e)


def execute(rows):
    sources = QualifiedSources(ROOT / "runtime_sources", immutable=True)
    try:
        out = []
        for row in rows:
            t = time.perf_counter_ns()
            actual = run_case(row, sources)
            out.append({"id": row["id"], "category": row["category"], "expected": row["expected"], "actual": actual,
                        "match": matches(row["expected"], actual),
                        "false_admission": row["expected"] != "ALLOW" and actual == "ALLOW",
                        "ms": round((time.perf_counter_ns() - t) / 1e6, 3)})
        return out, sources.profile_hash
    finally:
        sources.close()


def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def main():
    mode, out = sys.argv[1], Path(sys.argv[2])
    if mode == "run":
        out.mkdir(parents=True, exist_ok=False)
        rows = cases()
        write(out / "EXPECTED_SPEC.json", {"seed": SEED, "per_category": PER_CATEGORY, "expected": EXPECTED,
                                          "families": {k: "see tools/bridge_differential.py FAMILIES" for k in FAMILIES}})
        (out / "CASES.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8", newline="\n")
        results, profile = execute(rows)
        (out / "RESULTS.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in results), encoding="utf-8", newline="\n")
        by = {}
        for r in results:
            b = by.setdefault(r["category"], {"cases": 0, "matched": 0, "actual": {}})
            b["cases"] += 1; b["matched"] += r["match"]; b["actual"][r["actual"]] = b["actual"].get(r["actual"], 0) + 1
        summary = {"cases": len(results), "matched": sum(r["match"] for r in results),
                   "mismatches": sum(not r["match"] for r in results),
                   "false_admissions": sum(r["false_admission"] for r in results),
                   "categories": len(by), "by_category": by, "profile_sha256": profile,
                   "python": sys.version, "scope": "CONFORMANCE_ONLY_TEST_KEYS_NO_NETWORK"}
        write(out / "SUMMARY.json", summary)
        print(json.dumps({k: summary[k] for k in ("cases", "matched", "mismatches", "false_admissions", "categories")}))
    else:
        rows = [json.loads(l) for l in (out / "CASES.jsonl").read_text().splitlines()]
        assert rows == cases(), "frozen case definitions differ from generator"
        recorded = [json.loads(l) for l in (out / "RESULTS.jsonl").read_text().splitlines()]
        results, _ = execute(rows)
        def norm(r):  # family-level outcome (timeout code races are one spec outcome)
            return r["expected"] if r["match"] else r["actual"]
        diffs = [r["id"] for r, s in zip(results, recorded) if norm(r) != norm(s)]
        rep = {"replayed": len(results), "outcome_differences": len(diffs), "first": diffs[:20],
               "verdict": "REPLAY_MATCH" if not diffs else "REPLAY_MISMATCH", "python": sys.version}
        write(out / "INDEPENDENT_REPLAY.json", rep)
        print(json.dumps(rep))


if __name__ == "__main__":
    main()
