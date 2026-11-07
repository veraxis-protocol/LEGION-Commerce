"""WO-COM-AUTHORITY-005 WP5: adversarial verification of the signing infrastructure on the qualified BCh runtime.

Role keys are created through the REAL custody code (commerce_authority.custody.Store: passphrase +
DPAPI CurrentUser + ACL) in a temporary TEST store, enrolled with environment TEST-AUTHORITY-005,
and used through the REAL role-restricted signer. Core/CAGE/VEIP/reporter keys are CONFORMANCE-ONLY
test sources (no operational evidence is produced or claimed). A complete A04 install package is
built and validated with commerce_bridge.installation.validate_package (BCh: QualifiedSources +
consolidated worker + PreparedAdmission). Every attack must DENY or be REFUSED; positive controls
must PASS. No network; FakePayPal only.

Usage: python authority_tests/run_adversarial.py <out_dir>
"""
import base64
import copy
import functools
import hashlib
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
from commerce_adapter.contracts import Denied, digest  # noqa: E402
from commerce_adapter.gateway import Gateway  # noqa: E402
from commerce_authority import authority as A  # noqa: E402
from commerce_authority.custody import Store, sign as raw_sign  # noqa: E402
from commerce_authority.roles import ROLES  # noqa: E402
from commerce_bridge.consolidated import install as consolidated  # noqa: E402
from commerce_bridge.installation import read, validate_package  # noqa: E402
from commerce_bridge.prepared import PreparedAdmission  # noqa: E402
from commerce_bridge.qualified import QualifiedSources  # noqa: E402
from commerce_bridge.sources import adapter_hashes, bridge_hashes  # noqa: E402
from bridge_tests.fixtures import Fixture  # noqa: E402
from tests.helpers import FakePayPal  # noqa: E402

ENV = "TEST-AUTHORITY-005"
PW = {"control": "test-control-passphrase-005", "admission": "test-admission-passphrase-005",
      "acceptance": "test-acceptance-passphrase-005"}
IDENT = {"control": {"sid": "S-1-5-21-1-1-1-1001", "account": "TEST\\operator", "host": "TEST-HOST-1"},
         "admission": {"sid": "S-1-5-21-2-2-2-1002", "account": "TEST\\issuer", "host": "TEST-HOST-2"},
         "acceptance": {"sid": "S-1-5-21-3-3-3-1003", "account": "TEST\\reviewer", "host": "TEST-HOST-3"}}
BCH = functools.partial(PreparedAdmission, overlap=True)
RESULTS = []


def provision(store_root):
    store = Store(store_root)
    enroll = {}
    for role in ROLES:
        kid = store.generate(role, f"TEST {role} custodian", PW[role], IDENT[role])
        enroll[role] = A.enroll(store, role, kid, PW[role], environment=ENV)
    return store, enroll


class AuthorityFixture(Fixture):
    """Fixture whose control/admission/acceptance signatures come from the role-restricted signer."""
    def __init__(self, root, sources, store):
        self.store = store
        super().__init__(root, qualified_sources=sources)
        for role in ROLES:
            self.public[role] = store.active(role)["public_key"]
        self.trust.update({role: self.public[role] for role in ROLES})   # same dict object used by the bridge

    def sign(self, role, domain, payload):
        if role in ROLES:
            return A.role_sign(self.store, role, domain, copy.deepcopy(payload), PW[role], allow_dedicated=True)
        return super().sign(role, domain, payload)


def build_package(f, root):
    """Exactly the operator package layout of docs/BRIDGE_OPERATOR_PROCEDURE.md section 5."""
    root = Path(root); root.mkdir(parents=True)
    bundle = f.bundle(); assessment = f.assess(bundle)
    ev = root / "a03-evidence"; ev.mkdir()
    required = ["PROOF_MAP.json", "LEAN_BUILD.txt", "AXIOM_AUDIT.txt", "FORMAL_INPUTS.jsonl", "LEAN_VERDICTS.jsonl",
                "RUNTIME_VERDICTS.jsonl", "MISMATCHES.jsonl", "PERMIT_TRACE.jsonl", "NEGATIVE_RESULTS.json"]
    for n in required: (ev / n).write_text("CONFORMANCE_ONLY; AUTHORITY-005 TEST PACKAGE")
    pins = json.loads((ROOT / "design/SOURCE_PINS.json").read_text())
    for order, tdd in (("A01", "eac9c274f62ba0ca7c7b4c1b109fb5e69ecbc53a09d29efb96cc98af3378551d"),
                       ("A02", "6ceacae3da2e6acb4bc010db67a0cfc03ac50c6236a77c9f8e66f9eacc93782f")):
        rn = f"{order}_RESULT.json"; (ev / rn).write_text("CONFORMANCE_ONLY")
        up = {"schema": "commerce.upstream.acceptance.v1", "work_order": order, "status": "PASS", "tdd_sha256": tdd,
              "source_pins": f.sources.policy_pins, "mission_hash": f.mh, "expires_at": f.now + 800,
              "evidence_hashes": {rn: hashlib.sha256((ev / rn).read_bytes()).hexdigest()}}
        an = f"UPSTREAM_{order}_ACCEPTANCE.json"
        (ev / an).write_text(json.dumps(f.sign("acceptance", "commerce.upstream.acceptance.v1", up))); required += [rn, an]
    acc = {"schema": "commerce.a03.acceptance.v1", "status": "PASS", "tdd_sha256": pins["a03_tdd_sha256"], "source_pins": f.sources.policy_pins,
           "expires_at": f.now + 800, "tests": {f"A03-T{i:02}": "PASS" for i in range(1, 14)}, "adapter_schema": "commerce.a04.adapter.v1",
           "runtime_source_hashes": adapter_hashes(), "bridge_source_hashes": bridge_hashes(),
           "native_source_manifest_hash": digest("commerce.bridge.sources.v1", f.sources.verify()),
           "frozen_interface_sha256": hashlib.sha256((ROOT / "docs/ADMISSION_INTERFACE.md").read_bytes()).hexdigest(),
           "bridge_policy_hash": digest("commerce.bridge.policy.v1", f.policy), "upstream_acceptance": {"A01": "PASS", "A02": "PASS"},
           "evidence_hashes": {n: hashlib.sha256((ev / n).read_bytes()).hexdigest() for n in required}, "fixture": "CONFORMANCE_ONLY"}
    accept = f.sign("acceptance", "commerce.a03.acceptance.v1", acc)
    permit = {"schema": "commerce.permit.v1", "permit_id": "AUTH005-PERMIT", "issued_at": f.now - 100, "expires_at": f.now + 400,
              "quote_expires_at": f.now + 500, "mission_hash": f.mh, "acceptance_hash": digest("commerce.a03.acceptance.v1", acc), "epoch": 1,
              "intent": f.intent, "scope": "paypal.sandbox.orders.create_capture", "verdict": "T", "lean_rule_earned": True, "controller_act": True,
              "cage_allow": True, "veip_eligible": True, "prerequisites_satisfied": True,
              "bridge_evidence_hash": digest("commerce.bridge.bundle.v1", bundle),
              "bridge_assessment_hash": digest("commerce.bridge.assessment.v1", assessment), "core_observation_hash": assessment["observation_hash"]}
    files = {"trust.json": json.dumps(f.trust, indent=2, sort_keys=True) + "\n",
             "a03-acceptance.json": json.dumps(accept),
             "current-mission.json": json.dumps(f.sign("control", "commerce.mission.v1", f.mission)),
             "current-permit.json": json.dumps(f.sign("admission", "commerce.permit.v1", permit)),
             "current-control.json": json.dumps(f.sign("control", "commerce.control.v1", f.control)),
             "bridge-policy.json": json.dumps(bundle["policy"])}
    for n, t in files.items(): (root / n).write_text(t, encoding="utf-8", newline="\n")
    be = root / "bridge-evidence"; be.mkdir()
    (be / (digest("commerce.permit.locator.v1", permit["permit_id"]) + ".json")).write_text(json.dumps(bundle))
    seal_manifest(f, root)
    return root, permit, bundle, assessment, acc


def seal_manifest(f, root, signer="acceptance"):
    files = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*")
             if p.is_file() and p.name not in ("installation-manifest.json", "ledger.db", "ledger.db-wal", "ledger.db-shm")}
    m = {"schema": "commerce.bridge.installation.v1", "status": "ACCEPTED", "mission_hash": f.mh, "files": files}
    env = f.sign(signer, "commerce.bridge.installation.v1", m) if signer in ROLES and signer == "acceptance" else signer(m)
    (root / "installation-manifest.json").write_text(json.dumps(env))


def trust_file(root, trust):
    p = Path(root) / "operator-trust.json"
    p.write_text(json.dumps(trust, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return p, hashlib.sha256(p.read_bytes()).hexdigest()


def outcome(fn):
    try:
        fn(); return "PASS"
    except Denied as e:
        return "DENY:" + str(e)
    except Exception as e:  # fail closed, recorded by type
        return "ERROR:" + type(e).__name__


def record(name, actual, expect, category):
    # FAILCLOSED: any denial or non-Denied failure is acceptable; only an admission (PASS) fails the test.
    ok = (actual != "PASS") if expect == "FAILCLOSED" else (actual.startswith(expect) if expect != "PASS" else actual == "PASS")
    RESULTS.append({"test": name, "category": category, "expected": expect, "actual": actual, "pass": ok})
    print(("PASS " if ok else "FAIL ") + name, actual, flush=True)


def validate(pkg, sources, tp, ts, clock):
    return validate_package(pkg, sources, tp, ts, clock, BCH)


def main():
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="auth005-"))
    sources = QualifiedSources(ROOT / "runtime_sources", immutable=True); consolidated(sources)
    t0 = time.time()
    try:
        store, enroll = provision(work / "store")
        # ---------------- infrastructure: custody, enrollment, trust, separation
        record("store_files_are_dpapi_sealed_not_plain", "PASS" if all(b"PRIVATE" not in p.read_bytes() and not p.read_bytes().startswith(b"{")
               for p in (work / "store").rglob("*.key.dpapi")) else "FAIL", "PASS", "custody")
        record("wrong_passphrase_refused", outcome(lambda: store.unseal("control", store.active("control")["key_id"], "wrong-passphrase-xx")),
               "DENY:WRONG_PASSPHRASE_OR_TAMPERED_KEY", "custody")
        record("cli_requires_interactive_console", outcome(lambda: __import__("commerce_authority.custody", fromlist=["x"]).interactive_passphrase("p")),
               "DENY:INTERACTIVE_CUSTODIAN_CONSOLE_REQUIRED", "custody")
        record("private_key_location_dropbox_refused", outcome(lambda: Store(Path.home() / "Dropbox" / "x").generate("control", "x", PW["control"], IDENT["control"])),
               "DENY:PRIVATE_KEY_LOCATION_FORBIDDEN", "custody")
        record("cross_role_key_use_refused", outcome(lambda: store.unseal("admission", store.active("control")["key_id"], PW["control"])),
               "DENY:CROSS_ROLE_KEY_USE", "custody")
        for role in ROLES:
            record(f"selftest_{role}", outcome(lambda r=role: A.selftest(store, r, PW[r])), "PASS", "signing")
        data, report = A.build_trust(enroll, environment=ENV)
        trust = json.loads(data)
        record("trust_built_three_distinct_roles", "PASS" if len(set(trust.values())) == 3 else "FAIL", "PASS", "trust")
        record("operational_trust_rejects_test_enrollments", outcome(lambda: A.build_trust(enroll)), "DENY:ENROLLMENT_ENVIRONMENT", "trust")
        same = copy.deepcopy(enroll); same["acceptance"] = enroll["control"]
        record("trust_rejects_shared_identity", outcome(lambda: A.build_trust(same, environment=ENV)), "DENY:ENROLLMENT_ROLE", "trust")
        st2 = Store(work / "store_shared"); kid = st2.generate("acceptance", "TEST control custodian", "shared-custody-pass-1", IDENT["control"])
        shared = dict(enroll, acceptance=A.enroll(st2, "acceptance", kid, "shared-custody-pass-1", environment=ENV))
        record("trust_rejects_acceptance_sharing_control_custody", outcome(lambda: A.build_trust(shared, environment=ENV)),
               "DENY:ACCEPTANCE_CUSTODIAN_NOT_INDEPENDENT", "separation")
        forged_pop = copy.deepcopy(enroll["admission"]); forged_pop["payload"]["custodian"] = "someone else"
        record("enrollment_without_proof_of_possession_rejected", outcome(lambda: A.check_enrollment(forged_pop, "admission", ENV)),
               "DENY:INVALID_SIGNATURE", "trust")
        # ---------------- signer role restrictions (cross-role substitution at the signer)
        for role, domain in (("control", "commerce.permit.v1"), ("control", "commerce.a03.acceptance.v1"),
                             ("admission", "commerce.mission.v1"), ("admission", "commerce.bridge.installation.v1"),
                             ("acceptance", "commerce.control.v1"), ("acceptance", "commerce.permit.v1")):
            record(f"signer_refuses_{role}_signing_{domain}", outcome(lambda r=role, d=domain: A.role_sign(store, r, d, {"schema": d}, PW[r])),
                   "DENY:DOMAIN_NOT_PERMITTED_FOR_ROLE", "cross_role")
        record("generic_sign_refuses_permit_without_issuer_path", outcome(lambda: A.role_sign(store, "admission", "commerce.permit.v1", {"schema": "commerce.permit.v1"}, PW["admission"])),
               "DENY:DOMAIN_REQUIRES_DEDICATED_PATH", "admission_issuer")
        record("signer_refuses_payload_schema_mismatch", outcome(lambda: A.role_sign(store, "control", "commerce.mission.v1", {"schema": "commerce.control.v1"}, PW["control"])),
               "DENY:PAYLOAD_SCHEMA_DOMAIN_MISMATCH", "signing")

        # ---------------- full package on the qualified BCh runtime
        f = AuthorityFixture(work / "fx", sources, store)
        pkg, permit, bundle, assessment, acc = build_package(f, work / "pkg")
        tp, ts = trust_file(work, f.trust)
        clock = lambda: f.now
        record("positive_package_validates_bch", outcome(lambda: validate(pkg, sources, tp, ts, clock)), "PASS", "positive")
        # admission issuer path: re-executes the qualified assessment
        mission_env = read(pkg / "current-mission.json"); control_env = read(pkg / "current-control.json")
        record("issuer_signs_only_bound_eligible_request", outcome(lambda: A.sign_permit(store, PW["admission"], copy.deepcopy(permit), f.trust,
               mission_env, control_env, bundle, sources, assess=lambda: f.bridge.assess(mission_env, f.control, bundle, f.intent))), "PASS", "admission_issuer")
        bad = dict(permit, core_observation_hash="0" * 64)
        record("issuer_refuses_permit_not_bound_to_assessment", outcome(lambda: A.sign_permit(store, PW["admission"], bad, f.trust, mission_env, control_env,
               bundle, sources, assess=lambda: f.bridge.assess(mission_env, f.control, bundle, f.intent))), "DENY:PERMIT_NOT_BOUND_TO_ASSESSMENT", "admission_issuer")
        deny_obs = copy.deepcopy(f.observation); f.observation["controller_state"]["pay"]["unknown"] = True
        bad_bundle = f.bundle()
        record("issuer_refuses_when_assessment_denies", outcome(lambda: A.sign_permit(store, PW["admission"], copy.deepcopy(permit), f.trust, mission_env, control_env,
               bad_bundle, sources, assess=lambda: f.bridge.assess(mission_env, f.control, bad_bundle, f.intent))), "DENY:UNKNOWN_OR_MALFORMED_CORE_STATE", "admission_issuer")
        f.observation = deny_obs

        def mutated(name, mutate, expect, category, reseal=True):
            d = work / f"m-{len(RESULTS)}"; shutil.copytree(pkg, d)
            mutate(d)
            if reseal: seal_manifest(f, d)
            record(name, outcome(lambda: validate(d, sources, tp, ts, clock)), expect, category)

        def env_edit(name, fn):
            def m(d):
                e = read(d / name); fn(e); (d / name).write_text(json.dumps(e))
            return m

        def flip(e):
            s = bytearray(base64.b64decode(e["signature"])); s[7] ^= 1; e["signature"] = base64.b64encode(bytes(s)).decode()

        unseal = lambda role: store.unseal(role, store.active(role)["key_id"], PW[role])
        mutated("forged_control_signature", env_edit("current-control.json", flip), "DENY:INVALID_SIGNATURE", "forgery")
        mutated("forged_mission_signature", env_edit("current-mission.json", flip), "DENY:INVALID_SIGNATURE", "forgery")
        mutated("forged_admission_permit", env_edit("current-permit.json", flip), "DENY:INVALID_SIGNATURE", "forgery")
        mutated("forged_acceptance_signature", env_edit("a03-acceptance.json", flip), "DENY:INVALID_SIGNATURE", "forgery")
        mutated("forged_installation_manifest", env_edit("installation-manifest.json", flip), "DENY:INVALID_SIGNATURE", "forgery", reseal=False)
        # cross-role substitution at the verifier: genuine signatures by the WRONG role's real key
        for fname, domain, wrong in (("current-permit.json", "commerce.permit.v1", "control"),
                                     ("current-mission.json", "commerce.mission.v1", "admission"),
                                     ("current-control.json", "commerce.control.v1", "acceptance"),
                                     ("a03-acceptance.json", "commerce.a03.acceptance.v1", "control"),
                                     ("a03-acceptance.json", "commerce.a03.acceptance.v1", "admission")):
            def resign(d, fname=fname, domain=domain, wrong=wrong):
                e = read(d / fname); (d / fname).write_text(json.dumps(raw_sign(unseal(wrong), domain, e["payload"])))
            mutated(f"cross_role_{domain}_signed_by_{wrong}", resign, "DENY:INVALID_SIGNATURE", "cross_role")
        def manifest_by_control(d):
            seal_manifest(f, d, signer=lambda m: raw_sign(unseal("control"), "commerce.bridge.installation.v1", m))
        mutated("cross_role_installation_manifest_signed_by_control", manifest_by_control, "DENY:INVALID_SIGNATURE", "cross_role", reseal=False)
        # monetary / merchant terms
        def permit_terms(d, **ch):
            e = read(d / "current-permit.json"); p = e["payload"]; p["intent"] = dict(p["intent"], **ch)
            (d / "current-permit.json").write_text(json.dumps(f.sign("admission", "commerce.permit.v1", p)))
        mutated("modified_amount_in_permit", lambda d: permit_terms(d, amount_cents=4999), "DENY:ORIGINAL_INTENT_MISMATCH", "monetary")
        mutated("modified_merchant_in_permit", lambda d: permit_terms(d, merchant_id="ATTACKER-MERCHANT"), "DENY:ORIGINAL_INTENT_MISMATCH", "monetary")
        def mission_cap(d):
            e = read(d / "current-mission.json"); p = e["payload"]; p["cap_cents"] = 49999
            (d / "current-mission.json").write_text(json.dumps(f.sign("control", "commerce.mission.v1", p)))
        mutated("modified_mission_amount_resigned", mission_cap, "DENY:CONTROL_INACTIVE", "monetary")
        def mission_merchant(d):
            e = read(d / "current-mission.json"); p = e["payload"]; p["merchant_ids"] = ["ATTACKER-MERCHANT"]
            (d / "current-mission.json").write_text(json.dumps(f.sign("control", "commerce.mission.v1", p)))
        mutated("modified_mission_merchant_resigned", mission_merchant, "DENY:CONTROL_INACTIVE", "monetary")
        # epoch / expiry / source hash / evidence
        def epoch(d):
            c = copy.deepcopy(f.control); c["epoch"] = 2
            (d / "current-control.json").write_text(json.dumps(f.sign("control", "commerce.control.v1", c)))
        mutated("stale_epoch", epoch, "DENY:STALE_EPOCH", "epoch")
        def revoked_permit(d):
            c = copy.deepcopy(f.control); c["revoked_permits"] = [permit["permit_id"]]
            (d / "current-control.json").write_text(json.dumps(f.sign("control", "commerce.control.v1", c)))
        mutated("revoked_permit_in_control", revoked_permit, "DENY:REVOKED_PERMIT", "revocation")
        d_exp = work / "m-expired"; shutil.copytree(pkg, d_exp)
        record("expired_permit", outcome(lambda: validate(d_exp, sources, tp, ts, lambda: f.now + 450)), "DENY:EXPIRED_PERMIT", "expiry")
        def source_hash(d):
            e = read(d / "a03-acceptance.json"); p = e["payload"]; h = dict(p["bridge_source_hashes"])
            h["prepared.py"] = "0" * 64; p["bridge_source_hashes"] = h
            (d / "a03-acceptance.json").write_text(json.dumps(f.sign("acceptance", "commerce.a03.acceptance.v1", p)))
        mutated("changed_source_hash_in_acceptance", source_hash, "DENY:BRIDGE_SOURCE_CORRESPONDENCE", "source")
        def adapter_hash(d):
            e = read(d / "a03-acceptance.json"); p = e["payload"]; h = dict(p["runtime_source_hashes"]); h["gateway.py"] = "1" * 64
            p["runtime_source_hashes"] = h
            (d / "a03-acceptance.json").write_text(json.dumps(f.sign("acceptance", "commerce.a03.acceptance.v1", p)))
        mutated("changed_adapter_source_hash", adapter_hash, "DENY:A03_ADAPTER_CORRESPONDENCE", "source")
        def evidence_file(d):
            (d / "a03-evidence" / "LEAN_BUILD.txt").write_text("tampered")
        mutated("changed_evidence_file", evidence_file, "DENY:A03_EVIDENCE_HASH", "source")
        for missing in ("observation", "cage", "veip"):
            def drop(d, missing=missing):
                p = next((d / "bridge-evidence").glob("*.json")); b = json.loads(p.read_text()); b.pop(missing); p.write_text(json.dumps(b))
            mutated(f"missing_{missing}_evidence", drop, "DENY:CORE_EVIDENCE_BUNDLE_HASH", "evidence")
        for missing in ("observation", "cage", "veip"):
            def drop_rebind(d, missing=missing):
                # The issuer re-binds the permit to the stripped bundle, so the bundle-hash binding passes
                # and the missing-evidence check itself is reached.
                p = next((d / "bridge-evidence").glob("*.json")); b = json.loads(p.read_text()); b.pop(missing); p.write_text(json.dumps(b))
                e = read(d / "current-permit.json"); pp = e["payload"]; pp["bridge_evidence_hash"] = digest("commerce.bridge.bundle.v1", b)
                (d / "current-permit.json").write_text(json.dumps(f.sign("admission", "commerce.permit.v1", pp)))
            mutated(f"missing_{missing}_evidence_rebound_permit", drop_rebind, "FAILCLOSED", "evidence")
        def missing_bundle(d):
            next((d / "bridge-evidence").glob("*.json")).unlink()
        mutated("missing_bridge_bundle", missing_bundle, "DENY:CORE_EVIDENCE_BUNDLE_MISSING", "evidence")
        def unknown_evidence(d):
            p = next((d / "bridge-evidence").glob("*.json")); b = json.loads(p.read_text())
            b["observation"]["payload"]["controller_state"]["pay"]["unknown"] = True; p.write_text(json.dumps(b))
        mutated("unknown_core_state_evidence", unknown_evidence, "DENY:CORE_EVIDENCE_BUNDLE_HASH", "evidence")
        # trust-anchor replacement
        evil = {r: base64.b64encode(__import__("cryptography.hazmat.primitives.asymmetric.ed25519", fromlist=["x"]).Ed25519PrivateKey.generate().public_key().public_bytes_raw()).decode() for r in ROLES}
        def replace_trust(d):
            (d / "trust.json").write_text(json.dumps(evil, indent=2, sort_keys=True) + "\n")
        mutated("trust_anchor_replaced_in_package", replace_trust, "DENY:PACKAGE_TRUST_SUBSTITUTION", "trust_anchor")
        etp, ets = trust_file(work / "evil", evil) if (work / "evil").mkdir() is None else (None, None)
        record("trust_anchor_with_wrong_operator_fingerprint", outcome(lambda: validate(pkg, sources, tp, ets, clock)), "DENY:OPERATOR_TRUST_FINGERPRINT", "trust_anchor")
        record("attacker_trust_file_with_its_own_fingerprint", outcome(lambda: validate(pkg, sources, etp, ets, clock)), "DENY:PACKAGE_TRUST_SUBSTITUTION", "trust_anchor")
        def extra_key_file(d):
            (d / "a03-evidence" / "admission.key").write_text("x")
        mutated("private_key_file_in_package", extra_key_file, "DENY:PRIVATE_KEY_OR_LINK_IN_PACKAGE", "custody")

        # ---------------- revocation + rotation of the admission key
        old_kid = store.active("admission")["key_id"]
        rev = A.revoke(store, old_kid, "AUTHORITY-005 adversarial test: simulated compromise", PW["control"])
        record("revocation_record_signed_by_control", outcome(lambda: A.verify(rev, store.active("control")["public_key"], "commerce.authority.revocation.v1")), "PASS", "revocation")
        record("revoked_key_cannot_sign", outcome(lambda: store.unseal("admission", old_kid, PW["admission"])), "DENY:KEY_NOT_ACTIVE", "revocation")
        record("revoked_key_excluded_from_new_trust", outcome(lambda: A.build_trust(enroll, A.revoked_ids(store), environment=ENV)), "DENY:REVOKED_KEY_IN_TRUST", "revocation")
        new_kid = store.generate("admission", "TEST admission custodian", PW["admission"], IDENT["admission"])
        enroll2 = dict(enroll, admission=A.enroll(store, "admission", new_kid, PW["admission"], environment=ENV))
        data2, report2 = A.build_trust(enroll2, A.revoked_ids(store), environment=ENV)
        trust2 = json.loads(data2)
        record("rotated_trust_differs_only_in_admission", "PASS" if trust2["admission"] != trust["admission"] and trust2["control"] == trust["control"] else "FAIL", "PASS", "rotation")
        rot = work / "rotated"; shutil.copytree(pkg, rot); (rot / "trust.json").write_text(json.dumps(trust2, indent=2, sort_keys=True) + "\n")
        seal_manifest(f, rot)
        tp2, ts2 = trust_file(work / "rot-anchor" if (work / "rot-anchor").mkdir() is None else work, trust2)
        record("permit_signed_by_revoked_key_rejected_under_rotated_anchor", outcome(lambda: validate(rot, sources, tp2, ts2, clock)), "DENY:INVALID_SIGNATURE", "revocation")
        p2 = read(rot / "current-permit.json")["payload"]
        f.public["admission"] = trust2["admission"]; f.trust["admission"] = trust2["admission"]
        (rot / "current-permit.json").write_text(json.dumps(f.sign("admission", "commerce.permit.v1", p2))); seal_manifest(f, rot)
        record("rotated_key_permit_validates", outcome(lambda: validate(rot, sources, tp2, ts2, clock)), "PASS", "rotation")
        record("old_anchor_rejects_rotated_key_permit", outcome(lambda: validate(rot, sources, tp, ts, clock)), "DENY:PACKAGE_TRUST_SUBSTITUTION", "rotation")
        conf = A.confirm_trust(store, data2, hashlib.sha256(data2).hexdigest()[:16], PW["control"])
        record("operator_confirmation_of_rotated_anchor", outcome(lambda: A.verify(conf, trust2["control"], "commerce.authority.trust_confirmation.v1")), "PASS", "trust")
        record("confirmation_requires_typed_fingerprint", outcome(lambda: A.confirm_trust(store, data2, "0000000000000000", PW["control"])),
               "DENY:OPERATOR_FINGERPRINT_CONFIRMATION_MISMATCH", "trust")

        # ---------------- worker crash during verification, permit reuse (gateway on the rotated package)
        adm, mission, permit_env, control, _ = validate(rot, sources, tp2, ts2, clock)
        provider = FakePayPal(); g = Gateway(work / "ledger.db", adm, provider)
        g.apply_control(f.mission["mission_id"], control)
        S = type(sources.session); orig = S.dispatch
        def crash(self, *a):
            t = orig(self, *a); self.child.process.kill(); self.child.process.wait(); return t
        S.dispatch = crash
        def chk():
            with g.transaction() as db: g.check(db, mission, permit_env)
        r = outcome(chk); S.dispatch = orig
        record("worker_crash_during_verification_denies", r, "DENY:QUALIFIED_WORKER", "worker")
        record("worker_restarted_next_request_allows", outcome(chk), "PASS", "worker")
        eid = g.reserve(mission, permit_env); g.create(eid); provider.approve(); state = g.capture(eid)
        record("first_effect_captured_once", "PASS" if state == "CAPTURE_CONFIRMED" and provider.capture_count() == 1 else "FAIL:" + str(state), "PASS", "consumption")
        calls_before = len(provider.calls); effects_before = g.connect().execute("SELECT COUNT(*) FROM effects").fetchone()[0]
        again = outcome(lambda: g.reserve(mission, permit_env))
        calls_after = len(provider.calls); effects_after = g.connect().execute("SELECT COUNT(*) FROM effects").fetchone()[0]
        record("consumed_permit_reuse_creates_no_new_effect",
               "PASS" if again == "PASS" and calls_after == calls_before and effects_after == effects_before == 1 and provider.capture_count() == 1
               else f"FAIL:{again}:calls {calls_before}->{calls_after}:effects {effects_before}->{effects_after}", "PASS", "consumption")
        p3 = dict(p2, permit_id="AUTH005-PERMIT-2"); p3env = f.sign("admission", "commerce.permit.v1", p3)
        (rot / "bridge-evidence" / (digest("commerce.permit.locator.v1", p3["permit_id"]) + ".json")).write_text(
            next((rot / "bridge-evidence").glob("*.json")).read_text())
        record("second_permit_for_same_effect_refused", outcome(lambda: g.reserve(mission, p3env)), "DENY:EFFECT_IDENTITY_CONFLICT", "consumption")
        record("captures_total_after_reuse_attempts", "PASS" if provider.capture_count() == 1 else f"FAIL:{provider.capture_count()}", "PASS", "consumption")
        adm.close()
    finally:
        sources.close()
        shutil.rmtree(work, ignore_errors=True)
    cats = {}
    for r in RESULTS:
        c = cats.setdefault(r["category"], {"tests": 0, "pass": 0}); c["tests"] += 1; c["pass"] += r["pass"]
    attacks = [r for r in RESULTS if r["expected"] != "PASS"]
    summary = {"tests": len(RESULTS), "passed": sum(r["pass"] for r in RESULTS), "failed": [r["test"] for r in RESULTS if not r["pass"]],
               "attacks": len(attacks), "unauthorized_admissions": sum(r["actual"] == "PASS" for r in attacks),
               "cross_role_substitutions_accepted": sum(r["actual"] == "PASS" for r in attacks if r["category"] == "cross_role"),
               "revoked_permissions_accepted": sum(r["actual"] == "PASS" for r in attacks if r["category"] == "revocation"),
               "incorrect_monetary_terms_accepted": sum(r["actual"] == "PASS" for r in attacks if r["category"] == "monetary"),
               "by_category": cats, "runtime": "BCh (QualifiedSources + consolidated worker + PreparedAdmission overlap)",
               "scope": "TEST role keys in a temporary DPAPI store (environment TEST-AUTHORITY-005); conformance-only Core/CAGE/VEIP sources; FakePayPal",
               "seconds": round(time.time() - t0, 1), "python": sys.version}
    summary["verdict"] = "PASS" if not summary["failed"] else "FAIL"
    (out / "ADVERSARIAL_RESULTS.json").write_text(json.dumps({"summary": summary, "results": RESULTS}, indent=1) + "\n")
    print(json.dumps(summary, indent=1))
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
