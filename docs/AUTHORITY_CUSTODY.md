# LEGION Commerce signing authority: roles, custody and procedures (WO-COM-AUTHORITY-005)

Code: `commerce_authority/` (`roles.py`, `custody.py`, `authority.py`, CLI `python -m commerce_authority`).
The frozen A03/A04 interface is unchanged: Ed25519 raw public keys (base64, 32 bytes) in `trust.json`
under exactly `control`, `admission` and `acceptance`; envelopes `{payload, signature}` over `domain || 0x00 || canonical(payload)`.

## 1. Roles

| Role | Owner | May sign (enforced by the signer AND by A04 verification) |
|---|---|---|
| control | Veraxis operator | `commerce.mission.v1`, `commerce.control.v1` (epochs, revocations), `commerce.bridge.policy.v1`, trust-anchor confirmation, key-revocation records |
| admission | separately authorized LEGION admission issuer | `commerce.permit.v1`, only through `sign-permit`, which re-executes the qualified BCh assessment and requires an `ALLOW_ISSUER_REQUEST_ONLY` assessment with Lean verdict T, bound by hash to the permit |
| acceptance | independently designated technical reviewer, not the developer and not the operator | `commerce.a03.acceptance.v1`, `commerce.upstream.acceptance.v1`, `commerce.bridge.installation.v1` |

Every role also signs its own enrollment (proof of possession) and may run a harmless self-test (`commerce.authority.selftest.v1`, which no admission check accepts). Any other domain is refused (`DOMAIN_NOT_PERMITTED_FOR_ROLE`).

## 2. Custody mechanism (per key)

1. Ed25519 key generated in-process by `cryptography`. The private key is sealed immediately with a **custodian passphrase**: scrypt (n=2^15, r=8, p=1) feeds ChaCha20-Poly1305, with role and key id as associated data.
2. The sealed record is protected with **Windows DPAPI** (CurrentUser scope, role-bound entropy) and stored at `%LOCALAPPDATA%\Veraxis\LEGION-Commerce\authority\<role>\<key_id>.key.dpapi`. The store directory's ACL is restricted to the owning account, SYSTEM and Administrators, with inheritance removed.
3. The passphrase is read only from a **real interactive console** (`GetConsoleMode` on stdin). It is refused from arguments, environment, files, pipes and the NUL device; this is tested (`cli_requires_interactive_console`). It is never stored, logged or sent through chat.
4. Writing private key material under Dropbox, OneDrive, GitHub, `Documents\ChatGPT` or any git work tree is refused. A04 packages containing `.key`, `.pem`, `.pfx`, `.p12` or `.dpapi` files are rejected.
5. Public metadata only: `authority\registry.json` (role, key id, public key, custodian, account SID, host, status) and `authority\enrollments\*.json`.

**What this does and does not establish.** DPAPI binds a key to one Windows account. Any process running as that account, including Claude's agent on this host, can read the sealed file. What it cannot do is use the key without the passphrase, which only the custodian types into a console. Two roles under the same Windows account and the same person are **not** independent custody. `build-trust` refuses an acceptance key whose custodian name or account/host matches the control or admission custodian. The admission issuer and acceptance reviewer must generate their keys under their **own** Windows accounts, preferably on their own hosts.

## 3. Provisioning

| Step | Who | Command (interactive console) |
|---|---|---|
| Generate and enroll the control key | Veraxis operator (control custodian) | `python -m commerce_authority init-role --role control --custodian "<operator name>"` |
| Generate and enroll the admission key | designated admission issuer, own account | `python -m commerce_authority init-role --role admission --custodian "<issuer identity>"` |
| Generate and enroll the acceptance key | designated independent reviewer, own account/host | `python -m commerce_authority init-role --role acceptance --custodian "<reviewer name>"` |
| Send the public enrollment file (`authority\enrollments\<role>-<kid>.json`) to the operator | admission issuer, reviewer | any channel; the file is public and verified by proof of possession |
| Build `trust.json` | operator | `python -m commerce_authority build-trust --control … --admission … --acceptance … --out trust.json` |
| Confirm the anchor: type the fingerprint shown, sign with the control key | operator | `python -m commerce_authority confirm-trust --trust trust.json --out trust-confirmation.json` |
| Supply `--trust-sha256` to every `commerce_bridge` command | operator | from their own confirmation, never from a package |

Each `init-role` prints a redacted provisioning receipt: role, key id, public key, custodian, account SID, host and enrollment hash.

## 4. Backup and recovery
- **Backup:** `export-backup --role R --to <offline path>` writes the key encrypted under the custodian passphrase only (no DPAPI), so it can be restored on a rebuilt Windows account. Cloud and repository locations are refused. Store it offline (removable media in a safe). It is useless without the passphrase.
- **Recovery:** restore the backup file and re-seal it under DPAPI on the new account (planned `import-backup`, not implemented in this WO; until it exists, recovery means rotation, below). Losing both the backup and the passphrase means the role must rotate.

## 5. Rotation
1. The custodian runs `init-role` again. The new enrollment supersedes the previous ACTIVE key, which becomes RETIRED.
2. The operator runs `build-trust` with the new enrollment, then `confirm-trust`.
3. Every artifact signed by the old key is re-signed by its role. The package is rebuilt and the installation manifest re-signed by acceptance.
4. Install the new package. Packages under the old anchor fail (`PACKAGE_TRUST_SUBSTITUTION`, `OPERATOR_TRUST_FINGERPRINT`); permits signed by the old key fail under the new anchor (`INVALID_SIGNATURE`). Both are tested.

## 6. Revocation and suspected compromise
1. **Immediately:** the operator signs `revoke --key-id K --reason "…"` with the control key. The key is refused for any further signing (`KEY_NOT_ACTIVE`) and excluded from any new anchor (`REVOKED_KEY_IN_TRUST`).
2. **Stop dispatch:** the operator signs and applies a new `commerce.control.v1` that revokes the outstanding permits or deactivates the mission (`commerce_bridge apply-control`). The SQLite commit is the local revocation boundary and needs no permission from the compromised key.
3. **Rotate** the role (section 5). A04 has no key revocation list: removing the key from the installed anchor is what makes it inert for verification.
4. **If the control key itself is compromised:** create the new control key, then build and confirm the new anchor out of band. Signatures by the old control key stop verifying once the new anchor is installed. Until then, the compromised key could sign a control record, so the installation must be stopped (remove `%LOCALAPPDATA%\Veraxis\LEGION-Commerce\a04` from service) before rotation.
5. Record the incident: the revocation record, the old and new anchor fingerprints and the reason.

## 7. Prohibited
- No test, fixture or conformance key in an operational anchor: `build-trust` requires `environment = sandbox-operational` enrollments.
- No hardcoded key material, no automatic or developer acceptance, and no acceptance signature by anyone who built the bridge.
- No Core, CAGE or VEIP evidence produced by the signing infrastructure. Those are separate source authorities whose keys appear in the control-signed bridge policy.
