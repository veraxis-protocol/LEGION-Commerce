"""Explicit v2 qualification profile; existing operational v1 installer is unchanged.

Persistent processes amortize startup. Readiness checks exact Git bytes; every
request rehashes all imported Python sources and binaries. No authority result
or source-integrity result is cached on the request path.
"""
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from commerce_adapter.contracts import Denied,digest,require
from .runtime import CoreRuntime
from .sources import PINS,Sources
from .witness import LeanWitness

ROOT=Path(__file__).resolve().parent.parent
QUALIFIED="d9d38cd6fd9e93e002b0b5d5135377ce1d33d8c8"
QUALIFIED_DECIDE_SHA256="99cda684f29afba33e69b8f4811d754c499987c30c07ac1c67f19ec250ade73a"
PROFILE="design/QUALIFIED_CORE_PROFILE_v3.json"


class JsonProcess:
    def __init__(self,command,timeout=2):
        self.timeout=timeout; self.lock=threading.Lock(); self.answers=queue.Queue()
        env={k:v for k,v in os.environ.items() if not k.startswith(("STRUCT_","ZTL_","ZVERDICT","PYTHONPATH"))}
        env["PYTHONDONTWRITEBYTECODE"]="1"
        self.process=subprocess.Popen([str(v) for v in command],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,text=True,bufsize=1,env=env,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        def read():
            try:
                for line in self.process.stdout: self.answers.put(line)
            finally: self.answers.put(None)
        self.reader=threading.Thread(target=read,daemon=True); self.reader.start()

    def call(self,data):
        with self.lock:
            require(self.process.poll() is None,"QUALIFIED_WORKER_UNAVAILABLE")
            finished=threading.Event(); expired=threading.Event()
            def kill():
                if not finished.is_set():
                    expired.set()
                    if self.process.poll() is None: self.process.kill()
            timer=threading.Timer(self.timeout,kill); timer.daemon=True; timer.start()
            try:
                self.process.stdin.write(json.dumps(data)+"\n"); self.process.stdin.flush()
                answer=self.answers.get(timeout=self.timeout)
                require(not expired.is_set() and answer is not None,"QUALIFIED_WORKER_TIMEOUT")
                result=json.loads(answer)
                require("error" not in result,"QUALIFIED_WORKER_ERROR")
                return result
            except (queue.Empty,OSError,ValueError):
                kill()
                raise Denied("QUALIFIED_WORKER_TIMEOUT_OR_PROTOCOL") from None
            finally: finished.set(); timer.cancel()

    def close(self):
        if self.process.poll() is None: self.process.kill()
        self.process.wait(timeout=3); self.reader.join(timeout=3)
        self.process.stdin.close(); self.process.stdout.close()
    def __enter__(self): return self
    def __exit__(self,*_): self.close()


class WindowsSnapshot:
    """OS handles deny writes/deletion; immutable import inventory is in worker."""
    def __init__(self,files):
        import ctypes
        from ctypes import wintypes
        require(os.name=="nt","WINDOWS_SNAPSHOT_REQUIRED")
        self.api=ctypes.WinDLL("kernel32",use_last_error=True)
        self.api.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        self.api.CreateFileW.restype=wintypes.HANDLE
        self.api.CloseHandle.argtypes=[wintypes.HANDLE]; self.handles=[]; self.active=True
        try:
            for path,expected in files.items():
                handle=self.api.CreateFileW(str(path),0x80000000,1,None,3,0x80,None)
                require(handle not in (None,ctypes.c_void_p(-1).value),"SNAPSHOT_LOCK_FAILED")
                self.handles.append(handle)
                require(hashlib.sha256(path.read_bytes()).hexdigest()==expected,"SNAPSHOT_SOURCE_DRIFT")
        except BaseException:
            self.close(); raise
    def close(self):
        self.active=False
        for handle in self.handles: self.api.CloseHandle(handle)
        self.handles=[]


class QualifiedSources:
    def __init__(self,root,immutable=False):
        self.root=Path(root).resolve(); self.binary=self.root/"ztl-swarm/lean/.lake/build/bin/zverdict.exe"
        self.core=self.root/"LEGION_QUALIFIED"
        # v3: execute the qualified binary itself (FORMAL_CORE_001 sealed hash), never a local rebuild.
        self.decider=self.root/"qualified_bin/legion_decide.exe"
        require(self.decider.is_file() and hashlib.sha256(self.decider.read_bytes()).hexdigest()==QUALIFIED_DECIDE_SHA256,"QUALIFIED_DECIDER_BINARY")
        self.profile_hash=hashlib.sha256((ROOT/PROFILE).read_bytes()).hexdigest()
        self.policy_pins=json.loads((ROOT/"design/SOURCE_PINS.json").read_text())["source_pins"]
        self.policy_pins=dict(self.policy_pins); self.policy_pins["LEGION"]=QUALIFIED
        self.manifest=Sources(self.root).verify()  # preserve old pinned witnesses too
        def git(*args):
            return subprocess.run(["git","-C",str(self.core),*args],capture_output=True,check=True,timeout=30).stdout
        require(git("rev-parse","HEAD").decode().strip()==QUALIFIED,"QUALIFIED_PIN")
        require(not git("status","--porcelain","--untracked-files=all").strip(),"QUALIFIED_DIRTY")
        tree={line.split("\t",1)[1]:line.split("\t",1)[0].split()[2]
              for line in git("ls-tree","-r",QUALIFIED).decode().splitlines()}
        files={}
        for name in git("ls-files").decode().splitlines():
            if name.endswith((".py",".lean")) or name.endswith(("lean-toolchain","lakefile.toml")):
                raw=(self.core/name).read_bytes()
                blob=hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest()
                require(blob==tree[name],"QUALIFIED_SOURCE_BYTES")
                files[name]=hashlib.sha256(raw).hexdigest()
        self.manifest["LEGION_QUALIFIED"]={"commit":QUALIFIED,"files":files}
        self.manifest["legion_decide_sha256"]=hashlib.sha256(self.decider.read_bytes()).hexdigest()
        self.hot_files={self.core/n:h for n,h in files.items()}
        for name in PINS:
            self.hot_files.update({self.root/name/n:h for n,h in self.manifest[name]["files"].items()})
        self.hot_files[self.binary]=self.manifest["zverdict_exe_sha256"]
        self.hot_files[self.decider]=self.manifest["legion_decide_sha256"]
        self.hot_files[ROOT/PROFILE]=self.profile_hash
        self.snapshot=WindowsSnapshot(self.hot_files) if immutable else None
        self.session=QualifiedSession(self)

    def verify(self):
        require(not self.session.closed,"QUALIFIED_SESSION_CLOSED")
        if self.snapshot is not None:
            require(self.snapshot.active,"IMMUTABLE_SNAPSHOT_CLOSED")
            return self.manifest
        require(all(p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==h for p,h in self.hot_files.items()),"QUALIFIED_SOURCE_DRIFT")
        # New module/package injection must not change the import inventory.
        actual={p.relative_to(self.core).as_posix() for p in self.core.rglob("*.py") if ".lake" not in p.parts}
        expected={n for n in self.manifest["LEGION_QUALIFIED"]["files"] if n.endswith(".py")}
        require(actual==expected,"QUALIFIED_IMPORT_INVENTORY_DRIFT")
        return self.manifest

    def close(self):
        self.session.close()
        if self.snapshot is not None: self.snapshot.close()


class QualifiedSession:
    def __init__(self,sources):
        self.sources=sources; self.closed=False; self.last_profile={}
        self.worker=JsonProcess([sys.executable,"-B",Path(__file__).with_name("qualified_worker.py"),sources.core,sources.root/"ztl-swarm"],timeout=5)
        self.lean=JsonProcess([sources.decider])
        self.witness=LeanWitness(sources.binary)

    def calculate(self,observation,policy,now,witness=None):
        start=time.perf_counter_ns(); manifest=self.sources.verify(); validated=time.perf_counter_ns()
        result=self.worker.call({"observation":observation,"policy":policy,"now":now}); evaluated=time.perf_counter_ns()
        payload=result.pop("qualified_payload"); debug=result.pop("qualified_debug"); runtime=result.pop("qualified_runtime")
        lean=self.lean.call(payload); decided=time.perf_counter_ns()
        require(lean["verdict"]==runtime["verdict"] and dict(lean["grounds"])==runtime["grounds"]
                and sorted(lean["closed"])==runtime["closed"],"QUALIFIED_LEAN_RUNTIME_MISMATCH")
        result["qualified"]={"lean":lean,"runtime":runtime,"payload_hash":digest("commerce.qualified.alpha.v2",payload),
                             "binding":debug["binding"],"profile_sha256":self.sources.profile_hash}
        result["source_manifest"]=manifest
        answer=CoreRuntime(self.sources).compare(result,policy,witness or self.witness)
        ended=time.perf_counter_ns()
        self.last_profile={"source_integrity_ms":(validated-start)/1e6,"core_alpha_ms":(evaluated-validated)/1e6,
            "legion_lean_ms":(decided-evaluated)/1e6,"quorum_and_hash_ms":(ended-decided)/1e6}
        return answer

    def close(self):
        if self.closed: return
        self.closed=True
        for process in (self.worker,self.lean,self.witness): process.close()
