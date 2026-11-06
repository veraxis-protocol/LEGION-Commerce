"""WO-COM-LATENCY-004 WP2/WP4: gateway-level functional-equivalence differential, A vs B vs C vs BC.

Every case builds the SAME CONFORMANCE-ONLY state for every architecture (same keys, same
signed mission/permit/control/bundle) and runs the complete gate (Gateway.check, or the
reserve/create/capture lifecycle). Each gate is evaluated TWICE in a row (the second run
exercises the verification memo and the prepared context). The reference is A (existing
architecture, unchanged parent code). A case passes when every architecture's outcome equals
A's outcome exactly (timeouts compared by the same families as tools/bridge_differential.py),
and no category whose specification is a denial ever yields ALLOW.

Categories: the 28 frozen categories of tools/bridge_differential.py (same seed and mutations,
applied to the gateway path), plus gateway/authority categories: forged/mis-keyed permit and
mission, expired permit and acceptance, revocation and epoch transitions after a cached ALLOW,
deactivated control after ALLOW, trust-key rotation after ALLOW, bundle tampered/missing on
disk, non-canonical (float) evidence, changed intent amount re-signed.

Usage: python latency004/equivalence.py <out_dir> [per_category=40] [archs=A,B,C,BC]
"""
import base64
import copy
import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "tools"))
from arch import ARCHS, Denied, Fixture, base, build_gateway, digest, outcome, sources_for  # noqa: E402
from commerce_bridge.consolidated import ConsolidatedSession  # noqa: E402
from commerce_bridge.qualified import JsonProcess  # noqa: E402
import bridge_differential as bd  # noqa: E402  (frozen categories, seed, families)
from hoststate import host_state  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

SLEEPER = [sys.executable, "-c", "import time; time.sleep(30)"]
EXTRA = {
    "forged_permit_signature": "DENY", "permit_signed_by_wrong_key": "DENY", "forged_mission_signature": "DENY",
    "permit_expired": "DENY", "acceptance_expired": "DENY", "revocation_after_allow": "DENY",
    "actor_revoked_after_allow": "DENY", "epoch_transition_after_allow": "DENY", "control_deactivated_after_allow": "DENY",
    "trust_key_rotated_after_allow": "DENY", "bundle_tampered_on_disk": "DENY", "bundle_missing": "DENY",
    "noncanonical_float_evidence": "DENY", "intent_amount_resigned": "DENY",
}
ALLOW_CATEGORIES = {"valid", "reports_permuted", "duplicate_financial_effect"}


def mutate(f, c, rng):
    """bridge_differential.run_case mutations, verbatim, returning (intent, bundle)."""
    intent = bundle = None
    if c == "reports_permuted": f.set_reports([True, True, True]); rng.shuffle(f.observation["reports"])
    elif c == "missing_evidence": f.set_reports([] if rng.random() < .5 else [True])
    elif c == "contradictory_reports": v = [True, False, False]; rng.shuffle(v); f.set_reports(v)
    elif c == "equivocation":
        i = rng.randrange(3); other = copy.deepcopy(f.observation["reports"][i]); other["value"] = False
        f.observation["reports"].insert(rng.randrange(4), other)
    elif c == "sybil_shared_source":
        i, j = rng.sample(range(3), 2); f.policy["reporters"][j]["source_id"] = f.policy["reporters"][i]["source_id"]
    elif c == "unknown_state":
        if rng.random() < .5: f.observation["controller_state"]["pay"]["status"] = "UNKNOWN"
        else: f.observation["controller_state"]["pay"]["unknown"] = True
    elif c == "stale_observation": f.observation["observed_at"] = 1000 - 301 - rng.randrange(600)
    elif c == "stale_reports": f.set_reports([True, True, True], observed_at=1000 - 301 - rng.randrange(600))
    elif c == "forged_observation_envelope":
        bundle = f.bundle(); sig = bytearray(base64.b64decode(bundle["observation"]["signature"]))
        sig[rng.randrange(len(sig))] ^= 1 << rng.randrange(8)
        bundle["observation"]["signature"] = base64.b64encode(bytes(sig)).decode()
    elif c == "forged_report_signature":
        i = rng.randrange(3); s = bytearray.fromhex(f.observation["reports"][i]["sig"])
        s[rng.randrange(len(s))] ^= 1 << rng.randrange(8); f.observation["reports"][i]["sig"] = s.hex()
    elif c == "spoofed_report_root": f.observation["reports"][rng.randrange(3)]["root"] = "spoof-%d" % rng.randrange(1000)
    elif c == "authority_epoch_changed": f.control["epoch"] = 2 + rng.randrange(5)
    elif c == "authority_inactive": f.control["active"] = False
    elif c == "changed_intent_amount": intent = copy.deepcopy(f.intent); intent["amount_cents"] = 100 + 1 + rng.randrange(1000)
    elif c == "changed_intent_merchant": intent = copy.deepcopy(f.intent); intent["merchant_id"] = "OTHER-%d" % rng.randrange(1000)
    elif c == "changed_action_amount": f.observation["action"]["params"]["amount_cents"] = 100 + 1 + rng.randrange(1000)
    elif c == "budget_violation": f.observation["world_state"]["budget"]["available_cents"] = rng.randrange(100)
    elif c == "empty_obligations": f.mission["obligations"] = []
    elif c == "missing_prerequisite_record": f.records.pop(rng.randrange(len(f.records)))
    elif c == "core_permit_not_verified": f.observation["core_boundary"]["permit_verified"] = False
    elif c == "core_stale_permit_epoch": f.observation["core_boundary"]["permit"]["epoch"] = 0
    elif c == "core_replayed_nonce": b = f.observation["core_boundary"]; b["used_nonces"] = [b["permit"]["nonce"]]
    elif c == "core_fenced_command": b = f.observation["core_boundary"]; b["fenced"] = [b["permit"]["command_id"]]
    return intent, bundle


def norm(category, value):
    head, _, code = value.partition(":")
    for fam, test in bd.FAMILIES.items():
        if head == "DENY" and test(code.split(";")[0]) and fam.endswith(("FAILURE", "SIGNATURE")):
            return "FAMILY:" + fam + (";" + value.split(";", 1)[1] if ";" in value else "")
    return value


class Faults:
    """Inject decider/witness failures into one architecture's session; restore afterwards."""
    def __init__(self, arch, src, kind, rng):
        self.arch, self.src, self.kind = arch, src, kind
        self.dead = rng.random() < .5
        self.saved = None

    def __enter__(self):
        s = self.src.session
        if base(self.arch) in ("A", "C"):
            if self.kind == "decider":
                self.saved = ("lean", s.lean)
                if self.dead:
                    p = JsonProcess([self.src.decider]); p.close(); s.lean = p
                else:
                    s.lean = JsonProcess(SLEEPER, timeout=.2)
            else:
                self.saved = ("witness", s.witness)
                s.witness = bd.Broken("LEAN_UNAVAILABLE" if self.dead else "LEAN_TIMEOUT")
        else:
            self.saved = ("session", s)
            bad = str(Path(tempfile.gettempdir()) / "legion-no-such-binary.exe") if self.dead else SLEEPER
            kw = {"decider": bad} if self.kind == "decider" else {"zverdict": bad}
            self.src.session = ConsolidatedSession(self.src, child_timeout=.2, **kw)
        return self

    def __exit__(self, *a):
        name, old = self.saved
        s = self.src.session
        if name == "session":
            s.close(); self.src.session = old
        else:
            injected = getattr(s, name)
            if hasattr(injected, "close") and injected is not old:
                try: injected.close()
                except Exception: pass
            setattr(s, name, old)


def run_case(row, srcs, archs, tmp):
    c = row["category"]; rng = random.Random(row["param"])
    f = Fixture(f"{tmp}/fx", qualified_sources=srcs["A"])
    intent, bundle = mutate(f, c, rng)
    permit_mut = acc_mut = None
    if c == "permit_signed_by_wrong_key": pass
    if c == "acceptance_expired": acc_mut = lambda a: a.update(expires_at=f.now - 1 - rng.randrange(100))
    if c == "intent_amount_resigned":
        amount = 101 + rng.randrange(900)  # a copy: f.intent is also inside the signed mission
        permit_mut = lambda p: p.update(intent=dict(p["intent"], amount_cents=amount))
    gws = {}
    for a in archs:
        f.bridge.attempts.clear()
        g, provider, m, pe = build_gateway(f, f"{tmp}/{a}", a, srcs[a], permit_mutator=permit_mut, acceptance_mutator=acc_mut,
                                           bundle=copy.deepcopy(bundle) if bundle else None, intent=intent)
        if intent is not None and c in ("changed_intent_amount", "changed_intent_merchant"):
            pass
        gws[a] = [g, provider, m, pe]
    out = {}
    for a in archs:
        g, provider, m, pe = gws[a]
        adm = g.admission
        results = []
        if c == "forged_permit_signature":
            sig = bytearray(base64.b64decode(pe["signature"])); sig[rng.randrange(64)] ^= 1 << rng.randrange(8)
            pe = dict(pe, signature=base64.b64encode(bytes(sig)).decode())
        elif c == "permit_signed_by_wrong_key":
            pe = f.sign("control", "commerce.permit.v1", pe["payload"])
        elif c == "forged_mission_signature":
            m = dict(m, payload=dict(m["payload"], cap_cents=m["payload"]["cap_cents"] - 1 - rng.randrange(100)))
        elif c == "noncanonical_float_evidence":
            path = next(adm.bundle_dir.glob("*.json")); b = json.loads(path.read_text())
            b["observation"]["payload"]["world_state"]["budget"]["available_cents"] = 50000.5
            path.write_text(json.dumps(b))
        elif c == "bundle_tampered_on_disk":
            path = next(adm.bundle_dir.glob("*.json")); b = json.loads(path.read_text())
            b["observation"]["payload"]["observed_at"] -= 1 + rng.randrange(50)
            b["observation"] = f.sign("core", "commerce.core.observation.v1", b["observation"]["payload"])
            path.write_text(json.dumps(b))
        elif c == "bundle_missing":
            next(adm.bundle_dir.glob("*.json")).unlink()
        elif c == "permit_expired":
            f.now = 1400 + rng.randrange(300)
        check = lambda: outcome(lambda: _check(g, m, pe))
        if c in ("revocation_after_allow", "actor_revoked_after_allow", "epoch_transition_after_allow",
                 "control_deactivated_after_allow", "trust_key_rotated_after_allow"):
            results.append(check()); results.append(check())   # ALLOW twice: memo is warm
            ctl = copy.deepcopy(f.control); ctl["sequence"] = 2
            if c == "revocation_after_allow": ctl["revoked_permits"] = [pe["payload"]["permit_id"]]
            elif c == "actor_revoked_after_allow": ctl["revoked_actors"] = ["actor-1"]
            elif c == "epoch_transition_after_allow": ctl["epoch"] = 2 + rng.randrange(3)
            elif c == "control_deactivated_after_allow": ctl["active"] = False
            if c == "trust_key_rotated_after_allow":
                # (a private copy: the fixture trust dict is shared by every architecture's gateway)
                adm.trust = dict(adm.trust, admission=base64.b64encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw()).decode())
            else:
                results.append(outcome(lambda: g.apply_control(f.mission["mission_id"], f.sign("control", "commerce.control.v1", ctl))))
            results.append(check()); results.append(check())
        elif c == "revoked_after_reserve":
            eid = g.reserve(m, pe)
            ctl = copy.deepcopy(f.control); ctl["sequence"] = 2; ctl["revoked_permits"] = ["CONFORMANCE-PERMIT"]
            g.apply_control(f.mission["mission_id"], f.sign("control", "commerce.control.v1", ctl))
            try:
                g.create(eid); results.append("CREATED;PROVIDER_CALLS=%d" % len(provider.calls))
            except Denied as e:
                results.append("DENY:%s;PROVIDER_CALLS=%d" % (e, len(provider.calls)))
        elif c == "duplicate_financial_effect":
            ids = {g.reserve(m, pe) for _ in range(2 + rng.randrange(3))}
            eid = next(iter(ids)); g.create(eid); provider.approve()
            states = [g.capture(eid) for _ in range(2 + rng.randrange(3))]
            ok = states[0] == "CAPTURE_CONFIRMED"
            results.append("%s;CAPTURES=%d" % ("ONE_EFFECT" if len(ids) == 1 and ok else "EFFECTS=%d" % len(ids), provider.capture_count()))
        elif c in ("witness_timeout_or_unavailable", "decider_unavailable_or_timeout"):
            with Faults(a, srcs[a], "witness" if c.startswith("witness") else "decider", random.Random(row["param"])):
                results.append(check())
            results.append(check())  # recovery: the next request on the real session
        else:
            results.append(check()); results.append(check())
        f.now = 1000
        out[a] = results
        adm.close()
    return out


def _check(g, m, pe):
    with g.transaction() as db:
        g.check(db, m, pe)


def cases(per):
    rng = random.Random(bd.SEED); rows = []
    for category in list(bd.EXPECTED) + list(EXTRA):
        for _ in range(per):
            rows.append({"id": len(rows), "category": category, "param": rng.randrange(1 << 30)})
    return rows


def main():
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    per = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    archs = sys.argv[3].split(",") if len(sys.argv) > 3 else list(ARCHS)
    assert archs[0] == "A"
    host0 = host_state()
    srcs = {a: sources_for(a) for a in archs}
    rows = cases(per); results = []
    t0 = time.time()
    try:
        for row in rows:
            with tempfile.TemporaryDirectory() as tmp:
                r = run_case(row, srcs, archs, tmp)
            ref = [norm(row["category"], x) for x in r["A"]]
            mism = {a: r[a] for a in archs if [norm(row["category"], x) for x in r[a]] != ref}
            deny_spec = row["category"] not in ALLOW_CATEGORIES
            false_allow = {a: r[a] for a in archs if deny_spec and _allowed_after_mutation(row["category"], r[a])}
            results.append({**row, "outcomes": r, "equivalent": not mism, "mismatch": mism, "false_admission": false_allow})
            if mism or false_allow:
                print("CASE", row["id"], row["category"], r, flush=True)
    finally:
        for s in srcs.values(): s.close()
    by = {}
    for r in results:
        b = by.setdefault(r["category"], {"cases": 0, "equivalent": 0, "false_admissions": 0, "reference_outcomes": {}})
        b["cases"] += 1; b["equivalent"] += r["equivalent"]; b["false_admissions"] += bool(r["false_admission"])
        k = " | ".join(r["outcomes"]["A"]); b["reference_outcomes"][k] = b["reference_outcomes"].get(k, 0) + 1
    summary = {"cases": len(results), "gate_evaluations": sum(len(v) for r in results for v in r["outcomes"].values()),
               "archs": archs, "equivalent_cases": sum(r["equivalent"] for r in results),
               "verdict_mismatches": sum(not r["equivalent"] for r in results),
               "false_admissions": sum(bool(r["false_admission"]) for r in results), "categories": len(by), "by_category": by,
               "seconds": round(time.time() - t0, 1), "host": host0, "python": sys.version,
               "git_head": os.popen('git -C "%s" rev-parse HEAD' % HERE.parent).read().strip(), "scope": "CONFORMANCE_ONLY_TEST_KEYS_NO_NETWORK"}
    (out / "EQUIVALENCE_SUMMARY.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    with (out / "EQUIVALENCE_RESULTS.jsonl").open("w") as fh:
        for r in results: fh.write(json.dumps(r, sort_keys=True) + "\n")
    print(json.dumps({k: summary[k] for k in ("cases", "gate_evaluations", "equivalent_cases", "verdict_mismatches", "false_admissions", "categories", "seconds")}))


def _allowed_after_mutation(category, outcomes):
    """In a deny category, an ALLOW is legitimate only for the pre-mutation runs of *_after_allow
    cases (the first two evaluations happen before the revocation/rotation is applied) and for
    the recovery run after an injected fault (real session restored)."""
    if category.endswith("_after_allow"):
        return any(o == "ALLOW" for o in outcomes[3 if category != "trust_key_rotated_after_allow" else 2:])
    if category in ("witness_timeout_or_unavailable", "decider_unavailable_or_timeout"):
        return outcomes[0] == "ALLOW"
    if category == "revoked_after_reserve":
        return not outcomes[0].startswith("DENY:REVOKED_PERMIT;PROVIDER_CALLS=0")
    return any(o == "ALLOW" for o in outcomes)


if __name__ == "__main__":
    main()


