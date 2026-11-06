"""Host power/performance state recorded with every measurement (read-only)."""
import ctypes
import os
import platform
import subprocess
import time
from ctypes import wintypes


class _SPS(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte), ("BatteryLifePercent", ctypes.c_ubyte),
                ("SystemStatusFlag", ctypes.c_ubyte), ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]


def speed_index_ms():
    """Fixed pure-Python work (2e6 loop iterations); lower = faster effective CPU."""
    best = 1e9
    for _ in range(5):
        t = time.perf_counter_ns(); x = 0
        for i in range(2_000_000): x += i
        best = min(best, (time.perf_counter_ns() - t) / 1e6)
    return round(best, 2)


def host_state():
    s = _SPS(); ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s))
    try:
        scheme = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception as e:
        scheme = "unavailable:" + type(e).__name__
    return {"ac_line": {0: "OFFLINE_BATTERY", 1: "ONLINE_AC", 255: "UNKNOWN"}.get(s.ACLineStatus, s.ACLineStatus),
            "battery_percent": s.BatteryLifePercent, "battery_saver_on": bool(s.SystemStatusFlag & 1),
            "power_scheme": scheme, "cpu": platform.processor(), "logical_cpus": os.cpu_count(),
            "python_speed_index_ms_2e6_loop": speed_index_ms(), "at_unix": time.time()}


if __name__ == "__main__":
    import json; print(json.dumps(host_state(), indent=1))
