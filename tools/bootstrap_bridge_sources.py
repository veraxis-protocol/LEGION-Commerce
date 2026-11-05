"""Reproduce source clones/builds without touching any active Core worktree."""
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from commerce_bridge.sources import PINS,Sources


def main():
    out=ROOT/"bridge_evidence/discovery"/time.strftime("build-%Y%m%dT%H%M%SZ",time.gmtime())
    out.mkdir(parents=True,exist_ok=False)
    sources=ROOT/"runtime_sources"; sources.mkdir(exist_ok=True)
    urls={"LEGION":"https://github.com/veraxis-protocol/LEGION.git","ZTL":"https://github.com/inventor1975/ZTL.git",
          "ztl-swarm":"https://github.com/inventor1975/ztl-swarm.git"}
    for name,pin in PINS.items():
        repo=sources/name
        if not repo.exists():
            subprocess.run(["git","-c","core.autocrlf=false","clone","--no-checkout",urls[name],str(repo)],check=True,capture_output=True)
            subprocess.run(["git","-C",str(repo),"config","core.autocrlf","false"],check=True)
            subprocess.run(["git","-C",str(repo),"checkout","--detach",pin],check=True,capture_output=True)
        assert subprocess.check_output(["git","-C",str(repo),"rev-parse","HEAD"],text=True).strip()==pin,"EXISTING_SOURCE_PIN_MISMATCH"
        assert not subprocess.check_output(["git","-C",str(repo),"status","--porcelain","--untracked-files=no"],text=True).strip(),"EXISTING_SOURCE_DIRTY"
    lake=Path.home()/".elan/toolchains/leanprover--lean4---v4.29.1/bin/lake.exe"
    assert lake.is_file(),"INSTALL_LEAN_4_29_1_REQUIRED"
    pre={"pins":PINS,"toolchain":"leanprover/lean4:v4.29.1","lake_sha256":hashlib.sha256(lake.read_bytes()).hexdigest()}
    (out/"SOURCE_PREFLIGHT.json").write_text(json.dumps(pre,indent=2)+"\n")
    builds=[]
    for name,targets,log in (("ztl-swarm",["zverdict"],"SWARM_BUILD.txt"),("ZTL",["ZLegion","ZRuntimeGate"],"ZTL_BUILD.txt")):
        start=time.monotonic()
        p=subprocess.run([str(lake),"build",*targets],cwd=sources/name/"lean",capture_output=True,timeout=300)
        (out/log).write_bytes(p.stdout+p.stderr)
        builds.append({"repository":name,"targets":targets,"exit_code":p.returncode,"duration_seconds":time.monotonic()-start})
        (out/"BUILD_RESULTS.json").write_text(json.dumps(builds,indent=2)+"\n")
        assert p.returncode==0,"PINNED_LEAN_BUILD_FAILED"
    (ROOT/"bridge_evidence/discovery/LATEST_BUILD.json").write_text(json.dumps({"directory":out.relative_to(ROOT).as_posix()})+"\n")
    print(json.dumps({"build":"PASS","directory":str(out),"source_manifest":Sources(sources).verify()}))


if __name__=="__main__": main()
