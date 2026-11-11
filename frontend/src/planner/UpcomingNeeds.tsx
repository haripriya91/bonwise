import {AIWorking} from "../components/AIWorking";
import {useEffect,useRef,useState} from "react";
import {useQuery,useQueryClient} from "@tanstack/react-query";
import type {Session} from "@supabase/supabase-js";
import {apiRequest,getErrorText} from "../api/client";
import {editable,money,euros} from "./types";
import type {Item,Plan} from "./types";

type Saved={id:string;item:Item};
type Purpose="main"|"side"|"drink"|"extra"|"household";
type Suggestion=Item&{purpose?:Purpose};
type Proposal={base_version:number;items:Suggestion[];message:string;title?:string;explanation?:string;basis?:string};
const purposes:Record<Purpose,string>={main:"Main idea",side:"To go with it",drink:"Optional drink",extra:"Optional extra",household:"Household need"};

export function UpcomingNeeds({session,householdId,plan,disabled,open,onOpenChange,onAdd}:{
  session:Session;householdId:string;plan:Plan;disabled:boolean;open:boolean;
  onOpenChange:(open:boolean)=>void;onAdd:(items:Item[])=>boolean;
}){
  const base=`/households/${householdId}`,qc=useQueryClient(),key=["user",session.user.id,"household",householdId,"upcoming-needs"];
  const saved=useQuery({queryKey:key,queryFn:({signal})=>apiRequest<{needs:Saved[]}>(`${base}/upcoming-needs`,session.access_token,{signal,cache:"no-store"})});
  const [occasion,setOccasion]=useState("seasonal"),[text,setText]=useState(""),[reviewed,setReviewed]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(""),[message,setMessage]=useState(""),[proposal,setProposal]=useState<Proposal|null>(null),[selected,setSelected]=useState<string[]>([]);
  const [extraBudget,setExtraBudget]=useState("");
  const resultHeading=useRef<HTMLHeadingElement>(null);
  const requests=useRef(new Set<AbortController>());
  useEffect(()=>()=>requests.current.forEach(c=>c.abort()),[]);
  useEffect(()=>{setProposal(null);setSelected([]);},[plan.id,plan.version]);
  useEffect(()=>{if(proposal){resultHeading.current?.focus();}},[proposal?.base_version,proposal?.title]);
  const stale=proposal!==null&&proposal.base_version!==plan.version;
  function clearProposal(){setProposal(null);setSelected([]);setMessage("");setReviewed(false);}
  async function run(action:(signal:AbortSignal)=>Promise<void>){
    if(busy)return;
    const c=new AbortController();requests.current.add(c);setBusy(true);setError("");setMessage("");
    try{await action(c.signal);}catch(e){if(!c.signal.aborted)setError(getErrorText(e));}
    finally{requests.current.delete(c);if(!c.signal.aborted)setBusy(false);}
  }
  async function suggest(signal:AbortSignal){
    const limit=extraBudget.trim()?euros(extraBudget):null;
    if(extraBudget.trim()&&(!limit||limit<0))throw new Error("Enter an extra allowance above zero, or leave it empty.");
    const result=await apiRequest<Proposal>(`${base}/weekly-plans/${plan.id}/needs-preview`,session.access_token,{method:"POST",signal,body:{expected_version:plan.version,occasion,text,privacy_reviewed:reviewed,extra_budget_cents:limit}});
    if(!signal.aborted){setProposal(result);setSelected([]);}
  }
  function add(rows:Item[]){
    if(!onAdd(rows))return;
    setSelected([]);setProposal(null);onOpenChange(false);
    setMessage("Selected suggestions moved to your shopping list. Review and save your changes; no additional money has been reserved.");
  }
  const chosen=(proposal?.items??[]).filter(i=>selected.includes(i.id));
  return <section className="planner-card upcoming-needs" aria-label="Upcoming and seasonal needs">
    <div className="planner-section-heading">
      <div><p className="needs-eyebrow">{open?"Ideas to review · not on your shopping list":"Optional planning ideas"}</p>
        <h2>{open?"Suggestions for your occasion":"Planning for winter or having guests?"}</h2>
        <p className="planner-hint">{open?"Choose what is useful, then add it to your shopping list. Your existing list is kept separately while you review these ideas.":"Explore ideas, choose a few, then add them to your shopping list."}</p>
      </div>
      <button type="button" className="button button-outline" disabled={busy} aria-expanded={open} aria-controls="optional-needs-review" onClick={()=>onOpenChange(!open)}>{open?"Back to shopping list":"Explore needs"}</button>
    </div>
    {open&&<div id="optional-needs-review">
      <details className="needs-request" open={!proposal}>
        <summary>{proposal?"Your request · change and suggest again":"Describe what you are preparing for"}</summary>
        <label>What are you preparing for?<select value={occasion} disabled={busy} onChange={e=>{setOccasion(e.target.value);clearProposal();}}><option value="seasonal">Seasonal household needs</option><option value="guests">Guests or an occasion</option><option value="replacement">Replacement purchases</option><option value="other">Something else</option></select></label>
        <label>Describe what would help<textarea value={text} maxLength={400} disabled={busy} onChange={e=>{setText(e.target.value);clearProposal();}} placeholder="Two friends for dinner Saturday. Vegetarian snacks, keep it modest."/></label>
        <label>Optional extra allowance (€)<input inputMode="decimal" value={extraBudget} disabled={busy} onChange={e=>{setExtraBudget(e.target.value);clearProposal();}} placeholder="e.g. 15.00"/></label>
        <p className="planner-hint">This is a guide for suggestions within your existing budget. It does not increase your budget or reserve money. Unpriced items still need a price check.</p>
        <label className="needs-check"><input type="checkbox" checked={reviewed} disabled={busy} onChange={e=>setReviewed(e.target.checked)}/><span>I checked this contains shopping needs only, without personal details.</span></label>
        <p className="planner-hint">Sent to OpenAI: your description, selected occasion, optional allowance, catalogue details and saved list names. No receipt images or household identifiers are included.</p>
        <button type="button" className="button button-primary" disabled={busy||disabled||!reviewed||text.trim().length<3} onClick={()=>void run(suggest)}>{busy?"Working…":"Suggest ideas"}</button>
        {disabled&&<p className="planner-hint">Save current list changes before requesting or adding suggestions.</p>}
      </details>
      {busy&&<AIWorking label="Updating optional ideas"/>}
      {proposal&&<div className="needs-proposal">
        <div className="needs-idea"><p className="needs-eyebrow">Suggested approach</p>
          <h3 ref={resultHeading} tabIndex={-1}>{proposal.title??"Optional ideas for your request"}</h3>
          <p className="planner-hint"><strong>Your request:</strong> {text}</p>
          {proposal.explanation&&<p>{proposal.explanation}</p>}
          <p className="planner-hint">A proposed idea for you to review, not a complete recipe or a confirmed menu. Quantities are starting points.</p>
        </div>
        <div className="needs-basis"><h4>Why these items?</h4><p>{proposal.basis??"Based on your description, selected occasion and catalogue products. Saved shopping list names help avoid duplicates; they do not show what you already own. Estimates, when available, use your confirmed purchases."}</p></div>
        <h3>Choose suggestions to add</h3><p className="planner-hint">Tick an item to include it. Nothing is selected automatically. Drinks and extras are optional.</p>
        {proposal.items.length===0&&<p>No additional ideas proposed. Return to your shopping list to add an item manually.</p>}
        {proposal.items.map(item=><div className="upcoming-need-row" key={item.id}>
          <label className="needs-check needs-item-choice"><input type="checkbox" checked={selected.includes(item.id)} disabled={busy||stale} onChange={e=>setSelected(s=>e.target.checked?[...s,item.id]:s.filter(id=>id!==item.id))}/>
            <span><strong>{item.name}</strong><span className="needs-quantity">{item.quantity} {item.unit}</span></span>
          </label>
          <div className="needs-item-detail">
            <span className="needs-purpose">{item.purpose?purposes[item.purpose]:"Optional suggestion"}</span>
            <p>{item.reason}</p>
            <p className="needs-price">{item.estimate?.line_total_cents!=null?`Estimated ${money(item.estimate.line_total_cents)} · ${item.estimate.label}`:"Price unknown · check before approving the shopping list"}</p>
            <button className="text-link" type="button" disabled={busy} onClick={()=>{setProposal(p=>p?{...p,items:p.items.filter(i=>i.id!==item.id)}:p);setSelected(s=>s.filter(id=>id!==item.id));}}>Skip suggestion</button>
          </div>
        </div>)}
        <p className="planner-hint">{proposal.message}</p>
        <div className="needs-transfer">
          <p role="status">{chosen.length} suggestion{chosen.length===1?"":"s"} selected · not yet added</p>
          <div className="planner-actions">
            <button type="button" className="button button-primary" disabled={busy||disabled||stale||!chosen.length} onClick={()=>add(chosen)}>Add selected to shopping list</button>
            <button type="button" className="button button-outline" disabled={busy||stale||!chosen.length} onClick={()=>void run(async signal=>{
              const result=await apiRequest<{needs:Saved[]}>(`${base}/upcoming-needs`,session.access_token,{method:"POST",signal,body:{needs:chosen.map(i=>({id:i.id,item:editable(i)}))}});
              if(!signal.aborted){qc.setQueryData(key,result);setSelected([]);setMessage("Saved for later. Your shopping list and budget are unchanged.");}
            })}>Save selected for later</button>
          </div>
          <p className="planner-hint">Adding copies your choices into the editable shopping list. Save the list, review costs, then approve separately to reserve funds.</p>
        </div>
      </div>}
      {!!saved.data?.needs.length&&<details className="needs-saved"><summary>Saved for later ({saved.data.needs.length})</summary>
        {saved.data.needs.map(n=><div className="upcoming-need-row" key={n.id}><strong>{n.item.name}</strong><div className="planner-actions">
          <button type="button" className="button button-outline" disabled={busy||disabled} onClick={()=>add([n.item])}>Add to shopping list</button>
          <button type="button" className="text-link" disabled={busy} onClick={()=>void run(async signal=>{await apiRequest(`${base}/upcoming-needs/${n.id}`,session.access_token,{method:"DELETE",signal});if(!signal.aborted)await saved.refetch();})}>Dismiss</button>
        </div></div>)}
      </details>}
      {saved.error&&<p className="field-error" role="alert">Saved needs unavailable: {getErrorText(saved.error)}</p>}
    </div>}
    {error&&<p className="field-error" role="alert">{error}</p>}{message&&<p role="status">{message}</p>}
  </section>;
}
