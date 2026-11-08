// WO-COM-E2E-003: read-only observation of AG Studio widget state and rendering, used by the adapter for
//  (a) tool feedback: after a widget-changing tool call, the model is told the widget's ACTUAL stored configuration, its
//      filters, AG Studio's fresh health issues and the value it renders, plus a plain-language statement of what it computes;
//  (b) the completion gate: a task contract (from the backend, with ground truth computed server-side from the dataset)
//      is checked against the observed state and the rendered value before a completion is accepted.
// Nothing here changes Studio state (only viewWidget, a read-only AG tool, getState and DOM reads are used).

const studioApi = () => window.__studio && window.__studio.current && window.__studio.current.api;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function toolCtx() {
  return { signal: new AbortController().signal, run: { threadId: 'adapter-observe', runId: 'adapter-observe' },
    success: (r, d) => ({ success: true, response: r, data: d }),
    error: (m) => ({ success: false, issues: [].concat(m).map((x) => ({ message: String(x) })) }) };
}

function locate(state, widgetId) {
  for (const p of state.pages || []) if ((p.widgets || {})[widgetId]) return p;
  return null;
}

// The rendered element of a widget: AG Studio positions each layout widget absolutely with inline left/top in px,
// top = yTrack * rowHeight and left = xTrack * (content width / columns) (verified in E2E-003 DOM probe).
export function widgetElement(state, widgetId) {
  const p = locate(state, widgetId);
  const pos = p && (p.widgetLayout || {})[widgetId];
  if (!pos) return null;
  const content = document.querySelector('.ag-studio-layout-content-container');
  if (!content) return null;
  const cols = (p.layout && p.layout.columns) || 12;
  const rowH = (p.layout && p.layout.rowHeight) || 80;
  const colW = content.clientWidth / cols;
  return [...content.querySelectorAll(':scope > .ag-studio-layout-widget')].find((e) =>
    Math.abs(parseFloat(e.style.top) - pos.yTrack * rowH) < 2 && Math.abs(parseFloat(e.style.left) - pos.xTrack * colW) < 4) || null;
}

export function renderedValue(state, widgetId) {
  const el = widgetElement(state, widgetId);
  if (!el) return { found: false };
  el.scrollIntoView({ block: 'center' });
  const v = el.querySelector('.ag-studio-value-widget-content');
  const status = el.querySelector('.ag-studio-widget-invalid-configuration') && el.querySelector('.ag-studio-widget-invalid-configuration').offsetParent ? 'invalid-configuration'
    : el.querySelector('.ag-studio-widget-no-data') && el.querySelector('.ag-studio-widget-no-data').offsetParent ? 'no-data' : 'rendered';
  return { found: true, status, value_text: v ? v.textContent.trim() : null, text: el.innerText.replace(/\s+/g, ' ').trim().slice(0, 300) };
}

const fmtField = (f) => (f == null ? 'EMPTY' : typeof f !== 'object' ? String(f) : f.aggregation ? `${f.aggregation}(${f.id})` : String(f.id));
// dataMapping entries can be arrays with empty slots or plain values (seen right after configure_widget); keep only field refs.
const fieldRefs = (v) => [].concat(v).filter((f) => f && typeof f === 'object' && f.id);
function fmtFilter(f) {
  const m = f.model || {};
  if (m.conditions) return `(${m.conditions.map((c) => fmtFilter({ field: f.field, model: c })).join(` ${m.operator} `)})`;
  return `${f.field && f.field.id} ${m.operator}${m.value !== undefined ? ' ' + JSON.stringify(m.value) : ''}`;
}

export function filtersOf(state, widgetId) {
  const p = locate(state, widgetId) || {};
  const flt = p.filter || {};
  return { widget: ((flt.widget || {})[widgetId]) || [], page: Array.isArray(flt.page) ? flt.page : (flt.page ? [flt.page] : []) };
}

// Plain-language statement of what the widget computes from its STORED state (not from what the model intended).
export function semantics(widget, filters) {
  if (!widget) return 'widget does not exist';
  const dm = widget.dataMapping || {};
  const parts = Object.entries(dm).filter(([, v]) => fieldRefs(v).length).map(([k, v]) => `${k} = ${fieldRefs(v).map(fmtField).join(', ')}`);
  // raw form is reported too, so an odd stored mapping is visible to the model as is
  const sources = [...new Set(Object.values(dm).flatMap(fieldRefs).map((f) => String(f.id).split('.')[0]))];
  const all = [...filters.widget, ...filters.page];
  const where = all.length ? `only rows where ${all.map(fmtFilter).join(' AND ')}` : 'ALL rows (no widget or page filter is applied)';
  if (!parts.length) return `${widget.type} widget with NO data mapping: it computes nothing yet`;
  return `${widget.type} widget: ${parts.join('; ')}; computed over ${sources.join(', ')} ${where}`;
}

export async function observeWidget(widgetId, { settleMs = 1500 } = {}) {
  const api = studioApi();
  if (!api) return { widgetId, error: 'studio api unavailable' };
  await sleep(settleMs);
  const state = api.getState();
  const p = locate(state, widgetId);
  if (!p) return { widgetId, exists: false, note: 'no widget with this id exists in the Studio state' };
  const widget = p.widgets[widgetId];
  const filters = filtersOf(state, widgetId);
  let health = null;
  try {
    const v = await api.getAiTools().viewWidget({ pageId: p.id }).execute({ toolCallId: 'observe', name: 'viewWidget', args: { widgetId } }, toolCtx());
    health = v.success ? (v.data.issues || []).filter((i) => (i.score ?? 1) < 1).map((i) => ({ code: i.code, score: i.score, description: i.description })) : v.issues;
  } catch (e) { health = [{ error: String(e) }]; }
  let rendered = renderedValue(state, widgetId);
  for (let i = 0; i < 6 && rendered.found && widget.type === 'value' && !rendered.value_text; i++) { await sleep(500); rendered = renderedValue(api.getState(), widgetId); }
  return { widgetId, exists: true, pageId: p.id, type: widget.type, dataMapping: widget.dataMapping || null, filters,
    semantics: semantics(widget, filters), fresh_health_issues: health, rendered };
}

// Completion gate: contract = {source, widget_types, predicate:{field, operator, value}, ground_truth:{count}} from the
// backend. Returns {pass, candidates:[...observations with reasons]}.
export async function verifyContract(contract, baseWidgets) {
  const api = studioApi();
  const state = api.getState();
  const ids = state.pages.flatMap((p) => Object.keys(p.widgets || {})).filter((id) => !baseWidgets.has(id));
  const candidates = [];
  for (const id of ids) {
    const o = await observeWidget(id, { settleMs: 300 });
    const reasons = [];
    if (!contract.widget_types.includes(o.type)) reasons.push(`type ${o.type} is not one of ${contract.widget_types.join(', ')}`);
    const mapped = JSON.stringify(o.dataMapping || {}).includes(`"${contract.source}.`);
    if (!mapped) reasons.push(`its data mapping does not use the ${contract.source} data`);
    const pr = contract.predicate;
    const all = [...o.filters.widget, ...o.filters.page];
    const hasPred = all.some((f) => f.field && f.field.id === pr.field && f.model && (
      (f.model.operator === 'equals' && f.model.value === pr.value) ||
      (f.model.operator === 'isIn' && Array.isArray(f.model.value) && f.model.value.length === 1 && f.model.value[0] === pr.value)));
    if (!hasPred) reasons.push(`the required predicate ${pr.field} ${pr.operator} ${JSON.stringify(pr.value)} is NOT present in the widget's stored filters (stored filters: ${all.length ? all.map(fmtFilter).join(' AND ') : 'none'})`);
    const shown = o.rendered && o.rendered.value_text != null ? Number(String(o.rendered.value_text).replace(/[^0-9.\-]/g, '')) : null;
    if (shown === null || Number.isNaN(shown)) reasons.push(`no rendered value could be read (render status: ${o.rendered && o.rendered.status})`);
    else if (shown !== contract.ground_truth.count) reasons.push(`the displayed value ${o.rendered.value_text} does not match the independently computed ground truth`);
    candidates.push({ ...o, displayed_number: shown, reasons, pass: reasons.length === 0 });
  }
  return { pass: candidates.some((c) => c.pass), candidates, checked_at: new Date().toISOString() };
}

// Read-only diagnostic hook (used by tools/*probe*.mjs); exposes no state-changing function.
if (typeof window !== 'undefined') window.__legionObserve = { observeWidget, verifyContract, semantics, filtersOf, renderedValue };
