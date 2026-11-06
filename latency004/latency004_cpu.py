"""Thread CPU time with cycle precision (Windows QueryThreadCycleTime).

time.thread_time_ns() on Windows advances in 15.625 ms scheduler ticks, useless at
sub-millisecond scale. QueryThreadCycleTime counts TSC ticks while the thread runs;
TSC ticks at a constant rate, calibrated here against QPC during a busy spin.
"""
import ctypes
import time
from ctypes import wintypes

_k = ctypes.WinDLL("kernel32")
_k.GetCurrentThread.restype = wintypes.HANDLE
_k.QueryThreadCycleTime.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_ulonglong)]
_h = _k.GetCurrentThread()  # pseudo-handle: always "the calling thread"
_buf = ctypes.c_ulonglong()


def cycles():
    _k.QueryThreadCycleTime(_h, ctypes.byref(_buf)); return _buf.value


def _calibrate():
    best = 0.0
    for _ in range(3):
        c0, t0 = cycles(), time.perf_counter_ns()
        while time.perf_counter_ns() - t0 < 60_000_000: pass
        best = max(best, (cycles() - c0) / ((time.perf_counter_ns() - t0) / 1e9))
    return best


TSC_HZ = _calibrate()


def tc():
    return int(cycles() * 1e9 / TSC_HZ)
