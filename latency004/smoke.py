"""Smoke: each architecture admits the valid case; quick timing (not evidence)."""
import statistics
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from arch import ARCHS, Fixture, build_gateway, outcome, sources_for  # noqa: E402

for arch in sys.argv[1:] or ARCHS:
    src = sources_for(arch)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            f = Fixture(tmp + "/fx", qualified_sources=src)
            g, p, m, pe = build_gateway(f, tmp + "/" + arch, arch, src)
            xs = []
            with g.transaction() as db:
                first = outcome(lambda: g.check(db, m, pe))
                for i in range(220):
                    t = time.perf_counter_ns(); g.check(db, m, pe); xs.append((time.perf_counter_ns() - t) / 1e6)
            xs = sorted(xs[20:])
            print(arch, first, "p50 %.3f p95 %.3f" % (xs[len(xs) // 2], xs[int(len(xs) * .95) - 1]),
                  getattr(g.admission, "stats", ""))
            g.admission.close()
    finally:
        src.close()
