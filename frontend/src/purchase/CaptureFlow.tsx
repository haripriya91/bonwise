import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import type { Session } from "@supabase/supabase-js";
import { apiRequest, getErrorText } from "../api/client";
import type { components } from "../api/types";
import { getDraftScopeKey, getPendingDraftSubmission, prepareDraftSubmission } from "./workflow";

type Category = components["schemas"]["ItemInput-Input"]["category"];
type Unit = components["schemas"]["ItemInput-Input"]["quantity_unit"];
type Adjustment = components["schemas"]["AdjustmentInput"];
type ItemForm = {
  description: string;
  quantity: string;
  unit: Unit | "";
  unitPrice: string;
  category: Category;
};
type AdjustmentForm = {
  kind: Adjustment["kind"];
  label: string;
  amount: string;
  category: Category;
};
type Extraction = components["schemas"]["ExtractionOut"];
type PrivacyPreview = components["schemas"]["PrivacyPreview"];
type DraftOut = components["schemas"]["DraftOut"];
type DraftCreate = components["schemas"]["DraftCreate"];

const categories: { value: Category; label: string }[] = [
  { value: "groceries", label: "Groceries" },
  { value: "household", label: "Household" },
  { value: "personal_care", label: "Personal care" },
  { value: "transport", label: "Transport" },
  { value: "dining_out", label: "Dining out" },
  { value: "other", label: "Other" },
];

const units: { value: Unit; label: string }[] = [
  { value: "pack", label: "pack" },
  { value: "each", label: "each" },
  { value: "kg", label: "kg" },
  { value: "l", label: "l" },
];

function centsInput(value: number | null | undefined): string {
  if (value === null || value === undefined) return "";
  return `${Math.floor(value / 100)},${String(value % 100).padStart(2, "0")}`;
}

function parseCents(value: string, maximum = 1_000_000_000): number | null {
  const match = /^(\d+)(?:[,.](\d{1,2}))?$/.exec(value.trim());
  if (!match) return null;
  const cents = Number(match[1]) * 100 + Number((match[2] ?? "").padEnd(2, "0"));
  return Number.isSafeInteger(cents) && cents <= maximum ? cents : null;
}

function parseQuantity(value: string): string | null {
  const match = /^(\d+)(?:[,.](\d{1,3}))?$/.exec(value.trim());
  if (!match) return null;
  const milli = BigInt(match[1]) * 1000n + BigInt((match[2] ?? "").padEnd(3, "0"));
  if (milli <= 0n || milli > 1_000_000_000n) return null;
  return `${match[1]}${match[2] ? `.${match[2]}` : ""}`;
}

function initialForm(extraction: Extraction): {
  merchant: string;
  purchasedOn: string;
  reportedTotal: string;
  items: ItemForm[];
  adjustments: AdjustmentForm[];
} {
  const candidate = extraction.candidate;
  const extracted = extraction.extracted;
  return {
    merchant: candidate?.merchant ?? extracted.merchant ?? "",
    purchasedOn: candidate?.purchased_on ?? extracted.purchased_on ?? "",
    reportedTotal: centsInput(candidate?.reported_total_cents ?? extracted.reported_total_cents),
    items: (candidate?.items ?? extracted.items).map((item) => ({
      description: item.original_text,
      quantity: item.quantity == null ? "" : String(item.quantity).replace(".", ","),
      unit: item.quantity_unit ?? "",
      unitPrice: centsInput(item.unit_price_cents),
      category: item.category,
    })),
    adjustments: (candidate?.adjustments ?? extracted.adjustments).map((adjustment) => ({
      kind: adjustment.kind,
      label: adjustment.label,
      amount: centsInput(Math.abs(adjustment.amount_cents)),
      category: adjustment.category,
    })),
  };
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function PrivacyFindingLabel({ name }: { name: string }) {
  const labels: Record<string, string> = {
    email: "Email addresses",
    url: "Web addresses",
    iban: "IBANs",
    phone: "Phone numbers",
    long_identifier: "Long number identifiers",
    card_number: "Card numbers",
    masked_card: "Masked card numbers",
    street: "Street addresses",
    postcode_city: "Postcodes and cities",
    person_title: "Names",
    sensitive_line: "Sensitive lines",
  };
  return <>{labels[name] ?? name.replaceAll("_", " ")}</>;
}

export function CaptureFlow({
  session,
  householdId,
  onDraftCreated,
}: {
  session: Session;
  householdId: string;
  onDraftCreated: (draft: DraftOut, sourceText: string) => void;
}) {
  const [step, setStep] = useState<"entry" | "privacy" | "details">("entry");
  const [sourceText, setSourceText] = useState("");
  const [preview, setPreview] = useState<PrivacyPreview | null>(null);
  const [reviewedText, setReviewedText] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [extraction, setExtraction] = useState<Extraction | null>(null);
  const [form, setForm] = useState<ReturnType<typeof initialForm> | null>(null);
  const [busy, setBusy] = useState<"privacy" | "extraction" | "draft" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const scopeKey = getDraftScopeKey(session.user.id, householdId);
  const pendingSubmission = getPendingDraftSubmission(scopeKey);

  useEffect(() => () => controller.current?.abort(), []);

  function beginRequest(kind: "privacy" | "extraction" | "draft") {
    controller.current?.abort();
    const requestController = new AbortController();
    controller.current = requestController;
    setBusy(kind);
    setError(null);
    return requestController;
  }

  async function requestPrivacyPreview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!sourceText.trim()) return;
    const request = beginRequest("privacy");
    try {
      const result = await apiRequest<PrivacyPreview>(
        `/households/${encodeURIComponent(householdId)}/extraction/privacy-preview`,
        session.access_token,
        { method: "POST", body: { text: sourceText }, signal: request.signal },
      );
      setPreview(result);
      setReviewedText(result.reviewed_text);
      setSourceText("");
      setAcknowledged(false);
      setStep("privacy");
    } catch (requestError) {
      if (!isAbort(requestError)) setError(getErrorText(requestError));
    } finally {
      if (!request.signal.aborted) setBusy(null);
    }
  }

  async function requestExtraction() {
    if (!preview || !acknowledged || !reviewedText.trim()) return;
    const exactSubmittedText = reviewedText;
    const request = beginRequest("extraction");
    try {
      const result = await apiRequest<Extraction>(
        `/households/${encodeURIComponent(householdId)}/extraction/text`,
        session.access_token,
        {
          method: "POST",
          body: { reviewed_text: exactSubmittedText, privacy_reviewed: true },
          signal: request.signal,
        },
      );
      setExtraction(result);
      setForm(initialForm(result));
      setStep("details");
    } catch (requestError) {
      if (!isAbort(requestError)) setError(getErrorText(requestError));
    } finally {
      if (!request.signal.aborted) setBusy(null);
    }
  }

  function updateForm<K extends keyof NonNullable<typeof form>>(
    key: K,
    value: NonNullable<typeof form>[K],
  ) {
    setForm((current) => current ? { ...current, [key]: value } : current);
  }

  function buildPayload(): Omit<DraftCreate, "client_request_id"> | null {
    if (!form) return null;
    const issues: string[] = [];
    if (!form.purchasedOn) issues.push("Add the purchase date.");
    if (form.items.length === 0) issues.push("Add at least one item.");
    const items = form.items.map((item, index) => {
      const position = index + 1;
      const quantity = parseQuantity(item.quantity);
      const unitPriceCents = parseCents(item.unitPrice);
      if (!item.description.trim()) issues.push(`Add a description for item ${position}.`);
      if (!quantity) issues.push(`Enter a valid quantity for item ${position}.`);
      if (!item.unit) issues.push(`Choose a unit for item ${position}.`);
      if (unitPriceCents === null) issues.push(`Enter a valid unit price for item ${position}.`);
      return {
        original_text: item.description.trim(),
        quantity: quantity ?? "",
        quantity_unit: item.unit,
        unit_price_cents: unitPriceCents ?? 0,
        category: item.category,
      };
    });
    const reportedTotalCents = form.reportedTotal.trim()
      ? parseCents(form.reportedTotal, 1_000_000_000_000)
      : null;
    if (form.reportedTotal.trim() && reportedTotalCents === null) {
      issues.push("Enter a valid reported total or leave it blank.");
    }
    const adjustments = form.adjustments.map((adjustment, index) => {
      const amountCents = parseCents(adjustment.amount, 1_000_000_000_000);
      if (!adjustment.label.trim()) issues.push(`Add a label for adjustment ${index + 1}.`);
      if (amountCents === null || amountCents === 0) {
        issues.push(`Enter a valid non-zero amount for adjustment ${index + 1}.`);
      }
      return {
        kind: adjustment.kind,
        label: adjustment.label.trim(),
        amount_cents: adjustment.kind === "discount" ? -(amountCents ?? 0) : (amountCents ?? 0),
        category: adjustment.category,
      };
    });
    if (issues.length > 0) {
      setValidationError(issues.join(" "));
      return null;
    }
    setValidationError(null);
    return {
      source_type: "text",
      content: {
        ...(form.merchant.trim() ? { merchant: form.merchant.trim() } : {}),
        purchased_on: form.purchasedOn,
        currency: "EUR",
        items: items.map((item) => ({
          original_text: item.original_text,
          quantity: item.quantity,
          quantity_unit: item.quantity_unit as Unit,
          unit_price_cents: item.unit_price_cents,
          category: item.category,
        })),
        adjustments,
        ...(reportedTotalCents === null ? {} : { reported_total_cents: reportedTotalCents }),
      },
    };
  }

  async function submitDraft(body: DraftCreate) {
    const request = beginRequest("draft");
    try {
      const draft = await apiRequest<DraftOut>(
        `/households/${encodeURIComponent(householdId)}/purchase-drafts`,
        session.access_token,
        { method: "POST", body, signal: request.signal },
      );
      onDraftCreated(draft, reviewedText);
    } catch (requestError) {
      if (!isAbort(requestError)) setError(getErrorText(requestError));
    } finally {
      if (!request.signal.aborted) setBusy(null);
    }
  }

  async function createDraft(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const payload = buildPayload();
    if (!payload) return;
    await submitDraft(prepareDraftSubmission(scopeKey, payload));
  }

  async function retryDraft() {
    const pending = getPendingDraftSubmission(scopeKey);
    if (pending) await submitDraft(pending);
  }

  function updateItem(index: number, patch: Partial<ItemForm>) {
    if (!form) return;
    updateForm("items", form.items.map((item, itemIndex) =>
      itemIndex === index ? { ...item, ...patch } : item,
    ));
  }

  return (
    <section className="capture-flow" aria-labelledby="capture-title">
      <div className="capture-heading">
        <h1 id="capture-title">
          {step === "entry" && "Start with your purchases."}
          {step === "privacy" && "Review the text"}
          {step === "details" && "Check purchase details"}
        </h1>
      </div>

      {step === "entry" && (
        <form className="entry-surface" onSubmit={(event) => void requestPrivacyPreview(event)}>
          <h2>Add a purchase</h2>
          <p className="capture-intro">Describe what you bought in German or English.</p>
          <label htmlFor="purchase-entry">Purchase description</label>
          <textarea
            id="purchase-entry"
            rows={4}
            value={sourceText}
            onChange={(event) => setSourceText(event.target.value)}
            placeholder="For example: 2 cartons of milk at 1,19 EUR each"
            required
            maxLength={16000}
            disabled={busy !== null}
          />
          {error && <p className="workflow-error" role="alert">{error}</p>}
          <div className="capture-footer">
            <span>Typed entry</span>
            <button className="button button-lime" type="submit" disabled={!sourceText.trim() || busy !== null}>
              {busy === "privacy" ? "Checking text…" : "Review privacy"}
            </button>
          </div>
        </form>
      )}

      {step === "privacy" && preview && (
        <section className="workflow-surface privacy-step" aria-labelledby="privacy-title">
          <h2 id="privacy-title">Privacy review</h2>
          <p className="workflow-copy">
            Review the text that will be sent for extraction. This preview is not a guarantee that all personal information has been removed.
          </p>
          {Object.keys(preview.findings).length > 0 && (
            <div className="privacy-findings" aria-label="Privacy preview findings">
              <p>Items removed from the preview:</p>
              <ul>
                {Object.entries(preview.findings).map(([name, count]) => (
                  <li key={name}><PrivacyFindingLabel name={name} />: {count}</li>
                ))}
              </ul>
            </div>
          )}
          {Object.keys(preview.findings).length === 0 && (
            <p className="privacy-no-findings">No removable details were detected. Please review the text yourself.</p>
          )}
          <label htmlFor="reviewed-text">Text to send for extraction</label>
          <textarea
            id="reviewed-text"
            rows={6}
            value={reviewedText}
            onChange={(event) => {
              setReviewedText(event.target.value);
              setAcknowledged(false);
            }}
            maxLength={16000}
            disabled={busy !== null}
          />
          <label className="acknowledgement">
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={(event) => setAcknowledged(event.target.checked)}
              disabled={busy !== null}
            />
            <span>I have checked this text and want to send it for extraction.</span>
          </label>
          {error && <p className="workflow-error" role="alert">{error}</p>}
          <div className="step-actions">
            <button
              className="button button-outline"
              type="button"
              onClick={() => { setStep("entry"); setPreview(null); setReviewedText(""); setAcknowledged(false); }}
              disabled={busy !== null}
            >
              Back
            </button>
            <button
              className="button button-primary"
              type="button"
              onClick={() => void requestExtraction()}
              disabled={!acknowledged || !reviewedText.trim() || busy !== null}
            >
              {busy === "extraction" ? "Extracting…" : "Continue to extraction"}
            </button>
          </div>
          {busy === "extraction" && (
            <p className="long-running-note" role="status">
              Extraction can take a little while. Please keep this page open.
            </p>
          )}
        </section>
      )}

      {step === "details" && extraction && form && (
        <form className="workflow-surface details-step" onSubmit={(event) => void createDraft(event)}>
          <p className="workflow-copy">
            Correct the extracted details before creating a pending draft. It will not be recorded as a purchase yet.
          </p>
          {extraction.missing_fields.length > 0 && (
            <section className="extraction-notes" aria-labelledby="missing-fields-title">
              <h2 id="missing-fields-title">Fields to check</h2>
              <ul>{extraction.missing_fields.map((field) => <li key={field}>{field}</li>)}</ul>
            </section>
          )}
          {extraction.warnings.length > 0 && (
            <section className="extraction-notes warnings" aria-labelledby="warnings-title">
              <h2 id="warnings-title">Extraction notes</h2>
              <ul>{extraction.warnings.map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}</ul>
            </section>
          )}
          <div className="purchase-fields">
            <div className="field-pair">
              <div className="field">
                <label htmlFor="merchant">Merchant <span className="optional">(optional)</span></label>
                <input id="merchant" value={form.merchant} maxLength={200} onChange={(event) => updateForm("merchant", event.target.value)} />
              </div>
              <div className="field">
                <label htmlFor="purchased-on">Purchase date</label>
                <input id="purchased-on" type="date" value={form.purchasedOn} onChange={(event) => updateForm("purchasedOn", event.target.value)} required />
              </div>
            </div>
            <div className="item-editor-heading">
              <h2>Items</h2>
              <button
                className="text-link"
                type="button"
                onClick={() => updateForm("items", [...form.items, { description: "", quantity: "", unit: "", unitPrice: "", category: "other" }])}
              >
                Add item
              </button>
            </div>
            {form.items.map((item, index) => (
              <fieldset className="item-editor" key={index}>
                <legend>Item {index + 1}</legend>
                <div className="item-fields">
                  <div className="field item-name-field">
                    <label htmlFor={`item-description-${index}`}>Description</label>
                    <input
                      id={`item-description-${index}`}
                      value={item.description}
                      maxLength={300}
                      onChange={(event) => updateItem(index, { description: event.target.value })}
                      required
                    />
                  </div>
                  <div className="field">
                    <label htmlFor={`item-quantity-${index}`}>Quantity</label>
                    <input
                      id={`item-quantity-${index}`}
                      inputMode="decimal"
                      value={item.quantity}
                      onChange={(event) => updateItem(index, { quantity: event.target.value })}
                      placeholder="Required"
                      required
                    />
                  </div>
                  <div className="field">
                    <label htmlFor={`item-unit-${index}`}>Unit</label>
                    <select id={`item-unit-${index}`} value={item.unit} onChange={(event) => updateItem(index, { unit: event.target.value as Unit | "" })} required>
                      <option value="">Choose</option>
                      {units.map((unit) => <option value={unit.value} key={unit.value}>{unit.label}</option>)}
                    </select>
                  </div>
                  <div className="field">
                    <label htmlFor={`item-price-${index}`}>Unit price (€)</label>
                    <input
                      id={`item-price-${index}`}
                      inputMode="decimal"
                      value={item.unitPrice}
                      onChange={(event) => updateItem(index, { unitPrice: event.target.value })}
                      placeholder="Required"
                      required
                    />
                  </div>
                  <div className="field">
                    <label htmlFor={`item-category-${index}`}>Category</label>
                    <select id={`item-category-${index}`} value={item.category} onChange={(event) => updateItem(index, { category: event.target.value as Category })}>
                      {categories.map((category) => <option value={category.value} key={category.value}>{category.label}</option>)}
                    </select>
                  </div>
                </div>
                {form.items.length > 1 && (
                  <button className="remove-item" type="button" onClick={() => updateForm("items", form.items.filter((_, itemIndex) => itemIndex !== index))}>
                    Remove item {index + 1}
                  </button>
                )}
              </fieldset>
            ))}
            <div className="field reported-total-field">
              <label htmlFor="reported-total">Reported total (€) <span className="optional">(optional)</span></label>
              <input
                id="reported-total"
                inputMode="decimal"
                value={form.reportedTotal}
                onChange={(event) => updateForm("reportedTotal", event.target.value)}
                placeholder="Leave blank if unknown"
              />
            </div>
            <section className="adjustments-readonly" aria-labelledby="adjustments-title">
                <div className="item-editor-heading">
                  <h2 id="adjustments-title">Adjustments</h2>
                  <button
                    className="text-link"
                    type="button"
                    onClick={() => updateForm("adjustments", [...form.adjustments, { kind: "discount", label: "", amount: "", category: "other" }])}
                  >
                    Add adjustment
                  </button>
                </div>
                <div className="adjustment-editors">
                  {form.adjustments.map((adjustment, index) => (
                    <fieldset className="adjustment-editor" key={index}>
                      <legend>Adjustment {index + 1}</legend>
                      <div className="adjustment-fields">
                        <div className="field">
                          <label htmlFor={`adjustment-label-${index}`}>Description</label>
                          <input
                            id={`adjustment-label-${index}`}
                            value={adjustment.label}
                            maxLength={120}
                            onChange={(event) => updateForm("adjustments", form.adjustments.map((value, row) => row === index ? { ...value, label: event.target.value } : value))}
                            required
                          />
                        </div>
                        <div className="field">
                          <label htmlFor={`adjustment-kind-${index}`}>Type</label>
                          <select
                            id={`adjustment-kind-${index}`}
                            value={adjustment.kind}
                            onChange={(event) => updateForm("adjustments", form.adjustments.map((value, row) => row === index ? { ...value, kind: event.target.value as Adjustment["kind"] } : value))}
                          >
                            <option value="discount">Discount</option>
                            <option value="deposit">Deposit</option>
                          </select>
                        </div>
                        <div className="field">
                          <label htmlFor={`adjustment-amount-${index}`}>Amount (€)</label>
                          <input
                            id={`adjustment-amount-${index}`}
                            inputMode="decimal"
                            value={adjustment.amount}
                            onChange={(event) => updateForm("adjustments", form.adjustments.map((value, row) => row === index ? { ...value, amount: event.target.value } : value))}
                            required
                          />
                        </div>
                        <div className="field">
                          <label htmlFor={`adjustment-category-${index}`}>Category</label>
                          <select
                            id={`adjustment-category-${index}`}
                            value={adjustment.category}
                            onChange={(event) => updateForm("adjustments", form.adjustments.map((value, row) => row === index ? { ...value, category: event.target.value as Category } : value))}
                          >
                            {categories.map((category) => <option value={category.value} key={category.value}>{category.label}</option>)}
                          </select>
                        </div>
                      </div>
                      <button
                        className="remove-item"
                        type="button"
                        onClick={() => updateForm("adjustments", form.adjustments.filter((_, row) => row !== index))}
                      >
                        Remove adjustment {index + 1}
                      </button>
                    </fieldset>
                  ))}
                </div>
            </section>
          </div>
          {validationError && <p className="workflow-error" role="alert">{validationError}</p>}
          {error && (
            <div className="error-block" role="alert">
              <p>{error}</p>
              {pendingSubmission && (
                <button className="button button-outline" type="button" onClick={() => void retryDraft()} disabled={busy !== null}>
                  Retry original submission
                </button>
              )}
            </div>
          )}
          <div className="step-actions">
            <button className="button button-outline" type="button" onClick={() => setStep("privacy")} disabled={busy !== null}>
              Back to privacy review
            </button>
            <button className="button button-primary" type="submit" disabled={busy !== null}>
              {busy === "draft" ? "Creating draft…" : "Review purchase"}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
