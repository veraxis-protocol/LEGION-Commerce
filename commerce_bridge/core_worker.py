"""Isolated execution of actual pinned Core methods. No network or signing keys.

Input observations must be authenticated by the bridge before this worker is
used for admission. A raw worker result is a calculation, never authorization.
"""
import asyncio
import base64
import json
import os
import sys
from datetime import datetime,timezone
from pathlib import Path


def calculate(data,legion,swarm):
    sys.path.insert(0,str(Path(swarm).resolve()))
    sys.path.insert(0,str(Path(legion).resolve()))
    os.environ["LEGION_ZTL_SWARM_DIR"]=str(Path(swarm).resolve())
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from tests.legion_overlay.ztl_service import ZtlEvidenceService
    from tests.legion_struct.struct_controller import StructMissionController
    obs=data["observation"]
    # observers=0 deliberately avoids Core's auto-created demonstration signers.
    controller=StructMissionController(obs["world"],"","",observers=0,f=data["policy"]["f"])
    controller._records=obs["records"]
    controller._effects=obs["effects"]
    if set(obs["controller_state"])!=set(controller.state):
        raise ValueError("CONTROLLER_STATE_INVENTORY")
    controller.state=obs["controller_state"]
    task=controller.tasks[obs["task_id"]]
    expected_facts=controller._facts_for(task)
    action=obs["action"]
    validation_status,_=controller.world.validate_args(action["tool"],action["params"])
    tool=controller.world.tools.get(action["tool"],{})
    preconditions=all(controller.world.predicate(obs["world_state"],p,action["params"])
                      for p in tool.get("native_preconditions",[]))
    clock=lambda:datetime.fromtimestamp(data["now"],timezone.utc)
    service=ZtlEvidenceService(f=data["policy"]["f"],clock=clock,
                               max_age_s=data["policy"]["max_age_seconds"],lean=False)
    for entry in data["policy"]["reporters"]:
        service.agents[entry["agent"]]={"root":entry["root"],"pub":Ed25519PublicKey.from_public_bytes(base64.b64decode(entry["public_key"],validate=True))}
        service.roots[entry["root"]]=entry["basis"]
    submissions=[]
    for report in obs["reports"]:
        submissions.append(service.submit(report))
    facts=[]
    predicate_truths=[]
    for fact_id,predicate in expected_facts:
        facts.append(asyncio.run(service.verdict(fact_id)))
        predicate_truths.append(controller._deps_closed_from(task,obs["records"]) if predicate is None
                                else controller.world.predicate(obs["world_state"],predicate,action["params"]))
    return {"directive":controller._directive_locked(obs["task_id"]),
            "task_actor":task["agent_id"],"task_dependencies":task.get("depends_on",[]),
            "task_write_tool":task.get("write_tool"),"parameter_status":validation_status,
            "native_preconditions_ok":preconditions,"predicate_truths":predicate_truths,
            "world_id":controller.mission_id,"task_ids":sorted(controller.tasks),
            "facts":facts,"submissions":submissions,"core_report_archive":service.archive,
            "core_methods":["StructMissionController._directive_locked","StructMissionController._facts_for","ZtlEvidenceService.submit","ZtlEvidenceService.verdict"],
            "core_observers_generated":0,"core_network_calls":0}


if __name__=="__main__":
    try:
        request=json.load(sys.stdin)
        print(json.dumps(calculate(request,sys.argv[1],sys.argv[2]),sort_keys=True))
    except Exception as error:
        print(json.dumps({"error":"CORE_RUNTIME_ERROR","type":type(error).__name__}))
        raise SystemExit(2)
