import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Session } from "@supabase/supabase-js";
import { apiRequest, getErrorText } from "../api/client";
import "./ActivityCalendar.css";

type CalendarEvent = { id: string; date: string; kind: "purchase" | "commitment" | "plan"; title: string; amount_cents: number | null; status: string | null };
type CalendarData = { events: CalendarEvent[]; truncated: boolean };
const money = (cents: number) => new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" }).format(cents / 100);
const key = (date: Date) => `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,"0")}-${String(date.getDate()).padStart(2,"0")}`;
const parse = (value: string) => new Date(`${value}T12:00:00`);
const move = (date: Date, days: number) => { const result = new Date(date); result.setDate(result.getDate()+days); return result; };
const monday = (date: Date) => move(date, -((date.getDay()+6)%7));

export function ActivityCalendar({session, householdId, onAddPurchase, onOpenPlan, onManageCommitments}: {
  session: Session; householdId: string; onAddPurchase: () => void;
  onOpenPlan: (date: string, planId: string) => void; onManageCommitments: () => void;
}) {
  const [selected, setSelected] = useState(() => key(new Date()));
  const [mode, setMode] = useState<"week"|"month">("week");
  const date = parse(selected);
  const firstOfMonth = new Date(date.getFullYear(), date.getMonth(), 1, 12);
  const start = mode === "week" ? monday(date) : monday(firstOfMonth);
  const count = mode === "week" ? 7 : 42;
  const end = move(start, count-1);
  const startKey = key(start), endKey = key(end);
  const query = useQuery({queryKey: ["user",session.user.id,"household",householdId,"activity-calendar",startKey,endKey],
    queryFn: ({signal}) => apiRequest<CalendarData>(`/households/${encodeURIComponent(householdId)}/activity-calendar?start_date=${startKey}&end_date=${endKey}`, session.access_token, {signal, cache:"no-store"})});
  const events = query.data?.events ?? [];
  const byDay = useMemo(() => {
    const days = new Map<string, CalendarEvent[]>();
    for (const event of events) days.set(event.date, [...(days.get(event.date) ?? []),event]);
    return days;
  },[events]);
  const daily = byDay.get(selected) ?? [];
  const upcoming = events.filter(event => event.kind === "commitment" && event.date >= selected).slice(0,5);
  function navigate(direction: number) {
    if (mode === "week") setSelected(key(move(date, direction*7)));
    else setSelected(key(new Date(date.getFullYear(),date.getMonth()+direction,1,12)));
  }
  return <section className="activity-calendar" aria-labelledby="activity-calendar-title">
    <header className="calendar-heading"><div><h2 id="activity-calendar-title">Your {mode}</h2><p>Recorded purchases, shopping plans & commitments</p></div>
      <div className="calendar-mode" aria-label="Calendar view">{(["week","month"] as const).map(value => <button type="button" className={`button ${mode===value?"button-primary":"button-outline"}`} key={value} aria-pressed={mode===value} onClick={()=>setMode(value)}>{value === "week" ? "Week" : "Month"}</button>)}</div></header>
    <div className="calendar-navigation"><button type="button" className="button button-outline" aria-label={`Previous ${mode}`} onClick={()=>navigate(-1)}>‹</button>
      <strong aria-live="polite">{mode==="month" ? date.toLocaleDateString("en-GB",{month:"long",year:"numeric"}) : `${start.toLocaleDateString("en-GB",{day:"numeric",month:"short"})} – ${end.toLocaleDateString("en-GB",{day:"numeric",month:"short",year:"numeric"})}`}</strong>
      <button type="button" className="button button-outline" aria-label={`Next ${mode}`} onClick={()=>navigate(1)}>›</button><button type="button" className="text-link" onClick={()=>setSelected(key(new Date()))}>Today</button></div>
    {query.isPending && <p role="status">Loading calendar…</p>}
    {query.error && <p className="field-error" role="alert">{getErrorText(query.error)} <button type="button" className="text-link" onClick={()=>void query.refetch()}>Try again</button></p>}
    {query.data?.truncated && <p role="status">Some entries are not shown because this period has more than 500 records of one type.</p>}
    <div className={`calendar-days ${mode}`} aria-label="Choose a day">{Array.from({length:count},(_,index) => {
      const day = move(start,index), dayKey = key(day), entries = byDay.get(dayKey) ?? [];
      return <button type="button" key={dayKey} className={`calendar-day${selected===dayKey?" selected":""}${day.getMonth()!==date.getMonth()?" outside-month":""}`} aria-pressed={selected===dayKey} aria-current={dayKey===key(new Date())?"date":undefined} aria-label={`${day.toLocaleDateString("en-GB",{weekday:"long",day:"numeric",month:"long",year:"numeric"})}, ${entries.length} entries`} onClick={()=>setSelected(dayKey)}>
        <span className="calendar-weekday">{day.toLocaleDateString("en-GB",{weekday:"short"})}{dayKey===key(new Date())?" · Today":""}</span><strong>{day.getDate()}</strong>
        <span className="calendar-day-events">{entries.slice(0,2).map(event=><span key={`${event.kind}:${event.id}`}>{event.kind==="purchase"?`${money(event.amount_cents??0)} recorded`:event.kind==="plan"?"Shopping plan":event.title}</span>)}{entries.length>2&&<span>+{entries.length-2} more</span>}</span>
      </button>;
    })}</div>
    <div className="calendar-detail-grid"><section className="calendar-details"><h3>{date.toLocaleDateString("en-GB",{weekday:"long",day:"numeric",month:"long"})}</h3>
      {!query.isPending && !query.error && !daily.length && <p>No recorded purchases or scheduled entries for this day.</p>}
      {daily.map(event=><div className="calendar-event" key={`${event.kind}:${event.id}`}><strong>{event.title}</strong><span>{event.kind==="purchase"?"Confirmed purchase":event.kind==="commitment"?"Reserved commitment":`${event.status === "approved" ? "Approved" : "Draft"} shopping list`}{event.amount_cents!=null?` · ${money(event.amount_cents)}`:""}</span>{event.kind==="plan"&&<button type="button" className="text-link" onClick={()=>onOpenPlan(event.date,event.id)}>Open this plan</button>}</div>)}
      <button type="button" className="text-link" onClick={onAddPurchase}>+ Add a purchase</button></section>
      <aside className="calendar-details"><h3>Upcoming commitments</h3>{upcoming.map(event=><div className="calendar-event" key={event.id}><strong>{event.title}</strong><span>{parse(event.date).toLocaleDateString("en-GB",{day:"numeric",month:"short"})} · {money(event.amount_cents??0)} reserved</span></div>)}{!upcoming.length && !query.isPending && !query.error && <p>No upcoming commitments in this calendar period.</p>}<button type="button" className="text-link" onClick={onManageCommitments}>Manage commitments</button></aside></div>
  </section>;
}
