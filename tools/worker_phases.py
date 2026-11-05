"""WP3: in-process timing of the qualified worker internals on recorded inputs.

Usage: python tools/worker_phases.py <ALPHA_INPUTS.jsonl> <out.json>
"""
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True
sys.pycache_prefix = str(Path(tempfile.gettempdir()) / ("legion-nopyc-" + str(time.time_ns())))
CORE = str(ROOT / "runtime_sources/LEGION_QUALIFIED")
SWARM = str(ROOT / "runtime_sources/ztl-swarm")
sys.path.insert(0, str(ROOT / "commerce_bridge"))
import qualified_worker as qw
import core_worker

T = {}


def timed(label, fn):
    def inner(*a, **k):
        t = time.perf_counter_ns()
        try: return fn(*a, **k)
        finally: T.setdefault(label, []).append((time.perf_counter_ns() - t) / 1e6)
    return inner


def main():
    rows = [json.loads(l) for l in Path(sys.argv[1]).read_text().splitlines()]
    valid = [r for r in rows if r["category"] == "valid"][0]
    qw.frozen_imports(CORE, SWARM)
    qw.calculate = timed("core_python_calculate", core_worker.calculate)
    data = {"observation": valid["observation"], "policy": valid["policy"], "now": valid["now"]}
    line = json.dumps(data)
    for i in range(320):
        t = time.perf_counter_ns(); parsed = json.loads(line); a = (time.perf_counter_ns() - t) / 1e6
        t = time.perf_counter_ns(); result = qw.qualified(parsed, CORE, SWARM); b = (time.perf_counter_ns() - t) / 1e6
        t = time.perf_counter_ns(); out = json.dumps(result, sort_keys=True); c = (time.perf_counter_ns() - t) / 1e6
        if i >= 20:
            T.setdefault("json_in_parse", []).append(a); T.setdefault("qualified_total", []).append(b)
            T.setdefault("json_out_dump", []).append(c)
        else:
            T.pop("core_python_calculate", None)
    T["core_python_calculate"] = T["core_python_calculate"][-300:]
    res = {k: {"p50": round(statistics.median(v), 4), "mean": round(statistics.mean(v), 4)} for k, v in T.items()}
    res["request_bytes"] = len(line); res["response_bytes"] = len(out)
    Path(sys.argv[2]).write_text(json.dumps(res, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
