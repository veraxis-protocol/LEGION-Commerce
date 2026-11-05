"""Measure known overhead without changing decision logic or authorizing payments."""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from bridge_tests.fixtures import Fixture
from commerce_bridge.witness import LeanWitness


def main():
    out=ROOT/"bridge_evidence"/time.strftime("profile-%Y%m%dT%H%M%SZ",time.gmtime())
    out.mkdir(parents=True,exist_ok=False)
    (out/"FROZEN_INPUTS.json").write_text(json.dumps({"mode":"CONFORMANCE_ONLY","repetitions":5,
        "phases":["pinned_source_check","source_checked_core_runtime_and_witness","persistent_native_witness_only"],
        "purpose":"Attribution only; no optimization or operational authority"},indent=2)+"\n")
    rows=[]
    with tempfile.TemporaryDirectory() as tmp:
        f=Fixture(tmp)
        with LeanWitness(f.sources.binary) as witness:
            f.bridge.runtime.witness=witness
            for _ in range(5):
                row={}
                start=time.perf_counter_ns(); f.sources.verify(); row["pinned_source_check_ms"]=(time.perf_counter_ns()-start)/1e6
                start=time.perf_counter_ns(); result=f.bridge.runtime.calculate(f.observation,f.policy,f.now); row["source_checked_runtime_and_witness_ms"]=(time.perf_counter_ns()-start)/1e6
                assert result["all_facts_true"] and result["mismatches"]==0
                start=time.perf_counter_ns(); assert witness.evaluate(1,["T","T","T"])=="T"; row["persistent_witness_ms"]=(time.perf_counter_ns()-start)/1e6
                rows.append(row)
    (out/"MEASUREMENTS.json").write_text(json.dumps(rows,indent=2)+"\n")
    print(json.dumps({"evidence":str(out),"measurements":rows}))


if __name__=="__main__": main()
