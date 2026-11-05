"""Preserved, deterministic v2 qualification. No Core code is copied or published.

Usage: python tools/qualify_commerce.py [--replay evidence-directory]
Requires operator-provided local qualified source checkout, native Lean builds,
and the existing pinned witnesses. Does not use PayPal credentials or network.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import math
import platform
import random
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
# v3: the oracle module is loaded from hashed source, never from a bytecode cache.
sys.dont_write_bytecode=True
sys.pycache_prefix=str(Path(tempfile.gettempdir())/("legion-nopyc-"+str(time.time_ns())))
sys.path.insert(0,str(ROOT))
from commerce_adapter.contracts import Denied,digest
from commerce_bridge.qualified import QualifiedSources,JsonProcess,QUALIFIED
from tools.run_checks import Recording


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def write(path,value): path.write_text(json.dumps(value,indent=2,sort_keys=True)+"\n",encoding="utf-8",newline="\n")
def lines(path,rows): path.write_text("".join(json.dumps(r,sort_keys=True)+"\n" for r in rows),encoding="utf-8",newline="\n")
def load(path):
    spec=importlib.util.spec_from_file_location("qualified_generator",path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module
def pct(values): return sorted(values)[math.ceil(.95*len(values))-1]


def model_cases(module):
    rng=random.Random(20261105); rows=[]
    for i in range(1200):
        case,attack=module.gen_case(rng,i)
        case["journal"]={"tasks":[{"id":t["id"],"owner":t["owner"],"deps":t["deps"],"kinds":["DONE"],
            "na":t["id"] in case["state"]["closed"]} for t in case["spec"]["tasks"]],"records":[]}
        if i%40==0:
            case["state"]["issued"]=[]; attack="revoked"
        if i%40==1:
            for report in case["state"]["reports"]: report["time"]=0
            case["state"]["now"]=10000; attack="expired"
        if i%40==2:
            case["state"]["reports"].reverse(); attack="reordered:"+attack
        if i%40==3:
            case["req"]["target"]+=100; attack="changed-intent"
        rows.append({"id":i,"category":attack,"input":case})
    return rows


def run_models(rows,module,binary):
    results=[]
    with JsonProcess([binary]) as process:
        for row in rows:
            expected,grounds=module.oracle(row["input"])
            lean=process.call(row["input"])
            match=expected==lean["verdict"] and grounds==dict(lean["grounds"])
            category=row["category"].removeprefix("reordered:")
            forbidden=category not in ("none","missing_deps_noop")
            results.append({"id":row["id"],"category":row["category"],"input_sha256":digest("qualify.case.v2",row["input"]),
                "runtime":expected,"runtime_grounds":grounds,"lean":lean,"match":match,"false_authorization":forbidden and lean["verdict"]=="T"})
    return results


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--replay",type=Path); args=parser.parse_args()
    out=ROOT/"qualify_evidence"/time.strftime("replay-%Y%m%dT%H%M%SZ" if args.replay else "run-%Y%m%dT%H%M%SZ",time.gmtime())
    out.mkdir(parents=True)
    started=time.perf_counter_ns()
    sources=QualifiedSources(ROOT/"runtime_sources",immutable=True)
    ready_ms=(time.perf_counter_ns()-started)/1e6
    try:
        source_files=[p for folder in ("commerce_adapter","commerce_bridge","bridge_tests","tools","design") for p in (ROOT/folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts]
        source_hashes={p.relative_to(ROOT).as_posix():sha(p) for p in source_files}
        write(out/"COMMERCE_SOURCE_HASHES.json",source_hashes)
        write(out/"NATIVE_SOURCE_MANIFEST.json",sources.verify())
        module=load(sources.core/"formal/differential/diff_generated.py")
        if args.replay:
            recorded=json.loads((args.replay/"COMMERCE_SOURCE_HASHES.json").read_text())
            assert all(sha(ROOT/n)==h for n,h in recorded.items()),"Commerce replay source mismatch"
            assert json.loads((args.replay/"NATIVE_SOURCE_MANIFEST.json").read_text())==sources.verify(),"Native replay mismatch"
            rows=[json.loads(l) for l in (args.replay/"MATCHED_INPUTS.jsonl").read_text().splitlines()]
        else: rows=model_cases(module)
        lines(out/"MATCHED_INPUTS.jsonl",rows)  # freeze before first execution
        write(out/"FROZEN_RUN.json",{"scope":"CONFORMANCE_ONLY_NO_OPERATIONAL_AUTHORITY","seed":20261105,"cases":len(rows),
            "core":QUALIFIED,"python":sys.version,"platform":platform.platform(),"clock":"perf_counter_ns", "cold_readiness_ms":ready_ms,
            "excluded":"PayPal network, model inference, ztl-stand search only","profile_sha256":sources.profile_hash,
            "stop":"Any unexpected mismatch or false allow => FAIL; no production keys or native dispatch"})
        model_results=run_models(rows,module,sources.decider)
        lines(out/"MATCHED_RESULTS.jsonl",model_results)
        # Repeat every frozen A03 method without changing its assertions.
        import bridge_tests.test_conformance as tests
        from bridge_tests.fixtures import Fixture
        def fixture(root): return Fixture(root,qualified_sources=sources)
        tests.Fixture=fixture
        suite=unittest.defaultTestLoader.loadTestsFromTestCase(tests.Conformance)
        with (out/"A03_TEST_OUTPUT.txt").open("w",encoding="utf-8") as stream:
            result=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=Recording).run(suite)
        write(out/"A03_RESULTS.json",result.records); lines(out/"A03_RAW_EVENTS.jsonl",tests.TRACES)
        # Matched actual runtime-alpha/journal cases. Finite oracle comparison,
        # not a proof of alpha or of the full world-server execution boundary.
        alpha_inputs=[]
        with tempfile.TemporaryDirectory() as temp:
            f=fixture(temp)
            categories=["valid","forged","revoked","stale_epoch","changed_intent","unknown","inconsistent","reordered","replayed_nonce","fenced"]
            for i in range(1000):
                obs=copy.deepcopy(f.observation); category=categories[i%len(categories)]; b=obs["core_boundary"]
                if category in ("forged","revoked"): b["permit_verified"]=False
                if category=="stale_epoch": b["permit"]["epoch"]=0
                if category=="changed_intent": obs["action"]["params"]["amount_cents"]=200
                if category=="unknown": obs["records"][0]["facts"]={"missing":None}
                if category=="inconsistent": b["permit"]["actor"]="OTHER"
                if category=="reordered": obs["records"].reverse()
                if category=="replayed_nonce": b["used_nonces"]=[b["permit"]["nonce"]]
                if category=="fenced": b["fenced"]=[b["permit"]["command_id"]]
                alpha_inputs.append({"id":i,"category":category,"observation":obs,"policy":f.policy,"now":f.now})
            lines(out/"ALPHA_INPUTS.jsonl",alpha_inputs)
            alpha_results=[]
            for row in alpha_inputs:
                try:
                    calculated=sources.session.calculate(row["observation"],row["policy"],row["now"])
                    q=calculated["qualified"]
                    # v3: match also asserts the specified verdict (T only for valid), not merely no exception.
                    alpha_results.append({"id":row["id"],"category":row["category"],"qualified":q,
                        "match":(q["lean"]["verdict"]=="T")==(row["category"]=="valid"),
                        "false_authorization":row["category"]!="valid" and q["lean"]["verdict"]=="T"})
                except Denied as error:
                    alpha_results.append({"id":row["id"],"category":row["category"],"match":False,"error":str(error),"false_authorization":False})
            lines(out/"ALPHA_RESULTS.jsonl",alpha_results)
            # Full Admission.check, not just a Lean call or Bridge.assess. Includes
            # all acceptance file hashes, signatures, source binding and replay.
            gateway,provider,mission,permit=f.gateway()
            durations=[]; phases=[]
            with gateway.transaction() as db:
                for i in range(110):
                    start=time.perf_counter_ns(); gateway.check(db,mission,permit)
                    duration=(time.perf_counter_ns()-start)/1e6
                    if i>=10: durations.append(duration); phases.append(dict(sources.session.last_profile))
            write(out/"LATENCY.json",{"baseline_assess_p95_ms":1105.3761,"baseline_scope":"Bridge.assess N=10, cold Core/source checks",
                "after_p95_ms":pct(durations),"after_scope":"Gateway.check including current control signature and complete BridgeAdmission.check; SQLite transaction already open",
                "N":len(durations),"warmup":10,"samples_ms":durations,"phases":phases,"cold_readiness_ms":ready_ms,
                "gate":"PASS" if pct(durations)<=5 else "FUNCTIONAL_PASS_PERFORMANCE_FAIL", "transaction_reservation_not_in_admission_metric":True})
            # No admission-result cache: mutate current controls and clock after warmup.
            negatives=[]
            for name,mutation in (("revoked",lambda c:c["revoked_permits"].append("CONFORMANCE-PERMIT")),
                                  ("epoch",lambda c:c.update(epoch=2)),("inactive",lambda c:c.update(active=False))):
                control=copy.deepcopy(f.control); mutation(control)
                try: gateway.admission.check(mission,permit,control); negatives.append({"case":name,"pass":False})
                except Denied as error: negatives.append({"case":name,"pass":True,"denial":str(error)})
            f.now=1700
            try: gateway.admission.check(mission,permit,f.control); negatives.append({"case":"expired","pass":False})
            except Denied as error: negatives.append({"case":"expired","pass":True,"denial":str(error)})
            f.now=1000
            target=sources.core/"tests/legion_struct/struct_controller.py"
            try:
                with target.open("r+b"): pass
                negatives.append({"case":"immutable_source_write","pass":False})
            except PermissionError: negatives.append({"case":"immutable_source_write","pass":True})
            write(out/"WARM_NEGATIVES.json",negatives)
        summary={"A03_passed":sum(r["result"]=="PASS" for r in result.records),"A03_total":result.testsRun,
            "model_matched_cases":len(model_results),"model_mismatches":sum(not r["match"] for r in model_results),
            "alpha_matched_cases":len(alpha_results),"alpha_mismatches":sum(not r["match"] for r in alpha_results),
            "false_authorizations":sum(r["false_authorization"] for r in model_results+alpha_results),
            "warm_negative_failures":sum(not n["pass"] for n in negatives),"full_admission_p95_ms":pct(durations),
            "performance":"PASS" if pct(durations)<=5 else "FUNCTIONAL_PASS_PERFORMANCE_FAIL",
            "source_unchanged":all(sha(ROOT/n)==h for n,h in source_hashes.items()),
            "operational_acceptance":"BLOCKED_OWNER_KEYS_AND_INDEPENDENT_ACCEPTANCE_ABSENT",
            "native_dispatches":0,"captures":0,"go_to_a05":False,"overall":"BLOCKED"}
        write(out/"SUMMARY.json",summary)
        write(out/"EVIDENCE_HASHES.json",{p.name:sha(p) for p in out.iterdir() if p.is_file()})
        print(json.dumps({"directory":str(out),**summary}))
        return int(not result.wasSuccessful() or summary["model_mismatches"] or summary["alpha_mismatches"] or summary["false_authorizations"] or summary["warm_negative_failures"])
    finally: sources.close()


if __name__=="__main__": raise SystemExit(main())
