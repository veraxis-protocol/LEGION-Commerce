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
import Ajv from 'ajv';

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

export async function completeTurn(request, signal) {
  const turnId = Math.random().toString(36).slice(2);
  const sentTools = (request.tools || []).map((t) => ({ ...t, parameters: inlineRefs(t.parameters) }));
  const feedback = [];
  const history = [];
  for (let attempt = 1; attempt <= ATTEMPTS; attempt++) {
    const res = await post({ ...request, tools: sentTools, input: [...request.input, ...feedback],
      x_adapter: { turnId, attempt, maxAttempts: ATTEMPTS, inlinedRefs: true, previous: history } }, signal);
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
    if (!bad.length) return res;
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
