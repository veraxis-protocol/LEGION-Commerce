import base64
import hashlib
import json
import re
import time
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class Denied(Exception):
    """Safe, fixed diagnostic code; never include provider bodies or credentials."""


def require(condition, code):
    if not condition:
        raise Denied(code)


def canonical(value):
    def check(v):
        require(type(v) in (dict, list, str, int, bool, type(None)), "NONCANONICAL_TYPE")
        if isinstance(v, dict):
            require(all(type(k) is str for k in v), "NONSTRING_KEY")
            for item in v.values():
                check(item)
        elif isinstance(v, list):
            for item in v:
                check(item)
    check(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def digest(domain, value):
    return hashlib.sha256(domain.encode("ascii") + b"\x00" + canonical(value)).hexdigest()


def verify(envelope, public_key, domain):
    try:
        payload = envelope["payload"]
        signature = base64.b64decode(envelope["signature"], validate=True)
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True)).verify(
            signature, domain.encode("ascii") + b"\x00" + canonical(payload))
        return payload
    except Exception:
        raise Denied("INVALID_SIGNATURE") from None


def cents(value):
    require(type(value) is int and 1 <= value <= 50000, "INVALID_CENTS")
    return f"{value // 100}.{value % 100:02d}"


def parse_cents(value):
    require(type(value) is str and re.fullmatch(r"(0|[1-9][0-9]*)\.[0-9]{2}", value), "INVALID_PROVIDER_AMOUNT")
    return int(value.replace(".", ""))


def provider_id(value):
    require(type(value) is str and re.fullmatch(r"[A-Z0-9]{1,32}", value), "INVALID_PROVIDER_ID")
    return value


class Admission:
    """Trust roots and A03 evidence are operator-owned files, never browser input.

    Acceptance signature is an external acceptance attestation, NOT a Lean proof.
    A03 owner must supply the actual reviewed bridge and source correspondence.
    """
    def __init__(self, trust, acceptance, evidence_dir, a03_hash, source_pins, clock=time.time):
        self.trust = trust
        self.acceptance = acceptance
        self.evidence_dir = evidence_dir
        self.a03_hash = a03_hash
        self.source_pins = source_pins
        self.clock = clock

    def ready(self):
        a = verify(self.acceptance, self.trust["acceptance"], "commerce.a03.acceptance.v1")
        require(a["schema"] == "commerce.a03.acceptance.v1" and a["status"] == "PASS", "A03_NOT_PASS")
        require(a["tdd_sha256"] == self.a03_hash and a["source_pins"] == self.source_pins, "A03_SOURCE_MISMATCH")
        require(a["tests"] == {f"A03-T{i:02}": "PASS" for i in range(1, 14)}, "A03_CASES_MISSING")
        require(a["expires_at"] > self.clock(), "A03_ACCEPTANCE_EXPIRED")
        sources = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in Path(__file__).resolve().parent.glob("*.py")}
        require(a["adapter_schema"] == "commerce.a04.adapter.v1" and a["runtime_source_hashes"] == sources,
                "A03_ADAPTER_CORRESPONDENCE")
        required = {"PROOF_MAP.json", "LEAN_BUILD.txt", "AXIOM_AUDIT.txt", "FORMAL_INPUTS.jsonl",
                    "LEAN_VERDICTS.jsonl", "RUNTIME_VERDICTS.jsonl", "MISMATCHES.jsonl",
                    "PERMIT_TRACE.jsonl", "NEGATIVE_RESULTS.json"}
        require(required <= a["evidence_hashes"].keys(), "A03_EVIDENCE_MISSING")
        root = self.evidence_dir.resolve()
        for name, expected in a["evidence_hashes"].items():
            path = (root / name).resolve()
            require(path.is_relative_to(root) and path.is_file(), "A03_EVIDENCE_MISSING")
            require(hashlib.sha256(path.read_bytes()).hexdigest() == expected, "A03_EVIDENCE_HASH")
        return a

    def control(self, envelope):
        p = verify(envelope, self.trust["control"], "commerce.control.v1")
        require(p["schema"] == "commerce.control.v1" and type(p["sequence"]) is int
                and p["sequence"] >= 0 and type(p["epoch"]) is int, "CONTROL_SCHEMA")
        return p

    def check(self, mission_envelope, permit_envelope, control):
        acceptance = self.ready()
        mission = verify(mission_envelope, self.trust["control"], "commerce.mission.v1")
        permit = verify(permit_envelope, self.trust["admission"], "commerce.permit.v1")
        now = self.clock()
        mh = digest("commerce.mission.v1", mission)
        require(control["mission_hash"] == mh and control["active"] is True
                and control["expires_at"] > now, "CONTROL_INACTIVE")
        require(mission["schema"] == "commerce.mission.v1" and permit["schema"] == "commerce.permit.v1", "SCHEMA")
        require(mission["currency"] == "USD" and type(mission["cap_cents"]) is int
                and 0 < mission["cap_cents"] <= 50000, "MISSION_BUDGET")
        require(mission["obligations"] and len(set(mission["obligations"])) == len(mission["obligations"]), "EMPTY_OR_DUPLICATE_OBLIGATIONS")
        require(mission["deadline"] > now and permit["issued_at"] <= now < permit["expires_at"]
                <= permit["issued_at"] + 600, "EXPIRED_PERMIT")
        require(permit["quote_expires_at"] > now, "STALE_QUOTE")
        require(mission["epoch"] == permit["epoch"] == control["epoch"], "STALE_EPOCH")
        require(permit["permit_id"] not in control["revoked_permits"], "REVOKED_PERMIT")
        require(permit["mission_hash"] == mh and permit["acceptance_hash"] == digest("commerce.a03.acceptance.v1", acceptance), "PERMIT_BINDING")
        gates = ("lean_rule_earned", "controller_act", "cage_allow", "veip_eligible", "prerequisites_satisfied")
        require(all(permit[g] is True for g in gates) and permit["verdict"] == "T"
                and permit["scope"] == "paypal.sandbox.orders.create_capture", "ADMISSION_DENIED")
        intent = permit["intent"]
        require(intent in mission["effects"] and intent["obligation_id"] in mission["obligations"], "ORIGINAL_INTENT_MISMATCH")
        require(intent["actor"] == mission["actor"] and intent["currency"] == mission["currency"]
                and intent["merchant_id"] in mission["merchant_ids"], "SCOPE_MISMATCH")
        require(all(type(intent[k]) is str and intent[k] for k in
                    ("obligation_id", "effect_slot", "actor", "merchant_id", "quote_hash", "prerequisites_hash")), "INTENT_SCHEMA")
        cents(intent["amount_cents"])
        return mission, permit, intent
