// AG Studio AgLlmAdapter -> LEGION backend (/api/llm/turn) -> NVIDIA NIM. No credential is held in the browser.
//
// WO-COM-E2E-002 repairs (adapter only; LEGION Formal Core untouched):
//  1. Schema handling: AG Studio tool schemas use $ref/$defs (create_plan 14 refs, execute_query 51). NIM passes them
//     to the model unresolved, so the model never saw the referenced shapes. The adapter inlines $ref before sending.
//  2. Validation + feedback: every tool call the model returns is validated with ajv against AG Studio's ORIGINAL
//     schema. An invalid call is never handed to AG Studio; instead the exact validation errors are fed back to the
//     model as that call's tool result and the turn is retried (max ATTEMPTS). Every attempt is a separate journaled
//     backend call (request.x_adapter). If no valid reply is produced, the adapter returns an explicit failure message.
//  3. Empty replies (no text, no tool call) are retried with an explicit instruction.
// Model output is never rewritten: a call is either forwarded verbatim or not forwarded at all.
//
// WO-COM-E2E-003 additions (adapter only; Formal Core, model and Judge untouched):
//  4. Widget-state feedback: when AG Studio returns the result of a widget-changing tool (add_widget, configure_widget,
//     add/remove_widget_filter, add/remove_page_filter, position_widget), the adapter observes the widget AFTER the change
//     settled (stored dataMapping, stored widget/page filters, AG's fresh viewWidget health issues, the rendered value) and
//     appends it, with a plain-language statement of what the widget computes, to that tool result as sent to the model.
//     AG Studio's own tool result text is kept unchanged in front of it. Observations are cached per callId (made once).
//  5. Completion gate (only when the session carries a task contract): a completion (complete_task from an agent that can
//     change widgets, or the lead agent's final text) is accepted only if a new widget satisfies the contract: the required
//     predicate is present in the stored widget/page filters AND the rendered value equals the ground truth the backend
//     computed from the dataset. Otherwise the completion is not delivered; the observed failures are fed back. If the
//     attempts run out, the lead's final answer is replaced by an explicit ADAPTER gate failure (never a false completion).
//     Every gate decision is journaled through /api/llm/gate.
import Ajv from 'ajv';
import { observeWidget, verifyContract } from './observe.js';

const ATTEMPTS = 4;
const ajv = new Ajv({ allErrors: true, strict: false, validateFormats: false, logger: false });

// Inline every NON-recursive $defs entry at its use sites (so e.g. FieldRef appears as the literal {id, aggregation}
// object where it is required). Recursive definitions (expressions, filter groups, layout row/column nodes) cannot be
// inlined finitely (full inlining measured 2.5 MB / 4.5 MB), so they stay as $ref with a reduced $defs that only holds
// them. The result is semantically identical to the original schema.
export function inlineRefs(schema) {
  const defs = (schema && (schema.$defs || schema.definitions)) || {};
  const refsIn = (node, acc = new Set()) => {
    if (Array.isArray(node)) node.forEach((n) => refsIn(n, acc));
    else if (node && typeof node === 'object') {
      if (typeof node.$ref === 'string' && node.$ref.startsWith('#/')) acc.add(node.$ref.split('/').pop());
      Object.values(node).forEach((v) => refsIn(v, acc));
    }
    return acc;
  };
  const direct = Object.fromEntries(Object.entries(defs).map(([k, v]) => [k, refsIn(v)]));
  const reaches = (from, target, seen = new Set()) => [...(direct[from] || [])].some((n) => n === target || (!seen.has(n) && (seen.add(n), reaches(n, target, seen))));
  const recursive = new Set(Object.keys(defs).filter((k) => reaches(k, k)));
  const walk = (node) => {
    if (Array.isArray(node)) return node.map(walk);
    if (!node || typeof node !== 'object') return node;
    if (typeof node.$ref === 'string' && node.$ref.startsWith('#/')) {
      const name = node.$ref.split('/').pop();
      if (defs[name] && !recursive.has(name)) { const { $ref, ...rest } = node; return { ...walk(defs[name]), ...walk(rest) }; }
      return { ...node, $ref: `#/$defs/${name}` };
    }
    const out = {};
    for (const [k, v] of Object.entries(node)) if (k !== '$defs' && k !== 'definitions') out[k] = walk(v);
    return out;
  };
  const out = walk(schema);
  if (recursive.size) out.$defs = Object.fromEntries([...recursive].map((k) => [k, walk(defs[k])]));
  return out;
}

const validators = new Map();
function validatorFor(tool) {
  const key = tool.name + ':' + JSON.stringify(tool.parameters);
  if (!validators.has(key)) validators.set(key, ajv.compile(tool.parameters));
  return validators.get(key);
}

export function checkCalls(request, response) {
  const tools = Object.fromEntries((request.tools || []).map((t) => [t.name, t]));
  const bad = [];
  for (const o of response.output || []) {
    if (o.type !== 'function_call') continue;
    const tool = tools[o.name];
    let args;
    try { args = JSON.parse(o.arguments || '{}'); } catch (e) { bad.push({ call: o, issues: [{ message: 'arguments are not valid JSON: ' + e.message }] }); continue; }
    if (!tool) { bad.push({ call: o, issues: [{ message: `unknown tool ${o.name}; available: ${Object.keys(tools).join(', ')}` }] }); continue; }
    const v = validatorFor(tool);
    if (!v(args)) bad.push({ call: o, issues: [...new Map(v.errors.map((e) => [`${e.instancePath}|${e.message}`, { path: e.instancePath || '/', message: e.message, params: e.params }])).values()].slice(0, 12) });
  }
  return bad;
}

async function post(body, signal) {
  const r = await fetch('/api/llm/turn', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal });
  if (!r.ok) throw new Error(`LLM backend HTTP ${r.status}`);
  return r.json();
}

// Feedback is scoped to what the calling tool controls (E2E-003 fix after nimA_T4: the page agent, which by its own
// instructions cannot touch data mappings, was told on every position_widget result that the widget "computes nothing yet"
// and repositioned it 49 times). Data/semantics/health feedback goes only to the tools that change a widget's data
// (configure_widget, add/remove_widget_filter); layout tools get the stored layout only.
const DATA_TOOLS = new Set(['configure_widget', 'add_widget_filter', 'remove_widget_filter']);
const LAYOUT_TOOLS = new Set(['add_widget', 'position_widget']);
const WIDGET_TOOLS = new Set([...DATA_TOOLS, ...LAYOUT_TOOLS]);
const BASE_WIDGETS = new Set(['bar1', 'grid_offers', 'pie_checks', 'grid_events', 'grid_checks']);
const observations = new Map(); // callId -> observation text (made once, right after the call's result first appears)

async function withObservations(input) {
  const calls = Object.fromEntries(input.filter((i) => i.type === 'function_call').map((i) => [i.callId, i]));
  const out = [];
  for (const it of input) {
    const call = it.type === 'function_call_output' && calls[it.callId];
    if (!call || !WIDGET_TOOLS.has(call.name)) { out.push(it); continue; }
    if (!observations.has(it.callId)) {
      let wid = null; try { wid = JSON.parse(call.arguments || '{}').widgetId; } catch (e) { /* invalid JSON never reaches AG */ }
      let obs;
      try {
        if (!wid) obs = { note: 'no widgetId' };
        else if (DATA_TOOLS.has(call.name)) obs = await observeWidget(wid);
        else {
          const st = window.__studio.current.api.getState();
          const pg = st.pages.find((x) => (x.widgets || {})[wid]);
          obs = pg ? { widgetId: wid, exists: true, type: pg.widgets[wid].type, stored_layout: (pg.widgetLayout || {})[wid] }
            : { widgetId: wid, exists: false };
        }
      } catch (e) { obs = { error: String(e) }; }
      observations.set(it.callId, DATA_TOOLS.has(call.name)
        ? '\n\nADAPTER_OBSERVED_STATE (read from AG Studio after this call settled; the health results inside the tool response above may be stale, fresh_health_issues are current): ' + JSON.stringify(obs)
        : '\n\nADAPTER_OBSERVED_LAYOUT (stored in AG Studio after this call): ' + JSON.stringify(obs));
    }
    out.push({ ...it, output: (it.output || '') + observations.get(it.callId) });
  }
  return out;
}

async function contract() {
  try { return (await (await fetch('/api/contract')).json()).contract; } catch (e) { return null; }
}

function gateLog(entry) {
  fetch('/api/llm/gate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(entry) }).catch(() => {});
}

function gateFeedbackText(c, v, lead) {
  const lines = v.candidates.length ? v.candidates.map((x) => `- widget ${x.widgetId} (${x.type}): ${x.semantics}; displays ${x.rendered && x.rendered.value_text}. Problems: ${x.reasons.join('; ')}`)
    : ['- no new widget exists on the dashboard'];
  return `COMPLETION REJECTED by the adapter's verification gate: the task is not done yet, so this completion was not delivered. `
    + `Required: a new ${c.widget_types.join(' or ')} widget on the ${c.source} data whose stored filters contain the predicate ${c.predicate.field} ${c.predicate.operator} ${JSON.stringify(c.predicate.value)}, `
    + `and whose displayed value equals the count computed independently from the data. Observed now:\n${lines.join('\n')}\n`
    + (lead ? `Delegate the fix to the widget agent (delegate_to with {"type": {"id": "widget", "widgetId": "<id>", "widgetType": "<type>"}, "instructions": "..."}) and state exactly which predicate must be added.`
      : `A widget-level predicate is added with add_widget_filter (op "append", widgetId, value {field: {id}, model: {operator, value}}); check the result with view_widget before completing.`);
}

export async function completeTurn(request, signal) {
  const turnId = Math.random().toString(36).slice(2);
  const sentTools = (request.tools || []).map((t) => ({ ...t, parameters: inlineRefs(t.parameters) }));
  const toolNames = new Set((request.tools || []).map((t) => t.name));
  const observedInput = await withObservations(request.input);
  const observedIds = observedInput.filter((i, k) => i !== request.input[k]).map((i) => i.callId);
  const feedback = [];
  const history = [];
  for (let attempt = 1; attempt <= ATTEMPTS; attempt++) {
    const res = await post({ ...request, tools: sentTools, input: [...observedInput, ...feedback],
      x_adapter: { turnId, attempt, maxAttempts: ATTEMPTS, inlinedRefs: true, observedOutputs: observedIds, previous: history } }, signal);
    if (res.status === 'failed') return res;
    const calls = (res.output || []).filter((o) => o.type === 'function_call');
    const texts = (res.output || []).filter((o) => o.type === 'message');
    if (!calls.length && !texts.length) {
      history.push({ attempt, rejected: 'EMPTY_REPLY' });
      feedback.push({ id: `fb_${turnId}_${attempt}`, kind: 'input', type: 'message', role: 'system', status: 'completed',
        content: [{ type: 'text', text: 'Your previous reply was empty. Either call one of the available tools with arguments that match its schema, or answer the user in text.' }] });
      continue;
    }
    // Grounding guard (E2E-002, after nim_T1_a1 fabricated "seller 111 at $1.00" without any tool call): a text-only
    // reply is not accepted while tools are available but none has been executed yet in this conversation.
    const anyToolResult = request.input.some((i) => i.type === 'function_call_output');
    if (!calls.length && texts.length && (request.tools || []).length && !anyToolResult && attempt < ATTEMPTS) {
      history.push({ attempt, rejected: 'UNGROUNDED_TEXT', text: texts.map((m) => (m.content || []).map((c) => c.text).join('')).join(' ').slice(0, 300) });
      feedback.push({ ...texts[0] });
      feedback.push({ id: `fbg_${turnId}_${attempt}`, kind: 'input', type: 'message', role: 'system', status: 'completed',
        content: [{ type: 'text', text: 'REJECTED: that answer was not shown to the user because no tool has been called yet, so it is not based on the dashboard data. The data is only reachable through the tools. Call the appropriate tool (for data questions: delegate_to with {"type": {"id": "data"}, "instructions": "..."}) and answer only from tool results.' }] });
      continue;
    }
    const bad = checkCalls(request, res);
    if (!bad.length) {
      // Completion gate (E2E-003). Completions: complete_task by an agent that can change widgets; final text of the lead.
      const completes = calls.some((o) => o.name === 'complete_task') && (toolNames.has('configure_widget') || toolNames.has('add_widget_filter'));
      const leadFinal = !calls.length && texts.length > 0 && toolNames.has('delegate_to');
      const c = (completes || leadFinal) ? await contract() : null;
      if (!c) return res;
      const v = await verifyContract(c, BASE_WIDGETS);
      const kind = completes ? 'complete_task' : 'lead_final_text';
      gateLog({ turnId, attempt, kind, pass: v.pass, verification: v, contract: c });
      if (v.pass) return res;
      history.push({ attempt, rejected: 'GATE', kind, reasons: v.candidates.map((x) => ({ widgetId: x.widgetId, reasons: x.reasons })) });
      const text = gateFeedbackText(c, v, leadFinal);
      if (attempt === ATTEMPTS) {
        if (leadFinal) return { id: `adapter-gate-${turnId}`, createdAt: Date.now(), status: 'completed',
          output: [{ id: `msg_gate_${turnId}`, kind: 'output', type: 'message', role: 'assistant', status: 'completed',
            content: [{ type: 'text', text: `ADAPTER: task NOT completed; the verification gate rejected the final answer ${ATTEMPTS} times. ${text}`, annotations: [] }] }] };
        return res; // a sub-agent's complete_task is forwarded unchanged; the lead's final answer remains gated
      }
      if (leadFinal) {
        feedback.push({ ...texts[0] });
        feedback.push({ id: `fbgate_${turnId}_${attempt}`, kind: 'input', type: 'message', role: 'system', status: 'completed', content: [{ type: 'text', text }] });
      } else {
        for (const o of calls) {
          feedback.push({ ...o, id: `fbgc_${turnId}_${attempt}_${o.callId}` });
          feedback.push({ type: 'function_call_output', callId: o.callId, status: 'completed',
            output: JSON.stringify(o.name === 'complete_task' ? { success: false, adapter_gate: text }
              : { success: false, adapter_gate: 'NOT EXECUTED because the completion in the same reply was rejected; repeat it if still needed.' }) });
        }
      }
      continue;
    }
    history.push({ attempt, rejected: 'SCHEMA', calls: bad.map((b) => ({ name: b.call.name, issues: b.issues })) });
    for (const o of calls) {
      const b = bad.find((x) => x.call === o);
      feedback.push({ ...o, id: `fbc_${turnId}_${attempt}_${o.callId}` });
      feedback.push({ type: 'function_call_output', callId: o.callId, status: 'completed',
        output: JSON.stringify(b ? { success: false, adapter_validation: 'NOT EXECUTED: arguments do not match the tool schema. Fix exactly these errors and call the tool again.', issues: b.issues }
          : { success: false, adapter_validation: 'NOT EXECUTED because another call in the same reply was invalid; repeat it with the corrected call.' }) });
    }
  }
  return { id: `adapter-giveup-${turnId}`, createdAt: Date.now(), status: 'completed',
    output: [{ id: `msg_giveup_${turnId}`, kind: 'output', type: 'message', role: 'assistant', status: 'completed',
      content: [{ type: 'text', text: `ADAPTER: the model did not produce a schema-valid reply after ${ATTEMPTS} attempts; no tool was executed for this step. Rejections: ${JSON.stringify(history).slice(0, 1500)}`, annotations: [] }] }] };
}

export function legionAdapter() {
  return {
    executeTurn(request, options = {}) {
      const complete = completeTurn(request, options.signal);
      async function* events() {
        const res = await complete;
        for (const item of res.output || []) {
          if (item.type === 'message') {
            yield { type: 'TEXT_MESSAGE_START', messageId: item.id, role: 'assistant' };
            for (const c of item.content || []) if (c.type === 'text') yield { type: 'TEXT_MESSAGE_CONTENT', messageId: item.id, delta: c.text };
            yield { type: 'TEXT_MESSAGE_END', messageId: item.id };
          } else if (item.type === 'function_call') {
            yield { type: 'TOOL_CALL_START', toolCallId: item.callId, toolCallName: item.name };
            yield { type: 'TOOL_CALL_ARGS', toolCallId: item.callId, delta: item.arguments };
            yield { type: 'TOOL_CALL_END', toolCallId: item.callId };
          }
        }
      }
      return { stream: { [Symbol.asyncIterator]: () => events() }, complete };
    },
  };
}
