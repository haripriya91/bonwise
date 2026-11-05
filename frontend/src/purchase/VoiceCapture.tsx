import { useEffect, useRef, useState } from "react";
import type { components } from "../api/types";

type AudioFormat = components["schemas"]["TranscriptionRequest"]["audio_format"];
type PrivacyPreview = components["schemas"]["PrivacyPreview"];
type RecordingType = { mimeType: string };

const maximumAudioBytes = 2_000_000;
const maximumRecordingSeconds = 60;
const privacyFindingLabels: Record<string, string> = {
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

const recordingTypes: RecordingType[] = [
  { mimeType: "audio/webm;codecs=opus" },
  { mimeType: "audio/webm" },
  { mimeType: "audio/mp4;codecs=mp4a.40.2" },
  { mimeType: "audio/mp4" },
  { mimeType: "audio/ogg;codecs=opus" },
  { mimeType: "audio/ogg" },
];

function supportedRecordingType(): RecordingType | undefined {
  if (typeof MediaRecorder === "undefined" || typeof MediaRecorder.isTypeSupported !== "function") return undefined;
  return recordingTypes.find(({ mimeType }) => MediaRecorder.isTypeSupported(mimeType));
}

function actualAudioFormat(mimeType: string): AudioFormat | undefined {
  const container = mimeType.split(";", 1)[0].trim().toLowerCase();
  if (container === "audio/webm") return "webm";
  if (container === "audio/mp4") return "mp4";
  if (container === "audio/ogg") return "ogg";
  return undefined;
}

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result !== "string") {
        reject(new Error("The recording could not be read."));
        return;
      }
      const base64 = reader.result.split(",", 2)[1];
      if (!base64) {
        reject(new Error("The recording could not be read."));
        return;
      }
      resolve(base64);
    };
    reader.onerror = () => reject(new Error("The recording could not be read."));
    reader.readAsDataURL(blob);
  });
}

function formatElapsed(seconds: number): string {
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

export function VoiceCapture({
  active,
  transcript,
  onTranscriptChange,
  preview,
  reviewedText,
  acknowledged,
  onReviewedTextChange,
  onAcknowledgedChange,
  onTranscribe,
  onReviewPrivacy,
  onExtract,
  onBack,
  onEnterText,
  onWorkChange,
  busy,
  error,
}: {
  active: boolean;
  transcript: string;
  onTranscriptChange: (value: string) => void;
  preview: PrivacyPreview | null;
  reviewedText: string;
  acknowledged: boolean;
  onReviewedTextChange: (value: string) => void;
  onAcknowledgedChange: (value: boolean) => void;
  onTranscribe: (audioBase64: string, format: AudioFormat) => Promise<string>;
  onReviewPrivacy: () => void;
  onExtract: () => void;
  onBack: () => void;
  onEnterText: (transcript: string) => void;
  onWorkChange: (hasWork: boolean) => void;
  busy: "transcription" | "privacy" | "extraction" | null;
  error: string | null;
}) {
  const [recording, setRecording] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  const [requestingPermission, setRequestingPermission] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [audio, setAudio] = useState<Blob | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [recordError, setRecordError] = useState<string | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const chunks = useRef<Blob[]>([]);
  const objectUrl = useRef<string | null>(null);
  const recordingStartedAt = useRef(0);
  const timer = useRef<number | null>(null);
  const autoStopTimer = useRef<number | null>(null);
  const mounted = useRef(true);
  const microphoneRequestId = useRef(0);
  const captureSequence = useRef(0);
  const transcribing = useRef(false);
  const recorderFailed = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      microphoneRequestId.current += 1;
      captureSequence.current += 1;
      if (timer.current !== null) window.clearInterval(timer.current);
      if (autoStopTimer.current !== null) window.clearTimeout(autoStopTimer.current);
      const activeRecorder = recorder.current;
      recorder.current = null;
      if (activeRecorder && activeRecorder.state !== "inactive") activeRecorder.stop();
      stream.current?.getTracks().forEach((track) => track.stop());
      stream.current = null;
      chunks.current = [];
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
      objectUrl.current = null;
    };
  }, []);

  useEffect(() => {
    onWorkChange(recording || finalizing || audio !== null);
  }, [recording, finalizing, requestingPermission, audio, onWorkChange]);

  function clearAudio() {
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
    objectUrl.current = null;
    setAudioUrl(null);
    setAudio(null);
  }

  function finishTracks() {
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = null;
  }

  function stopRecording() {
    const activeRecorder = recorder.current;
    if (!activeRecorder || activeRecorder.state === "inactive") return;
    setRecording(false);
    setFinalizing(true);
    if (timer.current !== null) window.clearInterval(timer.current);
    timer.current = null;
    if (autoStopTimer.current !== null) window.clearTimeout(autoStopTimer.current);
    autoStopTimer.current = null;
    activeRecorder.stop();
  }

  useEffect(() => {
    if (active) return;
    microphoneRequestId.current += 1;
    captureSequence.current += 1;
    setRequestingPermission(false);
    if (recording) stopRecording();
  }, [active, recording]);

  async function startRecording() {
    setRecordError(null);
    if (!navigator.mediaDevices?.getUserMedia) {
      setRecordError("Microphone recording is not available in this browser.");
      return;
    }
    const supportedType = supportedRecordingType();
    if (!supportedType) {
      setRecordError("This browser does not support a compatible audio recording format.");
      return;
    }
    if (transcript.trim() && !window.confirm("Start a new recording and replace what we heard?")) return;

    const requestId = ++microphoneRequestId.current;
    setRequestingPermission(true);
    try {
      const microphone = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!mounted.current || requestId !== microphoneRequestId.current) {
        microphone.getTracks().forEach((track) => track.stop());
        return;
      }
      stream.current = microphone;
      const mediaRecorder = new MediaRecorder(microphone, { mimeType: supportedType.mimeType });
      recorder.current = mediaRecorder;
      chunks.current = [];
      recorderFailed.current = false;
      clearAudio();
      setElapsedSeconds(0);
      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunks.current.push(event.data);
      };
      mediaRecorder.onerror = () => {
        recorderFailed.current = true;
        setRecordError("Recording stopped because of a microphone error. Please try again.");
        if (mediaRecorder.state !== "inactive") mediaRecorder.stop();
      };
      mediaRecorder.onstop = () => {
        finishTracks();
        recorder.current = null;
        if (timer.current !== null) window.clearInterval(timer.current);
        timer.current = null;
        if (autoStopTimer.current !== null) window.clearTimeout(autoStopTimer.current);
        autoStopTimer.current = null;
        const blob = new Blob(chunks.current, { type: mediaRecorder.mimeType || supportedType.mimeType });
        chunks.current = [];
        if (!mounted.current) return;
        setRecording(false);
        setFinalizing(false);
        if (recorderFailed.current) {
          recorderFailed.current = false;
          setRecordError("Recording stopped because of a microphone error. Please try again.");
          return;
        }
        const format = actualAudioFormat(blob.type);
        if (!format) {
          setRecordError("The browser produced an unsupported audio container. Try another browser or enter the purchase as text.");
          return;
        }
        if (!blob.size) {
          setRecordError("The recording is empty. Please record again.");
          return;
        }
        if (blob.size > maximumAudioBytes) {
          setRecordError("This recording is larger than 2 MB. Please record a shorter note.");
          return;
        }
        const url = URL.createObjectURL(blob);
        objectUrl.current = url;
        setAudio(blob);
        setAudioUrl(url);
      };
      mediaRecorder.start();
      if (transcript.trim()) onTranscriptChange("");
      recordingStartedAt.current = Date.now();
      setRecording(true);
      setFinalizing(false);
      timer.current = window.setInterval(() => {
        const elapsed = Math.floor((Date.now() - recordingStartedAt.current) / 1000);
        setElapsedSeconds(Math.min(maximumRecordingSeconds, elapsed));
      }, 250);
      autoStopTimer.current = window.setTimeout(() => {
        if (mediaRecorder.state !== "inactive") {
          setElapsedSeconds(maximumRecordingSeconds);
          setRecording(false);
          setFinalizing(true);
          mediaRecorder.stop();
        }
      }, maximumRecordingSeconds * 1000);
    } catch (failure) {
      recorderFailed.current = true;
      const activeRecorder = recorder.current;
      recorder.current = null;
      if (activeRecorder && activeRecorder.state !== "inactive") activeRecorder.stop();
      finishTracks();
      if (failure instanceof DOMException && failure.name === "NotAllowedError") {
        setRecordError("Microphone access was denied. Allow microphone access in your browser settings, or enter the purchase as text.");
      } else if (failure instanceof DOMException && (failure.name === "NotFoundError" || failure.name === "DevicesNotFoundError")) {
        setRecordError("No microphone was found. Connect a microphone, or enter the purchase as text.");
      } else if (failure instanceof DOMException && failure.name === "NotSupportedError") {
        setRecordError("This browser cannot record in a compatible audio format. Try another browser or enter the purchase as text.");
      } else {
        setRecordError("The microphone could not be started. Check browser permissions and try again.");
      }
    } finally {
      if (mounted.current && requestId === microphoneRequestId.current) setRequestingPermission(false);
    }
  }

  async function transcribe() {
    if (!audio || busy !== null || transcribing.current) return;
    const format = actualAudioFormat(audio.type);
    if (!format) {
      setRecordError("The recording format is not supported. Please record again.");
      return;
    }
    const sequence = ++captureSequence.current;
    transcribing.current = true;
    try {
      let base64: string;
      try {
        base64 = await blobToBase64(audio);
      } catch {
        if (mounted.current && sequence === captureSequence.current) {
          setRecordError("The recording could not be prepared for upload. It is still available to retry.");
        }
        return;
      }
      if (!mounted.current || sequence !== captureSequence.current) return;
      const result = await onTranscribe(base64, format);
      if (!mounted.current || sequence !== captureSequence.current) return;
      onTranscriptChange(result);
      clearAudio();
      setRecordError(null);
    } catch {
      // The parent displays authenticated transcription errors; keep the recording for a deliberate retry.
    } finally {
      transcribing.current = false;
    }
  }

  function recordAgain() {
    if (transcript.trim() && !window.confirm("Record again and replace what we heard?")) return;
    captureSequence.current += 1;
    clearAudio();
    setRecordError(null);
    onTranscriptChange("");
  }

  function enterText() {
    onEnterText(transcript);
  }

  return (
    <section className="workflow-surface voice-capture" aria-labelledby="voice-capture-title">
      <h3 id="voice-capture-title">Voice purchase</h3>
      <p className="workflow-copy">Record a short description of what you bought. Your recording stays on this device until you choose to transcribe it.</p>

      {!audio && !recording && !finalizing && (
        <button className="button button-primary" type="button" onClick={() => void startRecording()} disabled={busy !== null || requestingPermission}>
          Start recording
        </button>
      )}
      {requestingPermission && <p className="status-message" role="status">Waiting for microphone permission…</p>}
      {recording && (
        <div className="voice-recording-status">
          <p role="status"><strong>Recording</strong> · {formatElapsed(elapsedSeconds)} / 01:00</p>
          <button className="button button-primary" type="button" onClick={stopRecording}>Stop recording</button>
        </div>
      )}
      {finalizing && <p className="status-message" role="status">Finishing recording…</p>}
      {audioUrl && (
        <div className="voice-playback">
          <label htmlFor="voice-recording-playback">Your recording</label>
          <audio id="voice-recording-playback" controls src={audioUrl} />
          <div className="voice-audio-actions">
            <button className="button button-outline" type="button" onClick={recordAgain} disabled={busy !== null}>Record again</button>
            <button className="button button-primary" type="button" onClick={() => void transcribe()} disabled={busy !== null || !audio}>
              {busy === "transcription" ? "Transcribing…" : "Transcribe recording"}
            </button>
          </div>
          <p className="voice-privacy-note">Your recording is sent to the transcription service. Avoid names, contact details and payment information.</p>
        </div>
      )}
      {busy === "transcription" && <p className="long-running-note" role="status">Still working… Transcription can take a little while. Please keep this dialog open.</p>}

      {recordError && <p className="workflow-error" role="alert">{recordError}</p>}
      {error && <p className="workflow-error" role="alert">{error}</p>}

      {(transcript || preview) && (
        <section className="voice-transcript" aria-labelledby="voice-transcript-title">
          <h3 id="voice-transcript-title">Check what we heard</h3>
          <p className="workflow-copy">Correct product names, quantities and prices. Keep the language you spoke; it will not be translated automatically.</p>
          {preview && (
            <div className="privacy-findings" aria-label="Privacy preview findings">
              {Object.keys(preview.findings).length > 0 ? (
                <>
                  <p>Review the privacy-preview changes:</p>
                  <ul>{Object.entries(preview.findings).map(([name, count]) => <li key={name}>{privacyFindingLabels[name] ?? name.replaceAll("_", " ")}: {count}</li>)}</ul>
                </>
              ) : (
                <p>No removable details were detected. Please review the text yourself.</p>
              )}
            </div>
          )}
          <label htmlFor="voice-transcript-editor">{preview ? "Reviewed transcript to send for extraction" : "Transcript"}</label>
          <textarea
            id="voice-transcript-editor"
            rows={6}
            value={preview ? reviewedText : transcript}
            onChange={(event) => preview ? onReviewedTextChange(event.target.value) : onTranscriptChange(event.target.value)}
            maxLength={16000}
            disabled={busy !== null}
          />
          {preview ? (
            <label className="acknowledgement">
              <input
                type="checkbox"
                checked={acknowledged}
                onChange={(event) => onAcknowledgedChange(event.target.checked)}
                disabled={busy !== null}
              />
              <span>I have checked this text and want to send it for extraction.</span>
            </label>
          ) : null}
          <div className="capture-dialog-actions">
            {!preview ? (
              <button className="button button-outline" type="button" onClick={onReviewPrivacy} disabled={!transcript.trim() || busy !== null}>
                {busy === "privacy" ? "Checking text…" : "Review text for privacy"}
              </button>
            ) : (
              <button className="button button-primary" type="button" onClick={onExtract} disabled={!acknowledged || !reviewedText.trim() || busy !== null}>
                {busy === "extraction" ? "Extracting…" : "Extract purchase"}
              </button>
            )}
          </div>
        </section>
      )}

      <div className="voice-navigation">
        <button className="button button-outline" type="button" onClick={onBack} disabled={busy === "privacy" || busy === "extraction"}>
          Back
        </button>
        <button className="text-link" type="button" onClick={enterText} disabled={busy !== null}>
          Enter as text
        </button>
      </div>
    </section>
  );
}
