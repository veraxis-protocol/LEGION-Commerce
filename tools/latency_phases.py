"""WP3: measured phase decomposition of the complete admission check (no profiler).

Wraps (does not alter) the functions on the Gateway.check path with
perf_counter_ns accumulators, and separately times the worker's internals
in-process on the same observation. CONFORMANCE-ONLY test authority.

Usage: python tools/latency_phases.py <out.json> [N]
"""
import json
import math
import statistics
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True
sys.pycache_prefix = str(Path(tempfile.gettempdir()) / ("legion-nopyc-" + str(time.time_ns())))
sys.path.insert(0, str(ROOT))
import commerce_adapter.contracts as contracts
import commerce_adapter.gateway as gateway_mod
import commerce_bridge.admission as admission
import commerce_bridge.runtime as runtime
import commerce_bridge.qualified as qualified
import commerce_bridge.witness as witness
import commerce_bridge.sources as sources_mod
from bridge_tests.fixtures import Fixture

ACC = defaultdict(int)
CNT = defaultdict(int)


def wrap(owner, name, label):
    original = getattr(owner, name)

    def timed(*a, **k):
        t = time.perf_counter_ns()
        try:
            return original(*a, **k)
        finally:
            ACC[label] += time.perf_counter_ns() - t
            CNT[label] += 1
    setattr(owner, name, timed)


def pct(v, p):
    s = sorted(v)
    return s[math.ceil(p * len(s)) - 1]


def main():
    out = Path(sys.argv[1]); n = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    for mod in (contracts, admission, gateway_mod):
        for fn in ("verify", "digest"):
            if hasattr(mod, fn):
                wrap(mod, fn, "sig_verify" if fn == "verify" else "digest")
    wrap(contracts.Admission, "ready", "adapter_ready(incl.)")
    wrap(admission.BridgeAdmission, "ready", "bridge_ready(incl.)")
    wrap(admission.Bridge, "assess", "bridge_assess(incl.)")
    wrap(sources_mod, "bridge_hashes", "bridge_source_rehash")
    wrap(sources_mod, "adapter_hashes", "adapter_source_rehash")
    admission.bridge_hashes = sources_mod.bridge_hashes
    admission.adapter_hashes = sources_mod.adapter_hashes
    wrap(qualified.QualifiedSources, "verify", "native_source_verify")
    wrap(witness.LeanWitness, "evaluate", "zverdict_witness")
    wrap(runtime.CoreRuntime, "compare", "witness_compare(incl.)")
    calls = {}
    original_call = qualified.JsonProcess.call

    def call(self, data):
        t = time.perf_counter_ns()
        try:
            return original_call(self, data)
        finally:
            label = calls.get(id(self), "jsonprocess")
            ACC[label] += time.perf_counter_ns() - t; CNT[label] += 1
    qualified.JsonProcess.call = call

    src = qualified.QualifiedSources(ROOT / "runtime_sources", immutable=True)
    calls[id(src.session.worker)] = "core_alpha_worker_roundtrip"
    calls[id(src.session.lean)] = "legion_decide_roundtrip"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            f = Fixture(tmp, qualified_sources=src)
            g, provider, mission, permit = f.gateway()
            totals = []
            with g.transaction() as db:
                for i in range(n + 20):
                    if i == 20:
                        ACC.clear(); CNT.clear()
                    t = time.perf_counter_ns(); g.check(db, mission, permit)
                    if i >= 20:
                        totals.append((time.perf_counter_ns() - t) / 1e6)
            obs, pol, now = f.observation, f.policy, f.now
    finally:
        src.close()
    phases = {k: {"mean_ms_per_check": round(ACC[k] / n / 1e6, 4), "calls_per_check": CNT[k] / n} for k in sorted(ACC)}
    result = {"N": n, "warmup": 20, "total_p50_ms": round(statistics.median(totals), 4),
              "total_p95_ms": round(pct(totals, .95), 4), "total_max_ms": round(max(totals), 4),
              "phases": phases, "samples_ms": [round(x, 4) for x in totals], "python": sys.version}
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: result[k] for k in ("total_p50_ms", "total_p95_ms", "total_max_ms")}))
    for k, v in phases.items():
        print(f"  {k:32} {v['mean_ms_per_check']:9.4f} ms  x{v['calls_per_check']}")


if __name__ == "__main__":
    main()
