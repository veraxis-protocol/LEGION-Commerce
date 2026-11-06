"""WO-COM-LATENCY-004 architecture B: consolidated admission worker.

One persistent isolated process executes, per request, exactly what the existing path
splits over three parent-driven round trips:
  1. qualified_worker.qualified()  - unmodified Core alpha + oracle (same function);
  2. the sealed qualified decider (legion_decide 99cda684...) on the alpha payload;
  3. the zverdict witness on every fact's seats;
and the checks QualifiedSession.calculate and CoreRuntime.compare apply to their
answers, with the same denial codes in the same precedence. 2 and 3 are this
process's own children and are driven concurrently (both inputs exist once 1 is done).

No keys, no network, no PayPal credentials: the parent authenticates the observation
and policy before sending them, and keeps every authority check.
Protocol: one JSON line in {"observation","policy","now"}; one JSON line out, either
{"denied": CODE} or the calculation fields Bridge.assess reads.
argv: legion_dir swarm_dir decider_exe zverdict_exe profile_sha256
"""
import json
import os
import sys
import tempfile
import uuid

sys.dont_write_bytecode = True
sys.pycache_prefix = os.path.join(tempfile.gettempdir(), "legion-nopyc-" + uuid.uuid4().hex)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import qualified_worker as qw  # noqa: E402
from commerce_adapter.contracts import Denied, digest  # noqa: E402
from commerce_bridge.transport import Child  # noqa: E402

DECIDER_CODES = {"unavailable": "QUALIFIED_WORKER_UNAVAILABLE", "timeout": "QUALIFIED_WORKER_TIMEOUT",
                 "protocol": "QUALIFIED_WORKER_TIMEOUT_OR_PROTOCOL"}
WITNESS_CODES = {"unavailable": "LEAN_UNAVAILABLE", "timeout": "LEAN_TIMEOUT", "protocol": "LEAN_UNAVAILABLE"}
FIELDS = ("directive", "task_actor", "task_dependencies", "task_write_tool", "world_id", "task_ids",
          "parameter_status", "native_preconditions_ok", "predicate_truths", "differential", "mismatches",
          "all_facts_true", "qualified", "submissions")


def require(condition, code):
    if not condition:
        raise Denied(code)


def seats_of(fact):
    return ["T" if value else "F" for basis in sorted(fact["bases"]) for value in sorted(fact["bases"][basis])]


class CalculationError(Exception):
    pass


def evaluate(data, legion, swarm, decider, witness, profile):
    try:
        result = qw.qualified(data, legion, swarm)       # raises -> QUALIFIED_WORKER_ERROR (as A)
    except Exception as error:
        raise CalculationError(type(error).__name__) from None
    f = data["policy"]["f"]
    payload = result.pop("qualified_payload"); debug = result.pop("qualified_debug"); runtime = result.pop("qualified_runtime")
    seat_lists = [seats_of(fact) for fact in result["facts"]]
    witness_ok = []
    for values in seat_lists:  # LeanWitness.evaluate input schema, checked before its write
        witness_ok.append(type(f) is int and 0 <= f <= 1000 and len(values) <= 10000 and all(v in ("T", "F", "-") for v in values))
    # Concurrent dispatch: both children start computing before either answer is read.
    with decider.lock, witness.lock:
        decider.send((json.dumps(payload) + "\n").encode())
        sent = []
        if seat_lists and witness_ok[0]:
            witness.send(f"{f} {''.join(seat_lists[0])}\n".encode()); sent.append(0)
        answer = decider.receive()
        try:
            lean = json.loads(answer)
        except ValueError:
            decider._kill(); raise Denied(DECIDER_CODES["protocol"]) from None
        require("error" not in lean, "QUALIFIED_WORKER_ERROR")
        require(lean["verdict"] == runtime["verdict"] and dict(lean["grounds"]) == runtime["grounds"]
                and sorted(lean["closed"]) == runtime["closed"], "QUALIFIED_LEAN_RUNTIME_MISMATCH")
        result["qualified"] = {"lean": lean, "runtime": runtime, "payload_hash": digest("commerce.qualified.alpha.v2", payload),
                               "binding": debug["binding"], "profile_sha256": profile}
        checked = []
        for i, fact in enumerate(result["facts"]):
            require(witness_ok[i], "LEAN_INPUT_SCHEMA")
            if i not in sent:
                witness.send(f"{f} {''.join(seat_lists[i])}\n".encode())
            verdict = witness.receive().decode().strip()
            require(verdict in ("T", "F", "-"), "LEAN_INVALID_RESPONSE")
            rt = {"ACCEPTED_TRUE": "T", "ACCEPTED_FALSE": "F"}.get(fact["state"], "-")
            require(verdict == rt and fact["lean_python_mismatches"] == 0, "LEAN_RUNTIME_MISMATCH")
            checked.append({"fact_id": fact["fact_id"], "seats": seat_lists[i], "lean": verdict, "runtime": rt,
                            "state": fact["state"], "fresh_reports": fact["fresh_reports"], "stale_reports": fact["stale_reports"]})
    result["differential"] = checked
    result["mismatches"] = 0
    result["all_facts_true"] = bool(checked) and all(r["lean"] == "T" for r in checked)
    return {k: result[k] for k in FIELDS}


def main():
    legion, swarm, decider_exe, zverdict_exe, profile = sys.argv[1:6]
    qw.frozen_imports(legion, swarm)
    command = lambda v: json.loads(v) if v.startswith("[") else [v]  # list form: fault-injection tests only
    spin = float(sys.argv[6]) if len(sys.argv) > 6 else 0.0
    child_timeout = float(sys.argv[7]) if len(sys.argv) > 7 else 2.0   # A: JsonProcess/LeanWitness default 2 s
    decider = Child(command(decider_exe), child_timeout, DECIDER_CODES, spin_seconds=spin)
    witness = Child(command(zverdict_exe), child_timeout, WITNESS_CODES, spin_seconds=spin)
    out = sys.stdout.buffer
    for line in sys.stdin.buffer:
        healthy = True
        try:
            answer = evaluate(json.loads(line), legion, swarm, decider, witness, profile)
        except CalculationError as error:
            # Before any child was used: identical to A (worker answers {"error"} and stays up).
            answer = {"denied": "QUALIFIED_WORKER_ERROR", "type": str(error)}
        except Exception as error:
            # A child answer may be pending or a child failed: never reuse this process.
            answer = {"denied": str(error) if isinstance(error, Denied) else "QUALIFIED_WORKER_ERROR"}
            healthy = False
        out.write((json.dumps(answer, sort_keys=True) + "\n").encode()); out.flush()
        if not healthy:
            break
    decider.close(); witness.close()


if __name__ == "__main__":
    main()




