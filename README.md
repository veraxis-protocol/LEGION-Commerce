# LEGION-Commerce 

External Windows-hosted PayPal Sandbox Orders v2 adapter, isolated from LEGION Core.
No Core files or proof sources are copied, modified or relicensed here.

**Current release decision: BLOCKED / NO-GO to A05.** Local mechanism checks and
Windows OAuth are separate evidence layers. Native payment acceptance requires
genuine A03 acceptance, scoped authority, authenticated buyer approval and fresh
provider readback. The adapter does not issue its own authority or return mission DONE.

## Run on Windows

Python 3.14 x64 was used. From this checkout in Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe tools\run_checks.py
.\.venv\Scripts\python.exe -m commerce_adapter oauth-check
.\.venv\Scripts\python.exe -m commerce_adapter preflight
```

Credentials stay at `%LOCALAPPDATA%\Veraxis\LEGION-Commerce\paypal_client_id.txt`
and `paypal_client_secret.dpapi`. DPAPI is unwrapped through Windows CryptUnprotectData
inside the server process. Whitespace and PowerShell text encodings are handled.
No credential import, WSL transfer, environment-token export or browser credential
delivery is supported. OAuth tokens remain in process memory and renew before expiry.
TLS uses the fixed Sandbox origin, system certificate validation, no redirects and
no environment proxy. Network failures have bounded timeouts and no implicit POST retry.
Administrators and SYSTEM remain trusted OS principals; this is not a boundary
against malicious code running as the same Windows user or an administrator.

## Operational authority contract

An independently provisioned, operator-owned directory
`%LOCALAPPDATA%\Veraxis\LEGION-Commerce\a04` must contain:

- `trust.json`: base64 raw Ed25519 public keys named `control`, `admission`,
  `acceptance`. Obtain these from the actual authority/acceptance owners. Do not use test keys.
- `a03-acceptance.json`: signed acceptance envelope plus `a03-evidence/` containing
  the exact hash-bound A03 records. Its acceptance must cover this adapter's exact
  runtime source hashes, source pins, schema and A03 T01–T13, and be current.
- `current-mission.json`, `current-permit.json`, `current-control.json`: genuine,
  signed original mission, one-time permit and current epoch/revocation control.

See [the interface contract](docs/ADMISSION_INTERFACE.md). This is an integration
contract for the A03 owner, not an implementation of A03 or a claim that signatures
prove Lean/Python source correspondence. There is no production key generator,
`--force`, mock live mode, fallback T, or bypass command. Test-only signatures are
created in temporary directories and used with FakePayPal only.

## Create, buyer approval, capture and readback

Proceed only after actual A03 acceptance and signed inputs are installed. The
current user instruction supplies work-order authorization; documentary freeze alone
does not substitute for current financial admission.

```powershell
$a04Runtime = Join-Path $env:LOCALAPPDATA 'Veraxis\LEGION-Commerce\a04'
.\.venv\Scripts\python.exe -m commerce_adapter preflight
.\.venv\Scripts\python.exe -m commerce_adapter apply-control --control "$a04Runtime\current-control.json"
.\.venv\Scripts\python.exe -m commerce_adapter create --mission "$a04Runtime\current-mission.json" --permit "$a04Runtime\current-permit.json"
```

`create` returns an effect ID and a Sandbox approval URL when PayPal supplies one.
Start the listener using that exact effect ID, then open the returned Sandbox URL
and authenticate as the Sandbox Personal buyer. Do not enter buyer passwords into
terminal commands or chat. The default callback is `http://127.0.0.1:8765/return`.
The permit must remain current during checkout (maximum permit lifetime 600 seconds).

```powershell
.\.venv\Scripts\python.exe -m commerce_adapter serve-approval --effect <effect-id>
```

The callback checks state, order ID and loopback Host, then performs authenticated
server GET. A callback's PayerID or return navigation never proves approval.
Only a current permit, held reservation, exact order match and provider APPROVED
with payer identity permit a capture POST. Alternatively the operator can run
`capture --effect <effect-id>` after checkout; the same checks apply.
The POST result alone cannot confirm payment: separate order and capture GETs must
match IDs, payee, effect, currency, amount and final COMPLETED statuses.

Run independent readback in a **new process**:

```powershell
.\.venv\Scripts\python.exe -m commerce_adapter independent-readback --effect <effect-id>
```

This reader has no POST capability, opens SQLite read-only, verifies original
signatures and fetches fresh provider data; it ignores stored receipt claims.
It currently uses the same merchant credential. A separately provisioned reader
credential and an institutionally independent A05 Judge require their own integration
and acceptance. Payment confirmation never proves the three services were delivered.
The minimum bundle is one merchant payment with synthetic fulfilment clearly labelled.

## Durability and failure behavior

- SQLite WAL, FULL synchronization and `BEGIN IMMEDIATE` serialize the single-host
  ledger. This is not a distributed ledger or a multi-host payment service.
- Integer cents govern both each signed mission cap and a gateway-wide $500 cap.
  Every held reservation or confirmed capture consumes its amount. Refund and
  decline do not automatically grant fresh spending authority.
- Unique `(mission, obligation, contract_version, slot)`, effect, permit and
  provider identities prevent a second financial effect. A retry of an identical
  admitted request is explicitly an idempotent lookup, not a new permit use.
- Distinct durable UUIDs identify create and capture. A pre-dispatch marker is
  committed before each POST. A crash before or after dispatch therefore leaves
  conservative uncertainty, never a free budget slot or a fresh-ID retry.
- Revocations and epoch controls are signed, monotonic and applied in the same
  database serialization order as commands. A revocation committed before dispatch
  prevents it; an already dispatched payment cannot be undone by local revocation.
- Timeout/5xx/lost reply retains the reservation. `reconcile --effect <effect-id>`
  only reads. Even APPROVED after an uncertain capture does not prove no capture
  happened; automatic recapture remains prohibited.
- Lost create reply without an order ID requires merchant-dashboard lookup.
  `attach-order --effect <effect-id> --order <candidate-order-id>` performs direct
  GET and exact immutable field joins before binding it. If the order cannot be
  identified, leave the reservation held and the effect pending.
- Readback contradictions persist a global safety stop. Further admissions and
  dispatches fail closed while read-only investigation remains available. No
  command silently clears this stop or deletes failed evidence.
- Cancelling checkout retains its reservation. There is no automatic refund,
  authorization void, budget reset or repair of an unknown provider effect.

The lifecycle includes RESERVED, CREATE_IN_FLIGHT, APPROVAL_PENDING, APPROVED,
CAPTURE_IN_FLIGHT, CAPTURE_CONFIRMED, RECONCILIATION_PENDING and FAILED_HELD.
FAILED_HELD is the conservative no-redelegation policy for a corroborated decline.

## Evidence and acceptance

Frozen A03/A04/A05 documents are preserved byte-for-byte in `design/` with hashes
from the master v1.2 freeze. `design/TEST_MATRIX.json` preserves all 14 A04 cases.
Each check run creates a new evidence directory with a pre-run source manifest,
frozen inputs, matrix, events, expected/actual outcomes and hashes. Failed runs
remain present. A hash manifest provides integrity checking, not external notarization.

`tools/collect_native_preflight.py` records redacted OAuth, credential ACL metadata,
missing prerequisites and a zero-Orders-call disposition. It never copies credentials.
`tools/run_checks.py` uses synthetic signatures/provider responses, real SQLite
transactions and real Windows process crashes; its results are MECHANISM_ONLY.
Native T01/T05/T06/T07 require the real Sandbox layer where supported. Mock fault
edges do not earn full A04 PASS. A04 cannot confer A05 mission/competitive acceptance.

See [the execution report](docs/WO_COM_A04_EXECUTION_REPORT.md) and retained `evidence/`.

## Official protocol references

[Orders v2 create](https://developer.paypal.com/api/orders/v2/orders-create),
[capture](https://developer.paypal.com/api/orders/v2/orders-capture),
[show order](https://developer.paypal.com/api/orders/v2/orders-get), and
[PayPal idempotency](https://developer.paypal.com/api/rest/reference/idempotency/).
Local deduplication outlives provider request-ID retention. No claim of production
fraud resistance, GAIA2 Pass@1 improvement, formal proof of this Python adapter or
competitive superiority is made by these component tests.

No new distribution licence has been selected in this work order. No upstream
Core material is relicensed; A06 distribution/licensing acceptance remains separate.
