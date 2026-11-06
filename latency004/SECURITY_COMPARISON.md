# LATENCY-004: security comparison of the admission architectures

Every architecture evaluates, on every request, every predicate of A04 `Admission.control`, `Admission.ready`, `Admission.check`, `BridgeAdmission.ready`/`check` and `Bridge.policy`/`assess`. The order and denial codes are A's. Equivalence evidence: `WP4_INDEPENDENT_TRIAL/equivalence_*` (1,680 cases × each architecture, every gate evaluated twice), A03 13/13, robustness, and the clock-skew test.

| | A (existing) | B (consolidated) | C (prepared) | BC | **BCh (final)** |
|---|---|---|---|---|---|
| Parent admission code | original | original | `PreparedAdmission` (same predicates) | Prepared + overlap | same as BC |
| Isolated processes per request | Core worker + decider + zverdict, each driven by the parent | 1 worker; it drives decider + zverdict (its children) | as A | as B | as B |
| Ed25519 verification | 11 full verifies per request | 11 full | exact memo: (key string, signature string, signed bytes) → verified only if never seen; failures never cached | as C | as C |
| Session-constant authority (acceptance, upstream ×2, accepted policy) | re-verified each request | re-verified | private deep copies, verified once; expiry, status, pins and bindings still evaluated per request | as C | as C |
| Control envelope (revocation, epoch, activity, expiry) | ledger read and full verify per request | same | ledger read per request; memo exact on bytes, so any new control is fully verified | as C | as C |
| Permit, mission, bundle, observation, CAGE, VEIP | per request | per request | per request (memo exact); bundle read from disk per request | read once per request at the start; presence and content evaluated at A's position | as BC |
| Canonicalization | full per call | full | once per object per request; float-free `json.loads` trees use the same `json.dumps` bytes without the Python type walk | as C | as C |
| Core calculation start | after all parent checks | after all parent checks | after all parent checks | speculative, as soon as policy + observation authenticate; used only for the same objects and an equivalent instant (`_same_freshness`); otherwise recomputed at A's instant | as BC |
| Lean decider ↔ runtime agreement, zverdict compare | parent | worker (same codes and precedence) | parent | worker | worker |
| Worker failure | dead worker → every later request denied | request denied; restart on the next request, only while the snapshot is active | as A | as B | as B |
| Idle wait | blocked pipe read | blocked | blocked | blocked | busy-poll ≤ 60 s after the last request, then blocked |

## What changed in the trust boundary (disclosed)

1. **B/BC/BCh: the decider's and witness's answers reach the parent through the consolidated worker.** In A the parent read `legion_decide`'s verdict directly from the decider process. In B the worker does. A compromised worker could therefore forge the decider verdict. In A, the same compromised worker could already make the honest decider return T by constructing the alpha payload, since the payload is built by that worker. The admission-relevant trust is therefore the same process in both. What is lost is the parent's direct observation of the decider. The worker holds no keys, no network and no PayPal credentials. Its code is OS-locked and import-frozen exactly as in A. Threat model unchanged: same-user OS isolation is not a hostile-code boundary (QUALIFY-002 statement).
2. **C/BC/BCh: verification memo.** Ed25519 verification is a pure function of (public key, message, signature). The memo key includes all three exactly, plus the domain inside the message. A cached result can never be applied after a change of signed content, key, authority (key) or epoch (content). Tested: revocation, epoch transition, deactivation and trust-key rotation after a cached ALLOW are all denied on the next request, identically to A (`*_after_allow` categories).
3. **C/BC/BCh: session-constant authority verified once.** A loads the acceptance, upstream acceptances and accepted policy once at construction (A04 design). C verifies private copies once, so a later in-memory mutation of the caller's object is not observed. A would observe it and deny `INVALID_SIGNATURE`. Every per-request predicate on them, including expiry, is kept.
4. **C/BC/BCh: `design/SOURCE_PINS.json`.** A re-reads and parses this file on every request (an eager default in `Bridge.policy`); its content is never used on the qualified path. C holds it under a deny-write/deny-delete OS lock and parses it once.
5. **BC/BCh: speculative calculation.** The worker computes on authenticated inputs before the authority checks finish. It is pure and effect-free; on denial the answer is drained and discarded. The bundle file is read once at request start (A reads it ~1 ms later in the same request).
6. **BCh: hot standby** changes only how the worker waits for its next request line, not what it computes. Cost: one logical CPU busy while idle, for up to 60 s after the last request.

## Invariants (measured, BCh and every other architecture)
- ALLOW ⇒ valid authority ∧ current epoch ∧ eligible action ∧ valid evidence: no false admission in 1,680 × 2 gate evaluations per architecture, the 28 frozen categories plus 14 authority categories.
- UNKNOWN ⇒ DENY: `unknown_state`, non-canonical float evidence, missing bundle and missing evidence all deny.
- REVOKED ⇒ no new dispatch: `revoked_after_reserve` gives 0 provider calls; revocation after a cached ALLOW is denied.
- Unauthorized effects = 0 (robustness: 0 provider calls). Verdict mismatches = 0.
