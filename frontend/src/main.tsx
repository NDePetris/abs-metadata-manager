import {useMemo, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';

type Change = {before: unknown; after: unknown};
type Item = {item_id: string; identity: string; reason?: string; changes: Record<string, Change>};
type Document = {schema_version: number; snapshot_id: string; scope: string; items: Item[]};
type Row = {item_id: string; field: string; before: unknown; current: unknown; after: unknown; title: string; kind: string; conflict: boolean; reason: string};
type Result = {item_id: string; fields: string[]; changes: Record<string, unknown>; state: string; message: string};
type Library = {id: string; name: string; mediaType: string};
const key = (r: Row) => `${r.item_id}/${r.field}`;
const show = (v: unknown) => typeof v === 'string' ? v : JSON.stringify(v, null, 2);

async function api(path: string, data?: unknown) {
  const response = await fetch(`/api/${path}`, {method: data === undefined ? 'GET' : 'POST', headers: {
    'Content-Type': 'application/json', 'X-Session-Token': document.querySelector<HTMLMetaElement>('meta[name=session-token]')!.content
  }, body: data === undefined ? undefined : JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || 'Request failed');
  return result;
}
function download(name: string, value: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], {type: 'application/json'}));
  const a = document.createElement('a'); a.href = url; a.download = name; a.click(); URL.revokeObjectURL(url);
}
function App() {
  const [server, setServer] = useState(''); const [token, setToken] = useState('');
  const [connectedServer, setConnectedServer] = useState('');
  const [libraries, setLibraries] = useState<Library[]>([]); const [library, setLibrary] = useState('');
  const [doc, setDoc] = useState<Document>(); const [rows, setRows] = useState<Row[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [edits, setEdits] = useState<Record<string, unknown>>({});
  const [query, setQuery] = useState(''); const [page, setPage] = useState(0);
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [message, setMessage] = useState('');
  const [progress, setProgress] = useState({done: 0, total: 0}); const [results, setResults] = useState<Result[]>([]);
  const [preview, setPreview] = useState<{digest: string; phrase: string; rows: Row[]; server: string; library_id: string}>(); const [phrase, setPhrase] = useState('');
  const visible = useMemo(() => rows.filter(r => `${r.title} ${r.field}`.toLowerCase().includes(query.toLowerCase())), [rows, query]);
  const status = (r: Row) => results.findLast(x => x.item_id === r.item_id && x.fields.includes(r.field) && JSON.stringify(x.changes[r.field]) === JSON.stringify(Object.hasOwn(edits, key(r)) ? edits[key(r)] : r.after));
  const eligible = (r: Row) => !r.conflict && status(r)?.state !== 'verified';
  const picked = rows.filter(r => selected.has(key(r)) && eligible(r));
  function proposal(chosen: Row[]): Document {
    const items = new Map<string, Item>();
    for (const r of chosen) {
      if (!items.has(r.item_id)) { const original = doc!.items.find(i => i.item_id === r.item_id)!; items.set(r.item_id, {...original, changes: {}}); }
      items.get(r.item_id)!.changes[r.field] = {before: r.before, after: Object.hasOwn(edits, key(r)) ? edits[key(r)] : r.after};
    }
    return {...doc!, items: [...items.values()]};
  }
  async function task(work: () => Promise<void>) {
    setBusy(true); setError('');
    try { await work(); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function job(path: string, data: unknown) {
    await api(path, data); setProgress({done: 0, total: 0});
    for (;;) {
      const j = await api('job'); setProgress(j); setMessage(typeof j.message === 'string' ? j.message : '');
      if (j.results) setResults(j.results);
      if (!j.running) { if (j.error) throw new Error(j.error); setMessage('Operation completed'); return j.result; }
      await new Promise(resolve => setTimeout(resolve, 400));
    }
  }
  function toggle(r: Row) { setSelected(old => { const next = new Set(old); next.has(key(r)) ? next.delete(key(r)) : next.add(key(r)); return next; }); }
  return <main>
    <header><h1>Bulk metadata</h1><p>Export your library, review proposed corrections, then apply selected changes.</p></header>
    <form onSubmit={e => {e.preventDefault(); task(async () => {
      const info = await job('connect', {server, token}); setToken(''); setConnectedServer(info.server || server); setLibraries(info.libraries.filter((l: Library) => l.mediaType === 'book'));
      setLibrary(info.libraries.find((l: Library) => l.mediaType === 'book')?.id || ''); setMessage(info.warnings.join(' ') || 'Connected');
      setRows([]); setSelected(new Set()); setResults([]); setDoc(undefined);
    });}} className="connection">
      <label>ABS server URL<input required type="url" value={server} onChange={e => setServer(e.target.value)} placeholder="http://your-server:13378" /></label>
      <label>API key<input required type="password" value={token} onChange={e => setToken(e.target.value)} autoComplete="off" /></label>
      <button disabled={busy}>Connect</button>
    </form>
    {connectedServer && <p>Connected to <strong>{connectedServer}</strong></p>}
    <section className="toolbar" aria-label="Library and files">
      <label>Library<select disabled={busy || !libraries.length} value={library} onChange={e => {setLibrary(e.target.value); setRows([]); setDoc(undefined); setSelected(new Set());}}>
        {!libraries.length && <option value="">Connect first</option>}{libraries.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
      </select></label>
      <button disabled={busy || !library} onClick={() => task(async () => download('abs-library.json', await job('export', {library_id: library})))}>Export JSON</button>
      <label className="upload">Import corrections JSON<input type="file" accept=".json,application/json" disabled={busy || !library} onChange={e => {
        const file = e.target.files?.[0]; e.target.value = ''; if (!file) return;
        task(async () => {const text = await file.text();
          // Preserve duplicate-key rejection: import the original text rather than reparsing before transport.
          const response = await fetch('/api/review?library_id=' + encodeURIComponent(library), {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Session-Token': document.querySelector<HTMLMetaElement>('meta[name=session-token]')!.content}, body: text});
          const data = await response.json(); if (!response.ok) throw new Error(data.detail);
          setDoc(JSON.parse(text)); setRows(data); setEdits({}); setSelected(new Set()); setResults([]); setPage(0); setQuery(''); setMessage(`${data.length} field differences imported`);
        });
      }} /></label>
      <button disabled={busy || !library} onClick={() => task(async () => setResults(await job('check', {})))}>Check ABS results</button>
    </section>
    {error && <p role="alert" className="error">{error}</p>}
    <p role="status">{busy ? 'Working… ' : ''}{message}</p>
    {busy && <progress max={progress.total || 1} value={progress.done} aria-label="Operation progress" />}
    {rows.length > 0 ? <>
      <div className="toolbar"><label>Filter books or fields<input value={query} onChange={e => {setQuery(e.target.value); setPage(0);}} /></label>
        <button disabled={busy} onClick={() => setSelected(new Set(visible.filter(eligible).map(key)))}>Select eligible filtered fields</button>
        <button disabled={busy} onClick={() => setSelected(new Set())}>Clear selection</button><span>{picked.length} fields selected · {visible.length} differences</span></div>
      <div className="table-wrap"><table><thead><tr><th>Select</th><th>Book / field</th><th>Old value</th><th>New value</th><th>Status</th></tr></thead><tbody>
        {visible.slice(page * 25, (page + 1) * 25).map(r => <tr key={key(r)}>
          <td><input aria-label={`Select ${r.title} ${r.field}`} type="checkbox" disabled={busy || !eligible(r)} checked={selected.has(key(r)) && eligible(r)} onChange={() => toggle(r)} /></td>
          <td><strong>{r.title}</strong><br />{r.field}<br /><small>{r.kind}</small>{r.reason && <details><summary>Reason</summary>{r.reason}</details>}</td>
          <td><pre>{show(r.current)}</pre>{r.conflict && <small>Imported before value: {show(r.before)}</small>}</td><td><pre>{show(Object.hasOwn(edits, key(r)) ? edits[key(r)] : r.after)}</pre>
            <details><summary>Edit value</summary><ValueEditor value={Object.hasOwn(edits, key(r)) ? edits[key(r)] : r.after} disabled={busy} onSave={v => setEdits({...edits, [key(r)]: v})} /></details></td>
          <td>{r.conflict ? 'Conflict: before differs from export' : status(r)?.state || 'Ready for review'}</td>
        </tr>)}
      </tbody></table></div>
      <div className="toolbar"><button disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button><span>Page {page + 1} of {Math.max(1, Math.ceil(visible.length / 25))}</span><button disabled={(page + 1) * 25 >= visible.length} onClick={() => setPage(page + 1)}>Next</button></div>
      <div className="toolbar">
        <button disabled={busy || !picked.some(r => r.kind === 'audiobook')} onClick={() => task(async () => {setPhrase(''); setPreview(await api('preview', proposal(picked.filter(r => r.kind === 'audiobook'))));})}>Review audiobook update</button>
        <button disabled={busy || !picked.some(r => r.kind === 'ebook')} onClick={() => task(async () => download('calibre-corrections.json', await api('ebook-report', proposal(picked.filter(r => r.kind === 'ebook')))))}>Download Calibre report</button>
        <button disabled={busy || !doc} onClick={() => download('abs-corrections.json', proposal(rows))}>Save edited proposals</button>
      </div>
    </> : <p className="empty">Export a complete library, then import corrections in the documented JSON format. Importing never writes to ABS.</p>}
    {results.length > 0 && <section><h2>Results</h2><ul>{results.map((r, i) => <li key={i}>{r.item_id}: <strong>{r.state}</strong> — {r.message}</li>)}</ul></section>}
    {preview && <div className="overlay"><section role="dialog" aria-modal="true" aria-labelledby="confirm-title"><h2 id="confirm-title">Confirm audiobook changes</h2>
      <p>Server: {preview.server}<br />Library: {libraries.find(l => l.id === preview.library_id)?.name} ({preview.library_id}). Only the fields below will be submitted to ABS.</p>
      <div className="confirmation-values">{preview.rows.map(r => <div key={key(r)}><strong>{r.title} / {r.field}</strong><small> · {r.item_id}</small><pre>{show(r.before)} → {show(r.after)}</pre></div>)}</div>
      <label>Type {preview.phrase}<input autoFocus value={phrase} onChange={e => setPhrase(e.target.value)} /></label>
      <div className="toolbar"><button onClick={() => setPreview(undefined)}>Cancel</button><button disabled={phrase !== preview.phrase || busy} onClick={() => {
        const p = preview; setPreview(undefined); task(async () => setResults(await job('apply', {digest: p.digest, confirmation: phrase})));
      }}>Apply confirmed changes</button></div>
    </section></div>}
    <footer>Ebooks: manual Calibre reports only. Keep the original export and private local snapshot together. See docs/BULK.md.</footer>
  </main>;
}
function ValueEditor({value, disabled, onSave}: {value: unknown; disabled: boolean; onSave: (v: unknown) => void}) {
  const [text, setText] = useState(JSON.stringify(value, null, 2)); const [error, setError] = useState('');
  return <div><label>JSON value<textarea aria-label="JSON value" value={text} disabled={disabled} onChange={e => setText(e.target.value)} /></label><button disabled={disabled} onClick={() => {try {onSave(JSON.parse(text)); setError('');} catch {setError('Enter a valid JSON value. Text needs double quotes.');}}}>Save value</button>{error && <p role="alert">{error}</p>}</div>;
}
createRoot(document.getElementById('root')!).render(<App />);
