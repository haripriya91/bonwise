import {AIWorking} from "../components/AIWorking";
import { useEffect, useRef, useState } from "react";
import type { PointerEvent } from "react";

type Mask = { id: number; x: number; y: number; width: number; height: number };
type PreparedImage = {
  dataUrl: string;
  width: number;
  height: number;
  mimeType: "image/jpeg" | "image/png";
};
type Rectangle = { x: number; y: number; width: number; height: number };

const maximumSourceBytes = 25_000_000;
const maximumOriginalPixels = 32_000_000;
const maximumPixels = 16_000_000;
const maximumOutputBytes = 2_000_000;

function blobFromCanvas(
  canvas: HTMLCanvasElement,
  mimeType: string,
  quality?: number,
): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => blob ? resolve(blob) : reject(new Error("The image could not be prepared in this browser.")),
      mimeType,
      quality,
    );
  });
}

function dataUrlFromBlob(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => typeof reader.result === "string"
      ? resolve(reader.result)
      : reject(new Error("The prepared image could not be read."));
    reader.onerror = () => reject(new Error("The prepared image could not be read."));
    reader.readAsDataURL(blob);
  });
}

async function detectImageType(file: File): Promise<"image/jpeg" | "image/png" | null> {
  if (file.type === "image/jpeg" || file.type === "image/png") return file.type;
  const bytes = new Uint8Array(await file.slice(0, 8).arrayBuffer());
  const png = [137, 80, 78, 71, 13, 10, 26, 10].every((value, index) => bytes[index] === value);
  if (png) return "image/png";
  const jpeg = bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255;
  return jpeg ? "image/jpeg" : null;
}

async function declaredDimensions(
  file: File,
  mimeType: "image/jpeg" | "image/png",
): Promise<{ width: number; height: number } | null> {
  const bytes = new Uint8Array(await file.slice(0, Math.min(file.size, 512_000)).arrayBuffer());
  if (mimeType === "image/png") {
    if (bytes.length < 24) return null;
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    return { width: view.getUint32(16), height: view.getUint32(20) };
  }

  if (bytes[0] !== 255 || bytes[1] !== 216) return null;
  const frameMarkers = new Set([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf]);
  let offset = 2;
  while (offset + 3 < bytes.length) {
    if (bytes[offset] !== 255) {
      offset += 1;
      continue;
    }
    while (bytes[offset] === 255) offset += 1;
    const marker = bytes[offset];
    offset += 1;
    if (marker === 0xd8 || marker === 0xd9 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) continue;
    if (offset + 1 >= bytes.length) return null;
    const segmentLength = bytes[offset] * 256 + bytes[offset + 1];
    if (segmentLength < 2 || offset + segmentLength > bytes.length) return null;
    if (frameMarkers.has(marker)) {
      if (segmentLength < 7) return null;
      return {
        height: bytes[offset + 3] * 256 + bytes[offset + 4],
        width: bytes[offset + 5] * 256 + bytes[offset + 6],
      };
    }
    offset += segmentLength;
  }
  return null;
}

async function prepareImage(file: File): Promise<PreparedImage> {
  if (file.size > maximumSourceBytes) {
    throw new Error("This file is too large to prepare safely. Choose a smaller JPEG or PNG; the image will not be cropped.");
  }
  const mimeType = await detectImageType(file);
  if (!mimeType) throw new Error("Choose a JPEG or PNG image.");
  const dimensions = await declaredDimensions(file, mimeType);
  if (!dimensions?.width || !dimensions.height) {
    throw new Error("The image dimensions could not be read safely. Choose another JPEG or PNG.");
  }
  if (dimensions.width * dimensions.height > maximumOriginalPixels) {
    throw new Error("This image is too large to prepare safely in the browser. Choose a smaller image; the image will not be cropped.");
  }

  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    throw new Error("This image could not be decoded. Try another JPEG or PNG file.");
  }

  try {
    const sourcePixels = bitmap.width * bitmap.height;
    if (!bitmap.width || !bitmap.height || sourcePixels > maximumOriginalPixels) {
      throw new Error("This image is too large to prepare in the browser. Choose a smaller image; the image will not be cropped.");
    }

    let scale = Math.min(1, Math.sqrt(maximumPixels / sourcePixels));
    const canvas = document.createElement("canvas");

    for (let attempt = 0; attempt < 18; attempt += 1) {
      const width = Math.max(1, Math.floor(bitmap.width * scale));
      const height = Math.max(1, Math.floor(bitmap.height * scale));
      if (width * height > maximumPixels) {
        scale *= Math.sqrt(maximumPixels / (width * height));
        continue;
      }

      canvas.width = width;
      canvas.height = height;
      const context = canvas.getContext("2d", { alpha: false });
      if (!context) throw new Error("Image preparation is not available in this browser.");
      context.fillStyle = "#FFFFFF";
      context.fillRect(0, 0, width, height);
      context.drawImage(bitmap, 0, 0, width, height);

      if (mimeType === "image/png" && attempt === 0) {
        const png = await blobFromCanvas(canvas, "image/png");
        if (png.size <= maximumOutputBytes) {
          return { dataUrl: await dataUrlFromBlob(png), width, height, mimeType: "image/png" };
        }
      }

      for (const quality of [0.94, 0.88, 0.82, 0.76]) {
        const jpeg = await blobFromCanvas(canvas, "image/jpeg", quality);
        if (jpeg.size <= maximumOutputBytes) {
          return { dataUrl: await dataUrlFromBlob(jpeg), width, height, mimeType: "image/jpeg" };
        }
      }

      if (width * height < 500_000) break;
      scale *= 0.88;
    }

    throw new Error("This image cannot be prepared below the upload limits without making receipt text too small. Choose a clearer, smaller image; it will not be cropped.");
  } finally {
    bitmap.close();
  }
}

async function applyMasks(prepared: PreparedImage, masks: Mask[]): Promise<string> {
  if (masks.length === 0) return prepared.dataUrl;

  const response = await fetch(prepared.dataUrl);
  const bitmap = await createImageBitmap(await response.blob());
  try {
    const canvas = document.createElement("canvas");
    canvas.width = prepared.width;
    canvas.height = prepared.height;
    const context = canvas.getContext("2d", { alpha: false });
    if (!context) throw new Error("The redacted image could not be prepared.");
    context.drawImage(bitmap, 0, 0);
    context.fillStyle = "#000000";
    for (const mask of masks) {
      // Covers are flattened into the raster used for both preview and extraction.
      context.fillRect(mask.x, mask.y, mask.width, mask.height);
    }
    if (canvas.width * canvas.height > maximumPixels) {
      throw new Error("The masked image exceeds the upload limits. Clear or reduce masks, or choose a smaller image.");
    }
    const qualities = prepared.mimeType === "image/jpeg" ? [0.94, 0.9, 0.86, 0.82, 0.78] : [undefined];
    for (const quality of qualities) {
      const blob = await blobFromCanvas(canvas, prepared.mimeType, quality);
      if (blob.size <= maximumOutputBytes) return await dataUrlFromBlob(blob);
    }
    throw new Error("The masked image exceeds the 2 MB upload limit. Choose a smaller image; it will not be cropped.");
  } finally {
    bitmap.close();
  }
}

function rectangleFromPoints(start: { x: number; y: number }, end: { x: number; y: number }): Rectangle {
  return {
    x: Math.min(start.x, end.x),
    y: Math.min(start.y, end.y),
    width: Math.abs(end.x - start.x),
    height: Math.abs(end.y - start.y),
  };
}

function clampedPoint(
  event: PointerEvent<SVGSVGElement>,
  width: number,
  height: number,
): { x: number; y: number } {
  const bounds = event.currentTarget.getBoundingClientRect();
  return {
    x: Math.max(0, Math.min(width, Math.round((event.clientX - bounds.left) * width / bounds.width))),
    y: Math.max(0, Math.min(height, Math.round((event.clientY - bounds.top) * height / bounds.height))),
  };
}

export function ImageCapture({
  extracting,
  error,
  onExtract,
  onBack,
  onWorkChange,
}: {
  extracting: boolean;
  error: string | null;
  onExtract: (dataUrl: string) => void;
  onBack: () => void;
  onWorkChange: (hasWork: boolean) => void;
}) {
  const [prepared, setPrepared] = useState<PreparedImage | null>(null);
  const [finalImage, setFinalImage] = useState<string | null>(null);
  const [masks, setMasks] = useState<Mask[]>([]);
  const [acknowledged, setAcknowledged] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [drawMode, setDrawMode] = useState(false);
  const [drawing, setDrawing] = useState<Rectangle | null>(null);
  const [rectangle, setRectangle] = useState<Rectangle>({ x: 0, y: 0, width: 120, height: 100 });
  const selectionSequence = useRef(0);
  const previewSequence = useRef(0);
  const nextMaskId = useRef(0);
  const viewport = useRef<HTMLDivElement>(null);
  const pan = useRef<{ pointerId: number; x: number; y: number; scrollLeft: number; scrollTop: number } | null>(null);
  const drawStart = useRef<{ x: number; y: number } | null>(null);

  useEffect(() => () => {
    selectionSequence.current += 1;
    previewSequence.current += 1;
  }, []);

  useEffect(() => {
    onWorkChange(Boolean(prepared || masks.length || preparing));
  }, [prepared, masks.length, onWorkChange, preparing]);

  useEffect(() => {
    if (!prepared) {
      setFinalImage(null);
      return;
    }
    const currentSequence = ++previewSequence.current;
    setFinalImage(null);
    void applyMasks(prepared, masks).then((image) => {
      if (previewSequence.current === currentSequence) setFinalImage(image);
    }).catch((failure: unknown) => {
      if (previewSequence.current === currentSequence) {
        setLocalError(failure instanceof Error ? failure.message : "The masked image could not be prepared.");
      }
    });
    return () => { previewSequence.current += 1; };
  }, [prepared, masks]);

  async function selectFile(file: File | undefined) {
    if (!file || extracting) return;
    const currentSequence = ++selectionSequence.current;
    setPrepared(null);
    setFinalImage(null);
    setMasks([]);
    setAcknowledged(false);
    setLocalError(null);
    setDrawing(null);
    setPreparing(true);
    try {
      const image = await prepareImage(file);
      if (selectionSequence.current !== currentSequence) return;
      setPrepared(image);
      setRectangle({ x: 0, y: 0, width: Math.min(120, image.width), height: Math.min(100, image.height) });
      setZoom(1);
    } catch (failure) {
      if (selectionSequence.current === currentSequence) {
        setLocalError(failure instanceof Error ? failure.message : "The image could not be prepared.");
      }
    } finally {
      if (selectionSequence.current === currentSequence) setPreparing(false);
    }
  }

  function addMask(candidate: Rectangle) {
    if (!prepared) return;
    if (![candidate.x, candidate.y, candidate.width, candidate.height].every(Number.isFinite)) {
      setLocalError("Enter numeric image coordinates and dimensions.");
      return;
    }
    const x = Math.floor(candidate.x);
    const y = Math.floor(candidate.y);
    const width = Math.floor(candidate.width);
    const height = Math.floor(candidate.height);
    if (
      x < 0 || y < 0 || width < 4 || height < 4 ||
      x + width > prepared.width || y + height > prepared.height
    ) {
      setLocalError("Enter a rectangle at least 4 × 4 pixels that fits within the image.");
      return;
    }
    if (masks.length >= 100) {
      setLocalError("A maximum of 100 privacy masks is supported.");
      return;
    }
    setMasks((current) => [...current, { id: ++nextMaskId.current, x, y, width, height }]);
    setAcknowledged(false);
    setLocalError(null);
  }

  function pointerDown(event: PointerEvent<SVGSVGElement>) {
    if (!prepared || !drawMode || extracting) return;
    event.preventDefault();
    event.stopPropagation();
    const point = clampedPoint(event, prepared.width, prepared.height);
    drawStart.current = point;
    setDrawing({ x: point.x, y: point.y, width: 0, height: 0 });
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function pointerMove(event: PointerEvent<SVGSVGElement>) {
    if (!prepared || !drawMode || !drawStart.current) return;
    event.preventDefault();
    event.stopPropagation();
    setDrawing(rectangleFromPoints(drawStart.current, clampedPoint(event, prepared.width, prepared.height)));
  }

  function pointerUp(event: PointerEvent<SVGSVGElement>) {
    if (!prepared || !drawMode || !drawStart.current) return;
    event.preventDefault();
    event.stopPropagation();
    const selected = rectangleFromPoints(drawStart.current, clampedPoint(event, prepared.width, prepared.height));
    drawStart.current = null;
    setDrawing(null);
    addMask(selected);
  }

  function panStart(event: PointerEvent<HTMLDivElement>) {
    if (drawMode || extracting || event.button !== 0 || !viewport.current) return;
    pan.current = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      scrollLeft: viewport.current.scrollLeft,
      scrollTop: viewport.current.scrollTop,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function panMove(event: PointerEvent<HTMLDivElement>) {
    const state = pan.current;
    if (!state || state.pointerId !== event.pointerId || !viewport.current) return;
    viewport.current.scrollLeft = state.scrollLeft - (event.clientX - state.x);
    viewport.current.scrollTop = state.scrollTop - (event.clientY - state.y);
  }

  return (
    <section className="image-capture workflow-surface" aria-labelledby="image-capture-title">
      <h2 id="image-capture-title">Receipt image</h2>
      <p className="workflow-copy">Choose a JPEG or PNG. Nothing is uploaded until you choose Get purchase details.</p>
      <aside className="receipt-privacy-warning" role="note" aria-label="Protect personal information before uploading">
        <span className="receipt-warning-icon" aria-hidden="true"><svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M12 5v9"/><circle cx="12" cy="19" r="1" fill="currentColor" stroke="none"/></svg></span>
        <div><strong>Check personal details before sending</strong><p>No automatic detection. Cover names, contact or payment details, membership IDs and identifying codes in the image review.</p></div>
      </aside>
      <div className="image-inputs">
        <label className="image-file-control mobile-camera-control">
          <span>Take a receipt photo</span>
          <input
            type="file"
            accept="image/jpeg,image/png"
            capture="environment"
            disabled={extracting || preparing}
            onChange={(event) => {
              const file = event.currentTarget.files?.[0];
              event.currentTarget.value = "";
              void selectFile(file);
            }}
          />
        </label>
        <label className="image-file-control">
          <span>Upload JPEG or PNG</span>
          <input
            type="file"
            accept="image/jpeg,image/png"
            disabled={extracting || preparing}
            onChange={(event) => {
              const file = event.currentTarget.files?.[0];
              event.currentTarget.value = "";
              void selectFile(file);
            }}
          />
        </label>
      </div>


      {preparing && <p className="status-message" role="status">Preparing image on this device…</p>}
      {(localError || error) && <p className="workflow-error" role="alert">{localError ?? error}</p>}

      {prepared && (
        <>
          <p className="mask-instruction">Drag over personal details to cover them. Nothing is hidden automatically.</p>
          <div className="image-viewer-controls" aria-label="Image zoom and pan controls">
            <button type="button" className="button button-outline" onClick={() => setZoom((value) => Math.max(0.5, value - 0.25))} disabled={zoom <= 0.5}>Zoom out</button>
            <button type="button" className="button button-outline" onClick={() => setZoom(1)}>Fit width</button>
            <button type="button" className="button button-outline" onClick={() => setZoom((value) => Math.min(3, value + 0.25))} disabled={zoom >= 3}>Zoom in</button>
            <span>{Math.round(zoom * 100)}%</span>
          </div>
          <div
            ref={viewport}
            className={drawMode ? "image-viewer-viewport drawing" : "image-viewer-viewport"}
            tabIndex={0}
            role="region"
            onPointerDown={panStart}
            onPointerMove={panMove}
            onPointerUp={() => { pan.current = null; }}
            onPointerCancel={() => { pan.current = null; }}
            aria-label="Prepared receipt image. Drag to draw covers when drawing mode is on; otherwise drag or scroll to pan."
          >
            <div className="image-viewer-stage" style={{ width: `${zoom * 100}%` }}>
              {finalImage && <img src={finalImage} alt="Prepared receipt preview with black privacy covers applied" draggable={false} />}
              <svg
                className="mask-drawing-layer"
                viewBox={`0 0 ${prepared.width} ${prepared.height}`}
                preserveAspectRatio="none"
                aria-label="Draw privacy covers by dragging a rectangle over the image"
                onPointerDown={pointerDown}
                onPointerMove={pointerMove}
                onPointerUp={pointerUp}
                onPointerCancel={() => { drawStart.current = null; setDrawing(null); }}
              >
                {drawing && <rect x={drawing.x} y={drawing.y} width={drawing.width} height={drawing.height} />}
              </svg>
            </div>
          </div>
          {!finalImage && <p className="status-message" role="status">Updating the masked preview…</p>}

          <div className="mask-actions">
            <button
              type="button"
              className={drawMode ? "button button-lime" : "button button-outline"}
              onClick={() => setDrawMode((value) => !value)}
              disabled={extracting || !finalImage}
              aria-pressed={drawMode}
            >
              Draw privacy covers
            </button>
            {drawMode && <span className="drawing-mode-indicator" role="status">Drawing mode on</span>}
            <button type="button" className="text-link" onClick={() => { setMasks((current) => current.slice(0, -1)); setAcknowledged(false); setLocalError(null); }} disabled={!masks.length || extracting}>Undo last cover</button>
            <button type="button" className="text-link" onClick={() => { setMasks([]); setAcknowledged(false); setLocalError(null); }} disabled={!masks.length || extracting}>Clear all covers</button>
          </div>
          <details className="coordinate-mask-details">
            <summary>Keyboard alternative: add a cover by coordinates</summary>
            <form
              className="coordinate-mask-form"
              onSubmit={(event) => {
                event.preventDefault();
                addMask({
                  x: Number(rectangle.x),
                  y: Number(rectangle.y),
                  width: Number(rectangle.width),
                  height: Number(rectangle.height),
                });
              }}
            >
              <fieldset disabled={extracting || preparing}>
                <legend>Add a cover by image coordinates (pixels)</legend>
                <div className="coordinate-mask-fields">
                  {([
                    ["mask-x", "Left (X)", "x"],
                    ["mask-y", "Top (Y)", "y"],
                    ["mask-width", "Width", "width"],
                    ["mask-height", "Height", "height"],
                  ] as const).map(([id, label, key]) => (
                    <label key={id} htmlFor={id}>
                      {label}
                      <input
                        id={id}
                        type="number"
                        min="0"
                        max={key === "x" || key === "width" ? prepared.width : prepared.height}
                        step="1"
                        value={rectangle[key]}
                        onChange={(event) => setRectangle((current) => ({ ...current, [key]: event.target.value === "" ? 0 : Number(event.target.value) }))}
                      />
                    </label>
                  ))}
                  <button className="button button-outline" type="submit">Add cover</button>
                </div>
              </fieldset>
            </form>
          </details>
          <ul className="mask-list" aria-label="Privacy covers">
            {masks.map((mask, index) => (
              <li key={mask.id}>
                <span>Cover {index + 1} · {mask.x}, {mask.y}, {mask.width} × {mask.height} px</span>
                <button type="button" className="text-link" onClick={() => { setMasks((current) => current.filter((entry) => entry.id !== mask.id)); setAcknowledged(false); setLocalError(null); }} disabled={extracting}>Remove cover {index + 1}</button>
              </li>
            ))}
          </ul>
          <label className="acknowledgement image-acknowledgement">
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={(event) => setAcknowledged(event.target.checked)}
              disabled={!finalImage || extracting}
            />
            <span>I checked this image and removed personal details.</span>
          </label>
          <div className="step-actions">
            <button className="button button-outline" type="button" onClick={onBack} disabled={extracting}>Back</button>
            <button
              className="button button-primary"
              type="button"
              onClick={() => finalImage && onExtract(finalImage)}
              disabled={!acknowledged || !finalImage || extracting || preparing}
            >
              {extracting ? "Preparing details…" : "Get purchase details"}
            </button>
          </div>
          {extracting && <AIWorking label="Reading receipt details for review"/>}
        </>
      )}
      {!prepared && (
        <div className="step-actions image-back-action">
          <button className="button button-outline" type="button" onClick={onBack} disabled={preparing}>Back</button>
        </div>
      )}
    </section>
  );
}
