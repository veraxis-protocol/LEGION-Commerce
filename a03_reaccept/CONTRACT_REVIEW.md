# A03 re-acceptance: payment contract review (owner decision 2026-11-08: conditional GO)

**Owner decision.** Conditional GO. Three steps must happen first, and until they do, production stays inactive:
1. verify the payment contract;
2. re-sign with a trusted key;
3. run an independent payment test with a confirmed result.

**This package covers step 1 only.** It does not sign anything and creates no authority. It made no network call and no payment.

## What the acceptance binds
`commerce_adapter.contracts.Admission.ready()` refuses to admit unless the signed `commerce.a03.acceptance.v1` matches the running code. It checks:
- `runtime_source_hashes`: every `commerce_adapter/*.py`, compared with the running bytes;
- `tdd_sha256` and `source_pins`;
- A03-T01..T13, all PASS;
- the evidence hash set;
- the expiry.

`bridge_source_hashes` is bound by the installation manifest. So any change to these files breaks the acceptance and the production profile fails closed with `A03_ADAPTER_CORRESPONDENCE`.

**No signed acceptance exists on this host.** WP4 of QUALIFY-002 was blocked because operator keys were missing, and the latest artifact is the UNSIGNED v4 review request of 57f32fa. "Re-sign" therefore means the designated independent reviewer signs the **current** sources for the first time.

## Bound files changed since the v4 review request (57f32fa → 61dd814)

| Set | File | v4 → now | Change |
|---|---|---|---|
| runtime | `commerce_adapter/gateway.py` | `865928f3…` → `164b414c…` | FORMAL-001/003, described below |
| bridge | `commerce_bridge/admission.py` | `8acfc094…` → `caee3fcd…` | adds `lean_attestation()`; `BridgeAdmission.check` sets `last_decision` |
| bridge | `commerce_bridge/prepared.py` | `2b5344aa…` → `b50dd67e…` | `PreparedAdmission.check` sets `last_decision` |

All other bound hashes are unchanged.

## Contract semantics: only restrictions were added
1. **`Gateway.check`**
   - Clears any earlier Lean attestation **before** the admission runs.
   - Afterwards it stores `(id(db), admission.last_decision)`, so the attestation exists only for that open transaction.
2. **`Gateway.require_lean`**
   - Applies to every non-GET `call`, and also runs before the `CREATE_IN_FLIGHT` and `CAPTURE_IN_FLIGHT` state changes.
   - It needs all of the following:
     - the same transaction;
     - `lean_verdict == "T"`;
     - `action_digest == digest("commerce.intent.v1", intent)` for **this** effect's stored intent.
   - The attestation is single-use: it is cleared after each POST.
   - It journals `LEAN_ATTESTED` in the dispatch transaction.
   - `MOCK_ONLY` test providers are exempt. A real provider (`PayPalSandbox`) is not.
3. **Re-check on dispatch.** `create` and `capture` re-run `recheck` → `check` inside the dispatch transaction, so a fresh attestation exists right before the POST. A stale or foreign attestation from an earlier transaction never matches `id(db)` of a live connection.
4. **`lean_attestation()`** returns `None` unless the qualified sources produced a Lean verdict. The v1/ZTL-only path therefore cannot reach PayPal (B-COM-2). An F or Z verdict is refused because T is required.
5. **Unchanged.** The payment terms in `contracts.py` did not change:
   - USD only;
   - `0 < cap_cents ≤ 50000`;
   - permit window ≤ 600 s;
   - quote expiry;
   - epoch and revocation;
   - `scope == paypal.sandbox.orders.create_capture`;
   - all five gates true and verdict T;
   - intent ∈ mission effects;
   - merchant allow-list;
   - the sandbox-only approval host.

   No check was removed or weakened.

## Consequence for the reviewer
- **Hot path changed.** `prepared.py` and `admission.py` are BCh hot-path files, so `hot_path_identical_to_measured` is now **false**. The COMMERCE_LATENCY_004 measurements were taken at `ca897f7`. A latency claim for 61dd814 needs a fresh measurement. Correctness does not depend on it.
- **Decider unchanged.** It is still `99cda684…`, and it is now reproducible bit-for-bit from source (R2 `formal_closure/pin_repro`).

## Re-run on 61dd814
The results are in `evidence/`, and `RUN.log` is the summary. See `REVIEW_REQUEST_v5_UNSIGNED.json` for the hashes the signer must bind.

| Suite (61dd814, dirty=0, 2026-11-08) | Result |
|---|---|
| A04 unittest | 42/42 OK |
| A03 v1 conformance | 13/13 OK |
| BCh qualification (`qualify_evidence/run-20261108T125621Z-BCh`) | A03 13/13; model 0/1200 and alpha 0/1000 mismatches; 0 false authorizations; p95 8.69 ms (performance FAIL against the 5 ms target, as before) |
| Authority adversarial | 73/73; 58 attacks; 0 unauthorized admissions |
| FORMAL-001 negatives | 6/6; 0 forbidden POSTs |

**Remaining before production:**
1. Independent signature of `REVIEW_REQUEST_v5_UNSIGNED.json` → `acceptance_must_bind`. The signer must not be Claude or the operator.
2. The operator's mission, control and policy, plus the issuer's permit.
3. install → preflight.
4. Independent payment test with a confirmed result.
5. Owner activation.
