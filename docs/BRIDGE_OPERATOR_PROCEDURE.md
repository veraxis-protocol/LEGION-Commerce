> **AUTHORITY-005 update (2026-11-06):** the owner authorized new, operator-controlled Ed25519 role keys. Provisioning, custody, rotation and revocation are now in `docs/AUTHORITY_CUSTODY.md` and `docs/PROVISIONING_PACKAGES.md` (`python -m commerce_authority`). The control key is enrolled; admission and acceptance remain undesignated. `commerce_bridge` now validates with the qualified BCh runtime by default (`--runtime bch`). Everything below about independent authority, real evidence and no substitute signatures still applies.

# Existing-key operator procedure — no substitute authority

The operator selected existing keys. No key paths/IDs or fingerprints have yet
been supplied to this work order. The bridge must remain operationally BLOCKED
until those actual identities and their authority are verified. Do not register
temporary conformance keys or sign the bridge's own acceptance as a developer.

## 1. Register existing public identities

Provide an independently obtained `trust.json` with distinct Ed25519 raw public
keys (base64, 32 bytes) for `control`, `admission` and `acceptance`. The frozen A04
signature format requires Ed25519. A different algorithm or unverified key lifecycle
requires an explicit separately reviewed interface revision; it is not silently
converted into another authority.

The operator must supply the file's SHA-256 through the trusted operator channel.
The bridge will not use a public key embedded in the document it is verifying as
its trust anchor. Private keys stay in the existing signing service/Windows custody;
this package never asks for their contents and does not generate replacements.

## 2. Supply the actual original mission and authenticated run evidence

The control owner signs the unchanged `commerce.mission.v1` and monotonic
`commerce.control.v1` envelopes defined in the frozen interface. Obligations,
amount, merchant, quote, currency, actor, epoch and deadline must be the real
authorized values; there is no default mission or default $1 authority.

The same control owner delegates a signed `commerce.bridge.policy.v1` containing:

- `mission_hash`, `expires_at`, `source_pins`, policy `f` and `max_age_seconds` (1–300).
- Exact `original_obligations`, a complete `obligation_task_map`, and `world_hash`
  (domain `commerce.core.world.v1`).
- Existing `core_public_key`, `cage_public_key`, `veip_public_key` and enrolled
  reporter entries (`agent`, `root`, `basis`, `source_id`, `public_key`). Actual
  independence provenance must be reviewed; different labels are insufficient.
- `payment_tool`, `payment_target`, `amount_param`, `merchant_param`, `budget_path`.
  The actual Core task/tool schema must support these exact bindings.

An authorized Core observer supplies a signed `commerce.core.observation.v1`:
`mission_hash`, `intent_hash`, `epoch`, `attempt_state=RUNNING`, integer
`observed_at`, `contract_hash`, `quote_hash`, actual `world`, `world_state`,
`task_id`, `records`, `effects`, `controller_state`, `action` (`tool`, `target`,
`params`), signed Core-format `reports` and the actual `claimed_directive`.
The bridge independently recomputes the directive, facts, validation and verdict.
The Core task-attempt epoch and the authority control epoch remain separate data;
both are bound, never silently substituted for one another.

CAGE and VEIP sources independently sign `commerce.cage.authorization.v1` and
`commerce.veip.authorization.v1`. They bind their real ALLOW / ELIGIBLE decision to
the same mission, intent, observation hash, actor, epoch, scope and freshness.
Put these four envelopes in one bundle with keys `policy`, `observation`, `cage`,
`veip`. Their source producers and delegated identities must be real; this bridge
does not turn an unsigned JSON log into a witnessed fact or create their authority.

## 3. Calculate an unsigned issuer request

```powershell
.\.venv\Scripts\python.exe -m commerce_bridge assess --trust <existing-public-trust-file> --trust-sha256 <operator-fingerprint> --mission <signed-mission> --control <signed-control> --bundle <signed-source-bundle> --output <new-assessment-file>
```

The result is `ALLOW_ISSUER_REQUEST_ONLY` with `dispatch_authorized=false`.
It cannot pass A04 without the existing admission issuer's valid signature and
the separate acceptance owner's signature. Denial, absence, expiry or a runtime
mismatch produces no permit or PayPal call.

## 4. Independent review and signatures

The acceptance owner must inspect the preserved compiler and axiom transcripts,
exact source and frozen specification hashes, conformance outcomes and separate
replay results. They must also review actual source/identity provenance, A01/A02
acceptance, operational source correspondence and process/key custody. This is
not a request to approve synthetic observations. A local 13/13 test result or a
second process's replay output is insufficient by itself.

The acceptance envelope retains all original A04 fields. Additional bound fields:
`frozen_interface_sha256`, `bridge_source_hashes`, `native_source_manifest_hash`,
`bridge_policy_hash` and `upstream_acceptance` with A01/A02 PASS. Include signed
`UPSTREAM_A01_ACCEPTANCE.json` and `UPSTREAM_A02_ACCEPTANCE.json` plus their original
hash-bound evidence in `a03-evidence/`. Upstream acceptance domain is
`commerce.upstream.acceptance.v1`, with `work_order`, `status`, `tdd_sha256`,
`source_pins`, `mission_hash`, `expires_at` and nonempty `evidence_hashes`.
Those evidence hashes must also be bound by the A03 acceptance file set.

The existing admission issuer signs the original `commerce.permit.v1` payload,
adding `bridge_assessment_hash`, `core_observation_hash` and
`bridge_evidence_hash` (domain `commerce.bridge.bundle.v1`). Store the exact bundle
at `bridge-evidence/<digest(commerce.permit.locator.v1, permit_id)>.json`.

The external acceptance owner signs a complete install manifest with domain
`commerce.bridge.installation.v1`: `schema`, `status=ACCEPTED`, `mission_hash`,
`files` mapping every immutable install-relative path to raw SHA-256. Exclude only
the manifest itself. Local operational SQLite files are never install inputs.
The measured latency requirement must retain its actual disposition; do not
silently relabel a performance failure as full frozen A03 acceptance.

## 5. Validate and install the externally accepted package

Package entries: the original six A04 prerequisite files/directories, plus
`bridge-policy.json`, `bridge-evidence/`, `installation-manifest.json`.
It contains public trust and signed evidence only. Private keys, DPAPI files,
arbitrary extra files, symlinks, unsigned or hash-mismatched evidence are rejected.

```powershell
.\.venv\Scripts\python.exe -m commerce_bridge install --package <accepted-package-directory> --trust <existing-public-trust-file> --trust-sha256 <operator-fingerprint>
.\.venv\Scripts\python.exe -m commerce_bridge preflight --trust <existing-public-trust-file> --trust-sha256 <operator-fingerprint>
```

The install validates first, stages under the Windows runtime parent, restricts
ACLs to the current Windows user/SYSTEM/local Administrators, verifies again and
renames atomically into `%LOCALAPPDATA%\Veraxis\LEGION-Commerce\a04`.
An existing installation is not overwritten. Current control cannot roll back
SQLite's committed epoch/sequence/revocation state.

Apply a signed revocation or epoch transition with the independent control-plane
command. It remains available when a permit expires, acceptance is broken or Lean
is down; it never needs the permission it is revoking to remain valid.

```powershell
.\.venv\Scripts\python.exe -m commerce_bridge apply-control --control <new-signed-control> --trust <existing-public-trust-file> --trust-sha256 <operator-fingerprint>
```

Its serialized SQLite commit is the local revocation boundary. After that commit,
old package/control files fail closed; they cannot roll back the new sequence.
Already dispatched provider requests cannot be undone by local revocation.

## 6. First genuine Sandbox effect

Only after accepted A03 and genuine preflight PASS:

```powershell
.\.venv\Scripts\python.exe -m commerce_bridge create --trust <existing-public-trust-file> --trust-sha256 <operator-fingerprint>
.\.venv\Scripts\python.exe -m commerce_bridge serve-approval --effect <returned-effect-id> --trust <existing-public-trust-file> --trust-sha256 <operator-fingerprint>
```

Open the returned PayPal Sandbox URL and authenticate as the actual Sandbox buyer.
Do not paste buyer credentials into the terminal or chat. The callback is only a
trigger: the server still requires authoritative APPROVED status, exact terms,
current admission and budget before capture. Run `independent-readback` with the
same effect ID in another process to fetch fresh order and capture GETs.

If observation/permit freshness expires, a signature is missing or the provider
state is uncertain, retain BLOCKED and the reservation. Do not create fresh effect
IDs, regenerate authority, assume consent or count a safe stop as mission completion.
