import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { Session } from "@supabase/supabase-js";
import { apiRequest, ApiError, getErrorText } from "../api/client";
import type { components } from "../api/types";
import type { SourceContext } from "./workflow";
import { PurchaseEditor } from "./PurchaseEditor";
import { formFromDraft, moneyFormat, purchaseFormSignature, validatePurchaseForm } from "./purchaseForm";
import type { PurchaseForm } from "./purchaseForm";

type DraftOut = components["schemas"]["DraftOut"];

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export function DraftReview({
  session,
  householdId,
  draftId,
  source,
  onBackToPurchases,
  onReenterSource,
}: {
  session: Session;
  householdId: string;
  draftId: string;
  source: SourceContext | undefined;
  onBackToPurchases: () => void;
  onReenterSource: () => void;
}) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<PurchaseForm | null>(null);
  const [baseline, setBaseline] = useState<PurchaseForm | null>(null);
  const [reviewAttempted, setReviewAttempted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [requiresReview, setRequiresReview] = useState(false);
  const [hasReviewed, setHasReviewed] = useState(false);
  const [confirmUncertain, setConfirmUncertain] = useState(false);
  const [busy, setBusy] = useState<"reload" | "save" | "confirm" | null>(null);
  const [confirmedPurchase, setConfirmedPurchase] = useState<components["schemas"]["PurchaseOut"] | null>(null);
  const requestController = useRef<AbortController | null>(null);
  const loadedRevision = useRef<string | null>(null);
  const uncertainRevision = useRef<string | null>(null);
  const draftQueryKey = ["user", session.user.id, "household", householdId, "draft", draftId] as const;

  const draftQuery = useQuery({
    queryKey: draftQueryKey,
    queryFn: ({ signal }) =>
      apiRequest<DraftOut>(
        `/households/${encodeURIComponent(householdId)}/purchase-drafts/${encodeURIComponent(draftId)}`,
        session.access_token,
        { signal },
      ),
    refetchOnMount: "always",
  });

  useEffect(() => {
    const draft = draftQuery.data;
    if (!draft || loadedRevision.current === draft.revision) return;
    loadedRevision.current = draft.revision;
    const initial = formFromDraft(draft.content, source?.receiptLineTotalsCents);
    setForm(initial);
    setBaseline(initial);
    setReviewAttempted(false);
    setRequiresReview(false);
    setHasReviewed(false);
  }, [draftQuery.data, source]);

  useEffect(() => () => requestController.current?.abort(), []);

  const result = useMemo(
    () => form
      ? validatePurchaseForm(form, "review")
      : { content: null, errors: [], calculatedTotalCents: null },
    [form],
  );
  const dirty = Boolean(form && baseline && purchaseFormSignature(form) !== purchaseFormSignature(baseline));
  const data = draftQuery.data;
  const readOnly = data?.status !== undefined && data.status !== "pending";
  const hasServerWarnings = Boolean(data?.warnings.length);
  const hasReportedMismatch = Boolean(
    data?.content.reported_total_cents != null
    && data.calculated_total_cents !== data.content.reported_total_cents,
  );
  const canConfirm = Boolean(
    data && form && result.content && result.errors.length === 0 && !dirty
    && hasReviewed && !requiresReview && !conflict && !readOnly
    && !hasServerWarnings && !hasReportedMismatch && busy === null,
  );

  function updateForm(next: PurchaseForm) {
    setForm(next);
    setError(null);
    setConfirmedPurchase(null);
    setConfirmUncertain(false);
  }

  function startRequest(kind: "reload" | "save" | "confirm"): AbortController {
    requestController.current?.abort();
    const controller = new AbortController();
    requestController.current = controller;
    setBusy(kind);
    setError(null);
    return controller;
  }

  async function reloadAfterConflict() {
    const controller = startRequest("reload");
    try {
      const result = await draftQuery.refetch();
      if (controller.signal.aborted) return;
      if (result.error) {
        setError(getErrorText(result.error));
        return;
      }
      if (!result.data) {
        setError("The latest draft could not be loaded.");
        return;
      }
      if (result.data.id !== draftId || result.data.household_id !== householdId) {
        setError("The returned draft does not match this household and review link.");
        return;
      }
      loadedRevision.current = result.data.revision;
      const latestForm = formFromDraft(result.data.content, source?.receiptLineTotalsCents);
      setForm(latestForm);
      setBaseline(latestForm);
      setReviewAttempted(false);
      setConflict(false);
      setRequiresReview(true);
      setHasReviewed(false);
      setConfirmedPurchase(null);
      setConfirmUncertain(false);
      uncertainRevision.current = null;
    } finally {
      if (!controller.signal.aborted) setBusy(null);
    }
  }

  async function reviewChanges() {
    if (!data || !form || !result.content || readOnly || conflict || busy !== null) {
      setReviewAttempted(true);
      return;
    }
    setReviewAttempted(true);
    if (result.errors.length > 0) return;

    const controller = startRequest("save");
    try {
      const updated = await apiRequest<DraftOut>(
        `/households/${encodeURIComponent(householdId)}/purchase-drafts/${encodeURIComponent(draftId)}`,
        session.access_token,
        {
          method: "PATCH",
          body: { revision: data.revision, content: result.content },
          signal: controller.signal,
        },
      );
      if (controller.signal.aborted) return;
      if (updated.id !== draftId || updated.household_id !== householdId) {
        setError("The server returned a draft that does not match this household and review link.");
        return;
      }
      queryClient.setQueryData(draftQueryKey, updated);
      loadedRevision.current = updated.revision;
      const savedForm = formFromDraft(updated.content, form.items.map((item) => item.receiptAmountCents));
      setForm(savedForm);
      setBaseline(savedForm);
      setReviewAttempted(false);
      setRequiresReview(false);
      setHasReviewed(true);
      setConflict(false);
      setConfirmUncertain(false);
      uncertainRevision.current = null;
      setError(null);
    } catch (requestError) {
      if (isAbort(requestError)) return;
      if (requestError instanceof ApiError && requestError.status === 409) {
        setConflict(true);
        setRequiresReview(true);
        setHasReviewed(false);
        setConfirmUncertain(false);
        setError("This draft changed elsewhere. Reload the latest version before reviewing it again.");
      } else {
        setError(getErrorText(requestError));
      }
    } finally {
      if (!controller.signal.aborted) setBusy(null);
    }
  }

  async function confirmPurchase(retryUncertain = false) {
    if (!data || !canConfirm) return;
    const revision = retryUncertain ? uncertainRevision.current : data.revision;
    if (!revision) return;
    uncertainRevision.current = revision;
    const controller = startRequest("confirm");
    try {
      const purchase = await apiRequest<components["schemas"]["PurchaseOut"]>(
        `/households/${encodeURIComponent(householdId)}/purchase-drafts/${encodeURIComponent(draftId)}/confirm`,
        session.access_token,
        {
          method: "POST",
          body: { revision },
          signal: controller.signal,
        },
      );
      if (controller.signal.aborted) return;
      if (purchase.draft_id !== draftId || purchase.household_id !== householdId) {
        setError("The confirmation response does not match this household and draft.");
        return;
      }
      setConfirmedPurchase(purchase);
      setError(null);
      setConfirmUncertain(false);
      uncertainRevision.current = null;
      void queryClient.invalidateQueries({
        queryKey: ["user", session.user.id, "household", householdId, "purchases"],
      });
      void queryClient.invalidateQueries({
        queryKey: ["user", session.user.id, "household", householdId, "draft", draftId],
      });
    } catch (requestError) {
      if (isAbort(requestError)) return;
      if (requestError instanceof ApiError && requestError.status === 409) {
        setConflict(true);
        setRequiresReview(true);
        setHasReviewed(false);
        setConfirmUncertain(false);
        uncertainRevision.current = null;
        setError("The draft revision is no longer current. Reload it and review the latest version before continuing.");
      } else {
        const rejected = requestError instanceof ApiError
          && requestError.status !== undefined
          && requestError.status >= 400
          && requestError.status < 500;
        setConfirmUncertain(!rejected);
        setError(getErrorText(requestError));
      }
    } finally {
      if (!controller.signal.aborted) setBusy(null);
    }
  }

  if (draftQuery.isPending) {
    return <section className="page-section draft-review"><p className="status-message" role="status">Loading draft…</p></section>;
  }

  if (draftQuery.error && !data) {
    return (
      <section className="page-section draft-review" aria-labelledby="draft-title">
        <button className="text-link back-link" onClick={onBackToPurchases}>Back to purchases</button>
        <h1 id="draft-title">Review purchase</h1>
        <div className="error-block" role="alert">
          <p>{getErrorText(draftQuery.error)}</p>
          <button className="button button-outline" onClick={() => void draftQuery.refetch()} disabled={draftQuery.isFetching}>
            {draftQuery.isFetching ? "Trying again…" : "Try again"}
          </button>
        </div>
      </section>
    );
  }

  if (!data || data.id !== draftId || data.household_id !== householdId) {
    return (
      <section className="page-section draft-review" aria-labelledby="draft-title">
        <button className="text-link back-link" onClick={onBackToPurchases}>Back to purchases</button>
        <h1 id="draft-title">Review purchase</h1>
        <p className="error-message" role="alert">This draft is unavailable or does not belong to the selected household.</p>
      </section>
    );
  }

  const statusLabel = data.status === "confirmed"
    ? "Confirmed"
    : data.status === "rejected"
      ? "Rejected"
      : "Pending · not recorded as a purchase";

  return (
    <section className="page-section draft-review" aria-labelledby="draft-title">
      <button className="text-link back-link" onClick={onBackToPurchases}>Back to purchases</button>
      <div className="review-title-row">
        <div>
          <p className="draft-status">{statusLabel}</p>
          <h1 id="draft-title">Review purchase</h1>
        </div>
      </div>

      {data.warnings.length > 0 && (
        <section className="extraction-notes warnings review-warnings" aria-labelledby="draft-warnings-title">
          <h2 id="draft-warnings-title">Please check</h2>
          <ul>{data.warnings.map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}</ul>
          <p>Confirmation stays unavailable until these checks are resolved and reviewed again.</p>
        </section>
      )}

      {conflict && (
        <div className="conflict-notice" role="alert">
          <p>{error ?? "This draft has changed on the server. Reload the latest version to continue."}</p>
          <button className="button button-outline" onClick={() => void reloadAfterConflict()} disabled={busy !== null}>
            {busy === "reload" ? "Reloading draft…" : "Reload draft"}
          </button>
        </div>
      )}

      {error && !conflict && (
        <div className="error-block" role="alert">
          <p>{error}</p>
          {busy === null && confirmUncertain && !readOnly && !confirmedPurchase && (
            <button className="button button-outline" onClick={() => void confirmPurchase(true)}>
              Retry confirmation
            </button>
          )}
        </div>
      )}

      {requiresReview && !conflict && (
        <p className="review-required-note" role="status">
          The latest server version is loaded. Review it again before confirming.
        </p>
      )}

      {confirmedPurchase && (
        <div className="confirmed-notice" role="status">
          <div>
            <strong>Purchase confirmed</strong>
            <span>{confirmedPurchase.merchant?.trim() || "Purchase"} · {moneyFormat.format(confirmedPurchase.total_cents / 100)}</span>
          </div>
          <button className="button button-primary" onClick={onBackToPurchases}>View purchases</button>
        </div>
      )}

      {source === undefined && (
        <div className="source-unavailable review-source-unavailable">
          The reviewed source is unavailable in this session. It cannot be retrieved or reconstructed from the draft. Return Home to provide and review the source again.
          <button className="text-link" onClick={onReenterSource}>Return Home to review the source again</button>
        </div>
      )}

      {form && (
        <form className="review-form" onSubmit={(event) => { event.preventDefault(); void reviewChanges(); }}>
          <PurchaseEditor
            form={form}
            onChange={updateForm}
            source={source}
            idPrefix="review"
            errors={result.errors}
            showErrors={reviewAttempted}
            disabled={readOnly || busy !== null || conflict || confirmedPurchase !== null}
            totalLabel="Calculated total"
            totalCents={dirty ? result.calculatedTotalCents ?? data.calculated_total_cents : data.calculated_total_cents}
            totalAwaiting={dirty}
          >
            {reviewAttempted && result.errors.length > 0 && (
              <div className="validation-errors" role="alert">
                <p>Complete the linked fields before reviewing.</p>
              </div>
            )}
            {hasServerWarnings && !dirty && (
              <p className="mismatch-blocker" role="status">
                Edit the purchase details to resolve the server checks. Then choose Review changes before confirming.
              </p>
            )}
            {!readOnly && !confirmedPurchase && (
              <div className="review-actions">
                {(!hasReviewed || dirty || requiresReview) && (
                  <button
                    className="button button-primary"
                    type="submit"
                    disabled={busy !== null || conflict}
                  >
                    {busy === "save"
                      ? "Reviewing changes…"
                      : dirty
                        ? "Review changes"
                        : requiresReview
                          ? "Review current draft"
                          : "Review purchase"}
                  </button>
                )}
                {hasReviewed && !dirty && !requiresReview && (
                  <button
                    className="button button-primary confirm-button"
                    type="button"
                    onClick={() => void confirmPurchase()}
                    disabled={!canConfirm}
                  >
                    {busy === "confirm" ? "Confirming…" : "Confirm purchase"}
                  </button>
                )}
              </div>
            )}
          </PurchaseEditor>
        </form>
      )}
    </section>
  );
}
