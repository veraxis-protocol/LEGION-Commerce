"""Thirteen preregistered A03 conformance cases, preserving every run and failure."""
import hashlib
import json
import platform
import re
import shutil
import sys
import time
import unittest
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from commerce_bridge.sources import Sources,build_directory
from tools.run_checks import Recording


def write(path,value):
    path.write_text(json.dumps(value,indent=2,sort_keys=True)+"\n",encoding="utf-8",newline="\n")


def main():
    out=ROOT/"bridge_evidence"/time.strftime("run-%Y%m%dT%H%M%SZ",time.gmtime())
    out.mkdir(parents=True,exist_ok=False)
    files=sorted(p for d in ("commerce_bridge","commerce_adapter","bridge_tests","bridge_integration_tests","tools","design") for p in (ROOT/d).rglob("*")
                 if p.is_file() and "__pycache__" not in p.parts)
    files += [ROOT/"docs/ADMISSION_INTERFACE.md",ROOT/"bridge-requirements.lock",ROOT/".gitattributes",ROOT/".gitignore"]
    source={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    write(out/"SOURCE_MANIFEST.json",source)
    with zipfile.ZipFile(out/"SOURCE_SNAPSHOT.zip","w",zipfile.ZIP_DEFLATED) as archive:
        for p in files: archive.write(p,p.relative_to(ROOT).as_posix())
    write(out/"SOURCE_PINS.json",Sources(ROOT/"runtime_sources").verify())
    write(out/"FROZEN_INPUTS.json",{"work_order":"WO-COM-A03-BRIDGE-001","mode":"CONFORMANCE_ONLY",
          "authority":"Test signatures only; no operational authority or acceptance installation",
          "real_execution":["Pinned Core methods","Compiled upstream Lean zverdict","SQLite A04 seam"],
          "repeat_N":10,"counted_tests":13,"stop":"Any false allow or mismatch => FAIL; unavailable operational evidence => BLOCKED",
          "python":sys.version,"platform":platform.platform(),"clock":"time.perf_counter_ns for elapsed; fixture clock=1000",
          "budget":"No PayPal transaction calls, no model/GPU calls"})
    a03=(ROOT/"design/WO-COM-A03_TECHNICAL_DESIGN_v1.1.md").read_text(encoding="utf-8")
    matrix=[]
    for line in a03.splitlines():
        if line.startswith("| A03-T"):
            p=[v.strip() for v in line.split("|")[1:-1]]
            matrix.append({"id":p[0],"action":p[1],"pass":p[2],"fail":p[3]})
    assert len(matrix)==13
    write(out/"TEST_MATRIX.json",matrix)
    for name in ("SWARM_BUILD.txt","ZTL_BUILD.txt","SOURCE_PREFLIGHT.json"):
        shutil.copyfile(build_directory()/name,out/name)
    suite=unittest.defaultTestLoader.discover(str(ROOT/"bridge_tests"),top_level_dir=str(ROOT))
    with (out/"TEST_OUTPUT.txt").open("w",encoding="utf-8",newline="\n") as stream:
        result=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=Recording).run(suite)
    from bridge_tests.test_conformance import TRACES
    write(out/"ACTUAL_RESULTS.json",result.records)
    write(out/"NEGATIVE_RESULTS.json",[r for r in result.records if not any(s in r["test"] for s in ("t01_","t02_","t13_"))])
    with (out/"RAW_EVENTS.jsonl").open("w",encoding="utf-8",newline="\n") as stream:
        for record in TRACES: stream.write(json.dumps(record,sort_keys=True)+"\n")
    attempts=[a for t in TRACES for a in t["bridge_attempts"]]
    for name,data in (("FORMAL_INPUTS.jsonl",[{"test":t["test"],"inputs":t["input"]} for t in TRACES]),
                      ("LEAN_VERDICTS.jsonl",[a["result"]["core"]["differential"] for a in attempts if "result" in a]),
                      ("RUNTIME_VERDICTS.jsonl",[a["result"]["core"] for a in attempts if "result" in a]),
                      ("MISMATCHES.jsonl",[a for a in attempts if a.get("denied")=="LEAN_RUNTIME_MISMATCH"]),
                      ("PERMIT_TRACE.jsonl",[{"mode":"CONFORMANCE_ONLY","operational_permits_issued":0,"assessment":a.get("result"),"denial":a.get("denied")} for a in attempts])):
        with (out/name).open("w",encoding="utf-8",newline="\n") as stream:
            for record in data: stream.write(json.dumps(record,sort_keys=True)+"\n")
    logs=(out/"SWARM_BUILD.txt").read_text(encoding="utf-8-sig")+"\n"+(out/"ZTL_BUILD.txt").read_text(encoding="utf-8-sig")
    (out/"LEAN_BUILD.txt").write_text(logs,encoding="utf-8",newline="\n")
    audit=[line for line in logs.splitlines() if "axiom" in line.lower()]
    (out/"AXIOM_AUDIT.txt").write_text("\n".join(audit)+"\n",encoding="utf-8",newline="\n")
    write(out/"PROOF_MAP.json",{"scope":"Source-checked upstream theorem premises only; not proof of Python, authority, report independence or PayPal effects",
         "runtime_rule":"ZSwarm.verdict via upstream zverdict; count-based, not ZLegion.merge",
         "theorems":["ZSwarm.safety","ZSwarm.root_safety","ZSwarm.verdict_is_threshold","ZRuntimeGate.gate_sound"],
         "dependencies":"Actual #print axioms output in AXIOM_AUDIT.txt",
         "required_external_premises":["Authenticated independent reporters and bounded faults","Original authorized mission and current authority","Accepted A01/A02 and independent A03 reviewer"]})
    timings=[v for t in TRACES for v in t.get("full_gate_duration_ms",[])]
    p95=sorted(timings)[max(0,__import__('math').ceil(len(timings)*.95)-1)] if timings else None
    write(out/"COUNTERS.json",{"conformance_cases":result.testsRun,"passed":sum(r["result"]=="PASS" for r in result.records),
          "failures":len(result.failures),"errors":len(result.errors),
          "observed_core_runtime_mismatches":sum(a.get("denied")=="LEAN_RUNTIME_MISMATCH" and not a["fault_injection"] for a in attempts),
          "deliberately_injected_mismatch_tests":sum(a.get("denied")=="LEAN_RUNTIME_MISMATCH" and a["fault_injection"] for a in attempts),
          "native_payment_dispatches":0,"native_captures":0,
          "operational_permits_issued":0,"operational_acceptance_signatures_created":0,"p95_full_gate_ms":p95,
          "performance_gate":"PASS" if p95 is not None and p95<=5 else "PERFORMANCE_FAIL_OR_NOT_MEASURED",
          "latency_scope":"Full local check including worker startup/source validation; excludes PayPal/model/stand"})
    unchanged=all(hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h for n,h in source.items())
    write(out/"WO_VERDICT.json",{"work_order":"WO-COM-A03-BRIDGE-001","overall":"BLOCKED","go_to_a04_native":False,
         "conformance":"PASS" if result.wasSuccessful() and unchanged else "FAIL","source_unchanged_during_run":unchanged,
         "acceptance":"NOT_SIGNED_NOT_SELF_ATTESTED","performance":"FUNCTIONAL_PASS_PERFORMANCE_FAIL" if result.wasSuccessful() and p95 and p95>5 else "SEE_COUNTERS",
         "operational_gaps":["Existing operator key paths/IDs pending","Genuine signed Core/CAGE/VEIP observations and original mission absent",
             "Independent A03 acceptance and A01/A02 accepted evidence absent","Authenticated buyer approval and native capture/readback not run"],
         "next_action":"Validate operator-supplied roots and signed evidence; external reviewer verifies and signs manifest; install only after all checks. If unavailable retain BLOCKED and zero native dispatches."})
    (out/"EVIDENCE_HASHES.sha256").write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in sorted(out.iterdir()) if p.is_file()),encoding="utf-8",newline="\n")
    print(json.dumps({"evidence":str(out),"tests":result.testsRun,"failures":len(result.failures),"errors":len(result.errors),"conformance_pass":result.wasSuccessful(),"overall":"BLOCKED","p95_full_gate_ms":p95}))
    return 0 if result.wasSuccessful() and unchanged else 1


if __name__=="__main__": raise SystemExit(main())
