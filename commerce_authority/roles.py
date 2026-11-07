"""WO-COM-AUTHORITY-005: the three Commerce signing roles and what each may sign.

A role key may sign ONLY its own domains. Cross-role signing is refused by the signer
and, independently, rejected by A04 verification (each domain is verified against
exactly one trust role).
"""

ROLES = ("control", "admission", "acceptance")

ENROLLMENT_DOMAIN = "commerce.authority.enrollment.v1"
TRUST_CONFIRMATION_DOMAIN = "commerce.authority.trust_confirmation.v1"
REVOCATION_DOMAIN = "commerce.authority.revocation.v1"
SELFTEST_DOMAIN = "commerce.authority.selftest.v1"

# Domains verified by the frozen A03/A04 interface, per trust role (contracts.py, admission.py,
# installation.py, locked_readiness.py).
DOMAINS = {
    "control": {"commerce.mission.v1", "commerce.control.v1", "commerce.bridge.policy.v1",
                TRUST_CONFIRMATION_DOMAIN, REVOCATION_DOMAIN},
    "admission": {"commerce.permit.v1"},
    "acceptance": {"commerce.a03.acceptance.v1", "commerce.upstream.acceptance.v1", "commerce.bridge.installation.v1"},
}
# Every role signs its own enrollment (proof of possession) and may sign a harmless self-test payload.
for _r in ROLES:
    DOMAINS[_r] |= {ENROLLMENT_DOMAIN, SELFTEST_DOMAIN}

# Domains that the generic `sign` command refuses: they need a dedicated, checked path.
DEDICATED = {"commerce.permit.v1": "sign-permit (re-executes the qualified admission assessment first)"}

OWNERS = {
    "control": "Veraxis operator: original purchasing mission, budget, policy, epochs, revocation",
    "admission": "separately authorized LEGION admission issuer: execution permits over an eligible issuer request only",
    "acceptance": "independently designated technical reviewer: qualification acceptance and installation manifests",
}

OPERATIONAL_ENVIRONMENT = "sandbox-operational"
