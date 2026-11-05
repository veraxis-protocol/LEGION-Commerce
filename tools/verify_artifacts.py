import hashlib
import json
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent


def main():
    pins=json.loads((ROOT/"design"/"SOURCE_PINS.json").read_text())
    for name,expected in pins["tdd_sha256"].items():
        assert hashlib.sha256((ROOT/"design"/name).read_bytes()).hexdigest()==expected,name
    records=0
    for manifest in (ROOT/"evidence").glob("*/EVIDENCE_HASHES.sha256"):
        for line in manifest.read_text().splitlines():
            expected,name=line.split("  ",1)
            assert hashlib.sha256((manifest.parent/name).read_bytes()).hexdigest()==expected,str(manifest)
            records+=1
    latest=sorted((ROOT/"evidence").glob("run-*"))[-1]
    source=json.loads((latest/"SOURCE_MANIFEST.json").read_text())
    for name,expected in source.items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected,"LATEST_TESTED_SOURCE_DRIFT: "+name
    files=subprocess.run(["git","ls-files","--cached"],cwd=ROOT,capture_output=True,text=True,check=True).stdout.splitlines()
    forbidden=[f for f in files if any(x in f.lower() for x in (".dpapi","paypal_client_id.txt",".venv/","ledger.db"))]
    assert not forbidden,"FORBIDDEN_TRACKED_FILE"
    print(json.dumps({"tdd_hashes_verified":3,"evidence_file_hashes_verified":records,
                      "latest_tested_source_hashes_verified":len(source),"forbidden_tracked_files":len(forbidden)}))


if __name__=="__main__": main()
