import {createContext,useContext,useEffect,useState,type ReactNode,type FormEvent} from 'react';
import {LockKeyhole,LogOut} from 'lucide-react';
const SessionContext=createContext({required:false,logout:()=>{}});
export function SessionButton(){const session=useContext(SessionContext);return session.required?<button className="text-button" onClick={session.logout}><LogOut size={13}/>Sign out</button>:null}
export default function AuthGate({children}:{children:ReactNode}){
 const [session,setSession]=useState<{required:boolean;authenticated:boolean}|null>(null);const [error,setError]=useState('');const [busy,setBusy]=useState(false);
 async function check(){try{const r=await fetch('/api/auth/session',{cache:'no-store'});if(!r.ok)throw new Error();setSession(await r.json());setError('')}catch{setError('Budget LPC’s server is unavailable. Check your connection and try again.')}}
 useEffect(()=>{void check();const expired=()=>setSession({required:true,authenticated:false});window.addEventListener('budget-session-expired',expired);return()=>window.removeEventListener('budget-session-expired',expired)},[]);
 async function login(e:FormEvent<HTMLFormElement>){e.preventDefault();setBusy(true);setError('');const form=e.currentTarget;try{const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:new FormData(form).get('password')})});const data=await r.json();if(!r.ok)throw new Error(data.detail||'Sign-in failed');form.reset();await check()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 async function logout(){try{const response=await fetch('/api/auth/logout',{method:'POST'});if(!response.ok)throw new Error();setSession({required:true,authenticated:false})}catch{setError('Could not sign out. Check the connection and try again.')}}
 if(!session)return <div className="auth-screen"><div><img src="/icons/icon-192.png" alt="Budget LPC"/><h1>Budget LPC</h1><p role="status">{error||'Opening your workspace…'}</p>{error&&<button className="primary" onClick={()=>void check()}>Try again</button>}</div></div>;
 if(session.required&&!session.authenticated)return <div className="auth-screen"><form onSubmit={login}><img src="/icons/icon-192.png" alt="Budget LPC"/><span className="eyebrow">YOUR PRIVATE FINANCIAL WORKSPACE</span><h1>Welcome back.</h1><p>Sign in to Budget LPC.</p><label>Password<input autoFocus type="password" name="password" autoComplete="current-password" required maxLength={512}/></label>{error&&<p className="error-text" role="alert">{error}</p>}<button className="primary" disabled={busy}><LockKeyhole size={16}/>{busy?'Signing in…':'Open my budget'}</button><small>Forgot your password? It can be reset by the owner through the hosting settings.</small></form></div>;
 return <SessionContext.Provider value={{required:session.required,logout:()=>void logout()}}>{error&&<div role="alert" className="notice error">{error}</div>}{children}</SessionContext.Provider>
}

