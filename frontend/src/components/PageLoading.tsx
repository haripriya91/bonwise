import { AIWorking } from "./AIWorking";

/** Page and AI requests share the same labelled three-dot indicator. */
export function PageLoading({ label = "Loading this section" }: { label?: string }) {
  return <div className="page-loading"><AIWorking label={label} /></div>;
}
