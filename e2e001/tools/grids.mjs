// WO-COM-E2E-002: independent grid check in headless Microsoft Edge (puppeteer-core). Counts rendered AG Grid data rows
// per grid widget, reads their cell text and compares it with the backend dataset. Writes JSON + a screenshot.
import puppeteer from '../web/node_modules/puppeteer-core/lib/esm/puppeteer/puppeteer-core.js';
import fs from 'node:fs';
const [,, outDir, url = 'http://127.0.0.1:8790/'] = process.argv;
fs.mkdirSync(outDir, { recursive: true });
// puppeteer.launch() cannot start Edge in this host's sandbox; Edge is started separately (tools/edge_headless.ps1) and attached.
const browser = await puppeteer.connect({ browserURL: process.env.EDGE_CDP || 'http://127.0.0.1:9333', defaultViewport: { width: 1800, height: 2400 } });
const page = await browser.newPage();
const consoleLog = [];
page.on('console', (m) => consoleLog.push(`${m.type()}: ${m.text()}`.slice(0, 400)));
await page.goto(url, { waitUntil: 'networkidle0', timeout: 60000 });
const dataset = await (await fetch(url + 'api/run')).json();
const expected = Object.fromEntries(dataset.sources.map((s) => [s.id, s.data.length]));
let result;
for (let i = 0; i < 40; i++) {
  await new Promise((r) => setTimeout(r, 1000));
  result = await page.evaluate(() => {
    const grids = [...document.querySelectorAll('[role="treegrid"], [role="grid"]')].filter((g) => g.closest('.ag-root-wrapper')).map((g) => {
      const headers = [...g.querySelectorAll('[role="columnheader"]')].map((h) => h.textContent.trim()).filter(Boolean);
      const bodyRows = [...g.querySelectorAll('[role="row"]')].filter((r) => r.querySelector('[role="gridcell"]'));
      const rows = bodyRows.map((r) => [...r.querySelectorAll('[role="gridcell"]')].map((c) => c.textContent.trim()));
      const ariaRowCount = Number(g.getAttribute('aria-rowcount') || 0);
      const headerRows = g.querySelectorAll('[role="row"]').length - bodyRows.length;
      const loading = g.closest('.ag-root-wrapper').textContent.includes('Loading...');
      return { headers, ariaRowCount, headerRows, renderedRows: rows.length, rowCount: rows.length, rows: rows.slice(0, 40), loading };
    });
    return { grids, bodyLoading: document.body.innerText.split('Loading...').length - 1 };
  });
  if (result.grids.length >= 3 && result.grids.every((g) => g.rowCount > 0 && !g.loading)) break;
}
await page.screenshot({ path: `${outDir}/dashboard_headless_edge.png`, fullPage: true });
// Scroll pass: virtualised grids render only visible rows; scroll each grid body and union rows by aria-rowindex.
const all = await page.evaluate(async () => {
  const res = [];
  for (const g of [...document.querySelectorAll('[role="treegrid"], [role="grid"]')].filter((x) => x.closest('.ag-root-wrapper'))) {
    const root = g.closest('.ag-root-wrapper');
    const vps = [root.querySelector('.ag-body-vertical-scroll-viewport'), root.querySelector('.ag-body-viewport')].filter(Boolean);
    const vp = vps.find((v) => v.scrollHeight > v.clientHeight + 2) || vps[0];
    const rows = new Map();
    for (let step = 0; step < 60; step++) {
      for (const r of g.querySelectorAll('[role="row"]')) {
        const cells = [...r.querySelectorAll('[role="gridcell"]')].map((c) => c.textContent.trim());
        if (cells.length) rows.set(r.getAttribute('aria-rowindex'), cells);
      }
      if (!vp || vp.scrollTop + vp.clientHeight >= vp.scrollHeight - 2) break;
      vp.scrollTop += Math.max(40, vp.clientHeight - 40); vp.dispatchEvent(new Event('scroll')); await new Promise((r) => setTimeout(r, 400));
    }
    res.push([...rows.values()]);
  }
  return res;
});
all.forEach((rows, i) => { if (result.grids[i]) { result.grids[i].rows = rows; } });
// Cell-level verification: every rendered row must equal a dataset row on the grid's columns (header -> field name).
const srcFor = (headers) => dataset.sources.find((s) => s.data.length && headers.every((h) => Object.keys(s.data[0]).some((k) => k.toLowerCase() === h.toLowerCase().replace(/ /g, '_'))));
const norm = (v) => (v === null || v === undefined ? '' : typeof v === 'number' ? String(v) : String(v)).trim();
const verification = result.grids.map((g) => {
  const src = srcFor(g.headers);
  if (!src) return { headers: g.headers, source: null, verified_rows: 0, unmatched: g.rows.length };
  const keys = g.headers.map((h) => Object.keys(src.data[0]).find((k) => k.toLowerCase() === h.toLowerCase().replace(/ /g, '_')));
  let ok = 0; const unmatched = [];
  for (const r of g.rows) {
    const hit = src.data.some((d) => keys.every((k, i) => { const a = norm(d[k]), b = norm(r[i]); return a === b || (a && b && Number(a) === Number(b.replace(/,/g, ''))); }));
    if (hit) ok++; else unmatched.push(r.slice(0, 4));
  }
  return { headers: g.headers, source: src.id, dataset_rows: src.data.length, aria_rows: g.ariaRowCount - 1, rendered_rows: g.rows.length, verified_rows: ok, unmatched: unmatched.slice(0, 5) };
});
const out = { url, browser: await browser.version(), expected_rows: expected, verification, ...result, console: consoleLog.slice(-30), at: new Date().toISOString() };
fs.writeFileSync(`${outDir}/GRID_CHECK.json`, JSON.stringify(out, null, 1));
console.log(JSON.stringify({ verification }));
console.log(JSON.stringify({ grids: result.grids.map((g) => ({ headers: g.headers.slice(0, 4), aria_rowcount: g.ariaRowCount, rendered: g.renderedRows, first: (g.rows[0]||[]).slice(0,3), loading: g.loading })), expected }));
await page.close(); browser.disconnect();
