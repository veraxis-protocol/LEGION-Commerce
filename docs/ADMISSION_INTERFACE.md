> **Terms used in this specification.** *A03* is the independent acceptance review of the payment adapter; *A04* is this
> payment adapter; *A05* is an independent payment judge. *Mission*, *control* and *permit* are the signed authorization
> records the adapter requires. These names also appear in the signature domains (for example `commerce.a03.acceptance.v1`),
> so they are kept verbatim below.

# A03 → A04 interface, v1

All envelopes are `{"payload": <object>, "signature": <base64 Ed25519 signature>}`.
Sign exactly `ASCII(domain) || 0x00 || canonical(payload)`. Canonical JSON sorts
keys, uses compact separators and ASCII escaping, and permits only objects with
string keys, lists, strings, integer numbers, booleans and null. Floats are rejected.
Domain-separated SHA-256 digests use the same bytes. Trust roots come from the
operator's accepted authority process; requests cannot supply public keys.

These are the exact fields consumed by this adapter. The A03 bridge must generate
them from actual current evidence and earn source-correspondence acceptance before
live activation. Signed test fixtures are not an acceptable upstream implementation.

## Acceptance owner: `commerce.a03.acceptance.v1`

`schema` equals domain; `status=PASS`; `tdd_sha256` equals the frozen A03 digest;
`source_pins` equals `design/SOURCE_PINS.json.source_pins`; `expires_at` is Unix
seconds. `tests` is exactly A03-T01 through A03-T13, every value PASS.
`adapter_schema=commerce.a04.adapter.v1`; `runtime_source_hashes` contains exactly
every filename and raw SHA-256 of the runtime package's `*.py` files.

`evidence_hashes` binds the actual accepted files beneath `a03-evidence/`, including
PROOF_MAP.json, LEAN_BUILD.txt, AXIOM_AUDIT.txt, FORMAL_INPUTS.jsonl,
LEAN_VERDICTS.jsonl, RUNTIME_VERDICTS.jsonl, MISMATCHES.jsonl, PERMIT_TRACE.jsonl,
NEGATIVE_RESULTS.json. Every file is checked on each admission. Paths escaping
the evidence directory are rejected. The external acceptance owner must review
content and correspondence; hashes alone do not prove those semantics.

## Mission owner: `commerce.mission.v1`

`schema`, `mission_id`, `contract_version`, `contract_hash`, `epoch`, `actor`,
`deadline`, `currency=USD`, `cap_cents` (1–50000), `obligations` (nonempty, unique),
`merchant_ids` and `effects`. Each effect is an exact object:

```json
{
  "obligation_id": "original-payable-obligation",
  "effect_slot": "bundle",
  "actor": "authorized-server-principal",
  "merchant_id": "accepted-sandbox-merchant-id",
  "quote_hash": "domain-separated-quote-digest",
  "prerequisites_hash": "domain-separated-prerequisite-digest",
  "currency": "USD",
  "amount_cents": 100
}
```

The example is schema guidance only, not a signed or executable mission. The
mission signature freezes original obligations and effects. Once a mission ID is
reserved its complete signed mission is immutable in this adapter. Replanning
and legitimately changing authority require a separately accepted future contract;
there is no silent replacement of a stored original mission.

## Admission issuer: `commerce.permit.v1`

`schema`, `permit_id`, `issued_at`, `expires_at` (at most issued_at+600),
`quote_expires_at`, `mission_hash`, `acceptance_hash`, `epoch`, `intent` (an exact
member of mission.effects), `scope=paypal.sandbox.orders.create_capture`, `verdict=T`.
`mission_hash` and `acceptance_hash` are domain-separated digests of the respective
payloads. All five independent booleans must be JSON true: `lean_rule_earned`,
`controller_act`, `cage_allow`, `veip_eligible`, `prerequisites_satisfied`.
The issuer is accountable for grounding these in the A03 accepted bridge, exact
snapshot, signed reports, identity roots, numerical checks and causal state.
The gateway never treats untrusted caller booleans or rationale as authority.

Reservation adds the separate checked budget conjunct atomically. Current signed
control, unexpired mission/quote/permit and unchanged exact intent are checked
again at both create and capture dispatch. Buyer approval is a separate PayPal
precondition and is never represented by a permit boolean.

## Current control owner: `commerce.control.v1`

`schema`, `mission_id`, `mission_hash`, integer `sequence`, integer `epoch`,
`active=true`, `expires_at`, `revoked_permits`. Apply with the CLI to serialize it
into SQLite. New sequence must strictly increase, epoch cannot decrease, and the
revocation set cannot shrink. A newer epoch invalidates earlier permits. Inactive
or expired control denies calls. No caller can use an older signed file to roll
back the authoritative control sequence.

## Handoff to A05

The separate readback command produces normalized IDs, exact cents, status,
observation time and response hashes without payer PII. It does not return
PASS_COMPLETED, delivery acceptance or VCC@1. A05 must independently acquire fresh
provider truth and validate the original nonempty service inventory, chronology,
authority and complete attempt trace. Same-merchant process readback is not a
separate institutional verifier or a trusted external signature anchor.
