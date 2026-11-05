# WO-COM-A04 execution report — 2026-11-05

**Decision: BLOCKED; NO-GO to A05.** Implementation and local mechanism checks
are delivered. A native PayPal capture is deliberately gated by the frozen A03/A04
preconditions. No Orders or capture request was dispatched in this work order.

## Foundation, cause, experiment and goal

**VERIFIED — scope:** a separate checkout of the initially empty
`veraxis-protocol/LEGION-Commerce` repository, branch
`codex/wo-com-a04-paypal-sandbox`. No LEGION Core checkout, branch or source was
written, and no Claude process was contacted or changed. Frozen A03/A04/A05 v1.1
files match the master v1.2 raw-byte digests. No Core source is copied or relicensed.

**Work type:** CAPABILITY EXPANSION for the requested external adapter;
INVARIANT VERIFICATION for authority, budget, idempotency and effect truth;
ROOT-CAUSE REPAIR for development defects found below. No performance optimization,
LLM/NIM/GPU campaign or new agent platform was introduced.

**L1 — experiment:** actual SQLite WAL contention, separate Windows writer
processes, abrupt process termination and loopback HTTP callbacks exercise the
adapter against synthetic signed admission and FakePayPal. Expected outcomes are
frozen before each run. PASS means the exact assertion holds; any assertion failure
or execution error is local FAIL. Mock payment responses never satisfy native A04.

**L2 — final goal:** preventing duplicate/unauthorized/overbudget effects and false
payment closure supports safe commercial execution and future cost-per-success
measurement. No GAIA2 episode, VCC@1 cohort, competitive baseline or model-cost
experiment was run; this work provides no measured improvement toward >=95% GAIA2
Pass@1. These require the separate authorized A05/GAIA2 experiments and their
frozen denominators after the native and independent-verification gates pass.

**L3 — invariants:** original signed inventory and exact intent are bound to
admission; UNKNOWN/F/Z cannot authorize calls; structural, semantic, authority,
budget and provider approval checks are separate; uncertainty retains budget and
effect identity; payment truth requires fresh provider GETs; payment never returns
mission DONE. These have local component/integration evidence, not an end-to-end
formal proof of LEGION or a live commerce result.

## Exact source commits and verification

- `61afc4d70fa61def5684cffedd5a2217f4d9b011`: Windows adapter, protocol contracts,
  approval listener, independent readback, tests, pinned designs and documentation.
- `36db39c39a0d42716cfd2be89b39d74f0f47eb44`: preserve raw design JSON bytes in Git.
  All **25** final tested source/dependency/attribute files match committed blob
  SHA-256 values. This is the final tested source snapshot.
- The subsequent evidence commit contains this report and every retained run.
  Its exact SHA is supplied in the delivery response and repository history.

The repository had no configured Git author identity. Commits use the explicit
automation identity `Codex <codex@localhost>` without changing global user settings.

## Measured outcomes

| Check | Result | Evidence scope |
|---|---|---|
| Final mechanism battery | **42/42 PASS**, 0 failures, 0 errors | `evidence/run-20261105T175417Z/` |
| Actual Windows DPAPI + PayPal OAuth | **PASS**, TTL 31257 seconds, 306.876 ms | `evidence/native-preflight-20261105T175257Z/OAUTH_RESULT.json` |
| Credential ACL metadata | **PASS**, no broad allow rules detected | Current user, SYSTEM and local Administrators trusted |
| Native Orders / buyer approvals / captures | **0 / 0 / 0** | Native preflight and prerequisite hold |
| Native transaction network attempts | **0** | OAuth is separately recorded and is not a transaction |
| Native capture/readback T01/T05/T06/T07 | **BLOCKED / NOT_RUN** | Accepted A03 and scoped runtime authority absent |
| GO to A05 | **NO-GO** | Full A04 mandatory acceptance not earned |

The final suite covers all 14 A04 requirement IDs at mechanism level, plus signed
source/receipt tampering, expiry, control replay, refunds, separate-process races,
duplicate provider effects and four actual loopback callback cases. Provider data
for these tests is synthetic. A04-T10 permits an identical request to join its
existing effect without another permit use; concurrent reuse for a different
effect is refused. Recorded primary-fixture counters have zero duplicate outbound
capture attempts and zero budget breaches; their exact coverage is declared in
`MOCK_COUNTERS.json`. Those zero counts are not a live-campaign safety result.

Independent readback is implemented as a separate, read-only process. It verifies
fresh order and capture data and ignores the stored receipt. It uses the same
merchant credential; a separate institutional verifier and A05 Judge remain
separate acceptance work. A single bundled merchant capture would not establish
three real merchant purchases or actual delivery of three services.

## Development failures preserved and repaired

1. `run-20261105T174607Z`: 33 tests, 0 failed assertions, **30 execution errors**.
   Cause: SQLite context managers committed but did not close connections; Windows
   refused temporary-database cleanup. Fix: deterministic connection close on exit.
   `run-20261105T174631Z`: 33/33 PASS. Later expanded batteries: 38/38, then 42/42.
2. First native preflight: OAuth PASS, ACL diagnostic ERROR. Windows PowerShell
   could not autoload `Microsoft.PowerShell.Security`. Fix: query file ACLs through
   the Windows .NET API; subsequent native preflight records both file ACLs.
3. A committed-blob hash check exposed Git newline normalization of generated
   design JSON. Raw design attributes and index renormalization repaired it in
   `36db39c`; all 25 final source hashes then matched committed blobs. Frozen TDD
   bytes and acceptance thresholds were never edited.

No unsuccessful run was deleted, reclassified as PASS or counted as native effect.

## Evidence-to-action ledger

| Field | A03 and current authority | Native capture and independent effect verification |
|---|---|---|
| Evidence gap | No installed accepted A03 package, trust roots or signed mission/permit/control | No admitted order, authenticated buyer approval, completed capture or fresh native readback |
| Required evidence | Signed A03 T01-T13 PASS bound to pinned sources, this adapter's exact source hashes and original evidence; current signed mission, one-time permit, monotonic control | Exact approved order, capture ID, final order/capture GETs and ledger joins; separate reader output; supported real fault dispositions |
| Acquisition method | Actual A03/authority owners provide the accepted package into `%LOCALAPPDATA%/Veraxis/LEGION-Commerce/a04`; schemas in `ADMISSION_INTERFACE.md` | Run documented create, buyer Sandbox checkout, capture and new-process readback after admission passes |
| Immediate execution completed | Scoped Dropbox/local artifact search, frozen-hash verification, missing-file preflight, real OAuth, ACL inspection and 42 mechanism checks | Callback/approval/readback and failure mechanisms tested without native writes; no substitute authority minted |
| Acceptance criteria | PASS only verified signatures, source/evidence correspondence, all 13 accepted A03 cases, exact immutable bindings and current controls; reject mismatch/expiry; BLOCKED if absent | A04 PASS only all mandatory cases, native capture+readback, exact money/merchant/identity, zero forbidden/duplicate/overbudget effects; FAIL unsafe effect/false completion; BLOCKED missing approval or evidence |
| Next action / fallback | Install genuine accepted artifacts, run `preflight` and apply signed control; if unavailable, keep writes disabled and hand off this exact interface and source snapshot | Once gates pass, create the smallest authorized Sandbox bundle and authenticate the buyer; reconcile uncertainty using GET only. If approval/evidence cannot be obtained, retain BLOCKED and held reservations; never report DONE |

**VERIFIED reason for current BLOCKED:** native preflight found all six operational
prerequisites absent: `trust.json`, `a03-acceptance.json`, `a03-evidence/`,
`current-mission.json`, `current-permit.json`, `current-control.json`. Credential
availability is not the blocker. Documentary A03 freeze status is NOT_RUN; no
accepted later package was located by the scoped searches. Any later supplied
package must be independently checked through the interface above before dispatch.

## Evidence integrity and handoff

Every retained run has its own evidence hash manifest. Before delivery, 3 frozen
TDD hashes, 112 retained evidence-file hashes and 25 final tested source hashes
were verified. The index contains no credential files, DPAPI files, virtual
environment or live ledger. This is a content-integrity check, not external
notarization. The archive and GitHub branch contain code, frozen public designs,
test results and redacted diagnostics only. Runtime credentials and authority
installation remain on Windows and are excluded from repository/archive delivery.
