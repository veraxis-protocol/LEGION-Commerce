// WO-COM-E2E-003 step 3: verify, BEFORE touching the agent, how a PASS predicate is applied to a KPI widget in AG Studio.
// Uses only AG Studio's own AI tools (the same functions the agents call), on the mixed-status fixture (30 PASS + 1 FAIL).
//   node tools/predicate_probe.mjs <outDir>
// Records, after every step: tool result, widget state (getState), viewWidget config/filters/issues, and the widget's
// rendered DOM text. No model is involved.
import puppeteer from '../web/node_modules/puppeteer-core/lib/esm/puppeteer/puppeteer-core.js';
import fs from 'node:fs';
const [,, outDir] = process.argv;
fs.mkdirSync(outDir, { recursive: true });
const browser = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9333', defaultViewport: { width: 1800, height: 1400 } });
const page = await browser.newPage();
await page.goto('http://127.0.0.1:8790/', { waitUntil: 'networkidle0', timeout: 60000 });
await new Promise((r) => setTimeout(r, 4000));
const dataset = await (await fetch('http://127.0.0.1:8790/api/run')).json();
const steps = [
  ['addWidget', { widgetId: 'probe_kpi', type: 'value', xTrack: 0, yTrack: 16, xSpan: 4, ySpan: 3 }],
  ['configureWidget', { widgetId: 'probe_kpi', op: 'patch', value: { type: 'value', dataMapping: { value: [{ id: 'checks.result', aggregation: 'count' }] } } }],
  ['addWidgetFilter', { widgetId: 'probe_kpi', op: 'append', value: { field: { id: 'checks.result' }, model: { operator: 'equals', value: 'PASS' } } }],
  ['removeWidgetFilter', { widgetId: 'probe_kpi', op: 'remove', index: 0 }],
  ['addWidgetFilter', { widgetId: 'probe_kpi', op: 'append', value: { field: { id: 'checks.result' }, model: { operator: 'isIn', value: ['PASS'] } } }],
];
const log = [];
for (const [name, args] of steps) {
  const r = await page.evaluate(async (name, args) => {
    const api = window.__studio.current.api;
    const pageId = api.getState().pages[0].id;
    const ctx = { signal: new AbortController().signal, run: { threadId: 'probe', runId: 'probe' },
      success: (r, d) => ({ success: true, response: r, data: d }), error: (m) => ({ success: false, issues: [].concat(m).map((x) => ({ message: String(x) })) }) };
    const T = api.getAiTools();
    const res = await T[name]({ pageId, widgetId: 'probe_kpi', widgetType: 'value' }).execute({ toolCallId: 'p', name, args }, ctx);
    await new Promise((r) => setTimeout(r, 2500));
    const v = await T.viewWidget({ pageId }).execute({ toolCallId: 'v', name: 'viewWidget', args: { widgetId: 'probe_kpi' } }, ctx);
    const valueEls = [...document.querySelectorAll('.ag-studio-layout-widget')].filter((e) => e.querySelector('[class*="value-widget"], [class*="ValueWidget"]'));
    return { tool_result: res, widget_state: api.getState().pages[0].widgets.probe_kpi, view_widget: v,
      value_widget_dom: valueEls.map((e) => e.innerText.replace(/\s+/g, ' ').trim()), value_classes: [...new Set([...document.querySelectorAll('[class*="value"]')].map((e) => e.className))].slice(0, 12) };
  }, name, args);
  log.push({ step: name, args, ...r });
  console.log(name, JSON.stringify(r.tool_result).slice(0, 200), '| DOM:', JSON.stringify(r.value_widget_dom));
}
await page.screenshot({ path: `${outDir}/probe.png` });
const rows = dataset.sources.find((s) => s.id === 'checks').data;
fs.writeFileSync(`${outDir}/PREDICATE_PROBE.json`, JSON.stringify({ fixture: dataset.fixture, ground_truth: { all: rows.length, pass: rows.filter((r) => r.result === 'PASS').length }, steps: log }, null, 1));
await page.close(); browser.disconnect();
