import hashlib
import json
import time
from pathlib import Path
from commerce_adapter.contracts import Admission,canonical,cents,digest,require,verify
from .runtime import CoreRuntime
from .sources import FROZEN_INTERFACE_SHA256,adapter_hashes,bridge_hashes


class Bridge:
    """Calculate admission only from authenticated, operator-scoped observations.

    This class has no private key and cannot sign a permit or acceptance. An ALLOW
    assessment is an unsigned issuer request; A04 still requires external signatures.
    """
    def __init__(self,trust,sources,clock=time.time,witness=None):
        self.trust,self.sources,self.clock=trust,sources,clock
        self.runtime=CoreRuntime(sources,witness)

    def policy(self,envelope,mission_hash):
        p=verify(envelope,self.trust["control"],"commerce.bridge.policy.v1")
        require(p["schema"]=="commerce.bridge.policy.v1" and p["mission_hash"]==mission_hash,"POLICY_BINDING")
        require(p["expires_at"]>self.clock() and type(p["f"]) is int and 0<=p["f"]<=1000
                and type(p["max_age_seconds"]) is int and 0<p["max_age_seconds"]<=300,"POLICY_BOUNDS")
        require(p["source_pins"]==getattr(self.sources,"policy_pins",self.design_pins()),"POLICY_SOURCE_PINS")
        if hasattr(self.sources,"profile_hash"):
            require(p.get("qualification_profile_sha256")==self.sources.profile_hash,"QUALIFIED_PROFILE_BINDING")
        entries=p["reporters"]
        require(entries and all(e["agent"] and e["root"] and e["basis"] and e["source_id"] for e in entries),"REPORTER_REGISTRY")
        for field in ("agent","root","public_key"):
            require(len({e[field] for e in entries})==len(entries),"DUPLICATE_REPORTER_IDENTITY")
        sources,bases={},{}
        for e in entries:
            require(sources.setdefault(e["source_id"],e["basis"])==e["basis"],"SHARED_SOURCE_FAKE_INDEPENDENCE")
            require(bases.setdefault(e["basis"],e["source_id"])==e["source_id"],"BASIS_PROVENANCE_MISMATCH")
        require(len({e["basis"] for e in entries})>=p["f"]+1,"INSUFFICIENT_INDEPENDENT_BASES")
        return p

    def manifest_hash(self,manifest):
        # The immutable session's manifest object is constant; hash it once.
        cached=getattr(self.sources,"manifest_digest",None)
        if cached is not None and manifest is self.sources.manifest: return cached
        return digest("commerce.bridge.sources.v1",manifest)

    @staticmethod
    def design_pins():
        return json.loads((Path(__file__).resolve().parent.parent/"design/SOURCE_PINS.json").read_text())["source_pins"]

    def assess(self,mission_envelope,control,bundle,intent):
        now=self.clock()
        mission=verify(mission_envelope,self.trust["control"],"commerce.mission.v1")
        mh=digest("commerce.mission.v1",mission)
        require(mission["obligations"] and len(set(mission["obligations"]))==len(mission["obligations"]),"EMPTY_OR_DUPLICATE_OBLIGATIONS")
        require(mission["deadline"]>now and mission["currency"]=="USD" and type(mission["cap_cents"]) is int
                and 0<mission["cap_cents"]<=50000,"MISSION_EXPIRED_OR_BUDGET")
        require(intent in mission["effects"] and intent["obligation_id"] in mission["obligations"],"ORIGINAL_INTENT_MISMATCH")
        cents(intent["amount_cents"])
        require(intent["amount_cents"]<=mission["cap_cents"] and intent["merchant_id"] in mission["merchant_ids"]
                and intent["currency"]==mission["currency"] and intent["actor"]==mission["actor"],"INTENT_SCOPE_OR_BUDGET")
        require(control["mission_hash"]==mh and control["epoch"]==mission["epoch"] and control["active"] is True
                and control["expires_at"]>now and mission["actor"] not in control.get("revoked_actors",[]),"CURRENT_AUTHORITY_DENIED")
        policy=self.policy(bundle["policy"],mh)
        require(policy["original_obligations"]==mission["obligations"]
                and set(policy["obligation_task_map"])==set(mission["obligations"]),"ORIGINAL_OBLIGATION_MAPPING")
        observation=verify(bundle["observation"],policy["core_public_key"],"commerce.core.observation.v1")
        oh=digest("commerce.core.observation.v1",observation)
        ih=digest("commerce.intent.v1",intent)
        require(observation["schema"]=="commerce.core.observation.v1" and observation["mission_hash"]==mh
                and observation["intent_hash"]==ih and observation["epoch"]==control["epoch"]
                and observation["attempt_state"]=="RUNNING","CORE_OBSERVATION_BINDING")
        for state in observation["controller_state"].values():
            require(set(state)=={"status","command_id","epoch","effect_id","unknown"}
                    and state["status"] in ("PENDING","CLAIMED","CLOSED") and state["unknown"] is False
                    and type(state["epoch"]) is int and state["epoch"]>=0,"UNKNOWN_OR_MALFORMED_CORE_STATE")
        sequences=[record["seq"] for record in observation["records"]]
        require(all(type(n) is int and n>=0 for n in sequences) and sequences==sorted(set(sequences)),"CORE_RECORD_CHRONOLOGY")
        require(0<=now-observation["observed_at"]<=policy["max_age_seconds"],"STALE_CORE_OBSERVATION")
        require(digest("commerce.core.world.v1",observation["world"])==policy["world_hash"],"WORLD_HASH_MISMATCH")
        require(observation["contract_hash"]==mission["contract_hash"] and observation["quote_hash"]==intent["quote_hash"],"CONTRACT_OR_QUOTE_MISMATCH")
        require(observation["task_id"]==policy["obligation_task_map"][intent["obligation_id"]],"TASK_MAPPING_MISMATCH")
        action=observation["action"]
        require(action["tool"]==policy["payment_tool"] and action["params"][policy["amount_param"]]==intent["amount_cents"]
                and action["params"][policy["merchant_param"]]==intent["merchant_id"] and action["target"]==policy["payment_target"],"CORE_ACTION_BINDING")
        available=observation["world_state"]
        for key in policy["budget_path"]: available=available[key]
        require(type(available) is int and intent["amount_cents"]<=available,"CORE_NUMERICAL_BUDGET")
        seen={}
        for report in observation["reports"]:
            key=(report["fact_id"],report["agent"])
            require(key not in seen or seen[key] is report["value"],"REPORTER_EQUIVOCATION")
            seen[key]=report["value"]
        for role,decision in (("cage","ALLOW"),("veip","ELIGIBLE")):
            domain=f"commerce.{role}.authorization.v1"
            a=verify(bundle[role],policy[role+"_public_key"],domain)
            require(a["schema"]==domain and a["decision"]==decision and a["mission_hash"]==mh
                    and a["intent_hash"]==ih and a["observation_hash"]==oh and a["actor"]==mission["actor"]
                    and a["epoch"]==control["epoch"] and a["scope"]=="paypal.sandbox.orders.create_capture"
                    and a["expires_at"]>now and 0<=now-a["observed_at"]<=policy["max_age_seconds"],"EXTERNAL_AUTHORITY_DENIED")
        require(digest("commerce.prerequisites.v1",observation["records"])==intent["prerequisites_hash"],"PREREQUISITE_RECORDS_MISMATCH")
        calculated=self.runtime.calculate(observation,policy,now)
        require(all(s=="ACCEPTED_FOR_EVALUATION" for s in calculated["submissions"]),"CORE_REPORT_SIGNATURE_OR_IDENTITY")
        require(set(policy["obligation_task_map"].values())<=set(calculated["task_ids"]),"MISSING_ORIGINAL_CORE_TASK")
        require(calculated["task_actor"]==mission["actor"] and calculated["directive"]["directive"]=="ACT","CORE_NOT_ACT")
        require(calculated["world_id"]==mission["mission_id"] and calculated["task_write_tool"]==action["tool"]
                and calculated["parameter_status"]==200 and calculated["native_preconditions_ok"],"CORE_TOOL_PRECONDITIONS")
        require(calculated["all_facts_true"] and all(calculated["predicate_truths"]),"CORE_NOT_EARNED_TRUE")
        require(observation["claimed_directive"]==calculated["directive"],"CORE_DIRECTIVE_MISMATCH")
        if hasattr(self.sources,"profile_hash"):
            require(calculated["qualified"]["lean"]["verdict"]=="T","QUALIFIED_CORE_NOT_T")
        source_manifest=calculated["source_manifest"]
        # Exclude evaluation clock/digests from the permit binding. Every dispatch
        # still recomputes freshness; only stable derived facts are hash-bound.
        summary={k:calculated[k] for k in ("directive","task_actor","task_dependencies","task_write_tool","world_id","task_ids",
                 "parameter_status","native_preconditions_ok","predicate_truths","differential","mismatches","all_facts_true")}
        if "qualified" in calculated: summary["qualified"]=calculated["qualified"]
        return {"schema":"commerce.bridge.assessment.v1","decision":"ALLOW_ISSUER_REQUEST_ONLY",
                "mission_hash":mh,"intent_hash":ih,"observation_hash":oh,"policy_hash":digest("commerce.bridge.policy.v1",policy),
                "epoch":control["epoch"],"core":summary,"source_manifest_hash":self.manifest_hash(source_manifest),
                "authority_checked":["control","core","cage","veip"],"dispatch_authorized":False}


def lean_attestation(assessment,intent,now):
    """FORMAL-001: the qualified (`admission .full`, legion_decide) verdict of THIS assessment, bound to the exact
    intent. Only the qualified sources produce one; the v1 path (ZTL witness only) yields None, so the Gateway
    refuses its external POSTs (closure B-COM-2)."""
    q=(assessment.get("core") or {}).get("qualified") or {}
    verdict=(q.get("lean") or {}).get("verdict")
    if verdict is None: return None
    return {"lean_verdict":verdict,"action_digest":digest("commerce.intent.v1",intent),"evaluated_at":now,
            "alpha_payload_hash":q.get("payload_hash"),"source":"commerce_bridge.qualified"}


class BridgeAdmission(Admission):
    """A04 composition seam; exact frozen A04 payloads remain compatible."""
    def __init__(self,*args,sources,policy_envelope,bundle_dir,**kwargs):
        super().__init__(*args,**kwargs)
        self.sources=sources
        self.policy_envelope=policy_envelope
        self.bundle_dir=Path(bundle_dir)
        # WP3: under an immutable qualified session, readiness inputs are OS-locked
        # once and every predicate is still evaluated per request (locked_readiness).
        self.locked=None
        if getattr(sources,"snapshot",None) is not None:
            from .locked_readiness import LockedReadiness
            self.locked=LockedReadiness(self,Path(__file__).resolve().parent.parent/"docs/ADMISSION_INTERFACE.md",FROZEN_INTERFACE_SHA256)

    def close(self):
        if self.locked is not None: self.locked.close()

    def ready(self):
        if self.locked is not None: return self.locked.ready()
        a=super().ready()
        interface=Path(__file__).resolve().parent.parent/"docs/ADMISSION_INTERFACE.md"
        require(a["frozen_interface_sha256"]==hashlib.sha256(interface.read_bytes()).hexdigest()==FROZEN_INTERFACE_SHA256,"BRIDGE_INTERFACE_DRIFT")
        require(a["bridge_source_hashes"]==bridge_hashes() and a["runtime_source_hashes"]==adapter_hashes(),"BRIDGE_SOURCE_CORRESPONDENCE")
        require(a["native_source_manifest_hash"]==digest("commerce.bridge.sources.v1",self.sources.verify()),"BRIDGE_NATIVE_SOURCE_CORRESPONDENCE")
        require(a["bridge_policy_hash"]==digest("commerce.bridge.policy.v1",self.policy_envelope["payload"]),"ACCEPTED_POLICY_MISMATCH")
        require(a["upstream_acceptance"]["A01"]==a["upstream_acceptance"]["A02"]=="PASS", "UPSTREAM_NOT_ACCEPTED")
        expected={"A01":"eac9c274f62ba0ca7c7b4c1b109fb5e69ecbc53a09d29efb96cc98af3378551d",
                  "A02":"6ceacae3da2e6acb4bc010db67a0cfc03ac50c6236a77c9f8e66f9eacc93782f"}
        for order,tdd_hash in expected.items():
            name=f"UPSTREAM_{order}_ACCEPTANCE.json"
            require(name in a["evidence_hashes"],"UPSTREAM_ACCEPTANCE_ARTIFACT_MISSING")
            upstream=verify(json.loads((self.evidence_dir/name).read_text()),self.trust["acceptance"],"commerce.upstream.acceptance.v1")
            require(upstream["work_order"]==order and upstream["status"]=="PASS"
                    and upstream["tdd_sha256"]==tdd_hash and upstream["source_pins"]==self.source_pins
                    and upstream["mission_hash"]==self.policy_envelope["payload"]["mission_hash"]
                    and upstream["expires_at"]>self.clock() and upstream["evidence_hashes"],"UPSTREAM_NOT_ACCEPTED")
            require(all(a["evidence_hashes"].get(n)==h for n,h in upstream["evidence_hashes"].items()),"UPSTREAM_EVIDENCE_NOT_BOUND")
        return a

    def check(self,mission_envelope,permit_envelope,control):
        mission,permit,intent=super().check(mission_envelope,permit_envelope,control)
        locator=digest("commerce.permit.locator.v1",permit["permit_id"])+".json"
        path=self.bundle_dir/locator
        require(path.is_file(),"CORE_EVIDENCE_BUNDLE_MISSING")
        bundle=json.loads(path.read_text(encoding="utf-8-sig"))
        require(digest("commerce.bridge.bundle.v1",bundle)==permit["bridge_evidence_hash"],"CORE_EVIDENCE_BUNDLE_HASH")
        require(bundle["policy"]==self.policy_envelope,"CURRENT_BRIDGE_POLICY_MISMATCH")
        assessment=Bridge(self.trust,self.sources,self.clock).assess(mission_envelope,control,bundle,intent)
        require(digest("commerce.bridge.assessment.v1",assessment)==permit["bridge_assessment_hash"],"BRIDGE_ASSESSMENT_BINDING")
        require(assessment["observation_hash"]==permit["core_observation_hash"],"PERMIT_CORE_OBSERVATION_BINDING")
        # FORMAL-001: attestation the Gateway requires before any external POST (fresh, bound to this exact intent).
        self.last_decision=lean_attestation(assessment,intent,self.clock())
        return mission,permit,intent
