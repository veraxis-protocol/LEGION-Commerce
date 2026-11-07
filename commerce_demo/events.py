"""Append-only, hash-chained event logs for DEMO-006 (chronology is verifiable by the Judge)."""
import hashlib
import json
import threading
import time

_lock = threading.Lock()


class Log:
    def __init__(self, path, attempt_id):
        self.path, self.attempt_id = path, attempt_id
        self.seq, self.prev = 0, "0" * 64
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                e = json.loads(line); self.seq, self.prev = e["seq"], e["hash"]

    def emit(self, kind, agent=None, **data):
        with _lock:
            self.seq += 1
            e = {"seq": self.seq, "at": time.time(), "attempt_id": self.attempt_id, "kind": kind, "agent": agent, "data": data, "prev": self.prev}
            e["hash"] = hashlib.sha256(json.dumps(e, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
            self.prev = e["hash"]
            with self.path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(e, sort_keys=True, default=str) + "\n")
            return e


def verify_chain(path):
    prev, seq, attempts = "0" * 64, 0, set()
    for line in path.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        h = e.pop("hash")
        if e["prev"] != prev or e["seq"] != seq + 1:
            return False, f"CHAIN_BREAK_AT_{e['seq']}", attempts
        if hashlib.sha256(json.dumps(e, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest() != h:
            return False, f"HASH_MISMATCH_AT_{e['seq']}", attempts
        prev, seq = h, e["seq"]; attempts.add(e["attempt_id"])
    return True, "OK", attempts
