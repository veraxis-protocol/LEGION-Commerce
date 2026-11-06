"""WO-COM-LATENCY-004: interleaved, uninstrumented full-gate benchmark of A, B, C, BC.

Boundary (unchanged from QUALIFY-002 WP3): wall time of Gateway.check(db, mission, permit)
with the SQLite transaction open = current control signature + complete admission check.
p95 = the ceil(0.95*N)-th smallest sample (same definition as tools/latency_phases.py).

Protocols
  repeat : the same mission/permit every request (QUALIFY-002 WP3 protocol).
  fresh  : every request presents a DIFFERENT permit (new permit_id), a new Core observation,
           new CAGE and VEIP authorizations and a new bundle file - i.e. 4 never-seen
           Ed25519 signatures, nothing memoizable except session-constant authority.
Architectures are interleaved in rounds (rotating order) so host frequency drift affects
all equally; host power state and a CPU speed index are recorded per round.

  fresh_cold: as fresh, without the issuer read-back (stress: AV on-access scan in the gate).
Load regimes: back-to-back (QUALIFY-002 protocol) or paced (one request every PACE ms; env PACE_MS).

Usage: python latency004/bench.py <out_dir> [N_per_arch_per_protocol=1000] [rounds=10] [archs=A,B,C,BC] [protocols=repeat,fresh]
"""
import copy
import json
import math
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from arch import ARCHS, Fixture, build_gateway, digest, sources_for  # noqa: E402
from hoststate import host_state, speed_index_ms  # noqa: E402


FAST_STATE_MAX = 120  # speed index (ms per 2e6-iteration loop) below which the CPU is unthrottled


def pct(v, p):
    s = sorted(v)
    return s[max(0, math.ceil(p * len(s)) - 1)]


def stats(v):
    return {"N": len(v), "p50": round(statistics.median(v), 4), "p95": round(pct(v, .95), 4), "p99": round(pct(v, .99), 4),
            "mean": round(statistics.mean(v), 4), "max": round(max(v), 4), "min": round(min(v), 4)}


def fresh_permits(f, n, gateways, readback=True):
    """Pre-generate n distinct (permit envelope) with their bundles written to every gateway."""
    base_obs = copy.deepcopy(f.observation)
    out = []
    for i in range(n):
        f.observation = copy.deepcopy(base_obs)
        f.observation["observed_at"] = 1000 - (i % 250)
        bundle = f.bundle()
        assessment = f.assess(bundle)
        permit = {"schema": "commerce.permit.v1", "permit_id": f"FRESH-PERMIT-{i:06d}", "issued_at": f.now - 100, "expires_at": f.now + 400,
                  "quote_expires_at": f.now + 500, "mission_hash": f.mh, "acceptance_hash": None, "epoch": 1,
                  "intent": f.intent, "scope": "paypal.sandbox.orders.create_capture", "verdict": "T", "lean_rule_earned": True,
                  "controller_act": True, "cage_allow": True, "veip_eligible": True, "prerequisites_satisfied": True,
                  "bridge_evidence_hash": digest("commerce.bridge.bundle.v1", bundle),
                  "bridge_assessment_hash": digest("commerce.bridge.assessment.v1", assessment),
                  "core_observation_hash": assessment["observation_hash"]}
        text = json.dumps(bundle)
        per = {}
        for arch, (g, _, _, _) in gateways.items():
            adm = g.admission
            p = dict(permit, acceptance_hash=digest("commerce.a03.acceptance.v1", adm.acceptance["payload"]))
            path = adm.bundle_dir / (digest("commerce.permit.locator.v1", p["permit_id"]) + ".json")
            path.write_text(text)
            if readback:  # issuer verifies its own write (absorbs the AV first-open scan; exp_file_open.py)
                path.read_bytes()
            per[arch] = f.sign("admission", "commerce.permit.v1", p)
        out.append(per)
    f.observation = base_obs
    f.bridge.attempts.clear()
    return out


def main():
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
    rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    archs = sys.argv[4].split(",") if len(sys.argv) > 4 else list(ARCHS)
    protocols = sys.argv[5].split(",") if len(sys.argv) > 5 else ["repeat", "fresh"]
    per_round = n // rounds
    meta = {"boundary": "Gateway.check(db,mission,permit), SQLite transaction open", "N_per_arch_per_protocol": per_round * rounds,
            "rounds": rounds, "warmup_per_arch": 100, "p95_definition": "ceil(0.95N)-th smallest", "python": sys.version,
            "host_start": host_state(), "git_head": os.popen('git -C "%s" rev-parse HEAD' % Path(__file__).resolve().parent.parent).read().strip(),
            "git_dirty": bool(os.popen('git -C "%s" status --porcelain -- commerce_bridge commerce_adapter' % Path(__file__).resolve().parent.parent).read().strip())}
    srcs = {a: sources_for(a) for a in archs}
    samples = {(a, p): [] for a in archs for p in protocols}
    outcomes = {(a, p): {} for a in archs for p in protocols}
    state = {(a, p): [] for a in archs for p in protocols}
    round_info = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            f = Fixture(tmp + "/fx", qualified_sources=srcs[archs[0]])
            gws = {a: build_gateway(f, f"{tmp}/{a}", a, srcs[a]) for a in archs}
            fresh = fresh_permits(f, per_round * rounds, gws) if "fresh" in protocols else []
            cold = fresh_permits(f, per_round * rounds, gws, readback=False) if "fresh_cold" in protocols else []
            pace_ns = int(float(os.environ.get("PACE_MS", "0")) * 1e6)
            meta["pace_ms"] = pace_ns / 1e6
            for a in archs:  # warm-up
                g, _, m, pe = gws[a]
                with g.transaction() as db:
                    for _ in range(100): g.check(db, m, pe)
            fi = 0
            for r in range(rounds):
                order = archs[r % len(archs):] + archs[:r % len(archs)]
                info = {"round": r, "order": order, "speed_index_ms": speed_index_ms()}
                info["block_speed"] = {}
                for proto in protocols:
                    for a in order:
                        g, _, m, pe = gws[a]
                        next_slot = 0
                        with g.transaction() as db:
                            for k in range(per_round):
                                permit = pe if proto == "repeat" else (fresh if proto == "fresh" else cold)[r * per_round + k][a]
                                if pace_ns:
                                    while time.perf_counter_ns() < next_slot: time.sleep(0.0005)
                                    next_slot = time.perf_counter_ns() + pace_ns
                                t = time.perf_counter_ns()
                                try:
                                    g.check(db, m, permit); res = "ALLOW"
                                except Exception as e:  # noqa: BLE001 - recorded, must be zero
                                    res = "DENY:" + str(e)
                                samples[(a, proto)].append((time.perf_counter_ns() - t) / 1e6)
                                outcomes[(a, proto)][res] = outcomes[(a, proto)].get(res, 0) + 1
                                state[(a, proto)].append(info["speed_index_ms"])
                round_info.append(info)
            for a in archs:
                gws[a][0].admission.close()
    finally:
        for s in srcs.values(): s.close()
    meta["host_end"] = host_state()
    result = {"meta": meta, "rounds": round_info, "results": {}}
    for (a, p), v in samples.items():
        fast = [x for x, si in zip(v, state[(a, p)]) if si < FAST_STATE_MAX]
        slow = [x for x, si in zip(v, state[(a, p)]) if si >= FAST_STATE_MAX]
        result["results"][f"{a}/{p}"] = {"stats_ms": stats(v), "outcomes": outcomes[(a, p)],
                                         "fast_host_state": stats(fast) if fast else None, "throttled_host_state": stats(slow) if slow else None}
    (out / "BENCH.json").write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    (out / "RAW_SAMPLES.json").write_text(json.dumps({f"{a}/{p}": [round(x, 5) for x in v] for (a, p), v in samples.items()}) + "\n")
    for k, v in result["results"].items():
        s = v["stats_ms"]
        fs = v["fast_host_state"]; ts = v["throttled_host_state"]
        print(f"{k:16} p50 {s['p50']:7.3f} p95 {s['p95']:7.3f} p99 {s['p99']:7.3f} max {s['max']:7.3f} | fast p95 {fs['p95'] if fs else '-'} (n={fs['N'] if fs else 0}) throttled p95 {ts['p95'] if ts else '-'}  {v['outcomes']}")
    print("speed index per round:", [x["speed_index_ms"] for x in round_info], meta["host_start"]["ac_line"])


if __name__ == "__main__":
    main()

