"""WO-COM-LATENCY-004 architecture C: prepared admission context.

PreparedAdmission evaluates EVERY predicate of
  commerce_adapter.contracts.Admission.control / .ready / .check,
  BridgeAdmission.ready (locked) / .check, Bridge.assess / .policy
in the same order with the same denial codes (equivalence: latency004 tests).
What changes is only how the cryptographic and canonical work is obtained:

1. Verification memo. Ed25519 verification is a pure function of (public key, message,
   signature). A successful verification is remembered under the EXACT key string,
   signature string and signed message bytes (domain || 0 || canonical(payload)). Any change
   of signed content, key, signature, domain, authority (key) or epoch (part of the content)
   is a different entry and is verified from scratch. Failures are never remembered.
   Canonicalization (and therefore its type checks) still runs on every presented payload.
2. Request-scoped canonical reuse. Within one request the same object is canonicalized once and
   the bytes are reused for its signature message and its digests. The evidence bundle is parsed
   by json.loads (same objects) while recording whether the document contains a float/NaN/Infinity;
   for dicts of a float-free json.loads tree canonical()'s type check cannot fail (only
   dict/list/str/int/bool/None with str keys exist), so their canonical bytes are produced by the
   same json.dumps call canonical() makes, without the Python type walk. Any float -> full canonical().
3. Immutable session context, bound to the sources/profile/trust at preparation: private
   deep copies of the operator-loaded acceptance, upstream acceptances and accepted policy
   (A loads these once too) are verified once; their per-request predicates (status, pins,
   tests, EXPIRY, bindings, evidence hashes) are still evaluated on every request.
   design/SOURCE_PINS.json, read eagerly by Bridge.policy on every request in A, is held
   under a deny-write/deny-delete OS lock and parsed once.
4. Optional overlap (architecture BC): when the session offers dispatch/collect, the pure Core
   calculation starts as soon as policy and observation are authenticated; all remaining
   checks run meanwhile; a denial drains and discards the calculation.

Per request and never cached: control envelope from the ledger (revocation, epoch, activity,
expiry), mission/permit/bundle/observation/CAGE/VEIP signatures (memo is exact), every
freshness, revocation, action, intent, budget and binding predicate, source snapshot state,
the Core calculation, the qualified decider, the zverdict witness.
"""
import base64
import copy
import hashlib
import json
import threading
from collections import OrderedDict
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from commerce_adapter.contracts import Denied, canonical, cents, require
from .admission import BridgeAdmission, lean_attestation
from .runtime import CoreRuntime

ROOT = Path(__file__).resolve().parent.parent
MEMO_SIZE = 4096


class PreparedAdmission(BridgeAdmission):
    def __init__(self, *args, overlap=False, early=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.early = early
        require(self.locked is not None, "PREPARED_CONTEXT_REQUIRES_IMMUTABLE_SESSION")
        self.overlap = overlap
        self._tl = threading.local()
        self._memo = OrderedDict(); self._memo_lock = threading.Lock()
        self._keys = {}
        self._frozen = {}
        self.stats = {"verify_memo_hits": 0, "verify_full": 0}
        # Private, immutable session inputs (A also loads them once, at construction).
        self.acceptance = copy.deepcopy(self.acceptance)
        self.policy_envelope = copy.deepcopy(self.policy_envelope)
        self._upstream = {name: json.loads(text) for name, text in self.locked.upstream.items()}
        for value in (self.acceptance["payload"], self.policy_envelope["payload"], *[u["payload"] for u in self._upstream.values()]):
            if isinstance(value, (dict, list)): self._frozen[id(value)] = (value, canonical(value))
        from .qualified import WindowsSnapshot
        pins = ROOT / "design/SOURCE_PINS.json"
        self._pins_lock = WindowsSnapshot({pins: hashlib.sha256(pins.read_bytes()).hexdigest()})
        self._design_pins = json.loads(pins.read_text())["source_pins"]

    def close(self):
        super().close()
        self._pins_lock.close()

    # ------------------------------------------------------------------ primitives
    def _req(self):
        r = getattr(self._tl, "req", None)
        if r is None:
            r = self._tl.req = {}
        return r

    def _begin(self):
        self._tl.req = {}; self._tl.native = None

    def canon(self, value):
        hit = self._frozen.get(id(value))
        if hit is not None and hit[0] is value:
            return hit[1]
        r = self._req()
        hit = r.get(id(value))
        if hit is not None and hit[0] is value:
            return hit[1]
        native = getattr(self._tl, "native", None)
        if native is not None and id(value) in native and native[id(value)] is value:
            # A dict produced by json.loads of a float-free document: only dict/list/str/int/bool/None
            # with str keys can occur, so canonical()'s type check cannot fail and its bytes are exactly
            # this json.dumps call (the one canonical() itself makes).
            body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        else:
            body = canonical(value)
        if isinstance(value, (dict, list)):
            r[id(value)] = (value, body)
        return body

    def load_native(self, text):
        """json.loads(text) (same objects) that also records whether the document is float-free."""
        nodes = {}; floats = []
        def hook(d):
            nodes[id(d)] = d; return d
        def pf(s):
            floats.append(s); return float(s)
        constants = {"-Infinity": float("-inf"), "Infinity": float("inf"), "NaN": float("nan")}
        def pc(s):
            floats.append(s); return constants[s]
        value = json.loads(text, object_hook=hook, parse_float=pf, parse_constant=pc)
        self._tl.native = {} if floats else nodes   # request scope; cleared with the request cache
        return value
    def digest(self, domain, value):
        return hashlib.sha256(domain.encode("ascii") + b"\x00" + self.canon(value)).hexdigest()

    def _key(self, public_key):
        key = self._keys.get(public_key)
        if key is None:
            key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True))
            if len(self._keys) < 256: self._keys[public_key] = key
        return key

    def verify(self, envelope, public_key, domain):
        try:
            payload = envelope["payload"]
            signature = envelope["signature"]
            message = domain.encode("ascii") + b"\x00" + self.canon(payload)
            entry = (public_key, signature, message)
            if entry in self._memo:
                self.stats["verify_memo_hits"] += 1
                return payload
            self._key(public_key).verify(base64.b64decode(signature, validate=True), message)
            self.stats["verify_full"] += 1
            with self._memo_lock:
                self._memo[entry] = True
                if len(self._memo) > MEMO_SIZE: self._memo.popitem(last=False)
            return payload
        except Exception:
            raise Denied("INVALID_SIGNATURE") from None

    # ------------------------------------------------------------------ A04 Admission
    def control(self, envelope):
        self._begin()
        p = self.verify(envelope, self.trust["control"], "commerce.control.v1")
        require(p["schema"] == "commerce.control.v1" and type(p["sequence"]) is int
                and p["sequence"] >= 0 and type(p["epoch"]) is int, "CONTROL_SCHEMA")
        return p

    def ready(self):
        L = self.locked
        require(L.locks.active, "IMMUTABLE_SNAPSHOT_CLOSED")
        a = self.verify(self.acceptance, self.trust["acceptance"], "commerce.a03.acceptance.v1")
        require(a["schema"] == "commerce.a03.acceptance.v1" and a["status"] == "PASS", "A03_NOT_PASS")
        require(a["tdd_sha256"] == self.a03_hash and a["source_pins"] == self.source_pins, "A03_SOURCE_MISMATCH")
        require(a["tests"] == {f"A03-T{i:02}": "PASS" for i in range(1, 14)}, "A03_CASES_MISSING")
        require(a["expires_at"] > self.clock(), "A03_ACCEPTANCE_EXPIRED")
        require(a["adapter_schema"] == "commerce.a04.adapter.v1" and a["runtime_source_hashes"] == L.adapter_hashes,
                "A03_ADAPTER_CORRESPONDENCE")
        require(L.REQUIRED <= a["evidence_hashes"].keys(), "A03_EVIDENCE_MISSING")
        for name, expected in a["evidence_hashes"].items():
            require(name in L.evidence_hashes and name not in L.escaped, "A03_EVIDENCE_MISSING")
            require(L.evidence_hashes[name] == expected, "A03_EVIDENCE_HASH")
        require(a["frozen_interface_sha256"] == L.interface_sha == L.frozen, "BRIDGE_INTERFACE_DRIFT")
        require(a["bridge_source_hashes"] == L.bridge_hashes and a["runtime_source_hashes"] == L.adapter_hashes, "BRIDGE_SOURCE_CORRESPONDENCE")
        manifest = self.sources.verify()
        manifest_hash = L.native_manifest_hash if manifest is L.manifest_obj else self.digest("commerce.bridge.sources.v1", manifest)
        require(a["native_source_manifest_hash"] == manifest_hash, "BRIDGE_NATIVE_SOURCE_CORRESPONDENCE")
        require(a["bridge_policy_hash"] == self.digest("commerce.bridge.policy.v1", self.policy_envelope["payload"]), "ACCEPTED_POLICY_MISMATCH")
        require(a["upstream_acceptance"]["A01"] == a["upstream_acceptance"]["A02"] == "PASS", "UPSTREAM_NOT_ACCEPTED")
        for order, tdd_hash in L.UPSTREAM.items():
            name = f"UPSTREAM_{order}_ACCEPTANCE.json"
            require(name in a["evidence_hashes"], "UPSTREAM_ACCEPTANCE_ARTIFACT_MISSING")
            upstream = self.verify(self._upstream[name], self.trust["acceptance"], "commerce.upstream.acceptance.v1")
            require(upstream["work_order"] == order and upstream["status"] == "PASS"
                    and upstream["tdd_sha256"] == tdd_hash and upstream["source_pins"] == self.source_pins
                    and upstream["mission_hash"] == self.policy_envelope["payload"]["mission_hash"]
                    and upstream["expires_at"] > self.clock() and upstream["evidence_hashes"], "UPSTREAM_NOT_ACCEPTED")
            require(all(a["evidence_hashes"].get(n) == h for n, h in upstream["evidence_hashes"].items()), "UPSTREAM_EVIDENCE_NOT_BOUND")
        return a

    def check(self, mission_envelope, permit_envelope, control):
        self._tl.spec = None
        try:
            if self.overlap and self.early:
                self._tl.spec = self._speculate(permit_envelope)
            return self._check(mission_envelope, permit_envelope, control)
        finally:
            spec = self._tl.spec; self._tl.spec = None
            if spec is not None and spec.get("ticket") is not None:
                spec["session"].abandon(spec.pop("ticket"))
            self._tl.req = {}; self._tl.native = None

    # ------------------------------------------------------------------ speculative early dispatch (BC)
    def _speculate(self, permit_envelope):
        """Read the evidence bundle and, if its policy and observation AUTHENTICATE, start the pure Core
        calculation before the authority checks run. Never raises and never decides: every check is
        still evaluated afterwards in A's order (a failure here is silent and re-raised by the real check
        at its original position). The bundle file is read once, here; its presence and content are
        evaluated at A's position. The result is used only if Bridge.assess later authenticates the very
        same objects and the evaluation instant is equivalent (_same_freshness)."""
        spec = {}
        try:
            payload = permit_envelope["payload"]                      # unverified: used only to locate a file
            path = self.bundle_dir / (self.digest("commerce.permit.locator.v1", payload["permit_id"]) + ".json")
            spec["path"] = path; spec["is_file"] = path.is_file()
            if not spec["is_file"]:
                return spec
            spec["bundle"] = self.load_native(path.read_text(encoding="utf-8-sig"))
        except Exception:
            return None                                                 # A's own read happens at its position
        try:
            session = getattr(self.sources, "session", None)
            if not hasattr(session, "dispatch"):
                return spec
            bundle = spec["bundle"]
            policy = self.verify(bundle["policy"], self.trust["control"], "commerce.bridge.policy.v1")
            observation = self.verify(bundle["observation"], policy["core_public_key"], "commerce.core.observation.v1")
            now = self.clock()
            spec.update(session=session, policy=policy, observation=observation, now=now,
                        ticket=session.dispatch(observation, policy, now))
        except Exception:
            pass
        return spec

    @staticmethod
    def _same_freshness(observation, policy, t0, t1):
        """True iff evaluating Core at t0 instead of t1 cannot change its output used here.
        The worker reads ``now`` only as ZtlEvidenceService's clock: report age classification
        0 <= now - observed_at <= max_age_seconds (core_worker/ztl_service). Identical instants, or every
        report classified identically at both instants, give identical decisions; anything unparsable
        -> False (recompute at A's instant)."""
        if t0 == t1:
            return True
        try:
            from datetime import datetime, timezone
            a, b = datetime.fromtimestamp(t0, timezone.utc), datetime.fromtimestamp(t1, timezone.utc)
            m = policy["max_age_seconds"]
            for rep in observation["reports"]:
                at = datetime.fromisoformat(rep["observed_at"].replace("Z", "+00:00"))
                x, y = (a - at).total_seconds(), (b - at).total_seconds()
                if (0 <= x <= m) != (0 <= y <= m):
                    return False
            return True
        except Exception:
            return False

    def _take_speculation(self, observation, policy, now):
        spec = getattr(self._tl, "spec", None)
        if not spec or spec.get("ticket") is None:
            return None
        if spec["observation"] is observation and spec["policy"] is policy and self._same_freshness(observation, policy, spec["now"], now):
            return spec.pop("ticket")
        spec["session"].abandon(spec.pop("ticket"))
        return None

    def _check(self, mission_envelope, permit_envelope, control):
        # --- commerce_adapter.contracts.Admission.check, predicate for predicate ---
        acceptance = self.ready()
        mission = self.verify(mission_envelope, self.trust["control"], "commerce.mission.v1")
        permit = self.verify(permit_envelope, self.trust["admission"], "commerce.permit.v1")
        now = self.clock()
        mh = self.digest("commerce.mission.v1", mission)
        require(control["mission_hash"] == mh and control["active"] is True
                and control["expires_at"] > now, "CONTROL_INACTIVE")
        require(mission["schema"] == "commerce.mission.v1" and permit["schema"] == "commerce.permit.v1", "SCHEMA")
        require(mission["currency"] == "USD" and type(mission["cap_cents"]) is int
                and 0 < mission["cap_cents"] <= 50000, "MISSION_BUDGET")
        require(mission["obligations"] and len(set(mission["obligations"])) == len(mission["obligations"]), "EMPTY_OR_DUPLICATE_OBLIGATIONS")
        require(mission["deadline"] > now and permit["issued_at"] <= now < permit["expires_at"]
                <= permit["issued_at"] + 600, "EXPIRED_PERMIT")
        require(permit["quote_expires_at"] > now, "STALE_QUOTE")
        require(mission["epoch"] == permit["epoch"] == control["epoch"], "STALE_EPOCH")
        require(permit["permit_id"] not in control["revoked_permits"], "REVOKED_PERMIT")
        require(permit["mission_hash"] == mh and permit["acceptance_hash"] == self.digest("commerce.a03.acceptance.v1", acceptance), "PERMIT_BINDING")
        gates = ("lean_rule_earned", "controller_act", "cage_allow", "veip_eligible", "prerequisites_satisfied")
        require(all(permit[g] is True for g in gates) and permit["verdict"] == "T"
                and permit["scope"] == "paypal.sandbox.orders.create_capture", "ADMISSION_DENIED")
        intent = permit["intent"]
        require(intent in mission["effects"] and intent["obligation_id"] in mission["obligations"], "ORIGINAL_INTENT_MISMATCH")
        require(intent["actor"] == mission["actor"] and intent["currency"] == mission["currency"]
                and intent["merchant_id"] in mission["merchant_ids"], "SCOPE_MISMATCH")
        require(all(type(intent[k]) is str and intent[k] for k in
                    ("obligation_id", "effect_slot", "actor", "merchant_id", "quote_hash", "prerequisites_hash")), "INTENT_SCHEMA")
        cents(intent["amount_cents"])
        # --- BridgeAdmission.check ---
        locator = self.digest("commerce.permit.locator.v1", permit["permit_id"]) + ".json"
        path = self.bundle_dir / locator
        spec = getattr(self._tl, "spec", None)
        if spec and spec.get("path") == path:
            require(spec["is_file"], "CORE_EVIDENCE_BUNDLE_MISSING")
            bundle = spec["bundle"]
        else:
            require(path.is_file(), "CORE_EVIDENCE_BUNDLE_MISSING")
            bundle = self.load_native(path.read_text(encoding="utf-8-sig"))
        require(self.digest("commerce.bridge.bundle.v1", bundle) == permit["bridge_evidence_hash"], "CORE_EVIDENCE_BUNDLE_HASH")
        require(bundle["policy"] == self.policy_envelope, "CURRENT_BRIDGE_POLICY_MISMATCH")
        assessment = self.assess(mission_envelope, control, bundle, intent)
        require(self.digest("commerce.bridge.assessment.v1", assessment) == permit["bridge_assessment_hash"], "BRIDGE_ASSESSMENT_BINDING")
        require(assessment["observation_hash"] == permit["core_observation_hash"], "PERMIT_CORE_OBSERVATION_BINDING")
        # FORMAL-001: attestation the Gateway requires before any external POST (fresh, bound to this exact intent).
        self.last_decision = lean_attestation(assessment, intent, self.clock())
        return mission, permit, intent

    # ------------------------------------------------------------------ Bridge
    def policy(self, envelope, mission_hash):
        p = self.verify(envelope, self.trust["control"], "commerce.bridge.policy.v1")
        require(p["schema"] == "commerce.bridge.policy.v1" and p["mission_hash"] == mission_hash, "POLICY_BINDING")
        require(p["expires_at"] > self.clock() and type(p["f"]) is int and 0 <= p["f"] <= 1000
                and type(p["max_age_seconds"]) is int and 0 < p["max_age_seconds"] <= 300, "POLICY_BOUNDS")
        require(p["source_pins"] == getattr(self.sources, "policy_pins", self._design_pins), "POLICY_SOURCE_PINS")
        if hasattr(self.sources, "profile_hash"):
            require(p.get("qualification_profile_sha256") == self.sources.profile_hash, "QUALIFIED_PROFILE_BINDING")
        entries = p["reporters"]
        require(entries and all(e["agent"] and e["root"] and e["basis"] and e["source_id"] for e in entries), "REPORTER_REGISTRY")
        for field in ("agent", "root", "public_key"):
            require(len({e[field] for e in entries}) == len(entries), "DUPLICATE_REPORTER_IDENTITY")
        sources, bases = {}, {}
        for e in entries:
            require(sources.setdefault(e["source_id"], e["basis"]) == e["basis"], "SHARED_SOURCE_FAKE_INDEPENDENCE")
            require(bases.setdefault(e["basis"], e["source_id"]) == e["source_id"], "BASIS_PROVENANCE_MISMATCH")
        require(len({e["basis"] for e in entries}) >= p["f"] + 1, "INSUFFICIENT_INDEPENDENT_BASES")
        return p

    def assess(self, mission_envelope, control, bundle, intent):
        now = self.clock()
        mission = self.verify(mission_envelope, self.trust["control"], "commerce.mission.v1")
        mh = self.digest("commerce.mission.v1", mission)
        require(mission["obligations"] and len(set(mission["obligations"])) == len(mission["obligations"]), "EMPTY_OR_DUPLICATE_OBLIGATIONS")
        require(mission["deadline"] > now and mission["currency"] == "USD" and type(mission["cap_cents"]) is int
                and 0 < mission["cap_cents"] <= 50000, "MISSION_EXPIRED_OR_BUDGET")
        require(intent in mission["effects"] and intent["obligation_id"] in mission["obligations"], "ORIGINAL_INTENT_MISMATCH")
        cents(intent["amount_cents"])
        require(intent["amount_cents"] <= mission["cap_cents"] and intent["merchant_id"] in mission["merchant_ids"]
                and intent["currency"] == mission["currency"] and intent["actor"] == mission["actor"], "INTENT_SCOPE_OR_BUDGET")
        require(control["mission_hash"] == mh and control["epoch"] == mission["epoch"] and control["active"] is True
                and control["expires_at"] > now and mission["actor"] not in control.get("revoked_actors", []), "CURRENT_AUTHORITY_DENIED")
        policy = self.policy(bundle["policy"], mh)
        require(policy["original_obligations"] == mission["obligations"]
                and set(policy["obligation_task_map"]) == set(mission["obligations"]), "ORIGINAL_OBLIGATION_MAPPING")
        observation = self.verify(bundle["observation"], policy["core_public_key"], "commerce.core.observation.v1")
        session = getattr(self.sources, "session", None)
        ticket = None
        if self.overlap and hasattr(session, "dispatch"):
            ticket = self._take_speculation(observation, policy, now) or session.dispatch(observation, policy, now)
        try:
            calculated = self._assess_rest(mission, mh, intent, control, policy, observation, bundle, now, session, ticket)
            ticket = None
        finally:
            if ticket is not None: session.abandon(ticket)
        return calculated

    def _assess_rest(self, mission, mh, intent, control, policy, observation, bundle, now, session, ticket):
        oh = self.digest("commerce.core.observation.v1", observation)
        ih = self.digest("commerce.intent.v1", intent)
        require(observation["schema"] == "commerce.core.observation.v1" and observation["mission_hash"] == mh
                and observation["intent_hash"] == ih and observation["epoch"] == control["epoch"]
                and observation["attempt_state"] == "RUNNING", "CORE_OBSERVATION_BINDING")
        for state in observation["controller_state"].values():
            require(set(state) == {"status", "command_id", "epoch", "effect_id", "unknown"}
                    and state["status"] in ("PENDING", "CLAIMED", "CLOSED") and state["unknown"] is False
                    and type(state["epoch"]) is int and state["epoch"] >= 0, "UNKNOWN_OR_MALFORMED_CORE_STATE")
        sequences = [record["seq"] for record in observation["records"]]
        require(all(type(n) is int and n >= 0 for n in sequences) and sequences == sorted(set(sequences)), "CORE_RECORD_CHRONOLOGY")
        require(0 <= now - observation["observed_at"] <= policy["max_age_seconds"], "STALE_CORE_OBSERVATION")
        require(self.digest("commerce.core.world.v1", observation["world"]) == policy["world_hash"], "WORLD_HASH_MISMATCH")
        require(observation["contract_hash"] == mission["contract_hash"] and observation["quote_hash"] == intent["quote_hash"], "CONTRACT_OR_QUOTE_MISMATCH")
        require(observation["task_id"] == policy["obligation_task_map"][intent["obligation_id"]], "TASK_MAPPING_MISMATCH")
        action = observation["action"]
        require(action["tool"] == policy["payment_tool"] and action["params"][policy["amount_param"]] == intent["amount_cents"]
                and action["params"][policy["merchant_param"]] == intent["merchant_id"] and action["target"] == policy["payment_target"], "CORE_ACTION_BINDING")
        available = observation["world_state"]
        for key in policy["budget_path"]: available = available[key]
        require(type(available) is int and intent["amount_cents"] <= available, "CORE_NUMERICAL_BUDGET")
        seen = {}
        for report in observation["reports"]:
            key = (report["fact_id"], report["agent"])
            require(key not in seen or seen[key] is report["value"], "REPORTER_EQUIVOCATION")
            seen[key] = report["value"]
        for role, decision in (("cage", "ALLOW"), ("veip", "ELIGIBLE")):
            domain = f"commerce.{role}.authorization.v1"
            a = self.verify(bundle[role], policy[role + "_public_key"], domain)
            require(a["schema"] == domain and a["decision"] == decision and a["mission_hash"] == mh
                    and a["intent_hash"] == ih and a["observation_hash"] == oh and a["actor"] == mission["actor"]
                    and a["epoch"] == control["epoch"] and a["scope"] == "paypal.sandbox.orders.create_capture"
                    and a["expires_at"] > now and 0 <= now - a["observed_at"] <= policy["max_age_seconds"], "EXTERNAL_AUTHORITY_DENIED")
        require(self.digest("commerce.prerequisites.v1", observation["records"]) == intent["prerequisites_hash"], "PREREQUISITE_RECORDS_MISMATCH")
        if ticket is not None:
            t, ticket = ticket, None
            calculated = session.collect(t)
        else:
            calculated = CoreRuntime(self.sources).calculate(observation, policy, now)
        require(all(s == "ACCEPTED_FOR_EVALUATION" for s in calculated["submissions"]), "CORE_REPORT_SIGNATURE_OR_IDENTITY")
        require(set(policy["obligation_task_map"].values()) <= set(calculated["task_ids"]), "MISSING_ORIGINAL_CORE_TASK")
        require(calculated["task_actor"] == mission["actor"] and calculated["directive"]["directive"] == "ACT", "CORE_NOT_ACT")
        require(calculated["world_id"] == mission["mission_id"] and calculated["task_write_tool"] == action["tool"]
                and calculated["parameter_status"] == 200 and calculated["native_preconditions_ok"], "CORE_TOOL_PRECONDITIONS")
        require(calculated["all_facts_true"] and all(calculated["predicate_truths"]), "CORE_NOT_EARNED_TRUE")
        require(observation["claimed_directive"] == calculated["directive"], "CORE_DIRECTIVE_MISMATCH")
        if hasattr(self.sources, "profile_hash"):
            require(calculated["qualified"]["lean"]["verdict"] == "T", "QUALIFIED_CORE_NOT_T")
        source_manifest = calculated["source_manifest"]
        summary = {k: calculated[k] for k in ("directive", "task_actor", "task_dependencies", "task_write_tool", "world_id", "task_ids",
                   "parameter_status", "native_preconditions_ok", "predicate_truths", "differential", "mismatches", "all_facts_true")}
        if "qualified" in calculated: summary["qualified"] = calculated["qualified"]
        cached = getattr(self.sources, "manifest_digest", None)
        manifest_hash = cached if cached is not None and source_manifest is self.sources.manifest else self.digest("commerce.bridge.sources.v1", source_manifest)
        return {"schema": "commerce.bridge.assessment.v1", "decision": "ALLOW_ISSUER_REQUEST_ONLY",
                "mission_hash": mh, "intent_hash": ih, "observation_hash": oh, "policy_hash": self.digest("commerce.bridge.policy.v1", policy),
                "epoch": control["epoch"], "core": summary, "source_manifest_hash": manifest_hash,
                "authority_checked": ["control", "core", "cage", "veip"], "dispatch_authorized": False}




