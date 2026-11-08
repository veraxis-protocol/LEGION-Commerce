// WO-COM-E2E-002: drive one AG Studio AI chat in headless Edge and capture observable evidence.
//   node tools/chat.mjs <outDir> <sessionName> <mode nim|script> <promptFile> [scriptFile] [maxSeconds]
// Opens the dashboard in a fresh tab, sets the backend journal session (and scripted responses, if any), types the
// prompt into AG Studio's chat box, waits until the conversation is idle, and writes: chat transcript (DOM text of the
// assistant panel), the Studio report state, the widget health, a screenshot, and the console. Never edits answers.
import puppeteer from '../web/node_modules/puppeteer-core/lib/esm/puppeteer/puppeteer-core.js';
import fs from 'node:fs';

const [,, outDir, session, mode, promptFile, scriptFile, maxSecondsArg] = process.argv;
const URL = 'http://127.0.0.1:8790/';
const maxSeconds = Number(maxSecondsArg || 240);
fs.mkdirSync(outDir, { recursive: true });
const prompt = fs.readFileSync(promptFile, 'utf8').trim();
const responses = scriptFile && scriptFile !== '-' ? JSON.parse(fs.readFileSync(scriptFile, 'utf8')) : [];
const journal = `${process.cwd()}/ai_turns/${session}.jsonl`;
if (fs.existsSync(journal)) throw new Error(`journal ${journal} exists: sessions are never overwritten`);

await fetch(URL + 'api/llm/session', { method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ name: session, mode, responses }) });
const browser = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9333', defaultViewport: { width: 1800, height: 1400 } });
const page = await browser.newPage();
const consoleLog = [];
page.on('console', (m) => consoleLog.push(`${m.type()}: ${m.text()}`.slice(0, 500)));
page.on('pageerror', (e) => consoleLog.push(`pageerror: ${e.message}`.slice(0, 500)));
await page.goto(URL, { waitUntil: 'networkidle0', timeout: 60000 });
await page.waitForSelector('textarea[placeholder="Ask the AI Assistant..."], [placeholder="Ask the AI Assistant..."]', { timeout: 30000 });
const box = await page.$('[placeholder="Ask the AI Assistant..."]');
await box.click();
await box.type(prompt);
await page.keyboard.press('Enter');
const t0 = Date.now();
const lines = () => (fs.existsSync(journal) ? fs.readFileSync(journal, 'utf8').trim().split('\n').filter(Boolean).length : 0);
let last = -1, stableSince = Date.now(), outcome = 'TIMEOUT';
while ((Date.now() - t0) / 1000 < maxSeconds) {
  await new Promise((r) => setTimeout(r, 2000));
  const n = lines();
  // Busy = AG Studio's chat panel shows a running step ("Thinking…", "Running") or its Stop control (E2E-002 fix: the
  // first version declared idle while the panel still showed "Thinking… / Stop").
  const busy = await page.evaluate(() => {
    const input = document.querySelector('[placeholder="Ask the AI Assistant..."]');
    let el = input; for (let i = 0; i < 8 && el; i++) { el = el.parentElement; if (el && el.innerText.includes('AI Assistant')) break; }
    const t = (el || document.body).innerText;
    return /Thinking…|\nRunning\n|\nStop\n/.test('\n' + t + '\n');
  });
  if (n !== last) { last = n; stableSince = Date.now(); }
  if (n > 0 && !busy && Date.now() - stableSince > 6000) { outcome = 'IDLE'; break; }
}
const panelText = await page.evaluate(() => {
  const input = document.querySelector('[placeholder="Ask the AI Assistant..."]');
  let el = input; for (let i = 0; i < 8 && el; i++) { el = el.parentElement; if (el && el.innerText.includes('AI Assistant')) break; }
  return (el || document.body).innerText;
});
const studio = await page.evaluate(async () => {
  const api = window.__studio && window.__studio.current && window.__studio.current.api;
  if (!api) return null;
  const state = api.getState();
  const T = api.getAiTools();
  const ctx = { signal: new AbortController().signal, run: { threadId: 'verify', runId: 'verify' },
    success: (r, d) => ({ success: true, response: r, data: d }), error: (m) => ({ success: false, issues: [].concat(m).map((x) => ({ message: x })) }) };
  const health = {};
  for (const pg of state.pages) for (const id of Object.keys(pg.widgets || {})) {
    const v = await T.viewWidget({ pageId: pg.id }).execute({ toolCallId: 'v', name: 'viewWidget', args: { widgetId: id } }, ctx);
    health[id] = v.success ? (v.data.issues || []).map((i) => ({ code: i.code, score: i.score, description: i.description })) : v.issues;
  }
  return { state, health };
});
const domWidgetText = await page.evaluate(() => {
  const input = document.querySelector('[placeholder="Ask the AI Assistant..."]');
  let chatPanel = input; for (let i = 0; i < 8 && chatPanel; i++) { chatPanel = chatPanel.parentElement; if (chatPanel && chatPanel.innerText.includes('AI Assistant')) break; }
  const leaves = [...document.querySelectorAll('body *')].filter((e) => e.children.length === 0 && e.textContent.trim()
    && !e.closest('.ag-root-wrapper') && !(chatPanel && chatPanel.contains(e)));
  return leaves.map((e) => e.textContent.trim()).join(' | ');
});
await page.screenshot({ path: `${outDir}/screenshot.png`, fullPage: false });
const out = { session, mode, prompt, outcome, dom_widget_text: domWidgetText, seconds: Math.round((Date.now() - t0) / 1000), journal_turns: lines(), panelText, studio,
  console: consoleLog.slice(-40), at: new Date().toISOString() };
fs.writeFileSync(`${outDir}/CHAT_RESULT.json`, JSON.stringify(out, null, 1));
console.log(JSON.stringify({ session, outcome, seconds: out.seconds, turns: out.journal_turns, panelTail: panelText.slice(-600) }));
await page.close(); browser.disconnect();
