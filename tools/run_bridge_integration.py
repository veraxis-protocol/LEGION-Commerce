import hashlib
import json
import sys
import time
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from tools.run_checks import Recording


def main():
    out=ROOT/"bridge_evidence"/time.strftime("integration-%Y%m%dT%H%M%SZ",time.gmtime())
    out.mkdir(parents=True,exist_ok=False)
    paths=sorted(p for d in ("commerce_bridge","commerce_adapter","bridge_tests","bridge_integration_tests","tools")
                 for p in (ROOT/d).rglob("*.py") if "__pycache__" not in p.parts)
    source={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    (out/"FROZEN_INPUTS.json").write_text(json.dumps({"mode":"CONFORMANCE_ONLY","test_keys":"Temporary memory only",
        "destination":"TemporaryDirectory only; never LOCALAPPDATA","expected_tests":6,"source_hashes":source},indent=2)+"\n")
    suite=unittest.defaultTestLoader.discover(str(ROOT/"bridge_integration_tests"),top_level_dir=str(ROOT))
    with (out/"TEST_OUTPUT.txt").open("w",encoding="utf-8") as stream:
        result=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=Recording).run(suite)
    (out/"ACTUAL_RESULTS.json").write_text(json.dumps(result.records,indent=2)+"\n")
    unchanged=all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected for name,expected in source.items())
    (out/"WO_VERDICT.json").write_text(json.dumps({"integration":"PASS" if result.wasSuccessful() and unchanged else "FAIL",
        "native_payment_dispatches":0,"operational_installs":0,"overall":"BLOCKED","source_unchanged":unchanged},indent=2)+"\n")
    (out/"EVIDENCE_HASHES.sha256").write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in sorted(out.iterdir()) if p.is_file()))
    print(json.dumps({"evidence":str(out),"tests":result.testsRun,"failures":len(result.failures),"errors":len(result.errors)}))
    return 0 if result.wasSuccessful() and unchanged else 1


if __name__=="__main__": raise SystemExit(main())
