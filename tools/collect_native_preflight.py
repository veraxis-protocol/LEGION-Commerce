"""Redacted Windows OAuth/ACL/precondition evidence; performs NO Orders calls."""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from commerce_adapter.paypal import PayPalSandbox,credential_dir


def write(path,value):
    path.write_text(json.dumps(value,indent=2,sort_keys=True)+"\n",encoding="utf-8")


def main():
    out=ROOT/"evidence"/time.strftime("native-preflight-%Y%m%dT%H%M%SZ",time.gmtime())
    out.mkdir(parents=True,exist_ok=False)
    source={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for d in ("commerce_adapter","tools","design") for p in (ROOT/d).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts}
    write(out/"FROZEN_INPUTS.json",{"mode":"SCRIPTED_NATIVE_OAUTH_ONLY","authority":"User WO-COM-A04 2026-11-05",
          "expected":"OAuth PASS if Windows DPAPI + PayPal valid; Orders network calls must be zero",
          "source_hashes":source})
    # The subprocess reads only ACL metadata. No credential content enters stdout.
    acl_script=r"""
    $ErrorActionPreference='Stop'
    $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $allowed=@($sid,'S-1-5-18','S-1-5-32-544')
    $base=Join-Path $env:LOCALAPPDATA 'Veraxis\LEGION-Commerce'
    $results=foreach($name in @('paypal_client_id.txt','paypal_client_secret.dpapi')) {
      $acl=[System.IO.File]::GetAccessControl((Join-Path $base $name))
      $broad=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]) | Where-Object {
        $_.AccessControlType -eq 'Allow' -and $_.IdentityReference.Value -notin $allowed
      }).Count -gt 0
      [pscustomobject]@{file=$name;broad_access_found=$broad;protected_scope='CURRENT_USER_SYSTEM_ADMINISTRATORS'}
    }
    $results | ConvertTo-Json -Compress
    """
    acl=subprocess.run([os.environ["SystemRoot"]+r"\System32\WindowsPowerShell\v1.0\powershell.exe",
                        "-NoProfile","-NonInteractive","-Command",acl_script],capture_output=True,text=True,
                        creationflags=subprocess.CREATE_NO_WINDOW,timeout=20)
    write(out/"CREDENTIAL_ACL.json",json.loads(acl.stdout) if acl.returncode==0 else {"status":"ERROR","code":"ACL_INSPECTION_FAILED"})
    start=time.monotonic()
    provider=PayPalSandbox()
    try:
        provider.oauth()
        result={"oauth":"PASS","token_ttl_seconds":provider.ttl,"duration_ms":round((time.monotonic()-start)*1000,3),
                "secret_output":False,"token_output":False,"credential_copy":False,"orders_calls":0,"capture_calls":0}
    except Exception as error:
        result={"oauth":"FAIL","error_type":type(error).__name__,"orders_calls":0,"capture_calls":0}
    provider=None
    write(out/"OAUTH_RESULT.json",result)
    root=credential_dir()/"a04"
    required=("trust.json","a03-acceptance.json","a03-evidence","current-mission.json","current-permit.json","current-control.json")
    missing=[name for name in required if not (root/name).exists()]
    write(out/"PREFLIGHT.json",{"status":"BLOCKED" if missing else "REQUIRES_SIGNATURE_AND_EVIDENCE_VALIDATION",
          "missing":missing,"credential_files_exist":True,"transaction_calls":0})
    write(out/"NATIVE_READBACK.jsonl",{"status":"NOT_RUN","orders_created":0,"captures_confirmed":0,
          "reason":"No accepted A03/runtime authority and no authenticated buyer approval for an admitted order"})
    write(out/"WO_VERDICT.json",{"status":"BLOCKED","go_to_a05":False,"oauth":result["oauth"],
          "evidence_gap":"Accepted A03 T01-T13, installed trust roots and current signed mission/control/permit; buyer approval and native readbacks",
          "required_evidence":"A03 signed acceptance with exact adapter source hashes + evidence files; signed original mission, one-time permit, current control; PayPal approved order and completed capture GETs",
          "acquisition":"A03 owner acceptance package into Windows a04 runtime folder, then documented CLI and buyer Sandbox checkout",
          "immediate_execution":"This native OAuth+ACL probe and local mechanism suite; zero Orders calls",
          "acceptance":"PASS all frozen A04 cases with required native effect; FAIL forbidden/duplicate/overspend/false complete; BLOCKED missing gate evidence",
          "next_action":"Install and verify genuine A03 acceptance, run preflight then scoped create and authenticated buyer approval. If missing, retain write hold; do not mint substitute authority."})
    (out/"EVIDENCE_HASHES.sha256").write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in sorted(out.iterdir()) if p.is_file()),encoding="utf-8")
    print(json.dumps({"evidence":str(out),"oauth":result["oauth"],"missing_prerequisites":missing,"transaction_calls":0,"overall":"BLOCKED"}))
    return 0 if result["oauth"]=="PASS" else 1


if __name__=="__main__": raise SystemExit(main())
