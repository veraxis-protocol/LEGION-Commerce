"""Compare a qualify_commerce run with its separate-process replay, case by case.

Usage: python tools/compare_replay.py <run-dir> <replay-dir>
"""
import json
import sys
from pathlib import Path


def rows(path):
    return [json.loads(l) for l in path.read_text().splitlines()]


def main():
    run, rep = Path(sys.argv[1]), Path(sys.argv[2])
    out = {}
    for name, key in (("MATCHED_RESULTS.jsonl", lambda r: (r["lean"], r["runtime"], r["runtime_grounds"])),
                      ("ALPHA_RESULTS.jsonl", lambda r: (r.get("qualified", {}).get("lean"), r.get("qualified", {}).get("runtime"), r.get("error")))):
        a, b = rows(run / name), rows(rep / name)
        diffs = [x["id"] for x, y in zip(a, b) if key(x) != key(y)] + (["LENGTH"] if len(a) != len(b) else [])
        out[name] = {"cases": len(a), "differences": len(diffs), "first": diffs[:10]}
    ra = {r["test"]: r["result"] for r in json.loads((run / "A03_RESULTS.json").read_text())}
    rb = {r["test"]: r["result"] for r in json.loads((rep / "A03_RESULTS.json").read_text())}
    out["A03_RESULTS.json"] = {"tests": len(ra), "differences": sum(ra[k] != rb.get(k) for k in ra)}
    out["inputs_identical"] = (run / "MATCHED_INPUTS.jsonl").read_bytes() == (rep / "MATCHED_INPUTS.jsonl").read_bytes()
    out["verdict"] = "REPLAY_MATCH" if out["inputs_identical"] and not any(v["differences"] for v in out.values() if isinstance(v, dict)) else "REPLAY_MISMATCH"
    (rep / "REPLAY_COMPARISON.json").write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(out))


if __name__ == "__main__":
    main()
