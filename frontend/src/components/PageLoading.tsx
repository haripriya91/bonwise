/** Lightweight feedback for code/data loading; no fake progress. */
export function PageLoading({label="Opening this section"}:{label?:string}){
 return <div className="page-loading" role="status" aria-live="polite"><span className="page-loading-spinner" aria-hidden="true"/><span>{label}</span></div>;
}
