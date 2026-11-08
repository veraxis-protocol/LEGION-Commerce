// WO-COM-E2E-002: exact schema violations of every tool call in a journal session.
//   node tools/violations.mjs <journal.jsonl> <out.json>
// For each function_call the model (or a script) returned, take the JSON schema AG Studio offered for that tool in the
// SAME request, validate the arguments independently with ajv, and pair it with AG Studio's own tool result (the
// function_call_output for that callId in a later request). Nothing is repaired here; this is measurement only.
import fs from 'node:fs';
import Ajv from '../web/node_modules/ajv/dist/ajv.js';

const [,, journalPath, outPath] = process.argv;
const turns = fs.readFileSync(journalPath, 'utf8').trim().split('\n').filter(Boolean).map((l) => JSON.parse(l));
const results = new Map();
for (const t of turns) for (const it of t.request?.input || []) if (it.type === 'function_call_output') results.set(it.callId, it.output);
const ajv = new Ajv.default({ allErrors: true, strict: false, validateFormats: false });
const calls = [];
for (const t of turns) {
  const tools = Object.fromEntries((t.request?.tools || []).map((x) => [x.name, x.parameters]));
  for (const o of t.response?.output || []) {
    if (o.type !== 'function_call') continue;
    let args, parseError = null;
    try { args = JSON.parse(o.arguments || '{}'); } catch (e) { parseError = e.message; }
    const schema = tools[o.name];
    let errors = null;
    if (!schema) errors = [{ message: 'tool not offered in this request' }];
    else if (parseError) errors = [{ message: 'arguments are not JSON: ' + parseError }];
    else {
      const v = ajv.compile(schema);
      errors = v(args) ? [] : v.errors.map((e) => ({ path: e.instancePath, keyword: e.keyword, message: e.message, params: e.params }));
    }
    const out = results.get(o.callId);
    let agResult = null;
    try { agResult = out ? JSON.parse(out) : null; } catch { agResult = { raw: out }; }
    calls.push({ seq: t.seq, tool: o.name, args_excerpt: (o.arguments || '').slice(0, 400), ajv_valid: errors.length === 0,
                 ajv_errors: errors.slice(0, 8), ag_success: agResult ? agResult.success : null,
                 ag_issues: agResult && agResult.issues ? agResult.issues.slice(0, 4) : null, ag_response_excerpt: agResult && agResult.response ? String(agResult.response).slice(0, 200) : null });
  }
}
const byTool = {};
for (const c of calls) {
  const b = (byTool[c.tool] ||= { calls: 0, ajv_invalid: 0, ag_failed: 0, ajv_invalid_but_ag_ok: 0, ajv_valid_but_ag_failed: 0, top_errors: {} });
  b.calls++; if (!c.ajv_valid) b.ajv_invalid++; if (c.ag_success === false) b.ag_failed++;
  if (!c.ajv_valid && c.ag_success === true) b.ajv_invalid_but_ag_ok++;
  if (c.ajv_valid && c.ag_success === false) b.ajv_valid_but_ag_failed++;
  for (const e of c.ajv_errors) { const k = `${e.path || ''} ${e.keyword || ''}: ${e.message}`; b.top_errors[k] = (b.top_errors[k] || 0) + 1; }
}
for (const b of Object.values(byTool)) b.top_errors = Object.fromEntries(Object.entries(b.top_errors).sort((a, c) => c[1] - a[1]).slice(0, 8));
fs.writeFileSync(outPath, JSON.stringify({ journal: journalPath, turns: turns.length, calls: calls.length, byTool, calls_detail: calls }, null, 1));
console.log(JSON.stringify(byTool, null, 1));
