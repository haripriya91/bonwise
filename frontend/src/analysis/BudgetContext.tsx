import {useQuery} from "@tanstack/react-query";
import type {Session} from "@supabase/supabase-js";
import {apiRequest,getErrorText} from "../api/client";
import type {components} from "../api/types";

type Result=components["schemas"]["AnalysisBudgetContext"];
const money=(cents:number)=>new Intl.NumberFormat("de-DE",{style:"currency",currency:"EUR"}).format(cents/100);
export function AnalysisBudgetContext({session,householdId,startDate,endDate,totalCents}:{session:Session;householdId:string;startDate:string;endDate:string;totalCents?:number}){
  const query=useQuery({queryKey:["user",session.user.id,"household",householdId,"analysis-budget",startDate,endDate],
    queryFn:({signal})=>apiRequest<Result>(`/households/${encodeURIComponent(householdId)}/analysis/budget-context?start_date=${startDate}&end_date=${endDate}`,session.access_token,{signal,cache:"no-store"})});
  const budget=query.data?.budget;
  const noBudget=query.data?.status==="not_set";
  const note=query.data?.status==="partial"?"No single budget covers all selected dates":noBudget?"No budget set for selected dates":query.isPending?"Loading selected-period budget":"Budget unavailable";
  const period=budget?`${budget.start_date} – ${budget.end_date}`:note;
  return <><section className="analysis-budget-summary" aria-label="Spending and selected-period budget">
    <article className="analysis-stat"><span>Recorded purchases</span><strong>{totalCents===undefined?"—":money(totalCents)}</strong><small>Confirmed records · selected dates</small></article>
    <article className="analysis-stat"><span>Budget covering selected dates</span><strong>{budget?money(budget.amount_cents):noBudget?money(0):"—"}</strong><small>{period}</small></article>
    <article className="analysis-stat"><span>Open reservations</span><strong>{budget?money(budget.reserved_cents):"—"}</strong><small>{budget?"Across the full budget period":note}</small></article>
    <article className="analysis-stat available"><span>Budget remaining</span><strong>{budget?money(budget.available_cents):"—"}</strong><small>{budget?"Across the full budget period, after recorded spending and reservations":note}</small></article>
  </section>{query.error&&<p className="field-error" role="alert">Budget could not be checked: {getErrorText(query.error)} <button type="button" className="text-link" onClick={()=>void query.refetch()}>Retry</button></p>}</>;
}
