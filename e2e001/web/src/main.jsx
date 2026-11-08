import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { AgStudio, AgStudioProvider } from 'ag-studio-react';
import { AgStudioAiModule, createAiHarness } from 'ag-studio';
import { legionAdapter } from './llmAdapter';
import dashboardState from './dashboardState.json';

const adapter = legionAdapter();
const starters = [
  { label: 'Offers by seller', prompt: 'Add a bar chart of offer price by seller from the offers data, and a grid of all offers with title, seller, price, availability and status.' },
  { label: 'Admission trail', prompt: 'Add a grid of the events data (seq, kind, agent, detail) and a KPI tile for the final price from the summary data.' },
  { label: 'Test results', prompt: 'Add a pie chart counting checks by result and a grid of the checks data.' },
];

function Banner({ run }) {
  const pill = (t, bg) => <span style={{ background: bg, color: '#fff', borderRadius: 4, padding: '2px 8px', marginRight: 8, fontSize: 12 }}>{t}</span>;
  if (!run) return <div style={{ padding: 12 }}>Loading run...</div>;
  return (
    <div style={{ padding: '10px 14px', borderBottom: '1px solid #ddd', background: '#fafafa', fontSize: 13 }}>
      <strong style={{ fontSize: 15, marginRight: 12 }}>LEGION Commerce E2E</strong>
      {pill(run.run, '#555')}{pill(`Judge ${run.verdict}`, run.verdict.startsWith('PASS') ? '#2e7d32' : '#c62828')}
      {pill(`Lean ${run.lean_verdict}`, '#1565c0')}{pill(run.evidence_mode, '#ef6c00')}
      <span>Final: {run.final.title} from {run.final.seller}, ${(run.final.price_cents / 100).toFixed(2)} (cap $500). Changed offer: {run.changed_offer.change_type} {run.changed_offer.offer_key}, ${run.changed_offer.old_price} to ${run.changed_offer.new_price}. </span>
      <em>No native PayPal payment: the payment leg is MECHANISM_ONLY (synthetic provider) until A03 is independently signed.</em>
    </div>
  );
}

function App() {
  const [run, setRun] = useState(null);
  const [license, setLicense] = useState(null);
  const ref = useRef(null);
  useEffect(() => {
    fetch('/api/run').then((r) => r.json()).then(setRun);
    fetch('/api/ag-license').then((r) => r.json()).then((j) => setLicense(j.licenseKey));
  }, []);
  useEffect(() => { window.__studio = ref; }, []);
  const data = React.useMemo(() => (run ? { sources: run.sources } : null), [run]);
  if (!run || !license) return <Banner run={run} />;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      <Banner run={run} />
      <div style={{ flex: 1, minHeight: 0, position: 'relative' }}><div style={{ position: 'absolute', inset: 0 }}>
        <AgStudioProvider licenseKey={license} modules={[AgStudioAiModule]}>
          <AgStudio ref={ref} data={data} initialState={dashboardState} mode="edit"
            ai={(params) => createAiHarness(params.api, { adapter, promptStarters: starters })} />
        </AgStudioProvider>
      </div></div>
    </div>
  );
}

createRoot(document.getElementById('root')).render(<App />);
