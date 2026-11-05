# WO-COM-A03-BRIDGE-001 — historical resume checkpoint

User requested on 2026-11-05: "continue once the usage limit resets".
The user subsequently requested immediate resumption. The scheduled heartbeat was
deleted and work resumed. This file records the earlier pause state; consult
docs/WO_COM_A03_BRIDGE_001_EXECUTION_REPORT.md for final results and commits.
No native payment or authority generation is authorized merely by resumption.

## Workspace and isolation

- Worktree: `C:\Users\Administrator\Documents\ChatGPT\Hackathons\LEGION-Commerce-A03-Bridge`.
- Branch: `codex/wo-com-a03-bridge-001`, based on A04 evidence commit
  `4c7e6b7c97b828008dd7382f1f11a4c9b9c5685b`.
- New bridge files remain uncommitted; preserve them. No push/bridge commit yet.
- Active Claude checkout `C:\Users\Administrator\LEGION_FORMAL_CORE` was read only.
  Its initial branch/head were `formal-core-001` / `4375a8e7569c3dc78d7e743e2b4839b589d1e91f`.
- Isolated upstream clones are ignored under `runtime_sources/`; no Core code is
  copied into the Commerce repository. PayPal credentials stay on Windows and
  must never enter logs, GitHub, Dropbox or source clones.

## Completed evidence and code

- Pinned LEGION d083c44, ZTL e1141c6 and ztl-swarm 7d2a826 cloned and byte-checked.
- Real Windows Lean 4.29.1 builds of ZLegion, ZRuntimeGate and ZSwarm/zverdict
  succeeded; compiler and actual axiom transcripts preserved in bridge_evidence.
- External `commerce_bridge` executes actual Core methods in an isolated worker
  and compares real Core service verdicts to a persistent compiled Lean process.
- Signed Core/CAGE/VEIP input bindings, explicit original inventory, source/key
  roles, numerical budget, epoch/revocation, strict UNKNOWN rejection, action terms
  and freshness checks are implemented. No production signing/key generation.
- `BridgeAdmission` composes with the unchanged frozen A04 runtime package.
  Frozen ADMISSION_INTERFACE.md SHA-256:
  adc2ff7f83d642b75ac5baf0865025369312891edda06a29ca0949e580086127.
- Operator package verification, independent public-root fingerprint, separate
  acceptance/install signatures, atomic staged installation and Windows ACLs
  implemented. Dedicated apply-control works without valid acceptance or Lean.
- Read-only reconciliation remains available after permit expiry.
- Executable replay, bootstrap, profiling and unsigned review-request tools exist.
- Operator docs: BRIDGE_README.md and docs/BRIDGE_OPERATOR_PROCEDURE.md.

## Results so far — do not inflate scope

- bridge_evidence/run-20261105T181519Z: 13/13 conformance checks passed using real
  pinned Core/Lean calculations and TEST signatures/observations only.
- bridge_evidence/integration-20261105T182121Z: 5/5 package tests passed before
  later ACL/revocation additions. Current integration suite now has 6 tests and
  needs its final run.
- Targeted seam check passed; latest witness deadline diagnostic passed
  (1 test, 1.703s). Deadline now covers a blocked stdin write as well as stdout.
- Full gate p95 in initial run: 1593.437 ms, above frozen <=5ms target.
  Keep PERFORMANCE_FAIL; never report full A03 acceptance from conformance alone.
- Phase profile bridge_evidence/profile-20261105T182831Z:
  source checks 642.9–736.9 ms, Core worker + witness 459.9–544.5 ms,
  persistent native verdict alone 0.279–0.383 ms. No optimization performed.
- Operational preflight: BLOCKED. Nine package entries absent, including all six
  original A04 prerequisites. 0 native Orders/capture calls and 0 operational installs.
- User chose "Use existing keys — I will provide paths/IDs". Actual paths/IDs,
  fingerprints, independently signed acceptance and genuine operational inputs
  have NOT been supplied. Do not generate substitutes or self-attest acceptance.

## Concrete next steps after reset

1. Re-read this checkpoint and current Git status; keep all changes and failures.
2. Inspect current bridge for correctness; final checks must cover latest state
   schema, blocked-I/O timeout and independent revocation command.
3. Commit implementation on the separate bridge branch using explicit Codex
   author identity if necessary. Original repository has no configured author.
4. Run final 13-case conformance, 6-case package integration, and 42 A04 regressions.
   Use tools/run_bridge_conformance.py, tools/run_bridge_integration.py and
   tools/run_checks.py. Source manifests/snapshots freeze before each run.
5. Run tools/replay_bridge_evidence.py in a separate process against the final
   conformance run; preserve replay output outside that frozen run directory.
6. Prepare unsigned review manifest with tools/prepare_bridge_review.py. It must
   remain UNSIGNED_REVIEW_REQUIRED until the actual external acceptance owner acts.
7. Write docs/WO_COM_A03_BRIDGE_001_EXECUTION_REPORT.md with exact test dispositions,
   commits, latency failure, source/authority gaps and six-field executable paths.
8. Verify raw Git blob/evidence/archive hashes, exclude runtime_sources, secrets,
   virtualenvs and live ledgers; commit/push evidence and create a local ZIP/receipt.
9. If genuine operator roots/evidence arrive, validate them via the documented
   procedure. Only genuine acceptance + current admission + buyer approval may
   reach native Sandbox capture and separate provider GET readback.

Acceptance: GO only all frozen mandatory gates, source correspondence, independent
acceptance, actual authority and required native effect evidence pass. FAIL any
unsafe allowance, duplicate/overbudget payment, false DONE or hidden mismatch.
If required external evidence remains unavailable, finish independently executable
integration deliverables and retain BLOCKED/NO-GO with the exact acquisition path.
