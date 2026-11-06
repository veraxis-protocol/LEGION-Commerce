"""WP1/WP3 E1d: decomposition of the full gate under PACED load (one request every PACE ms).

Identical instrumentation to profile_arch.py; the only difference is an idle gap before every
request, which is where back-to-back and paced p95 diverge on this host.

Usage: python latency004/profile_paced.py <out_dir> <arch> <pace_ms> [N=500] [warmup=50]
"""
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import profile_arch as pa  # noqa: E402
pw = pa.pw


def main():
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    arch, pace = sys.argv[2], float(sys.argv[3])
    n = int(sys.argv[4]) if len(sys.argv) > 4 else 500
    warm = int(sys.argv[5]) if len(sys.argv) > 5 else 50
    host0 = pw.host_state()
    pw.install(); pa.install_prepared(); pa.install_consolidated()
    src = pa.sources_for(arch); per = []
    try:
        pa.attach_shims(arch, src)
        with tempfile.TemporaryDirectory() as tmp:
            f = pa.Fixture(tmp + "/fx", qualified_sources=src)
            g, _, m, pe = pa.build_gateway(f, tmp + "/g", arch, src)
            with g.transaction() as db:
                nxt = 0
                for i in range(warm + n):
                    while time.perf_counter_ns() < nxt: time.sleep(0.0005)
                    nxt = time.perf_counter_ns() + int(pace * 1e6)
                    pw.REQ = {}; pw.IPC.clear()
                    w = pw.pc(); g.check(db, m, pe); tw = pw.pc() - w
                    if i >= warm: per.append({"total": tw, "c": pw.REQ})
                pw.REQ = None
            g.admission.close()
    finally:
        src.close()
    comps = sorted({k for r in per for k in r["c"]})
    table = {k: {"wall": pw.stats([r["c"].get(k, [0, 0, 0])[0] for r in per])} for k in comps}
    res = {"arch": arch, "pace_ms": pace, "N": n, "end_to_end": pw.stats([r["total"] for r in per]), "components": table,
           "host_start": host0, "host_end": pw.host_state()}
    (out / f"PROFILE_PACED_{arch}_{int(pace)}ms.json").write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    print(arch, "paced", pace, res["end_to_end"])
    for k, v in sorted(table.items(), key=lambda kv: -kv[1]["wall"]["mean"])[:22]:
        print(f"  {k:58} mean {v['wall']['mean']:8.4f} p95 {v['wall']['p95']:8.4f}")


if __name__ == "__main__":
    main()
