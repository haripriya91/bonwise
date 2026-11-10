import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import "./InformationTip.css";

export function InformationTip({ label, text }: { label: string; text: string }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ left: 16, top: 16, above: false });
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dismissed = useRef(false);
  function cancelTimer() {if(timer.current) clearTimeout(timer.current);}
  function reveal() {cancelTimer();if(!dismissed.current)setOpen(true);}
  function close(restoreFocus = false) {
    cancelTimer();dismissed.current=true;setOpen(false);
    if(restoreFocus)trigger.current?.focus({preventScroll:true});
  }
  function leave() {cancelTimer();timer.current=setTimeout(()=>{setOpen(false);dismissed.current=false;},150);}
  useEffect(()=>()=>cancelTimer(),[]);
  useLayoutEffect(()=>{
    if(!open)return;
    function place() {
      const anchor=trigger.current?.getBoundingClientRect(), bubble=panel.current?.getBoundingClientRect();
      if(!anchor || !bubble)return;
      const width=Math.min(320,window.innerWidth-32);
      const above=anchor.bottom+bubble.height+12>window.innerHeight-16 && anchor.top>bubble.height+12;
      setPosition({left:Math.max(16,Math.min(anchor.left-12,window.innerWidth-width-16)),
        top:above?anchor.top-bubble.height-10:anchor.bottom+10,above});
    }
    function outside(event: Event) {if(!trigger.current?.contains(event.target as Node) && !panel.current?.contains(event.target as Node))close();}
    function escape(event: KeyboardEvent) {if(event.key==='Escape'){event.preventDefault();close(!!panel.current?.contains(document.activeElement));}}
    place();window.addEventListener('resize',place);window.addEventListener('scroll',place,true);
    document.addEventListener('pointerdown',outside);document.addEventListener('keydown',escape);
    return ()=>{window.removeEventListener('resize',place);window.removeEventListener('scroll',place,true);document.removeEventListener('pointerdown',outside);document.removeEventListener('keydown',escape);};
  },[open]);
  function blur(next: EventTarget | null) {if(!panel.current?.contains(next as Node) && !trigger.current?.contains(next as Node))leave();}
  return <span className="information-tip">
    <button ref={trigger} type="button" className="information-tip__trigger" aria-label={label} aria-haspopup="dialog" aria-expanded={open} aria-controls={open?id:undefined}
      onMouseEnter={reveal} onMouseLeave={leave} onFocus={reveal} onBlur={event=>blur(event.relatedTarget)}
      onClick={()=>{dismissed.current=false;cancelTimer();setOpen(true);}}><span aria-hidden="true">i</span></button>
    {open && createPortal(<div ref={panel} id={id} role="dialog" aria-label={label} className={`information-tip__panel${position.above?' is-above':''}`}
      style={{left:position.left,top:position.top}} onMouseEnter={cancelTimer} onMouseLeave={leave} onBlur={event=>blur(event.relatedTarget)}>
      <p>{text}</p><button type="button" className="information-tip__close" aria-label="Close information" onClick={()=>close(true)}>×</button>
    </div>,document.body)}
  </span>;
}

