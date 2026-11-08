// WO-COM-E2E-002 adapter unit checks (node, no browser, no model). Usage: node adapter_unit.test.mjs <replay journals...>
import fs from 'node:fs';
import Ajv from 'ajv';
import { inlineRefs, checkCalls } from './src/llmAdapter.js';
const files = process.argv.slice(2);
const res = { equivalence: { checked: 0, differ: 0 }, inline: {}, calls: { total: 0, recorded_ag_failed: 0, recorded_ag_ok: 0, adapter_flagged_of_failed: 0, adapter_flagged_of_ok: 0 }, mismatches: [] };
const ajv = new Ajv({ allErrors: true, strict: false, logger: false, validateFormats: false });
for (const f of files) {
  const turns = fs.readFileSync(f, 'utf8').trim().split('\n').map((l) => JSON.parse(l));
  const outputs = new Map();
  for (const t of turns) for (const it of t.request.input || []) if (it.type === 'function_call_output') outputs.set(it.callId, JSON.parse(it.output));
  for (const t of turns) {
    for (const tool of t.request.tools || []) if (!res.inline[tool.name]) {
      const inl = inlineRefs(tool.parameters); const s = JSON.stringify(inl);
      res.inline[tool.name] = { refs_before: JSON.stringify(tool.parameters).split('"$ref"').length - 1, refs_after: s.split('"$ref"').length - 1,
                                defs_after: s.includes('"$defs"'), compiles: !!ajv.compile(inl), chars_after: s.length };
    }
    const toolMap = Object.fromEntries((t.request.tools || []).map((x) => [x.name, x.parameters]));
    for (const o of (t.response.output || []).filter((x) => x.type === 'function_call' && toolMap[x.name])) {
      let a; try { a = JSON.parse(o.arguments || '{}'); } catch { continue; }
      const v1 = ajv.compile(toolMap[o.name])(a), v2 = ajv.compile(inlineRefs(toolMap[o.name]))(a);
      res.equivalence.checked++; if (v1 !== v2) res.equivalence.differ++;
    }
    const bad = checkCalls(t.request, t.response);
    for (const o of (t.response.output || []).filter((x) => x.type === 'function_call')) {
      const ag = outputs.get(o.callId); if (!ag) continue;
      res.calls.total++;
      const flagged = bad.some((b) => b.call === o);
      if (ag.success === false) { res.calls.recorded_ag_failed++; if (flagged) res.calls.adapter_flagged_of_failed++; else res.mismatches.push({ f, tool: o.name, kind: 'AG failed, adapter passed', ag: JSON.stringify(ag).slice(0, 200) }); }
      else { res.calls.recorded_ag_ok++; if (flagged) { res.calls.adapter_flagged_of_ok++; res.mismatches.push({ f, tool: o.name, kind: 'AG ok, adapter flagged' }); } }
    }
  }
}
res.pass = res.equivalence.differ === 0 && res.mismatches.length === 0 && Object.values(res.inline).every((x) => x.refs_after === 0 || x.refs_after <= x.refs_before);
console.log(JSON.stringify(res, null, 1));
