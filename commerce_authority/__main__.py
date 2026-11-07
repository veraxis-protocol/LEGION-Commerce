"""Commerce signing authority CLI (WO-COM-AUTHORITY-005).

Every command that touches a private key asks the role custodian for the passphrase on an
interactive console. Outputs are public (envelopes, enrollments, receipts) and never contain keys.

  python -m commerce_authority status
  python -m commerce_authority init-role   --role control --custodian "<name>"
  python -m commerce_authority selftest    --role control
  python -m commerce_authority build-trust --control E --admission E --acceptance E --out trust.json
  python -m commerce_authority confirm-trust --trust trust.json --out trust-confirmation.json
  python -m commerce_authority sign        --role control --domain commerce.mission.v1 --payload p.json --out env.json
  python -m commerce_authority sign-permit --permit p.json --trust T --trust-sha256 S --mission M --control C --bundle B --out O
  python -m commerce_authority revoke      --key-id K --reason "..." --out revocation.json
  python -m commerce_authority export-backup --role R --to <offline path>
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from commerce_adapter.contracts import Denied
from . import authority as A
from .custody import Store, acl_text, interactive_passphrase, windows_identity
from .roles import OWNERS, ROLES


def out(path, value):
    with open(path, "x", encoding="utf-8", newline="\n") as f:
        json.dump(value, f, indent=2, sort_keys=True); f.write("\n")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main():
    ap = argparse.ArgumentParser(description="LEGION Commerce signing authority (no keys ever printed)")
    ap.add_argument("command", choices=("status", "init-role", "selftest", "build-trust", "confirm-trust", "sign",
                                        "sign-permit", "revoke", "export-backup"))
    for a in ("--role", "--custodian", "--control", "--admission", "--acceptance", "--out", "--trust", "--trust-sha256",
              "--domain", "--payload", "--permit", "--mission", "--bundle", "--key-id", "--reason", "--to", "--sources"):
        ap.add_argument(a)
    args = ap.parse_args()
    store = Store()
    try:
        if args.command == "status":
            reg = store.registry()
            result = {"store": str(store.root), "keys": reg["keys"], "owners": OWNERS,
                      "store_acl": acl_text(store.root) if store.root.exists() else None}
        elif args.command == "init-role":
            assert args.role in ROLES and args.custodian, "--role and --custodian required"
            print(f"Role: {args.role} - {OWNERS[args.role]}\nCustodian: {args.custodian}", file=sys.stderr)
            print("Choose a passphrase only you know (>= 12 chars). It is not shown and never stored.", file=sys.stderr)
            pw = interactive_passphrase("Custodian passphrase: ", confirm=True)
            ident = windows_identity()
            kid = store.generate(args.role, args.custodian, pw, ident)
            env = A.enroll(store, args.role, kid, pw)
            d = store.root / "enrollments"; d.mkdir(exist_ok=True)
            path = d / f"{args.role}-{kid}.json"
            out(path, env)
            result = {"receipt": "commerce.authority.provisioning-receipt.v1", "role": args.role, "key_id": kid,
                      "public_key": env["payload"]["public_key"], "custodian": args.custodian,
                      "windows_account": ident["account"], "windows_sid": ident["sid"], "host": ident["host"],
                      "enrollment_file": str(path), "enrollment_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "private_key": "SEALED (passphrase + DPAPI CurrentUser), never exported"}
        elif args.command == "selftest":
            result = A.selftest(store, args.role, interactive_passphrase(f"{args.role} passphrase: "))
        elif args.command == "build-trust":
            data, report = A.build_trust({"control": read(args.control), "admission": read(args.admission),
                                          "acceptance": read(args.acceptance)}, A.revoked_ids(store))
            with open(args.out, "xb") as f: f.write(data)
            result = report
        elif args.command == "confirm-trust":
            data = Path(args.trust).read_bytes(); sha = hashlib.sha256(data).hexdigest()
            print(f"Trust anchor SHA-256: {sha}\n{data.decode()}", file=sys.stderr)
            typed = input("Type the first 16 hex characters of the fingerprint to confirm: ")
            env = A.confirm_trust(store, data, typed, interactive_passphrase("control passphrase: "))
            out(args.out, env); result = {"trust_sha256": sha, "confirmation": args.out}
        elif args.command == "sign":
            env = A.role_sign(store, args.role, args.domain, read(args.payload), interactive_passphrase(f"{args.role} passphrase: "))
            out(args.out, env); result = {"signed": args.domain, "role": args.role, "output": args.out}
        elif args.command == "sign-permit":
            from commerce_bridge.installation import checked_trust
            from commerce_bridge.qualified import QualifiedSources
            from commerce_bridge.consolidated import install as consolidated
            trust = checked_trust(args.trust, args.trust_sha256)
            sources = QualifiedSources(Path(args.sources or Path(__file__).resolve().parent.parent / "runtime_sources"), immutable=True)
            try:
                consolidated(sources)
                env, assessment = A.sign_permit(store, interactive_passphrase("admission passphrase: "), read(args.permit),
                                                trust, read(args.mission), read(args.control), read(args.bundle), sources)
            finally:
                sources.close()
            out(args.out, env); result = {"signed": "commerce.permit.v1", "assessment_decision": assessment["decision"], "output": args.out}
        elif args.command == "revoke":
            env = A.revoke(store, args.key_id, args.reason, interactive_passphrase("control passphrase: "))
            out(args.out, env); result = {"revoked": args.key_id, "record": args.out,
                                          "next": "rotate the role (init-role), build-trust, confirm-trust, reinstall"}
        elif args.command == "export-backup":
            e = store.active(args.role)
            result = store.export_backup(args.role, e["key_id"], interactive_passphrase(f"{args.role} passphrase: "), args.to)
        print(json.dumps(result, indent=1, sort_keys=True))
        return 0
    except (Denied, AssertionError, OSError, ValueError, KeyError) as e:
        print(json.dumps({"status": "REFUSED", "command": args.command, "code": str(e) if isinstance(e, (Denied, AssertionError)) else type(e).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
