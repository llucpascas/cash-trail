import { useRef, useState } from 'react';
import { ArrowRight, Check, FileSpreadsheet, Upload, AlertCircle, ShieldCheck } from 'lucide-react';
import { api, money, type Preview, type Settings } from './types';

type Props={settings:Settings;onDone:(message:string)=>void;onAccount:()=>void};
export default function ImportWizard({settings,onDone,onAccount}:Props){
 const [account,setAccount]=useState(settings.accounts.find(a=>a.bank!=='Manual'&&a.bank!=='Original workbook')?.id || settings.accounts[0]?.id || 0);
 const [file,setFile]=useState<File|null>(null);
 const [result,setResult]=useState<Preview|null>(null);
 const [options,setOptions]=useState<Record<string,unknown>>({date_order:'day-first',decimal:'auto'});
 const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [dirty,setDirty]=useState(false);
 const [start,setStart]=useState('');const [end,setEnd]=useState('');
 const fileInput=useRef<HTMLInputElement>(null);
 const selected=settings.accounts.find(a=>a.id===account);
 function option(key:string,value:unknown){setOptions(p=>({...p,[key]:value}));setDirty(true)}
 async function inspect(chosen=file, config=options){
  if(!chosen)return;setBusy(true);setError('');
  try{const body=new FormData();body.append('file',chosen);body.append('options',JSON.stringify({...config,account_id:account}));
   const data=await api<Preview>('/api/imports/preview',{method:'POST',body});setResult(data);setOptions({...config,mapping:data.mapping,header:data.header,sheet:data.sheet});setDirty(false);
  }catch(e){setError((e as Error).message);setResult(null)}finally{setBusy(false)}
 }
 async function commit(){if(!result)return;setBusy(true);setError('');try{
  const answer=await api<{imported:number;duplicates:number}>('/api/imports/commit',{method:'POST',body:JSON.stringify({token:result.token,coverage_start:start||null,coverage_end:end||null})});
  onDone(`${answer.imported} transactions imported for review. ${answer.duplicates} duplicates skipped.`);
 }catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 const mapping=(options.mapping||{}) as Record<string,number>;
 return <div className="import-wizard">
  <div className="steps"><span className="done"><b>1</b> Choose statement</span><ArrowRight size={14}/><span className={result?'done':''}><b>2</b> Preview & check</span><ArrowRight size={14}/><span><b>3</b> Review transactions</span></div>
  <div className="import-context"><label>Bank account<select value={account} disabled={busy} onChange={e=>{const id=Number(e.target.value);setAccount(id);setResult(null);setFile(null);setOptions({date_order:'day-first',decimal:'auto',...settings.accounts.find(a=>a.id===id)?.profile});if(fileInput.current)fileInput.current.value=''}}>{settings.accounts.map(a=><option key={a.id} value={a.id}>{a.name} · {a.currency}</option>)}</select></label><button type="button" className="secondary" onClick={onAccount}>Add an account</button></div>
  <p className="muted">Download a transaction statement from your bank, then choose it here. Each bank can use its own column layout. {selected?.currency} is used when the file has no currency column.</p>
  <div className="dropzone"><FileSpreadsheet size={30}/><h3>{file?.name||'Bring your bank statement'}</h3><p>Excel .xlsx · CSV · bank HTML .xls · up to 10 MB</p><label className="button primary file-button"><Upload size={16}/>{file?'Choose another file':'Choose a statement'}<input ref={fileInput} type="file" accept=".xlsx,.csv,.xls" disabled={busy} onChange={e=>{const chosen=e.target.files?.[0];if(chosen){setFile(chosen);setStart('');setEnd('');const config={date_order:'day-first',decimal:'auto',...selected?.profile};setOptions(config);void inspect(chosen,config)}}}/></label><small>No bank credentials are needed. PDF and old binary .xls files are not supported.</small></div>
  {error&&<div role="alert" className="notice error"><AlertCircle size={18}/>{error}</div>}
  {busy&&<p role="status" className="muted">Reading your statement…</p>}
  {result&&<>
   <details className="mapping" open={result.mapping_required||result.errors.length>0||undefined}><summary>Check the statement layout <span>Columns, dates & number format</span></summary>
    <div className="form-grid">
     {result.sheets.length>0&&<label>Worksheet<select value={String(options.sheet||'')} onChange={e=>{setResult(null);const next={...options,sheet:e.target.value,header:undefined,mapping:undefined};setOptions(next);void inspect(file,next)}}>{result.sheets.map(s=><option key={s}>{s}</option>)}</select></label>}
     <label>Header row<input type="number" min="1" value={Number(options.header||0)+1} onChange={e=>{option('header',Number(e.target.value)-1);setOptions(p=>({...p,mapping:undefined}))}}/></label>
     <label>Date format<select value={String(options.date_order)} onChange={e=>option('date_order',e.target.value)}><option value="day-first">Day / month / year</option><option value="month-first">Month / day / year</option></select></label>
     <label>Decimal separator<select value={String(options.decimal)} onChange={e=>option('decimal',e.target.value)}><option value="auto">Automatic</option><option value=",">Comma: 1.234,56</option><option value=".">Point: 1,234.56</option></select></label>
     {Object.entries({date:'Transaction date',description:'Description',amount:'Signed amount',debit:'Money out (if separate)',credit:'Money in (if separate)',balance:'Balance (optional)',currency:'Currency (optional)'}).map(([field,label])=><label key={field}>{label}<select value={mapping[field]??-1} onChange={e=>option('mapping',{...mapping,[field]:Number(e.target.value)})}><option value={-1}>Not present</option>{result.headers.map((header,i)=><option value={i} key={i}>{i+1}. {header}</option>)}</select></label>)}
    </div>
    <label className="check"><input type="checkbox" checked={Boolean(options.invert)} onChange={e=>option('invert',e.target.checked)}/>Reverse signs (only if this bank shows spending as positive)</label>
    <p className="muted">Use either a signed amount column, or separate money-out and money-in columns. Check that purchases are negative and deposits are positive below.</p>
    <button className="secondary" disabled={busy} onClick={()=>void inspect()}>Update preview</button>
   </details>
   {dirty&&<div className="notice">Layout changed. Update the preview before importing.</div>}
   {result.errors.length>0&&<div className="notice error"><div><strong>{result.errors.length} rows need attention. Nothing has been imported.</strong><ul>{result.errors.slice(0,8).map((e,i)=><li key={i}>Row {e.row}: {e.error}</li>)}</ul><p>Check the layout, or remove headings and totals from a copy of the export. No invalid rows are silently skipped.</p></div></div>}
   {!result.mapping_required&&<><div className="preview-summary"><div><b>{result.total}</b><span>transactions found</span></div><div><b>{result.total-result.duplicates}</b><span>new transactions</span></div><div><b>{result.duplicates}</b><span>duplicates to skip</span></div></div>
    <div className="table-scroll preview-table"><table><thead><tr><th>Date</th><th>Description</th><th className="number">Amount</th><th>Import status</th></tr></thead><tbody>{result.rows.map((r,i)=><tr key={i}><td>{r.date}</td><td>{r.description}</td><td className={`number ${r.cents>=0?'positive':''}`}>{money(r.cents,r.currency)}</td><td><span className={`badge ${r.duplicate?'neutral':'green'}`}>{r.duplicate?'Duplicate':'New'}</span></td></tr>)}</tbody></table></div>
    {result.total>100&&<p className="muted">Showing the first 100 of {result.total} transactions. All valid rows will be checked.</p>}
    <details className="mapping"><summary>Statement coverage <span>Optional, for missing-period checks</span></summary><p className="muted">Enter the dates requested from your bank, including days with no activity. Otherwise, only the first and last transaction dates are recorded; complete coverage is not assumed.</p><div className="form-grid"><label>Statement from<input type="date" value={start} onChange={e=>setStart(e.target.value)}/></label><label>Statement through<input type="date" value={end} onChange={e=>setEnd(e.target.value)}/></label></div></details>
    <div className="notice"><ShieldCheck size={20}/><span>Imports stay on this computer and enter your review queue. Totals change after you approve them. AI is not used during import.</span></div>
    <div className="dialog-actions"><button className="primary" disabled={busy||dirty||result.errors.length>0||!result.total} onClick={()=>void commit()}><Check size={17}/>Import for review</button></div>
   </>}
  </>}
 </div>
}

