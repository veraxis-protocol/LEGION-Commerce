"""WP3 E6: combining independent checks - speculative overlap of the pure Core calculation.

Hypothesis: starting the (pure, effect-free) consolidated calculation as soon as policy and
observation are authenticated hides the remaining parent checks behind the worker's compute.
Mechanism: PreparedAdmission.overlap (dispatch after observation verification, collect at
A's runtime.calculate position; a later denial drains and discards the answer).
Same gateway, same process, same session; overlap toggled per block, alternating (ABAB) so host
drift affects both. Negative tests: a denial AFTER dispatch (forged CAGE) must produce A's code and
leave the worker stream in step (the next valid request ALLOWs), in both modes.
Refutation: overlap p50 not lower than no-overlap p50, or any outcome difference.

Usage: python latency004/exp_overlap.py <out.json> [blocks=10] [per_block=100]
"""
import base64
import copy
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from arch import Fixture, build_gateway, digest, outcome, sources_for  # noqa: E402
from bench import stats  # noqa: E402
from hoststate import host_state  # noqa: E402


MODES = {"no_overlap": (False, False), "overlap_late": (True, False), "overlap_early": (True, True)}


def main():
    out = Path(sys.argv[1]); blocks = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    per = int(sys.argv[3]) if len(sys.argv) > 3 else 100
    src = sources_for("BC"); res = {"host_start": host_state(), "samples": {m: [] for m in MODES}, "negative": {}}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            f = Fixture(tmp + "/fx", qualified_sources=src)
            g, _, m, pe = build_gateway(f, tmp + "/g", "BC", src)
            adm = g.admission
            with g.transaction() as db:
                for _ in range(100): g.check(db, m, pe)
                for b in range(blocks * len(MODES)):
                    mode = list(MODES)[b % len(MODES)]; adm.overlap, adm.early = MODES[mode]
                    for _ in range(per):
                        t = time.perf_counter_ns(); g.check(db, m, pe); res["samples"][mode].append((time.perf_counter_ns() - t) / 1e6)
            # negative: denial after dispatch (CAGE forged in the bundle file; permit re-bound to it)
            for mode in MODES:
                adm.overlap, adm.early = MODES[mode]
                bundle = f.bundle(); sig = bytearray(base64.b64decode(bundle["cage"]["signature"])); sig[3] ^= 1
                bundle["cage"]["signature"] = base64.b64encode(bytes(sig)).decode()
                p = copy.deepcopy(pe["payload"]); p["permit_id"] = "NEG-" + mode; p["bridge_evidence_hash"] = digest("commerce.bridge.bundle.v1", bundle)
                (adm.bundle_dir / (digest("commerce.permit.locator.v1", p["permit_id"]) + ".json")).write_text(json.dumps(bundle))
                neg = f.sign("admission", "commerce.permit.v1", p)
                def chk(permit):
                    with g.transaction() as db: g.check(db, m, permit)
                res["negative"][mode] = {"denied_after_dispatch": outcome(lambda: chk(neg)), "next_valid": outcome(lambda: chk(pe))}
            adm.close()
    finally:
        src.close()
    res["stats_ms"] = {k: stats(v) for k, v in res["samples"].items()}
    n = res["stats_ms"]["no_overlap"]
    res["delta_vs_no_overlap_ms"] = {m: {"p50": round(n["p50"] - res["stats_ms"][m]["p50"], 4), "p95": round(n["p95"] - res["stats_ms"][m]["p95"], 4)} for m in MODES}
    expect = {"denied_after_dispatch": "DENY:INVALID_SIGNATURE", "next_valid": "ALLOW"}
    res["negatives_equal"] = all(v == expect for v in res["negative"].values())
    d = res["delta_vs_no_overlap_ms"]
    res["hypothesis"] = {"late_overlap": "SUPPORTED" if d["overlap_late"]["p50"] > 0 and res["negatives_equal"] else "REFUTED",
                         "early_dispatch": "SUPPORTED" if d["overlap_early"]["p50"] > d["overlap_late"]["p50"] and res["negatives_equal"] else "REFUTED"}
    res["host_end"] = host_state()
    out.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: res[k] for k in ("stats_ms", "delta_vs_no_overlap_ms", "negative", "hypothesis")}, indent=1))


if __name__ == "__main__":
    main()


