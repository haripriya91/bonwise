import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import type { Session } from "@supabase/supabase-js";
import { apiRequest, getErrorText } from "../api/client";
import type { components } from "../api/types";
import { VoiceCapture } from "../purchase/VoiceCapture";

type HouseholdOption = { id: string };
type AnalysisInput = components["schemas"]["AnalysisInput"];
type AnalysisOut = components["schemas"]["AnalysisOut"];
type ConversationContext = components["schemas"]["ConversationContext"];
type EvidenceReference = components["schemas"]["EvidenceReference"];
type AudioFormat = components["schemas"]["TranscriptionRequest"]["audio_format"];
type TranscriptionOut = components["schemas"]["TranscriptionOut"];
type TranscriptionRequest = components["schemas"]["TranscriptionRequest"];

type AnswerEntry = {
  question: string;
  startDate: string;
  endDate: string;
  result: AnalysisOut;
};

type AnalysisChatState = {
  entries: AnswerEntry[];
  question: string;
  pending: boolean;
  error: string | null;
  updateQuestion: (value: string) => void;
  submitQuestion: (value: string, startDate: string, endDate: string) => Promise<void>;
  newConversation: () => void;
};

const AnalysisChatContext = createContext<AnalysisChatState | null>(null);

export function AnalysisChatProvider({
  session,
  householdId,
  children,
}: {
  session: Session;
  householdId: string;
  children: ReactNode;
}) {
  const [entries, setEntries] = useState<AnswerEntry[]>([]);
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conversationContext, setConversationContext] = useState<ConversationContext | null | undefined>(undefined);
  const [hasConversationTurn, setHasConversationTurn] = useState(false);
  const generation = useRef(0);
  const requestController = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const owner = useRef({ userId: session.user.id, householdId });
  owner.current = { userId: session.user.id, householdId };

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      generation.current += 1;
      requestController.current?.abort();
      requestController.current = null;
    };
  }, []);

  const updateQuestion = useCallback((value: string) => {
    setQuestion(value);
  }, []);

  const submitQuestion = useCallback(async (value: string, startDate: string, endDate: string) => {
    const submittedQuestion = value.trim();
    if (
      pending ||
      requestController.current ||
      submittedQuestion.length < 3 ||
      !householdId
    ) return;

    const requestOwner = {
      userId: session.user.id,
      householdId,
      generation: generation.current,
    };
    const isCurrentOwner = () =>
      mounted.current &&
      owner.current.userId === requestOwner.userId &&
      owner.current.householdId === requestOwner.householdId &&
      generation.current === requestOwner.generation;
    const body: AnalysisInput = {
      question: submittedQuestion,
      start_date: startDate,
      end_date: endDate,
      ...(hasConversationTurn ? { conversation_context: conversationContext } : {}),
    };
    const controller = new AbortController();
    requestController.current = controller;
    setPending(true);
    setError(null);
    try {
      const result = await apiRequest<AnalysisOut>(
        `/households/${encodeURIComponent(householdId)}/analysis/ask`,
        session.access_token,
        { method: "POST", body, signal: controller.signal },
      );
      if (controller.signal.aborted || !isCurrentOwner()) return;
      setEntries((current) => [
        ...current,
        { question: submittedQuestion, startDate, endDate, result },
      ]);
      setConversationContext(result.conversation_context);
      setHasConversationTurn(true);
      setQuestion((current) => current.trim() === submittedQuestion ? "" : current);
    } catch (requestError) {
      if (!controller.signal.aborted && isCurrentOwner()) setError(getErrorText(requestError));
    } finally {
      if (requestController.current === controller) {
        requestController.current = null;
        if (!controller.signal.aborted && isCurrentOwner()) setPending(false);
      }
    }
  }, [conversationContext, hasConversationTurn, householdId, pending, session]);

  const newConversation = useCallback(() => {
    generation.current += 1;
    requestController.current?.abort();
    requestController.current = null;
    setEntries([]);
    setQuestion("");
    setPending(false);
    setError(null);
    setConversationContext(undefined);
    setHasConversationTurn(false);
  }, []);

  return (
    <AnalysisChatContext.Provider
      value={{ entries, question, pending, error, updateQuestion, submitQuestion, newConversation }}
    >
      {children}
    </AnalysisChatContext.Provider>
  );
}

function useAnalysisChat(): AnalysisChatState {
  const chat = useContext(AnalysisChatContext);
  if (!chat) throw new Error("Ask Bonwise must be rendered inside its authenticated chat provider.");
  return chat;
}

const suggestedQuestions = [
  "How much did we spend on groceries?",
  "Which category had the highest spending?",
  "Show purchases for this period.",
];

const evidenceCategoryLabels: Record<
  NonNullable<EvidenceReference["category"]>,
  string
> = {
  groceries: "Groceries",
  clothing: "Clothing & accessories",
  household: "Household",
  personal_care: "Personal care",
  transport: "Transport",
  dining_out: "Dining out",
  other: "Other",
};

const completedOperationLabels: Record<string, string> = {
  get_spending_summary: "Spending totals",
  get_purchase_evidence: "Confirmed purchases",
};

function formatPeriodDate(value: string): string {
  const [year, month, day] = value.split("-").map(Number);
  return new Intl.DateTimeFormat("de-DE", { dateStyle: "medium" }).format(
    new Date(year, month - 1, day),
  );
}

function periodLabel(startDate: string, endDate: string): string {
  return `${formatPeriodDate(startDate)} – ${formatPeriodDate(endDate)}`;
}

function statusLabel(status: AnalysisOut["status"]): string {
  switch (status) {
    case "answered":
      return "Bonwise";
    case "clarification_required":
      return "More detail needed";
    case "unsupported":
      return "Not supported yet";
    case "no_data":
      return "No confirmed purchases found";
  }
}

function responsePeriod(result: AnalysisOut, fallbackStart: string, fallbackEnd: string): string {
  const dateRanges = result.summaries.length > 0
    ? result.summaries
    : result.evidence;
  const uniquePeriods = [...new Set(
    dateRanges.map(({ start_date, end_date }) => `${start_date}/${end_date}`),
  )];
  if (uniquePeriods.length === 0) return periodLabel(fallbackStart, fallbackEnd);
  return uniquePeriods
    .map((period) => {
      const [startDate, endDate] = period.split("/");
      return periodLabel(startDate, endDate);
    })
    .join(" · ");
}

function EvidenceList({ evidence }: { evidence: EvidenceReference[] }) {
  if (evidence.length === 0) return null;

  return (
    <details className="analysis-evidence">
      <summary>Supporting records</summary>
      <ul className="analysis-evidence-list">
        {evidence.map((record) => (
          <li
            className={`analysis-evidence-row ${record.kind}${record.item_description ? " item-evidence" : ""}`}
            key={record.evidence_id}
          >
            {record.kind === "summary" ? (
              <>
                <strong>Period summary</strong>
                <span>{periodLabel(record.start_date, record.end_date)}</span>
                <span>
                  {record.category
                    ? evidenceCategoryLabels[record.category]
                    : "All categories"}
                </span>
                <strong>{formatAmount(record.total_cents)}</strong>
                <p>
                  {record.purchase_count === null || record.purchase_count === undefined
                    ? "Purchase count unavailable."
                    : `${record.purchase_count} confirmed ${
                        record.purchase_count === 1 ? "purchase" : "purchases"
                      } in the period were checked.`}
                </p>
              </>
            ) : (
              <>
                {record.item_description && (
                  <strong className="analysis-evidence-item">{record.item_description}</strong>
                )}
                <span className="analysis-evidence-merchant">
                  {record.merchant || "Merchant not available"}
                </span>
                <span>
                  {record.purchased_on
                    ? formatPeriodDate(record.purchased_on)
                    : "Purchase date not available"}
                </span>
                <strong>{formatAmount(record.total_cents)}</strong>
              </>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}

function formatAmount(cents: number): string {
  return new Intl.NumberFormat("de-DE", {
    style: "currency",
    currency: "EUR",
  }).format(cents / 100);
}

export function AskBonwise({
  session,
  household,
  startDate,
  endDate,
}: {
  session: Session;
  household: HouseholdOption;
  startDate: string;
  endDate: string;
}) {
  const [expanded, setExpanded] = useState(true);
  const {
    entries,
    question,
    pending,
    error,
    updateQuestion: chatUpdateQuestion,
    submitQuestion,
    newConversation,
  } = useAnalysisChat();
  const [voiceActive, setVoiceActive] = useState(false);
  const [transcriptionBusy, setTranscriptionBusy] = useState(false);
  const [transcriptionError, setTranscriptionError] = useState<string | null>(null);
  const [recordingWork, setRecordingWork] = useState(false);
  const transcriptionController = useRef<AbortController | null>(null);

  useEffect(() => () => {
    transcriptionController.current?.abort();
  }, []);

  const updateRecordingWork = useCallback((hasWork: boolean) => {
    setRecordingWork(hasWork);
  }, []);

  async function transcribe(audioBase64: string, audioFormat: AudioFormat): Promise<string> {
    const controller = new AbortController();
    transcriptionController.current = controller;
    setTranscriptionBusy(true);
    setTranscriptionError(null);
    const body: TranscriptionRequest = {
      audio_base64: audioBase64,
      audio_format: audioFormat,
      language_hint: null,
      transcription_requested: true,
    };
    try {
      const response = await apiRequest<TranscriptionOut>(
        `/households/${encodeURIComponent(household.id)}/capture/transcribe`,
        session.access_token,
        { method: "POST", body, signal: controller.signal },
      );
      if (controller.signal.aborted) {
        throw new DOMException("Transcription was cancelled.", "AbortError");
      }
      return response.transcript;
    } catch (transcriptionRequestError) {
      if (!controller.signal.aborted) {
        const message = getErrorText(transcriptionRequestError);
        setTranscriptionError(message);
      }
      throw transcriptionRequestError;
    } finally {
      if (transcriptionController.current === controller) {
        transcriptionController.current = null;
        if (!controller.signal.aborted) setTranscriptionBusy(false);
      }
    }
  }

  function updateQuestion(value: string) {
    chatUpdateQuestion(value);
  }

  function insertTranscript(value: string) {
    const currentQuestion = question;
    const separator = currentQuestion && !/\s$/.test(currentQuestion) ? " " : "";
    const availableCharacters = Math.max(0, 2000 - currentQuestion.length - separator.length);
    updateQuestion(`${currentQuestion}${separator}${value.slice(0, availableCharacters)}`);
    setTranscriptionError(
      value.length > availableCharacters
        ? "The transcript was shortened to fit the question limit."
        : null,
    );
    setVoiceActive(false);
    setRecordingWork(false);
  }

  function cancelVoice() {
    transcriptionController.current?.abort();
    transcriptionController.current = null;
    setTranscriptionBusy(false);
    setTranscriptionError(null);
    setRecordingWork(false);
    setVoiceActive(false);
  }

  function toggleExpanded() {
    if (expanded && voiceActive) {
      transcriptionController.current?.abort();
      transcriptionController.current = null;
      setTranscriptionBusy(false);
      setVoiceActive(false);
      setRecordingWork(false);
    }
    setExpanded((current) => !current);
  }

  function submitTypedQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submitQuestion(question, startDate, endDate);
  }

  function startNewConversation() {
    cancelVoice();
    newConversation();
  }

  return (
    <section className="analysis-ask-panel" aria-labelledby="ask-bonwise-title">
      <div className="analysis-ask-header">
        <button
          className="analysis-panel-toggle"
          type="button"
          aria-expanded={expanded}
          aria-controls="ask-bonwise-content"
          onClick={toggleExpanded}
        >
          <span className="analysis-ask-heading">
            <span className="analysis-panel-title" id="ask-bonwise-title" role="heading" aria-level={2}>Ask Bonwise</span>
            <span className="analysis-panel-helper">Type or use your voice</span>
          </span>
          <svg className="analysis-panel-chevron" aria-hidden="true" viewBox="0 0 20 20">
            <path d="m5 7.5 5 5 5-5" />
          </svg>
        </button>
        <button className="analysis-new-conversation" type="button" onClick={startNewConversation}>
          New conversation
        </button>
      </div>

      <div id="ask-bonwise-content" className="analysis-ask-content" hidden={!expanded}>
        <div className="analysis-suggestions" aria-label="Suggested questions">
          {suggestedQuestions.map((suggestion) => (
            <button
              className="button button-outline"
              type="button"
              key={suggestion}
              onClick={() => updateQuestion(suggestion)}
            >
              {suggestion === suggestedQuestions[0]
                ? "Grocery spending"
                : suggestion === suggestedQuestions[1]
                  ? "Highest category"
                  : "Show purchases"}
            </button>
          ))}
        </div>

        {entries.length > 0 && (
          <ol className="analysis-answer-list" aria-label="Questions and answers">
            {entries.map((entry, index) => (
              <li className="analysis-answer-entry" key={`${entry.startDate}-${index}`}>
                <div className="analysis-asked-question">
                  <strong>{entry.question}</strong>
                  <span>{responsePeriod(entry.result, entry.startDate, entry.endDate)}</span>
                </div>
                <div className="analysis-answer">
                  <span className="analysis-answer-status">{statusLabel(entry.result.status)}</span>
                  <p>{entry.result.answer}</p>
                  {entry.result.tool_activity.length > 0 && (
                    <p className="analysis-tool-activity">
                      Checked: {[...new Set(entry.result.tool_activity
                        .map(({ name }) => completedOperationLabels[name])
                        .filter((label): label is string => Boolean(label)))]
                        .join(" · ")}
                    </p>
                  )}
                  {entry.result.evidence.length > 0 && (
                    <EvidenceList evidence={entry.result.evidence} />
                  )}
                  {entry.result.evidence_truncated && (
                    <p className="analysis-evidence-note">
                      Some supporting records were omitted from this response.
                    </p>
                  )}
                  {entry.result.calculation_note && (
                    <details className="analysis-calculation">
                      <summary>How this was calculated</summary>
                      <p>{entry.result.calculation_note}</p>
                    </details>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}

        {pending && <p className="status-message" role="status">Checking confirmed purchases…</p>}
        {error && <p className="analysis-ask-error" role="alert">{error}</p>}

        <form className="analysis-question-form" onSubmit={submitTypedQuestion}>
          {voiceActive ? (
            <span className="analysis-question-label">Your question</span>
          ) : (
            <label htmlFor="analysis-question">Your question</label>
          )}
          {!voiceActive && <p className="analysis-voice-note">Audio is sent for transcription when you stop.</p>}
          <div className="analysis-question-controls">
            {voiceActive ? (
              <VoiceCapture
                mode="question"
                active={voiceActive}
                transcript=""
                onTranscriptChange={insertTranscript}
                onTranscribe={transcribe}
                onWorkChange={updateRecordingWork}
                busy={transcriptionBusy ? "transcription" : null}
                error={transcriptionError}
                onCancel={cancelVoice}
              />
            ) : (
              <>
                <textarea
                  id="analysis-question"
                  rows={2}
                  value={question}
                  onChange={(event) => updateQuestion(event.target.value)}
                  placeholder="Ask about confirmed spending…"
                  maxLength={2000}
                  disabled={pending}
                />
                <button
                  className="button button-outline analysis-voice-toggle"
                  type="button"
                  onClick={() => {
                    setTranscriptionError(null);
                    setVoiceActive(true);
                  }}
                  disabled={pending}
                >
                  <svg
                    aria-hidden="true"
                    width="18"
                    height="18"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <rect x="9" y="2" width="6" height="12" rx="3" />
                    <path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8" />
                  </svg>
                  Use voice
                </button>
                <button
                  className="button button-primary analysis-send-button"
                  type="submit"
                  disabled={pending || recordingWork || question.trim().length < 3}
                >
                  Send
                </button>
              </>
            )}
          </div>
          {!voiceActive && transcriptionError && <p className="analysis-ask-error" role="alert">{transcriptionError}</p>}
        </form>

        <p className="analysis-selected-period">Selected period: {periodLabel(startDate, endDate)}</p>
      </div>
    </section>
  );
}
