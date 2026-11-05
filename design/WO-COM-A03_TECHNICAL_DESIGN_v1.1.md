# WO-COM-A03 — Formal admissibility conformance and consequence-bound enforcement

**Document:** TECHNICAL DESIGN DOCUMENT (TDD) | **Revision:** v1.1 | **Design date:** 2026-11-05 | **Status:** FROZEN-DESIGN; NOT_RUN (Z)
**Product:** LEGION Commerce / VCC@1
**Scope:** Proof sources + external compiled-verdict witness + payment permit; no Core edit
**Depends on:** A01/A02 artifacts pinned and validated
**Constraints:** NO Core modifications; NO payment execution before passing safety gates; PayPal Sandbox only.
**Source manifest:** LEGION d083c44af2526e38068e0220891cd0bb5fce52af; ZTL e1141c6893af6cf49f363620432ca6aaa0b06301; ztl-swarm 7d2a8265926aecab9a838b09059bd106941b5c18; ztl-stand f604e071bb0cf929269338d6769c9ab3ec8cc497.

## 1. Exact claim and nonclaim

**H-A03:** a payment intent will have one exact admissibility decision bound to current mission, quote, budget, authority, agent, epoch and causal prerequisites; only an earned T plus current permit can reach the payment boundary. Inputs representing F, Z/UNKNOWN, expired authority, stale quote or missing obligation inventory are denied/deferred. A kernel theorem alone cannot prove provider effects or all Python code correct.

**Important source fact:** LEGION `tests/legion_overlay/ztl_service.py` supports compiled Lean `zverdict`, but structural `tests/legion_struct/struct_controller.py` constructs `ZtlEvidenceService(..., lean=False)`. Thus payment-admission path must be externally checked and enforced; do not say the unmodified controller already runs compiled Lean. Existing `ZLegion.lean` merge is order-sensitive for some 3-agent combinations, so the exact production aggregation must be specified and tested.

## 2. Authority and policy composition

The following are **independent conjunctive conditions**; none alone gives `EXECUTE`:

`PERMIT = LEAN_RULE_EARNED ∧ CONTROLLER_ACT ∧ CAGE_ALLOW ∧ VEIP_ELIGIBLE ∧ CONTRACT_UNCHANGED ∧ QUOTE_FRESH ∧ EXACT_BUDGET_RESERVED ∧ EPOCH_CURRENT ∧ PAYMENT_TOOL_SCOPE_VALID`.

Requirements to avoid fabricated security: canonical domain-separated hashes for mandate, task, principal, snapshot, quote, amount, merchant, readback and command; server-owned binding, not caller's `ztl_state`. Evidence reporters must have valid identities and declared independence roots. Unknown/broken bound denies. A permit is **single-purpose, one-time, revocable and short-lived**. An agent cannot request authority expansion by returning a more convincing rationale.

## 3. Proof/refinement bridge design

- Proof inventory: ZTL `lean/ZLegion.lean`, `lean/ZRuntimeGate.lean`, ztl-swarm `lean/ZSwarm.lean`, actual source SHAs and `#print axioms` transcripts. Preexisting formal properties are reused only within their hypotheses.
- Exact frozen serialization: `commerce_admission_input.json` holds world snapshot hash, set of required obligations, signed reports or reporter roots, control epoch, contract hash and requested action. An adapter maps these to the exact compiled verdict input.
- Run **compiled Lean witness** and Python controller rule on same evidence under the same pinned fault bound and identity basis; record both verdicts, explanation and mismatches. A `lean_mismatch` is a fatal integration error.
- Because business budget and service coverage are not proved by ZSwarm's truth-threshold theorem, require *separate checked numerical constraints* from A01 and runtime atomic reservation. Neither a threshold proof nor an `EARNED` plan by itself authorizes payments.
- External consequence guard revalidates permit and exact budget immediately before PayPal call; the only PayPal credential owner is this guard. Existence of a logged permit after the fact is insufficient.
- Do not silently treat `LeanVerdict` invocation as Lean proof of a network signature, world state truth, JSON parsing semantics or exact real-world transaction.

## 4. Proof obligations and falsifiability

P01 Safety induction for named policy/model: admitted transitions preserve I(s) under declared assumptions; P02 binding source hash, model, epoch, merchant, amount and quote; P03 aggregation cannot promote contradictory or insufficient evidence to T; P04 each capture has a prior admitted current permit; P05 all permitted writes have native receipts; P06 no empty inventory can produce DONE; P07 reenforcement after restart. For each proof, capture full theorem name, environment assumptions, kernel check, actual runtime reference or state `UNPROVEN`.

## 5. Mandatory tests

| ID | Given / test action | Required observable result (PASS) | FAIL condition |
|---|---|---|---|
| A03-T01 | Compile pinned formal artefacts | Lean kernel acceptance and axiom audit documented | Proof source/mismatch not reproducible |
| A03-T02 | Matched valid evidence T | Lean and controller agree; current scoped permit | Different verdicts or unbound permit |
| A03-T03 | Missing evidence / Z | No payment permit or PayPal network call | Z treated as T |
| A03-T04 | Contradictory roots / equivocation | No false ACCEPTED_TRUE | Contradiction promoted |
| A03-T05 | Reordered evidence [T,F,T]/[F,T,T] | Actual aggregation invariant or conservative no-promotion | Order-sensitive false admission |
| A03-T06 | Stale observation > max_age | Deferred/denied until fresh source | Stale witness reused |
| A03-T07 | Wrong signer / root / spoofing | Reject with explicit reason, no permit | Forged actor admitted |
| A03-T08 | Revocation or new epoch after proposal | Prior permit invalid at action boundary | TOCTOU financial effect |
| A03-T09 | Switch amount/merchant after permission | Digest mismatch => no effect | Permit covers altered transaction |
| A03-T10 | Controller ACT but numerical budget fails | Refuse; zero payment | Independent safeguards conflated |
| A03-T11 | Lean unavailable / timeout / error | FAIL-CLOSED, report UNKNOWN and abort | Fallback silently grants permission |
| A03-T12 | Missing/empty obligations | No DONE or capture | Vacuous completion |
| A03-T13 | Repeat valid frozen case N times | Zero Lean/Python verdict mismatches, exact policy scope | Nondeterministic ungrounded decision |
## 6. Metrics and acceptance gates

Mandatory: T01–T13 PASS with complete evidence, zero false admissions and zero Lean/Python mismatches in frozen battery; exact expected-vs-actual table. Local admissibility p95 target <=5 ms measured with hardware/clock declaration, **excluding** ztl-stand search, PayPal network and model inference. The p95 target is a performance gate for the stated hardware fixture, not a theorem. If performance fails but safety holds, record `FUNCTIONAL_PASS_PERFORMANCE_FAIL` rather than disguise as full PASS.

**No source-code edit to Core**: add a sidecar/adapter enforcing the combined decision. If it can be bypassed by a direct PayPal client request, A03 is FAIL regardless of Lean tests. No claim of cryptographic production attestation without signature verification and external root anchoring.

## 7. Required record and exit

`PROOF_MAP.json`, `LEAN_BUILD.txt`, `AXIOM_AUDIT.txt`, `FORMAL_INPUTS.jsonl`, `LEAN_VERDICTS.jsonl`, `RUNTIME_VERDICTS.jsonl`, `MISMATCHES.jsonl`, `PERMIT_TRACE.jsonl`, `NEGATIVE_RESULTS.json` and exact source commit manifest. `GO→A04` only when no bypass path exists and payment adapter can reliably require external permit. `NO-GO`: unknown promoted to allow, fake authority, version mismatch, or unsupported proof scope. Rollback = disable PayPal capability, not weaken gate.

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

### D. A03-specific minimum design and gates

**Input precondition:** exact world/contract/epoch/action and Ed25519 provenance roots, compiled Lean target if used, actual LEGION controller mode reported (known source lean=False). **Output:** theorem-to-rule mapping, exact differential verdict rows, denial reasons, signed/replayed grounds, p95 with measurement harness.

**PASS:** at least 1000 frozen matched cases, zero unexplained Lean/runtime mismatches for the actual compared rule, missing/expired/contradictory/reordered inputs never cause unauthorized T, current epoch/action bound. **FAIL:** one false T or root forgery accepted, order-induced promotion, proof claim beyond theorem premises. **BLOCKED:** compiled Lean unavailable, unmatched semantics of Python controller or incomplete formal refinement; record TEST_ONLY versus runtime truth. **STOP:** any payment command before approved permit+terms+epoch+money reservation. **Boundary/fault:** T/F/Z permutations, expired source, attack on digest, signer/root equivalence, timeout and stale authorization. **Handoff:** conditionally valid permit envelope to A04; Lean theorem does not alone imply actual capture.
