# WO-COM-E2E-001 — LEGION Commerce end-to-end integration: report

**Overall: NO-GO.**
- The native PayPal Sandbox transaction was not executed, because three things are missing:
  - a genuine independent A03 acceptance;
  - current signed production permits;
  - the owner's buyer approval.
- The rest of the chain is real and was run end to end: Channel3 offers, NIM agents, qualified Lean admission, the A04 Gateway, a separate-process readback and the Judge. The payment leg is the synthetic provider, labelled `MECHANISM_ONLY`.
- AG Studio AI interactions run through the LEGION LLM backend, but the NIM model completed no task (F).

## Environment and versions

| Item | Value |
|---|---|
| Branch | `claude/wo-com-e2e-001`, from `bf72f06` |
| Host | Windows 11; Python 3.14.7 (`.venv`); Node 24.19.0; npm 11.17.0 |
| AG Studio | `ag-studio@3.1.0` and `ag-studio-react@3.1.0` (exact); React 19.1.0; Vite 6.3.5 |
| AG licence | Checked with AG Studio's own `AgStudioLicenseManager`: valid, **STUDIO-PRO-AI**, trial, expires 22 December 2026, version 3. The `AgStudioAiModule` requires PRO-AI, which is satisfied. Output: `evidence/AG_STUDIO_LICENSE_CHECK.json`, key material redacted. |
| LLM | `nvidia/nemotron-3.5-lightning-30b-a3b` via NVIDIA NIM (`commerce_demo.nim`, `enable_thinking=false`) |
| Formal Core | Qualified Core `d9d38cd`; `legion_decide` sha256 `99cda684…` (reproduced bit-exactly from source, R2 `formal_closure/pin_repro`) |
| Channel3 | `api.trychannel3.com` `POST /v1/search` and `GET /v1/products/{id}` |
| PayPal Sandbox credentials | Present: `paypal_client_id.txt` (84 bytes) and `paypal_client_secret.dpapi` (782 bytes, DPAPI-sealed). **Not read** in this WO; zero PayPal network calls. No A04 installation package exists. |

**Credentials:**
- The Channel3, NIM and AG keys are read only by the backend, from the Desktop files, at call time.
- They are not in the bundle (`web/dist`), the source, git or any log; a scan found 0 hits.
- The AG licence key is a client-side licence by design. It is served at runtime from `127.0.0.1` only.

## Architecture

```
user intent -> agent:planner-1..3 (NIM) -> Channel3 search (backend) -> offers -> agent:chair (NIM) -> live re-quote
  -> changed offer -> recheck -> PLAN_REJECTED -> agent:chair replan -> live re-quote of the final offer
  -> SandboxDemoAdmission: qualified Core ACT + Lean decider T for the exact action (issue_permit)
  -> A04 Gateway reserve/create/capture; at EVERY gate: live Channel3 price gate + fresh Core observation + fresh Lean T
  -> payment provider (FakePayPal, MECHANISM_ONLY) -> separate-process readback -> separate-process Judge
AG Studio dashboard (React) -> backend /api/run (data), /api/llm/turn (AgLlmAdapter -> NIM), /api/channel3/search
```

| Component | File |
|---|---|
| Orchestrator | `e2e_run.py` |
| Channel3 client | `channel3.py` |
| Readback | `readback.py` |
| Judge | `judge.py` |
| Transaction negatives | `negatives.py` |
| Backend | `server.py` |
| Dashboard | `web/src/main.jsx`, `web/src/llmAdapter.js`, `web/src/dashboardState.json` |
| Licence check | `tools/verify_ag_license.cjs` |

Authority for the mechanism run:
- The owner, admission and Core-observer keys are **ephemeral TEST keys** created in memory for each run and never persisted.
- They are not the owner's Control Key, and no A03 was self-signed.

## Runs (all preserved in `runs/`)

| Run | Outcome | Cause |
|---|---|---|
| `run-20261108T160036Z` | Aborted: `CHAIR_SELECTION_INVALID` | The chair's guided JSON stalled in whitespace until the token limit (`finish_reason=length`). |
| `run-20261108T160158Z` | Aborted: same | Same, after a retry with a bounded rationale. |
| `run-20261108T160330Z` | Aborted | Orchestrator defect: the `kind` field collided with `Log.emit`. |
| `run-20261108T160349Z` | Aborted: `CHAIR_SELECTION_INVALID` | The model dropped `#domain` from composite offer keys. The fix was short offer IDs (O1…O15) mapped in code. |
| `run-20261108T160415Z` | Pipeline completed; Judge **FAIL** (J06) | Judge defect: a stale field name. Its verdict file is kept unchanged. |
| **`run-20261108T160504Z`** | **Judge PASS_MECHANISM_ONLY, 11/11** | The reference run, below. |

## Reference run `run-20261108T160504Z`

- **Intent.** Over-ear ANC wireless headphones, new, in stock, USD, US seller, at most $500.
- **Proposals.** Three model-driven NIM proposals:
  - "over-ear active noise cancelling wireless headphones";
  - "over ear active noise cancelling wireless headphones";
  - "Sony WH-1000XM5 noise cancelling headphones".
- **Offers.** 12 raw Channel3 responses were preserved with sha256. They yielded **15 eligible genuine offers** from 12 sellers (InStock, USD, ≤ $500), each traced by the Judge to the raw response bytes.
- **Initial selection.** The chair picked `s6UDa3X#adorama.com`, Bose QuietComfort, $368.00.
- **Changed offer.** The live re-quote returned an **unchanged** price, so the preregistered disruption injector published a **labelled INJECTED** change: $368 → $550 (110% of the cap). The recheck gave `EXCEEDS_CAP`, then `PLAN_REJECTED`.
- **Replan.** The chair replanned to `0hn75zJ#microcenter.com`, *QuietComfort Bluetooth Wireless ANC Headphones*. The live re-quote was $169.00, InStock, so the final line is 16,900 cents.
- **Formal admission.**
  - Qualified Core directive ACT, Lean verdict **T**, `action_digest` bound to the exact intent, permit `E2E-001-20261108T160504Z-PERMIT-1`.
  - Seven live Channel3 price gates (reserve, create and capture checks), all equal to the plan price.
  - A fresh Core observation and Lean T at every gate.
- **Payment (MECHANISM_ONLY).**
  - One effect reached `CAPTURE_CONFIRMED` with exactly 2 provider POSTs: create and capture.
  - A duplicate `capture()` in the same run dispatched nothing.
  - The separate-process readback matches the ledger (amount, order↔capture link, COMPLETED).
- **Judge J01–J11 all pass:**
  - chain intact;
  - signatures valid;
  - raw responses intact;
  - offers traced;
  - model calls recorded;
  - changed offer rejected and replanned;
  - final matches the live re-quote within the cap;
  - Lean T bound to the action;
  - one payment, no duplicate;
  - readback matches;
  - price gate and Lean before the effect.
- **Judge negatives.** 9/9 tampered copies were rejected: altered log, altered offer price, altered raw response, no replan, altered amount, Lean not T, duplicate capture, readback mismatch, forged permit.

## Transaction negatives

The negatives ran on the real admission path: qualified Core and Lean, the A04 Gateway, `FakePayPal`. Run 1 is preserved as `NEGATIVES_run1_HARNESS_DEFECT.json`: N2, N5 and N6 expected a returned state, but the Gateway correctly raised the denial. Run 2 asserts the exact denial code and zero POSTs; no system code was changed. **11/11 PASS:**

| Case | Result | Forbidden POSTs |
|---|---|---|
| P1 positive control | CAPTURE_CONFIRMED, Lean T | 2 (create, capture) |
| N1 expired permit | `EXPIRED_PERMIT` | 0 |
| N2 permit revoked after reservation | `REVOKED_PERMIT` | 0 |
| N3 revoked actor | `REVOKED_ACTOR` | 0 |
| N4 mission deactivated by owner control | `CONTROL_INACTIVE` | 0 |
| N5 stale price before create (+$15.00) | `STALE_PRICE_OR_OFFER_CHANGED` | 0 |
| N6 stale price before capture (−$7.00) | `STALE_PRICE_OR_OFFER_CHANGED` | 0 captures |
| N7 permit amount altered after signing | `INVALID_SIGNATURE` | 0 |
| N8 amount over the $500 cap | refused at issue by Core/Lean (`CORE_TOOL_PRECONDITIONS`) | none (no permit) |
| N9 amount altered at the provider | `RECONCILIATION_PENDING` + safety stop | 0 captures |
| N10 duplicate capture (same instance and a second Gateway instance) | 1 capture total | — |

## AG Studio dashboard and AI

**Dashboard.**
- `AgStudioProvider` is configured with the local licence and `[AgStudioAiModule]`.
- `AgStudio` is fed five data sources from the run: offers, events, price_gates, checks, summary.
- The initial report holds five widgets: a bar chart of lowest price by seller, an offers grid, a pie of checks by result, an events grid and a checks grid. They were built with AG Studio's own `addWidget` and `configureWidget` tools.
- AG's own health check reports the bar chart rendering 12 seller categories with variance, and the pie rendering one category (all 31 checks PASS).
- The three grids showed "Loading…" in the hidden in-app browser pane. Their row rendering was **not visually confirmed**.

**AI.**
- `createAiHarness` runs AG's built-in agents (lead, planning, data, page, widget) against an `AgLlmAdapter` that calls the backend `/api/llm/turn`, which calls NIM. Tool calling works.
- Over three sessions there were 216 journaled turns (`ai_turns/`).
- Session 1: `delegate_to` failed, because the model sent `type` as a string.
  - Fix: a format hint, plus a journaled normalisation of `"type":"x"` to `{"id":"x"}`.
- Session 2: delegation worked, but `create_plan` failed AG's schema validation in a loop.
- Session 3: a data question led to 31 `execute_query` calls with malformed queries and **no final answer**.
- The AI plumbing functions. **The NIM model completed no dashboard task.**

## Acceptance criteria

| # | Criterion | T/F/Z | Basis |
|---|---|---|---|
| 1 | Credentials located; AG licence and AI entitlement verified; PayPal credentials inspected without display | **T** | Channel3 and AG keys present. Licence valid PRO-AI to 2026-12-22. PayPal credentials present, not read. |
| 2 | AG Studio 3.1.0 installed (exact) with `AgStudioProvider` and `AgStudioAiModule` | **T** | `package.json` uses exact versions; `npm ls` |
| 3 | LLM and Channel3 routed through the backend, no credential exposure | **T** | `server.py`; 0 key hits in the bundle, source or git |
| 4 | ≥ 3 genuine product offers with source, seller, price, currency and availability; raw responses preserved | **T** | 15 offers from 12 sellers, traced to 12 raw responses |
| 5 | Discovery not represented as a purchase | **T** | `OFFERS.json` notice; the payee is a labelled synthetic merchant |
| 6 | Agent selection and replanning after a changed offer | **T** (with disclosure) | Model-driven selection and replan. The change was **INJECTED**, because the live re-quote was unchanged; this is preregistered and labelled. |
| 7 | AG Studio dashboard | **T / Z** | Dashboard with five widgets; charts verified rendering; grid rows not visually confirmed (Z) |
| 8 | Functioning AI interactions | **F** | 216 turns through the backend with tool calls and delegation, but 0 completed tasks or answers from the NIM model |
| 9 | Correct formal admission and budget constraints | **T** | Lean T bound to the exact action; 16,900 ≤ 50,000; over-cap refused (N8); fresh Lean T at every gate |
| 10 | One authorized native PayPal Sandbox payment, only if all gates are satisfied | **F** (not executed, by design) | Gates unsatisfied: no independent A03 (v5 unsigned), no signed production permit, no buyer approval |
| 11 | Independent provider readback | **Z** | Separate-process readback matches, but against the synthetic provider (`MECHANISM_ONLY`) |
| 12 | Judge assessment | **T** (`MECHANISM_ONLY`) | J01–J11 pass; 9/9 Judge negatives rejected |
| 13 | Zero unauthorized or duplicate payments | **T** | 0 PayPal calls in the WO; mechanism run has 1 capture; duplicates refused (N10, J09) |
| 14 | Negatives: expired, revoked, stale price, altered amount, duplicate capture | **T** | 11/11 (run 2); run 1 harness defect preserved |
| 15 | Failed runs preserved; synthetic results labelled | **T** | 5 aborted or failed runs plus 1 harness-defect negatives file kept; `MECHANISM_ONLY` everywhere |

## Blockers requiring owner authority

1. **Independent A03 acceptance.** A designated reviewer, who must be neither Claude nor the operator, signs `commerce.a03.acceptance.v1` over `a03_reaccept/REVIEW_REQUEST_v5_UNSIGNED.json` with the trusted acceptance key.
2. **Production authority.** The operator signs the mission, control and bridge policy; the admission issuer signs the permit; then install and preflight. Today there is no A04 installation.
3. **Buyer approval.** The owner approves the buyer step in the PayPal Sandbox browser window.
4. **Owner decision on PayPal.** WO-007 PayPal payments were paused by the owner earlier, with 4 held reservations. Any native payment needs the owner's explicit go.
5. **Not owner authority, but open.** The NIM model cannot drive AG Studio's agents to completion. A stronger tool-calling model behind the same adapter, or AG-specific few-shot prompts, would be needed for criterion 8.

## GO/NO-GO

**NO-GO.** The integrated chain was executed for real on a $500-cap procurement: user intent, NIM agents, genuine Channel3 offers, selection, a labelled injected offer change with replanning, and a qualified Lean admission bound to the exact action with live price gates. The payment leg then ran through the A04 Gateway, a separate-process readback and an independent Judge, which passed 11/11 and rejected all 9 tampered copies. 11/11 transaction negatives were refused with zero forbidden provider calls, and no PayPal call was made. The GO condition is not met: the payment ran only on the synthetic provider (`MECHANISM_ONLY`), because there is no independent A03 acceptance, no signed production permit and no buyer approval. The AG Studio AI, while wired to the LEGION LLM, did not complete a task. Nothing was self-signed, no gate was bypassed, no threshold was relaxed, and no synthetic result is presented as native evidence.
