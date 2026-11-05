import { useQuery } from "@tanstack/react-query";
import type { Session } from "@supabase/supabase-js";
import { apiRequest, getErrorText } from "../api/client";
import type { components } from "../api/types";

type DraftOut = components["schemas"]["DraftOut"];

const moneyFormat = new Intl.NumberFormat("de-DE", {
  style: "currency",
  currency: "EUR",
});

export function DraftHandoff({
  session,
  householdId,
  draftId,
  sourceText,
  onBack,
}: {
  session: Session;
  householdId: string;
  draftId: string;
  sourceText: string | undefined;
  onBack: () => void;
}) {
  const { data, error, isPending, isFetching, refetch } = useQuery({
    queryKey: ["user", session.user.id, "household", householdId, "draft", draftId],
    queryFn: ({ signal }) =>
      apiRequest<DraftOut>(
        `/households/${encodeURIComponent(householdId)}/purchase-drafts/${encodeURIComponent(draftId)}`,
        session.access_token,
        { signal },
      ),
  });

  return (
    <section className="page-section draft-handoff" aria-labelledby="draft-title">
      <button className="text-link back-link" onClick={onBack}>Back to home</button>
      <h1 id="draft-title">Purchase draft</h1>
      {isPending && <p className="status-message" role="status">Loading draft…</p>}
      {error && (
        <div className="error-block" role="alert">
          <p>{getErrorText(error)}</p>
          <button className="button button-outline" onClick={() => void refetch()} disabled={isFetching}>
            {isFetching ? "Trying again…" : "Try again"}
          </button>
        </div>
      )}
      {data && (
        <div className="workflow-surface handoff-surface">
          <p className="draft-status">
            {data.status === "pending" ? "Pending · not recorded as a purchase" : `Draft status: ${data.status}`}
          </p>
          <h2>{data.content.merchant?.trim() || "Purchase"}</h2>
          <p className="draft-meta">
            {new Intl.DateTimeFormat("de-DE", { dateStyle: "medium" }).format(
              new Date(`${data.content.purchased_on}T12:00:00`),
            )}
            <span>{moneyFormat.format(data.calculated_total_cents / 100)}</span>
          </p>
          <ul className="handoff-items" aria-label="Draft items">
            {data.content.items.map((item, index) => (
              <li key={`${item.original_text}-${index}`}>
                <span>{item.original_text}</span>
                <span>{String(item.quantity).replace(".", ",")} {item.quantity_unit}</span>
                <span>{moneyFormat.format(item.unit_price_cents / 100)}</span>
              </li>
            ))}
          </ul>
          {data.warnings.length > 0 && (
            <div className="extraction-notes warnings">
              <h2>Draft notes</h2>
              <ul>{data.warnings.map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}</ul>
            </div>
          )}
          {sourceText === undefined ? (
            <p className="source-unavailable">
              The reviewed source text is unavailable in this session. Return Home to provide and review the source again; it cannot be reconstructed from the draft.
            </p>
          ) : (
            <section className="source-snapshot" aria-labelledby="source-snapshot-title">
              <h2 id="source-snapshot-title">Reviewed source</h2>
              <pre>{sourceText}</pre>
            </section>
          )}
          <p className="handoff-next-step">Final purchase review is the next implementation step.</p>
        </div>
      )}
    </section>
  );
}
