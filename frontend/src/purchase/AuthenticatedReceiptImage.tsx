import { useEffect, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { ApiError, apiBinaryRequest, getErrorText } from "../api/client";

type ReceiptState =
  | { status: "loading" }
  | { status: "image"; url: string }
  | { status: "missing" }
  | { status: "error"; message: string };

export function AuthenticatedReceiptImage({
  session,
  path,
  alt,
  emptyMessage = "No receipt saved for this purchase.",
}: {
  session: Session;
  path: string;
  alt: string;
  emptyMessage?: string;
}) {
  const [attempt, setAttempt] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [receipt, setReceipt] = useState<ReceiptState>({ status: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | undefined;
    setReceipt({ status: "loading" });
    setZoom(1);

    void apiBinaryRequest(path, session.access_token, { signal: controller.signal })
      .then((blob) => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setReceipt({ status: "image", url: objectUrl });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        if (error instanceof ApiError && error.status === 404) {
          setReceipt({ status: "missing" });
          return;
        }
        setReceipt({ status: "error", message: getErrorText(error) });
      });

    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [path, session.access_token, session.user.id, attempt]);

  if (receipt.status === "loading") {
    return <p className="status-message" role="status">Loading saved reviewed receipt…</p>;
  }
  if (receipt.status === "missing") {
    return <p className="empty-state">{emptyMessage}</p>;
  }
  if (receipt.status === "error") {
    return (
      <div className="error-block" role="alert">
        <p>{receipt.message}</p>
        <button className="button button-outline receipt-retry" type="button" onClick={() => setAttempt((value) => value + 1)}>
          Retry image
        </button>
      </div>
    );
  }

  return (
    <div className="saved-receipt-image">
      <div className="image-viewer-controls" aria-label="Saved receipt zoom controls">
        <button
          type="button"
          className="button button-outline"
          onClick={() => setZoom((value) => Math.max(0.5, value - 0.25))}
          disabled={zoom <= 0.5}
        >
          Zoom out
        </button>
        <button type="button" className="button button-outline" onClick={() => setZoom(1)}>Fit width</button>
        <button
          type="button"
          className="button button-outline"
          onClick={() => setZoom((value) => Math.min(3, value + 0.25))}
          disabled={zoom >= 3}
        >
          Zoom in
        </button>
        <span>{Math.round(zoom * 100)}%</span>
      </div>
      <div className="review-image-viewport" tabIndex={0} role="region" aria-label="Scrollable saved reviewed receipt image. Use zoom controls and scroll to read.">
        <div className="review-image-stage" style={{ width: `${zoom * 100}%` }}>
          <img src={receipt.url} alt={alt} />
        </div>
      </div>
    </div>
  );
}
