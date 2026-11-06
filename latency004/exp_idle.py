"""WP3 E1f: is the paced-load penalty the CPU idling between requests?

Same BC gateway, same 20 ms request interval; the client either SLEEPS through the gap (idle CPU,
as a real request arrival pattern) or BUSY-WAITS through it (the CPU never idles). Alternating
blocks. If busy-wait p95 returns to the back-to-back level, the penalty is idle-state / clock
ramp-down governed by the host power policy, not admission work.

Usage: python latency004/exp_idle.py <out.json> [pace_ms=20] [blocks=8] [per_block=150]
"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from arch import Fixture, build_gateway, sources_for  # noqa: E402
from bench import stats  # noqa: E402
from hoststate import host_state  # noqa: E402

MODES = ["sleep_gap", "busy_gap", "back_to_back"]


def main():
    out = Path(sys.argv[1]); pace = float(sys.argv[2]) if len(sys.argv) > 2 else 20
    blocks = int(sys.argv[3]) if len(sys.argv) > 3 else 8; per = int(sys.argv[4]) if len(sys.argv) > 4 else 150
    src = sources_for("BC"); res = {"pace_ms": pace, "host_start": host_state(), "samples": {m: [] for m in MODES}}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            f = Fixture(tmp + "/fx", qualified_sources=src)
            g, _, m, pe = build_gateway(f, tmp + "/g", "BC", src)
            with g.transaction() as db:
                for _ in range(100): g.check(db, m, pe)
                for b in range(blocks * len(MODES)):
                    mode = MODES[b % len(MODES)]; nxt = 0
                    for _ in range(per):
                        if mode == "sleep_gap":
                            while time.perf_counter_ns() < nxt: time.sleep(0.0005)
                        elif mode == "busy_gap":
                            while time.perf_counter_ns() < nxt: pass
                        nxt = time.perf_counter_ns() + int(pace * 1e6)
                        t = time.perf_counter_ns(); g.check(db, m, pe); res["samples"][mode].append((time.perf_counter_ns() - t) / 1e6)
            g.admission.close()
    finally:
        src.close()
    res["stats_ms"] = {mm: stats(v) for mm, v in res["samples"].items()}
    s = res["stats_ms"]
    res["idle_penalty_p95_ms"] = round(s["sleep_gap"]["p95"] - s["busy_gap"]["p95"], 4)
    res["hypothesis"] = "SUPPORTED" if s["busy_gap"]["p95"] < s["sleep_gap"]["p95"] - 0.5 else "REFUTED"
    res["host_end"] = host_state()
    out.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    print(json.dumps({x: res[x] for x in ("stats_ms", "idle_penalty_p95_ms", "hypothesis")}, indent=1))


if __name__ == "__main__":
    main()
