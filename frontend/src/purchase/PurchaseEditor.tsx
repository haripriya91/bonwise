import { AIWorking } from "../components/AIWorking";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { Session } from "@supabase/supabase-js";
import { InformationTip } from "../components/InformationTip";
import { ApiError, apiRequest, getErrorText } from "../api/client";
import type { components } from "../api/types";
import type { SourceContext } from "./workflow";
import {
  apiQuantityFromGrams,
  categories,
  lineTotalForItem,
  moneyFormat,
  newPurchaseItemId,
  parseCents,
  quantityToGrams,
  units,
} from "./purchaseForm";
import type { FieldIssue, PurchaseForm } from "./purchaseForm";

type ProductSuggestion = components["schemas"]["ProductSuggestionOut"];
type ProductCandidate = components["schemas"]["ProductCandidateOut"];
type ProductSuggestionInput = components["schemas"]["ProductSuggestionInput"];

type ProductSuggestionState =
  | { status: "loading"; signature: string }
  | { status: "error"; signature: string; message: string }
  | { status: "result"; signature: string; result: ProductSuggestion };

function issueFor(errors: FieldIssue[], id: string): string | undefined {
  return errors.find((error) => error.id === id)?.message;
}

function ReceiptKeepControl({
  checked,
  disabled,
  onChange,
}: {
  checked: boolean;
  disabled: boolean;
  onChange: (keep: boolean) => void;
}) {
  return (
    <div className="receipt-keep-control">
      <label className="receipt-keep-option">
        <input
          type="checkbox"
          checked={checked}
          onChange={(event) => onChange(event.target.checked)}
          disabled={disabled}
        />
        <span>Keep this reviewed receipt with this purchase</span>
      </label>
      <p>Only the reviewed copy will be saved.</p>
    </div>
  );
}

function FieldError({ id, errors, prefix }: { id: string; errors: FieldIssue[]; prefix: string }) {
  const message = issueFor(errors, id);
  return message ? <span className="field-error" id={`${prefix}-error`}>{message}</span> : null;
}

function ReadOnlySource({
  source,
  zoom,
  setZoom,
  keepReceipt,
  onKeepReceiptChange,
  receiptOptionDisabled,
}: {
  source: SourceContext;
  zoom: number;
  setZoom: (update: (value: number) => number) => void;
  keepReceipt?: boolean;
  onKeepReceiptChange?: (keep: boolean) => void;
  receiptOptionDisabled: boolean;
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
        <>
          <div className="review-image-viewport" tabIndex={0} role="region" aria-label="Scrollable reviewed receipt image. Use zoom controls and scroll to read.">
            <div className="review-image-stage" style={{ width: `${zoom * 100}%` }}>
              <img src={source.dataUrl} alt="Receipt image used to read purchase details" />
            </div>
          </div>
          {keepReceipt !== undefined && onKeepReceiptChange && (
            <ReceiptKeepControl
              checked={keepReceipt}
              disabled={receiptOptionDisabled}
              onChange={onKeepReceiptChange}
            />
          )}
        </>
      ) : (
        <pre>{source.text}</pre>
      )}
    </>
  );
}

function formatPackageContents(candidate: ProductCandidate): string {
  const quantity = candidate.pack_base_quantity?.trim().replace(",", ".");
  if (!quantity || !candidate.base_unit || /^0+(?:\.0+)?$/.test(quantity)) return "Size not recorded";
  const [whole, fraction] = quantity.split(".");
  const amount = fraction
    ? `${whole},${fraction.replace(/0+$/, "")}`.replace(/,$/, "")
    : whole;
  return `${amount} ${candidate.base_unit}`;
}

function ProductCandidateRow({
  candidate,
  resultStatus,
  suggestedProductId,
  onUseProduct,
}: {
  candidate: ProductCandidate;
  resultStatus: ProductSuggestion["status"];
  suggestedProductId: string | null | undefined;
  onUseProduct: (productId: string) => void;
}) {
  const isSuggested = resultStatus === "suggested"
    && (candidate.product_id === suggestedProductId || suggestedProductId == null);
  const needsReview = resultStatus === "review_required" || candidate.status === "check_details";

  return (
    <li className="review-product-candidate">
      <div className="review-product-candidate-facts">
        <span className={`review-product-badge${needsReview ? " needs-review" : ""}`}>
          {isSuggested ? "Suggested product" : needsReview ? "Check details" : "Product option"}
        </span>
        <div className="review-product-name-row"><strong>{candidate.name_de || candidate.name}</strong>
          {candidate.match_origin && candidate.match_origin!=="catalogue" && <InformationTip
            label="Why this product was suggested"
            text={candidate.match_origin==="exact_memory"
              ? "You previously chose this product for the same item description. Check that it still fits."
              : "You previously chose this product for a similar item. Check that it fits this purchase."} />}
        </div>
        {candidate.brand && <span>{candidate.brand}</span>}
        <span>{formatPackageContents(candidate)}</span>
        {candidate.source === "synthetic" && <small>Demo product</small>}
        {candidate.checks.length > 0 && (
          <ul className="review-product-checks" aria-label="Product checks">
            {candidate.checks.map((check, index) => <li key={`${check}-${index}`}>{check}</li>)}
          </ul>
        )}
      </div>
      <button className="button button-primary review-use-product" type="button" onClick={() => onUseProduct(candidate.product_id)}>
        Use this match
      </button>
    </li>
  );
}

function ProductSuggestionResults({
  itemId,
  result,
  onUseProduct,
  onKeepUnmatched,
}: {
  itemId: string;
  result: ProductSuggestion;
  onUseProduct: (productId: string) => void;
  onKeepUnmatched: () => void;
}) {
  const [showOtherOptions, setShowOtherOptions] = useState(false);
  const candidates = result.candidates?.slice(0, 3) ?? [];
  const primaryCandidate = result.suggested_product_id
    ? candidates.find(({ product_id }) => product_id === result.suggested_product_id) ?? candidates[0]
    : candidates[0];
  const otherCandidates = candidates.filter((candidate) => candidate !== primaryCandidate);
  const memoryLabel = result.memory_status === "conflict"
    ? "Previous choices differ — please review"
    : null;

  return (
    <div className="review-product-results" aria-live="polite">
      {memoryLabel && <p className="review-product-memory-label">{memoryLabel}</p>}
      {result.status === "no_match" ? (
        <>
          <p className="review-product-message">{result.message}</p>
          <button className="button button-outline review-keep-unmatched" type="button" onClick={onKeepUnmatched}>
            Skip product matching
          </button>
        </>
      ) : (
        <>
          {primaryCandidate && (
            <ul className="review-product-candidate-list">
              <ProductCandidateRow
                candidate={primaryCandidate}
                resultStatus={result.status}
                suggestedProductId={result.suggested_product_id}
                onUseProduct={onUseProduct}
              />
            </ul>
          )}
          {otherCandidates.length > 0 && (
            <>
              <button
                className="review-product-options-toggle"
                type="button"
                aria-expanded={showOtherOptions}
                aria-controls={`${itemId}-other-product-options`}
                onClick={() => setShowOtherOptions((current) => !current)}
              >
                {showOtherOptions ? "Hide other options" : `Other options (${otherCandidates.length})`}
              </button>
              {showOtherOptions && (
                <ul className="review-product-candidate-list review-product-other-options" id={`${itemId}-other-product-options`}>
                  {otherCandidates.map((candidate) => (
                    <ProductCandidateRow
                      key={candidate.product_id}
                      candidate={candidate}
                      resultStatus={result.status}
                      suggestedProductId={result.suggested_product_id}
                      onUseProduct={onUseProduct}
                    />
                  ))}
                </ul>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}

export function PurchaseEditor({
  form,
  onChange,
  session,
  householdId,
  source,
  savedReceiptPanel,
  idPrefix,
  errors,
  showErrors,
  disabled = false,
  totalLabel = "Calculated total",
  totalCents,
  totalAwaiting = false,
  keepReceipt,
  onKeepReceiptChange,
  receiptOptionDisabled = false,
  children,
}: {
  form: PurchaseForm;
  onChange: (form: PurchaseForm) => void;
  session: Session;
  householdId: string;
  source: SourceContext | undefined;
  savedReceiptPanel?: ReactNode;
  idPrefix: string;
  errors: FieldIssue[];
  showErrors: boolean;
  disabled?: boolean;
  totalLabel?: string;
  totalCents: number | null;
  totalAwaiting?: boolean;
  keepReceipt?: boolean;
  onKeepReceiptChange?: (keep: boolean) => void;
  receiptOptionDisabled?: boolean;
  children?: ReactNode;
}) {
  const [sourceOpen, setSourceOpen] = useState(false);
  const [sourceZoom, setSourceZoom] = useState(1);
  const [productSuggestions, setProductSuggestions] = useState<Record<string, ProductSuggestionState>>({});
  const [openProductFinders, setOpenProductFinders] = useState<Set<string>>(() => new Set());
  const sourceDialog = useRef<HTMLDialogElement>(null);
  const sourceTrigger = useRef<HTMLButtonElement>(null);
  const suggestionControllers = useRef(new Map<string, AbortController>());
  const currentForm = useRef(form);
  const currentOwner = useRef({ userId: session.user.id, householdId });
  const ownerIdentity = JSON.stringify([session.user.id, householdId]);
  const previousOwnerIdentity = useRef(ownerIdentity);
  currentForm.current = form;
  currentOwner.current = { userId: session.user.id, householdId };
  const imageSource = source?.type === "image";
  const voiceSource = source?.type === "voice";
  const sourceTitle = imageSource ? "Reviewed receipt" : voiceSource ? "Your voice entry" : "Your written entry";
  const sourceDescription = imageSource
    ? "This is the receipt you checked. It cannot be edited here."
    : voiceSource
      ? "These are the words from your recording that you checked."
      : "This is the written entry you checked.";

  useEffect(() => {
    if (sourceOpen && sourceDialog.current && !sourceDialog.current.open) sourceDialog.current.showModal();
  }, [sourceOpen]);

  useEffect(() => {
    const itemIds = new Set(form.items.map(({ localId }) => localId));
    for (const [localId, controller] of suggestionControllers.current) {
      if (!itemIds.has(localId)) {
        controller.abort();
        suggestionControllers.current.delete(localId);
      }
    }
    setProductSuggestions((current) => Object.fromEntries(
      Object.entries(current).filter(([localId]) => itemIds.has(localId)),
    ));
    setOpenProductFinders((current) => new Set([...current].filter((localId) => itemIds.has(localId))));
  }, [form.items]);

  useEffect(() => () => {
    suggestionControllers.current.forEach((controller) => controller.abort());
    suggestionControllers.current.clear();
  }, []);

  useEffect(() => {
    if (previousOwnerIdentity.current === ownerIdentity) return;
    previousOwnerIdentity.current = ownerIdentity;
    suggestionControllers.current.forEach((controller) => controller.abort());
    suggestionControllers.current.clear();
    setProductSuggestions({});
    setOpenProductFinders(new Set());
  }, [ownerIdentity]);

  function closeSourceViewer() {
    sourceDialog.current?.close();
    setSourceOpen(false);
    requestAnimationFrame(() => sourceTrigger.current?.focus());
  }

  function update<K extends keyof PurchaseForm>(key: K, value: PurchaseForm[K]) {
    onChange({ ...form, [key]: value });
  }

  function clearAllProductSuggestions() {
    suggestionControllers.current.forEach((controller) => controller.abort());
    suggestionControllers.current.clear();
    setProductSuggestions({});
    setOpenProductFinders(new Set());
  }

  function updateItem(index: number, patch: Partial<PurchaseForm["items"][number]>) {
    const item = form.items[index];
    if (!item) return;
    const matchingInputChanged =
      (patch.description !== undefined && patch.description !== item.description) ||
      (patch.category !== undefined && patch.category !== item.category) ||
      (patch.unit !== undefined && patch.unit !== item.unit);
    if (matchingInputChanged) clearProductSuggestion(item.localId, true);
    update("items", form.items.map((currentItem, itemIndex) => itemIndex === index
      ? { ...currentItem, ...patch, ...(matchingInputChanged ? { productId: null } : {}) }
      : currentItem));
  }

  function clearProductSuggestion(localId: string, closeFinder = false) {
    suggestionControllers.current.get(localId)?.abort();
    suggestionControllers.current.delete(localId);
    setProductSuggestions((current) => {
      if (!(localId in current)) return current;
      const next = { ...current };
      delete next[localId];
      return next;
    });
    if (closeFinder) {
      setOpenProductFinders((current) => {
        if (!current.has(localId)) return current;
        const next = new Set(current);
        next.delete(localId);
        return next;
      });
    }
  }

  function suggestionSignature(item: PurchaseForm["items"][number]): string {
    return JSON.stringify([
      item.description.trim(),
      item.category,
      item.unit,
      form.merchant.trim(),
    ]);
  }

  async function findProduct(item: PurchaseForm["items"][number]) {
    if (suggestionControllers.current.has(item.localId)) return;
    const originalText = item.description.trim();
    if (!originalText || !item.unit) return;

    const signature = suggestionSignature(item);
    const requestOwner = { userId: session.user.id, householdId };
    const controller = new AbortController();
    suggestionControllers.current.set(item.localId, controller);
    setOpenProductFinders((current) => new Set(current).add(item.localId));
    setProductSuggestions((current) => ({
      ...current,
      [item.localId]: { status: "loading", signature },
    }));

    const merchant = form.merchant.trim();
    const body: ProductSuggestionInput = {
      original_text: originalText,
      merchant: merchant || null,
      category: item.category,
      quantity_unit: item.unit,
    };
    const isCurrentRequest = () => {
      const liveItem = currentForm.current.items.find(({ localId }) => localId === item.localId);
      return (
        !controller.signal.aborted &&
        currentOwner.current.userId === requestOwner.userId &&
        currentOwner.current.householdId === requestOwner.householdId &&
        liveItem !== undefined &&
        suggestionSignature(liveItem) === signature
      );
    };

    try {
      const result = await apiRequest<ProductSuggestion>(
        `/households/${encodeURIComponent(householdId)}/product-suggestions`,
        session.access_token,
        { method: "POST", body, signal: controller.signal },
      );
      if (!isCurrentRequest()) return;
      setProductSuggestions((current) => ({
        ...current,
        [item.localId]: { status: "result", signature, result },
      }));
    } catch (requestError) {
      if (!isCurrentRequest()) return;
      const message = requestError instanceof ApiError && requestError.status === 422
        ? "Please remove personal details and check the item wording, category, and unit."
        : requestError instanceof ApiError && (requestError.status === 401 || requestError.status === 403)
          ? getErrorText(requestError)
          : "Suggestions unavailable. You can continue without a product link.";
      setProductSuggestions((current) => ({
        ...current,
        [item.localId]: { status: "error", signature, message },
      }));
    } finally {
      if (suggestionControllers.current.get(item.localId) === controller) {
        suggestionControllers.current.delete(item.localId);
      }
    }
  }

  function useProduct(itemIndex: number, localId: string, productId: string) {
    updateItem(itemIndex, { productId });
    clearProductSuggestion(localId, true);
  }

  function toggleProductFinder(localId: string) {
    setOpenProductFinders((current) => {
      const next = new Set(current);
      if (next.has(localId)) next.delete(localId);
      else next.add(localId);
      return next;
    });
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
          {imageSource ? "View receipt" : voiceSource ? "View voice transcript" : "View source text"}
        </button>
      )}
      <div className={`purchase-editor-layout${source || savedReceiptPanel ? "" : " source-missing"}`}>
        {!source && savedReceiptPanel && (
          <aside className="review-source-pane saved-receipt-source" aria-labelledby={`${idPrefix}-saved-receipt-title`}>
            <h2 id={`${idPrefix}-saved-receipt-title`}>Saved reviewed receipt</h2>
            <div className="review-image-wrap">{savedReceiptPanel}</div>
          </aside>
        )}
        {source && (
          <aside className="review-source-pane" aria-labelledby={`${idPrefix}-source-title`}>
            <h2 id={`${idPrefix}-source-title`}>{sourceTitle}</h2>
            <p>{sourceDescription}</p>
            {imageSource ? (
              <div className="review-image-wrap">
                <div className="image-viewer-controls" aria-label="Receipt zoom controls">
                  <button type="button" className="button button-outline" onClick={() => setSourceZoom((value) => Math.max(0.5, value - 0.25))} disabled={sourceZoom <= 0.5}>Zoom out</button>
                  <button type="button" className="button button-outline" onClick={() => setSourceZoom(() => 1)}>Fit width</button>
                  <button type="button" className="button button-outline" onClick={() => setSourceZoom((value) => Math.min(3, value + 0.25))} disabled={sourceZoom >= 3}>Zoom in</button>
                  <span>{Math.round(sourceZoom * 100)}%</span>
                </div>
                <div className="review-image-viewport" tabIndex={0} role="region" aria-label="Scrollable reviewed receipt image. Use zoom controls and scroll to read.">
                  <div className="review-image-stage" style={{ width: `${sourceZoom * 100}%` }}>
                    <img src={source.dataUrl} alt="Reviewed receipt" />
                  </div>
                </div>
                {keepReceipt !== undefined && onKeepReceiptChange && (
                  <ReceiptKeepControl
                    checked={keepReceipt}
                    disabled={receiptOptionDisabled}
                    onChange={onKeepReceiptChange}
                  />
                )}
              </div>
            ) : (
              <pre>{source.text}</pre>
            )}
          </aside>
        )}

        <section className="purchase-editor-fields">
          <fieldset disabled={disabled}>
            <legend>Purchase details</legend>
            <p className="review-product-help">Check the items, quantities and prices. Product matching is optional and helps compare offers; you can save without it.</p>
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
                  onChange={(event) => {
                    const nextMerchant = event.target.value;
                    if (nextMerchant !== form.merchant) clearAllProductSuggestions();
                    update("merchant", nextMerchant);
                  }}
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
                      localId: newPurchaseItemId(),
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
                const suggestionState = productSuggestions[item.localId];
                const activeSuggestionState = suggestionState?.signature === suggestionSignature(item)
                  ? suggestionState
                  : undefined;
                const isFindingOpen = openProductFinders.has(item.localId);
                const isFinding = activeSuggestionState?.status === "loading";
                const canFindProduct = Boolean(item.description.trim() && item.unit);
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
                  <fieldset className="review-item" key={item.localId}>
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
                    <div className="review-product-suggestion">
                      {item.productId && !isFindingOpen ? (
                        <div className="review-product-linked">
                          <span role="status">Product matched</span>
                          {!disabled && (
                            <>
                              <button type="button" className="review-product-link-action" onClick={() => toggleProductFinder(item.localId)}>
                                Change
                              </button>
                              <button
                                type="button"
                                className="review-product-link-action"
                                onClick={() => {
                                  updateItem(index, { productId: null });
                                  clearProductSuggestion(item.localId, true);
                                }}
                              >
                                Remove
                              </button>
                            </>
                          )}
                        </div>
                      ) : (
                        <button
                          type="button"
                          className="button button-outline review-find-product"
                          onClick={() => void findProduct(item)}
                          disabled={disabled || isFinding || !canFindProduct}
                          aria-describedby={`${idPrefix}-product-help-${item.localId}`}
                          aria-live="polite"
                        >
                          {isFinding
                            ? "Checking matches…"
                            : activeSuggestionState?.status === "error"
                              ? "Retry"
                              : "Match product"}
                        </button>
                      )}
                      {(!item.productId || isFindingOpen) && (
                        <>
                          <p className="review-product-help" id={`${idPrefix}-product-help-${item.localId}`}>
                            Optional—helps compare offers. <InformationTip label="How product matching works" text="Your item description has already been recorded. Matching uses AI to check which product it refers to. It does not change your purchase price or quantity. You can skip it. See Privacy notes for information sent to service providers." />
                          </p>
                          {isFinding && <AIWorking label="Checking product matches"/>}
                          {!canFindProduct && <p className="review-product-help">Enter the item name and choose a unit to check product matches.</p>}
                          {activeSuggestionState?.status === "error" && (
                            <div className="review-product-message" role="alert">
                              <p>{activeSuggestionState.message}</p>
                            </div>
                          )}
                          {activeSuggestionState?.status === "result" && (
                            <ProductSuggestionResults
                              itemId={item.localId}
                              result={activeSuggestionState.result}
                              onUseProduct={(productId) => useProduct(index, item.localId, productId)}
                              onKeepUnmatched={() => {
                                updateItem(index, { productId: null });
                                clearProductSuggestion(item.localId, true);
                              }}
                            />
                          )}
                        </>
                      )}
                    </div>
                    {!disabled && form.items.length > 1 && (
                      <button
                        className="remove-item"
                        type="button"
                        onClick={() => {
                          clearProductSuggestion(item.localId, true);
                          update("items", form.items.filter((_, itemIndex) => itemIndex !== index));
                        }}
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
            <h2 id={`${idPrefix}-source-dialog-title`}>{sourceTitle}</h2>
            <button className="button button-outline" type="button" onClick={closeSourceViewer}>Close</button>
          </div>
          <ReadOnlySource
            source={source}
            zoom={sourceZoom}
            setZoom={setSourceZoom}
            keepReceipt={keepReceipt}
            onKeepReceiptChange={onKeepReceiptChange}
            receiptOptionDisabled={receiptOptionDisabled}
          />
        </dialog>
      )}
    </div>
  );
}
