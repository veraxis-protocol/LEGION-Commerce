# WO-COM-A03-BRIDGE-001 — execution report, 2026-11-05

**Decision: BLOCKED / NO-GO for native payment.** The executable bridge and its
independently replayable conformance evidence are delivered. Operational A03
acceptance, a signed evidence manifest, installed authority and a genuine Sandbox
payment have not been substituted with test signatures or developer attestations.
The resolution paths for each remaining gate are specified below.

## Foundation and source identity

**VERIFIED:** implementation commit
`df13731afe774b58c1f46006857e7d97837b0d02`, branch
`codex/wo-com-a03-bridge-001`, based on A04
`4c7e6b7c97b828008dd7382f1f11a4c9b9c5685b`.
The evidence/report follow-up commit is recorded in Git history and the delivery
receipt. All 40 files in the final conformance source manifest match this exact
implementation commit's raw Git blobs and current worktree bytes.

The A04 `commerce_adapter` package and frozen `docs/ADMISSION_INTERFACE.md` have
zero diff from the A04 base. Interface raw SHA-256 is
`adc2ff7f83d642b75ac5baf0865025369312891edda06a29ca0949e580086127`.

Real, isolated upstream sources used:

| Source | Exact commit | Execution |
|---|---|---|
| LEGION | `d083c44af2526e38068e0220891cd0bb5fce52af` | Actual controller, world and evidence-service methods |
| ZTL | `e1141c6893af6cf49f363620432ca6aaa0b06301` | Actual ZLegion and ZRuntimeGate kernel builds/axiom output |
| ztl-swarm | `7d2a8265926aecab9a838b09059bd106941b5c18` | Actual ZSwarm proofs and compiled zverdict |

Native Windows Lean 4.29.1 was used. The bridge does not copy or relicense Core.
Its source clones and build outputs are excluded from the Commerce repository.
The active Claude checkout remained at branch `formal-core-001`, commit
`4375a8e7569c3dc78d7e743e2b4839b589d1e91f` when checked before and after the work.
No writes, commits or process interference were performed there. Its formal
directory contained BASELINE_MANIFEST.json, PROOF_OBLIGATIONS.json,
SOURCE_TO_FORMAL_MAP.md and SPEC.md; those documents were not treated as completed
kernel proofs or operational authorization.

## L1 — experiments and observed results

| Battery | Result | Exact evidence |
|---|---|---|
| A03 13-case local conformance | **13/13 PASS**, 0 failures/errors | `bridge_evidence/run-20261105T211604Z/` |
| Installation, trust, ACL, tamper and revocation | **6/6 PASS** in temporary directories | `bridge_evidence/integration-20261105T211739Z/` |
| Existing A04 regression suite | **42/42 PASS** | `evidence/run-20261105T211820Z/` |
| Separate-process replay | **REPLAY_MATCH**, 39 recorded bridge assessments or denials replayed | `bridge_evidence/INDEPENDENT_REPLAY_20261105T211604Z.json` |
| Deliberately injected witness failures | 3 preserved, including 1 intentional mismatch | Frozen traces and replay result |
| Unexpected Core/runtime verdict mismatches | **0 measured in the tested inputs** | Conformance COUNTERS.json and differential traces |
| Operational keys/acceptance signatures generated | **0** | No production signer or key generator exists in the bridge |
| Operational installs / native PayPal dispatches / native captures | **0 / 0 / 0** | Final operational preflight and test modes |
| Genuine operational A04 preflight | **BLOCKED**, exit 2 | `bridge_evidence/OPERATIONAL_PREFLIGHT_FINAL.json` |
| Full local admission p95, N=10 | **1105.3761 ms — PERFORMANCE FAIL** against <=5 ms | Conformance COUNTERS.json and raw timings |

The 13-case denominator is local conformance, using temporary test authority and
observations while executing actual pinned Core/Lean calculations. It is not a
claim of 13/13 independently accepted operational A03 cases. Automated replay is
process independence and reproducibility, not institutional independence or an
acceptance signature. The real payment acceptance criterion remains unexecuted.
Early signature/schema denials remain before the Core calculation. The replay
total is not a count of accepted permits, Core invocations or native effects.

The frozen A03 cases have explicit expected/actual records: T01 builds and axiom
audit; T02 matching valid calculation and the A04 composition seam; T03 missing/Z
and malformed/UNKNOWN state; T04 false/bound-broken evidence, equivocation and
shared-source Sybils; T05 report permutations; T06 stale observations/reports;
T07 forged signatures, roots and actor; T08 revocation/epoch; T09 changed terms;
T10 ACT with insufficient numeric budget; T11 missing, timed-out, erroneous and
mismatched Lean; T12 empty/missing obligations; T13 ten identical repeats.

## Cause analysis and minimal repairs

**VERIFIED mapping facts:** the pinned controller creates its ZTL service with
Lean disabled; the bridge therefore recomputes the actual service verdict and
executes the external compiled ZSwarm witness on exactly the same seats. The five
default Core observers share a source and are not used as independently grounded
Commerce authority. A signed operator policy must enroll actual reporter roots
and their source provenance; one source cannot be relabelled as several bases.

The bridge adds narrowly scoped guards at the external boundary:

- Validates source commits, source bytes, package initializers and native executable
  identity **before** importing/executing the Core calculation.
- Rejects malformed/UNKNOWN controller state, unknown effect state and invalid
  record chronology. The pure Core directive function assumes its internal state
  schema; an external serialized state must satisfy that schema before use.
- Rejects equivocation before Core's latest-report-wins map can hide conflicting
  statements; requires every original fact, not only the first fact's summary.
- Binds real Core action parameters, mission inventory, actor, quote, merchant,
  currency, budget, source observation and both epoch contexts.
- Bounds the native witness deadline across both blocked stdin and stdout. Missing
  or failed witness never silently falls back to a Python allow.
- Separates revocation from the permission being revoked. The control command can
  commit signed revocation even if acceptance is broken or Lean unavailable.
- Keeps current control monotonic in the A04 SQLite serialization order and
  permits only read-only reconciliation after admission expiry/failure.

These repairs are external to Core. Local component/integration checks substantiate
their tested behavior; they do not prove all reachable LEGION executions or isolate
malicious programs running as the same Windows account/administrator.

## L2 — final goal and L3 — invariant scope

**Work type:** INVARIANT VERIFICATION plus the requested integration capability;
minimal ROOT-CAUSE REPAIR at serialization, source-validation, timeout and
revocation boundaries. No new model, general agent platform or GPU/LLM campaign.

**Final goal:** the bridge provides the admission seam needed before the first
legitimate Sandbox effect and protects the original intent, exact money terms and
effect identity. There was no GAIA2 episode, VCC@1 cohort, B0/B1 experiment or real
cost-per-success denominator. No measured progress toward >=95% GAIA2 Pass@1 or
commercial superiority is claimed. Those measurements require the separately
authorized A05/GAIA2 experiment after the gates below pass.

**Fundamental scope:** tested unit/integration properties include no promotion of
UNKNOWN/empty input, separation of proof/authority/admission/budget, exact intent
binding, revocation before dispatch, native-effect truth in the A04 mechanism,
and preservation of effect/reservation identity through failure. Actual Core
methods and upstream Lean builds are evidence layers distinct from a formal proof
of this Python bridge, real source truth, institutional authority or a native
PayPal execution. Those latter gates remain subject to the explicit acceptance
and acquisition ledger below; no unit result substitutes for them.

## Performance evidence

The final full gate p95 is 1105.3761 ms, including source checks and isolated worker
startup, excluding PayPal, model inference and stand search. Clock:
`time.perf_counter_ns`; Python/platform were frozen before the run. Supplemental
post-run hardware metadata records an Intel Core i5-1145G7, 4 cores/8 logical
processors. This was a development host, not a dedicated cross-system benchmark.

The earlier five-repetition phase profile records source checks at 642.9–736.9 ms,
isolated Core worker plus native witness at 459.9–544.5 ms, and the persistent native
verdict alone at 0.279–0.383 ms. That profile preceded moving source verification
before Core execution; do not reuse its phase labels as the final full-gate timing.
It identifies verification/process overhead for a later bounded repair. No checks
were removed to meet the target, and no performance optimization was performed.

## Evidence-to-action and acceptance ledger

| Field | Existing authority and accepted source evidence | Full local latency | Genuine payment/readback |
|---|---|---|---|
| Evidence gap / failed gate | Existing key paths/IDs/fingerprints, genuine signed Core/CAGE/VEIP input, A01/A02 and independent A03 acceptance, and custody/source correspondence are absent | Measured 1105.3761 ms exceeds <=5 ms; gate FAIL | No accepted installed operational package, buyer-approved order, native capture or native provider readback |
| Required evidence | Independently obtained public roots and provenance; real original mission, observations and current signed control/permit; externally reviewed source-bound acceptance and install manifest | Same-scope full gate <=5 ms on declared hardware with every safety check retained | Approved order plus one authorized capture, exact capture/order IDs, currency/cents/merchant and fresh independent GET joins |
| Where/how to acquire | Actual operator and source/acceptance owners supply their existing identities and signed artifacts through the documented operator procedure; actual agent/custody boundary must be tested | After correctness/authority review, profile and bound the validated source-check/worker overhead; compare a versioned repair on the same frozen cases | After genuine A03/preflight PASS, create the smallest explicitly authorized offer, authenticate the Sandbox buyer, capture and run the separate reader |
| Independently executed now | Real source checkout/build/audit; 13 conformance, 6 package, 42 A04 checks; 39 separate-process replays; exact source/evidence hashes; a no-key/no-POST operational preflight | N=10 full-gate timing and preserved phase profile; failure retained | Approval/capture/readback negative mechanisms checked; no substitute native receipt or consent created |
| Objective acceptance | PASS only genuine scoped signatures, exact pinned source/interface/evidence, original inventory, current authority and independently accepted premises; reject forgery/unknown/revocation | PASS <=5 ms full gate with unchanged safeguards; otherwise FUNCTIONAL_PASS_PERFORMANCE_FAIL | PASS only required native effect/readback and all mandatory safety cases; FAIL forbidden, duplicate, overbudget or false-complete result |
| Next action / fallback | Receive promised existing key paths/IDs and public fingerprint, validate actual signed package, external reviewer signs; if unavailable keep operational BLOCKED | Make a separately reviewed minimal repair once correctness boundaries are accepted; if target still fails retain NO-GO/performance failure | Run native flow only after gates and real buyer approval; otherwise keep writes disabled and any uncertain reservation held |

The final preflight explicitly lists all original six A04 prerequisite entries as
absent: trust.json, a03-acceptance.json, a03-evidence/, current-mission.json,
current-permit.json, current-control.json. The bridge additionally requires
bridge-policy.json, bridge-evidence/ and installation-manifest.json.

## Delivered manifests and custody

`bridge_evidence/review-request-20261105T211949Z/EVIDENCE_MANIFEST_UNSIGNED.json`
is explicitly **UNSIGNED_REVIEW_REQUIRED**, with null signature. It binds the
source commit, exact A04/bridge hashes, frozen interface, final run files and replay
output. It cannot serve as an acceptance envelope or execution permit. An actual
external acceptance owner must review and sign; the bridge contains no command to
self-attest that acceptance.

Before evidence publication, 179 retained per-run evidence-file hashes and all 40
final conformance source-file hashes were verified. A further artifact-wide hash
manifest covers supplemental diagnostics. The initial 13-case and 5-case runs,
targeted diagnostics, profile, pause checkpoint and final runs remain preserved.
All conformance signatures are test signatures with public fixture identities;
private fixture keys stayed in test memory and no operational roots were installed.

The archive contains Commerce code, pinned public designs, test records, build
transcripts and redacted diagnostics. It excludes upstream Core clones, virtual
environments, PayPal credentials, DPAPI files and operational SQLite ledgers.
Hashes provide content integrity, not external notarization or a signature anchor.
