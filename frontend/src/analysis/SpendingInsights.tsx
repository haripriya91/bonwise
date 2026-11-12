import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { Session } from "@supabase/supabase-js";
import { apiRequest, getErrorText } from "../api/client";
import type { components } from "../api/types";
import "./SpendingInsights.css";

type Insights = components["schemas"]["InsightsOut"];
type Action = "review" | "offers" | "budget" | "planner";
const money = (value: number) => new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" }).format(value / 100);
const actionLabel = { review: "Review these purchases", offers: "Compare offers", budget: "Review budget settings", planner: "Review my shopping list" };

export function SpendingInsights({ session, householdId, startDate, endDate, purchaseCount, onAction, onPurchase }: {
  session: Session; householdId: string; startDate: string; endDate: string; purchaseCount: number;
  onAction: (action: Action) => void; onPurchase: (id: string) => void;
}) {
  const client = useQueryClient();
  const [result, setResult] = useState<Insights | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const generation = useRef(0);

  useEffect(() => {
    // Parent keys this component by account, household and dates. Invalidate local
    // results immediately after relevant app writes; never auto-call the model.
    const stop = client.getQueryCache().subscribe(event => {
      if (event.type !== "updated" || event.action.type !== "invalidate") return;
      const key = event.query.queryKey;
      if (key[0] !== "user" || key[1] !== session.user.id || key[2] !== "household" || key[3] !== householdId) return;
      generation.current += 1;
      controller.current?.abort();
      controller.current = null;
      setPending(false); setResult(null); setError(null);
      setNotice("Records changed. Generate fresh insights when you’re ready.");
    });
    return () => { stop(); generation.current += 1; controller.current?.abort(); };
  }, [client, householdId, session.user.id]);

  async function generate() {
    if (controller.current || purchaseCount < 1) return;
    const active = new AbortController();
    const version = ++generation.current;
    controller.current = active;
    setPending(true); setError(null); setNotice(null); setResult(null);
    try {
      const data = await apiRequest<Insights>(`/households/${encodeURIComponent(householdId)}/analysis/insights`, session.access_token, {
        method: "POST", body: { start_date: startDate, end_date: endDate }, signal: active.signal, cache: "no-store",
      });
      if (!active.signal.aborted && version === generation.current) setResult(data);
    } catch (failure) {
      if (!active.signal.aborted && version === generation.current) setError(getErrorText(failure));
    } finally {
      if (version === generation.current) { controller.current = null; setPending(false); }
    }
  }

  function cancel() {
    generation.current += 1; controller.current?.abort(); controller.current = null;
    setPending(false); setNotice("Stopped waiting. You can generate again.");
  }

  if (purchaseCount < 1) return null;
  return <section className="spending-insights" aria-labelledby="insights-title">
    <div className="insights-heading">
      <div><h2 id="insights-title">Ideas for your next shop</h2>
        <p>Checks and next steps supported by your recorded purchases.</p></div>
      <button type="button" className="button button-primary" onClick={() => void generate()} disabled={pending}>
        {pending ? "Checking your purchases…" : result ? "Regenerate suggestions" : "AI suggestions for this period"}
      </button>
    </div>
    {!result && !pending && <details className="insights-privacy"><summary>What is shared with AI?</summary><p>Only minimized aggregate facts, dates and eligible action identifiers are sent on request. Receipt images, item names and account/purchase identifiers are excluded.</p></details>}
    {pending && <p role="status">Preparing evidence-backed suggestions… <button type="button" className="text-link" onClick={cancel}>Stop waiting</button></p>}
    {notice && <p role="status">{notice}</p>}
    {error && <p className="field-error" role="alert">{error} <button type="button" className="text-link" onClick={() => void generate()}>Try again</button></p>}
    {result && <>
      {result.status === "fallback" && <p role="status" className="insights-fallback">AI prioritization is unavailable. Here are checks supported by your records. <button type="button" className="text-link" onClick={() => void generate()}>Retry AI suggestions</button></p>}
      {result.status === "no_data" && <p role="status">No confirmed purchases remain in these dates. No AI call was made.</p>}
      {result.status !== "no_data" && result.cards.length === 0 && <p role="status" className="insights-empty">{result.notice} No AI call was needed.</p>}
      <div className="insights-grid">{result.cards.map(card => <article className="insight-card" key={card.id}>
        <h3>{card.title}</h3><p>{card.detail}</p>
        <button type="button" className="text-link" onClick={() => onAction(card.action)}>{actionLabel[card.action]}</button>
        {(card.evidence ?? []).length > 0 && <details><summary>Contributing purchases ({card.evidence?.length}{card.evidence_truncated ? "+" : ""})</summary>
          <ul>{(card.evidence ?? []).map(row => <li key={row.purchase_id}><button type="button" className="text-link" onClick={() => onPurchase(row.purchase_id)}>{row.purchased_on}</button><span>{money(row.total_cents)} receipt total</span></li>)}</ul>
          {card.evidence_truncated && <p>Showing the first 20 contributing purchases. Open purchase history for more.</p>}
        </details>}
      </article>)}</div>
      {result.cards.length > 0 && <p className="insights-footnote">{result.notice} Selected dates: {startDate} – {endDate}. Generated {new Date(result.generated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}. Regenerate after changes.</p>}
    </>}
  </section>;
}
