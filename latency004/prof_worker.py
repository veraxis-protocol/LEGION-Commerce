"""WO-COM-LATENCY-004 WP1: profiling shim around the UNMODIFIED qualified worker.

Runs commerce_bridge/qualified_worker.qualified() exactly as the production
worker loop does, and appends a "_prof" object with QueryPerformanceCounter
stamps (perf_counter_ns is QPC on Windows: one system-wide clock, so stamps are
comparable with the parent's) and thread CPU time. Profiling only.
"""
import json
import os
import sys
import tempfile
import time
import uuid

sys.dont_write_bytecode = True
sys.pycache_prefix = os.path.join(tempfile.gettempdir(), "legion-nopyc-" + uuid.uuid4().hex)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "commerce_bridge"))
import qualified_worker as qw  # noqa: E402
sys.path.insert(0, HERE)
from latency004_cpu import tc  # noqa: E402

if __name__ == "__main__":
    qw.frozen_imports(sys.argv[1], sys.argv[2])
    stdin = sys.stdin
    while True:
        line = stdin.readline()
        t_recv = time.perf_counter_ns()
        if not line:
            break
        c0 = tc()
        try:
            data = json.loads(line); t_parsed = time.perf_counter_ns()
            answer = qw.qualified(data, sys.argv[1], sys.argv[2]); t_done = time.perf_counter_ns()
        except Exception as error:
            answer = {"error": "QUALIFIED_CALCULATION_ERROR", "type": type(error).__name__}
            t_parsed = t_done = time.perf_counter_ns()
        cpu = tc() - c0
        text = json.dumps(answer, sort_keys=True); t_ser = time.perf_counter_ns()
        # Stamps appended after the production serialization was measured.
        prof = {"t_recv": t_recv, "t_parsed": t_parsed, "t_done": t_done, "t_ser": t_ser, "cpu_ns": cpu,
                "bytes_in": len(line), "bytes_out": len(text)}
        sys.stdout.write(text[:-1] + ',"_prof":' + json.dumps(prof) + "}\n"); sys.stdout.flush()

