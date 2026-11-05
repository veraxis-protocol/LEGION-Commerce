"""Copy only the named public design inputs; never inspect credential files."""
import hashlib
import json
import re
import shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
SOURCE=Path(r"C:\Users\Administrator\Dropbox\PayPal\WO-COM-MASTER-001")
EXPECTED={
 "WO-COM-A03_TECHNICAL_DESIGN_v1.1.md":"a5e9ae9cb123592e787bac678eabe59818daec03050163b776e35dab62bff14a",
 "WO-COM-A04_TECHNICAL_DESIGN_v1.1.md":"53654c7d251eb9cd7d94a854bcd29e4511731560b55e502525dad3f7a62b5f93",
 "WO-COM-A05_TECHNICAL_DESIGN_v1.1.md":"2ab62c9282b268db7ba226ee5427126c5d05427a50a4ed4977fc546aa0e83591"}


def main():
    destination=ROOT/"design"
    destination.mkdir(exist_ok=True)
    for name,expected in EXPECTED.items():
        assert hashlib.sha256((SOURCE/name).read_bytes()).hexdigest()==expected, "FROZEN_DESIGN_HASH_MISMATCH"
        shutil.copyfile(SOURCE/name,destination/name)
    manifest=json.loads((SOURCE/"FREEZE_MANIFEST_v1.2.json").read_text(encoding="utf-8-sig"))
    pins={"source_pins":manifest["source_pins"],"a03_tdd_sha256":EXPECTED["WO-COM-A03_TECHNICAL_DESIGN_v1.1.md"],
          "tdd_sha256":EXPECTED,"core_policy":"NO_CORE_MODIFICATIONS", "freeze_verified_against":"SHA256SUMS_v1.2.txt"}
    (destination/"SOURCE_PINS.json").write_text(json.dumps(pins,indent=2)+"\n",encoding="utf-8")
    a04=(destination/"WO-COM-A04_TECHNICAL_DESIGN_v1.1.md").read_text(encoding="utf-8")
    rows=[]
    for line in a04.splitlines():
        if line.startswith("| A04-T"):
            parts=[v.strip() for v in line.split("|")[1:-1]]
            rows.append({"id":parts[0],"action":parts[1],"expected_pass":parts[2],"expected_fail":parts[3],
                         "mechanism_mode":"MOCK_ONLY", "native_evidence_required":parts[0] in ("A04-T01","A04-T05","A04-T06","A04-T07")})
    assert len(rows)==14
    (destination/"TEST_MATRIX.json").write_text(json.dumps(rows,indent=2)+"\n",encoding="utf-8")
    (destination/"REQUIREMENTS.json").write_text(json.dumps({"work_order":"WO-COM-A04","tdd_sha256":EXPECTED,
        "invariants":[line[2:] for line in a04.splitlines() if line.startswith("- **G")],
        "stop":"Any forbidden/duplicate/overbudget financial effect, false completion, stale authority or blind uncertain retry",
        "native_gate":"A03 complete accepted evidence + current signed mission/permit/control + reserve + authenticated buyer approval",
        "acceptance":"All frozen mandatory cases, separately labelled native and mock evidence; no mock substitutes for real capture"},indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"frozen_designs_verified":len(EXPECTED),"test_cases":len(rows)}))


if __name__=="__main__": main()
