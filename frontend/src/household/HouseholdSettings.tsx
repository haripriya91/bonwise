import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { Session } from "@supabase/supabase-js";
import type { components } from "../api/types";
import { apiRequest, getErrorText } from "../api/client";
import { dayInput, euros, money } from "../planner/types";
import "./HouseholdSettings.css";
import { InformationTip } from "../components/InformationTip";

export type Settings = { household_id: string; name: string; currency: string; shopping_day: number | null; setup_completed: boolean; version: number; can_edit: boolean };
const days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
export function useHouseholdSettings(session: Session, householdId: string) {
  return useQuery({ queryKey: ["user", session.user.id, "household", householdId, "settings"],
    queryFn: ({ signal }) => apiRequest<Settings>(`/households/${householdId}/settings`, session.access_token, { signal, cache: "no-store" }), staleTime: 0 });
}

export function SetupReminder({session,householdId,onOpen}:{session:Session;householdId:string;onOpen:()=>void}) {
  const query=useHouseholdSettings(session,householdId);
  if(!query.data?.can_edit||query.data.setup_completed)return null;
  return <div className="household-settings-heading household-setup-reminder"><span>Choose your shopping day and review your household budget.</span><button type="button" className="button button-outline" onClick={onOpen}>Set up household</button></div>;
}

type Budget = components["schemas"]["BudgetOut"];
export function CreateHousehold({session,onCreated}:{session:Session;onCreated:(id:string)=>void}){
  const qc=useQueryClient(),id=useRef(crypto.randomUUID()),request=useRef<AbortController|null>(null);
  const [name,setName]=useState(""),[busy,setBusy]=useState(false),[error,setError]=useState("");
  useEffect(()=>()=>request.current?.abort(),[]);
  async function create(){if(!name.trim()||busy)return;const c=new AbortController();request.current=c;setBusy(true);setError("");try{
    const result=await apiRequest<Settings>("/households",session.access_token,{method:"POST",signal:c.signal,body:{client_request_id:id.current,name:name.trim()}});
    if(!c.signal.aborted){await qc.invalidateQueries({queryKey:["user",session.user.id,"me"]});onCreated(result.household_id);}
  }catch(e){if(!c.signal.aborted)setError(getErrorText(e));}finally{if(!c.signal.aborted)setBusy(false);}}
  return <section className="household-settings"><h1>Welcome to Bonwise</h1><p>Create a private household to start. Then choose your shopping day and budget.</p><label>Household name<input maxLength={120} value={name} disabled={busy} onChange={e=>setName(e.target.value)} placeholder="e.g. Our household"/></label><div className="household-settings-actions"><button type="button" className="button button-primary" disabled={busy||!name.trim()} onClick={()=>void create()}>Create household</button></div>{error&&<p className="error-message" role="alert">{error}</p>}</section>;
}

export function HouseholdSettings({ session, householdId }: { session: Session; householdId: string }) {
  const settings = useHouseholdSettings(session, householdId), qc = useQueryClient();
  const [name, setName] = useState(""); const [day, setDay] = useState("");
  const [step, setStep] = useState(0); const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null); const [message, setMessage] = useState("");
  const [amount, setAmount] = useState(""); const [budget, setBudget] = useState<Budget | null>(null);
  const [commit, setCommit] = useState(false); const [title, setTitle] = useState(""); const [commitAmount, setCommitAmount] = useState("");
  const [commitDate, setCommitDate] = useState(dayInput(new Date()));
  const budgetId = useRef(crypto.randomUUID()), commitId = useRef(crypto.randomUUID());
  const requests = useRef(new Set<AbortController>());
  useEffect(() => () => { requests.current.forEach(c => c.abort()); }, []);
  useEffect(() => { if (settings.data) {setName(settings.data.name);setDay(settings.data.shopping_day == null ? "" : String(settings.data.shopping_day));} }, [settings.data]);
  const base = `/households/${householdId}`;
  async function run(action: (signal: AbortSignal) => Promise<void>) {
    if (busy) return; const c = new AbortController(); requests.current.add(c);setBusy(true);setError(null);setMessage("");
    try { await action(c.signal); } catch(e) {if(!c.signal.aborted){setError(getErrorText(e));void settings.refetch();}} finally {requests.current.delete(c);if(!c.signal.aborted)setBusy(false);}
  }
  async function refresh() { await qc.invalidateQueries({ predicate: q => q.queryKey.includes(session.user.id) }); }
  async function saveSettings(signal: AbortSignal, completed: boolean) {
    if (!settings.data || !name.trim()) throw new Error("Enter a household name.");
    const saved = await apiRequest<Settings>(`${base}/settings`, session.access_token, {method:"PATCH",signal,body:{expected_version:settings.data.version,name:name.trim(),shopping_day:day===""?null:Number(day),setup_completed:completed}});
    if(!signal.aborted){qc.setQueryData(["user",session.user.id,"household",householdId,"settings"],saved);await refresh();}
  }
  async function openSetup(signal: AbortSignal) {
    const page=await apiRequest<{budgets:Budget[]}>(`${base}/budgets`,session.access_token,{signal,cache:"no-store"});
    const today=dayInput(new Date()),current=page.budgets.find(b=>b.start_date<=today&&b.end_date>=today)??null;
    if(signal.aborted)return;setCommit(false);setBudget(current);setAmount(current?String(current.amount_cents/100):"");setStep(1);
  }
  async function budgetStep(signal: AbortSignal) {
    const cents=euros(amount);if(cents===null)throw new Error("Enter a valid budget amount, or choose Skip for now.");
    let saved:Budget;
    if(budget){saved=cents===budget.amount_cents?budget:await apiRequest<Budget>(`${base}/budgets/${budget.id}`,session.access_token,{method:"PATCH",signal,body:{expected_version:budget.version,amount_cents:cents}});}
    else{const now=new Date();saved=await apiRequest<Budget>(`${base}/budgets`,session.access_token,{method:"POST",signal,body:{client_request_id:budgetId.current,start_date:dayInput(new Date(now.getFullYear(),now.getMonth(),1)),end_date:dayInput(new Date(now.getFullYear(),now.getMonth()+1,0)),amount_cents:cents}});}
    if(signal.aborted)return;setBudget(saved);setStep(3);await refresh();
  }
  async function finish(signal:AbortSignal) {
    if(commit){const cents=euros(commitAmount);if(!budget||!title.trim()||!cents)throw new Error("Enter a commitment title and an amount above zero.");
      const saved=await apiRequest<Budget>(`${base}/budgets/${budget.id}/commitments`,session.access_token,{method:"POST",signal,body:{client_request_id:commitId.current,expected_version:budget.version,title:title.trim(),planned_on:commitDate,amount_cents:cents}});
      if(signal.aborted)return;setBudget(saved);setCommit(false);commitId.current=crypto.randomUUID();
    }
    await saveSettings(signal,true);if(!signal.aborted){setStep(0);setMessage("Household setup saved. You can change these settings later.");}
  }
  if(settings.isPending)return <p role="status">Loading household settings…</p>;
  if(settings.error)return <section className="household-settings"><p role="alert" className="error-message">{getErrorText(settings.error)}</p><button className="button button-outline" onClick={()=>void settings.refetch()}>Retry settings</button></section>;
  if(!settings.data)return null;
  const canEdit=settings.data.can_edit;
  return <section className="household-settings" aria-label="Household settings">
    <div className="household-settings-heading"><div><div className="household-title-with-help"><h2>Shopping routine</h2><InformationTip label="What shopping routine changes" text="The household name is shared with members. Your usual shopping day pre-fills future shopping dates. Changing it does not change existing approved plans, reserve money or record purchases." /></div><p>Name your household and choose when you usually shop.</p></div>{canEdit&&!settings.data.setup_completed&&step===0&&<button className="button button-outline" disabled={busy} onClick={()=>void run(openSetup)}>Guided setup (optional)</button>}</div>
    {error&&<p className="error-message" role="alert">{error}</p>}{message&&<p role="status">{message}</p>}
    {step===0?<><div className="household-settings-grid"><label>Household name<input value={name} maxLength={120} disabled={!canEdit||busy} onChange={e=>setName(e.target.value)}/></label><label>Usual shopping day<select value={day} disabled={!canEdit||busy} onChange={e=>setDay(e.target.value)}><option value="">No fixed day</option>{days.map((d,i)=><option key={d} value={i}>{d}</option>)}</select></label></div><p className="household-settings-note">Used for future plans. Existing lists and reservations stay as reviewed.</p>{canEdit?<button className="button button-primary" disabled={busy} onClick={()=>void run(async signal=>{await saveSettings(signal,settings.data!.setup_completed);if(!signal.aborted)setMessage("Settings saved.");})}>Save shopping routine</button>:<p>Only the household owner can change these shared settings.</p>}</>:<div className="household-onboarding"><p>Step {step} of 3</p>
      {step===1&&<><h3>When do you usually shop?</h3><label>Shopping day<select value={day} onChange={e=>setDay(e.target.value)} disabled={busy}><option value="">No fixed day</option>{days.map((d,i)=><option key={d} value={i}>{d}</option>)}</select></label><button className="button button-primary" disabled={busy} onClick={()=>setStep(2)}>Continue</button></>}
      {step===2&&<><h3>What budget would you like to track?</h3><p>{budget?`Updating your existing period: ${budget.start_date} – ${budget.end_date}.` : "Create a budget for this calendar month."} Recorded purchases and commitments are retained.</p><label>Budget amount (€)<input value={amount} inputMode="decimal" onChange={e=>setAmount(e.target.value)} disabled={busy}/></label><p>This tracks spending entered in Bonwise; it is not your bank balance. Saving this step updates the budget immediately.</p><div className="household-settings-actions"><button className="button button-outline" disabled={busy} onClick={()=>setStep(1)}>Back</button><button className="button button-primary" disabled={busy} onClick={()=>void run(budgetStep)}>Save budget & continue</button><button className="button button-outline" disabled={busy} onClick={()=>setStep(3)}>Skip budget</button></div></>}
      {step===3&&<><h3>Anything to set money aside for?</h3><p>Existing commitments stay unchanged. A new commitment is optional.</p>{budget?<><label className="household-check"><input type="checkbox" checked={commit} disabled={busy} onChange={e=>setCommit(e.target.checked)}/>Add a commitment</label>{commit&&<div className="household-settings-grid"><label>What is it for?<input value={title} maxLength={120} onChange={e=>setTitle(e.target.value)} disabled={busy}/></label><label>Amount (€)<input value={commitAmount} inputMode="decimal" onChange={e=>setCommitAmount(e.target.value)} disabled={busy}/></label><label>Planned date<input type="date" min={budget.start_date} max={budget.end_date} value={commitDate} onChange={e=>setCommitDate(e.target.value)} disabled={busy}/></label></div>}<p>Available before this new commitment: {money(budget.available_cents)}. No bank transfer takes place.</p></>:<p>Create a budget later to set money aside.</p>}<p>Review: {day===""?"No fixed shopping day":days[Number(day)]}{budget?` · Budget ${money(budget.amount_cents)}`:" · No budget change"}{commit?` · New commitment: ${title||"Untitled"}, €${commitAmount||"0"}`:" · No new commitment"}</p><button className="button button-primary" disabled={busy} onClick={()=>void run(finish)}>Confirm & finish setup</button></>}
      <button className="text-link" disabled={busy} onClick={()=>{setStep(0);setMessage("Setup closed. Any budget already saved remains in place.");}}>Close setup</button>
    </div>}
  </section>;
}

export function ProfilePanel({session}:{session:Session}) {
  const qc=useQueryClient(),key=["user",session.user.id,"profile"];
  const query=useQuery({queryKey:key,queryFn:({signal})=>apiRequest<{display_name:string;version:number}>("/profile",session.access_token,{signal,cache:"no-store"})});
  const [name,setName]=useState(""),[busy,setBusy]=useState(false),[error,setError]=useState(""),[message,setMessage]=useState("");const active=useRef<AbortController|null>(null);
  useEffect(()=>()=>active.current?.abort(),[]);useEffect(()=>{if(query.data)setName(query.data.display_name);},[query.data]);
  async function save(){if(!query.data||busy)return;const c=new AbortController();active.current=c;setBusy(true);setError("");setMessage("");try{const result=await apiRequest<{display_name:string;version:number}>("/profile",session.access_token,{method:"PATCH",signal:c.signal,body:{expected_version:query.data.version,display_name:name.trim()}});if(!c.signal.aborted){qc.setQueryData(key,result);setMessage("Profile saved.");}}catch(e){if(!c.signal.aborted)setError(getErrorText(e));}finally{if(!c.signal.aborted)setBusy(false);}}
  return <details className="account-profile"><summary>Your profile</summary><div className="account-profile-content"><h2>Your profile</h2><p>Personal details are separate from shared household settings.</p><label>Display name<input value={name} maxLength={80} disabled={busy||!query.data} onChange={e=>setName(e.target.value)}/></label><p>Email: {session.user.email??"Signed-in account"}</p>{(error||query.error)&&<p className="error-message" role="alert">{error||getErrorText(query.error)}</p>}{query.isPending&&<p role="status">Loading profile…</p>}{query.error&&<button className="button button-outline" onClick={()=>void query.refetch()}>Retry</button>}<button className="button button-primary" disabled={busy||!query.data} onClick={()=>void save()}>Save profile</button>{message&&<p role="status">{message}</p>}</div></details>;
}
