import "./AIWorking.css";

/** Honest request feedback: no timed pretend stages or invented percentages. */
export function AIWorking({label="Preparing your result",onCancel}:{label?:string;onCancel?:()=>void}){
  return <div className="ai-working" role="status" aria-live="polite">
    <span className="ai-working-dots" aria-hidden="true"><i/><i/><i/></span>
    <span className="ai-working-label">{label}</span>
    {onCancel&&<button type="button" className="text-link" onClick={onCancel}>Stop waiting</button>}
  </div>;
}
