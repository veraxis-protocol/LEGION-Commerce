# WO-COM-LATENCY-004: admission performance, final report

**Verdict: GO.** Architecture **BCh** (consolidated admission worker + prepared admission context + speculative early dispatch + worker hot standby) gives a full-gate p95 of **2.67â€“2.81 ms back-to-back** and **3.27â€“3.28 ms at a paced 50 req/s**. The existing architecture A gives 6.46â€“6.51 ms and 12.3â€“12.5 ms on the same host. Same boundary, same p95 definition, no safety check removed. Measurement conditions: AC power, CPU not thermally throttled. When the laptop is thermally throttled (speed index â‰¥ 120, CPU ~3Ã— slower), no architecture meets 5 ms; see "Limits".

- Repository: https://github.com/veraxis-protocol/LEGION-Commerce, branch `claude/wo-com-latency-004` from `ed8b3e1`.
- Measured code: `ca897f7`. Report head: see SOURCE_COMMITS.txt.
- Formal Core, Lean proofs, the qualified decider `99cda684â€¦`, the A04 adapter and the frozen requirements are unchanged.

## Boundary and definition (unchanged from QUALIFY-002 WP3)
- **Measured interval:** wall time of `Gateway.check(db, mission, permit)` with the SQLite transaction open. This is the current control signature plus the complete admission check.
- **p95:** the âŒˆ0.95Â·NâŒ‰-th smallest sample. Clock: `perf_counter_ns` (QPC).
- **Protocols:**
  - `repeat`: the same permit on every request (the QUALIFY-002 protocol).
  - `fresh`: every request presents a new permit, observation, CAGE, VEIP and bundle file, so four never-seen signatures. The issuer reads its bundle back after writing it (E1c).
  - `fresh_cold`: as `fresh`, without the read-back.
- **Load regimes:** back-to-back (the QUALIFY-002 regime), or paced at one request every 20 ms.

## WP1: where the latency goes
Baseline A at `ed8b3e1` was profiled at N=1000 after 100 warm-up requests, with each component timed exclusively in wall time and in cycle-precise CPU time. QPC stamps from shims around the unmodified workers measure IPC transit and wake-up directly. The sum of components reconstructs **99.94 %** of the observed mean, with an unattributed residual of p95 0.017 ms. This profile was taken on battery in the throttled state (p50 23.0, p95 26.3, p99 29.6 ms). The same decomposition on AC with final code is in `WP2_ARCHITECTURES/wp2_profiles/PROFILE_A_repeat.json` (p50 6.44, p95 7.04 ms, 99.92 % explained). Paced (20 ms) decompositions: `WP1_PROFILE/wp1_paced_profiles`. BCh paced instrumented p95 is 3.41 ms; its worker critical path is 1.79 ms mean, of which Core is 1.15 ms.

| Component (A, `ed8b3e1`, battery) | mean ms | p95 | p99 | calls/req |
|---|---|---|---|---|
| Core worker compute (frozen Core + alpha + oracle, in its process) | 6.13 | 6.99 | 8.61 | 1 |
| Ed25519 verification (OpenSSL) | 4.06 | 4.71 | 6.07 | 11 |
| Canonicalization for digests | 2.38 | 2.88 | 3.37 | 13 |
| Bundle load (is_file, read, parse) and bindings | 1.72 | 2.08 | 2.51 | 1 |
| Canonicalization inside verify | 1.24 | 1.53 | 1.88 | 11 |
| `design/SOURCE_PINS.json` re-read (eager default in `Bridge.policy`) | 1.20 | 1.47 | 1.84 | 1 |
| Watchdog `threading.Timer` thread created per IPC call | 1.42 | â€” | â€” | 3 |
| Session glue, key decode, verify glue | 1.34 | â€” | â€” | â€” |
| IPC: decider and witness round trips, Core-worker transit and wake | 0.91 | â€” | â€” | 3 |
| JSON serialize/parse of the Core request and response | 0.95 | â€” | â€” | 4 |
| Predicates (readiness, A04, bridge, policy, control) and SQLite | 0.89 | â€” | â€” | â€” |

Also measured:
- **Cold start:** session preparation (hash, lock, git verify, spawn) median 4.46 s; first gateway admission after it 17.8â€“61.4 ms (10 cold processes).
- **Memory:** 159 KB peak allocation per request, 2.1 KB retained.
- **GC:** 0.047 pauses per request, up to 0.71 ms at p99.
- **Lock contention:** the IPC lock waits 0.008â€“0.03 ms (single client).
- **Windows scheduling and wake:** worker wake 0.09 ms; return transit plus parent wake 0.33 ms. Under paced load, idle clock-down costs 3.4â€“4.5 ms of p95 (E1f).

## WP2: architectures (equivalence proven per architecture, see WP4)
| Arch | Mechanism | Back-to-back repeat p95 | fresh p95 | fresh_cold p95 | Paced 20 ms p95 (repeat / fresh) |
|---|---|---|---|---|---|
| A | existing: three parent-driven isolated round trips | 6.51 (dedicated); 6.61 (interleaved) | 6.46; 8.71 | 6.74 | 12.32 / 12.47 |
| B | one consolidated isolated worker runs Core, alpha, oracle, decider â€– witness; the parent is unchanged | 5.72 | 8.15 | 5.83 | â€” |
| C | prepared context: exact verify memo, canonical reuse, immutable session authority; A's processes | 4.11 | 6.24 | 4.17 | â€” |
| BC | B + C + speculative early dispatch | 3.05 | 4.43 | 2.84 | 7.65 / 7.60 |
| **BCh** | BC + worker hot standby | **2.81** (dedicated, N=2000) | **2.67** | â€” | **3.27 / 3.28** (N=2000) |

The interleaved runs rotate A, B, C and BC in 10 rounds; all rounds were unthrottled (speed index 62â€“71). BCh is benchmarked dedicated, because its busy-polling worker also keeps the CPU awake for any architecture interleaved with it (E1g confound, recorded). The interleaved `fresh` tail (A 8.71, B 8.15, C 6.24, BC 4.43) coincides with Defender scanning the ~4,000 bundle files the run had just written. `fresh_cold` and the dedicated fresh run (BCh 2.67) do not show it.

## WP3: isolated experiments (hypothesis, mechanism, before/after, negatives, refutation)
| # | Hypothesis | Mechanism | Before â†’ after | Negative tests | Result |
|---|---|---|---|---|---|
| E1a | Per-call watchdog threads and thread hand-offs cost ~1.4 ms | persistent watchdog per child; caller-thread pipe I/O (`transport.Child`) | A 3 Ã— 0.38â€“0.56 ms Timer starts â†’ 0 | hang â†’ `QUALIFIED_WORKER_TIMEOUT` in 0.512 s (bound 0.5 s); crash â†’ deny | SUPPORTED |
| E1a' | Unbuffered pipes are fine | first transport used `bufsize=0` | found: `readline()` issued one `ReadFile` per byte; buffered â†’ transit and wake to parent 0.88 â†’ 0.05 ms | â€” | defect found and repaired |
| E1b | EcoQoS opt-out speeds up our processes | `SetProcessInformation` per process | 1e6-loop 31.07 vs 31.22 vs 31.21 ms | â€” | REFUTED (no effect) |
| E1c | A new bundle file's first open is slow (AV on-access scan) and can be absorbed at issuance | issuer read-back after write | first open p50 0.82 â†’ 0.24 ms, p95 1.30 â†’ 0.34 ms, max 25.6 â†’ 1.3 ms | â€” | SUPPORTED |
| E1d | A bounded spin while waiting for an answer helps | `spin_seconds` (BCs) | BC 4.47 â†’ BCs 4.77 ms p95 repeat; fresh_cold 5.11 â†’ 6.36 | â€” | REFUTED |
| E1e | Per-process HighQoS / priority fixes paced load | QoS on parent and worker | run 1 (pre-final): gains â‰¤ 0.05 ms; run 2 (final): 7.84 â†’ 6.29 ms; not reproducible, never < 5 ms. Found: for the Python worker it applies to the venv launcher, not the interpreter (see defects) | â€” | INCONCLUSIVE, not adopted |
| E1f | Paced penalty = CPU idling between requests | same gateway, 20 ms gaps slept or busy-waited | slept 6.45 vs busy 3.62 vs back-to-back 3.01 ms p95 | â€” | SUPPORTED (idle penalty 2.8â€“4.5 ms) |
| E1g | Keeping the worker's core awake removes the idle penalty | worker hot standby: busy-poll its request pipe â‰¤ 60 s after the last request | dedicated paced p95 BC 7.65 / 7.60 â†’ BCh 3.27 / 3.28 ms | full battery on BCh (WP4) | SUPPORTED, adopted |
| E2 | Consolidating the three isolated round trips helps | consolidated worker; decider â€– witness concurrent | A 6.61 â†’ B 5.72 ms p95 (interleaved) | equivalence on B; decider/witness faults | SUPPORTED |
| E2b | Worker glue (3Ã— `Path.resolve`, a Proactor loop per fact) is avoidable | resolve once; drive the non-suspending coroutine by `send()` | 1,200 inputs, 0 output differences; worker mean 1.18 â†’ 0.64 ms | a suspending coroutine fails closed | SUPPORTED |
| E3 | Serialization is significant | slim response; float-free `json.loads` tree â‡’ the same `json.dumps` bytes | canonicalize 1.96 â†’ 0.46 ms per request (instrumented) | float evidence â†’ `NONCANONICAL_TYPE` (equivalence) | SUPPORTED (an intermediate compose-by-parts variant was slower and is recorded) |
| E4 | Signature work is memoizable without semantic change | exact memo (key, sig, signed bytes); key-object cache | repeat: 11 â†’ 0 verifies; fresh: 11 â†’ 4 | forged permit/mission/observation; wrong key; trust-key rotation after ALLOW | SUPPORTED |
| E5 | Revocation and state stay per request | control read and fully checked per request; memo exact | â€” | revocation, actor revocation, epoch transition, deactivation after a cached ALLOW, revoked after reserve: all deny exactly as A, 0 provider calls | SUPPORTED |
| E6 | Overlapping the independent checks with Core helps | (late) dispatch after observation verify; (early) speculative dispatch at request start | p95: no overlap 5.62, late 5.23, early 5.14 ms; p50: late âˆ’0.05, early +0.24 | denial after dispatch: identical code, next request ALLOW (all modes); clock skew 81/81 | late REFUTED (p50); early SUPPORTED |

## WP4: independent trial of BCh (final code)
- **A03:** 13/13. **Matched cases:** 1,200 model + 1,000 alpha, 0 mismatches. **False authorizations:** 0. Warm negatives pass. The tool's own N=100 p95 is 2.72 ms (`qualify_evidence/run-20261106T043810Z-BCh`).
- **Gateway-level differential**, reference A, every gate evaluated twice: A, B, C, BC and BCh on 1,680 frozen cases (42 categories × 40), 19,200 gate evaluations: **1,680/1,680 equivalent to A, 0 verdict mismatches, 0 false admissions** (`WP4_INDEPENDENT_TRIAL/wp4_equivalence_run`).
- **Independent replay** (separate process, A vs BCh): 1,680 cases, 7,680 gate evaluations: 1,680/1,680 equivalent, 0 mismatches, 0 false admissions. Run-to-run comparison: **REPLAY_MATCH**. 0 family-level differences; 0 raw differences for BCh. 5 raw differences for A only, which are A's own decider-timeout code race (`QUALIFIED_WORKER_TIMEOUT` vs `…_TIMEOUT_OR_PROTOCOL`, one specified family; BCh is deterministic) (`WP4_REPLAY_COMPARISON.json`).
- **Revocation and epoch transitions; forged signatures; changed intent/amount; unknown evidence:** inside the differential (42 categories).
- **Advancing clock:** 81/81 for both A and BCh; the speculative result was used 71 times and discarded 10 times, exactly at the freshness boundary.
- **Worker crash, restart, timeout; concurrency (8 threads, 1,200 mixed requests):** all PASS. 0 mismatches, 0 false admissions, 0 provider calls.
- **Invariants:** ALLOW â‡’ valid authority âˆ§ current epoch âˆ§ eligible action âˆ§ valid evidence; UNKNOWN â‡’ DENY; REVOKED â‡’ no new dispatch; UNAUTHORIZED_EFFECTS = 0; VERDICT_MISMATCHES = 0.

## GO / NO-GO
| Criterion | Result |
|---|---|
| Full admission p95 â‰¤ 5 ms | BCh 2.67â€“2.81 ms back-to-back, 3.27â€“3.28 ms paced 50 req/s (unthrottled host, AC) |
| All safety checks kept | yes; disclosures in SECURITY_COMPARISON.md |
| Zero false admissions | yes: 0 in the differential (19,200 evaluations), replay, A03, matched cases, concurrency and clock skew |
| Zero unexplained divergences | yes: 0 verdict mismatches; the only run-to-run variation is A's documented timeout-code race |
| Independent reproduction PASS | yes: REPLAY_MATCH in an independent process (A vs BCh, same frozen seed) |

## Limits (measured, not hidden)
- **Thermal throttling.** Under sustained heavy load this laptop throttles even on AC; the speed index jumped from ~65 to 150â€“220. In that state every component slows ~3Ã—, the frozen Core calculation alone takes ~3â€“4 ms, and BC's p95 was 7.1 ms. No architecture meets 5 ms there. The measured lower bound of BCh's critical path on an unthrottled host is about 2.3 ms p50: Core ~1.1â€“1.3 ms, decider â€– witness 0.2 ms, IPC 0.15 ms, and parent work ~0.8 ms that is mostly overlapped.
- **Hot standby** keeps one logical CPU busy while the gateway is idle, for up to 60 s after the last request. On battery that costs power. Without it (BC), paced p95 is 7.6 ms on this host's Balanced plan. A host-side alternative is a performance power mode, which only the owner may switch.
- **Fresh bundle files.** The first open of a never-opened bundle file is charged an AV scan unless the issuer reads it back once (E1c).
