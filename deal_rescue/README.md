# LEGION Deal Rescue (WO-PAYPAL-LEGION-004)

*Turn customer requests you cannot fulfill into verified business opportunities.*
*Find partners. Agree on terms. Verify delivery. Get paid with PayPal when authorized.*

This is a merchant-facing layer over an **existing, retained seven-owner LEGION run**. It does not reimplement
Marketplace, Federation, Core or the Judge. It reads one run directory. The run is produced by
`legion_marketplace.e2e`, with Marketplace 906ec0f, Federation 9d2a32a and Core d083c44. From that run it renders five screens:

1. Unfulfilled demand
2. Opportunity
3. Terms
4. Verified execution, with replay
5. Outcome & PayPal

It also renders a Deal Dossier (with JSON download) and an Execution Passport.

| Piece | File | What it does |
|---|---|---|
| Dossier builder | `dossier.py` | Reads the run's journals. Every figure becomes `{value, ref → file#seq + JSON path + record hash}`. |
| Replay server | `server.py` | Read-only, stdlib only, `127.0.0.1`. Has no write path and makes no model or PayPal calls. |
| UI | `web/index.html` | Single file, no build. Every number is clickable and opens the exact retained record. |
| Auditor | `audit.py` | `UI_EVIDENCE_AUDITOR` and `ECONOMIC_ORACLE`: provenance at 100%, mission status, claim checks, the N01–N15 matrix. It is **not** the Judge. |
| Gate zero | `tools/gate_zero.py` | Pins, historic integrity, license gate, and a **read-only** PayPal authority preflight. |
| Comparators | `tools/counterfactuals.py` | A0/A1/A2/A3 from retained evidence, plus the fresh WO-004 attempt. |

## Run (Windows PowerShell, replay mode — no secrets needed)
```
python -B -m deal_rescue.server --evidence <evidence_dir> --port 8795   # then open http://127.0.0.1:8795/
```
`<evidence_dir>` must contain:
- `runs/<run>/`: the retained seven-owner run;
- `dossier/<run_id>/DEAL_DOSSIER.json`, built by `dossier.py`;
- optionally the gate and audit JSON files.

`run_deal_rescue.ps1` rebuilds everything from the pinned sources:
```
.\deal_rescue\run_deal_rescue.ps1 -Evidence .\wo004_evidence                 # replay only
.\deal_rescue\run_deal_rescue.ps1 -Evidence .\wo004_evidence -FreshRun -NimKeyFile C:\path\nvapi.txt
```
`-FreshRun` runs a NEW seven-owner synthetic episode with the frozen Judge. It needs the PolyForm-NC LEGION Core,
Federation and Marketplace checked out at the pins, plus an NVIDIA NIM key.

## What is and is not claimed
- **SIMULATED**: the five requests, manufacture, qualification, shipment and buyer ledger payments.
  The price shock is an **INJECTED / TEST** environment event.
- **Judge**: `PASS_COMPLETED` with synthetic scope only.
- **PayPal**: no PayPal effect is produced here.
  - The historic replay shows the 2026-11-07 LMF-007 Sandbox state: 4 orders created, 0 approved, 0 captured, 4 holds open.
  - That state is shown only because that mission was bound to this exact contract hash.
  - A fresh run shows `NOT_AUTHORIZED`.
- **Economics**: $495 is charged against $495 of costs, so the contribution is $0 before any overhead.
  The product says **NOT YET PROFITABLE / NOT ECONOMICALLY VALIDATED**.
- **Licensing**: the runnable stack is **PolyForm Noncommercial 1.0.0**, which is not OSI open source.
  This folder adds no license. The competition OSS condition is NOT MET until the owner decides.
