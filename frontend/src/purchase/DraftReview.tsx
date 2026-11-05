import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { Session } from "@supabase/supabase-js";
import { apiRequest, ApiError, getErrorText } from "../api/client";
import type { components } from "../api/types";

type DraftOut = components["schemas"]["DraftOut"];
type DraftContent = components["schemas"]["PurchaseContent-Output"];
type DraftContentInput = components["schemas"]["PurchaseContent-Input"];
type DraftItem = DraftContent["items"][number];
type DraftAdjustment = components["schemas"]["AdjustmentInput"];
type Category = DraftItem["category"];
type Unit = DraftItem["quantity_unit"];
type QuantityUnit = Unit | "";

type ItemForm = {
  productId: string | null;
  description: string;
  quantity: string;
  unit: QuantityUnit;
  unitPrice: string;
  category: Category;
};

type AdjustmentForm = {
  kind: DraftAdjustment["kind"];
  label: string;
  amount: string;
  category: Category;
};

type DraftForm = {
  merchant: string;
  purchasedOn: string;
  reportedTotal: string;
  items: ItemForm[];
  adjustments: AdjustmentForm[];
};

type FormResult = {
  content: DraftContentInput | null;
  errors: string[];
};

const categories: { value: Category; label: string }[] = [
  { value: "groceries", label: "Groceries" },
  { value: "household", label: "Household" },
  { value: "personal_care", label: "Personal care" },
  { value: "transport", label: "Transport" },
  { value: "dining_out", label: "Dining out" },
  { value: "other", label: "Other" },
];

const units: { value: NonNullable<Unit>; label: string }[] = [
  { value: "pack", label: "pack" },
  { value: "each", label: "each" },
  { value: "kg", label: "kg" },
  { value: "l", label: "l" },
];

const moneyFormat = new Intl.NumberFormat("de-DE", {
  style: "currency",
  currency: "EUR",
});

function centsInput(value: number | null | undefined): string {
  if (value === null || value === undefined) return "";
  const absolute = Math.abs(value);
  return `${Math.floor(absolute / 100)},${String(absolute % 100).padStart(2, "0")}`;
}

function parseCents(value: string, maximum: number): number | null {
  const match = /^(\d+)(?:[,.](\d{1,2}))?$/.exec(value.trim());
  if (!match) return null;
  const cents = BigInt(match[1]) * 100n + BigInt((match[2] ?? "").padEnd(2, "0"));
  if (cents > BigInt(maximum)) return null;
  return Number(cents);
}

function parseQuantity(value: string): string | null {
  const match = /^(\d+)(?:[,.](\d{1,3}))?$/.exec(value.trim());
  if (!match) return null;
  const milli = BigInt(match[1]) * 1000n + BigInt((match[2] ?? "").padEnd(3, "0"));
  if (milli <= 0n || milli > 1_000_000_000n) return null;
  return `${match[1]}${match[2] ? `.${match[2]}` : ""}`;
}

function toForm(content: DraftContent): DraftForm {
  return {
    merchant: content.merchant ?? "",
    purchasedOn: content.purchased_on,
    reportedTotal: centsInput(content.reported_total_cents),
    items: content.items.map((item) => ({
      productId: item.product_id ?? null,
      description: item.original_text,
      quantity: item.quantity.replace(".", ","),
      unit: item.quantity_unit,
      unitPrice: centsInput(item.unit_price_cents),
      category: item.category,
    })),
    adjustments: (content.adjustments ?? []).map((adjustment) => ({
      kind: adjustment.kind,
      label: adjustment.label,
      amount: centsInput(adjustment.amount_cents),
      category: adjustment.category,
    })),
  };
}

function formResult(form: DraftForm): FormResult {
  const errors: string[] = [];
  if (!form.purchasedOn || !/^\d{4}-\d{2}-\d{2}$/.test(form.purchasedOn)) {
    errors.push("Enter a valid purchase date.");
  }
  if (form.merchant.trim().length > 200) errors.push("Merchant name must be 200 characters or fewer.");
  if (form.items.length < 1) errors.push("Add at least one item.");
  if (form.items.length > 200) errors.push("A purchase can contain at most 200 items.");
  if (form.adjustments.length > 30) errors.push("A purchase can contain at most 30 adjustments.");

  const items = form.items.map((item, index) => {
    const position = index + 1;
    const quantity = parseQuantity(item.quantity);
    const unitPriceCents = parseCents(item.unitPrice, 1_000_000_000);
    if (!item.description.trim()) errors.push(`Enter a description for item ${position}.`);
    if (item.description.trim().length > 300) errors.push(`Item ${position} description must be 300 characters or fewer.`);
    if (quantity === null) errors.push(`Enter a valid quantity for item ${position}.`);
    if (!item.unit) errors.push(`Choose a unit for item ${position}.`);
    if (unitPriceCents === null) errors.push(`Enter a valid unit price for item ${position}.`);

    return {
      ...(item.productId ? { product_id: item.productId } : {}),
      original_text: item.description.trim(),
      quantity: quantity ?? "",
      quantity_unit: item.unit,
      unit_price_cents: unitPriceCents ?? 0,
      category: item.category,
    };
  });

  let reportedTotalCents: number | null = null;
  if (form.reportedTotal.trim()) {
    reportedTotalCents = parseCents(form.reportedTotal, 1_000_000_000_000);
    if (reportedTotalCents === null) errors.push("Enter a valid reported total or leave it blank.");
  }

  const adjustments = form.adjustments.map((adjustment, index) => {
    const position = index + 1;
    const amountCents = parseCents(adjustment.amount, 1_000_000_000_000);
    if (!adjustment.label.trim()) errors.push(`Enter a label for adjustment ${position}.`);
    if (adjustment.label.trim().length > 120) errors.push(`Adjustment ${position} label must be 120 characters or fewer.`);
    if (amountCents === null || amountCents === 0) errors.push(`Enter a valid non-zero amount for adjustment ${position}.`);
    return {
      kind: adjustment.kind,
      label: adjustment.label.trim(),
      amount_cents: adjustment.kind === "discount" ? -(amountCents ?? 0) : (amountCents ?? 0),
      category: adjustment.category,
    };
  });

  if (errors.length > 0) return { content: null, errors };

  return {
    content: {
      merchant: form.merchant.trim() || null,
      purchased_on: form.purchasedOn,
      currency: "EUR",
      items: items.map((item) => ({
        ...item,
        quantity_unit: item.quantity_unit as NonNullable<Unit>,
      })),
      adjustments,
      reported_total_cents: reportedTotalCents,
    },
    errors,
  };
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export function DraftReview({
  session,
  householdId,
  draftId,
  sourceText,
  onBackToPurchases,
  onReenterSource,
}: {
  session: Session;
  householdId: string;
  draftId: string;
  sourceText: string | undefined;
  onBackToPurchases: () => void;
  onReenterSource: () => void;
}) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<DraftForm | null>(null);
  const [baseline, setBaseline] = useState<DraftForm | null>(null);
  const [reviewAttempted, setReviewAttempted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [requiresReview, setRequiresReview] = useState(false);
  const [hasReviewed, setHasReviewed] = useState(false);
  const [confirmUncertain, setConfirmUncertain] = useState(false);
  const [busy, setBusy] = useState<"reload" | "save" | "confirm" | null>(null);
  const [confirmedPurchase, setConfirmedPurchase] = useState<components["schemas"]["PurchaseOut"] | null>(null);
  const [sourceOpen, setSourceOpen] = useState(false);
  const sourceDialog = useRef<HTMLDialogElement>(null);
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
    const initial = toForm(draft.content);
    setForm(initial);
    setBaseline(initial);
    setReviewAttempted(false);
    setRequiresReview(false);
    setHasReviewed(false);
  }, [draftQuery.data]);

  useEffect(() => () => requestController.current?.abort(), []);

  useEffect(() => {
    if (!sourceOpen || !sourceDialog.current) return;
    sourceDialog.current.showModal();
  }, [sourceOpen]);

  const result = useMemo(() => form ? formResult(form) : { content: null, errors: [] }, [form]);
  const dirty = Boolean(form && baseline && JSON.stringify(form) !== JSON.stringify(baseline));
  const data = draftQuery.data;
  const readOnly = data?.status !== undefined && data.status !== "pending";
  const canConfirm = Boolean(data && form && result.content && !dirty && hasReviewed && !requiresReview && !conflict && !readOnly && busy === null);

  function updateForm<K extends keyof DraftForm>(key: K, value: DraftForm[K]) {
    setForm((current) => current ? { ...current, [key]: value } : current);
    setError(null);
    setConfirmedPurchase(null);
    setConfirmUncertain(false);
  }

  function updateItem(index: number, patch: Partial<ItemForm>) {
    if (!form) return;
    updateForm("items", form.items.map((item, itemIndex) =>
      itemIndex === index ? { ...item, ...patch } : item,
    ));
  }

  function updateAdjustment(index: number, patch: Partial<AdjustmentForm>) {
    if (!form) return;
    updateForm("adjustments", form.adjustments.map((adjustment, adjustmentIndex) =>
      adjustmentIndex === index ? { ...adjustment, ...patch } : adjustment,
    ));
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
      const latestForm = toForm(result.data.content);
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
      const savedForm = toForm(updated.content);
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
        setConfirmUncertain(true);
        setError(getErrorText(requestError));
      }
    } finally {
      if (!controller.signal.aborted) setBusy(null);
    }
  }

  function closeSourceViewer() {
    sourceDialog.current?.close();
    setSourceOpen(false);
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
        <button className="button button-outline mobile-source-button" onClick={() => setSourceOpen(true)} disabled={sourceText === undefined}>
          View source text
        </button>
      </div>

      {data.warnings.length > 0 && (
        <section className="extraction-notes warnings review-warnings" aria-labelledby="draft-warnings-title">
          <h2 id="draft-warnings-title">Please check</h2>
          <ul>{data.warnings.map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}</ul>
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

      {sourceText === undefined && (
        <div className="source-unavailable review-source-unavailable">
          The reviewed source text is unavailable in this session. It cannot be reconstructed from the draft. Return Home to enter and review the source again.
          <button className="text-link" onClick={onReenterSource}>Return Home to review source again</button>
        </div>
      )}

      <div className={sourceText === undefined ? "review-layout source-missing" : "review-layout"}>
        {sourceText !== undefined && (
          <section className="review-source-pane" aria-labelledby="source-pane-title">
            <h2 id="source-pane-title">Reviewed source</h2>
            <p>This text is read-only and separate from the purchase details.</p>
            <pre>{sourceText}</pre>
          </section>
        )}

        <form className="review-form" onSubmit={(event) => { event.preventDefault(); void reviewChanges(); }}>
          <fieldset disabled={readOnly || busy !== null || conflict || confirmedPurchase !== null}>
            <legend>Purchase details</legend>
            <div className="review-fields">
              <div className="review-field">
                <label htmlFor="review-merchant">Merchant <span className="optional">(optional)</span></label>
                <input id="review-merchant" value={form?.merchant ?? ""} maxLength={200} onChange={(event) => updateForm("merchant", event.target.value)} />
              </div>
              <div className="review-field">
                <label htmlFor="review-date">Purchase date</label>
                <input id="review-date" type="date" value={form?.purchasedOn ?? ""} onChange={(event) => updateForm("purchasedOn", event.target.value)} />
              </div>
              {form?.items.map((item, index) => (
                <fieldset className="review-item" key={index}>
                  <legend>Item {index + 1}</legend>
                  <div className="review-item-fields">
                    <div className="review-field item-description-field">
                      <label htmlFor={`review-item-description-${index}`}>Description</label>
                      <input id={`review-item-description-${index}`} value={item.description} maxLength={300} onChange={(event) => updateItem(index, { description: event.target.value })} />
                    </div>
                    <div className="review-field">
                      <label htmlFor={`review-item-quantity-${index}`}>Quantity</label>
                      <input id={`review-item-quantity-${index}`} inputMode="decimal" value={item.quantity} onChange={(event) => updateItem(index, { quantity: event.target.value })} />
                    </div>
                    <div className="review-field">
                      <label htmlFor={`review-item-unit-${index}`}>Unit</label>
                      <select id={`review-item-unit-${index}`} value={item.unit} onChange={(event) => updateItem(index, { unit: event.target.value as QuantityUnit })}>
                        <option value="">Choose unit</option>
                        {units.map((unit) => <option value={unit.value} key={unit.value}>{unit.label}</option>)}
                      </select>
                    </div>
                    <div className="review-field">
                      <label htmlFor={`review-item-price-${index}`}>Unit price (€)</label>
                      <input id={`review-item-price-${index}`} inputMode="decimal" value={item.unitPrice} onChange={(event) => updateItem(index, { unitPrice: event.target.value })} />
                    </div>
                    <div className="review-field">
                      <label htmlFor={`review-item-category-${index}`}>Category</label>
                      <select id={`review-item-category-${index}`} value={item.category} onChange={(event) => updateItem(index, { category: event.target.value as Category })}>
                        {categories.map((category) => <option value={category.value} key={category.value}>{category.label}</option>)}
                      </select>
                    </div>
                  </div>
                </fieldset>
              ))}

              <fieldset className="review-adjustments">
                <legend>Adjustments</legend>
                <div className="adjustment-editors">
                  {form?.adjustments.map((adjustment, index) => (
                    <fieldset className="adjustment-editor" key={index}>
                      <legend>Adjustment {index + 1}</legend>
                      <div className="adjustment-fields">
                        <div className="field">
                          <label htmlFor={`review-adjustment-label-${index}`}>Description</label>
                          <input id={`review-adjustment-label-${index}`} value={adjustment.label} maxLength={120} onChange={(event) => setForm((current) => current ? { ...current, adjustments: current.adjustments.map((row, rowIndex) => rowIndex === index ? { ...row, label: event.target.value } : row) } : current)} />
                        </div>
                        <div className="field">
                          <label htmlFor={`review-adjustment-kind-${index}`}>Type</label>
                          <select id={`review-adjustment-kind-${index}`} value={adjustment.kind} onChange={(event) => updateAdjustment(index, { kind: event.target.value as DraftAdjustment["kind"] })}>
                            <option value="discount">Discount</option>
                            <option value="deposit">Deposit</option>
                          </select>
                        </div>
                        <div className="field">
                          <label htmlFor={`review-adjustment-amount-${index}`}>Amount (€)</label>
                          <input id={`review-adjustment-amount-${index}`} inputMode="decimal" value={adjustment.amount} onChange={(event) => updateAdjustment(index, { amount: event.target.value })} />
                        </div>
                        <div className="field">
                          <label htmlFor={`review-adjustment-category-${index}`}>Category</label>
                          <select id={`review-adjustment-category-${index}`} value={adjustment.category} onChange={(event) => updateAdjustment(index, { category: event.target.value as Category })}>
                            {categories.map((category) => <option value={category.value} key={category.value}>{category.label}</option>)}
                          </select>
                        </div>
                      </div>
                      <button className="remove-item" type="button" onClick={() => updateForm("adjustments", form!.adjustments.filter((_, rowIndex) => rowIndex !== index))}>
                        Remove adjustment {index + 1}
                      </button>
                    </fieldset>
                  ))}
                </div>
                {(!readOnly && !confirmedPurchase) && (
                  <button
                    className="text-link add-adjustment"
                    type="button"
                    onClick={() => updateForm("adjustments", [...(form?.adjustments ?? []), { kind: "discount", label: "", amount: "", category: "other" }])}
                    disabled={(form?.adjustments.length ?? 0) >= 30}
                  >
                    Add adjustment
                  </button>
                )}
              </fieldset>

              <div className="review-field reported-total">
                <label htmlFor="review-reported-total">Reported total (€) <span className="optional">(optional)</span></label>
                <input id="review-reported-total" inputMode="decimal" value={form?.reportedTotal ?? ""} onChange={(event) => updateForm("reportedTotal", event.target.value)} placeholder="Leave blank if unknown" />
              </div>
            </div>
          </fieldset>

          {reviewAttempted && result.errors.length > 0 && (
            <div className="validation-errors" role="alert">
              <p>Complete these fields before reviewing:</p>
              <ul>{result.errors.map((message) => <li key={message}>{message}</li>)}</ul>
            </div>
          )}

          <section className={dirty ? "review-total awaiting" : "review-total"} aria-live="polite">
            <div>
              <span>Calculated total</span>
              {dirty && <small>Awaiting server recalculation</small>}
            </div>
            <strong>{moneyFormat.format(data.calculated_total_cents / 100)}</strong>
          </section>
          {form?.reportedTotal.trim() && result.content?.reported_total_cents != null && (
            <p className="reported-total-readout">
              Reported total: {moneyFormat.format(result.content.reported_total_cents / 100)}
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
        </form>
      </div>

      {sourceText !== undefined && (
        <dialog
          ref={sourceDialog}
          className="source-dialog"
          aria-labelledby="source-dialog-title"
          onClose={() => setSourceOpen(false)}
        >
          <div className="source-dialog-header">
            <h2 id="source-dialog-title">Reviewed source text</h2>
            <button className="button button-outline" onClick={closeSourceViewer}>Close</button>
          </div>
          <pre>{sourceText}</pre>
        </dialog>
      )}
    </section>
  );
}
