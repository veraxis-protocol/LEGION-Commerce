"""Emit an UNSIGNED review manifest. Never signs acceptance or changes its status."""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from commerce_bridge.sources import FROZEN_INTERFACE_SHA256,adapter_hashes,bridge_hashes


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--conformance",required=True)
    parser.add_argument("--replay",required=True)
    args=parser.parse_args()
    run=Path(args.conformance).resolve()
    replay=Path(args.replay).resolve()
    assert run.is_relative_to(ROOT/"bridge_evidence") and replay.is_relative_to(ROOT/"bridge_evidence")
    counters=json.loads((run/"COUNTERS.json").read_text())
    assert counters["conformance_cases"]==13 and counters["passed"]==13 and counters["observed_core_runtime_mismatches"]==0
    output=ROOT/"bridge_evidence"/time.strftime("review-request-%Y%m%dT%H%M%SZ",time.gmtime())
    output.mkdir(exist_ok=False)
    files={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in run.iterdir() if p.is_file()}
    files[replay.relative_to(ROOT).as_posix()]=hashlib.sha256(replay.read_bytes()).hexdigest()
    manifest={"schema":"commerce.bridge.review-request.v1","status":"UNSIGNED_REVIEW_REQUIRED",
        "source_commit":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
        "runtime_source_hashes":adapter_hashes(),"bridge_source_hashes":bridge_hashes(),
        "frozen_interface_sha256":FROZEN_INTERFACE_SHA256,"evidence_files":files,"local_conformance":counters,
        "signature":None,"acceptance_authority":"Existing operator-selected independent acceptance identity; paths/IDs still required",
        "not_an_execution_permit":True,"native_payment_dispatches":0,
        "required_review":["Actual Core/CAGE/VEIP input provenance","Existing key lifecycle and role scope","A01/A02 and A03 independent acceptance",
                           "Full 5ms latency gate disposition","Agent/credential custody boundary","Authenticated buyer approval and native effect verification"]}
    (output/"EVIDENCE_MANIFEST_UNSIGNED.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8",newline="\n")
    print(json.dumps({"review_request":str(output),"signed":False,"status":"BLOCKED_PENDING_EXTERNAL_ACCEPTANCE"}))


if __name__=="__main__": main()
