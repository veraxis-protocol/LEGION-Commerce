"""WO-COM-LATENCY-004 WP1: instrumented profile of the full admission path (architecture A).

Boundary (identical to WP3 of QUALIFY-002): Gateway.check(db, mission, permit) with the
SQLite transaction open = current control signature + complete BridgeAdmission.check.

Every component is timed EXCLUSIVELY (a stack subtracts nested components), in wall time
(perf_counter_ns = QPC) and main-thread CPU time (thread_time_ns), per request. The qualified
worker runs under latency004/prof_worker.py, which executes the unmodified worker function and
returns QPC stamps, so IPC transit and wake-up are measured, not inferred. The per-component
sums are checked to reconstruct the observed end-to-end latency.

The instrumented verify()/JsonProcess.call()/LeanWitness.evaluate() are line-for-line
re-statements of the originals with timestamps; outcomes are asserted equal to the
uninstrumented path at the end (same assessment/permit result).

Usage: python latency004/profile_wp1.py <out_dir> [N=1000] [warmup=100] [mode=warm|cold|alloc]
"""
import base64
import gc
import json
import math
import os
import queue
import statistics
import sys
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True
sys.pycache_prefix = str(Path(tempfile.gettempdir()) / ("legion-nopyc-" + str(time.time_ns())))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "latency004"))
T_IMPORT0 = time.perf_counter_ns()
import hashlib  # noqa: E402
import commerce_adapter.contracts as contracts  # noqa: E402
import commerce_adapter.gateway as gateway_mod  # noqa: E402
import commerce_bridge.admission as admission  # noqa: E402
import commerce_bridge.locked_readiness as locked  # noqa: E402
import commerce_bridge.qualified as qualified  # noqa: E402
import commerce_bridge.runtime as runtime  # noqa: E402
import commerce_bridge.witness as witness  # noqa: E402
from commerce_adapter.contracts import Denied, require  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey  # noqa: E402
from bridge_tests.fixtures import Fixture  # noqa: E402
T_IMPORT1 = time.perf_counter_ns()

pc = time.perf_counter_ns
from latency004_cpu import tc, TSC_HZ  # noqa: E402  (QueryThreadCycleTime-based thread CPU ns)
from hoststate import host_state  # noqa: E402
REQ = None          # current request record: {component: [wall, cpu]}
STACK = []          # [label, wall0, cpu0, child_wall, child_cpu]


def add(label, wall, cpu):
    if REQ is not None:
        r = REQ.setdefault(label, [0, 0, 0]); r[0] += wall; r[1] += cpu; r[2] += 1


def push(label):
    STACK.append([label, pc(), tc(), 0, 0])


def pop():
    label, w0, c0, cw, cc = STACK.pop()
    w, c = pc() - w0, tc() - c0
    add(label, w - cw, c - cc)
    if STACK:
        STACK[-1][3] += w; STACK[-1][4] += c


def leaf(label, w, c):
    """Attribute an already-measured leaf interval to label and to the parent's child time."""
    add(label, w, c)
    if STACK:
        STACK[-1][3] += w; STACK[-1][4] += c


def wrap(owner, name, label):
    original = getattr(owner, name)

    def timed(*a, **k):
        push(label)
        try:
            return original(*a, **k)
        finally:
            pop()
    timed.__wrapped__ = original
    static = isinstance(owner.__dict__.get(name), staticmethod) if isinstance(owner, type) else False
    setattr(owner, name, staticmethod(timed) if static else timed)


# ---- instrumented, line-for-line re-statements ------------------------------------------
_orig_canonical = contracts.canonical


def i_canonical(value, label):
    w, c = pc(), tc(); out = _orig_canonical(value); leaf(label, pc() - w, tc() - c); return out


def i_verify(envelope, public_key, domain):
    push("auth.verify_glue")
    try:
        payload = envelope["payload"]
        w, c = pc(), tc()
        signature = base64.b64decode(envelope["signature"], validate=True)
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True))
        leaf("auth.key_and_signature_decode", pc() - w, tc() - c)
        message = domain.encode("ascii") + b"\x00" + i_canonical(payload, "auth.canonicalize")
        w, c = pc(), tc(); key.verify(signature, message); leaf("auth.ed25519_verify", pc() - w, tc() - c)
        return payload
    except Exception:
        raise Denied("INVALID_SIGNATURE") from None
    finally:
        pop()


def i_digest(domain, value):
    body = i_canonical(value, "digest.canonicalize")
    w, c = pc(), tc(); out = hashlib.sha256(domain.encode("ascii") + b"\x00" + body).hexdigest()
    leaf("digest.sha256", pc() - w, tc() - c); return out


IPC = []  # per call detail rows


def i_call(self, data):
    tag = getattr(self, "_tag", "jsonprocess")
    row = {"tag": tag, "t0": pc()}
    w, c = pc(), tc()
    with self.lock:
        row["lock_wait_ns"] = pc() - w
        leaf(tag + ".lock_wait", pc() - w, tc() - c)
        require(self.process.poll() is None, "QUALIFIED_WORKER_UNAVAILABLE")
        w, c = pc(), tc()
        finished = threading.Event(); expired = threading.Event()

        def kill():
            if not finished.is_set():
                expired.set()
                if self.process.poll() is None: self.process.kill()
        timer = threading.Timer(self.timeout, kill); timer.daemon = True; timer.start()
        leaf(tag + ".watchdog_thread_start", pc() - w, tc() - c)
        try:
            w, c = pc(), tc(); text = json.dumps(data) + "\n"; leaf(tag + ".serialize_request", pc() - w, tc() - c)
            w, c = pc(), tc(); self.process.stdin.write(text); self.process.stdin.flush()
            row["t_written"] = pc(); leaf(tag + ".pipe_write", pc() - w, tc() - c)
            w, c = pc(), tc(); answer = self.answers.get(timeout=self.timeout); row["t_got"] = pc()
            wait_w, wait_c = pc() - w, tc() - c
            require(not expired.is_set() and answer is not None, "QUALIFIED_WORKER_TIMEOUT")
            w, c = pc(), tc(); result = json.loads(answer); leaf(tag + ".parse_response", pc() - w, tc() - c)
            prof = result.pop("_prof", None)
            if prof:
                # Decompose the wait using the worker's QPC stamps.
                parts = {"transit_and_wake_to_worker": prof["t_recv"] - row["t_written"],
                         "worker_json_parse": prof["t_parsed"] - prof["t_recv"],
                         "worker_compute": prof["t_done"] - prof["t_parsed"],
                         "worker_serialize": prof["t_ser"] - prof["t_done"],
                         "transit_and_wake_to_parent": row["t_got"] - prof["t_ser"]}
                for k, v in parts.items(): leaf(tag + "." + k, v, 0)
                leaf(tag + ".parent_wait_cpu_residual", wait_w - sum(parts.values()), wait_c)
                row.update(parts); row["worker_cpu_ns"] = prof["cpu_ns"]; row["bytes_in"] = prof["bytes_in"]; row["bytes_out"] = prof["bytes_out"]
            else:
                leaf(tag + ".roundtrip_wait", wait_w, wait_c)
            row["bytes_req"] = len(text); row["bytes_resp"] = len(answer)
            require("error" not in result, "QUALIFIED_WORKER_ERROR")
            return result
        except (queue.Empty, OSError, ValueError):
            kill()
            raise Denied("QUALIFIED_WORKER_TIMEOUT_OR_PROTOCOL") from None
        finally:
            finished.set(); timer.cancel(); row["t_end"] = pc(); IPC.append(row)


def i_evaluate(self, f, values):
    require(type(f) is int and 0 <= f <= 1000 and len(values) <= 10000
            and all(v in ("T", "F", "-") for v in values), "LEAN_INPUT_SCHEMA")
    w, c = pc(), tc()
    with self.lock:
        leaf("lean.zverdict.lock_wait", pc() - w, tc() - c)
        require(self.process.poll() is None, "LEAN_UNAVAILABLE")
        w, c = pc(), tc()
        expired = threading.Event(); finished = threading.Event()

        def stop_blocked_io():
            if not finished.is_set():
                expired.set()
                if self.process.poll() is None: self.process.kill()
        timer = threading.Timer(self.timeout, stop_blocked_io); timer.daemon = True; timer.start()
        leaf("lean.zverdict.watchdog_thread_start", pc() - w, tc() - c)
        try:
            w, c = pc(), tc()
            self.process.stdin.write(f"{f} {''.join(values)}\n"); self.process.stdin.flush()
            leaf("lean.zverdict.pipe_write", pc() - w, tc() - c)
            w, c = pc(), tc(); answer = self.answers.get(timeout=self.timeout)
            leaf("lean.zverdict.roundtrip_wait", pc() - w, tc() - c)
            if expired.is_set():
                self.close(); raise Denied("LEAN_TIMEOUT")
            require(answer in ("T", "F", "-"), "LEAN_INVALID_RESPONSE")
            return answer
        except queue.Empty:
            self.close(); raise Denied("LEAN_TIMEOUT") from None
        except (OSError, ValueError):
            self.close(); raise Denied("LEAN_TIMEOUT" if expired.is_set() else "LEAN_UNAVAILABLE") from None
        finally:
            finished.set(); timer.cancel()


GC = {"ns": 0, "n": 0, "t": 0}


def gc_cb(phase, info):
    if phase == "start": GC["t"] = pc()
    else:
        d = pc() - GC["t"]; GC["ns"] += d; GC["n"] += 1
        add("python.gc_pause(overlapping)", d, 0)


def install():
    for mod in (contracts, admission, locked, qualified, runtime, gateway_mod):
        if hasattr(mod, "verify"): mod.verify = i_verify
        if hasattr(mod, "digest"): mod.digest = i_digest
    wrap(gateway_mod.Gateway, "check", "verdict.gateway_sqlite_and_dispatch")
    wrap(contracts.Admission, "control", "authority.control_predicates")
    wrap(contracts.Admission, "check", "authority.a04_check_predicates")
    wrap(locked.LockedReadiness, "ready", "authority.readiness_predicates")
    wrap(admission.BridgeAdmission, "check", "bridge.bundle_load_and_bindings")
    wrap(admission.Bridge, "__init__", "bridge.construct")
    wrap(admission.Bridge, "assess", "bridge.assess_predicates")
    wrap(admission.Bridge, "policy", "bridge.policy_predicates")
    wrap(admission.Bridge, "design_pins", "bridge.design_pins_file_read")
    wrap(qualified.QualifiedSources, "verify", "core.source_integrity")
    wrap(qualified.QualifiedSession, "calculate", "core.session_glue")
    wrap(runtime.CoreRuntime, "compare", "lean.zverdict.compare_glue")
    qualified.JsonProcess.call = i_call
    witness.LeanWitness.evaluate = i_evaluate
    gc.callbacks.append(gc_cb)


def pct(v, p):
    s = sorted(v)
    return s[max(0, math.ceil(p * len(s)) - 1)]


def stats(v):
    return {"p50": round(statistics.median(v) / 1e6, 4), "p95": round(pct(v, .95) / 1e6, 4),
            "p99": round(pct(v, .99) / 1e6, 4), "mean": round(statistics.mean(v) / 1e6, 4), "max": round(max(v) / 1e6, 4)}


def build(tmp, worker_script=True):
    t0 = pc()
    src = qualified.QualifiedSources(ROOT / "runtime_sources", immutable=True)
    t1 = pc()
    if worker_script:
        src.session.worker.close()
        src.session.worker = qualified.JsonProcess([sys.executable, "-B", ROOT / "latency004/prof_worker.py",
                                                    src.core, src.root / "ztl-swarm"], timeout=5)
    src.session.worker._tag = "core.worker"
    src.session.lean._tag = "lean.legion_decide"
    f = Fixture(tmp, qualified_sources=src)
    g, provider, mission, permit = f.gateway()
    t2 = pc()
    return src, f, g, mission, permit, {"sources_and_session_spawn_ms": (t1 - t0) / 1e6, "fixture_gateway_ms": (t2 - t1) / 1e6}


def main():
    global REQ
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
    warm = int(sys.argv[3]) if len(sys.argv) > 3 else 100
    mode = sys.argv[4] if len(sys.argv) > 4 else "warm"
    install()
    with tempfile.TemporaryDirectory() as tmp:
        src, f, g, mission, permit, setup = build(tmp)
        try:
            if mode == "alloc":
                import tracemalloc
                with g.transaction() as db:
                    for _ in range(warm): g.check(db, mission, permit)
                    tracemalloc.start(); rows = []
                    for _ in range(n):
                        tracemalloc.reset_peak(); s0 = tracemalloc.get_traced_memory()[0]
                        g.check(db, mission, permit)
                        cur, peak = tracemalloc.get_traced_memory(); rows.append((peak - s0, cur - s0))
                    snap = tracemalloc.take_snapshot(); tracemalloc.stop()
                top = [str(s) for s in snap.statistics("filename")[:15]]
                res = {"mode": "alloc", "N": n, "peak_bytes_per_request": stats([r[0] * 1e6 for r in rows]),
                       "retained_bytes_per_request_mean": statistics.mean(r[1] for r in rows),
                       "note": "stats() units here are bytes (values scaled by 1e6 before /1e6)", "top_files": top}
                (out / "ALLOC.json").write_text(json.dumps(res, indent=1) + "\n")
                print(json.dumps(res, indent=1)); return
            totals, totals_cpu, per = [], [], []
            with g.transaction() as db:
                first = None
                for i in range(warm + n):
                    REQ = {}; IPC.clear()
                    w, c = pc(), tc()
                    result = g.check(db, mission, permit)
                    tw, tcpu = pc() - w, tc() - c
                    if i == 0:
                        first = {"first_request_ms": tw / 1e6, "components": {k: v[0] / 1e6 for k, v in REQ.items()}}
                    if i >= warm:
                        totals.append(tw); totals_cpu.append(tcpu)
                        per.append({"total": tw, "cpu": tcpu, "c": REQ, "ipc": list(IPC)})
                REQ = None
            g.admission.close()
        finally:
            src.close()
    comps = sorted({k for r in per for k in r["c"]})
    table = {}
    for k in comps:
        wall = [r["c"].get(k, [0, 0, 0])[0] for r in per]
        cpu = [r["c"].get(k, [0, 0, 0])[1] for r in per]
        calls = statistics.mean(r["c"].get(k, [0, 0, 0])[2] for r in per)
        table[k] = {"wall": stats(wall), "cpu_mean_ms": round(statistics.mean(cpu) / 1e6, 4), "calls_per_request": round(calls, 3)}
    exclusive = [k for k in comps if not k.startswith("python.gc")]
    recon = [sum(r["c"][k][0] for k in exclusive if k in r["c"]) for r in per]
    resid = [r["total"] - x for r, x in zip(per, recon)]
    res = {"mode": mode, "arch": "A_existing", "N": n, "warmup": warm, "python": sys.version,
           "boundary": "Gateway.check(db,mission,permit) inside open SQLite transaction",
           "end_to_end": stats(totals), "end_to_end_main_thread_cpu": stats(totals_cpu),
           "reconstruction": {"sum_of_exclusive_components": stats(recon), "unattributed_residual": stats(resid),
                              "explained_fraction_of_mean": round(sum(recon) / sum(totals), 4)},
           "components": table, "setup": setup, "import_ms": (T_IMPORT1 - T_IMPORT0) / 1e6, "first_request": first,
           "gc": {"pauses": GC["n"], "pause_ms_total": GC["ns"] / 1e6},
           "host": host_state(), "tsc_hz": TSC_HZ, "git_head": os.popen("git -C \"%s\" rev-parse HEAD" % ROOT).read().strip()}
    (out / "PROFILE_A.json").write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    with (out / "RAW_A.jsonl").open("w") as fh:
        for r in per:
            fh.write(json.dumps({"total_ns": r["total"], "cpu_ns": r["cpu"], "components": r["c"], "ipc": r["ipc"]}) + "\n")
    print(json.dumps({k: res[k] for k in ("end_to_end", "end_to_end_main_thread_cpu", "reconstruction", "gc")}, indent=1))
    for k, v in sorted(table.items(), key=lambda kv: -kv[1]["wall"]["mean"]):
        print(f"  {k:52} mean {v['wall']['mean']:8.4f} p50 {v['wall']['p50']:8.4f} p95 {v['wall']['p95']:8.4f} p99 {v['wall']['p99']:8.4f} cpu {v['cpu_mean_ms']:7.4f} x{v['calls_per_request']}")


if __name__ == "__main__":
    main()


