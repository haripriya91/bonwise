import {Component} from "react";
import type {ReactNode} from "react";
/** A failed lazy chunk should offer recovery, rather than a blank page. */
export class SectionBoundary extends Component<{children:ReactNode},{failed:boolean}>{
 state={failed:false};
 static getDerivedStateFromError(){return {failed:true};}
 render(){if(this.state.failed)return <section className="error-block" role="alert"><h2>This section could not open</h2><p>Refresh to try again. Unsaved edits may be lost.</p><button type="button" className="button button-primary" onClick={()=>window.location.reload()}>Refresh application</button></section>;return this.props.children;}
}
