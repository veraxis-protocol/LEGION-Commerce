"""WO-COM-AUTHORITY-005: enrollment, trust anchor, confirmation, role-restricted signing, revocation, rotation.

Nothing here can create acceptance or Core/CAGE/VEIP evidence by itself: every signature needs the
role's custodian passphrase; the admission issuer path re-executes the qualified admission assessment;
the acceptance role is only provisioned for a designated independent reviewer.
"""
import base64
import hashlib
import json
import os
import time
from pathlib import Path

from commerce_adapter.contracts import Denied, digest, require, verify
from .custody import Store, load_public, sign
from .roles import (DEDICATED, DOMAINS, ENROLLMENT_DOMAIN, OPERATIONAL_ENVIRONMENT, REVOCATION_DOMAIN, ROLES,
                    SELFTEST_DOMAIN, TRUST_CONFIRMATION_DOMAIN)


# ---------------------------------------------------------------- enrollment (proof of possession)
def enroll(store, role, kid, passphrase, environment=OPERATIONAL_ENVIRONMENT):
    e = store.entry(kid)
    require(e["role"] == role and e["status"] == "PENDING_ENROLLMENT", "ENROLLMENT_STATE")
    payload = {"schema": ENROLLMENT_DOMAIN, "role": role, "key_id": kid, "public_key": e["public_key"],
               "custodian": e["custodian"], "custody": e["custody"], "environment": environment,
               "created_at": e["created_at"], "enrolled_at": int(time.time()), "nonce": base64.b64encode(os.urandom(16)).decode()}
    private = store.unseal(role, kid, passphrase)
    envelope = sign(private, ENROLLMENT_DOMAIN, payload)
    # Rotation: a newly enrolled key supersedes the role's previous ACTIVE key (revoked keys stay revoked).
    for old in [x for x in store.registry()["keys"] if x["role"] == role and x["status"] == "ACTIVE"]:
        store.set_status(old["key_id"], "RETIRED", superseded_by=kid, retired_at=payload["enrolled_at"])
    store.set_status(kid, "ACTIVE", enrolled_at=payload["enrolled_at"],
                     enrollment_sha256=hashlib.sha256(json.dumps(envelope, sort_keys=True).encode()).hexdigest())
    return envelope


def check_enrollment(envelope, role, environment=OPERATIONAL_ENVIRONMENT):
    p = envelope["payload"]
    require(p.get("schema") == ENROLLMENT_DOMAIN and p.get("role") == role, "ENROLLMENT_ROLE")
    require(p.get("environment") == environment, "ENROLLMENT_ENVIRONMENT")       # no fallback to test keys
    load_public(p["public_key"])
    verify(envelope, p["public_key"], ENROLLMENT_DOMAIN)                          # proof of possession
    require(p["key_id"] == hashlib.sha256(base64.b64decode(p["public_key"])).hexdigest()[:24], "ENROLLMENT_KEY_ID")
    require(p["custody"].get("passphrase_gated") is True, "ENROLLMENT_CUSTODY")
    return p


# ---------------------------------------------------------------- trust anchor
def build_trust(enrollments, revoked_key_ids=(), environment=OPERATIONAL_ENVIRONMENT):
    """Exactly one verified enrollment per role -> trust.json bytes (frozen A04 format) + report."""
    require(set(enrollments) == set(ROLES), "TRUST_REQUIRES_ALL_THREE_ROLES")
    payloads = {r: check_enrollment(enrollments[r], r, environment) for r in ROLES}
    keys = {r: payloads[r]["public_key"] for r in ROLES}
    require(len(set(keys.values())) == 3, "TRUST_ROLE_SEPARATION")
    require(not (set(p["key_id"] for p in payloads.values()) & set(revoked_key_ids)), "REVOKED_KEY_IN_TRUST")
    # Separation of custody: the acceptance reviewer and the admission issuer must not share the
    # control custodian's identity or Windows account.
    names = {r: payloads[r]["custodian"].strip().lower() for r in ROLES}
    sids = {r: (payloads[r]["custody"].get("host", ""), payloads[r]["custody"].get("windows_account_sid", "")) for r in ROLES}
    require(names["acceptance"] not in (names["control"], names["admission"]), "ACCEPTANCE_CUSTODIAN_NOT_INDEPENDENT")
    require(sids["acceptance"] not in (sids["control"], sids["admission"]), "ACCEPTANCE_CUSTODY_ACCOUNT_SHARED")
    require(names["admission"] != names["control"] or sids["admission"] != sids["control"], "ADMISSION_CUSTODY_NOT_SEPARATE")
    data = (json.dumps(keys, indent=2, sort_keys=True) + "\n").encode("ascii")
    report = {"schema": "commerce.authority.trust-build.v1", "trust_sha256": hashlib.sha256(data).hexdigest(),
              "roles": {r: {"key_id": payloads[r]["key_id"], "custodian": payloads[r]["custodian"],
                            "windows_account_sid": payloads[r]["custody"].get("windows_account_sid"),
                            "host": payloads[r]["custody"].get("host"),
                            "enrollment_sha256": hashlib.sha256(json.dumps(enrollments[r], sort_keys=True).encode()).hexdigest()} for r in ROLES}}
    return data, report


def check_trust_file(data, expected_sha256):
    """Same checks as commerce_bridge.installation.checked_trust, on bytes."""
    require(len(expected_sha256) == 64 and hashlib.sha256(data).hexdigest() == expected_sha256, "OPERATOR_TRUST_FINGERPRINT")
    trust = json.loads(data)
    require(set(trust) == set(ROLES) and len(set(trust.values())) == 3, "TRUST_ROLE_SEPARATION")
    for v in trust.values(): load_public(v)
    return trust


def confirm_trust(store, trust_bytes, typed_prefix, passphrase, now=None):
    """Operator (control custodian) confirms the anchor: types the fingerprint prefix shown, signs a receipt."""
    sha = hashlib.sha256(trust_bytes).hexdigest()
    require(typed_prefix.strip().lower() == sha[:16], "OPERATOR_FINGERPRINT_CONFIRMATION_MISMATCH")
    trust = check_trust_file(trust_bytes, sha)
    e = store.active("control")
    require(trust["control"] == e["public_key"], "CONFIRMING_KEY_NOT_THE_ANCHORED_CONTROL_KEY")
    private = store.unseal("control", e["key_id"], passphrase)
    payload = {"schema": TRUST_CONFIRMATION_DOMAIN, "trust_sha256": sha, "trust": trust, "confirmed_at": int(now or time.time()),
               "confirmed_by": e["custodian"], "statement": "I, the control-role custodian, confirm this Commerce trust anchor."}
    return sign(private, TRUST_CONFIRMATION_DOMAIN, payload)


# ---------------------------------------------------------------- role-restricted signing
def role_sign(store, role, domain, payload, passphrase, allow_dedicated=False):
    require(role in ROLES, "UNKNOWN_ROLE")
    require(domain in DOMAINS[role], "DOMAIN_NOT_PERMITTED_FOR_ROLE")             # cross-role substitution refused
    require(allow_dedicated or domain not in DEDICATED, "DOMAIN_REQUIRES_DEDICATED_PATH")
    require(isinstance(payload, dict) and payload.get("schema") == domain, "PAYLOAD_SCHEMA_DOMAIN_MISMATCH")
    e = store.active(role)
    private = store.unseal(role, e["key_id"], passphrase)
    return sign(private, domain, payload)


def selftest(store, role, passphrase):
    """Harmless round trip with the real key; the domain is accepted by no admission check."""
    e = store.active(role)
    env = role_sign(store, role, SELFTEST_DOMAIN, {"schema": SELFTEST_DOMAIN, "role": role, "at": int(time.time())}, passphrase)
    verify(env, e["public_key"], SELFTEST_DOMAIN)
    return {"role": role, "key_id": e["key_id"], "selftest": "PASS"}


def sign_permit(store, passphrase, permit_payload, trust, mission, control, bundle, sources, intent_index=0, assess=None):
    """Admission issuer: sign commerce.permit.v1 ONLY over a fresh eligible issuer request.

    The qualified admission assessment is re-executed here (not trusted from a file) and the
    permit must bind exactly its hash, the observation hash and the bundle hash."""
    from commerce_bridge.admission import Bridge
    current = verify(control, trust["control"], "commerce.control.v1")
    intent = mission["payload"]["effects"][intent_index]
    assessment = (assess or (lambda: Bridge(trust, sources).assess(mission, current, bundle, intent)))()
    require(assessment["decision"] == "ALLOW_ISSUER_REQUEST_ONLY" and assessment["dispatch_authorized"] is False, "NOT_AN_ELIGIBLE_ISSUER_REQUEST")
    require(assessment["core"].get("qualified", {}).get("lean", {}).get("verdict") == "T", "QUALIFIED_VERDICT_NOT_T")
    p = permit_payload
    require(p.get("schema") == "commerce.permit.v1", "PAYLOAD_SCHEMA_DOMAIN_MISMATCH")
    require(p["bridge_assessment_hash"] == digest("commerce.bridge.assessment.v1", assessment)
            and p["core_observation_hash"] == assessment["observation_hash"]
            and p["bridge_evidence_hash"] == digest("commerce.bridge.bundle.v1", bundle)
            and p["mission_hash"] == assessment["mission_hash"] and p["epoch"] == assessment["epoch"]
            and p["intent"] == intent, "PERMIT_NOT_BOUND_TO_ASSESSMENT")
    return role_sign(store, "admission", "commerce.permit.v1", p, passphrase, allow_dedicated=True), assessment


# ---------------------------------------------------------------- revocation and rotation
def revoke(store, key_id, reason, control_passphrase, now=None):
    """Control custodian signs a revocation record; the key is refused for signing immediately and
    excluded from any new trust anchor. Its removal from INSTALLED trust takes effect when the
    rotated, operator-confirmed trust.json is installed (A04 has no revocation list)."""
    target = store.entry(key_id)
    require(target["status"] != "REVOKED", "ALREADY_REVOKED")
    record = {"schema": REVOCATION_DOMAIN, "key_id": key_id, "role": target["role"], "public_key": target["public_key"],
              "reason": reason, "revoked_at": int(now or time.time())}
    # A revoked control key cannot sign its own revocation after the fact, but may sign it first.
    envelope = role_sign(store, "control", REVOCATION_DOMAIN, record, control_passphrase)
    store.set_status(key_id, "REVOKED", revoked_at=record["revoked_at"], reason=reason)
    return envelope


def revoked_ids(store):
    return [e["key_id"] for e in store.registry()["keys"] if e["status"] == "REVOKED"]

