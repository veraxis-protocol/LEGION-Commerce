# WO-COM-A04 — Atomic money boundary and PayPal Orders v2 Sandbox evidence

**Document:** TECHNICAL DESIGN DOCUMENT (TDD) | **Revision:** v1.1 | **Design date:** 2026-11-05 | **Status:** FROZEN-DESIGN; NOT_RUN (Z)
**Product:** LEGION Commerce / VCC@1
**Scope:** Independent external adapter and read-only provider reconciliation
**Depends on:** A03 consequence permit and A02 goal/obligations ready
**Constraints:** NO Core modifications; NO payment execution before passing safety gates; PayPal Sandbox only.
**Source manifest:** LEGION d083c44af2526e38068e0220891cd0bb5fce52af; ZTL e1141c6893af6cf49f363620432ca6aaa0b06301; ztl-swarm 7d2a8265926aecab9a838b09059bd106941b5c18; ztl-stand f604e071bb0cf929269338d6769c9ab3ec8cc497.

## 1. Purpose and boundary

**H-A04:** at least one real PayPal Sandbox financial effect can be made by the demo ONLY after a current, one-time, formally supported LEGION permission and global budget reservation; a payment effect cannot be duplicated, forged or inferred from a pending request. PayPal's own security, customer consent, idempotency and lifecycle remain authoritative. The test uses sandbox funds only.

**Not allowed:** exposing PayPal credentials to any agent, model prompt, front-end, repository, readback logs or PDF/video; authorizing card-on-file payment without correct PayPal permission and test-account support; treating order CREATED/APPROVED as CAPTURE COMPLETED; running production payments.

## 2. Transaction schema and atomicity

New external module `commerce_adapter`: a single server-only payer gateway. Store durable ledger with uniqueness constraints: `mission_id + obligation_id + contract_version + effect_slot → mission_effect_id`, `mission_effect_id → request_id`, `request_id → provider order/capture`, and one-time `permit_id` ledger. Persist via durable transactional store (SQLite WAL acceptable for single-host contest with exclusive DB write transactions; label non-distributed). No mutable in-memory counter as budget authority.

Budget accounting in integer cents: `captured_nonrefunded + active_reservations <= cap`. PayPal response ambiguity retains reservation, not release. Convert with exact decimal formatting at API boundary, currency code locked. Refund does not automatically restore delegated ability to spend unless the mission policy explicitly authorizes that transition; account for provider payout/reconciliation semantics.

`CREATED → PERMIT_VERIFIED → RESERVED → ORDER_CREATED → APPROVAL_PENDING → APPROVED → CAPTURE_IN_FLIGHT → {CAPTURE_CONFIRMED | RECONCILIATION_PENDING | FAILED_RELEASED}`. A missing approval is not the same as successful capture. Validate provider Order/capture status through server-side `GET` or documented API readback, not only the first POST body. Never close service delivery based only on payment success.

## 3. APIs and exact error behavior

PayPal REST OAuth client credentials server-to-server; sandbox base `https://api-m.sandbox.paypal.com`, Orders v2 create, required payer approval route, capture, and authoritative order/payment readback. Use `PayPal-Request-Id` only on endpoints that document support; provider retention is endpoint-dependent and not an eternal dedupe guarantee. Local effect identity remains durable beyond that window. PayPal Agent Toolkit may be used for ordering/read operations if compatible; direct official Orders v2 server SDK/HTTP is also acceptable, but only one adapter owns credentials.

Required HTTP behavior: 2xx + provider final status → corroborate readback; 4xx hard reject → no invented payment; 5xx/timeout/lost reply → uncertain, retain reservation, reconcile with provider before any new command. If provider returns ambiguous PENDING status, Judge stays INDETERMINATE and payment not counted COMPLETE. No provider-specific behavior assumed until verified in Sandbox.

For contest economics, minimum verified effect is ONE PayPal Sandbox capture of a bundled seller offer explicitly covering the mission's three services, with synthetic fulfilment clearly labeled. Stretch: three separate vendor captures with distinct obligations. Do not claim three actual independent merchant purchases if only one bundle merchant was paid.

## 4. Mandatory test matrix

| ID | Given / test action | Required observable result (PASS) | FAIL condition |
|---|---|---|---|
| A04-T01 | Valid permit + budget + payer approval | Real Sandbox capture readback status confirmed | Created-only order called complete |
| A04-T02 | Request denied by ZTL/authority | 0 outbound PayPal transaction calls | Any charge/order attempt |
| A04-T03 | Two simultaneous $320/$260 reservations under $500 | At most one reservation wins; <=50,000 cents | Both permitted / oversubscription |
| A04-T04 | Retry same request after simulated timeout | Same effect identity; max 1 capture | Duplicate capture |
| A04-T05 | Lost response after provider success | Reconcile provider state; do not repeat capture | Blind retry with fresh request ID |
| A04-T06 | Order created without payer approval | No completed payment or Judge PASS | Created treated as captured |
| A04-T07 | Provider capture PENDING/DECLINED | Hold/abort; never COUNT as completed | Pending counted PASS |
| A04-T08 | Change amount/merchant after admission | Mismatch rejected before network | Wrong payee/amount charged |
| A04-T09 | Revoke permit after reserve before capture | Abort unstarted capture, recover state safely | Stale permit used |
| A04-T10 | Concurrent reuse of same permit | Single accepted use, other refusal | Double spend |
| A04-T11 | Crash/restart local ledger process | Durable reserved state and effect join intact | Lost local idempotency state |
| A04-T12 | Provider readback altered or missing | No verified completion; discrepancy logged | Untrusted fabricated receipt accepted |
| A04-T13 | Amount 0.01 / 499.99 / 500.01 | Exact cents and boundary acceptance | Float or rounding overflow |
| A04-T14 | Force auth / network 401, 403, 500 | Correct fail closed and classified retryability | Leaks secret or treats failure as success |
## 5. Pre/post, resource constraints, verdict

Pre: all A03 scope and permit tests pass; sandbox merchant/buyer test accounts exist; payer-consent procedure verified; no live API keys in output; provider SDK pinned. Post: provider-confirmed capture ID and exact normalized provider receipt joined to original mission; zero forbidden/double effects in tests; successful tests cannot be passed by mock responses alone.

Mandatory PASS: T01–T14 with real sandbox for T01/T05/T06/T07 where supported; mock fault injection may test hard-to-trigger failure edges but label `MOCK_ONLY` and independently run at least one real wire flow. Full mission PASS belongs to A05, not this WO. Report `orders_created`, `approved`, `captures_confirmed`, `network_attempts`, `reconciled_unknowns`, `duplicate_effects`, `budget_violation_count`, `p95_latency`, API error codes and durations. No unverified claim of zero production fraud.

`GO→A05` only on actual provider capture + readback and exact ledger match, mandatory negative suite; otherwise NO-GO for PayPal-integrated mission. Rollback: disable PayPal adapter, void pending authorization when allowed; never hide completed captures or rewrite history.

## Global controlling invariants (apply to every WO)

- **G0 — original authority**: mission contract, required obligations, currency/budget, vendor eligibility, epoch and deadline are frozen before an episode; only separately authorized updates may change them.
- **G1 — proposal != authority**: an agent, optimizer or model may suggest; only independently evaluated, current T verdict and policy-scoped admission can enable a consequential command.
- **G2 — source of truth**: payment success = provider-confirmed final effect, never an LLM claim, simulated world transition, or an HTTP request alone.
- **G3 — no overspend**: captured net outstanding + unreleased reservations <= authorized budget, in integer minor units. Concurrent contenders must serialize their reservations.
- **G4 — no duplicate**: unique mission_effect_id binds exactly one authorized financial effect; retries preserve identity; unknown effect must reconcile before a new attempt.
- **G5 — completion closure**: DONE requires a nonempty exact original obligation inventory and authoritative readback for every consequential obligation, plus independent Judge pass.
- **G6 — formal scope**: kernel-proof, Python/Lean differential, world effects, PayPal sandbox and independent Judge are separate evidence layers; none silently substitutes for another.
- **G7 — intellectual property**: LEGION Core unchanged under PolyForm Noncommercial; ztl-stand © Vitaly Reznik, Apache-2.0 OR MIT; new demo files under independent approved licence. No copied Core file is relicensed.
- **G8 — VCC@1**: first *bounded episode* includes internal agent proposals/replanning; any manual repair, restart, hidden oracle intervention or changed scorer creates another attempt. Missing/unknown outcome is 0, not a PASS.

## Uniform design-control / status contract

- **Design status `FROZEN-DESIGN`** means the requirements, cases, expected verdicts and source manifest are frozen at publication. **It does not imply any experiment was run.**
- **Execution status** starts `NOT_RUN (Z)`. Runtime PASS must be earned from recorded inputs, commands, effects, independent verification and hash-locked evidence; a developer's summary is not sufficient.
- **FAIL** on any mandatory negative-control false acceptance, evidence disappearance, ungrounded authority, budget overrun, wrong money effect or false DONE. An explicit UNKNOWN/OPEN is not PASS.
- **INVALID_TEST** requires a preregistered reason, raw trace and disposition; never erase a failed attempt by classifying it invalid after seeing the result.
- **Frozen change policy**: fixes require a new document version, changed hash and change log before testing on revised inputs. No in-place rewriting of prior failures or changing acceptance thresholds after viewing results.
- **Evidence format**: `INPUTS.json`, `SOURCE_MANIFEST.json`, `TEST_MATRIX.json`, `EVENTS.jsonl`, `ACTUAL_RESULTS.json`, `NEGATIVE_RESULTS.json`, `EVIDENCE_HASHES.sha256`, `WO_VERDICT.json`, plus specific artifacts named below. All tests must preserve pass **and** fail records.
- **Owners**: Veraxis handles LEGION integration/product; Vitaly/Logic owns the correct attribution and interpretation of upstream ztl-stand/ZTL properties. This allocates work, not permissions to modify each other's source.

## Versioned source references

[S01] LEGION Core: https://github.com/veraxis-protocol/LEGION/tree/d083c44af2526e38068e0220891cd0bb5fce52af
[S02] LEGION structural A2A world: https://github.com/veraxis-protocol/LEGION/blob/d083c44af2526e38068e0220891cd0bb5fce52af/tests/legion_struct/worlds/SYN-GAIA-A2A.world.json
[S03] LEGION causal controller: https://github.com/veraxis-protocol/LEGION/blob/d083c44af2526e38068e0220891cd0bb5fce52af/tests/legion_struct/struct_controller.py
[S04] LEGION world engine: https://github.com/veraxis-protocol/LEGION/blob/d083c44af2526e38068e0220891cd0bb5fce52af/tests/legion_struct/world_engine.py
[S05] LEGION governance plugin: https://github.com/veraxis-protocol/LEGION/blob/d083c44af2526e38068e0220891cd0bb5fce52af/tests/legion_struct/struct_plugin.py
[S06] LEGION ZTL evidence service: https://github.com/veraxis-protocol/LEGION/blob/d083c44af2526e38068e0220891cd0bb5fce52af/tests/legion_overlay/ztl_service.py
[S07] LEGION judge adapter: https://github.com/veraxis-protocol/LEGION/blob/d083c44af2526e38068e0220891cd0bb5fce52af/judge/components/struct_adapter.py
[S08] Independent judge reference and limitations: https://github.com/veraxis-protocol/LEGION/blob/d083c44af2526e38068e0220891cd0bb5fce52af/judge/components/judge_s1/README.md
[S09] Historical 13/13 replay (NOT PayPal VCC@1): https://github.com/veraxis-protocol/LEGION/blob/d083c44af2526e38068e0220891cd0bb5fce52af/docs/WO059_INDEPENDENT_REPLAY.md
[S10] ztl-stand (Apache-2.0 OR MIT; © Vitaly Reznik): https://github.com/inventor1975/ztl-stand/tree/f604e071bb0cf929269338d6769c9ab3ec8cc497
[S11] ZTL Lean source: https://github.com/inventor1975/ZTL/tree/e1141c6893af6cf49f363620432ca6aaa0b06301
[S12] ztl-swarm: https://github.com/inventor1975/ztl-swarm/tree/7d2a8265926aecab9a838b09059bd106941b5c18
[S13] PayPal Orders v2: https://developer.paypal.com/api/rest/integration/orders-api
[S14] PayPal REST idempotency: https://developer.paypal.com/api/rest/requests/
[S15] PayPal Agent Toolkit: https://github.com/paypal/agent-toolkit
[S16] PayPal Hackathon Official Rules: https://paypalaihackathon.devpost.com/rules

## Normative design-control appendix (v1.1, 2026-11-05)

**Document lifecycle:** DESIGN_FROZEN / EXECUTION_NOT_RUN (Z). No execution authorized by documentary freeze alone. Source pin and core non-modification rules in the main TDD remain binding. This appendix is normative and, in case of a wording conflict, adopts the more restrictive financial-safety gate.

### A. Mandatory twelve-field TDD acceptance checklist

1. **Objective and hypothesis:** stated in the purpose section; falsified by the explicit negative/STOP results below.
2. **Existing assets:** pinned LEGION d083c44af2526e38068e0220891cd0bb5fce52af, ZTL e1141c6893af6cf49f363620432ca6aaa0b06301, ztl-swarm 7d2a8265926aecab9a838b09059bd106941b5c18, ztl-stand f604e071bb0cf929269338d6769c9ab3ec8cc497; component-specific paths cited in [S01]–[S16]. Use existing components without copying proprietary Core into the open demo.
3. **Missing code:** limited to the external demo/adapter/harness modules explicitly named in this TDD; no new generic agent platform, commercial ledger in Core, Lean theorem prover or payment rail.
4. **Architecture/interface contract:** immutable original mission and epoch-bound typed inputs; model proposal cannot be authority; external native effect is source-verified; independent Judge consumes the evidence rather than an agent narrative.
5. **Formal preconditions/invariants:** G0–G8 (in original TDD) are normative. Execute(a) implies current authorized T, valid contract hash/epoch/actor, current evidence and budget reserve; DONE implies all original nonempty obligations and native readbacks independently verified.
6. **Functional/nonfunctional requirements:** all R/T requirements in the main TDD are mandatory, not optional. Each test case has a unique ID and observable expected result. Performance/cost are reported with measurement conditions, never inferred from source claims.
7. **Testing:** positive + negative + exact-boundary + fault/timeout/revocation tests must be separately labelled and preserved; no case removed after observing outcome. If external faults cannot be induced live, a mock test may be marked MECHANISM_ONLY but cannot substitute for required native effect evidence.
8. **PASS:** every mandatory case satisfies the frozen expected result, exact source/evidence hashes match, and all upstream prerequisites have actually passed.
9. **FAIL:** any safety-critical positive acceptance of an invalid action, false DONE, misbound proof, original-obligation deletion, duplicate money effect, or missing required test evidence.
10. **BLOCKED (Z):** necessary tool, developer credentials, consent, runtime environment, formal source correspondence or independent readback is unavailable. Report which prerequisite, exact error, mitigation and test NOT_RUN; never convert BLOCKED to PASS.
11. **STOP / fail closed:** any attempt to execute financial effect without current T+authority+budget+consent, or to relabel a script as model autonomy. Disable the external effect adapter and retain original evidence, no undocumented retries.
12. **Release/freeze manifest:** per-WO input schema version, source commit, TDD SHA-256, expected result matrix, negative controls, model mode, Judge version and test denominator frozen BEFORE any counted run. A later change creates v1.2 or newer; no in-place reclassification of past outcomes.

### B. Interface contract and responsibility partition

Proposal subsystem: untrusted agent/model/stand output (candidate only); causal subsystem: original obligations, dependencies and epoch; ZTL/Lean: evidence semantics within explicit theorem premises; CAGE/VEIP: authority/scope/execution admission; external adapter: native PayPal calls if and only if admission passes; independent provider readback: effect truth; independent Judge: VCC@1 and retained FAIL/Z. These boundaries remain separate even when one process hosts multiple test adapters.

### C. Required per-WO evidence ledger

Before run: FROZEN_INPUTS.json, SHA256SUMS.txt, TDD_SHA256.txt, SOURCE_PINS.json, REQUIREMENTS.json, TEST_MATRIX.json and owner-approved run authority. After run: RAW_EVENTS.jsonl, MODEL_OR_SCRIPT_MODE.json, NATIVE_READBACK.jsonl (or explicit NOT_APPLICABLE), ACTUAL_RESULTS.json, NEGATIVE_RESULTS.json, COUNTERS.json, WO_VERDICT.json. Every file has exact content digest. Any missing required file is BLOCKED/FAIL according to pre-registered collection rule, not automatically a negative finding about the underlying logic.

### D. A04-specific minimum design and gates

**Input precondition:** valid current signed CAGE/VEIP/ZTL admission, original nonempty obligation inventory, payer consent supported in Sandbox, durable SQLite WAL transaction and unique mission-effect identity. **Output:** transaction states, exact cents, PayPal order/authorization/capture identifiers, independent readback and event ledger.

**PASS:** approved Sandbox create+capture+readback with 0 unauthorized external calls, 0 overspend, 0 duplicate captures, and all mandatory negative/boundary cases. **FAIL:** completed payment inferred from CREATED/PENDING, two competing reservations exceed cap, stale terms paid, PayPal request-ID reused for a DIFFERENT effect. **BLOCKED:** API credentials or payer approval absent, Sandbox processing unsupported; record exact PayPal error and NO completed effect. **STOP:** money attempted without T or consent; ambiguous provider result leads to blind new-ID capture. **Boundary/fault:** parallel $320/$260 contention under $500, 1/49999/50001 cents, crash/restart WAL, lost response, mismatched capture status, permit revocation pre-capture. **Handoff:** externally corroborated receipts to A05; one bundled merchant payment does NOT prove 3 independent vendor deliveries.
