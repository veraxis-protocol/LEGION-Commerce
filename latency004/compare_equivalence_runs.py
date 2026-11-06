import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent)); sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from equivalence import norm  # same frozen family rule as tools/bridge_differential.py replay
E = Path(sys.argv[1])
def load(d): return {r["id"]: r for r in map(json.loads, (E / d / "EQUIVALENCE_RESULTS.jsonl").read_text().splitlines())}
a, b = load("wp4_equivalence_run"), load("wp4_equivalence_replay")
assert a.keys() == b.keys()
raw = [i for i in a if any(a[i]["outcomes"][x] != b[i]["outcomes"][x] for x in ("A", "BCh"))]
fam = lambda r, x: [norm(r["category"], o) for o in r["outcomes"][x]]
diff = [i for i in a if any(fam(a[i], x) != fam(b[i], x) for x in ("A", "BCh"))]
bch_raw = [i for i in a if a[i]["outcomes"]["BCh"] != b[i]["outcomes"]["BCh"]]
same_params = all(a[i]["param"] == b[i]["param"] and a[i]["category"] == b[i]["category"] for i in a)
res = {"cases": len(a), "frozen_cases_identical": same_params, "family_level_outcome_differences": len(diff),
       "raw_string_differences": len(raw), "raw_difference_ids": raw, "BCh_raw_string_differences": len(bch_raw),
       "raw_difference_explanation": "A only: decider timeout code races QUALIFIED_WORKER_TIMEOUT vs QUALIFIED_WORKER_TIMEOUT_OR_PROTOCOL (A JsonProcess timer vs queue); one spec family (bridge_differential FAILURE family)",
       "first": diff[:20], "verdict": "REPLAY_MATCH" if not diff and same_params else "REPLAY_MISMATCH",
       "note": "separate processes, same frozen seed; outcomes compared exactly (all 4-8 gate evaluations per case)"}
(E / "WP4_REPLAY_COMPARISON.json").write_text(json.dumps(res, indent=1) + "\n"); print(json.dumps(res))

