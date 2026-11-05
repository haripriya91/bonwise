import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import type { Session } from "@supabase/supabase-js";
import { apiRequest, getErrorText } from "../api/client";
import type { components } from "../api/types";
import { getDraftScopeKey, getPendingDraftSubmission, prepareDraftSubmission } from "./workflow";
import type { SourceContext } from "./workflow";
import { ImageCapture } from "./ImageCapture";
import { PurchaseEditor } from "./PurchaseEditor";
import {
  formFromExtraction,
  validatePurchaseForm,
} from "./purchaseForm";
import type { PurchaseForm } from "./purchaseForm";

type Extraction = components["schemas"]["ExtractionOut"] | components["schemas"]["ImageExtractionOut"];
type ImageExtraction = components["schemas"]["ImageExtractionOut"];
type ImageExtractionRequest = components["schemas"]["ImageExtractionRequest"];
type PrivacyPreview = components["schemas"]["PrivacyPreview"];
type DraftOut = components["schemas"]["DraftOut"];
type DraftCreate = components["schemas"]["DraftCreate"];
type CaptureStep = "choices" | "image" | "text" | "privacy" | "details";

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
  onDraftCreated: (draft: DraftOut, source: SourceContext) => void;
}) {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [step, setStep] = useState<CaptureStep>("choices");
  const [sourceText, setSourceText] = useState("");
  const [preview, setPreview] = useState<PrivacyPreview | null>(null);
  const [reviewedText, setReviewedText] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [extraction, setExtraction] = useState<Extraction | null>(null);
  const [source, setSource] = useState<SourceContext | undefined>();
  const [form, setForm] = useState<PurchaseForm | null>(null);
  const [imageHasWork, setImageHasWork] = useState(false);
  const [busy, setBusy] = useState<"privacy" | "extraction" | "draft" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showErrors, setShowErrors] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const launchButton = useRef<HTMLButtonElement>(null);
  const currentScopeKey = useRef(getDraftScopeKey(session.user.id, householdId));
  const scopeKey = getDraftScopeKey(session.user.id, householdId);
  const pendingSubmission = getPendingDraftSubmission(scopeKey);
  const result = useMemo(() => form ? validatePurchaseForm(form, "capture") : null, [form]);

  function resetCapture() {
    controller.current?.abort();
    controller.current = null;
    setStep("choices");
    setSourceText("");
    setPreview(null);
    setReviewedText("");
    setAcknowledged(false);
    setExtraction(null);
    setSource(undefined);
    setForm(null);
    setImageHasWork(false);
    setBusy(null);
    setError(null);
    setShowErrors(false);
  }

  useEffect(() => () => controller.current?.abort(), []);

  useEffect(() => {
    if (currentScopeKey.current === scopeKey) return;
    currentScopeKey.current = scopeKey;
    resetCapture();
    setDialogOpen(false);
    dialog.current?.close();
  }, [scopeKey]);

  useEffect(() => {
    if (dialogOpen && dialog.current && !dialog.current.open) dialog.current.showModal();
  }, [dialogOpen]);

  const reportImageWork = useCallback((hasWork: boolean) => setImageHasWork(hasWork), []);

  async function imageRequestData(dataUrl: string): Promise<string> {
    const match = /^data:(image\/(?:jpeg|png));base64,([A-Za-z0-9+/]+={0,2})$/.exec(dataUrl);
    if (!match) throw new Error("The reviewed image is not a valid JPEG or PNG.");
    let binary: string;
    try {
      binary = atob(match[2]);
    } catch {
      throw new Error("The reviewed image could not be decoded.");
    }
    if (binary.length === 0 || binary.length > 2_000_000) {
      throw new Error("The reviewed image exceeds the 2 MB upload limit.");
    }
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    let bitmap: ImageBitmap;
    try {
      bitmap = await createImageBitmap(new Blob([bytes], { type: match[1] }));
    } catch {
      throw new Error("The reviewed image could not be decoded.");
    }
    try {
      if (!bitmap.width || !bitmap.height || bitmap.width * bitmap.height > 16_000_000) {
        throw new Error("The reviewed image exceeds the 16 megapixel limit.");
      }
    } finally {
      bitmap.close();
    }
    return match[2];
  }

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
    const unreviewedText = sourceText;
    const request = beginRequest("privacy");
    try {
      const result = await apiRequest<PrivacyPreview>(
        `/households/${encodeURIComponent(householdId)}/extraction/privacy-preview`,
        session.access_token,
        { method: "POST", body: { text: unreviewedText }, signal: request.signal },
      );
      if (request.signal.aborted) return;
      setPreview(result);
      setReviewedText(result.reviewed_text);
      setSourceText("");
      setAcknowledged(false);
      setError(null);
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
      if (request.signal.aborted) return;
      setExtraction(result);
      setForm(formFromExtraction(result));
      setSource({
        type: "text",
        text: exactSubmittedText,
        receiptLineTotalsCents: result.extracted.items.map((item) => item.line_total_cents),
      });
      setShowErrors(true);
      setStep("details");
    } catch (requestError) {
      if (!isAbort(requestError)) setError(getErrorText(requestError));
    } finally {
      if (!request.signal.aborted) setBusy(null);
    }
  }

  async function requestImageExtraction(dataUrl: string) {
    const request = beginRequest("extraction");
    try {
      const image_base64 = await imageRequestData(dataUrl);
      if (request.signal.aborted) return;
      const body: ImageExtractionRequest = { image_base64, privacy_reviewed: true };
      const result = await apiRequest<ImageExtraction>(
        `/households/${encodeURIComponent(householdId)}/extraction/image`,
        session.access_token,
        { method: "POST", body, signal: request.signal },
      );
      if (request.signal.aborted) return;
      setExtraction(result);
      setForm(formFromExtraction(result));
      setSource({
        type: "image",
        dataUrl: result.source_image_data_url,
        receiptLineTotalsCents: result.extracted.items.map((item) => item.line_total_cents),
      });
      setShowErrors(true);
      setStep("details");
    } catch (requestError) {
      if (!isAbort(requestError)) setError(getErrorText(requestError));
    } finally {
      if (!request.signal.aborted) setBusy(null);
    }
  }

  async function submitDraft(body: DraftCreate) {
    const request = beginRequest("draft");
    try {
      const draft = await apiRequest<DraftOut>(
        `/households/${encodeURIComponent(householdId)}/purchase-drafts`,
        session.access_token,
        { method: "POST", body, signal: request.signal },
      );
      if (request.signal.aborted) return;
      if (!source || !form) {
        setError("The reviewed source is no longer available. Return to capture and review the source again.");
        return;
      }
      onDraftCreated(draft, {
        ...source,
        receiptLineTotalsCents: form.items.map((item) => item.receiptAmountCents),
      });
    } catch (requestError) {
      if (!isAbort(requestError)) setError(getErrorText(requestError));
    } finally {
      if (!request.signal.aborted) setBusy(null);
    }
  }

  async function createDraft(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setShowErrors(true);
    if (!result?.content || busy !== null) return;
    const payload = {
      source_type: source?.type ?? "text",
      content: result.content,
    } satisfies Omit<DraftCreate, "client_request_id">;
    await submitDraft(prepareDraftSubmission(scopeKey, payload));
  }

  async function retryDraft() {
    const pending = getPendingDraftSubmission(scopeKey);
    if (pending) await submitDraft(pending);
  }

  function openDialog() {
    setError(null);
    setDialogOpen(true);
  }

  function closeDialog() {
    const hasWork = Boolean(
      sourceText.trim() || preview || reviewedText.trim() || extraction || form || imageHasWork,
    );
    if (hasWork && !window.confirm("Close capture and discard this unsaved purchase?")) return;
    resetCapture();
    dialog.current?.close();
    setDialogOpen(false);
    requestAnimationFrame(() => launchButton.current?.focus());
  }

  function goBackToChoices() {
    setError(null);
    if (step === "privacy" && reviewedText) {
      setSourceText(reviewedText);
    }
    setStep("choices");
  }

  function backFromDetails() {
    setError(null);
    if (source?.type === "text") {
      setStep("privacy");
      return;
    }
    setStep("choices");
  }

  return (
    <>
      <section className="capture-launch entry-surface" aria-labelledby="capture-title">
        <p className="capture-eyebrow">Purchase capture</p>
        <h1 id="capture-title">Start with your purchases.</h1>
        <h2>Add a purchase</h2>
        <p className="capture-intro">Scan a receipt or describe what you bought. Review the details before anything is recorded.</p>
        <button ref={launchButton} className="button button-lime capture-receipt-action" type="button" onClick={openDialog}>
          Add a purchase
        </button>
      </section>

      {dialogOpen && (
        <dialog
          ref={dialog}
          className="capture-dialog"
          aria-labelledby="capture-dialog-title"
          onCancel={(event) => {
            event.preventDefault();
            closeDialog();
          }}
        >
          <div className="capture-dialog-header">
            <div>
              <p className="capture-eyebrow">Purchase capture</p>
              <h2 id="capture-dialog-title">
                {step === "choices" && "Choose how to add a purchase"}
                {step === "image" && "Receipt"}
                {step === "text" && "Text"}
                {step === "privacy" && "Review the text"}
                {step === "details" && "Correct purchase details"}
              </h2>
            </div>
            <button className="button button-outline" type="button" onClick={closeDialog} disabled={busy !== null}>
              Close
            </button>
          </div>

          {step === "choices" && (
            <section className="capture-mode-choices" aria-label="Capture choices">
              <button className="capture-mode-option" type="button" onClick={() => { setStep("image"); setError(null); }}>
                <strong>Receipt</strong>
                <span>Choose or take a JPEG/PNG photo, check it, then extract details.</span>
              </button>
              <button className="capture-mode-option" type="button" onClick={() => { setStep("text"); setError(null); }}>
                <strong>Text</strong>
                <span>Describe your purchase in German or English.</span>
              </button>
              {extraction && form && (
                <button className="button button-outline" type="button" onClick={() => setStep("details")}>
                  Continue correcting extracted details
                </button>
              )}
            </section>
          )}

          {dialogOpen && step !== "details" && (
            <div hidden={step !== "image"}>
              <ImageCapture
                extracting={busy === "extraction"}
                error={error}
                onExtract={(dataUrl) => void requestImageExtraction(dataUrl)}
                onBack={goBackToChoices}
                onWorkChange={reportImageWork}
              />
            </div>
          )}

          {step === "text" && (
            <form className="entry-surface capture-text-step" onSubmit={(event) => void requestPrivacyPreview(event)}>
              <h3>Purchase description</h3>
              <p className="capture-intro">Describe what you bought in German or English.</p>
              <label htmlFor="purchase-entry">Purchase description</label>
              <textarea
                id="purchase-entry"
                rows={5}
                value={sourceText}
                onChange={(event) => setSourceText(event.target.value)}
                placeholder="For example: 2 cartons of milk at 1,19 EUR each"
                required
                maxLength={16000}
                disabled={busy !== null}
              />
              {error && <p className="workflow-error" role="alert">{error}</p>}
              <div className="capture-dialog-actions">
                <button className="button button-outline" type="button" onClick={goBackToChoices} disabled={busy !== null}>
                  Back to Receipt or Text
                </button>
                <button className="button button-primary" type="submit" disabled={!sourceText.trim() || busy !== null}>
                  {busy === "privacy" ? "Checking text…" : "Review privacy"}
                </button>
              </div>
            </form>
          )}

          {step === "privacy" && preview && (
            <section className="workflow-surface privacy-step" aria-labelledby="privacy-title">
              <h3 id="privacy-title">Privacy review</h3>
              <p className="workflow-copy">
                Review the text that will be sent for extraction. This preview is not a guarantee that all personal information has been removed.
              </p>
              {Object.keys(preview.findings).length > 0 ? (
                <div className="privacy-findings" aria-label="Privacy preview findings">
                  <p>Items removed from the preview:</p>
                  <ul>
                    {Object.entries(preview.findings).map(([name, count]) => (
                      <li key={name}><PrivacyFindingLabel name={name} />: {count}</li>
                    ))}
                  </ul>
                </div>
              ) : (
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
              <div className="capture-dialog-actions">
                <button className="button button-outline" type="button" onClick={() => { setSourceText(reviewedText); setStep("text"); }} disabled={busy !== null}>Back to Text</button>
                <button className="button button-primary" type="button" onClick={() => void requestExtraction()} disabled={!acknowledged || !reviewedText.trim() || busy !== null}>
                  {busy === "extraction" ? "Extracting…" : "Extract purchase"}
                </button>
              </div>
              {busy === "extraction" && <p className="long-running-note" role="status">Extraction can take a little while. Please keep this dialog open.</p>}
            </section>
          )}

          {step === "details" && extraction && form && result && source && (
            <form className="capture-correction-form" onSubmit={(event) => void createDraft(event)}>
              <p className="workflow-copy">
                Correct the details before creating a pending draft. It will not be recorded as a purchase yet.
              </p>
              {extraction.missing_fields.length > 0 && (
                <p className="extraction-missing-summary" role="status">
                  The extraction left some required fields blank. Fill in the highlighted details to continue.
                </p>
              )}
              {extraction.warnings.length > 0 && (
                <section className="extraction-notes warnings" aria-labelledby="capture-warnings-title">
                  <h3 id="capture-warnings-title">Extraction notes</h3>
                  <ul>{extraction.warnings.map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}</ul>
                </section>
              )}
              <PurchaseEditor
                form={form}
                onChange={setForm}
                source={source}
                idPrefix="capture"
                errors={result.errors}
                showErrors={showErrors}
                disabled={busy !== null}
                totalLabel="Provisional calculated total"
                totalCents={result.calculatedTotalCents}
              >
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
                <div className="capture-dialog-actions">
                  <button className="button button-outline" type="button" onClick={backFromDetails} disabled={busy !== null}>
                    Back
                  </button>
                  <button className="button button-primary" type="submit" disabled={busy !== null}>
                    {busy === "draft" ? "Creating draft…" : "Review purchase"}
                  </button>
                </div>
              </PurchaseEditor>
            </form>
          )}
        </dialog>
      )}
    </>
  );
}
