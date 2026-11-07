# WO-COM-AUTHORITY-005: initial Sandbox signing infrastructure, final report

**Infrastructure verdict: BLOCKED.** The signing infrastructure is built and passes every signing, verification, revocation, rotation and adversarial test. The control role is genuinely established under the owner's custody. The admission issuer and the independent acceptance reviewer have not been designated, so only one of the three role identities exists and the trust anchor cannot be built or independently confirmed.

**Operational verdict: BLOCKED.** No genuine trust anchor, mission, Core/CAGE/VEIP evidence, issuer, independent acceptance or installed package exists. `commerce_bridge preflight` returns BLOCKED (9/9 package entries absent). Nothing was forced to PASS. No substitute signature was created, no PayPal call was made, and no buyer or credential was touched.

- Branch: `claude/wo-com-authority-005`, from `57f32fa`. Commits: see SOURCE_COMMITS.txt.
- The admission hot path is byte-identical to the measured `ca897f7` (12/12 files). Only `commerce_bridge/__main__.py` and `installation.py` changed, to run the operational CLI on the qualified BCh runtime (`--runtime bch`, the default; `v1` stays selectable).
- Lean proofs, the Formal Core, the qualified decider and the A04 adapter are unchanged.

## WP1: three signing authorities
| Role | Owner | Status |
|---|---|---|
| control | Veraxis operator | **ESTABLISHED.** The owner personally ran `init-role` in their own console on 2026-11-06 and entered their own passphrase; the owner confirms the self-test passed. Key id `fdaab34c5defcf9805c32bb5`, public key `uHY+AZAKcPqxj43UmXfLNOYJ6t7uNPnNwuMYkKXulh0=`. The enrollment's proof of possession was verified independently. |
| admission | separately authorized LEGION admission issuer | **NOT DESIGNATED.** Provisioning package ready (`docs/PROVISIONING_PACKAGES.md`). Not operational. |
| acceptance | independently designated technical reviewer | **NOT DESIGNATED.** Provisioning package ready. Not operational. Claude has not acted and will not act in this role. |

Role restriction is enforced twice. The signer refuses any domain outside the role's list (`DOMAIN_NOT_PERMITTED_FOR_ROLE`). Permits can be signed only through `sign-permit`, which re-executes the qualified BCh assessment. A04 verification independently checks each domain against exactly one trust role.

## WP2: key storage and custody
- **Two protection layers:** a custodian passphrase (scrypt, then ChaCha20-Poly1305, role and key id as associated data) inside Windows DPAPI (CurrentUser, role-bound entropy).
- **Store:** `%LOCALAPPDATA%\Veraxis\LEGION-Commerce\authority`, ACL limited to Administrator, SYSTEM and Administrators, inheritance removed. Verified with `icacls`.
- **Passphrase input:** only from a real interactive console, checked with `GetConsoleMode`.
  - Defect found and fixed: `isatty()` reports True for the NUL device that automated tools use as stdin, so the first version would have prompted invisibly.
  - Tested: Claude's tools are refused (`INTERACTIVE_CUSTODIAN_CONSOLE_REQUIRED`).
- **Refused:** private-key locations in Dropbox, OneDrive, GitHub, `Documents\ChatGPT` or any git tree; key files inside install packages; enrollments marked as test keys in an operational anchor; acceptance custody shared with the control or admission custodian.
- **Honest custody statement:** the control key lives under `E2D5C336J3\Administrator`, the same Windows account Claude's agent runs as. DPAPI does **not** separate the owner from Claude here; the owner's passphrase does. It is never typed into chat, logged or stored. Claude never opened, unsealed or copied the sealed file.
- **Procedures** for backup, recovery, rotation, revocation and suspected compromise: `docs/AUTHORITY_CUSTODY.md`. Backup export is passphrase-encrypted only and refused in cloud or repository locations. `import-backup` is not implemented; until it is, recovery means rotation.

## WP3: trust anchor
**BLOCKED.** It requires three verified enrollments. The tooling is ready and tested:
- `build-trust` checks Ed25519 format, distinct keys, proof of possession, the operational environment, revoked-key exclusion and custody separation.
- `confirm-trust` makes the operator type the fingerprint prefix and sign a confirmation with the control key.
- The output is byte-compatible with `commerce_bridge.installation.checked_trust`. A full package validated under a test anchor built this way (`positive_package_validates_bch`).

## WP4: operational artifacts
| Artifact | Signer | Status | Missing |
|---|---|---|---|
| current-mission.json | control | not signed | the real mission terms from the owner: obligations, merchant, amount, currency, deadline, quote, actor |
| current-control.json | control | not signed | the mission above; epoch and sequence decided by the owner |
| bridge-policy.json | control | not signed | real Core observer, CAGE and VEIP public keys; enrolled reporters with reviewed independence; world hash of the real Core mission |
| Core observation | Core observer | absent | no Commerce Core-observer identity or actual Core run exists on this host |
| CAGE authorization | CAGE | absent | no Commerce CAGE authority or execution exists |
| VEIP authorization | VEIP | absent | no Commerce VEIP authority or execution exists |
| Admission permit | admission | absent | the issuer is not designated, and there is no eligible assessment without the evidence above |
| A01/A02/A03 acceptance | acceptance | absent | the reviewer is not designated; A01/A02 evidence is absent; unsigned v4 review request issued |
| Installation manifest | acceptance | absent | as above |

No ALLOW or ELIGIBLE result was invented, and no artifact was signed with invented terms. `REVIEW_REQUEST_v4_UNSIGNED.json` binds the exact BCh sources (12/12 hot-path files identical to `ca897f7`) and the qualified Core and decider for the future reviewer.

## WP5: adversarial verification
Run on the qualified BCh runtime, with role keys made by the real custody code in a temporary TEST store, conformance-only Core/CAGE/VEIP sources, a full A04 install package and FakePayPal. **73/73 PASS.**

| Required test | Result |
|---|---|
| Forged control / mission / admission permit / acceptance / installation manifest signature | DENY `INVALID_SIGNATURE` |
| Cross-role substitution (11 cases at the signer and the verifier, incl. permit by control, mission by admission, control by acceptance, acceptance by control or admission, manifest by control) | refused or `INVALID_SIGNATURE`; **0 accepted** |
| Modified amount / merchant in the permit | `ORIGINAL_INTENT_MISMATCH` |
| Modified mission cap / merchants, re-signed | `CONTROL_INACTIVE` (the control binds the mission hash) |
| Revoked key | refused for signing (`KEY_NOT_ACTIVE`), excluded from the anchor, its permit rejected under the rotated anchor |
| Rotation | the new key's permit validates; the old anchor rejects it; operator confirmation of the rotated anchor verified |
| Stale epoch / revoked permit / expired permit | `STALE_EPOCH` / `REVOKED_PERMIT` / `EXPIRED_PERMIT` |
| Changed bridge source / adapter source / evidence hash | `BRIDGE_SOURCE_CORRESPONDENCE` / `A03_ADAPTER_CORRESPONDENCE` / `A03_EVIDENCE_HASH` |
| Missing Core / CAGE / VEIP evidence | `CORE_EVIDENCE_BUNDLE_HASH`. With the permit re-bound to the stripped bundle it fails closed (`KeyError`, CLI → BLOCKED; same pre-existing behaviour as A). Missing bundle → `CORE_EVIDENCE_BUNDLE_MISSING` |
| Trust-anchor replacement / wrong fingerprint / attacker anchor with its own fingerprint | `PACKAGE_TRUST_SUBSTITUTION` / `OPERATOR_TRUST_FINGERPRINT` |
| Worker crash during verification | DENY `QUALIFIED_WORKER_TIMEOUT_OR_PROTOCOL`; the next request is served after restart |
| Reuse of a consumed permit | no new effect and no provider call; a second permit for the same effect → `EFFECT_IDENTITY_CONFLICT`; captures = 1 |

Totals: unauthorized admissions 0, cross-role substitutions accepted 0, revoked permissions accepted 0, incorrect monetary terms accepted 0.

**Regressions:** A04 42/42; A03 v1 conformance 13/13; A03 on BCh 13/13 with 1,200 + 1,000 matched cases, 0 mismatches, 0 false authorizations.

**Latency recheck:**

| p95 | Recheck now (quiet host) | LATENCY-004 baseline |
|---|---|---|
| BCh, back-to-back repeat | 5.25 ms | 2.81 ms |
| BCh, back-to-back fresh | 4.86 ms | 2.67 ms |
| BCh, paced repeat | 7.28 ms | 3.27 ms |
| BCh, paced fresh | 7.85 ms | 3.28 ms |
| A03 tool on BCh (N=100) | 2.99 ms | — |

This is a **material deviation, recorded**. It is not caused by this WO: the admission hot path is byte-identical, and the unchanged LATENCY-004 control experiment shifted by the same amount on the same host (back-to-back 3.01 → 5.81 ms; busy gap 3.62 → 6.83 ms). The ≤ 5 ms GO of LATENCY-004 therefore does not hold in the host's current state. The cause is host-side; the speed index during paced runs was 95–130 against 65–70 before.

## WP6: preflight
`python -m commerce_bridge preflight` → `{"status":"BLOCKED","code":"EXISTING_OPERATOR_KEY_PATHS_AND_FINGERPRINTS_REQUIRED","missing":[all 9 entries],"transaction_calls":0}`. Nothing was installed, because no artifact has established authority and provenance.

## Minimum remaining owner actions
1. **Designate the admission issuer:** a person or service, with its own Windows account. It runs the admission package and sends its enrollment.
2. **Designate the independent acceptance reviewer:** not Claude, not you, on their own account or host. They run the acceptance package and send their enrollment.
3. **Provide the real Sandbox mission terms** (obligation, merchant, amount, currency, deadline, quote), so you can sign the mission, control and bridge policy with your control key.
4. **Identify the actual Core-observer, CAGE and VEIP authorities and executions** for this mission (their public keys go into the policy). Without them no eligible issuer request can exist.
5. When enrollments 1 and 2 arrive: run `build-trust` and `confirm-trust` yourself and keep the fingerprint.
6. **Optional: re-establish the latency host conditions** (AC, cool, quiet) before relying on the ≤ 5 ms claim, or treat it as unverified in the current host state.
