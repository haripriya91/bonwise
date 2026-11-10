import { useEffect, useId, useRef, useState } from "react";
import "./PrivacyNotes.css";

export const PRIVACY_NOTES_VERSION = "2026-11-09-correction-rag-v3";

export const PRIVACY_SECTIONS = [
  {
    title: "Use synthetic data for this demo",
    text: "Use fictional receipts and non-sensitive questions and commitment titles. The release privacy review is not complete. Do not upload payment-card details, loyalty identifiers, addresses, names or sensitive personal information. Crop or redact receipts before submitting them; automated detection cannot guarantee that every identifier is removed.",
  },
  {
    title: "What Bonwise processes",
    text: "Bonwise uses sign-in and household information, receipt images, purchase details, questions, voice recordings/transcripts, budgets and commitment titles and amounts to provide its features. Receipts and purchase history linked to an account can be personal data, even after obvious names are removed.",
  },
  {
    title: "When information leaves your device",
    text: "Choosing receipt extraction or text interpretation sends the submitted content to the backend and OpenAI. Choosing voice transcription sends the recording through the backend to OpenAI. Read and edit the transcript before sending a question or confirming a purchase. Sending a question can also send it and relevant records or conversation context to the model. Reading these notes is not permission to submit content automatically.",
  },
  {
    title: "Storage and service providers",
    text: "Supabase supports accounts and application storage. Drafts, purchase records and receipt files may be stored during the workflow; confirmation records spending. Logging out does not delete saved records. OpenAI handles model and speech processing. Pinecone holds the public product catalogue; product search wording is also processed to create a search embedding. LangSmith supports diagnostics. Provider retention and deletion behavior require separate verification; an application setting alone is not a promise of zero provider retention.",
  },
  {
    title: "Shopping suggestions and saved plans",
    text: "Opening Shopping plan may send selected product descriptions and purchase-frequency/date summaries from your household to OpenAI to prepare suggestions. It does not send receipt images or commitment titles for this step. Text-edit requests are sent when you choose Preview changes. Raw edit text is not stored in the plan; a proposed change is held for up to 20 minutes for acceptance. Saved lists, evidence references and minimized workflow checkpoints remain in the private database across sessions. Approval reserves funds; checking an item Done does not record spending. Cancelling releases the open reservation but does not delete plan history. Checkpoint cleanup is an operator task; provider and backup retention require separate verification.",
  },
  {
    title: "No location selection for offers",
    text: "The offers feature does not ask for GPS, a home address or a postcode, and does not infer an area from your purchases or IP address to choose offers. It lists offers with an explicit national scope. Receipts can still contain merchant/location details, and hosting services may process connection information. Demo offers are fictional; nationwide offer scope does not guarantee stock or eligibility at every branch.",
  },
  {
    title: "Learning from accepted product links",
    text: "Confirming a purchase with a product you selected saves an accepted matching example for your household. Minimal product wording is sent to OpenAI for an embedding and stored as a vector in a separate household scope in Pinecone. Full receipts, separate price fields and merchant names are not embedded for this purpose. Later matching can use similar accepted examples alongside the public product catalogue. This is retrieval, not model training. Examples remain suggestions and cannot confirm a purchase. Old vector records are ignored if their SQL source changes or is deleted; removing those vectors from the provider must also be part of the operator’s deletion process. The release retention and cross-provider deletion review remains pending.",
  },
  {
    title: "Review, access and deletion",
    text: "Review extracted details and proposed product matches before confirming. Household access should be limited to authorized members. Contact the demo operator for privacy questions and data-access or deletion requests. Self-service export/deletion, retention periods and cross-provider deletion are release checks; they are not claimed as completed features here. Closing this window does not delete data or withdraw a previously submitted request.",
  },
] as const;

type Props = {
  contactText?: string;
  label?: string;
};

/** Mount outside sign-in gates as well as in the app. This is a notice, not a consent banner. */
export function PrivacyNotes({ contactText, label = "Privacy notes" }: Props) {
  const [open, setOpen] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const titleId = useId();

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (open && !element.open) element.showModal();
    if (!open && element.open) element.close();
  }, [open]);

  function finish() {
    setOpen(false);
    trigger.current?.focus();
  }

  return (
    <>
      <button ref={trigger} type="button" className="privacy-link" onClick={() => setOpen(true)}>
        {label}
      </button>
      <dialog ref={dialog} aria-labelledby={titleId} className="privacy-notes" onCancel={finish} onClose={finish}>
        <header className="privacy-notes__header">
          <div>
            <p className="privacy-notes__eyebrow">Bonwise · demo</p>
            <h2 id={titleId}>Privacy notes</h2>
          </div>
          <button type="button" onClick={finish} autoFocus aria-label="Close privacy notes">Close</button>
        </header>
        <div className="privacy-notes__body">
          {PRIVACY_SECTIONS.map((section) => (
            <section key={section.title}>
              <h3>{section.title}</h3>
              <p>{section.text}</p>
            </section>
          ))}
          <section>
            <h3>Contact</h3>
            <p>{contactText?.trim() || "For this supervised demo, contact the presenter before submitting personal data. A named operator and contact channel must be configured before public release."}</p>
          </section>
          <p className="privacy-notes__version">Notice version: {PRIVACY_NOTES_VERSION}. Privacy review pending; no compliance certification is claimed.</p>
        </div>
      </dialog>
    </>
  );
}
