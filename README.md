# LEGION-Commerce

PayPal payments for LEGION collectives. This repository contains:

- a Windows-hosted **PayPal Sandbox Orders v2 adapter** (`commerce_adapter/`) that executes payments only under signed authority;
- an **admission bridge** that obtains an independent admission decision for each exact payment action;
- the **payment orchestration** used by [LEGION Deal Rescue](https://github.com/veraxis-protocol/LEGION-Deal-Rescue).

No LEGION Core file or proof source is copied, modified or relicensed here.

## Status

- **Five PayPal Sandbox payments completed** for the LEGION Deal Rescue scenario: 5 buyers × $99.00 = $495.00 in Sandbox test money.
  - Each payment was approved by its own Sandbox buyer and captured once.
  - Each was confirmed by an independent readback in a separate process.
- **Payment code:** commit `0bb385f99915a429d0c03fa8f91a13de4e8a7636`, branch `release/sandbox-payments-2026-11`.
- **Evidence and hashes:** [PAYPAL_STATUS.md](https://github.com/veraxis-protocol/LEGION-Deal-Rescue/blob/main/PAYPAL_STATUS.md) and [PAYMENT_RUNTIME.md](https://github.com/veraxis-protocol/LEGION-Deal-Rescue/blob/main/docs/PAYMENT_RUNTIME.md) in LEGION Deal Rescue.
- **Production is not enabled.** It additionally requires an independent acceptance review of the adapter, which has not been signed.

This branch holds the adapter's first release. Unlike the payment commit above, it does not yet journal the admission attestation before each POST.

## Run on Windows

Python 3.14 x64. In Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .
.\.venv\Scripts\python.exe -m commerce_adapter oauth-check
.\.venv\Scripts\python.exe -m commerce_adapter preflight
```

**Credentials.**
- Stored in `%LOCALAPPDATA%\Veraxis\LEGION-Commerce\` (`paypal_client_id.txt`, `paypal_client_secret.dpapi`).
- Unwrapped with Windows DPAPI inside the server process; OAuth tokens stay in memory.
- Never imported from the environment, WSL or a browser.

**Network.**
- TLS only to the fixed Sandbox origin, with system certificate validation.
- No redirects and no environment proxy.
- Bounded timeouts and no implicit POST retry.

**Trust boundary.** Administrators and SYSTEM remain trusted OS principals. This is not a boundary against malicious code running as the same Windows user.

## Authority

An operator-provisioned directory `%LOCALAPPDATA%\Veraxis\LEGION-Commerce\a04` holds:

- `trust.json`: the Ed25519 public keys named `control`, `admission` and `acceptance`, obtained from their actual holders. Test keys are never valid.
- `a03-acceptance.json`: the signed independent acceptance of this adapter, together with its hash-bound review records in `a03-evidence/`. It must cover the adapter's exact source hashes and source pins, and it must be current.
- `current-mission.json`, `current-permit.json`, `current-control.json`: the signed payment authorization, a one-time permit, and the current epoch and revocation state.

The envelope formats are specified in [docs/ADMISSION_INTERFACE.md](docs/ADMISSION_INTERFACE.md). There is no key generator, no `--force`, no mock live mode and no bypass command. Test signatures exist only in temporary test directories, where they are used with the fake provider.

## Create, buyer approval, capture and readback

```powershell
$auth = Join-Path $env:LOCALAPPDATA 'Veraxis\LEGION-Commerce\a04'
.\.venv\Scripts\python.exe -m commerce_adapter preflight
.\.venv\Scripts\python.exe -m commerce_adapter apply-control --control "$auth\current-control.json"
.\.venv\Scripts\python.exe -m commerce_adapter create --mission "$auth\current-mission.json" --permit "$auth\current-permit.json"
.\.venv\Scripts\python.exe -m commerce_adapter serve-approval --effect <effect-id>
```

1. `create` returns an effect ID and the PayPal approval URL.
2. The Sandbox Personal buyer approves on PayPal's page.
3. The callback (`http://127.0.0.1:8765/return`) checks the state, order ID and host, then performs an authenticated server GET. A return navigation alone never counts as approval.
4. A capture is sent only when all of these hold:
   - the permit is current;
   - a reservation is held;
   - the order matches exactly;
   - PayPal reports `APPROVED` with a payer identity.
5. The order and capture GETs must agree on IDs, payee, effect, currency, amount and `COMPLETED`.

Run the independent readback in a **new process**:

```powershell
.\.venv\Scripts\python.exe -m commerce_adapter independent-readback --effect <effect-id>
```

The readback:
- cannot POST;
- opens the ledger read-only;
- verifies the original signatures;
- fetches fresh data from PayPal, ignoring any stored receipts.

A confirmed payment never proves that the purchased goods or services were delivered.

## Durability and failure behaviour

- **Single-host ledger.** SQLite WAL, FULL synchronization and `BEGIN IMMEDIATE` serialize writes.
- **Budget.** Integer cents govern each authorization's cap and a gateway-wide $500 cap. Refunds and declines do not create new spending authority.
- **No second effect.** Unique (authorization, obligation, contract version, slot), effect, permit and provider identities prevent a second financial effect. An identical retry is an idempotent lookup.
- **Before every POST.** Durable request IDs are assigned and a pre-dispatch marker is committed. A crash therefore leaves conservative uncertainty, never a free budget slot.
- **Revocations and epochs.** These are signed, monotonic and serialized together with commands.
- **Uncertain outcomes.** A timeout, a 5xx error or a lost reply keeps the reservation. `reconcile --effect <id>` only reads, and there is never an automatic recapture.
- **Lost create reply.** It is resolved with `attach-order`, which does a direct GET and exact field joins.
- **Contradictions.** A contradiction in a readback sets a global safety stop that is never cleared silently.

Lifecycle states: RESERVED, CREATE_IN_FLIGHT, APPROVAL_PENDING, APPROVED, CAPTURE_IN_FLIGHT, CAPTURE_CONFIRMED,
RECONCILIATION_PENDING, FAILED_HELD.

## Evidence

- `design/` keeps the frozen design documents byte-for-byte, with their hashes.
- Each test run writes a new evidence directory containing the source manifest, inputs, expected and actual outcomes, and hashes. Failed runs are kept.
- Tests use synthetic signatures, a fake PayPal provider, real SQLite transactions and real Windows process crashes.

## References

PayPal [Orders v2 create](https://developer.paypal.com/api/orders/v2/orders-create),
[capture](https://developer.paypal.com/api/orders/v2/orders-capture),
[show order](https://developer.paypal.com/api/orders/v2/orders-get),
[idempotency](https://developer.paypal.com/api/rest/reference/idempotency/).

## License

- This repository has no license file; all rights are reserved by Veraxis.
- The payment adapter (`commerce_adapter/`, at the payment commit above) is also published under Apache-2.0 in [LEGION Deal Rescue](https://github.com/veraxis-protocol/LEGION-Deal-Rescue).

## Related repositories

[LEGION Deal Rescue](https://github.com/veraxis-protocol/LEGION-Deal-Rescue) ·
[LEGION](https://github.com/veraxis-protocol/LEGION) ·
[LEGION-Formal-Core](https://github.com/veraxis-protocol/LEGION-Formal-Core) ·
[LEGION-Federation](https://github.com/veraxis-protocol/LEGION-Federation) ·
[LEGION-Marketplace](https://github.com/veraxis-protocol/LEGION-Marketplace)
