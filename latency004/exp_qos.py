"""WP3 E1e: per-process QoS under PACED load (no system or power-plan setting is changed).

Hypothesis: with idle gaps the Balanced plan lowers the clock of our processes between requests;
Windows applies its background (EcoQoS-eligible) policy to the windowless gateway parent. Opting
OUR processes out of power throttling (SetProcessInformation ProcessPowerThrottling, explicit
HighQoS) and/or raising their priority class restores the clock for the request.
Modes alternate per block (ABCD...) on one BC gateway, paced at PACE ms:
  children_only : current code (workers opted out, gateway parent left at system default)
  parent_highqos: + the gateway parent process opted out
  parent_highqos_above_normal: + ABOVE_NORMAL priority for parent and worker
  children_default: workers returned to system default (shows whether the existing opt-out matters)
Refutation: no mode lowers paced p95 versus children_only.

Usage: python latency004/exp_qos.py <out.json> [pace_ms=20] [blocks=8] [per_block=150]
"""
import ctypes
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from arch import Fixture, build_gateway, sources_for  # noqa: E402
from bench import stats  # noqa: E402
from hoststate import host_state, speed_index_ms  # noqa: E402
from commerce_bridge.transport import current_process_handle  # noqa: E402


class STATE(ctypes.Structure):
    _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong), ("StateMask", ctypes.c_ulong)]


k = ctypes.WinDLL("kernel32", use_last_error=True)
k.SetProcessInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
k.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_ulong]


def qos(handle, level):
    s = {"high": STATE(1, 1, 0), "eco": STATE(1, 1, 1), "default": STATE(1, 0, 0)}[level]
    return bool(k.SetProcessInformation(ctypes.c_void_p(int(handle)), 4, ctypes.byref(s), ctypes.sizeof(s)))


def prio(handle, above):
    return bool(k.SetPriorityClass(ctypes.c_void_p(int(handle)), 0x8000 if above else 0x20))


MODES = ["children_only", "parent_highqos", "parent_highqos_above_normal", "children_default"]


def main():
    out = Path(sys.argv[1]); pace = float(sys.argv[2]) if len(sys.argv) > 2 else 20
    blocks = int(sys.argv[3]) if len(sys.argv) > 3 else 8; per = int(sys.argv[4]) if len(sys.argv) > 4 else 150
    src = sources_for("BC"); me = current_process_handle()
    res = {"pace_ms": pace, "host_start": host_state(), "samples": {m: [] for m in MODES}, "applied": {}, "speed": []}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            f = Fixture(tmp + "/fx", qualified_sources=src)
            g, _, m, pe = build_gateway(f, tmp + "/g", "BC", src)
            child = src.session.child
            with g.transaction() as db:
                for _ in range(100): g.check(db, m, pe)
                for b in range(blocks * len(MODES)):
                    mode = MODES[b % len(MODES)]; wh = child.process._handle
                    a = {"parent_qos": qos(me, "high" if mode.startswith("parent") else "default"),
                         "parent_prio": prio(me, mode.endswith("above_normal")),
                         "worker_qos": qos(wh, "default" if mode == "children_default" else "high"),
                         "worker_prio": prio(wh, mode.endswith("above_normal"))}
                    res["applied"][mode] = a
                    time.sleep(0.5); nxt = 0
                    for _ in range(per):
                        while time.perf_counter_ns() < nxt: time.sleep(0.0005)
                        nxt = time.perf_counter_ns() + int(pace * 1e6)
                        t = time.perf_counter_ns(); g.check(db, m, pe); res["samples"][mode].append((time.perf_counter_ns() - t) / 1e6)
                    if b % len(MODES) == len(MODES) - 1: res["speed"].append(speed_index_ms())
            qos(me, "default"); prio(me, False)
            g.admission.close()
    finally:
        src.close()
    res["stats_ms"] = {mm: stats(v) for mm, v in res["samples"].items()}
    base = res["stats_ms"]["children_only"]["p95"]
    res["p95_gain_vs_children_only_ms"] = {mm: round(base - s["p95"], 4) for mm, s in res["stats_ms"].items()}
    res["hypothesis"] = "SUPPORTED" if max(res["p95_gain_vs_children_only_ms"].values()) > 0.2 else "REFUTED"
    res["host_end"] = host_state()
    out.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    print(json.dumps({x: res[x] for x in ("stats_ms", "p95_gain_vs_children_only_ms", "applied", "speed", "hypothesis")}, indent=1))


if __name__ == "__main__":
    main()
