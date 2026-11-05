"""WP1: differential of two legion_decide binaries on identical frozen inputs.

Usage: python tools/decider_binary_equivalence.py <MATCHED_INPUTS.jsonl> <binA> <binB> <out.json>
Compares the complete JSON answers (verdict, grounds, closed set) line by line.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from commerce_bridge.qualified import JsonProcess


def main():
    rows = [json.loads(l) for l in Path(sys.argv[1]).read_text().splitlines()]
    a, b = Path(sys.argv[2]), Path(sys.argv[3])
    diffs = []
    with JsonProcess([a]) as pa, JsonProcess([b]) as pb:
        for row in rows:
            ra, rb = pa.call(row["input"]), pb.call(row["input"])
            if ra != rb:
                diffs.append({"id": row["id"], "a": ra, "b": rb})
    out = {"inputs": len(rows), "inputs_sha256": hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest(),
           "binary_a": {"path": a.name, "sha256": hashlib.sha256(a.read_bytes()).hexdigest()},
           "binary_b": {"path": b.name, "sha256": hashlib.sha256(b.read_bytes()).hexdigest()},
           "differences": len(diffs), "first_differences": diffs[:20]}
    Path(sys.argv[4]).write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: out[k] for k in ("inputs", "differences")}))


if __name__ == "__main__":
    main()
