"""Hash/AST inventory only: never publishes Core source or patch text."""
import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
CORE=ROOT/"runtime_sources/LEGION_QUALIFIED"
OLD="d083c44af2526e38068e0220891cd0bb5fce52af"
NEW="d9d38cd6fd9e93e002b0b5d5135377ce1d33d8c8"


def git(*args): return subprocess.run(["git","-C",str(CORE),*args],capture_output=True,check=True).stdout
def tree(pin):
    return {line.split("\t",1)[1]:line.split("\t",1)[0].split()[2] for line in git("ls-tree","-r",pin).decode().splitlines()}
def objects(ids):
    result=subprocess.run(["git","-C",str(CORE),"cat-file","--batch"],input=("\n".join(sorted(ids))+"\n").encode(),capture_output=True,check=True).stdout
    out={}; pos=0
    while pos<len(result):
        end=result.index(b"\n",pos); oid,kind,size=result[pos:end].split(); length=int(size)
        out[oid.decode()]=result[end+1:end+1+length]; pos=end+length+2
    return out
def semantic(raw):
    try:
        parsed=ast.parse(raw)
        for node in ast.walk(parsed):
            if isinstance(node,(ast.Module,ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)) and node.body and isinstance(node.body[0],ast.Expr) and isinstance(node.body[0].value,ast.Constant) and isinstance(node.body[0].value.value,str):
                node.body=node.body[1:]
        return hashlib.sha256(ast.dump(parsed,include_attributes=False).encode()).hexdigest()
    except (SyntaxError,ValueError): return None
def main():
    old,new=tree(OLD),tree(NEW)
    paths=sorted(p for p in old.keys()|new.keys() if old.get(p)!=new.get(p))
    blobs=objects({o for p in paths for o in (old.get(p),new.get(p)) if o})
    critical={"tests/legion_struct/struct_controller.py":"Observer origins collapse to one world-state source, f=0; advisory controller only. Commerce creates no Core demo observers.",
      "tests/legion_struct/struct_plugin.py":"Bound actor, target and args digest requested from authority before CAGE seal; not called by Commerce adapter.",
      "tests/legion_struct/causal_007/attempt_authority.py":"Mission/spec authority and MAC-bound exact action; service is not an operator Ed25519 trust root.",
      "tests/legion_struct/world_server.py":"Actual commit invokes compiled Lean and raw-journal alpha; Commerce now composes alpha+native decision, not this synthetic world actuator.",
      "tests/legion_struct/causal_007/lean_admission.py":"Added actual runtime alpha; trusted content/binding input, single-origin f=0, cap empty/demand zero; journal closure executed in Lean.",
      "tests/legion_struct/causal_007/contract_identity.py":"Domain-separated original mission identity and args digest; retained separately from Commerce intent hash.",
      "tests/legion_struct/causal_007/contract_binding_check.py":"Contract/authority identity checks before runtime; operational identity must be supplied externally.",
      "tests/legion_struct/causal_007/sidecar.py":"Raw merged causal journal and activation replay; no receipt substitution.",
      "tests/legion_struct/supervisor.py":"Requires and forwards Lean binary; test posture remains distinct from operational authority.",
      "tests/legion_struct/mission_host.py":"Forwards qualified Lean executable dependency."}
    rows=[]
    for p in paths:
        a=blobs.get(old.get(p),b""); b=blobs.get(new.get(p),b"")
        before=semantic(a) if p.endswith(".py") and p in old else None
        after=semantic(b) if p.endswith(".py") and p in new else None
        if p in critical: scope="COMMERCE_RELEVANT_REVIEWED"; explanation=critical[p]
        elif p.startswith("formal/LegionCore/"): scope="FORMAL_MODEL"; explanation="Versioned qualified model/build/proof obligation; exact hashes and axiom audit checked. Not a Python refinement proof."
        elif p.startswith("formal/differential/"): scope="QUALIFICATION_HARNESS"; explanation="Qualified comparison oracles and generators; actual imported runtime used by qualification, not authorization."
        elif p.endswith(".py") and before==after and before is not None: scope="AST_IDENTICAL"; explanation="Only non-executable text/format changed after removing docstrings; byte hashes retained."
        elif p.endswith(".py"): scope="OUTSIDE_COMMERCE_REVIEW_REQUIRED"; explanation="Executable difference inventoried; not imported by the Commerce evaluation path. No claim of repository-wide semantic equivalence."
        else: scope="NON_PYTHON_DELTA"; explanation="Repository artifact change; byte/object inventory only, no claim that arbitrary config/prose change is behavior-neutral."
        rows.append({"path":p,"status":"ADDED" if p not in old else "REMOVED" if p not in new else "MODIFIED",
            "old_blob":old.get(p),"new_blob":new.get(p),"old_sha256":hashlib.sha256(a).hexdigest() if p in old else None,
            "new_sha256":hashlib.sha256(b).hexdigest() if p in new else None,"old_ast":before,"new_ast":after,"scope":scope,"explanation":explanation})
    proof_path=Path(r"C:\Users\Administrator\Dropbox\L1\LEGION_100\FORMAL_CORE_001\G5_RELEASE\PROOF_MANIFEST.json")
    proof=json.loads(proof_path.read_text()); expected=proof["source_sha256"]
    proof_checks={p:{"expected":h,"actual":hashlib.sha256((CORE/"formal/LegionCore"/p).read_bytes()).hexdigest()} for p,h in expected.items()}
    unchanged={p:old.get(p)==new.get(p) for p in ("tests/legion_struct/world_engine.py","tests/legion_overlay/ztl_service.py","tests/legion_struct/causal_007/causal_registry.py","tests/legion_struct/causal_007/action_binding.py")}
    out=ROOT/"qualify_evidence/CORRESPONDENCE.json"
    result={"old":OLD,"qualified":NEW,"changed_paths":len(rows),"inventory":rows,"critical_unchanged":unchanged,
        "qualified_proof_manifest_sha256":hashlib.sha256(proof_path.read_bytes()).hexdigest(),"proof_source_checks":proof_checks,
        "proof_sources_match":all(v["actual"]==v["expected"] for v in proof_checks.values()),
        "unreviewed_executable_paths":[r["path"] for r in rows if r["scope"]=="OUTSIDE_COMMERCE_REVIEW_REQUIRED"],
        "WP1":"PARTIAL_EXACT_INVENTORY_AND_COMMERCE_PATH_REVIEW;_REPOSITORY_WIDE_SEMANTIC_REVIEW_AND_EXTERNAL_PROFILE_ACCEPTANCE_PENDING"}
    out.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"changed_paths":len(rows),"unreviewed_executable_paths":len(result["unreviewed_executable_paths"]),"proof_sources_match":result["proof_sources_match"],"output":str(out)}))
if __name__=="__main__": main()
