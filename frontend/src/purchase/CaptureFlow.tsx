import {AIWorking} from "../components/AIWorking";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import type { Session } from "@supabase/supabase-js";
import { ApiError, apiRequest, getErrorText } from "../api/client";
import type { components } from "../api/types";
import { getDraftScopeKey, getPendingDraftSubmission, prepareDraftSubmission } from "./workflow";
import type { SourceContext } from "./workflow";
import { ImageCapture } from "./ImageCapture";
import { PurchaseEditor } from "./PurchaseEditor";
import { VoiceCapture } from "./VoiceCapture";
import {
  formFromExtraction,
  validatePurchaseForm,
} from "./purchaseForm";
import type { PurchaseForm } from "./purchaseForm";

type Extraction = components["schemas"]["ExtractionOut"] | components["schemas"]["ImageExtractionOut"];
type ImageExtraction = components["schemas"]["ImageExtractionOut"];
type ImageExtractionRequest = components["schemas"]["ImageExtractionRequest"];
type PrivacyPreview = components["schemas"]["PrivacyPreview"];
type TranscriptionOut = components["schemas"]["TranscriptionOut"];
type TranscriptionRequest = components["schemas"]["TranscriptionRequest"];
type DraftOut = components["schemas"]["DraftOut"];
type DraftCreate = components["schemas"]["DraftCreate"];
type CaptureMode = "image" | "voice" | "text";
type CaptureStep = CaptureMode | "privacy" | "details";

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
  onDraftCreated: (
    draft: DraftOut,
    source: SourceContext,
    receiptError?: string,
    requiresReview?: boolean,
    keepReviewedReceipt?: boolean,
  ) => void;
}) {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [step, setStep] = useState<CaptureStep>("image");
  const [sourceText, setSourceText] = useState("");
  const [preview, setPreview] = useState<PrivacyPreview | null>(null);
  const [reviewedText, setReviewedText] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [voiceTranscript, setVoiceTranscript] = useState("");
  const [voicePreview, setVoicePreview] = useState<PrivacyPreview | null>(null);
  const [voiceReviewedText, setVoiceReviewedText] = useState("");
  const [voiceAcknowledged, setVoiceAcknowledged] = useState(false);
  const [extraction, setExtraction] = useState<Extraction | null>(null);
  const [source, setSource] = useState<SourceContext | undefined>();
  const [form, setForm] = useState<PurchaseForm | null>(null);
  const [keepReceipt, setKeepReceipt] = useState(true);
  const [imageHasWork, setImageHasWork] = useState(false);
  const [voiceHasWork, setVoiceHasWork] = useState(false);
  const [busy, setBusy] = useState<"privacy" | "transcription" | "extraction" | "draft" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showErrors, setShowErrors] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const launchButton = useRef<HTMLElement | null>(null);
  const currentScopeKey = useRef(getDraftScopeKey(session.user.id, householdId));
  const scopeKey = getDraftScopeKey(session.user.id, householdId);
  const pendingSubmission = getPendingDraftSubmission(scopeKey);
  const result = useMemo(() => form ? validatePurchaseForm(form, "capture") : null, [form]);

  function resetCapture() {
    controller.current?.abort();
    controller.current = null;
    setStep("image");
    setSourceText("");
    setPreview(null);
    setReviewedText("");
    setAcknowledged(false);
    setVoiceTranscript("");
    setVoicePreview(null);
    setVoiceReviewedText("");
    setVoiceAcknowledged(false);
    setExtraction(null);
    setSource(undefined);
    setForm(null);
    setKeepReceipt(true);
    setImageHasWork(false);
    setVoiceHasWork(false);
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
  const reportVoiceWork = useCallback((hasWork: boolean) => setVoiceHasWork(hasWork), []);
  const activeMode: CaptureMode = step === "privacy" ? "text" : step === "details" ? "image" : step;

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

  function beginRequest(kind: "privacy" | "transcription" | "extraction" | "draft") {
    controller.current?.abort();
    const requestController = new AbortController();
    controller.current = requestController;
    setBusy(kind);
    setError(null);
    return requestController;
  }

  async function requestPrivacyPreview(
    unreviewedText: string,
    onSuccess: (result: PrivacyPreview) => void,
  ) {
    if (!unreviewedText.trim()) return;
    const request = beginRequest("privacy");
    try {
      const result = await apiRequest<PrivacyPreview>(
        `/households/${encodeURIComponent(householdId)}/extraction/privacy-preview`,
        session.access_token,
        { method: "POST", body: { text: unreviewedText }, signal: request.signal },
      );
      if (request.signal.aborted) return;
      onSuccess(result);
      setError(null);
    } catch (requestError) {
      if (!isAbort(requestError)) setError(getErrorText(requestError));
    } finally {
      if (!request.signal.aborted) setBusy(null);
    }
  }

  async function requestTextPrivacyPreview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await requestPrivacyPreview(sourceText, (result) => {
      setPreview(result);
      setReviewedText(result.reviewed_text);
      setSourceText("");
      setAcknowledged(false);
      setStep("privacy");
    });
  }

  async function requestVoicePrivacyPreview() {
    await requestPrivacyPreview(voiceTranscript, (result) => {
      setVoicePreview(result);
      setVoiceReviewedText(result.reviewed_text);
      setVoiceAcknowledged(false);
    });
  }

  async function extractReviewedText(exactSubmittedText: string, sourceType: "text" | "voice") {
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
        type: sourceType,
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

  async function requestTextExtraction() {
    if (!preview || !acknowledged || !reviewedText.trim()) return;
    await extractReviewedText(reviewedText, "text");
  }

  async function requestVoiceExtraction() {
    if (!voicePreview || !voiceAcknowledged || !voiceReviewedText.trim()) return;
    await extractReviewedText(voiceReviewedText, "voice");
  }

  async function requestTranscription(audioBase64: string, audioFormat: TranscriptionRequest["audio_format"]): Promise<string> {
    const body: TranscriptionRequest = {
      audio_base64: audioBase64,
      audio_format: audioFormat,
      language_hint: null,
      transcription_requested: true,
    };
    const request = beginRequest("transcription");
    try {
      const result = await apiRequest<TranscriptionOut>(
        `/households/${encodeURIComponent(householdId)}/capture/transcribe`,
        session.access_token,
        { method: "POST", body, signal: request.signal },
      );
      if (request.signal.aborted) throw new DOMException("The transcription was cancelled.", "AbortError");
      setVoicePreview(null);
      setVoiceReviewedText("");
      setVoiceAcknowledged(false);
      setError(null);
      return result.transcript;
    } catch (requestError) {
      if (!isAbort(requestError)) setError(getErrorText(requestError));
      throw requestError;
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
      const reviewedImageBase64 = await imageRequestData(result.source_image_data_url);
      if (request.signal.aborted) return;
      setExtraction(result);
      setForm(formFromExtraction(result));
      setSource({
        type: "image",
        dataUrl: result.source_image_data_url,
        imageBase64: reviewedImageBase64,
        sourceSha256: result.source_sha256,
        receiptLineTotalsCents: result.extracted.items.map((item) => item.line_total_cents),
      });
      setKeepReceipt(true);
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
      let draft = await apiRequest<DraftOut>(
        `/households/${encodeURIComponent(householdId)}/purchase-drafts`,
        session.access_token,
        { method: "POST", body, signal: request.signal },
      );
      if (request.signal.aborted) return;
      if (!source || !form) {
        setError("The reviewed source is no longer available. Return to capture and review the source again.");
        return;
      }
      let receiptError: string | undefined;
      let requiresReview = false;
      if (source.type === "image" && keepReceipt) {
        try {
          const attached = await apiRequest<DraftOut>(
            `/households/${encodeURIComponent(householdId)}/purchase-drafts/${encodeURIComponent(draft.id)}/receipt`,
            session.access_token,
            {
              method: "PUT",
              body: {
                revision: draft.revision,
                image_base64: source.imageBase64,
                source_sha256: source.sourceSha256,
                privacy_reviewed: true,
              },
              signal: request.signal,
            },
          );
          if (
            attached.id !== draft.id
            || attached.household_id !== householdId
            || !attached.receipt_available
          ) {
            throw new Error("The receipt response does not match this household and draft.");
          }
          draft = attached;
        } catch (requestError) {
          if (isAbort(requestError)) return;
          receiptError = `The draft was created, but the reviewed receipt was not saved: ${getErrorText(requestError)}`;
          const status = requestError instanceof ApiError ? requestError.status : undefined;
          const uncertain = status === undefined || status === 409 || status >= 500;
          if (uncertain) {
            requiresReview = true;
            try {
              const latest = await apiRequest<DraftOut>(
                `/households/${encodeURIComponent(householdId)}/purchase-drafts/${encodeURIComponent(draft.id)}`,
                session.access_token,
                { signal: request.signal },
              );
              if (request.signal.aborted) return;
              if (latest.id === draft.id && latest.household_id === householdId) {
                draft = latest;
                receiptError = "The receipt save could not be confirmed. The latest draft has been loaded; review it before trying again.";
              } else {
                receiptError = "The receipt save could not be confirmed, and the refreshed draft did not match this household. Return to Purchases and reload the draft before continuing.";
              }
            } catch (refreshError) {
              if (isAbort(refreshError)) return;
              receiptError = `The receipt save could not be confirmed, and the latest draft could not be loaded: ${getErrorText(refreshError)}`;
            }
          }
        }
      }
      onDraftCreated(draft, {
        ...source,
        receiptLineTotalsCents: form.items.map((item) => item.receiptAmountCents),
      }, receiptError, requiresReview, source.type === "image" && keepReceipt);
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

  function openDialog(mode: CaptureMode, trigger: HTMLElement) {
    setError(null);
    setStep(mode);
    launchButton.current = trigger;
    setDialogOpen(true);
  }

  function closeDialog() {
    const hasWork = Boolean(
      sourceText.trim() || preview || reviewedText.trim() || voiceTranscript.trim()
      || voicePreview || voiceReviewedText.trim() || extraction || form || imageHasWork || voiceHasWork,
    );
    if (hasWork && !window.confirm("Close capture and discard this unsaved purchase?")) return;
    resetCapture();
    dialog.current?.close();
    setDialogOpen(false);
    requestAnimationFrame(() => launchButton.current?.focus());
  }

  function selectMode(mode: CaptureMode) {
    controller.current?.abort();
    controller.current = null;
    setBusy(null);
    setError(null);
    setStep(mode === "text" && preview ? "privacy" : mode);
  }

  function backFromDetails() {
    setError(null);
    if (source?.type === "text") {
      setStep("privacy");
      return;
    }
    if (source?.type === "voice") {
      setStep("voice");
      return;
    }
    setStep("image");
  }

  return (
    <>
      <section className="capture-launch entry-surface" aria-labelledby="capture-title">
        <p className="capture-eyebrow">Your purchases are the starting point</p>
        <h1 id="capture-title">Add a purchase, your way.</h1>
        <p className="capture-intro">Check the details before anything enters your purchase history.</p>
        <button
          className="capture-receipt-action"
          type="button"
          onClick={(event) => openDialog("image", event.currentTarget)}
          aria-label="Scan or upload a receipt"
        >
          <span className="capture-action-copy">
            <strong>Scan or upload a receipt</strong>
            <span>German or English · review before saving</span>
          </span>
          <span className="capture-action-arrow" aria-hidden="true">↗</span>
        </button>
        <div className="capture-secondary-actions">
          <button type="button" onClick={(event) => openDialog("voice", event.currentTarget)}>
            <strong>Describe your purchase</strong>
            <span>Short voice entry</span>
          </button>
          <button type="button" onClick={(event) => openDialog("text", event.currentTarget)}>
            <strong>Enter purchase details</strong>
            <span>Type what you bought</span>
          </button>
        </div>
        <footer className="capture-panel-footer">Photo, voice and text → the same review</footer>
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
                {step === "image" && "Receipt"}
                {step === "text" && "Text"}
                {step === "voice" && "Voice"}
                {step === "privacy" && "Review text privacy"}
                {step === "details" && "Correct purchase details"}
              </h2>
            </div>
            <button className="button button-outline" type="button" onClick={closeDialog} disabled={busy === "draft"}>
              Close
            </button>
          </div>

          {step !== "details" && (
            <div className="capture-mode-tabs" role="tablist" aria-label="Purchase entry method" onKeyDown={(event) => {
              const modes: CaptureMode[] = ["image", "voice", "text"];
              const currentIndex = modes.indexOf(activeMode);
              const nextIndex = event.key === "ArrowRight"
                ? (currentIndex + 1) % modes.length
                : event.key === "ArrowLeft"
                  ? (currentIndex + modes.length - 1) % modes.length
                  : event.key === "Home"
                    ? 0
                    : event.key === "End"
                      ? modes.length - 1
                      : -1;
              if (nextIndex < 0) return;
              event.preventDefault();
              selectMode(modes[nextIndex]);
              requestAnimationFrame(() => document.getElementById(`capture-tab-${modes[nextIndex]}`)?.focus());
            }}>
              {([
                ["image", "Receipt"],
                ["voice", "Voice"],
                ["text", "Text"],
              ] as const).map(([mode, label]) => (
                <button
                  key={mode}
                  id={`capture-tab-${mode}`}
                  type="button"
                  role="tab"
                  aria-selected={activeMode === mode}
                  aria-controls={`capture-panel-${mode}`}
                  tabIndex={activeMode === mode ? 0 : -1}
                  onClick={() => selectMode(mode)}
                  disabled={busy === "draft"}
                >
                  {label}
                </button>
              ))}
            </div>
          )}

          {dialogOpen && step !== "details" && (
            <>
            <div id="capture-panel-image" role="tabpanel" aria-labelledby="capture-tab-image" hidden={activeMode !== "image"}>
              <ImageCapture
                extracting={busy === "extraction"}
                error={error}
                onExtract={(dataUrl) => void requestImageExtraction(dataUrl)}
                onBack={closeDialog}
                onWorkChange={reportImageWork}
              />
            </div>

            <div id="capture-panel-voice" role="tabpanel" aria-labelledby="capture-tab-voice" hidden={activeMode !== "voice"}>
              <VoiceCapture
              active={activeMode === "voice"}
              transcript={voiceTranscript}
              onTranscriptChange={(value) => {
                setVoiceTranscript(value);
                setVoicePreview(null);
                setVoiceReviewedText("");
                setVoiceAcknowledged(false);
              }}
              preview={voicePreview}
              reviewedText={voiceReviewedText}
              acknowledged={voiceAcknowledged}
              onReviewedTextChange={(value) => {
                setVoiceReviewedText(value);
                setVoiceAcknowledged(false);
              }}
              onAcknowledgedChange={setVoiceAcknowledged}
              onTranscribe={requestTranscription}
              onReviewPrivacy={() => void requestVoicePrivacyPreview()}
              onExtract={() => void requestVoiceExtraction()}
              onBack={closeDialog}
              onEnterText={(transcript) => {
                if (transcript.trim() && sourceText.trim() && sourceText !== transcript) {
                  if (!window.confirm("Replace the text purchase with what we heard? Your typed text remains available if you cancel.")) {
                    return;
                  }
                }
                if (transcript.trim()) setSourceText(transcript);
                setVoicePreview(null);
                setVoiceReviewedText("");
                setVoiceAcknowledged(false);
                setError(null);
                setStep("text");
              }}
              onWorkChange={reportVoiceWork}
              busy={busy === "transcription" || busy === "privacy" || busy === "extraction" ? busy : null}
              error={error}
            />
            </div>
            </>
          )}

          {dialogOpen && step !== "details" && (
            <div id="capture-panel-text" role="tabpanel" aria-labelledby="capture-tab-text" hidden={activeMode !== "text"}>
            {step === "text" && (
            <form className="capture-text-step" onSubmit={(event) => void requestTextPrivacyPreview(event)}>
              <h3>Purchase description</h3>
              <p className="capture-intro">Describe what you bought in German or English.</p>
              <label htmlFor="purchase-entry">Purchase description</label>
              <textarea
                id="purchase-entry"
                rows={5}
                value={sourceText}
                onChange={(event) => setSourceText(event.target.value)}
                placeholder="For example: Markt, 5 November — 2 packs of milk at 1,19 EUR and 1 loaf of bread at 2,49 EUR"
                required
                maxLength={16000}
                disabled={busy !== null}
              />
              {error && <p className="workflow-error" role="alert">{error}</p>}
              <div className="capture-dialog-actions">
                <button className="button button-outline" type="button" onClick={closeDialog} disabled={busy === "draft"}>
                  Back
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
                <span>I checked this text and want to prepare the purchase details.</span>
              </label>
              {error && <p className="workflow-error" role="alert">{error}</p>}
              <div className="capture-dialog-actions">
                <button className="button button-outline" type="button" onClick={() => { setSourceText(reviewedText); setStep("text"); }} disabled={busy !== null}>Back to Text</button>
                <button className="button button-primary" type="button" onClick={() => void requestTextExtraction()} disabled={!acknowledged || !reviewedText.trim() || busy !== null}>
                  {busy === "extraction" ? "Preparing details…" : "Get purchase details"}
                </button>
              </div>
              {busy === "extraction" && <AIWorking label="Preparing purchase details for review"/>}
            </section>
          )}
            </div>
          )}

          {step === "details" && extraction && form && result && source && (
            <form className="capture-correction-form" onSubmit={(event) => void createDraft(event)}>
              <p className="workflow-copy">
                Check and edit the details, then continue to confirmation. Spending is recorded only when you confirm the purchase.
              </p>
              {extraction.missing_fields.length > 0 && (
                <p className="extraction-missing-summary" role="status">
                  We could not read every detail. Fill in the highlighted fields to continue.
                </p>
              )}
              {extraction.warnings.length > 0 && (
                <section className="extraction-notes warnings" aria-labelledby="capture-warnings-title">
                  <h3 id="capture-warnings-title">Please check</h3>
                  <ul>{extraction.warnings.map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}</ul>
                </section>
              )}
              <PurchaseEditor
                form={form}
                onChange={setForm}
                session={session}
                householdId={householdId}
                source={source}
                idPrefix="capture"
                errors={result.errors}
                showErrors={showErrors}
                disabled={busy !== null}
                totalLabel="Provisional calculated total"
                totalCents={result.calculatedTotalCents}
                keepReceipt={source.type === "image" ? keepReceipt : undefined}
                onKeepReceiptChange={source.type === "image" ? setKeepReceipt : undefined}
                receiptOptionDisabled={busy !== null}
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
