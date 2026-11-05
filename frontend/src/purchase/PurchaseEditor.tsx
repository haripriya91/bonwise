import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { SourceContext } from "./workflow";
import {
  apiQuantityFromGrams,
  categories,
  lineTotalForItem,
  moneyFormat,
  parseCents,
  quantityToGrams,
  units,
} from "./purchaseForm";
import type { FieldIssue, PurchaseForm } from "./purchaseForm";

function issueFor(errors: FieldIssue[], id: string): string | undefined {
  return errors.find((error) => error.id === id)?.message;
}

function FieldError({ id, errors, prefix }: { id: string; errors: FieldIssue[]; prefix: string }) {
  const message = issueFor(errors, id);
  return message ? <span className="field-error" id={`${prefix}-error`}>{message}</span> : null;
}

function ReadOnlySource({
  source,
  zoom,
  setZoom,
}: {
  source: SourceContext;
  zoom: number;
  setZoom: (update: (value: number) => number) => void;
}) {
  const image = source.type === "image";
  return (
    <>
      {image ? (
        <div className="image-viewer-controls" aria-label="Receipt zoom controls">
          <button type="button" className="button button-outline" onClick={() => setZoom((value) => Math.max(0.5, value - 0.25))} disabled={zoom <= 0.5}>Zoom out</button>
          <button type="button" className="button button-outline" onClick={() => setZoom(() => 1)}>Fit width</button>
          <button type="button" className="button button-outline" onClick={() => setZoom((value) => Math.min(3, value + 0.25))} disabled={zoom >= 3}>Zoom in</button>
          <span>{Math.round(zoom * 100)}%</span>
        </div>
      ) : null}
      {image ? (
        <div className="review-image-viewport" tabIndex={0} role="region" aria-label="Scrollable reviewed receipt image. Use zoom controls and scroll to read.">
          <div className="review-image-stage" style={{ width: `${zoom * 100}%` }}>
            <img src={source.dataUrl} alt="Read-only prepared receipt image sent for extraction" />
          </div>
        </div>
      ) : (
        <pre>{source.text}</pre>
      )}
    </>
  );
}

export function PurchaseEditor({
  form,
  onChange,
  source,
  idPrefix,
  errors,
  showErrors,
  disabled = false,
  totalLabel = "Calculated total",
  totalCents,
  totalAwaiting = false,
  children,
}: {
  form: PurchaseForm;
  onChange: (form: PurchaseForm) => void;
  source: SourceContext | undefined;
  idPrefix: string;
  errors: FieldIssue[];
  showErrors: boolean;
  disabled?: boolean;
  totalLabel?: string;
  totalCents: number | null;
  totalAwaiting?: boolean;
  children?: ReactNode;
}) {
  const [sourceOpen, setSourceOpen] = useState(false);
  const [sourceZoom, setSourceZoom] = useState(1);
  const sourceDialog = useRef<HTMLDialogElement>(null);
  const sourceTrigger = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (sourceOpen && sourceDialog.current && !sourceDialog.current.open) sourceDialog.current.showModal();
  }, [sourceOpen]);

  function closeSourceViewer() {
    sourceDialog.current?.close();
    setSourceOpen(false);
    requestAnimationFrame(() => sourceTrigger.current?.focus());
  }

  function update<K extends keyof PurchaseForm>(key: K, value: PurchaseForm[K]) {
    onChange({ ...form, [key]: value });
  }

  function updateItem(index: number, patch: Partial<PurchaseForm["items"][number]>) {
    update("items", form.items.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));
  }

  function updateAdjustment(index: number, patch: Partial<PurchaseForm["adjustments"][number]>) {
    update("adjustments", form.adjustments.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));
  }

  const reportedTotalCents = form.reportedTotal.trim()
    ? parseCents(form.reportedTotal, 1_000_000_000_000)
    : null;
  const difference = totalCents !== null && reportedTotalCents !== null
    ? totalCents - reportedTotalCents
    : null;

  return (
    <div className="purchase-editor">
      {source && (
        <button
          ref={sourceTrigger}
          className="button button-outline mobile-source-button"
          type="button"
          onClick={() => setSourceOpen(true)}
        >
          {source.type === "image" ? "View receipt" : "View source text"}
        </button>
      )}
      <div className={`purchase-editor-layout${source ? "" : " source-missing"}`}>
        {source && (
          <aside className="review-source-pane" aria-labelledby={`${idPrefix}-source-title`}>
            <h2 id={`${idPrefix}-source-title`}>{source.type === "image" ? "Reviewed receipt" : "Reviewed source text"}</h2>
            <p>{source.type === "image" ? "Prepared image sent for extraction. Read-only." : "Exact reviewed text sent for extraction. Read-only."}</p>
            {source.type === "image" ? (
              <div className="review-image-wrap">
                <div className="image-viewer-controls" aria-label="Receipt zoom controls">
                  <button type="button" className="button button-outline" onClick={() => setSourceZoom((value) => Math.max(0.5, value - 0.25))} disabled={sourceZoom <= 0.5}>Zoom out</button>
                  <button type="button" className="button button-outline" onClick={() => setSourceZoom(() => 1)}>Fit width</button>
                  <button type="button" className="button button-outline" onClick={() => setSourceZoom((value) => Math.min(3, value + 0.25))} disabled={sourceZoom >= 3}>Zoom in</button>
                  <span>{Math.round(sourceZoom * 100)}%</span>
                </div>
                <div className="review-image-viewport" tabIndex={0} role="region" aria-label="Scrollable reviewed receipt image. Use zoom controls and scroll to read.">
                  <div className="review-image-stage" style={{ width: `${sourceZoom * 100}%` }}>
                    <img src={source.dataUrl} alt="Read-only prepared receipt image sent for extraction" />
                  </div>
                </div>
              </div>
            ) : (
              <pre>{source.text}</pre>
            )}
          </aside>
        )}

        <section className="purchase-editor-fields">
          <fieldset disabled={disabled}>
            <legend>Purchase details</legend>
            {showErrors && errors.length > 0 && (
              <nav className="validation-errors" aria-label="Fields to correct">
                <p>Correct these fields to continue:</p>
                <ul>
                  {errors.map((error) => (
                    <li key={`${error.id}-${error.message}`}><a href={`#${error.id}`}>{error.message}</a></li>
                  ))}
                </ul>
              </nav>
            )}

            <div className="review-fields">
              <div className="review-field">
                <label htmlFor={`${idPrefix}-merchant`}>Merchant <span className="optional">(optional)</span></label>
                <input
                  id={`${idPrefix}-merchant`}
                  value={form.merchant}
                  maxLength={200}
                  aria-invalid={showErrors && Boolean(issueFor(errors, `${idPrefix}-merchant`))}
                  aria-describedby={showErrors ? `${idPrefix}-merchant-error` : undefined}
                  onChange={(event) => update("merchant", event.target.value)}
                />
                <FieldError id={`${idPrefix}-merchant`} errors={showErrors ? errors : []} prefix={`${idPrefix}-merchant`} />
              </div>
              <div className="review-field">
                <label htmlFor={`${idPrefix}-date`}>Purchase date</label>
                <input
                  id={`${idPrefix}-date`}
                  type="date"
                  value={form.purchasedOn}
                  aria-invalid={showErrors && Boolean(issueFor(errors, `${idPrefix}-date`))}
                  aria-describedby={showErrors ? `${idPrefix}-date-error` : undefined}
                  onChange={(event) => update("purchasedOn", event.target.value)}
                />
                <FieldError id={`${idPrefix}-date`} errors={showErrors ? errors : []} prefix={`${idPrefix}-date`} />
              </div>
              <div className="item-editor-heading">
                <h2 id={`${idPrefix}-items`}>Items</h2>
                {!disabled && (
                  <button
                    className="text-link"
                    type="button"
                    onClick={() => update("items", [...form.items, {
                      productId: null,
                      description: "",
                      quantity: "",
                      grams: null,
                      unit: "",
                      unitPrice: "",
                      category: "other",
                      receiptAmountCents: null,
                    }])}
                    disabled={form.items.length >= 200}
                  >
                    Add item
                  </button>
                )}
              </div>
              {form.items.map((item, index) => {
                const descriptionId = `${idPrefix}-item-description-${index}`;
                const quantityId = `${idPrefix}-item-quantity-${index}`;
                const unitId = `${idPrefix}-item-unit-${index}`;
                const priceId = `${idPrefix}-item-price-${index}`;
                const categoryId = `${idPrefix}-item-category-${index}`;
                const lineTotal = lineTotalForItem(item);
                const quantityIssue = issueFor(errors, quantityId);
                const descriptionIssue = issueFor(errors, descriptionId);
                const unitIssue = issueFor(errors, unitId);
                const priceIssue = issueFor(errors, priceId);
                return (
                  <fieldset className="review-item" key={`${idPrefix}-item-${index}`}>
                    <legend>Item {index + 1}</legend>
                    <div className="review-item-fields">
                      <div className="review-field item-description-field">
                        <label htmlFor={descriptionId}>Description</label>
                        <input
                          id={descriptionId}
                          value={item.description}
                          maxLength={300}
                          aria-invalid={showErrors && Boolean(descriptionIssue)}
                          aria-describedby={showErrors ? `${descriptionId}-error` : undefined}
                          onChange={(event) => updateItem(index, { description: event.target.value })}
                        />
                        <FieldError id={descriptionId} errors={showErrors ? errors : []} prefix={descriptionId} />
                      </div>
                      <div className="review-field">
                        <label htmlFor={quantityId}>Quantity bought{item.grams !== null ? " (g)" : ""}</label>
                        <input
                          id={quantityId}
                          inputMode="decimal"
                          value={item.grams ?? item.quantity}
                          aria-invalid={showErrors && Boolean(quantityIssue)}
                          aria-describedby={showErrors ? `${quantityId}-error` : undefined}
                          onChange={(event) => updateItem(index, item.grams === null
                            ? { quantity: event.target.value }
                            : {
                                grams: event.target.value,
                                quantity: apiQuantityFromGrams(event.target.value) ?? "",
                              })}
                        />
                        {item.unit === "kg" && !disabled && (
                          <label className="gram-entry-toggle">
                            <input
                              type="checkbox"
                              checked={item.grams !== null}
                              onChange={(event) => updateItem(index, {
                                grams: event.target.checked ? quantityToGrams(item.quantity) : null,
                              })}
                            />
                            Enter weight in whole grams
                          </label>
                        )}
                        {item.grams !== null && <small className="field-hint">Converted exactly to kilograms for the API.</small>}
                        <FieldError id={quantityId} errors={showErrors ? errors : []} prefix={quantityId} />
                      </div>
                      <div className="review-field">
                        <label htmlFor={unitId}>Unit</label>
                        <select
                          id={unitId}
                          value={item.unit}
                          aria-invalid={showErrors && Boolean(unitIssue)}
                          aria-describedby={showErrors ? `${unitId}-error` : undefined}
                          onChange={(event) => updateItem(index, {
                            unit: event.target.value as PurchaseForm["items"][number]["unit"],
                            grams: event.target.value === "kg" ? item.grams : null,
                          })}
                        >
                          <option value="">Choose unit</option>
                          {units.map((unit) => <option value={unit.value} key={unit.value}>{unit.label}</option>)}
                        </select>
                        <FieldError id={unitId} errors={showErrors ? errors : []} prefix={unitId} />
                      </div>
                      <div className="review-field">
                        <label htmlFor={priceId}>
                          {item.unit === "kg" ? "Price per kg (€)" : item.unit === "pack" ? "Price per pack (€)" : item.unit === "each" ? "Price per item (€)" : item.unit === "l" ? "Price per litre (€)" : "Unit price (€)"}
                        </label>
                        <input
                          id={priceId}
                          inputMode="decimal"
                          value={item.unitPrice}
                          aria-invalid={showErrors && Boolean(priceIssue)}
                          aria-describedby={showErrors ? `${priceId}-error` : undefined}
                          onChange={(event) => updateItem(index, { unitPrice: event.target.value })}
                        />
                        <FieldError id={priceId} errors={showErrors ? errors : []} prefix={priceId} />
                      </div>
                      <div className="review-field">
                        <label htmlFor={categoryId}>Category</label>
                        <select
                          id={categoryId}
                          value={item.category}
                          onChange={(event) => updateItem(index, { category: event.target.value as PurchaseForm["items"][number]["category"] })}
                        >
                          {categories.map((category) => <option value={category.value} key={category.value}>{category.label}</option>)}
                        </select>
                      </div>
                      <div className="line-total-readout">
                        <span>Calculated Line total</span>
                        <strong>{lineTotal === null ? "—" : moneyFormat.format(lineTotal / 100)}</strong>
                        {item.receiptAmountCents !== null && (
                          <span>Receipt amount: {moneyFormat.format(item.receiptAmountCents / 100)}</span>
                        )}
                      </div>
                    </div>
                    {!disabled && form.items.length > 1 && (
                      <button
                        className="remove-item"
                        type="button"
                        onClick={() => update("items", form.items.filter((_, itemIndex) => itemIndex !== index))}
                      >
                        Remove item {index + 1}
                      </button>
                    )}
                  </fieldset>
                );
              })}

              <fieldset className="review-adjustments">
                <legend id={`${idPrefix}-adjustments`}>Adjustments</legend>
                <div className="adjustment-editors">
                  {form.adjustments.map((adjustment, index) => {
                    const labelId = `${idPrefix}-adjustment-label-${index}`;
                    const kindId = `${idPrefix}-adjustment-kind-${index}`;
                    const amountId = `${idPrefix}-adjustment-amount-${index}`;
                    const categoryId = `${idPrefix}-adjustment-category-${index}`;
                    return (
                      <fieldset className="adjustment-editor" key={`${idPrefix}-adjustment-${index}`}>
                        <legend>Adjustment {index + 1}</legend>
                        <div className="adjustment-fields">
                          <div className="field">
                            <label htmlFor={labelId}>Description</label>
                            <input id={labelId} value={adjustment.label} maxLength={120} onChange={(event) => updateAdjustment(index, { label: event.target.value })} />
                            <FieldError id={labelId} errors={showErrors ? errors : []} prefix={labelId} />
                          </div>
                          <div className="field">
                            <label htmlFor={kindId}>Type</label>
                            <select id={kindId} value={adjustment.kind} onChange={(event) => updateAdjustment(index, { kind: event.target.value as PurchaseForm["adjustments"][number]["kind"] })}>
                              <option value="discount">Discount</option>
                              <option value="deposit">Deposit</option>
                            </select>
                          </div>
                          <div className="field">
                            <label htmlFor={amountId}>Amount (€)</label>
                            <input id={amountId} inputMode="decimal" value={adjustment.amount} onChange={(event) => updateAdjustment(index, { amount: event.target.value })} />
                            <FieldError id={amountId} errors={showErrors ? errors : []} prefix={amountId} />
                          </div>
                          <div className="field">
                            <label htmlFor={categoryId}>Category</label>
                            <select id={categoryId} value={adjustment.category} onChange={(event) => updateAdjustment(index, { category: event.target.value as PurchaseForm["adjustments"][number]["category"] })}>
                              {categories.map((category) => <option value={category.value} key={category.value}>{category.label}</option>)}
                            </select>
                          </div>
                        </div>
                        {!disabled && (
                          <button className="remove-item" type="button" onClick={() => update("adjustments", form.adjustments.filter((_, itemIndex) => itemIndex !== index))}>
                            Remove adjustment {index + 1}
                          </button>
                        )}
                      </fieldset>
                    );
                  })}
                </div>
                {!disabled && (
                  <button
                    className="text-link add-adjustment"
                    type="button"
                    onClick={() => update("adjustments", [...form.adjustments, { kind: "discount", label: "", amount: "", category: "other" }])}
                    disabled={form.adjustments.length >= 30}
                  >
                    Add adjustment
                  </button>
                )}
              </fieldset>

              <div className="review-field reported-total">
                <label htmlFor={`${idPrefix}-reported-total`}>Printed receipt total (€) <span className="optional">(optional)</span></label>
                <input
                  id={`${idPrefix}-reported-total`}
                  inputMode="decimal"
                  value={form.reportedTotal}
                  aria-invalid={showErrors && Boolean(issueFor(errors, `${idPrefix}-reported-total`))}
                  aria-describedby={showErrors ? `${idPrefix}-reported-total-error` : undefined}
                  onChange={(event) => update("reportedTotal", event.target.value)}
                  placeholder="Leave blank if unknown"
                />
                <FieldError id={`${idPrefix}-reported-total`} errors={showErrors ? errors : []} prefix={`${idPrefix}-reported-total`} />
              </div>
            </div>
          </fieldset>

          <section className={`review-total${totalAwaiting ? " awaiting" : ""}`} aria-live="polite">
            <div>
              <span>{totalLabel}</span>
              {totalAwaiting && <small>Awaiting backend recalculation</small>}
            </div>
            <strong>{totalCents === null ? "—" : moneyFormat.format(totalCents / 100)}</strong>
          </section>
          {reportedTotalCents !== null && (
            <div className="receipt-comparison">
              <span>Printed receipt total</span>
              <strong>{moneyFormat.format(reportedTotalCents / 100)}</strong>
              {totalCents !== null && (
                <>
                  <span>Difference (calculated − printed)</span>
                  <strong className={difference === 0 ? "" : "mismatch"}>
                    {difference !== null && difference > 0 ? "+" : ""}{difference === null ? "—" : moneyFormat.format(difference / 100)}
                  </strong>
                </>
              )}
            </div>
          )}
          {children}
        </section>
      </div>

      {source && (
        <dialog
          ref={sourceDialog}
          className="source-dialog"
          aria-labelledby={`${idPrefix}-source-dialog-title`}
          onClose={() => {
            setSourceOpen(false);
            requestAnimationFrame(() => sourceTrigger.current?.focus());
          }}
        >
          <div className="source-dialog-header">
            <h2 id={`${idPrefix}-source-dialog-title`}>{source.type === "image" ? "Reviewed receipt" : "Reviewed source text"}</h2>
            <button className="button button-outline" type="button" onClick={closeSourceViewer}>Close</button>
          </div>
          <ReadOnlySource source={source} zoom={sourceZoom} setZoom={setSourceZoom} />
        </dialog>
      )}
    </div>
  );
}
