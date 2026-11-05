import argparse
import json
import sqlite3
from pathlib import Path
from .contracts import Denied, require
from .gateway import Gateway, ProviderError
from .paypal import PayPalSandbox, credential_dir


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def runtime():
    from .contracts import Admission
    root = credential_dir() / "a04"
    config = read(root / "trust.json")
    pins = read(Path(__file__).resolve().parent.parent / "design" / "SOURCE_PINS.json")
    admission = Admission(config, read(root / "a03-acceptance.json"), root / "a03-evidence",
                          pins["a03_tdd_sha256"], pins["source_pins"])
    return root, admission


def main():
    parser = argparse.ArgumentParser(description="Windows-hosted, Sandbox-only A04 gateway")
    parser.add_argument("command", choices=["oauth-check", "preflight", "apply-control", "create", "capture", "reconcile", "attach-order", "status", "serve-approval", "independent-readback"])
    parser.add_argument("--mission")
    parser.add_argument("--permit")
    parser.add_argument("--control")
    parser.add_argument("--effect")
    parser.add_argument("--order")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        if args.command == "oauth-check":
            provider = PayPalSandbox()
            provider.oauth()
            print(json.dumps({"oauth": "PASS", "token_ttl_seconds": provider.ttl, "transaction_calls": 0}))
            return 0
        root, admission = runtime()
        if args.command == "preflight":
            admission.ready()
            print(json.dumps({"preflight": "PASS", "transaction_calls": 0}))
            return 0
        if args.command == "independent-readback":
            from .readback import independent_readback
            print(json.dumps(independent_readback(root / "ledger.db", args.effect, admission, PayPalSandbox())))
            return 0
        if args.command in ("create", "capture", "serve-approval"):
            admission.ready()  # Before constructing a credential-owning writer.
        gateway = Gateway(root / "ledger.db", admission,
                          PayPalSandbox(read_only=args.command not in ("create", "capture", "serve-approval")))
        if args.command == "apply-control":
            envelope = read(args.control)
            gateway.apply_control(envelope["payload"]["mission_id"], envelope)
            output = {"control": "APPLIED"}
        elif args.command == "create":
            eid = gateway.reserve(read(args.mission), read(args.permit))
            gateway.create(eid, args.port)
            output = gateway.status(eid)
        elif args.command == "capture":
            output = {"state": gateway.capture(args.effect)}
        elif args.command == "reconcile":
            output = {"state": gateway.reconcile(args.effect)}
        elif args.command == "attach-order":
            output = {"state": gateway.attach_order(args.effect, args.order)}
        elif args.command == "status":
            output = gateway.status(args.effect)
        elif args.command == "serve-approval":
            from .approval import serve
            serve(gateway, args.effect, args.port)
            return 0
        else:
            raise Denied("UNSUPPORTED_COMMAND")
        print(json.dumps(output))
        return 0
    except (Denied, ProviderError) as error:
        print(json.dumps({"status": "BLOCKED", "code": str(error), "mission_complete": False}))
        return 2
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        print(json.dumps({"status": "BLOCKED", "code": "MISSING_OR_INVALID_LOCAL_CONFIGURATION", "mission_complete": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
