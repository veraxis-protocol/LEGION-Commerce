"""CONFORMANCE_ONLY fixtures. Keys live in test memory; never installed operationally."""
import base64
import copy
import hashlib
import json
from datetime import datetime,timezone
from pathlib import Path
import rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from commerce_adapter.contracts import canonical,digest
from commerce_adapter.gateway import Gateway
from commerce_bridge.admission import Bridge,BridgeAdmission
from commerce_bridge.sources import Sources,adapter_hashes,bridge_hashes
from tests.helpers import FakePayPal

ROOT=Path(__file__).resolve().parent.parent


class TracedBridge(Bridge):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.attempts=[]

    def assess(self,*args,**kwargs):
        from commerce_adapter.contracts import Denied
        entry={"arguments":copy.deepcopy(args),"keywords":copy.deepcopy(kwargs),"now":self.clock(),
               "fault_injection":self.runtime.witness is not None and self.runtime.witness.__class__.__name__!="LeanWitness"}
        try:
            result=super().assess(*args,**kwargs)
            entry["result"]=result
            return result
        except Denied as error:
            entry["denied"]=str(error)
            raise
        finally:
            self.attempts.append(entry)


class Fixture:
    def __init__(self,root):
        self.root=Path(root)
        self.now=1000
        self.sources=Sources(ROOT/"runtime_sources")
        self.keys={role:Ed25519PrivateKey.generate() for role in ("control","admission","acceptance","core","cage","veip","reporter0","reporter1","reporter2","reporter3")}
        self.public={role:base64.b64encode(key.public_key().public_bytes_raw()).decode() for role,key in self.keys.items()}
        self.trust={role:self.public[role] for role in ("control","admission","acceptance")}
        self.world={"schema_version":"legion.world.v1","world_id":"CONFORMANCE-ONLY-COMMERCE",
            "initial_state":{"budget":{"available_cents":50000}},
            "tools":{"paypal.capture":{"kind":"write","target":"MERCHANT1","params":{
               "amount_cents":{"type":"integer","min":1,"max":50000},"merchant_id":{"type":"string","enum":["MERCHANT1"]}},"native_preconditions":[]}},
            "roles":[{"agent_id":"actor-1","tasks":[
              {"task_id":"observe","kinds":["READ"],"depends_on":[]},
              {"task_id":"analyze","kinds":["ANALYZE"],"depends_on":["observe"]},
              {"task_id":"pay","kinds":["WRITE"],"depends_on":["observe","analyze"],"write_tool":"paypal.capture"}]}]}
        self.records=[{"seq":1,"kind":"OBSERVATION","agent_id":"actor-1","task_id":"observe"},
                      {"seq":2,"kind":"ANALYSIS_RECORD","agent_id":"actor-1","task_id":"analyze"}]
        self.intent={"obligation_id":"pay","effect_slot":"bundle","actor":"actor-1","merchant_id":"MERCHANT1",
                     "quote_hash":digest("fixture.quote",{"amount":100}),"prerequisites_hash":digest("commerce.prerequisites.v1",self.records),
                     "currency":"USD","amount_cents":100}
        self.mission={"schema":"commerce.mission.v1","mission_id":self.world["world_id"],"contract_version":"1",
            "contract_hash":digest("fixture.contract",self.world),"epoch":1,"actor":"actor-1","deadline":1800,
            "currency":"USD","cap_cents":50000,"obligations":["observe","analyze","pay"],"merchant_ids":["MERCHANT1"],"effects":[self.intent]}
        self.mh=digest("commerce.mission.v1",self.mission)
        self.control={"schema":"commerce.control.v1","sequence":1,"mission_id":self.mission["mission_id"],"mission_hash":self.mh,
                      "epoch":1,"active":True,"expires_at":1800,"revoked_permits":[],"revoked_actors":[]}
        self.policy={"schema":"commerce.bridge.policy.v1","mission_hash":self.mh,"expires_at":1800,"f":1,"max_age_seconds":300,
            "source_pins":Bridge.design_pins(),"original_obligations":self.mission["obligations"],
            "obligation_task_map":{k:k for k in self.mission["obligations"]},"world_hash":digest("commerce.core.world.v1",self.world),
            "core_public_key":self.public["core"],"cage_public_key":self.public["cage"],"veip_public_key":self.public["veip"],
            "payment_tool":"paypal.capture","payment_target":"MERCHANT1","amount_param":"amount_cents","merchant_param":"merchant_id","budget_path":["budget","available_cents"],
            "reporters":[{"agent":f"r{i}","root":f"root{i}","basis":f"basis{i}","source_id":f"CONFORMANCE_SOURCE_{i}","public_key":self.public[f"reporter{i}"]} for i in range(3)]}
        self.observation={"schema":"commerce.core.observation.v1","mission_hash":self.mh,"intent_hash":digest("commerce.intent.v1",self.intent),
            "epoch":1,"attempt_state":"RUNNING","observed_at":1000,"contract_hash":self.mission["contract_hash"],"quote_hash":self.intent["quote_hash"],
            "world":self.world,"world_state":copy.deepcopy(self.world["initial_state"]),"task_id":"pay","records":self.records,"effects":[],
            "controller_state":{"pay":{"status":"PENDING","command_id":None,"epoch":0,"effect_id":None,"unknown":False}},
            "action":{"tool":"paypal.capture","target":"MERCHANT1","params":{"amount_cents":100,"merchant_id":"MERCHANT1"}},"reports":[]}
        command=digest_dummy_command(self.mission["mission_id"],"pay",1)
        self.observation["claimed_directive"]={"directive":"ACT","task_id":"pay","epoch":1,"command_id":command}
        self.set_reports([True,True,True])
        self.bridge=TracedBridge(self.trust,self.sources,lambda:self.now)
        self.last=None

    def sign(self,role,domain,payload):
        return {"payload":copy.deepcopy(payload),"signature":base64.b64encode(self.keys[role].sign(domain.encode()+b"\0"+canonical(payload))).decode()}

    def set_reports(self,values,observed_at=1000):
        reports=[]
        stamp=datetime.fromtimestamp(observed_at,timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for i,value in enumerate(values):
            body={"fact_id":"pay:deps_closed","agent":f"r{i}","root":f"root{i}","value":value,"observed_at":stamp}
            reports.append({**body,"sig":self.keys[f"reporter{i}"].sign(rfc8785.dumps(body)).hex()})
        self.observation["reports"]=reports

    def bundle(self):
        oh=digest("commerce.core.observation.v1",self.observation)
        result={"policy":self.sign("control","commerce.bridge.policy.v1",self.policy),
                "observation":self.sign("core","commerce.core.observation.v1",self.observation)}
        for role,decision in (("cage","ALLOW"),("veip","ELIGIBLE")):
            domain=f"commerce.{role}.authorization.v1"
            payload={"schema":domain,"decision":decision,"mission_hash":self.mh,"intent_hash":digest("commerce.intent.v1",self.intent),
                     "observation_hash":oh,"actor":"actor-1","epoch":1,"scope":"paypal.sandbox.orders.create_capture","expires_at":self.now+400,"observed_at":self.now}
            result[role]=self.sign(role,domain,payload)
        return result

    def assess(self,bundle=None,intent=None):
        result=self.bridge.assess(self.sign("control","commerce.mission.v1",self.mission),self.control,bundle or self.bundle(),intent or self.intent)
        self.last=result
        return result

    def gateway(self):
        bundle=self.bundle()
        assessment=self.assess(bundle)
        evidence=self.root/"accepted"
        evidence.mkdir()
        required=["PROOF_MAP.json","LEAN_BUILD.txt","AXIOM_AUDIT.txt","FORMAL_INPUTS.jsonl","LEAN_VERDICTS.jsonl",
                  "RUNTIME_VERDICTS.jsonl","MISMATCHES.jsonl","PERMIT_TRACE.jsonl","NEGATIVE_RESULTS.json"]
        for name in required: (evidence/name).write_text("CONFORMANCE_ONLY; NOT INDEPENDENT ACCEPTANCE")
        pins=json.loads((ROOT/"design/SOURCE_PINS.json").read_text())
        for order,tdd in (("A01","eac9c274f62ba0ca7c7b4c1b109fb5e69ecbc53a09d29efb96cc98af3378551d"),
                          ("A02","6ceacae3da2e6acb4bc010db67a0cfc03ac50c6236a77c9f8e66f9eacc93782f")):
            result_name=f"{order}_RESULT.json"
            (evidence/result_name).write_text("CONFORMANCE_ONLY; NOT UPSTREAM EVIDENCE")
            payload={"schema":"commerce.upstream.acceptance.v1","work_order":order,"status":"PASS","tdd_sha256":tdd,
                     "source_pins":pins["source_pins"],"mission_hash":self.mh,"expires_at":self.now+800,
                     "evidence_hashes":{result_name:hashlib.sha256((evidence/result_name).read_bytes()).hexdigest()}}
            acceptance_name=f"UPSTREAM_{order}_ACCEPTANCE.json"
            (evidence/acceptance_name).write_text(json.dumps(self.sign("acceptance","commerce.upstream.acceptance.v1",payload)))
            required.extend([result_name,acceptance_name])
        acceptance={"schema":"commerce.a03.acceptance.v1","status":"PASS","tdd_sha256":pins["a03_tdd_sha256"],"source_pins":pins["source_pins"],
            "expires_at":self.now+800,"tests":{f"A03-T{i:02}":"PASS" for i in range(1,14)},"adapter_schema":"commerce.a04.adapter.v1",
            "runtime_source_hashes":adapter_hashes(),"bridge_source_hashes":bridge_hashes(),"native_source_manifest_hash":digest("commerce.bridge.sources.v1",self.sources.verify()),
            "frozen_interface_sha256":hashlib.sha256((ROOT/"docs/ADMISSION_INTERFACE.md").read_bytes()).hexdigest(),
            "bridge_policy_hash":digest("commerce.bridge.policy.v1",self.policy),"upstream_acceptance":{"A01":"PASS","A02":"PASS"},
            "evidence_hashes":{name:hashlib.sha256((evidence/name).read_bytes()).hexdigest() for name in required},"fixture":"CONFORMANCE_ONLY"}
        acceptance_envelope=self.sign("acceptance","commerce.a03.acceptance.v1",acceptance)
        permit={"schema":"commerce.permit.v1","permit_id":"CONFORMANCE-PERMIT","issued_at":self.now-100,"expires_at":self.now+400,"quote_expires_at":self.now+500,
            "mission_hash":self.mh,"acceptance_hash":digest("commerce.a03.acceptance.v1",acceptance),"epoch":1,
            "intent":self.intent,"scope":"paypal.sandbox.orders.create_capture","verdict":"T","lean_rule_earned":True,"controller_act":True,
            "cage_allow":True,"veip_eligible":True,"prerequisites_satisfied":True,
            "bridge_evidence_hash":digest("commerce.bridge.bundle.v1",bundle),"bridge_assessment_hash":digest("commerce.bridge.assessment.v1",assessment),
            "core_observation_hash":assessment["observation_hash"]}
        bundles=self.root/"bundles"; bundles.mkdir()
        (bundles/(digest("commerce.permit.locator.v1",permit["permit_id"])+".json")).write_text(json.dumps(bundle))
        admission=BridgeAdmission(self.trust,acceptance_envelope,evidence,pins["a03_tdd_sha256"],pins["source_pins"],clock=lambda:self.now,
            sources=self.sources,policy_envelope=bundle["policy"],bundle_dir=bundles)
        provider=FakePayPal()
        gateway=Gateway(self.root/"ledger.db",admission,provider)
        gateway.apply_control(self.mission["mission_id"],self.sign("control","commerce.control.v1",self.control))
        return gateway,provider,self.sign("control","commerce.mission.v1",self.mission),self.sign("admission","commerce.permit.v1",permit)


def digest_dummy_command(mission,task,epoch):
    # Exact public Core serialization; worker independently recomputes the command.
    value=json.dumps({"mission":mission,"task_id":task,"epoch":epoch},sort_keys=True,separators=(",",":"),ensure_ascii=False)
    return hashlib.sha256(value.encode()).hexdigest()
