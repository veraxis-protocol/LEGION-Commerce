"""Freeze every run before execution and preserve failed as well as passed runs."""
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))


def write(path,value):
    path.write_text(json.dumps(value,indent=2,sort_keys=True)+"\n",encoding="utf-8")


class Recording(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.records=[]
    def startTest(self,test):
        self.started=time.monotonic()
        super().startTest(test)
    def record(self,test,status,detail=None):
        self.records.append({"test":test.id(),"description":test.shortDescription(),"result":status,
                             "duration_ms":round((time.monotonic()-self.started)*1000,3),
                             "evidence_mode":"MECHANISM_ONLY", "detail":detail})
    def addSuccess(self,test):
        self.record(test,"PASS"); super().addSuccess(test)
    def addFailure(self,test,err):
        self.record(test,"FAIL",self._exc_info_to_string(err,test)); super().addFailure(test,err)
    def addError(self,test,err):
        self.record(test,"ERROR",self._exc_info_to_string(err,test)); super().addError(test,err)
    def addSubTest(self,test,subtest,err):
        if err:
            self.record(subtest,"FAIL" if issubclass(err[0],test.failureException) else "ERROR",self._exc_info_to_string(err,test))
        super().addSubTest(test,subtest,err)


def hashes(paths):
    return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def main():
    out=ROOT/"evidence"/time.strftime("run-%Y%m%dT%H%M%SZ",time.gmtime())
    out.mkdir(parents=True,exist_ok=False)
    files=sorted(p for directory in ("commerce_adapter","tests","tools","design") for p in (ROOT/directory).rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    files += [ROOT/"requirements.txt",ROOT/"requirements.lock",ROOT/".gitattributes"]
    frozen=hashes(files)
    write(out/"SOURCE_MANIFEST.json",frozen)
    write(out/"FROZEN_INPUTS.json",{"mode":"SCRIPTED_MECHANISM_ONLY", "provider":"FakePayPal", "authority":"SYNTHETIC_TEST_KEYS",
          "original_tdd":"design/WO-COM-A04_TECHNICAL_DESIGN_v1.1.md", "run_authority":"User WO-COM-A04 request 2026-11-05",
          "predeclared_rule":"Any failed assertion/error => local check FAIL. No mock result satisfies mandatory real Sandbox evidence.",
          "python":sys.version,"platform":platform.platform(),"source_manifest_sha256":hashlib.sha256((out/"SOURCE_MANIFEST.json").read_bytes()).hexdigest()})
    shutil.copyfile(ROOT/"design"/"TEST_MATRIX.json",out/"TEST_MATRIX.json")
    shutil.copyfile(ROOT/"design"/"SOURCE_PINS.json",out/"SOURCE_PINS.json")
    shutil.copyfile(ROOT/"design"/"REQUIREMENTS.json",out/"REQUIREMENTS.json")
    (out/"TDD_SHA256.txt").write_text(hashlib.sha256((ROOT/"design"/"WO-COM-A04_TECHNICAL_DESIGN_v1.1.md").read_bytes()).hexdigest()+"\n")
    write(out/"MODEL_OR_SCRIPT_MODE.json",{"mode":"SCRIPTED_TESTS", "model_autonomy":False,"core_modified":False})
    suite=unittest.defaultTestLoader.discover(str(ROOT/"tests"),top_level_dir=str(ROOT))
    with (out/"TEST_OUTPUT.txt").open("w",encoding="utf-8") as stream:
        result=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=Recording).run(suite)
    from tests.test_gateway import COLLECTED
    write(out/"ACTUAL_RESULTS.json",result.records)
    write(out/"NEGATIVE_RESULTS.json",[r for r in result.records if "t01_" not in r["test"]])
    with (out/"RAW_EVENTS.jsonl").open("w",encoding="utf-8") as stream:
        for record in COLLECTED: stream.write(json.dumps(record,sort_keys=True)+"\n")
    write(out/"NATIVE_READBACK.jsonl",{"status":"NOT_RUN", "reason":"A03 accepted evidence, trust roots, scoped permit and authenticated buyer approval required before Orders calls"})
    durations=[r["duration_ms"] for r in result.records]
    calls=[c for r in COLLECTED for c in r["provider_calls"]]
    events=[(r["test"],e) for r in COLLECTED for e in r["events"]]
    def unique_event(kind,predicate=lambda e:True):
        return len({(test,e["effect_id"]) for test,e in events if e["kind"]==kind and predicate(json.loads(e["data"]))})
    duplicate_posts=sum(max(0,sum(m=="POST" and p==path and p.endswith("/capture") for m,p,_ in r["provider_calls"])-1)
                        for r in COLLECTED for path in {p for m,p,_ in r["provider_calls"] if m=="POST" and p.endswith("/capture")})
    budget_violations=sum(sum(e["amount"] for e in r["effects"] if e["held"])>50000 for r in COLLECTED)
    write(out/"COUNTERS.json",{"tests_run":result.testsRun,"passed":sum(r["result"]=="PASS" for r in result.records),
          "failures":len(result.failures),"errors":len(result.errors),"mock_network_attempts_recorded":len(calls),
          "native_orders_created":0,"native_approved":0,"native_captures_confirmed":0,"native_transaction_network_attempts":0,
          "p95_test_duration_ms":sorted(durations)[int((len(durations)-1)*.95)],
          "p95_native_latency_ms":None,"mock_scope":"Per-case provider traces; nested subtest fixtures not included in event counters"})
    write(out/"MOCK_COUNTERS.json",{"scope":"Recorded primary gateway fixtures only; MOCK_ONLY",
          "orders_created":unique_event("ORDER_CREATED"),"approved":unique_event("ORDER_READBACK",lambda e:e["provider_status"]=="APPROVED"),
          "captures_confirmed":unique_event("CAPTURE_READBACK"),"network_attempts":len(calls),
          "duplicate_outbound_capture_attempts":duplicate_posts,"budget_violation_count":budget_violations,
          "reconciled_unknowns":len({(t,e["effect_id"]) for t,e in events if e["kind"]=="CAPTURE_READBACK"}
              & {(t,e["effect_id"]) for t,e in events if e["kind"]=="CAPTURE_UNCERTAIN"}),
          "api_errors":[json.loads(e["data"]) for _,e in events if e["kind"]=="NETWORK_ERROR"],
          "native_reconciled_unknowns":0,"native_duplicate_effects":0,"native_budget_violation_count":0,
          "native_scope":"No native Orders/capture attempts; zero counts are not evidence about a live campaign"})
    unchanged=frozen==hashes(files)
    write(out/"WO_VERDICT.json",{"work_order":"WO-COM-A04", "overall":"BLOCKED", "go_to_a05":False,
          "mechanism_checks":"PASS" if result.wasSuccessful() and unchanged else "FAIL", "source_unchanged_during_run":unchanged,
          "native_capture":"NOT_RUN", "blockers":["Accepted A03 T01-T13 evidence and independently provisioned authority roots absent",
          "No current signed mission/control/permit installed", "No authenticated buyer approval for an admitted order"],
          "next_action":"Obtain and verify A03 acceptance and scoped signed inputs, run preflight, then create+buyer approval+capture+separate readback. If unavailable keep external writes disabled.",
          "acceptance":"PASS only all A04 mandatory cases, native T01/T05/T06/T07 where supported, zero forbidden/duplicate/overbudget effects, exact ledger and readback joins"})
    (out/"EVIDENCE_HASHES.sha256").write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in sorted(out.iterdir()) if p.is_file()),encoding="utf-8")
    print(json.dumps({"evidence":str(out),"tests":result.testsRun,"failures":len(result.failures),"errors":len(result.errors),"local_pass":result.wasSuccessful(),"overall":"BLOCKED"}))
    return 0 if result.wasSuccessful() and unchanged else 1


if __name__=="__main__":
    raise SystemExit(main())
