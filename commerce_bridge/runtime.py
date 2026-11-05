import json
import os
import subprocess
import sys
from pathlib import Path
from commerce_adapter.contracts import Denied,digest,require
from .witness import LeanWitness


class CoreRuntime:
    def __init__(self,sources,witness=None):
        self.sources=sources
        self.witness=witness

    def calculate(self,observation,policy,now):
        # Source correspondence is a precondition of executing the imported Core.
        source_manifest=self.sources.verify()
        env={k:v for k,v in os.environ.items() if not k.startswith(("STRUCT_","ZTL_","ZVERDICT"))}
        env["PYTHONDONTWRITEBYTECODE"]="1"
        command=[sys.executable,"-B",str(Path(__file__).with_name("core_worker.py")),
                 str(self.sources.root/"LEGION"),str(self.sources.root/"ztl-swarm")]
        try:
            p=subprocess.run(command,input=json.dumps({"observation":observation,"policy":policy,"now":now}),
                             capture_output=True,text=True,timeout=10,env=env,
                             creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            require(p.returncode==0,"CORE_RUNTIME_ERROR")
            result=json.loads(p.stdout)
            require("error" not in result,"CORE_RUNTIME_ERROR")
            result["source_manifest"]=source_manifest
        except subprocess.TimeoutExpired:
            raise Denied("CORE_RUNTIME_TIMEOUT") from None
        if self.witness is not None:
            return self.compare(result,policy,self.witness)
        with LeanWitness(self.sources.binary) as witness:
            return self.compare(result,policy,witness)

    def compare(self,result,policy,witness):
        checked=[]
        for fact in result["facts"]:
            seats=["T" if value else "F" for basis in sorted(fact["bases"]) for value in sorted(fact["bases"][basis])]
            lean=witness.evaluate(policy["f"],seats)
            runtime={"ACCEPTED_TRUE":"T","ACCEPTED_FALSE":"F"}.get(fact["state"],"-")
            require(lean==runtime and fact["lean_python_mismatches"]==0,"LEAN_RUNTIME_MISMATCH")
            checked.append({"fact_id":fact["fact_id"],"seats":seats,"lean":lean,"runtime":runtime,
                            "state":fact["state"],"fresh_reports":fact["fresh_reports"],"stale_reports":fact["stale_reports"]})
        result["differential"]=checked
        result["mismatches"]=0
        result["all_facts_true"]=bool(checked) and all(r["lean"]=="T" for r in checked)
        result["calculation_hash"]=digest("commerce.core.calculation.v1",result)
        return result
