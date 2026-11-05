import hashlib
import json
import subprocess
from pathlib import Path
from commerce_adapter.contracts import require

PINS={"LEGION":"d083c44af2526e38068e0220891cd0bb5fce52af",
      "ztl-swarm":"7d2a8265926aecab9a838b09059bd106941b5c18",
      "ZTL":"e1141c6893af6cf49f363620432ca6aaa0b06301"}
FILES={"LEGION":["tests/__init__.py","tests/legion_overlay/__init__.py","tests/legion_struct/struct_controller.py","tests/legion_struct/world_engine.py","tests/legion_overlay/ztl_service.py"],
       "ztl-swarm":["ztlswarm/__init__.py","ztlswarm/ztlrule.py","lean/ZSwarm.lean","lean/VerdictMain.lean","lean/lean-toolchain","lean/lakefile.toml"],
       "ZTL":["lean/ZLegion.lean","lean/ZRuntimeGate.lean","lean/lean-toolchain","lean/lakefile.toml"]}
FROZEN_INTERFACE_SHA256="adc2ff7f83d642b75ac5baf0865025369312891edda06a29ca0949e580086127"


class Sources:
    def __init__(self,root):
        self.root=Path(root).resolve()
        self.binary=self.root/"ztl-swarm/lean/.lake/build/bin/zverdict.exe"

    def verify(self):
        manifest={}
        interface=Path(__file__).resolve().parent.parent/"docs/ADMISSION_INTERFACE.md"
        require(hashlib.sha256(interface.read_bytes()).hexdigest()==FROZEN_INTERFACE_SHA256,"FROZEN_INTERFACE_DRIFT")
        for name,pin in PINS.items():
            repo=self.root/name
            def git(*args):
                return subprocess.run(["git","-C",str(repo),*args],capture_output=True,check=True,timeout=20).stdout
            require(git("rev-parse","HEAD").decode().strip()==pin,"SOURCE_COMMIT_MISMATCH")
            require(not git("status","--porcelain","--untracked-files=all").strip(),"SOURCE_TRACKED_CHANGES")
            if name=="LEGION":
                require(not (repo/"tests/legion_struct/__init__.py").exists(),"UNPINNED_PACKAGE_INITIALIZER")
            hashes={}
            for relative in FILES[name]:
                actual=(repo/relative).read_bytes()
                require(actual==git("show",pin+":"+relative),"SOURCE_BYTES_MISMATCH")
                hashes[relative]=hashlib.sha256(actual).hexdigest()
            manifest[name]={"commit":pin,"files":hashes}
        require(self.binary.is_file(),"LEAN_UNAVAILABLE")
        manifest["zverdict_exe_sha256"]=hashlib.sha256(self.binary.read_bytes()).hexdigest()
        return manifest


def adapter_hashes():
    root=Path(__file__).resolve().parent.parent
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/"commerce_adapter").glob("*.py")}


def bridge_hashes():
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).resolve().parent.glob("*.py")}


def build_directory():
    root=Path(__file__).resolve().parent.parent
    pointer=root/"bridge_evidence/discovery/LATEST_BUILD.json"
    if not pointer.exists(): return root/"bridge_evidence/discovery"
    directory=(root/json.loads(pointer.read_text())["directory"]).resolve()
    require(directory.is_relative_to(root/"bridge_evidence/discovery"),"BUILD_RECORD_PATH")
    return directory
