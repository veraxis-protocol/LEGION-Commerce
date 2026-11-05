"""Separate read-only replay process. Reproducibility is not reviewer acceptance."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from commerce_adapter.contracts import Denied,canonical,require
from commerce_bridge.admission import Bridge
from commerce_bridge.sources import Sources


def replay(directory):
    directory=Path(directory).resolve()
    checked=0
    for line in (directory/"EVIDENCE_HASHES.sha256").read_text().splitlines():
        expected,name=line.split("  ",1)
        target=(directory/name).resolve()
        require(target.is_relative_to(directory) and hashlib.sha256(target.read_bytes()).hexdigest()==expected,"EVIDENCE_HASH_MISMATCH")
        checked+=1
    sources=Sources(ROOT/"runtime_sources")
    require(sources.verify()==json.loads((directory/"SOURCE_PINS.json").read_text()),"REPLAY_SOURCE_MISMATCH")
    source_manifest=json.loads((directory/"SOURCE_MANIFEST.json").read_text())
    require(all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected for name,expected in source_manifest.items()),"REPLAY_IMPLEMENTATION_DRIFT")
    replayed=0; injected=0
    for line in (directory/"RAW_EVENTS.jsonl").read_text().splitlines():
        trace=json.loads(line)
        for attempt in trace["bridge_attempts"]:
            if attempt["fault_injection"]:
                require(attempt.get("denied") in ("LEAN_UNAVAILABLE","LEAN_TIMEOUT","LEAN_RUNTIME_MISMATCH"),"UNEXPECTED_FAULT_DISPOSITION")
                injected+=1
                continue
            bridge=Bridge(trace["public_test_trust"],sources,clock=lambda:attempt["now"])
            try:
                actual=bridge.assess(*attempt["arguments"],**attempt["keywords"])
            except Denied as error:
                require(attempt.get("denied")==str(error),"REPLAY_DENIAL_MISMATCH")
            else:
                require("result" in attempt and canonical(actual)==canonical(attempt["result"]),"REPLAY_ASSESSMENT_MISMATCH")
            replayed+=1
    return {"verification":"REPLAY_MATCH","evidence_files_checked":checked,"real_core_lean_attempts_replayed":replayed,
            "expected_fault_injections_preserved":injected,"mode":"CONFORMANCE_ONLY",
            "independent_process":True,"independent_institutional_acceptance":False,"native_payment_dispatches":0}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("directory")
    args=parser.parse_args()
    try:
        print(json.dumps(replay(args.directory))); return 0
    except (Denied,OSError,ValueError,KeyError,TypeError) as error:
        print(json.dumps({"verification":"FAIL","code":str(error) if isinstance(error,Denied) else "INVALID_REPLAY_INPUT"})); return 1


if __name__=="__main__": raise SystemExit(main())
