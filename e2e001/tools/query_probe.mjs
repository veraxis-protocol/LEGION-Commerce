// WO-COM-E2E-002: execute candidate execute_query arguments with AG Studio's own tool (headless Edge), to verify that a
// guidance example is genuinely valid and returns the expected rows before it is shown to the model.
import puppeteer from '../web/node_modules/puppeteer-core/lib/esm/puppeteer/puppeteer-core.js';
import fs from 'node:fs';
const [,, argsFile, outFile] = process.argv;
const cases = JSON.parse(fs.readFileSync(argsFile, 'utf8'));
const b = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9333', defaultViewport: { width: 1800, height: 1400 } });
const p = await b.newPage(); await p.goto('http://127.0.0.1:8790/', { waitUntil: 'networkidle0' }); await new Promise((r) => setTimeout(r, 3000));
const out = await p.evaluate(async (cases) => {
  const T = window.__studio.current.api.getAiTools();
  const ctx = { signal: new AbortController().signal, run: { threadId: 'probe', runId: 'probe' }, success: (r, d) => ({ success: true, response: r, data: d }),
    error: (m) => ({ success: false, issues: [].concat(m).map((x) => ({ message: x })) }) };
  const res = {};
  for (const [name, args] of Object.entries(cases)) res[name] = await T.executeQuery().execute({ toolCallId: name, name: 'executeQuery', args }, ctx);
  return res;
}, cases);
fs.writeFileSync(outFile, JSON.stringify(out, null, 1));
for (const [k, v] of Object.entries(out)) console.log(k, v.success, v.success ? JSON.stringify(v.data && v.data.result).slice(0, 200) : JSON.stringify(v.issues).slice(0, 300));
await p.close(); b.disconnect();
