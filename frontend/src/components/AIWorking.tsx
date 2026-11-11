import {useEffect,useState} from "react";
import "./AIWorking.css";

/** Honest request feedback: no timed pretend stages or invented percentages. */
export function AIWorking({label="Preparing your result",onCancel}:{label?:string;onCancel?:()=>void}){
  const [slow,setSlow]=useState(false);
  useEffect(()=>{setSlow(false);const timer=window.setTimeout(()=>setSlow(true),12000);return()=>window.clearTimeout(timer);},[label]);
  return <div className="ai-working" role="status" aria-live="polite">
    <span className="ai-working-orbit" aria-hidden="true"><i/><i/><i/></span>
    <div className="ai-working-body"><strong>{label}</strong>
      <span>{slow?"Taking longer than usual. You can keep waiting.":"Your request is in progress."}</span>
      <div className="ai-working-lines" aria-hidden="true"><i/><i/></div>
    </div>
    {onCancel&&<button type="button" className="text-link" onClick={onCancel}>Stop waiting</button>}
  </div>;
}
