"""WO-COM-LATENCY-004: bounded line-protocol transport to an isolated child process.

Replaces, for the new architectures only, the per-call costs measured in WP1:
* one ``threading.Timer`` thread created per call (0.4-0.6 ms each, 3 per request);
* a reader thread + ``queue.Queue`` hand-off (an extra thread wake per answer).

Here the caller thread writes and reads the pipe itself. A deadline is enforced by
ONE persistent watchdog thread per child, which polls an armed (generation, deadline)
pair and kills the child if a call exceeds it, so a blocked read/write returns EOF and
the call fails closed. Semantics kept from JsonProcess/LeanWitness: no answer, a late
answer, a malformed answer or a dead child is a denial; nothing is cached; one call in
flight per child (lock). The child is restarted only by an explicit ``restart()``
(the session does that on the NEXT request, never to rescue the failing one).
"""
import ctypes
import json
import os
import subprocess
import threading
import time
from commerce_adapter.contracts import Denied

POLL_SECONDS = 0.02


def disable_power_throttling(handle):
    """Opt the process out of Windows EcoQoS execution-speed throttling (per process, not system).

    Returns True when applied. Background windowless processes are otherwise eligible
    for EcoQoS (lower clock) on Windows 11.
    """
    if os.name != "nt":
        return False
    class STATE(ctypes.Structure):
        _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong), ("StateMask", ctypes.c_ulong)]
    PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1
    state = STATE(1, PROCESS_POWER_THROTTLING_EXECUTION_SPEED, 0)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.SetProcessInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
    ProcessPowerThrottling = 4
    return bool(k.SetProcessInformation(ctypes.c_void_p(int(handle)), ProcessPowerThrottling, ctypes.byref(state), ctypes.sizeof(state)))


def current_process_handle():
    k = ctypes.WinDLL("kernel32"); k.GetCurrentProcess.restype = ctypes.c_void_p
    return k.GetCurrentProcess()


class Child:
    """A persistent child speaking one line in, one line out (bytes)."""

    def __init__(self, command, timeout, codes, env=None, unthrottle=True, spin_seconds=0.0):
        self.command = [str(c) for c in command]
        self.timeout = timeout
        # codes: {"unavailable":..., "timeout":..., "protocol":...} exact A-path denial codes.
        self.codes = codes
        self.env = env
        self.unthrottle = unthrottle
        # Bounded busy-wait for the answer before blocking in ReadFile (experiment E1:
        # avoids the idle wake-up of a thread blocked on the pipe). 0 = always block.
        self.spin_ns = int(spin_seconds * 1e9)
        self._pipe = None
        self.lock = threading.Lock()
        self._armed = None          # (generation, deadline_ns) or None
        self._generation = 0
        self._expired = False
        self._closed = False
        self.process = None
        self.unthrottled = False
        self._spawn()
        self._watchdog = threading.Thread(target=self._watch, daemon=True)
        self._watchdog.start()

    def _spawn(self):
        try:
            # Buffered pipes (bufsize=0 would make readline() issue one ReadFile per byte).
            self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, bufsize=-1, env=self.env,
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            self.process = None  # every call denies with the "unavailable" code (as LeanWitness)
            return
        self.unthrottled = disable_power_throttling(self.process._handle) if self.unthrottle else False
        if os.name == "nt":
            import msvcrt
            self._pipe = msvcrt.get_osfhandle(self.process.stdout.fileno())

    def _watch(self):
        while not self._closed:
            time.sleep(POLL_SECONDS)
            armed = self._armed
            if armed is not None and time.perf_counter_ns() > armed[1] and armed[0] == self._generation:
                self._expired = True
                self._kill()

    def _kill(self):
        p = self.process
        if p is not None and p.poll() is None:
            try: p.kill()
            except OSError: pass

    def alive(self):
        return self.process is not None and self.process.poll() is None

    def send(self, line):
        """Write one request line; arms the deadline. Caller must hold self.lock until receive()."""
        if not self.alive():
            raise Denied(self.codes["unavailable"])
        self._generation += 1; self._expired = False
        self._armed = (self._generation, time.perf_counter_ns() + int(self.timeout * 1e9))
        try:
            self.process.stdin.write(line); self.process.stdin.flush()
        except (OSError, ValueError):
            self._disarm(); self._kill()
            raise Denied(self.codes["timeout"] if self._expired else self.codes["protocol"]) from None

    def receive(self):
        """Read the answer line; any failure kills the child and denies."""
        if self.spin_ns and self._pipe is not None:
            import _winapi
            end = time.perf_counter_ns() + self.spin_ns
            try:
                while _winapi.PeekNamedPipe(self._pipe, 0)[0] == 0 and time.perf_counter_ns() < end:
                    pass
            except OSError:
                pass  # broken pipe: readline below returns EOF and the call denies
        try:
            answer = self.process.stdout.readline()
        except (OSError, ValueError):
            answer = b""
        expired = self._expired
        self._disarm()
        if expired or not answer:
            self._kill()
            raise Denied(self.codes["timeout"] if expired else self.codes["protocol"])
        return answer

    def _disarm(self):
        self._armed = None

    def call(self, line):
        with self.lock:
            self.send(line)
            return self.receive()

    def call_json(self, data):
        try:
            result = json.loads(self.call((json.dumps(data) + "\n").encode()))
        except ValueError:
            self._kill(); raise Denied(self.codes["protocol"]) from None
        return result

    def restart(self):
        with self.lock:
            self._restart_locked()

    def _restart_locked(self):
        self._kill()
        try: self.process.wait(timeout=3)
        except Exception: pass
        self._close_streams()
        self._armed = None; self._expired = False
        self._spawn()

    def _close_streams(self):
        if self.process is None: return
        for s in (self.process.stdin, self.process.stdout):
            try: s.close()
            except Exception: pass

    def close(self):
        self._closed = True
        self._kill()
        if self.process is not None:
            try: self.process.wait(timeout=3)
            except Exception: pass
            self._close_streams()
        self._watchdog.join(timeout=1)

    def __enter__(self): return self
    def __exit__(self, *a): self.close()



