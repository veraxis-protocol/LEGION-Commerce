"""WP3 E1b: does the per-process EcoQoS opt-out (and/or priority) stabilise the CPU speed of OUR processes?

Per-process settings only (SetProcessInformation ProcessPowerThrottling, SetPriorityClass);
no system/power-plan setting is changed. Alternates conditions over time to see drift.
"""
import ctypes, json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from commerce_bridge.transport import disable_power_throttling, current_process_handle
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hoststate import host_state

def loop_ms():
    t = time.perf_counter_ns(); x = 0
    for i in range(1_000_000): x += i
    return (time.perf_counter_ns() - t) / 1e6

class STATE(ctypes.Structure):
    _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong), ("StateMask", ctypes.c_ulong)]
k = ctypes.WinDLL("kernel32", use_last_error=True)
k.SetProcessInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
k.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
h = current_process_handle()
def set_throttle(on):
    s = STATE(1, 1, 1 if on else 0)  # EXECUTION_SPEED: StateMask 1 = EcoQoS on, 0 = explicitly off
    return bool(k.SetProcessInformation(h, 4, ctypes.byref(s), ctypes.sizeof(s)))
def set_default():
    s = STATE(1, 0, 0)  # let the system decide (default)
    return bool(k.SetProcessInformation(h, 4, ctypes.byref(s), ctypes.sizeof(s)))
conds = {"system_default": lambda: set_default(), "ecoqos_off": lambda: set_throttle(False),
         "ecoqos_off+high_priority": lambda: (set_throttle(False), k.SetPriorityClass(h, 0x80))}
res = {c: [] for c in conds}
for rep in range(int(sys.argv[2]) if len(sys.argv) > 2 else 6):
    for c, apply in conds.items():
        apply(); time.sleep(0.3)
        res[c].append(round(min(loop_ms() for _ in range(5)), 2))
    k.SetPriorityClass(h, 0x20)
out = {"loop_1e6_ms_min_of_5": res, "host": host_state()}
print(json.dumps(out, indent=1))
if len(sys.argv) > 1: Path(sys.argv[1]).write_text(json.dumps(out, indent=1) + "\n")
