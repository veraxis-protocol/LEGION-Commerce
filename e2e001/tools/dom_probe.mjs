// WO-COM-E2E-003: read-only exploration of the AG Studio API surface and widget DOM (no state change).
import puppeteer from '../web/node_modules/puppeteer-core/lib/esm/puppeteer/puppeteer-core.js';
const browser = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9333', defaultViewport: { width: 1800, height: 1400 } });
const page = await browser.newPage();
await page.goto('http://127.0.0.1:8790/', { waitUntil: 'networkidle0', timeout: 60000 });
await new Promise((r) => setTimeout(r, 4000));
const out = await page.evaluate(() => {
  const api = window.__studio.current.api;
  const T = api.getAiTools();
  const proto = Object.getOwnPropertyNames(Object.getPrototypeOf(api)).concat(Object.keys(api));
  const w = document.querySelectorAll('[data-widget-id], [data-id], [widget-id]');
  const attrs = new Set();
  document.querySelectorAll('*').forEach((e) => { for (const a of e.attributes) if (/widget/i.test(a.name) || /widget/i.test(a.value)) attrs.add(a.name + '=' + a.value.slice(0, 60)); });
  return { tools: Object.keys(T), api: proto, widgetEls: w.length, attrs: [...attrs].slice(0, 60), state: api.getState().pages[0].widgets.pie_checks };
});
console.log(JSON.stringify(out, null, 1));
await page.close(); browser.disconnect();
