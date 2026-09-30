export type Category = { id: number; name: string; parent_id: number | null; color: string };
export type Account = { id: number; name: string; bank: string; currency: string; profile: Record<string, unknown> };
export type Settings = { hosted: boolean; accounts: Account[]; categories: Category[]; currencies: string[]; latest_month: string | null; migration: { imported: Record<string, number>; reconciled: boolean; issues: {sheet:string;row:number;reason:string}[]; legacy_savings_overrides: string[] } | null };
export type Transaction = {id:number; date:string; description:string; cents:number; currency:string; kind:string; status:string; category_id:number|null; category:string|null; account_id:number; account:string; label:string; expense_type:string; source:string; notes:string; suggestion: {category:string;description:string;confidence:number}|null; matches:{id:number;date:string;description:string;kind:string}[]};
export type Dashboard = {month:string; currency:string; totals:{income:number;expenses:number;investments:number;savings:number;after_investments:number;savings_rate:number|null;fixed:number;variable:number}; monthly:{month:string;income:number;expenses:number;savings:number}[];categories:{id:number|null;name:string;color:string;cents:number}[]; direct_categories:Record<string,number>;recent:Transaction[];pending:number;pending_cents:number;all_pending:number};
export type Budget = {month:string;currency:string;income_cents:number;investment_cents:number;limits:Record<string,number>;previous:string|null};
export type Preview = {token:string;filename:string;headers:string[];header:number;mapping:Record<string,number>;sheets:string[];sheet:string;sample:string[][];rows:{date:string;description:string;cents:number;currency:string;duplicate:boolean}[];errors:{row:number;error:string}[];mapping_required?:boolean;total:number;duplicates:number;start?:string;end?:string};
export async function api<T>(url:string, options:RequestInit={}):Promise<T> {
 const response=await fetch(url,{...options,headers:options.body instanceof FormData?options.headers:{'Content-Type':'application/json',...options.headers}});
 const data=await response.json();
 if(response.status===401) window.dispatchEvent(new Event('budget-session-expired'));
 if(!response.ok) throw new Error(typeof data.detail==='string'?data.detail:'Please check the fields and try again.');
 return data;
}
export const money=(cents:number,currency='EUR')=>new Intl.NumberFormat('en-GB',{style:'currency',currency}).format(cents/100);
export const today=()=>{const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`};

