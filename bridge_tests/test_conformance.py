import copy
import itertools
import json
import tempfile
import time
import unittest
import threading
from unittest.mock import patch
from commerce_adapter.contracts import Denied,digest
from commerce_bridge.runtime import CoreRuntime
from commerce_bridge.witness import LeanWitness
from commerce_bridge.sources import build_directory
from .fixtures import Fixture,ROOT

TRACES=[]


class Conformance(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.f=Fixture(self.tmp.name)
        self.trace={"test":self.id(),"mode":"REAL_PINNED_CORE_AND_LEAN_WITH_CONFORMANCE_INPUTS",
                    "authority":"TEST_KEYS_ONLY_NO_OPERATIONAL_INSTALL", "native_payment_dispatches":0}

    def tearDown(self):
        self.trace["input"]={"mission":self.f.mission,"control":self.f.control,"bundle":self.f.bundle(),"intent":self.f.intent}
        self.trace["last_assessment"]=self.f.last
        self.trace["public_test_trust"]=self.f.trust
        self.trace["bridge_attempts"]=self.f.bridge.attempts
        TRACES.append(self.trace)
        self.tmp.cleanup()

    def test_t01_compile_pinned_and_axiom_audit(self):
        """A03-T01: real pinned Lean kernel builds and axiom transcripts."""
        manifest=self.f.sources.verify()
        for name in ("SWARM_BUILD.txt","ZTL_BUILD.txt"):
            log=(build_directory()/name).read_text(encoding="utf-8-sig")
            self.assertIn("Build completed successfully",log)
            self.assertNotIn("sorryAx",log)
            self.assertNotIn("error:",log)
        ztl=(build_directory()/"ZTL_BUILD.txt").read_text(encoding="utf-8-sig")
        swarm=(build_directory()/"SWARM_BUILD.txt").read_text(encoding="utf-8-sig")
        self.assertIn("'ZRuntimeGate.gate_sound' does not depend on any axioms",ztl)
        self.assertIn("'ZSwarm.root_safety' does not depend on any axioms",swarm)
        self.trace["source_manifest"]=manifest

    def test_t02_real_core_lean_agree_and_a04_seam(self):
        """A03-T02: real methods and compiled witness, test signatures only."""
        assessment=self.f.assess()
        self.assertTrue(assessment["core"]["all_facts_true"])
        self.assertEqual(assessment["core"]["mismatches"],0)
        self.assertFalse(assessment["dispatch_authorized"])
        gateway,provider,mission,permit=self.f.gateway()
        eid=gateway.reserve(mission,permit)
        gateway.create(eid)
        provider.approve()
        self.assertEqual(gateway.capture(eid),"CAPTURE_CONFIRMED")
        self.assertEqual(provider.capture_count(),1)
        self.trace["a04_mechanism_only_calls"]=provider.calls

    def test_t03_unknown_missing_evidence(self):
        """A03-T03: no report set is not T."""
        self.f.set_reports([])
        with self.assertRaisesRegex(Denied,"CORE_NOT_EARNED_TRUE"): self.f.assess()
        self.f.set_reports([True,True,True])
        self.f.observation["controller_state"]["pay"]["status"]="UNKNOWN"
        raw=self.f.bridge.runtime.calculate(self.f.observation,self.f.policy,self.f.now)
        self.trace["raw_core_on_malformed_state"]=raw["directive"]
        with self.assertRaisesRegex(Denied,"UNKNOWN_OR_MALFORMED_CORE_STATE"): self.f.assess()
        self.f.observation["controller_state"]["pay"]["status"]="PENDING"
        self.f.observation["controller_state"]["pay"]["unknown"]=True
        with self.assertRaisesRegex(Denied,"UNKNOWN_OR_MALFORMED_CORE_STATE"): self.f.assess()

    def test_t04_contradiction_equivocation_and_fake_independence(self):
        """A03-T04: contradictory roots, equivocation, one-source Sybils denied."""
        self.f.set_reports([True,False,False])
        with self.assertRaisesRegex(Denied,"CORE_NOT_EARNED_TRUE"): self.f.assess()
        self.f.policy["reporters"].append({"agent":"r3","root":"root3","basis":"basis3","source_id":"CONFORMANCE_SOURCE_3","public_key":self.f.public["reporter3"]})
        self.f.set_reports([True,True,False,False])
        computed=self.f.bridge.runtime.calculate(self.f.observation,self.f.policy,self.f.now)
        self.assertEqual(computed["facts"][0]["state"],"NOT_DECIDED_BOUND_BROKEN")
        self.trace["contradictory_core_state"]=computed["facts"][0]["state"]
        with self.assertRaisesRegex(Denied,"CORE_NOT_EARNED_TRUE"): self.f.assess()
        self.f.policy["reporters"].pop()
        self.f.set_reports([True,True,True])
        other=copy.deepcopy(self.f.observation["reports"][0]); other["value"]=False
        self.f.observation["reports"].append(other)
        with self.assertRaisesRegex(Denied,"REPORTER_EQUIVOCATION"): self.f.assess()
        self.f.set_reports([True,True,True])
        self.f.policy["reporters"][1]["source_id"]=self.f.policy["reporters"][0]["source_id"]
        with self.assertRaisesRegex(Denied,"SHARED_SOURCE_FAKE_INDEPENDENCE"): self.f.assess()

    def test_t05_permutations(self):
        """A03-T05: [T,F,T]/[F,T,T] and all report order permutations."""
        self.f.set_reports([True,False,True])
        reports=copy.deepcopy(self.f.observation["reports"])
        results=[]
        for order in itertools.permutations(reports):
            self.f.observation["reports"]=list(order)
            results.append(self.f.assess()["core"]["differential"])
        self.assertTrue(all(r==results[0] for r in results))
        self.trace["permutations_checked"]=len(results)

    def test_t06_stale_observations_and_reports(self):
        """A03-T06: both signed snapshot and individual report freshness."""
        self.f.observation["observed_at"]=600
        with self.assertRaisesRegex(Denied,"STALE_CORE_OBSERVATION"): self.f.assess()
        self.f.observation["observed_at"]=1000
        self.f.set_reports([True,True,True],observed_at=600)
        with self.assertRaisesRegex(Denied,"CORE_NOT_EARNED_TRUE"): self.f.assess()

    def test_t07_forged_signers_roots_actor(self):
        """A03-T07: forged envelope/report, spoofed root and actor denied."""
        bundle=self.f.bundle(); bundle["observation"]["signature"]="Zm9yZ2Vk"
        with self.assertRaises(Denied): self.f.assess(bundle)
        self.f.observation["reports"][0]["sig"]="00"*64
        with self.assertRaisesRegex(Denied,"CORE_REPORT_SIGNATURE_OR_IDENTITY"): self.f.assess()
        self.f.set_reports([True,True,True]); self.f.observation["reports"][0]["root"]="spoof"
        with self.assertRaisesRegex(Denied,"CORE_REPORT_SIGNATURE_OR_IDENTITY"): self.f.assess()
        self.f.set_reports([True,True,True])
        gateway,provider,mission,permit=self.f.gateway()
        permit["payload"]["intent"]["actor"]="spoof"
        with self.assertRaises(Denied): gateway.reserve(mission,permit)
        self.assertEqual(provider.calls,[])

    def test_t08_revocation_epoch_and_current_boundary(self):
        """A03-T08: revoke after reserve; zero further calls."""
        gateway,provider,mission,permit=self.f.gateway()
        eid=gateway.reserve(mission,permit)
        self.f.control["sequence"]=2; self.f.control["revoked_permits"]=["CONFORMANCE-PERMIT"]
        gateway.apply_control(self.f.mission["mission_id"],self.f.sign("control","commerce.control.v1",self.f.control))
        with self.assertRaisesRegex(Denied,"REVOKED_PERMIT"): gateway.create(eid)
        self.assertEqual(provider.calls,[])
        self.f.control["epoch"]=2
        with self.assertRaisesRegex(Denied,"CURRENT_AUTHORITY_DENIED"): self.f.assess()

    def test_t09_changed_amount_merchant_and_action(self):
        """A03-T09: immutable mission/intent/action digests bind exact terms."""
        for field,value in (("amount_cents",200),("merchant_id","OTHER")):
            intent=copy.deepcopy(self.f.intent); intent[field]=value
            with self.assertRaisesRegex(Denied,"ORIGINAL_INTENT_MISMATCH"): self.f.assess(intent=intent)
        self.f.observation["action"]["params"]["amount_cents"]=200
        with self.assertRaisesRegex(Denied,"CORE_ACTION_BINDING"): self.f.assess()

    def test_t10_act_but_budget_fails(self):
        """A03-T10: numerical Core budget is an independent conjunct."""
        self.f.observation["world_state"]["budget"]["available_cents"]=99
        computed=self.f.bridge.runtime.calculate(self.f.observation,self.f.policy,self.f.now)
        self.assertEqual(computed["directive"]["directive"],"ACT")
        with self.assertRaisesRegex(Denied,"CORE_NUMERICAL_BUDGET"): self.f.assess()
        self.trace["actual_core_directive_before_budget_guard"]=computed["directive"]

    def test_t11_witness_absence_timeout_error_mismatch(self):
        """A03-T11: injected witness failures never fall back to Python allow."""
        with self.assertRaisesRegex(Denied,"LEAN_UNAVAILABLE"): LeanWitness(self.f.root/"absent.exe")
        for code in ("LEAN_TIMEOUT","LEAN_UNAVAILABLE"):
            class Broken:
                def evaluate(self,*args): raise Denied(code)
            self.f.bridge.runtime=CoreRuntime(self.f.sources,Broken())
            with self.assertRaisesRegex(Denied,code): self.f.assess()
        class Wrong:
            def evaluate(self,*args): return "F"
        self.f.bridge.runtime=CoreRuntime(self.f.sources,Wrong())
        with self.assertRaisesRegex(Denied,"LEAN_RUNTIME_MISMATCH"): self.f.assess()
        # Exercise the actual protocol deadline, including blocked stdin.
        class BlockedProcess:
            def __init__(self): self.stopped=threading.Event(); self.stdin=self; self.stdout=self
            def write(self,*args): self.stopped.wait(3); raise BrokenPipeError()
            def flush(self): return None
            def poll(self): return 0 if self.stopped.is_set() else None
            def kill(self): self.stopped.set()
            def wait(self,**kwargs): self.stopped.wait(3); return 0
            def close(self): return None
            def __iter__(self): self.stopped.wait(3); return iter(())
        with patch("commerce_bridge.witness.subprocess.Popen",return_value=BlockedProcess()):
            with LeanWitness("CONFORMANCE_ONLY",timeout=.05) as bounded:
                start=time.monotonic()
                with self.assertRaisesRegex(Denied,"LEAN_TIMEOUT"): bounded.evaluate(1,["T","T"])
                self.assertLess(time.monotonic()-start,1)

    def test_t12_empty_inventory_missing_dependency_no_done(self):
        """A03-T12: original obligations and required causal records cannot vanish."""
        original=self.f.mission["obligations"]
        self.f.mission["obligations"]=[]
        with self.assertRaisesRegex(Denied,"EMPTY_OR_DUPLICATE_OBLIGATIONS"): self.f.assess()
        self.f.mission["obligations"]=original
        self.f.records.pop()
        with self.assertRaisesRegex(Denied,"PREREQUISITE_RECORDS_MISMATCH"): self.f.assess()

    def test_t13_repeat_identical_evidence(self):
        """A03-T13: N=10 repeated real Core/Lean decisions, exact stable binding."""
        records=[]; hashes=[]; durations=[]
        with LeanWitness(self.f.sources.binary) as witness:
            self.f.bridge.runtime=CoreRuntime(self.f.sources,witness)
            for _ in range(10):
                started=time.perf_counter_ns()
                result=self.f.assess()
                durations.append((time.perf_counter_ns()-started)/1e6)
                hashes.append(digest("commerce.bridge.assessment.v1",result))
                records.append(result["core"]["differential"])
        self.assertEqual(len(set(hashes)),1)
        self.trace.update(repeats=10,mismatches=0,full_gate_duration_ms=durations,
                          measurement="Includes isolated Core worker startup, pinned-source validation and native witness; excludes PayPal/LLM/search")
