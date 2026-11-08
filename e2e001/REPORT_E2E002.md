# WO-COM-E2E-002 — Close the AG Studio AI execution gap: report

**Verdict: NO-GO.**
- **Final configuration:** **4/5 tasks** passed. 3/3 grids verified. **0 schema violations reached AG Studio** in every NIM session after the repair.
- **T5 failed on NO-GO grounds:**
  - it ran 92 backend calls, beyond the 80-call loop limit;
  - its final answer claims a "count of checks with result PASS", but the widget it configured counts all checks with no PASS filter. The value (31) is right only because every check passes, so the claim has no observable basis.
- **Unchanged:** the model (`nvidia/nemotron-3.5-lightning-30b-a3b`), LEGION Formal Core, and the native PayPal block (no A03, no owner authority).

Branch `claude/wo-com-e2e-002` (from `0a290ae`). Evidence: `e2e002_evidence/` plus `e2e001/ai_turns/`, both deposited to Dropbox.

## 1. Reproduction of the three failed sessions

The three failures were reproduced deterministically by **replaying the recorded model responses**, through the backend's `script` mode, into the real AG Studio running in headless Microsoft Edge 156. No model was involved.

| Replay | Recorded responses | Result |
|---|---|---|
| R1 | 39 | Same failure: 24 `delegate_to` calls rejected, "no target agent specified" |
| R2 | 133 | Same failure: 64 `create_plan` calls rejected, loop |
| R3 | 43 (turn 0 excluded) | Same failure: 31 `execute_query` calls rejected, no final answer |

R3's turn 0 was a leftover turn from the still-running session-2 loop; this is recorded in `scripts/R3_NOTE.txt`.

Tooling: `e2e001/tools/chat.mjs` (driver) and `scripts/R*_replay.json`. Journals: `ai_turns/replay_R*.jsonl`.

## 2. Exact schema violations

Source: `replay_R*/VIOLATIONS.json`, produced by `tools/violations.mjs`. Each call's arguments were validated with ajv against the schema AG Studio offered in the same request, and compared with AG Studio's own tool result. They agree on **every call**: 119 invalid calls, all rejected by AG; 0 valid calls rejected.

| Tool | Calls | Invalid | Exact violations (count) |
|---|---|---|---|
| `delegate_to` (R1) | 24 | 24 | `/type`: must be object (96); `/type`: must match `anyOf` (24). The model sent `"type": "data"` or omitted it; the schema needs `{"type": {"id": "data", …}}`. |
| `create_plan` (R2) | 64 | 64 | `/plan/layout/root/children/0`: missing `width` (96); `/plan/layout/root`: missing `height` (48); `root/type`: not in enum (48); children: extra properties (48) |
| `execute_query` (R3) | 31 | 31 | `/query/measures/0`: extra properties (44); `measures/0/field`: must be object (42); `/query/axes/0`: must be object (23); `field`: `anyOf` (21). The model also often sent `"query"` as a JSON **string**. |

**Root cause.**
- `create_plan` and `execute_query` depend on `$ref`/`$defs` (14 and 51 references), and the model did not follow the referenced shapes.
- No tool result told the model what exactly was wrong in a way it acted on.
- AG Studio ran every invalid call and looped.

## 3. Adapter verified deterministically before NIM

**Unit level** (`web/adapter_unit.test.mjs` → `ADAPTER_UNIT.json`):
- The adapter's validator flags **119/119** calls AG rejected and **0/82** calls AG accepted.
- The `$ref`-inlined schemas sent to the model reach **the same verdict as the originals on all 203 calls**.

**Browser level** (scripted responses, real AG Studio, headless Edge):

| Script | Behaviour verified |
|---|---|
| S0 | Tool call → AG executes it → the result is fed back as `function_call_output` → the final text renders |
| S1 | An invalid `delegate_to` is **not** executed by AG; its exact errors are fed back; the valid follow-up call is executed; the answer renders |
| S2 | An empty reply is retried |
| S3 | After 4 invalid attempts the adapter returns an explicit `ADAPTER:` failure and **nothing is executed** |
| S4 | A text answer given before any tool was used (fabrication) is never shown; the tool runs; a grounded answer renders |

## 4. Repairs (adapter and backend only; Formal Core untouched)

| Component | Change |
|---|---|
| `web/src/llmAdapter.js` | Inline non-recursive `$defs`. Recursive ones (expressions, layout nodes) stay as `$ref`, because full inlining measured 2.5 MB and 4.5 MB. |
| `web/src/llmAdapter.js` | Validate every model tool call against AG's original schema. Invalid calls are **never forwarded**: their exact errors go back to the model as the tool result, up to 4 attempts, each attempt journaled (`x_adapter`). Model output is never rewritten. |
| `web/src/llmAdapter.js` | Grounding guard: a text-only reply before any tool result is rejected. Added after `nim_T1_a1` fabricated "seller 111 at $1.00". |
| `server.py` | Removed the E2E-001 `delegate_to` string→object rewrite; output is now verbatim. |
| `server.py` | System hint covering the `delegate_to` format, grounding, and two `execute_query` examples verified by AG's own `executeQuery`. The examples use the **events** table, so they contain no task answer. |
| `server.py` | Generic note that aggregation `sort`/`limit` does not select a min/max group (added after final1 T1). |
| `server.py` | Retry NIM 429/502/503 with backoff, recorded per call (added after final1 T5 ended on HTTP 429). |
| `server.py` | Full journal per call: AG request including tool schemas, provider body, raw provider response, response, adapter attempt. |

## 5. Five predefined tasks on real Channel3 data

**Setup.**
- Data: run `run-20261108T160504Z`, 15 genuine Channel3 offers from 12 sellers.
- Tasks and expected tokens: `tasks/TASKS.json`, fixed before the first run.
- Checker: `tools/check_task.py`.

**A task passes only if all of the following hold:**
- it reaches true idle with at most 80 backend calls;
- 0 validation failures at AG;
- the final answer is accepted and visible in the chat;
- for answers: correct, **and** every grounding value appears in a tool result that AG actually executed;
- for widgets: a new widget of the right type and mapping, which AG's health check says renders data.

| Task | final1 | final2 (final configuration) |
|---|---|---|
| T1 lowest-priced seller (macys.com, $29.99) | **FAIL**: "bestbuy.com at $44.99". A valid aggregation was misused (`sort` + `limit 1`) and the wrong row taken; checker: answer incorrect | **PASS**: "macys.com at $29.99 USD" |
| T2 offers and distinct sellers (15 / 12) | **PASS** | **PASS** |
| T3 FINAL offer (microcenter.com, $169) | **PASS** | **PASS** |
| T4 column chart, average price by brand | **PASS**: correct mapping, renders | **PASS** |
| T5 KPI, number of PASS checks | **FAIL**: NIM HTTP 429 ended the session with no final answer | **FAIL**: 92 calls (loop limit); claims a PASS filter that does not exist |
| **Score** | **3/5** | **4/5** |

**Earlier attempts, preserved.** These ran before the final configuration existed, so they don't count toward the score:
- `nim_T1_a1`: fabrication, which led to the grounding guard.
- `nim_T1_a2`: PASS.
- `nim_T2_a1`: FAIL. Its CHECK.json read a rejected reply; that is a checker defect, and the re-check is `CHECK_v2_accepted_only.json`.
- `nim_T3_a1`: PASS.

**Schema violations.** **0 schema violations reached AG Studio across all 15 post-repair NIM sessions.** The model still produced invalid calls, which the adapter intercepted and fed back without executing them; the counts are in `SESSION_STATS.json` (100 across all NIM sessions).

**Checker limitation (disclosed).** The T5 mapping rule only required a `checks.*` field, so it accepted a widget with no PASS filter. Its `dom_shows_expected` check passed only because all 31 checks are PASS.

## 6. Grids: 3/3 verified

The problem was a layout defect, not data:
- The AG Studio container had no definite height, and the page used default tracks, so widgets were squeezed into a strip of about 50 px.
- In headless Edge the grids rendered 0 rows; the screenshot is in `grid_check_baseline/`.

**Fix:** absolute-positioned container, plus page layout `columns: 12, rowHeight: 80`.

**Verification** (`grid_check_final/GRID_CHECK.json` plus screenshot; `tools/grids.mjs`): headless Edge scrolled each grid's viewport and matched **every row cell by cell** against the backend dataset.

| Grid | Rows verified | Mismatches |
|---|---|---|
| offers | 15/15 | 0 |
| events | 32/32 | 0 |
| checks | 31/31 | 0 |

## Acceptance

| Criterion | T/F/Z |
|---|---|
| 1. Three failed sessions reproduced from the preserved traces | **T** |
| 2. Exact schema violations identified (`delegate_to`, `create_plan`, `execute_query`) | **T** |
| 3. Adapter verified with deterministic responses before NIM | **T** (unit 119/119, 0 false positives, 203/203 equivalence; S0–S4) |
| 4. Schema handling and tool-result feedback repaired, Formal Core unchanged | **T** |
| 5. Five predefined tasks completed with correct final answers | **F** (final configuration 4/5; T5 loop and unsupported claim) |
| 6. Three grids display actual rows | **T** (3/3, every row cell-verified) |
| 7. Every response, tool call, failure and final answer preserved | **T** |
| GO: zero schema violations | **T** at AG (0 in 15 sessions); the model still emits invalid calls, which are intercepted |
| NO-GO triggers | **Present**: T5 looped (92 calls) and claimed a filter that was not configured |
| Native PayPal | Still blocked: no genuine A03, no owner authority |

## GO/NO-GO

**NO-GO.** The adapter is now independently established as correct. Its validator matches AG Studio's own verdicts on all 201 recorded calls, the shipped schemas are semantically identical to AG's, and the feedback, retry, grounding and give-up paths are each proven with scripted responses in the real AG Studio. In the final configuration no schema violation reached AG Studio, and all three grids show every row of real data. With the unchanged NIM model, four of the five predefined tasks completed with correct, data-grounded answers. T5 (a PASS-count KPI) did not: it exceeded the loop limit and reported a PASS filter that was not configured. That is success without observable evidence, which the work order defines as NO-GO. The remaining gap is model capability on multi-step widget configuration, not adapter correctness. Since the WO allows changing models only after the adapter is established, a model change is now the logical next step, and it is the owner's call.
