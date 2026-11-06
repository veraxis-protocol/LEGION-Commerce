"""WO-COM-LATENCY-004 WP4: worker crash / restart / timeout and concurrency trials (BC; A as reference).

1. crash_during_request : the worker is killed after dispatch, before its answer -> that request DENIES;
                          the next request restarts the worker (snapshot still active) and is evaluated normally.
2. crash_while_idle     : worker killed between requests -> next request restarts it (A: denies forever).
3. hang_timeout         : worker suspended (NtSuspendProcess) mid-request -> deadline kill -> DENY:QUALIFIED_WORKER_TIMEOUT
                          within the bound; next request restarts.
4. restart_refused_after_session_close : after sources.close() no restart and every request denies.
5. concurrency          : T threads x K admissions each, interleaving valid, forged-permit and revoked-control
                          requests on the SAME prepared admission object; every outcome must equal its single-thread
                          expectation (no cross-request cache contamination).
Invariants recorded: unauthorized effects (provider calls) = 0, false admissions = 0, verdict mismatches = 0.

Usage: python latency004/robustness.py <out_dir> [threads=8] [per_thread=150]
"""
import base64
import copy
import ctypes
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from arch import Denied, Fixture, build_gateway, outcome, sources_for  # noqa: E402
from hoststate import host_state  # noqa: E402
import commerce_bridge.consolidated as consolidated  # noqa: E402


def suspend(pid):
    ntdll = ctypes.WinDLL("ntdll"); k = ctypes.WinDLL("kernel32")
    k.OpenProcess.restype = ctypes.c_void_p
    h = k.OpenProcess(0x0800, False, pid)  # PROCESS_SUSPEND_RESUME
    ntdll.NtSuspendProcess.argtypes = [ctypes.c_void_p]
    ntdll.NtSuspendProcess(h); k.CloseHandle(ctypes.c_void_p(h))


def check(g, m, pe):
    with g.transaction() as db:
        g.check(db, m, pe)


def main():
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    threads = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    per = int(sys.argv[3]) if len(sys.argv) > 3 else 150
    res = {"host": host_state(), "trials": {}}
    src = sources_for("BC")
    S = consolidated.ConsolidatedSession
    try:
        with tempfile.TemporaryDirectory() as tmp:
            f = Fixture(tmp + "/fx", qualified_sources=src)
            g, provider, m, pe = build_gateway(f, tmp + "/g", "BC", src)
            session = src.session
            T = res["trials"]
            T["baseline"] = [outcome(lambda: check(g, m, pe)) for _ in range(3)]
            # 1. crash during request
            orig = S.dispatch
            def crashing(self, *a):
                t = orig(self, *a); self.child.process.kill(); self.child.process.wait(); return t
            S.dispatch = crashing
            first = outcome(lambda: check(g, m, pe))
            S.dispatch = orig
            r0 = session.restarts
            T["crash_during_request"] = {"crashed_request": first, "next": outcome(lambda: check(g, m, pe)),
                                         "restarts": session.restarts - r0, "provider_calls": len(provider.calls)}
            # 2. crash while idle
            session.child.process.kill(); session.child.process.wait(); r0 = session.restarts
            T["crash_while_idle"] = {"next": outcome(lambda: check(g, m, pe)), "restarts": session.restarts - r0}
            # 3. hang -> deadline
            session.child.timeout = 0.5
            orig = S.dispatch
            def hanging(self, *a):
                t = orig(self, *a); suspend(self.child.process.pid); return t
            S.dispatch = hanging
            t0 = time.perf_counter(); hung = outcome(lambda: check(g, m, pe)); waited = time.perf_counter() - t0
            S.dispatch = orig
            session.child.timeout = consolidated.PARENT_TIMEOUT; r0 = session.restarts
            T["hang_timeout"] = {"hung_request": hung, "seconds_to_denial": round(waited, 3), "bound_seconds": 0.5,
                                 "next": outcome(lambda: check(g, m, pe)), "restarts": session.restarts - r0}
            # 5. concurrency on one prepared admission object (direct admission calls; the ledger lock is bypassed
            #    on purpose so requests truly overlap inside the admission code)
            adm = g.admission
            ctl_env = f.sign("control", "commerce.control.v1", f.control)
            revoked = copy.deepcopy(f.control); revoked["revoked_permits"] = [pe["payload"]["permit_id"]]
            rev_env = f.sign("control", "commerce.control.v1", revoked)
            sig = bytearray(base64.b64decode(pe["signature"])); sig[5] ^= 4
            forged = dict(pe, signature=base64.b64encode(bytes(sig)).decode())
            kinds = {"valid": (ctl_env, pe, "ALLOW"), "forged_permit": (ctl_env, forged, "DENY:INVALID_SIGNATURE"),
                     "revoked_control": (rev_env, pe, "DENY:REVOKED_PERMIT")}
            names = list(kinds); tally = {"total": 0, "mismatch": 0, "false_admission": 0, "by_kind": {}}
            lock = threading.Lock()
            def worker(tid):
                for i in range(per):
                    kind = names[(tid + i) % len(names)]
                    c_env, p_env, expect = kinds[kind]
                    got = outcome(lambda: adm.check(m, p_env, adm.control(c_env)))
                    with lock:
                        tally["total"] += 1
                        b = tally["by_kind"].setdefault(kind, {})
                        b[got] = b.get(got, 0) + 1
                        if got != expect: tally["mismatch"] += 1
                        if got == "ALLOW" and expect != "ALLOW": tally["false_admission"] += 1
            ts = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
            t0 = time.perf_counter()
            for t in ts: t.start()
            for t in ts: t.join()
            tally["threads"] = threads; tally["seconds"] = round(time.perf_counter() - t0, 2)
            T["concurrency"] = tally
            T["provider_calls_total"] = len(provider.calls)
            adm.close()
        # 4. after close: no restart, deny
        src.close()
        try:
            t = src.session.dispatch({}, {}, 0)
            src.session.collect(t); after = "ALLOW"
        except Denied as e:
            after = "DENY:" + str(e)
        res["trials"]["restart_refused_after_session_close"] = after
    finally:
        try: src.close()
        except Exception: pass
    T = res["trials"]
    res["checks"] = {
        "crash_during_request_denied": T["crash_during_request"]["crashed_request"].startswith("DENY:QUALIFIED_WORKER"),
        "restart_after_crash_serves": T["crash_during_request"]["next"] == "ALLOW" and T["crash_while_idle"]["next"] == "ALLOW",
        "hang_denied_within_bound": T["hang_timeout"]["hung_request"] == "DENY:QUALIFIED_WORKER_TIMEOUT"
                                    and T["hang_timeout"]["seconds_to_denial"] < 0.5 + 0.2,
        "restart_after_hang_serves": T["hang_timeout"]["next"] == "ALLOW",
        "closed_session_denies": T["restart_refused_after_session_close"].startswith("DENY:"),
        "concurrency_zero_mismatch": T["concurrency"]["mismatch"] == 0 and T["concurrency"]["false_admission"] == 0,
        "unauthorized_effects_zero": T["provider_calls_total"] == 0}
    res["pass"] = all(res["checks"].values())
    (out / "ROBUSTNESS.json").write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    print(json.dumps(res["trials"], indent=1)); print(json.dumps(res["checks"], indent=1), "PASS" if res["pass"] else "FAIL")


if __name__ == "__main__":
    main()

