import {useEffect,useRef,useState} from "react";
import {createPortal} from "react-dom";
import type {MouseEvent,KeyboardEvent} from "react";
import {PrivacyNotes} from "../components/PrivacyNotes";
import {referenceMarkup} from "./referenceMarkup";
import "./Welcome.css";
const featureNames=["Capture & review","Ask Bonwise","Plan your weekly shop","Compare offers"];
const examples={milk:{q:"What did I pay for milk last time?",a:"In the sample receipt, whole milk was €1.29 for 1 L."},last:{q:"What did I buy in my last shop?",a:"The sample receipt lists milk, bananas, wholegrain bread and eggs. Total: €8.56."},bread:{q:"When did I last buy bread?",a:"The sample receipt records wholegrain bread on 7 November for €2.49."}};
/** Static reference markup contains no scripts, provider output or user input. */
export function Welcome({onLogin}:{onLogin:()=>void}){
 const root=useRef<HTMLDivElement>(null),featureIndex=useRef(0);
 const [privacySlot,setPrivacySlot]=useState<Element|null>(null);
 useEffect(()=>{setPrivacySlot(root.current?.querySelector('[data-privacy-slot]')??null);},[]);
 function select(index:number,focus=false){
  const el=root.current;if(!el)return;const tabs=[...el.querySelectorAll<HTMLButtonElement>('[data-feature]')];
  featureIndex.current=(index+tabs.length)%tabs.length;
  tabs.forEach((tab,i)=>{const selected=i===featureIndex.current;tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected?0:-1;const panel=el.querySelector<HTMLElement>('#'+tab.getAttribute('aria-controls'));if(panel)panel.hidden=!selected;el.querySelectorAll('.feature-dots span')[i]?.classList.toggle('active',selected);});
  const status=el.querySelector('#feature-status');if(status)status.textContent=String(featureIndex.current+1).padStart(2,'0')+' / 04 · '+featureNames[featureIndex.current];
  if(focus)tabs[featureIndex.current].focus();
 }
 function click(e:MouseEvent){
  if(!(e.target instanceof Element))return;const button=e.target.closest<HTMLButtonElement>('button');if(!button)return;
  if(button.hasAttribute('data-login')){onLogin();return;}
  if(button.hasAttribute('data-feature')){select([...root.current!.querySelectorAll('[data-feature]')].indexOf(button));return;}
  if(button.id==='next-feature'){select(featureIndex.current+1);return;}if(button.id==='previous-feature'){select(featureIndex.current-1);return;}
  const key=button.dataset.example as keyof typeof examples;if(key&&examples[key]){const example=examples[key],el=root.current!;el.querySelector('#example-question')!.textContent=example.q;el.querySelector('#example-answer')!.textContent=example.a;el.querySelectorAll('[data-example]').forEach(t=>t.setAttribute('aria-pressed',String(t===button)));}
 }
 function keydown(e:KeyboardEvent){
  if(!(e.target instanceof Element)||!e.target.closest('[data-feature]'))return;
  const next=e.key==='ArrowRight'?featureIndex.current+1:e.key==='ArrowLeft'?featureIndex.current-1:e.key==='Home'?0:e.key==='End'?3:null;
  if(next!==null){e.preventDefault();select(next,true);}
 }
 return <><div className="bw-reference" ref={root} onClick={click} onKeyDown={keydown} dangerouslySetInnerHTML={{__html:referenceMarkup}}/>{privacySlot&&createPortal(<PrivacyNotes/>,privacySlot)}</>;
}
