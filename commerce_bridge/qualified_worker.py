"""Qualification-only persistent calculation. No keys, network or effect API.

The parent authenticates the observation. permit_verified is a signed producer
premise, never a buyer input or a replacement for a genuine Core receipt.
"""
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

# v3: executed code must be the hashed, locked source. Never read or write a
# bytecode cache (a stale/foreign __pycache__/*.pyc is validated only by
# mtime/size and lies outside the source inventory).
sys.dont_write_bytecode=True
sys.pycache_prefix=os.path.join(tempfile.gettempdir(),"legion-nopyc-"+uuid.uuid4().hex)
import importlib.abc
import importlib.util
import importlib.machinery

from core_worker import calculate


def frozen_imports(legion,swarm):
    """Allow only the source inventory checked and locked by the parent.

    Namespace packages are explicit; adding an __init__.py cannot inject code.
    This is import correspondence, not a hostile same-user OS security boundary.
    """
    modules={}; packages=set()
    for root,prefix in ((Path(legion)/"tests","tests"),(Path(swarm)/"ztlswarm","ztlswarm"),
                        (Path(legion)/"tests/legion_struct/causal_007","")):
        for path in root.rglob("*.py"):
            relative=path.relative_to(root).with_suffix("").parts
            name=".".join(([prefix] if prefix else [])+list(relative))
            if name.endswith(".__init__"): name=name[:-9]; packages.add(name)
            elif name=="__init__": continue
            modules[name]=path
            pieces=name.split(".")
            packages.update(".".join(pieces[:n]) for n in range(1,len(pieces)))
    class Frozen(importlib.abc.MetaPathFinder):
        def find_spec(self,fullname,path=None,target=None):
            if fullname in modules:
                return importlib.util.spec_from_file_location(fullname,modules[fullname],submodule_search_locations=[] if fullname in packages else None)
            if fullname in packages:
                spec=importlib.machinery.ModuleSpec(fullname,loader=None,is_package=True)
                spec.submodule_search_locations=[]; return spec
            if fullname.startswith(("tests.","ztlswarm.","causal_007.")):
                raise ModuleNotFoundError("Module outside validated snapshot")
    sys.meta_path.insert(0,Frozen())


_ORACLES={}


def _oracle(legion):
    """Load the qualified oracle once per worker, like every other imported Core
    module. The parent holds the source locked (or rehashes it) for the session,
    so a once-loaded module is the hashed bytes; v2 recompiled it per request."""
    if legion not in _ORACLES:
        import importlib.util
        spec=importlib.util.spec_from_file_location("qualified_oracle",Path(legion)/"formal/differential/diff_generated.py")
        oracle=importlib.util.module_from_spec(spec); spec.loader.exec_module(oracle)
        _ORACLES[legion]=oracle
    return _ORACLES[legion]


def qualified(data,legion,swarm):
    result=calculate(data,legion,swarm)
    folder=Path(legion)/"tests/legion_struct/causal_007"
    if str(folder) not in sys.path: sys.path.insert(0,str(folder))
    import lean_admission as alpha
    import contract_identity as identity
    obs=data["observation"]
    boundary=obs["core_boundary"]
    status=boundary["status"]
    assert status["epoch"]==obs["epoch"] and status["state"]==obs["attempt_state"]
    assert status["contract_sha256"]==identity.mission_sha256(obs["world"])
    action=obs["action"]
    body={"agent_id":result["task_actor"],"task_id":obs["task_id"],"target":action["target"],
          "tool":action["tool"],"args":action["params"],"attempt_uid":status["attempt_uid"],
          "command_id":result["directive"].get("command_id")}
    assert type(boundary["permit_verified"]) is bool
    payload,debug=alpha.build_payload(spec=obs["world"],mission_sha=status["contract_sha256"],status=status,
        permit=boundary["permit"],permit_verified=boundary["permit_verified"],body=body,
        world_records=obs["records"]+obs["effects"],gate_records=boundary["gate_records"],
        readback_records=boundary["readback_records"],used_nonces=boundary["used_nonces"],fenced=boundary["fenced"],
        gate_value="T" if all(result["predicate_truths"]) else "F")
    result["qualified_payload"]=payload
    result["qualified_debug"]=debug
    # Actual qualified Python specification oracle, applied to the same alpha.
    oracle=_oracle(legion)
    import copy
    runtime_input=copy.deepcopy(payload)
    runtime_input["state"]["closed"]=[int(k) for k,v in debug["unintern"].items() if v[0]=="task" and v[1] in debug["py_closed"]]
    verdict,grounds=oracle.oracle(runtime_input)
    result["qualified_runtime"]={"verdict":verdict,"grounds":grounds,"closed":sorted(runtime_input["state"]["closed"])}
    return result


if __name__=="__main__":
    frozen_imports(sys.argv[1],sys.argv[2])
    for line in sys.stdin:
        try: answer=qualified(json.loads(line),sys.argv[1],sys.argv[2])
        except Exception as error: answer={"error":"QUALIFIED_CALCULATION_ERROR","type":type(error).__name__}
        print(json.dumps(answer,sort_keys=True),flush=True)
