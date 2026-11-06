"""WO-COM-LATENCY-004 architecture B: parent side of the consolidated admission worker.

Drop-in replacement for QualifiedSession on a QualifiedSources object: the same
``calculate(observation, policy, now, witness=None)`` contract (returns the fields
Bridge.assess reads plus ``source_manifest``), served by ONE isolated round trip.
``dispatch``/``collect``/``abandon`` let a caller start the pure calculation as soon
as its inputs are authenticated and keep checking in parallel (used by architecture BC).

Fail-closed rules kept: no answer / late answer / malformed answer / dead worker deny
with the A-path codes; a crashed or timed-out worker is never reused for the failing
request; it is restarted at the start of a LATER request, only while the immutable
source snapshot is still active (sources.verify()).
"""
import json
import os
import sys
from pathlib import Path
from commerce_adapter.contracts import Denied
from .transport import Child

WORKER_CODES = {"unavailable": "QUALIFIED_WORKER_UNAVAILABLE", "timeout": "QUALIFIED_WORKER_TIMEOUT",
                "protocol": "QUALIFIED_WORKER_TIMEOUT_OR_PROTOCOL"}
# Worker-internal child deadlines are 2 s each (as A); the parent bound covers both plus the 5 s A worker bound.
PARENT_TIMEOUT = 9


class Ticket:
    __slots__ = ("error", "open")

    def __init__(self):
        self.error = None; self.open = False


class ConsolidatedSession:
    def __init__(self, sources, decider=None, zverdict=None, timeout=PARENT_TIMEOUT, spin_seconds=0.0, child_timeout=2.0):
        self.sources = sources; self.closed = False; self.restarts = 0; self.last_profile = {}
        env = {k: v for k, v in os.environ.items() if not k.startswith(("STRUCT_", "ZTL_", "ZVERDICT", "PYTHONPATH"))}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        arg = lambda v, default: json.dumps([str(x) for x in v]) if isinstance(v, (list, tuple)) else str(v or default)
        self.command = [sys.executable, "-B", Path(__file__).with_name("consolidated_worker.py"), sources.core,
                        sources.root / "ztl-swarm", arg(decider, sources.decider), arg(zverdict, sources.binary), sources.profile_hash, str(spin_seconds), str(child_timeout)]
        self.child = Child(self.command, timeout, WORKER_CODES, env, spin_seconds=spin_seconds)

    # --- split API ---------------------------------------------------------------------
    def dispatch(self, observation, policy, now):
        """Start the calculation; never raises (a failure is reported by collect, at A's position)."""
        t = Ticket()
        self.child.lock.acquire(); t.open = True
        try:
            if self.closed:
                raise Denied("QUALIFIED_SESSION_CLOSED")
            if not self.child.alive():
                self.sources.verify()          # restart only under an active immutable snapshot
                self.child._restart_locked(); self.restarts += 1
            self.child.send((json.dumps({"observation": observation, "policy": policy, "now": now}) + "\n").encode())
        except Denied as e:
            t.error = e
        return t

    def collect(self, ticket):
        try:
            manifest = self.sources.verify()   # same position as QualifiedSession.calculate
            if ticket.error is not None:
                raise ticket.error
            line = self.child.receive()
            try:
                result = json.loads(line)
            except ValueError:
                self.child._kill(); raise Denied(WORKER_CODES["protocol"]) from None
            if "denied" in result:
                raise Denied(result["denied"])
            result["source_manifest"] = manifest
            return result
        finally:
            self._release(ticket)

    def abandon(self, ticket):
        """A parent check denied after dispatch: drain the pure answer so the stream stays in step."""
        if not ticket.open:
            return
        try:
            if ticket.error is None and self.child.alive():
                try: self.child.receive()
                except Denied: pass
        finally:
            self._release(ticket)

    def _release(self, ticket):
        if ticket.open:
            ticket.open = False; self.child.lock.release()

    # --- QualifiedSession-compatible API -------------------------------------------------
    def calculate(self, observation, policy, now, witness=None):
        return self.collect(self.dispatch(observation, policy, now))

    def close(self):
        if self.closed: return
        self.closed = True
        self.child.close()


def install(sources, **kwargs):
    """Replace a QualifiedSources' three-process session by the consolidated one (B)."""
    old = sources.session
    sources.session = ConsolidatedSession(sources, **kwargs)
    old.close()
    return sources.session



