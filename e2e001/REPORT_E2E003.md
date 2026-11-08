# WO-COM-E2E-003 — Semantic Closure: report

**Verdict: GO, on the confirmation run `nimC`.**
- Run `nimC` used the current NIM model (`nvidia/nemotron-3.5-lightning-30b-a3b`). The adapter, checker, server and bundle were hash-frozen before it started (`FROZEN_BEFORE_nimC.sha256`, re-verified OK afterwards).
- **5/5 correct tasks.**
- **Zero false completions reached the user.**
- **No runaway loop:** at most 49 backend calls against the 80-call budget.
- **3/3 grids** verified cell by cell.
- **T5:**
  - the KPI widget stores the predicate `checks.result equals "PASS"`;
  - it renders **30**, equal to the ground truth computed independently from the data (30 PASS + 1 FAIL fixture);
  - an unfiltered count would show 31.

**Unchanged:** LEGION Formal Core, the model, the Judge (`judge.py`), and the T1–T4 acceptance criteria. The checker was only made stricter, plus one disclosed visibility fix (§7).

**Native PayPal stays blocked:** there is no genuine A03 and no owner authority.

Branch `claude/wo-com-e2e-003` (from `7675008`). Evidence: `e2e003_evidence/`, which includes the journals copied into `journals/`.

## 1. Preservation
- The Formal Core was not touched.
- The E2E-002 adapter (schema inlining, validation and feedback, empty-reply retry, grounding guard) is unchanged. The E2E-003 additions are separate code paths (§4, §5).
- Regression:
  - adapter unit test on the E2E-002 replays: `ADAPTER_UNIT_regression.json` → pass, 0 mismatches;
  - browser scripts S0, S1, S3, S4 behave as in E2E-002 (`reg_S*`);
  - `reg_S2`: script S2 was recorded before E2E-002 added the grounding guard. The guard now correctly rejects its untooled text answer, so the script runs out of responses. This is expected E2E-002 behaviour, not a regression.
- All E2E-001/002 evidence is untouched. Every E2E-003 session, including the failed ones, has its own journal.

## 2. T5 reproduced on a mixed-status fixture
- **Fixture.** `server.py --fixture e2e003_mixed_checks` overrides **one** genuine check row (the last one, `N9_permit_signature_forged`) from PASS to FAIL. The row's detail text says so, the dashboard shows a "TEST FIXTURE" badge, and `/api/run` returns the fixture metadata including the original row. The result is 30 PASS + 1 FAIL. Offers and events are untouched.
- **Replay** (`replay_T5_fixture`): the 92 recorded model responses of E2E-002 `final2_T5` were replayed with no model involved.
  - The widget stores **no filter** and renders **31**; the ground truth is 30.
  - It still claims "count of checks with result PASS".
  - The E2E-003 checker fails it on predicate, displayed value, DOM and loop.

## 3. The PASS-predicate mechanism, verified before touching the agent
`tools/predicate_probe.mjs` ran on the fixture using AG Studio's own AI tools, with no model involved. Results are in `predicate_probe/PREDICATE_PROBE.json` and the screenshot.

| Step | Stored widget filters | Rendered KPI |
|---|---|---|
| `addWidget` value + `configureWidget` count(`checks.result`) | none | **31** |
| `addWidgetFilter` `checks.result equals "PASS"` | `[checks.result equals PASS]` | **30** |
| `removeWidgetFilter` index 0 | none | **31** |
| `addWidgetFilter` `checks.result isIn ["PASS"]` | `[checks.result isIn [PASS]]` | **30** |

Findings that explain the E2E-002 T5 failure:

1. **The predicate lives in `pages[].filter.widget[<id>]`, not in the widget's `dataMapping`.**
   - The widget's own `getState()` entry never shows it; only `viewWidget` and the page's filter map do.
   - The E2E-002 checker looked only at `dataMapping`, so it could not see whether a filter existed.
   - The E2E-002 agent instead tried to put `filters` inside `configure_widget`, which the schema rejects.
2. **`configure_widget`'s own reply carries a stale health result.**
   - It returned "data_mapping_complete … score 0" for a widget that renders correctly; a fresh `viewWidget` shows no such issue.
   - The E2E-002 T5 agent kept reconfiguring the widget in response to that message.
3. **Mapping a widget id to its rendered element is deterministic.**
   - AG Studio positions each layout widget with inline `top = yTrack·rowHeight` and `left = xTrack·(width/columns)`.
   - The adapter and the driver read rendered values this way, with separately written code.

## 4. Explicit tool feedback on the widget's actual configuration and semantics
Code: `web/src/observe.js`, plus `withObservations` in `web/src/llmAdapter.js`.

**What the feedback contains.**
- After AG Studio returns the result of `configure_widget`, `add_widget_filter` or `remove_widget_filter`, the adapter waits for the change to settle.
- It then appends `ADAPTER_OBSERVED_STATE` to that tool result, as sent to the model:
  - the **stored** `dataMapping`;
  - the stored widget and page filters;
  - AG Studio's **fresh** `viewWidget` health issues;
  - the **rendered** value;
  - a plain statement such as *"value widget: value = count(checks.result); computed over checks ALL rows (no widget or page filter is applied)"*.
- AG Studio's own reply text is kept in front, unchanged.
- Layout tools (`add_widget`, `position_widget`) get only the stored layout (`ADAPTER_OBSERVED_LAYOUT`).
- The model's output is never rewritten.

**Scoping defect, found and fixed in run `nimA`.**
- The first version gave the data semantics and health to layout tools too.
- In `nimA_T4` the page agent was told on every `position_widget` result that the chart "computes nothing yet". Its own instructions say it cannot change data, so it repositioned the widget 49 times (108 calls, a loop).
- Feedback is now scoped to what the calling tool controls.
- The `nimA` run is preserved.

## 5. Completion gate
Code: `verifyContract` in `observe.js`, plus the gate in `completeTurn`. Decisions are journaled to `ai_turns/<session>.gate.jsonl`.

**Contract.**
- The task contract (`tasks/T5_contract.json`) holds the source, the allowed widget types and the predicate.
- The driver hands it to the backend. The backend serves it with a **ground truth computed in Python from the served dataset** (`contract_with_ground_truth`, independent of AG Studio).
- Only T5 carries a contract; T1–T4 run without one.

**What is gated.**
- Completions are a `complete_task` by an agent that can change widgets, and the lead agent's final text.
- A completion is delivered only if some new widget of an allowed type, mapped to `checks`:
  - has the predicate in its **stored** filters; and
  - renders a value equal to the ground truth.
- Otherwise the completion is withheld, and the observed failures are fed back without revealing the ground-truth number.

**When the attempts run out.**
- A sub-agent's `complete_task` is forwarded unchanged, so the lead's final answer stays gated.
- The lead's final answer is replaced by an explicit `ADAPTER: task NOT completed …`.

Verified with deterministic scripts before any model run (`scripts/G*.json`; runs `gate3_*`, final build):

| Script | Behaviour | Checker |
|---|---|---|
| G1 lead claims "31 PASS checks", no widget | 4 gated rejections → explicit `ADAPTER: task NOT completed` | FAIL (no false completion) |
| G2 KPI without predicate, then `add_widget_filter equals PASS` | `complete_task` rejected (31, predicate missing); after the filter: accepted (30); final answer accepted | PASS |
| G3 KPI filtered by `checks.result notEqual "FAIL"`: right number (30), wrong predicate | rejected on every completion attempt → explicit failure | FAIL (correct number without the required predicate) |

`gate_*` (first build) and `gate2_*` are preserved. `gate_*` exposed an observer crash on an empty `sparklineX` slot in the stored mapping, and `gate2_*` ran before the feedback was scoped.

## 6. T1–T5 with the current NIM model, unchanged acceptance criteria
- The tasks and prompts are E2E-002's (`tasks/TASKS.json`, derived and fixed before the first run).
- T5's expected value follows the fixture (30), as the work order states.
- Budgets match E2E-002: 420 s per task in the driver and 80 backend calls in the checker.

| Run | Configuration | T1 | T2 | T3 | T4 | T5 | Score (frozen v2 checker / v3) |
|---|---|---|---|---|---|---|---|
| nimA | feedback not yet scoped | PASS | PASS | PASS | **FAIL**: page-agent loop, 108 calls (§4 defect) | PASS (gate rejected 1, then the predicate was added; renders 30) | 4/5 / 4/5 |
| nimB | scoped feedback | PASS | PASS | **FAIL** (v2): only `final_answer_visible_in_chat`, a markdown artefact (§7) | PASS | PASS (gate rejected 1; renders 30) | 4/5 / **5/5** |
| **nimC** | **everything frozen before the run** | PASS | PASS | PASS | PASS | PASS (§6.1) | — / **5/5** |

**nimC final answers:**
- T1 "macys.com at $29.99 USD"
- T2 "15 offers … 12 distinct sellers"
- T3 "microcenter.com … $169"
- T4: a column chart of `avg(offers.price)` by `offers.brand`, rendering data
- T5: a KPI showing 30 with the stored predicate `checks.result equals "PASS"`

### 6.1 How T5 reached the result in nimC
1. The widget agent configured `count(checks.result)`; the feedback stated "ALL rows (no widget or page filter is applied)" and showed 31.
2. Its `complete_task` was rejected 4 times in one turn and then forwarded unchanged (the designed behaviour for sub-agents).
3. The lead delegated a widget agent again. It added `checks.result equals "PASS"`, the KPI rendered 30, the gate accepted its `complete_task`, and then the lead's final answer.
4. No completion the gate had not accepted was shown to the user.

### 6.2 Traces and costs
Per-session details are in `STATS_nim*.json`: calls, adapter rejections by kind, gate decisions, tokens, latency, NIM transport retries and tool calls by name. The NIM endpoint carries no per-call price, so tokens are the cost measure.

| Run | Backend calls | Tokens (prompt + completion) | Model latency | NIM 429/5xx retries |
|---|---|---|---|---|
| nimA | 240 | 7,378,838 (T4 loop alone: 5,526,999) | 353 s | 6 |
| nimB | 130 | 1,894,212 | 197 s | 2 |
| nimC | 111 | 1,502,957 | 140 s | 2 |

**Schema violations.**
- **0 schema violations reached AG Studio in every E2E-003 session.**
- This holds across all 39 `CHECK*.json` files.
- The model still emits invalid calls and gated completions; the adapter intercepts them and feeds the errors back. In nimC the checker counts 34 adapter-rejected outputs, none of which was executed.

## 7. Disclosed checker change (visibility only)
**What went wrong.**
- `nimB_T3` answered correctly and was grounded: "seller **microcenter.com** at a price of **$169**".
- AG Studio's chat renders markdown, so the panel showed "microcenter.com".
- The E2E-002 rule compared the raw 60-character prefix and failed it.

**The fix.**
- v3 compares both sides after removing markdown markup and collapsing whitespace (`md_plain`).
- Correctness and grounding still use the raw text. Nothing else changed.

**Application.**
- v3 was frozen **before** nimC (hashes in `FROZEN_BEFORE_nimC.sha256`).
- Earlier runs were re-scored into separate `CHECK_v3.json` files; the original `CHECK.json` files are kept.
- Re-scoring changes no verdict apart from `nimB_T3`. The gate scripts and the E2E-002 T5 replay keep their verdicts.

**Stricter in E2E-003:**
- the predicate must be in the stored state;
- the rendered value must equal a ground truth the checker computes itself from the dataset captured in `CHAT_RESULT.json`;
- gate-rejected replies never count as answers.

## 8. Step 7 (alternative model): not triggered
The comparison was conditional on T5 still failing; T5 passed in all three live runs. It was prepared anyway (`ALT_MODEL_PROBE.json`, one tiny tool call each, using the exact backend request body):

| Model | Result |
|---|---|
| `nvidia/nemotron-3-ultra-550b-a55b` | correct tool call, 0.8 s. **Would have been the controlled alternative**: same family and template, identical body. |
| `nemotron-3.5-super-vl-120b-a12b`, `minimax-m3.1-flash` | correct tool call |
| `deepseek-v4.1-flash` | correct tool call, 84 s |
| `nemotron-3-super-120b-a12b`, `kimi-k2.6`, `llama-3.1-nemotron-ultra-253b-v1` | 404: not enabled for this account |
| `mistral-large-4-1t-a52b`, `glm-5.3-flash`, `gpt-oss-20b` | timed out (120 s) |

The backend now accepts a per-session `model` (journaled); it was not used for any scored run.

## 9. Grids: 3/3
`grid_check_fixture/GRID_CHECK.json` plus the screenshot, using `tools/grids.mjs` in headless Edge on the fixture dataset. Every row was cell-matched, including the overridden FAIL row.

| Grid | Rows verified | Unmatched |
|---|---|---|
| offers | 15/15 | 0 |
| events | 32/32 | 0 |
| checks | 31/31 | 0 |

## Acceptance

| Criterion | T/F/Z |
|---|---|
| 1. Validated adapter, Formal Core and existing evidence preserved | **T** |
| 2. T5 reproduced on 30 PASS + 1 FAIL; expected 30, not 31 | **T** (replay renders 31 without a predicate; fails) |
| 3. PASS-predicate mechanism verified before modifying the agent | **T** (31 → 30 → 31 → 30 via AG's own tools) |
| 4. Explicit tool feedback on actual widget configuration and semantics | **T** (scoped after the nimA defect) |
| 5. Completion only with the predicate in the widget state and display = independent ground truth | **T** (G1–G3; gate journals) |
| 6. T1–T5 retried with the current NIM model, unchanged criteria | **T** (nimC 5/5; nimA 4/5 and nimB 4/5 (v2) preserved) |
| 7. Alternative-model comparison if T5 still fails | **Z**: not triggered (T5 passed 3/3 live runs); prepared and probed |
| 8. All traces, costs, tool calls and failures preserved | **T** |
| GO: 5/5 correct tasks | **T** (nimC) |
| GO: zero false completion | **T** (no gate-rejected completion was ever shown to the user; G1/G3 end in explicit failure) |
| GO: no runaway loops | **T** in nimC (max 49 of 80). nimA_T4 looped (108) under the defective first feedback version, which was then fixed |
| GO: 3/3 grids verified | **T** |
| NO-GO triggers in the final configuration | **None**: no correct number without the predicate, no unverified widget state, no exhausted budget |
| Native PayPal | Still blocked: no genuine A03, no owner authority |

## GO/NO-GO and limits
**GO**, on the frozen confirmation run. Limits:
- **Sample size.** It is one confirmation run of five tasks. Across the three live runs T5 passed 3/3, and the other tasks failed only from the two disclosed defects, both fixed before nimC.
- **Scope of the gate.** The gate covers tasks that carry a contract, here T5. T1–T4 rely on the E2E-002 guards (validation, grounding) and the checker.
- **Effort.** In nimC the model needed the gate's feedback (4 rejections) before it added the predicate. The task succeeds through verified state transitions, not first-attempt reasoning.
- **Fixture.** The mixed-status data is a labelled fixture over the genuine run. It is not a genuine Judge result and must not be read as one.
