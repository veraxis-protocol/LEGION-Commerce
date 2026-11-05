"""Validate operator-supplied public roots and externally signed packages only."""
import hashlib
import json
import os
import shutil
import re
import subprocess
import tempfile
from pathlib import Path
from commerce_adapter.contracts import Denied,canonical,digest,require
from commerce_adapter.gateway import Gateway
from .admission import BridgeAdmission
from .sources import Sources

REQUIRED=("trust.json","a03-acceptance.json","a03-evidence","current-mission.json","current-permit.json","current-control.json","bridge-policy.json","bridge-evidence","installation-manifest.json")
LEDGER_FILES={"ledger.db","ledger.db-wal","ledger.db-shm"}


def read(path):
    def unique(pairs):
        result={}
        for key,value in pairs:
            require(key not in result,"DUPLICATE_JSON_KEY")
            result[key]=value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8-sig"),object_pairs_hook=unique)


def checked_trust(path,expected_sha256):
    require(len(expected_sha256)==64 and hashlib.sha256(Path(path).read_bytes()).hexdigest()==expected_sha256,"OPERATOR_TRUST_FINGERPRINT")
    trust=read(path)
    require(set(trust)=={"control","admission","acceptance"} and len(set(trust.values()))==3,"TRUST_ROLE_SEPARATION")
    import base64
    require(all(len(base64.b64decode(v,validate=True))==32 for v in trust.values()),"ED25519_PUBLIC_ROOT_REQUIRED")
    return trust


def validate_package(root,sources,trust_path,trust_sha256,clock=None):
    root=Path(root).resolve()
    require(not any(p.is_symlink() for p in root.rglob("*")),"PACKAGE_SYMLINK")
    missing=[name for name in REQUIRED if not (root/name).exists()]
    require(not missing,"REQUIRED_PACKAGE_ARTIFACT_MISSING")
    trust=checked_trust(trust_path,trust_sha256)
    require(read(root/"trust.json")==trust,"PACKAGE_TRUST_SUBSTITUTION")
    pins=read(Path(__file__).resolve().parent.parent/"design/SOURCE_PINS.json")
    kwargs={} if clock is None else {"clock":clock}
    admission=BridgeAdmission(trust,read(root/"a03-acceptance.json"),root/"a03-evidence",pins["a03_tdd_sha256"],pins["source_pins"],
        sources=sources,policy_envelope=read(root/"bridge-policy.json"),bundle_dir=root/"bridge-evidence",**kwargs)
    mission,permit,control=(read(root/name) for name in ("current-mission.json","current-permit.json","current-control.json"))
    current=admission.control(control)
    admission.check(mission,permit,current)
    # Require an independently signed manifest for the complete install set.
    from commerce_adapter.contracts import verify
    envelope=read(root/"installation-manifest.json")
    manifest=verify(envelope,trust["acceptance"],"commerce.bridge.installation.v1")
    require(manifest["schema"]=="commerce.bridge.installation.v1" and manifest["status"]=="ACCEPTED"
            and manifest["mission_hash"]==digest("commerce.mission.v1",mission["payload"]),"INSTALLATION_NOT_ACCEPTED")
    files={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
           for p in root.rglob("*") if p.is_file() and p.relative_to(root).as_posix() not in LEDGER_FILES|{"installation-manifest.json"}}
    require(files==manifest["files"],"INSTALLATION_FILE_SET_MISMATCH")
    allowed={"trust.json","a03-acceptance.json","current-mission.json","current-permit.json","current-control.json","bridge-policy.json"}
    for name in files:
        require(name in allowed or name.startswith(("a03-evidence/","bridge-evidence/")),"UNEXPECTED_PACKAGE_FILE")
        path=root/name
        require(not path.is_symlink() and path.resolve().is_relative_to(root)
                and path.suffix.lower() not in (".dpapi",".pem",".key",".pfx",".p12"),"PRIVATE_KEY_OR_LINK_IN_PACKAGE")
    return admission,mission,permit,control,manifest


def gateway_for(root,sources,trust_path,trust_sha256,provider,clock=None):
    admission,mission,permit,control,manifest=validate_package(root,sources,trust_path,trust_sha256,clock)
    gateway=Gateway(Path(root)/"ledger.db",admission,provider)
    current=admission.control(control)
    with gateway.connect() as db:
        stored=db.execute("SELECT sequence,envelope FROM controls WHERE mission_id=?",(current["mission_id"],)).fetchone()
    if stored is None or current["sequence"]>stored["sequence"]:
        gateway.apply_control(current["mission_id"],control)
    else:
        require(current["sequence"]==stored["sequence"] and canonical(control).decode()==stored["envelope"],"CURRENT_CONTROL_FILE_ROLLBACK")
    with gateway.transaction() as db:
        gateway.check(db,mission,permit)
    return gateway,mission,permit


def apply_external_control(root,trust_path,trust_sha256,envelope):
    # Revocation must not depend on an unexpired permit, Lean availability or
    # acceptance of the permit it is revoking. The ledger commit is its boundary.
    from commerce_adapter.contracts import Admission
    root=Path(root)
    trust=checked_trust(trust_path,trust_sha256)
    require(read(root/"trust.json")==trust,"INSTALLED_TRUST_MISMATCH")
    admission=Admission(trust,{},root/"a03-evidence","",{})
    control=admission.control(envelope)
    gateway=Gateway(root/"ledger.db",admission,None)
    gateway.apply_control(control["mission_id"],envelope)
    return {"control":"APPLIED","mission_id":control["mission_id"],"sequence":control["sequence"],"transaction_calls":0}


def install_package(package,destination,sources,trust_path,trust_sha256):
    package,destination=Path(package).resolve(),Path(destination).resolve()
    require(not any((package/name).exists() for name in LEDGER_FILES),"LEDGER_NOT_AN_INSTALL_INPUT")
    validate_package(package,sources,trust_path,trust_sha256)
    require(not destination.exists(),"INSTALLATION_ALREADY_EXISTS")
    destination.parent.mkdir(parents=True,exist_ok=True)
    # Copy only after full validation. No secrets/private keys are accepted.
    stage=Path(tempfile.mkdtemp(prefix="a03-install-",dir=destination.parent))
    try:
        require(os.name=="nt","WINDOWS_INSTALL_REQUIRED")
        shell=Path(os.environ["SystemRoot"])/"System32/WindowsPowerShell/v1.0/powershell.exe"
        identity=subprocess.run([str(shell),"-NoProfile","-NonInteractive","-Command",
             "[Security.Principal.WindowsIdentity]::GetCurrent().User.Value"],capture_output=True,text=True,timeout=10,
             creationflags=subprocess.CREATE_NO_WINDOW)
        sid=identity.stdout.strip()
        require(identity.returncode==0 and re.fullmatch(r"S-1-[0-9-]+",sid),"WINDOWS_OWNER_IDENTITY")
        acl=subprocess.run(["icacls.exe",str(stage),"/inheritance:r","/grant:r",f"*{sid}:(OI)(CI)F",
             "*S-1-5-18:(OI)(CI)F","*S-1-5-32-544:(OI)(CI)F"],capture_output=True,timeout=10,
             creationflags=subprocess.CREATE_NO_WINDOW)
        require(acl.returncode==0,"OPERATOR_DIRECTORY_ACL")
        for p in package.rglob("*"):
            relative=p.relative_to(package)
            if p.is_dir(): (stage/relative).mkdir(exist_ok=True)
            elif p.is_file(): shutil.copyfile(p,stage/relative)
        validate_package(stage,sources,trust_path,trust_sha256)
        os.rename(stage,destination)
    except BaseException:
        # Stage is a verified, task-created child of the exact destination parent.
        require(stage.resolve().parent==destination.parent,"STAGE_PATH_BOUNDARY")
        shutil.rmtree(stage)
        raise
    return destination
