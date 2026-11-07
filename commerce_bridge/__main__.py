import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from commerce_adapter.contracts import Denied,digest,require,verify
from commerce_adapter.paypal import PayPalSandbox,credential_dir
from commerce_adapter.gateway import ProviderError
from .admission import Bridge
from .installation import REQUIRED,apply_external_control,checked_trust,gateway_for,install_package,read
from .sources import Sources

ROOT=Path(__file__).resolve().parent.parent


def audit(event):
    directory=ROOT/"bridge_evidence/operator"
    directory.mkdir(parents=True,exist_ok=True)
    # Contains fixed diagnostics/public hashes only; no keys or raw provider data.
    with (directory/"EVENTS.jsonl").open("a",encoding="utf-8",newline="\n") as stream:
        stream.write(json.dumps({"at":int(time.time()),**event},sort_keys=True)+"\n")


def main():
    parser=argparse.ArgumentParser(description="A03 bridge: no key generation or acceptance self-signing")
    parser.add_argument("command",choices=("source-check","preflight","assess","install","apply-control","create","capture","reconcile","serve-approval","independent-readback"))
    parser.add_argument("--sources",default=str(ROOT/"runtime_sources"))
    parser.add_argument("--trust",help="Existing operator-controlled public trust file")
    parser.add_argument("--trust-sha256",help="Fingerprint obtained independently from the operator")
    parser.add_argument("--package")
    parser.add_argument("--mission")
    parser.add_argument("--control")
    parser.add_argument("--bundle")
    parser.add_argument("--intent-index",type=int,default=0)
    parser.add_argument("--output")
    parser.add_argument("--effect")
    parser.add_argument("--port",type=int,default=8765)
    # AUTHORITY-005: operational admission runs the qualified BCh runtime by default
    # (QualifiedSources immutable snapshot + consolidated worker + PreparedAdmission with overlap).
    parser.add_argument("--runtime",choices=("bch","v1"),default="bch")
    args=parser.parse_args()
    qualified=None; admission_class=None
    try:
        sources=Sources(args.sources)
        if args.runtime=="bch" and args.command in ("preflight","install","create","capture","serve-approval") and args.trust:
            import functools
            from .qualified import QualifiedSources
            from .consolidated import install as consolidated
            from .prepared import PreparedAdmission
            qualified=QualifiedSources(args.sources,immutable=True); consolidated(qualified)
            sources=qualified; admission_class=functools.partial(PreparedAdmission,overlap=True)
        if args.command=="source-check":
            result={"source_check":"PASS","manifest":sources.verify(),"operational_authority":False}
        elif args.command=="preflight" and (not args.trust or not args.trust_sha256):
            root=credential_dir()/"a04"
            result={"status":"BLOCKED","code":"EXISTING_OPERATOR_KEY_PATHS_AND_FINGERPRINTS_REQUIRED",
                    "missing":[name for name in REQUIRED if not (root/name).exists()],"transaction_calls":0}
            audit(result)
            print(json.dumps(result)); return 2
        elif args.command=="assess":
            trust=checked_trust(args.trust,args.trust_sha256)
            mission,control=read(args.mission),read(args.control)
            current=verify(control,trust["control"],"commerce.control.v1")
            intent=mission["payload"]["effects"][args.intent_index]
            result=Bridge(trust,sources).assess(mission,current,read(args.bundle),intent)
            if args.output:
                output=Path(args.output)
                with output.open("x",encoding="utf-8",newline="\n") as stream: json.dump(result,stream,indent=2)
            result={"assessment_hash":digest("commerce.bridge.assessment.v1",result),"dispatch_authorized":False,
                    "signature":"EXTERNAL_ADMISSION_ISSUER_REQUIRED","output_written":bool(args.output)}
        elif args.command=="install":
            target=install_package(args.package,credential_dir()/"a04",sources,args.trust,args.trust_sha256,admission_class)
            result={"installation":"VERIFIED_AND_INSTALLED","destination":str(target),"transaction_calls":0}
        elif args.command=="apply-control":
            result=apply_external_control(credential_dir()/"a04",args.trust,args.trust_sha256,read(args.control))
        elif args.command in ("reconcile","independent-readback"):
            # Reads remain available after permit expiry or admission outage.
            # They cannot authorize a POST or clear a reservation.
            from commerce_adapter.__main__ import runtime
            from commerce_adapter.gateway import Gateway
            root,admission=runtime()
            require(read(root/"trust.json")==checked_trust(args.trust,args.trust_sha256),"INSTALLED_TRUST_MISMATCH")
            require((root/"ledger.db").is_file(),"NO_LEDGER_TO_RECONCILE")
            gateway=Gateway(root/"ledger.db",admission,PayPalSandbox(read_only=True))
            if args.command=="reconcile": result={"state":gateway.reconcile(args.effect)}
            else:
                from commerce_adapter.readback import independent_readback
                result=independent_readback(root/"ledger.db",args.effect,admission,gateway.provider)
        else:
            root=credential_dir()/"a04"
            # Validate all signatures/evidence before creating any writer.
            gateway,mission,permit=gateway_for(root,sources,args.trust,args.trust_sha256,None,admission_class=admission_class)
            if args.command=="preflight":
                result={"preflight":"PASS","all_six_a04_prerequisites_valid":True,"bridge_evidence_valid":True,"transaction_calls":0,"runtime":args.runtime}
            else:
                gateway.provider=PayPalSandbox(read_only=args.command in ("reconcile","independent-readback"))
                if args.command=="create":
                    eid=gateway.reserve(mission,permit); gateway.create(eid,args.port); result=gateway.status(eid)
                elif args.command=="capture": result={"state":gateway.capture(args.effect)}
                elif args.command=="reconcile": result={"state":gateway.reconcile(args.effect)}
                elif args.command=="serve-approval":
                    from commerce_adapter.approval import serve
                    serve(gateway,args.effect,args.port); return 0
                elif args.command=="independent-readback":
                    from commerce_adapter.readback import independent_readback
                    result=independent_readback(root/"ledger.db",args.effect,gateway.admission,gateway.provider)
                else: raise Denied("UNSUPPORTED_COMMAND")
        audit({"command":args.command,"result":result})
        print(json.dumps(result))
        return 0
    except (Denied,ProviderError) as error:
        result={"status":"BLOCKED","command":args.command,"code":str(error),"mission_complete":False}
    except (OSError,ValueError,KeyError,TypeError,IndexError,sqlite3.Error):
        result={"status":"BLOCKED","command":args.command,"code":"MISSING_OR_INVALID_OPERATOR_ARTIFACT","mission_complete":False}
    except subprocess.SubprocessError:
        result={"status":"BLOCKED","command":args.command,"code":"SOURCE_OR_CONTROL_SUBPROCESS_FAILURE","mission_complete":False}
    finally:
        if qualified is not None:
            try: qualified.close()
            except Exception: pass
    audit(result)
    print(json.dumps(result))
    return 2


if __name__=="__main__": raise SystemExit(main())
