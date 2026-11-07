# Provisioning packages for the undesignated roles (WO-COM-AUTHORITY-005)

The admission issuer and the independent acceptance reviewer have **not been designated**, so neither role is operational. This document is the package each designee receives. Nothing in it is secret.

## Admission issuer package
**Who:** the separately authorized LEGION admission issuer, named by the Veraxis owner. It must not be the acceptance reviewer. It should be a distinct Windows account (a service or person), preferably not the operator's interactive account.

1. Clone `https://github.com/veraxis-protocol/LEGION-Commerce`, branch `claude/wo-com-authority-005`, and create the Python 3.14 venv per the repository README.
2. In an interactive console **under the issuer's own Windows account**:
   `python -m commerce_authority init-role --role admission --custodian "<issuer name>"`
   Choose a passphrase known only to the issuer's custodian, then run `python -m commerce_authority selftest --role admission`.
3. Send the operator the public file `%LOCALAPPDATA%\Veraxis\LEGION-Commerce\authority\enrollments\admission-<key_id>.json`.
4. Operation: permits are signed only with
   `python -m commerce_authority sign-permit --permit <unsigned permit> --trust <trust.json> --trust-sha256 <operator fingerprint> --mission <signed mission> --control <signed control> --bundle <signed source bundle> --out <signed permit>`.
   This re-executes the qualified BCh assessment and refuses unless it yields `ALLOW_ISSUER_REQUEST_ONLY` with Lean verdict T, bound by hash to the permit. The generic `sign` command refuses `commerce.permit.v1`.

## Acceptance reviewer package
**Who:** an independently designated technical reviewer. Not Claude, not the operator, not the developer of the bridge. Use the reviewer's own Windows account, ideally on the reviewer's own host. `build-trust` rejects an acceptance key whose custodian name or account/host matches the control or admission custodian.

1. Same clone and venv as above. In an interactive console under the reviewer's own account:
   `python -m commerce_authority init-role --role acceptance --custodian "<reviewer name>"`, then `selftest --role acceptance`.
2. Send the operator the public file `authority\enrollments\acceptance-<key_id>.json`.
3. Review before any signature. The input is `REVIEW_REQUEST_v4_UNSIGNED.json`, which binds the exact BCh bridge and adapter source hashes, the qualified Core `d9d38cd`, the decider `99cda684…`, the profile, the frozen interface and the evidence sets. Review in particular:
   - the qualified Formal Core compiler and axiom transcripts (`FORMAL_CORE_001`);
   - the A01/A02 upstream evidence (not present on this host);
   - the actual Core/CAGE/VEIP source provenance and key custody;
   - process and key custody (`docs/AUTHORITY_CUSTODY.md`);
   - the latency disposition: GO under unthrottled host conditions only, and the current recheck is above 5 ms (see the AUTHORITY-005 report).
4. Only after your own review, sign `commerce.a03.acceptance.v1`, the A01/A02 `commerce.upstream.acceptance.v1` and the `commerce.bridge.installation.v1` manifest with `python -m commerce_authority sign --role acceptance --domain <domain> --payload <file> --out <file>`.

## Operator steps after both enrollments arrive
1. `python -m commerce_authority build-trust --control <control enrollment> --admission <admission enrollment> --acceptance <acceptance enrollment> --out trust.json`
2. `python -m commerce_authority confirm-trust --trust trust.json --out trust-confirmation.json`: type the fingerprint prefix shown, then your control passphrase.
3. Keep the full SHA-256 in your own records. It is the `--trust-sha256` for every `commerce_bridge` command.

Current control key (enrolled 2026-11-06 by the Veraxis Owner): key id `fdaab34c5defcf9805c32bb5`, public key `uHY+AZAKcPqxj43UmXfLNOYJ6t7uNPnNwuMYkKXulh0=`.
