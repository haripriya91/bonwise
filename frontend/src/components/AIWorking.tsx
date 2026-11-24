import "./AIWorking.css";

type Props = { label?: string; onCancel?: () => void };

export function AIWorking({ label = "Preparing your result", onCancel }: Props) {
  return (
    <div className="ai-working" role="status" aria-live="polite">
      <span className="ai-working-dots" aria-hidden="true"><i /><i /><i /></span>
      <span className="ai-working-label">{label}</span>
      {onCancel && <button type="button" className="text-link" onClick={onCancel}>Stop waiting</button>}
    </div>
  );
}
