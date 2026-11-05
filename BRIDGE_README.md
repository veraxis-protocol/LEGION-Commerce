# WO-COM-A03-BRIDGE-001 — Verified Commerce Admission

External bridge for the frozen [A03 → A04 interface](docs/ADMISSION_INTERFACE.md).
It executes actual pinned Core methods and the compiled upstream Lean verdict,
then verifies separate operator authority, mission, intent, budget and epoch bindings.
No source in Claude's `formal-core-001` checkout is modified. No Core file is
copied into this repository; upstream sources live in ignored isolated clones.

**Operational decision: BLOCKED.** Conformance inputs use temporary test keys;
they never establish operational permission. This package has no key generator,
acceptance signer, arbitrary signing command or fallback authorization. Existing
operator key paths/IDs and a genuine independently accepted input package are
required. Follow [the operator procedure](docs/BRIDGE_OPERATOR_PROCEDURE.md).

## Reproduce on Windows

Python 3.14 x64 and Lean `leanprover/lean4:v4.29.1` were used. All PayPal credentials
remain in their existing Windows-user DPAPI files and are never copied to the
source clones, repository or evidence outputs.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r bridge-requirements.lock
.\.venv\Scripts\python.exe tools\bootstrap_bridge_sources.py
.\.venv\Scripts\python.exe -m commerce_bridge source-check
.\.venv\Scripts\python.exe tools\run_bridge_conformance.py
.\.venv\Scripts\python.exe tools\run_bridge_integration.py
.\.venv\Scripts\python.exe tools\run_checks.py
```

The bootstrap never resets an existing source clone: mismatched or dirty sources
stop the build. It builds ZSwarm/zverdict, ZLegion and ZRuntimeGate and preserves
the actual compiler/axiom transcripts. Source pins remain those in the frozen TDD:
LEGION `d083c44…`, ZTL `e1141c6…`, ztl-swarm `7d2a826…`. ztl-stand remains the A01
upstream source pin; this bridge does not claim to run its optimization/acceptance.

Each A03 run freezes its exact inputs/source hashes, archives the source bytes,
preserves all 13 outcomes and records every bridge assessment/denial. Reproduce
the saved calculations in another process:

```powershell
.\.venv\Scripts\python.exe tools\replay_bridge_evidence.py <bridge-evidence-run-directory>
```

`REPLAY_MATCH` means the calculations reproduced. It is not an institutional
independent acceptance signature or proof that test observations happened in the
real world. Deliberately injected unavailable/timeout/mismatch witness cases are
preserved separately; they are not counted as observed Core/Lean disagreements.

## Runtime and proof scope

- `core_worker.py` imports the pinned Core in a separate process and executes
  `StructMissionController._directive_locked`, `_facts_for`, `StructWorld.validate_args`
  and predicates, and `ZtlEvidenceService.submit/verdict`. It does not contact the
  world server, generate reporters or mint permits. Input observations must already
  be authenticated by the delegated Core observation signer.
- The Core evidence service verifies reporter Ed25519 signatures in its own JCS
  format, applies its freshness window and collapses independence bases. The bridge
  rejects equivocation before Core's latest-report-wins map can hide it. It also
  rejects counting one declared source as several independent bases.
- A separate, bounded native process executes the exact compiled `ZSwarm.verdict`
  on those same seats. Missing executable, timeout, malformed response or mismatch
  denies admission. ZLegion's order-sensitive merge is not used as this runtime rule.
- Every required fact must pass, and its predicate must also hold in the authenticated
  Core snapshot. The first fact's state cannot conceal a later false/unknown fact.
- Core ACT is separate from actor scope, CAGE ALLOW, VEIP ELIGIBLE, numerical budget,
  current control, original inventory, exact action terms and PayPal approval.
- The unsigned assessment binds stable derived results, observation/policy/intent
  hashes, both epoch contexts and exact native source identity. Freshness and
  current authority are recomputed at each A04 boundary.
- The original A04 runtime package and frozen interface remain unchanged. The bridge
  integrates through `BridgeAdmission` and the existing `Gateway` composition seam.
  It adds evidence/source bindings in signed payload extension fields permitted by
  the original envelope format.

The Lean theorem premises remain explicit. Signatures prove an enrolled key signed
bytes; they do not prove observer independence, real-world truth, the Python bridge,
all of LEGION, or PayPal effects. The pinned Core's five auto-generated observers
are not used as real independent authority. The signed observation protocol is an
external producer seam; an authorized Core observer must actually export it from
the real run. An unsigned saved log or developer-authored snapshot is not enough.

## Current operational commands

`preflight` without operator roots reports the exact missing files and performs
zero PayPal calls. After external acceptance, the bridge validates the original six
A04 prerequisites plus its signed policy, observation bundle and installation
manifest. It replays Core/Lean admission before opening a credential-owning writer.

```powershell
.\.venv\Scripts\python.exe -m commerce_bridge preflight
```

Once genuine public roots, externally signed inputs and buyer consent are available,
the documented `install`, `preflight`, `create`, `serve-approval`, `capture`,
`reconcile` and `independent-readback` commands use the same A04 ledger and Windows
credential owner. Reconciliation/readback remain read-only and can run after a
permit expires. No read command can release uncertainty or authorize a POST.

The host's Windows account, SYSTEM and local Administrators are trusted. Same-user
or administrator malicious code is not isolated by Python package boundaries.
An agent-facing deployment still needs an independently accepted account/process
custody boundary; no claim of hostile-local-user resistance is made here.

See the execution report in `docs/WO_COM_A03_BRIDGE_001_EXECUTION_REPORT.md` for
exact commits, results, performance gate, evidence gaps and next actions.
