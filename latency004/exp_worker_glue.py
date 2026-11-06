"""WP3 E2b: worker glue - Path.resolve once + direct coroutine driver vs the ed8b3e1 core_worker.

Hypothesis: per-request Path.resolve() (filesystem walks) and a Proactor event-loop iteration per
fact are a measurable part of the worker's compute and can be removed without changing any output.
Method: load commerce_bridge/core_worker.py at ed8b3e1 (git show) and the current file as two
modules in ONE process; on 1,200 inputs (valid + every bridge_differential observation mutation)
call both alternately; outputs must be identical (JSON); timings interleaved.
Negative test: a coroutine that suspends must fail closed in the new driver.
Refutation: any output difference, or no timing reduction.

Usage: python latency004/exp_worker_glue.py <out.json>
"""
import asyncio
import copy
import importlib.util
import json
import random
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent; ROOT = HERE.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT / "tools"))
from arch import Fixture, sources_for  # noqa: E402
from bench import stats  # noqa: E402
from hoststate import host_state  # noqa: E402
import equivalence as eq  # noqa: E402  (frozen mutations)
import bridge_differential as bd  # noqa: E402


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def main():
    out = Path(sys.argv[1])
    old_src = subprocess.run(["git", "-C", str(ROOT), "show", "ed8b3e1:commerce_bridge/core_worker.py"], capture_output=True, check=True).stdout
    tmpd = Path(tempfile.mkdtemp()); (tmpd / "core_worker_ed8b3e1.py").write_bytes(old_src)
    src = sources_for("A")
    inputs = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            rng = random.Random(bd.SEED)
            cats = [c for c in bd.EXPECTED if c not in ("witness_timeout_or_unavailable", "decider_unavailable_or_timeout",
                                                        "revoked_after_reserve", "duplicate_financial_effect", "forged_observation_envelope")]
            for i in range(1200):
                c = cats[i % len(cats)]
                f = Fixture(f"{tmp}/{i}", qualified_sources=src)
                eq.mutate(f, c, random.Random(rng.randrange(1 << 30)))
                inputs.append({"category": c, "data": json.loads(json.dumps({"observation": f.observation, "policy": f.policy, "now": f.now}))})
    finally:
        src.close()
    legion = str(ROOT / "runtime_sources/LEGION_QUALIFIED"); swarm = str(ROOT / "runtime_sources/ztl-swarm")
    sys.path.insert(0, str(ROOT / "commerce_bridge"))
    import qualified_worker as qw
    qw.frozen_imports(legion, swarm)
    old = load("core_worker_old", tmpd / "core_worker_ed8b3e1.py"); new = load("core_worker_new", ROOT / "commerce_bridge/core_worker.py")
    def run(mod, data):
        try: return json.dumps(mod.calculate(copy.deepcopy(data), legion, swarm), sort_keys=True)
        except Exception as e: return "ERROR:" + type(e).__name__
    for row in inputs[:30]: run(old, row["data"]); run(new, row["data"])
    t_old, t_new, diffs = [], [], []
    for i, row in enumerate(inputs):
        order = (old, new) if i % 2 == 0 else (new, old)
        r = {}
        for mod in order:
            t = time.perf_counter_ns(); r[mod.__name__] = run(mod, row["data"]); d = (time.perf_counter_ns() - t) / 1e6
            (t_old if mod is old else t_new).append(d)
        if r["core_worker_old"] != r["core_worker_new"]: diffs.append(i)
    async def suspends():
        await asyncio.sleep(0.01); return 1
    try:
        new._run(suspends()); neg = "RETURNED"
    except RuntimeError as e:
        neg = "FAIL_CLOSED:" + str(e)
    res = {"inputs": len(inputs), "output_differences": len(diffs), "first_differences": diffs[:10],
           "old_ms": stats(t_old), "new_ms": stats(t_new), "delta_mean_ms": round(statistics.mean(t_old) - statistics.mean(t_new), 4),
           "negative_suspending_coroutine": neg, "host": host_state(),
           "hypothesis": "SUPPORTED" if not diffs and statistics.mean(t_new) < statistics.mean(t_old) and neg.startswith("FAIL_CLOSED") else "REFUTED"}
    out.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: res[k] for k in ("inputs", "output_differences", "old_ms", "new_ms", "delta_mean_ms", "negative_suspending_coroutine", "hypothesis")}, indent=1))


if __name__ == "__main__":
    main()
