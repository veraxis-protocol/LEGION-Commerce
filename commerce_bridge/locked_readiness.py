"""WP3: immutable, OS-locked readiness inputs for the A04 acceptance check.

The A04 Admission.ready re-reads and re-hashes every accepted evidence file and
every adapter source from disk on each request. Under an immutable qualified
session the same files are opened once with write-denying share modes (no
FILE_SHARE_WRITE), hashed while locked, and held for the session. In-place
modification is then refused by the OS for every process, so the per-request
predicates below are evaluated over bytes that provably cannot change.

Every predicate of Admission.ready and BridgeAdmission.ready is still evaluated
on every request (signature, status, TDD, pins, 13 tests, expiry, adapter
schema, source correspondence, evidence presence/hashes, interface, policy,
upstream signatures/status/expiry/binding). Only the disk I/O is replaced.
Equivalence with the disk path is tested by tools/ready_equivalence.py.
"""
import hashlib
import json
import locale
import os
from pathlib import Path
from commerce_adapter.contracts import digest,require,verify

FILE_SHARE_READ=1
FILE_SHARE_DELETE=4
GENERIC_READ=0x80000000
OPEN_EXISTING=3


class FileLocks:
    """Open files read-only, denying writers; hash under the lock."""
    def __init__(self,paths):
        import ctypes
        from ctypes import wintypes
        require(os.name=="nt","WINDOWS_SNAPSHOT_REQUIRED")
        self.api=ctypes.WinDLL("kernel32",use_last_error=True)
        self.api.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        self.api.CreateFileW.restype=wintypes.HANDLE
        self.api.CloseHandle.argtypes=[wintypes.HANDLE]
        invalid=ctypes.c_void_p(-1).value
        self.handles=[]; self.hashes={}; self.data={}; self.active=True
        try:
            for path in paths:
                # Writers are denied; deletion/rename of the name stays possible
                # (temp cleanup) but cannot alter the locked, verified content.
                handle=self.api.CreateFileW(str(path),GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_DELETE,None,OPEN_EXISTING,0x80,None)
                require(handle not in (None,invalid),"SNAPSHOT_LOCK_FAILED")
                self.handles.append(handle)
                raw=Path(path).read_bytes()
                self.data[path]=raw
                self.hashes[path]=hashlib.sha256(raw).hexdigest()
        except BaseException:
            self.close(); raise

    def close(self):
        self.active=False
        for handle in self.handles: self.api.CloseHandle(handle)
        self.handles=[]


class LockedReadiness:
    REQUIRED={"PROOF_MAP.json","LEAN_BUILD.txt","AXIOM_AUDIT.txt","FORMAL_INPUTS.jsonl",
              "LEAN_VERDICTS.jsonl","RUNTIME_VERDICTS.jsonl","MISMATCHES.jsonl",
              "PERMIT_TRACE.jsonl","NEGATIVE_RESULTS.json"}
    UPSTREAM={"A01":"eac9c274f62ba0ca7c7b4c1b109fb5e69ecbc53a09d29efb96cc98af3378551d",
              "A02":"6ceacae3da2e6acb4bc010db67a0cfc03ac50c6236a77c9f8e66f9eacc93782f"}

    def __init__(self,admission,interface,frozen_interface_sha256):
        import commerce_adapter.contracts as contracts
        self.adm=admission; self.frozen=frozen_interface_sha256
        self.adapter_dir=Path(contracts.__file__).resolve().parent
        self.bridge_dir=Path(__file__).resolve().parent
        adapter=sorted(self.adapter_dir.glob("*.py")); bridge=sorted(self.bridge_dir.glob("*.py"))
        names=admission.acceptance.get("payload",{}).get("evidence_hashes",{})
        root=admission.evidence_dir.resolve(); self.evidence={}; self.escaped=set()
        for name in names:
            path=(root/name).resolve()
            if path.is_relative_to(root) and path.is_file(): self.evidence[name]=path
            else: self.escaped.add(name)
        self.locks=FileLocks(adapter+bridge+[interface]+list(self.evidence.values()))
        h=self.locks.hashes
        self.adapter_hashes={p.name:h[p] for p in adapter}
        self.bridge_hashes={p.name:h[p] for p in bridge}
        self.interface_sha=h[interface]
        self.evidence_hashes={n:h[p] for n,p in self.evidence.items()}
        # Locked text, decoded as Path.read_text() would; parsed per request (as the disk path does).
        encoding=locale.getencoding()
        self.upstream={}
        for order in self.UPSTREAM:
            name=f"UPSTREAM_{order}_ACCEPTANCE.json"
            if name in self.evidence:
                self.upstream[name]=self.locks.data[self.evidence[name]].decode(encoding).replace("\r\n","\n").replace("\r","\n")
        # Native manifest object is immutable for the session (its own locked snapshot).
        self.manifest_obj=admission.sources.verify()
        self.native_manifest_hash=digest("commerce.bridge.sources.v1",self.manifest_obj)

    def close(self): self.locks.close()

    def ready(self):
        adm=self.adm
        require(self.locks.active,"IMMUTABLE_SNAPSHOT_CLOSED")
        # --- exactly the predicates of commerce_adapter.contracts.Admission.ready ---
        a=verify(adm.acceptance,adm.trust["acceptance"],"commerce.a03.acceptance.v1")
        require(a["schema"]=="commerce.a03.acceptance.v1" and a["status"]=="PASS","A03_NOT_PASS")
        require(a["tdd_sha256"]==adm.a03_hash and a["source_pins"]==adm.source_pins,"A03_SOURCE_MISMATCH")
        require(a["tests"]=={f"A03-T{i:02}":"PASS" for i in range(1,14)},"A03_CASES_MISSING")
        require(a["expires_at"]>adm.clock(),"A03_ACCEPTANCE_EXPIRED")
        require(a["adapter_schema"]=="commerce.a04.adapter.v1" and a["runtime_source_hashes"]==self.adapter_hashes,
                "A03_ADAPTER_CORRESPONDENCE")
        require(self.REQUIRED<=a["evidence_hashes"].keys(),"A03_EVIDENCE_MISSING")
        for name,expected in a["evidence_hashes"].items():
            require(name in self.evidence_hashes and name not in self.escaped,"A03_EVIDENCE_MISSING")
            require(self.evidence_hashes[name]==expected,"A03_EVIDENCE_HASH")
        # --- exactly the predicates of BridgeAdmission.ready ---
        require(a["frozen_interface_sha256"]==self.interface_sha==self.frozen,"BRIDGE_INTERFACE_DRIFT")
        require(a["bridge_source_hashes"]==self.bridge_hashes and a["runtime_source_hashes"]==self.adapter_hashes,"BRIDGE_SOURCE_CORRESPONDENCE")
        manifest=adm.sources.verify()  # still per request: session open, snapshot active
        manifest_hash=self.native_manifest_hash if manifest is self.manifest_obj else digest("commerce.bridge.sources.v1",manifest)
        require(a["native_source_manifest_hash"]==manifest_hash,"BRIDGE_NATIVE_SOURCE_CORRESPONDENCE")
        require(a["bridge_policy_hash"]==digest("commerce.bridge.policy.v1",adm.policy_envelope["payload"]),"ACCEPTED_POLICY_MISMATCH")
        require(a["upstream_acceptance"]["A01"]==a["upstream_acceptance"]["A02"]=="PASS","UPSTREAM_NOT_ACCEPTED")
        for order,tdd_hash in self.UPSTREAM.items():
            name=f"UPSTREAM_{order}_ACCEPTANCE.json"
            require(name in a["evidence_hashes"],"UPSTREAM_ACCEPTANCE_ARTIFACT_MISSING")
            upstream=verify(json.loads(self.upstream[name]),adm.trust["acceptance"],"commerce.upstream.acceptance.v1")
            require(upstream["work_order"]==order and upstream["status"]=="PASS"
                    and upstream["tdd_sha256"]==tdd_hash and upstream["source_pins"]==adm.source_pins
                    and upstream["mission_hash"]==adm.policy_envelope["payload"]["mission_hash"]
                    and upstream["expires_at"]>adm.clock() and upstream["evidence_hashes"],"UPSTREAM_NOT_ACCEPTED")
            require(all(a["evidence_hashes"].get(n)==h for n,h in upstream["evidence_hashes"].items()),"UPSTREAM_EVIDENCE_NOT_BOUND")
        return a
