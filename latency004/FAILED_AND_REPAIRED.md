# LATENCY-004: failures, defects and negative results (all preserved)

| # | Found | Effect | Disposition | Evidence |
|---|---|---|---|---|
| 1 | A creates a `threading.Timer` thread on every IPC call (3 per request) | 0.38–0.56 ms each | New transport: one persistent watchdog per child. A unchanged (reference) | WP1 PROFILE_A |
| 2 | `Bridge.policy` evaluates `self.design_pins()` eagerly as a `getattr` default: reads and parses `design/SOURCE_PINS.json` on every request, value unused on the qualified path | 0.35–1.2 ms | C: file held under a deny-write/deny-delete OS lock, parsed once. A unchanged | WP1 |
| 3 | `core_worker.calculate` called `Path.resolve()` 3× per request and ran a Proactor event-loop iteration per fact | worker mean 1.18 ms vs 0.64 ms | Resolve once per worker; drive the non-suspending coroutine with `send()`; fail closed if it suspends. 1,200 inputs, 0 output differences | E2b |
| 4 | My first transport opened pipes with `bufsize=0`: `readline()` then issues one `ReadFile` per byte | return transit 0.88 ms | Buffered pipes (0.05 ms) | profiles before/after |
| 5 | My first canonical-reuse scheme composed canonical bytes from parts: 241 small calls per request, slower than one full call | +1.2 ms | Replaced by the float-free `json.loads` fast path (canonicalize 1.96 → 0.46 ms) | profiles |
| 6 | Bounded spin-wait for answers (BCs) | no gain (p95 4.47 → 4.77) | Not adopted (negative result) | ARCHIVE/run2 |
| 7 | Late overlap (dispatch after observation verify) | p50 not improved | Not adopted alone; early speculative dispatch adopted | E6 |
| 8 | EcoQoS / priority opt-outs | no reproducible effect | Not adopted | E1b, E1e |
| 9 | The per-process EcoQoS opt-out in `transport.Child` is applied to `process._handle`. For the Python worker that is the venv `python.exe` launcher, not the interpreter it starts | The Python worker interpreter itself was never opted out. The Lean decider and witness (real executables) were | Recorded, not changed (no safety role, no measurable effect, E1b/E1e). Kill and timeout are unaffected: killing the launcher terminates the interpreter (crash/hang trials pass) | robustness `suspended_pids` (7-process tree) |
| 10 | Robustness hang trial v1 suspended the worker AFTER writing the request; the worker finished first. v2 suspended only the launcher (#9) | false FAIL ("hung" request answered) | v3 suspends the whole tree before the write: `QUALIFIED_WORKER_TIMEOUT` at 0.512 s | wp4_robustness |
| 11 | Equivalence harness: rotating the trust key mutated the fixture's trust dict shared by every architecture's gateway; the intent mutator mutated `f.intent` (also inside the signed mission) | 2 false mismatch reports; one category exercised a different code | Private copies; rerun 0 mismatches | trial runs |
| 12 | The profiling shim for the consolidated worker re-implements its main loop and lacked the hot-standby wait | The first "BCh paced" instrumented profile actually measured BC | Shim fixed; profile re-run. The GO benchmark uses the real worker and is unaffected | wp1_paced_profiles |
| 13 | Interleaving BCh with other architectures | Its busy-polling worker keeps the CPU awake for them too (confound) | BCh benchmarked dedicated; interleaved comparison is A/B/C/BC | E1g notes |
| 14 | Host: battery and thermal throttling (speed index 65 ↔ 240), a foreign `lake build IAA` (Lean 4.35, not ours) during two paced runs, Defender scanning freshly written bundle files | Large confounds | Runs discarded or archived with host state recorded; final runs on AC, gated on no foreign Lean build, with cool-downs | ARCHIVE/, bench host_state |
| 15 | Intermediate evidence on non-final code (equivalence 1,680-case run on `f20e179`, benchmarks before early dispatch / hot standby) | Not final-code evidence | Archived, not cited as final | ARCHIVE/ |

Pre-existing behaviour unchanged: malformed upstream-acceptance JSON fails closed with `JSONDecodeError` (QUALIFY-002 note).
