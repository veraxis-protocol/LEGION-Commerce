"""WO-COM-LATENCY-004: profiling shim around the UNMODIFIED consolidated worker (B/BC).

Runs consolidated_worker.evaluate() exactly as its main loop does and appends "_prof"
QPC stamps (system-wide clock) and cycle-precise thread CPU time. Profiling only.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "commerce_bridge"))
import consolidated_worker as cw  # noqa: E402  (sets no-bytecode, frozen paths)
sys.path.insert(0, HERE)
from latency004_cpu import tc  # noqa: E402

pc = time.perf_counter_ns
STAMP = {}
_q = cw.qw.qualified


def qualified(*a, **k):
    STAMP["core0"] = pc(); c = tc()
    try:
        return _q(*a, **k)
    finally:
        STAMP["core1"] = pc(); STAMP["core_cpu"] = tc() - c


cw.qw.qualified = qualified
_recv = cw.Child.receive


def receive(self):
    t = pc()
    try:
        return _recv(self)
    finally:
        STAMP["child_wait_ns"] = STAMP.get("child_wait_ns", 0) + pc() - t


cw.Child.receive = receive


def main():
    legion, swarm, decider_exe, zverdict_exe, profile = sys.argv[1:6]
    cw.qw.frozen_imports(legion, swarm)
    command = lambda v: json.loads(v) if v.startswith("[") else [v]
    spin = float(sys.argv[6]) if len(sys.argv) > 6 else 0.0
    child_timeout = float(sys.argv[7]) if len(sys.argv) > 7 else 2.0   # A: JsonProcess/LeanWitness default 2 s
    decider = cw.Child(command(decider_exe), child_timeout, cw.DECIDER_CODES, spin_seconds=spin)
    witness = cw.Child(command(zverdict_exe), child_timeout, cw.WITNESS_CODES, spin_seconds=spin)
    out = sys.stdout.buffer
    for line in sys.stdin.buffer:
        t_recv = pc(); STAMP.clear(); c0 = tc(); t_parsed = t_recv
        healthy = True
        try:
            data = json.loads(line); t_parsed = pc()
            answer = cw.evaluate(data, legion, swarm, decider, witness, profile)
        except cw.CalculationError as error:
            answer = {"denied": "QUALIFIED_WORKER_ERROR", "type": str(error)}
        except Exception as error:
            answer = {"denied": str(error) if isinstance(error, cw.Denied) else "QUALIFIED_WORKER_ERROR"}
            healthy = False
        t_done = pc()
        text = json.dumps(answer, sort_keys=True); t_ser = pc()
        prof = {"t_recv": t_recv, "t_parsed": t_parsed, "t_core0": STAMP.get("core0", t_parsed), "t_core1": STAMP.get("core1", t_parsed),
                "t_done": t_done, "t_ser": t_ser, "child_wait_ns": STAMP.get("child_wait_ns", 0),
                "core_cpu_ns": STAMP.get("core_cpu", 0), "cpu_ns": tc() - c0, "bytes_in": len(line), "bytes_out": len(text)}
        out.write((text[:-1] + ',"_prof":' + json.dumps(prof) + "}\n").encode()); out.flush()
        if not healthy:
            break
    decider.close(); witness.close()


if __name__ == "__main__":
    main()



