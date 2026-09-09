import { useState } from "react";
import { copyToClipboard, joinFinalText } from "../api/lines";
import type { ImageDetailDto } from "../api/types";

export function Toolbar({
  image,
  onApprove,
  onNext,
  onRetry,
}: {
  image: ImageDetailDto;
  onApprove: () => void;
  onNext: () => void;
  onRetry: () => void;
}) {
  const reviewed = image.lines.filter((l) => l.status !== "unreviewed").length;
  const failed = image.status === "failed";
  const [copiedAll, setCopiedAll] = useState(false);
  const [copyAllError, setCopyAllError] = useState<string | null>(null);

  async function copyAll() {
    try {
      await copyToClipboard(joinFinalText(image.lines));
      setCopyAllError(null);
      setCopiedAll(true);
      setTimeout(() => setCopiedAll(false), 1200);
    } catch (err) {
      setCopyAllError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <header className="toolbar">
      <strong className="toolbar-filename" title={image.filename}>
        {image.filename}
      </strong>
      {failed ? (
        <span className="toolbar-error">OCR failed: {image.error ?? "unknown error"}</span>
      ) : (
        <span className="muted">
          {reviewed}/{image.lines.length} lines touched · {image.status}
        </span>
      )}
      <div className="toolbar-actions">
        {failed && (
          <button className="toolbar-retry" onClick={onRetry}>
            Retry OCR
          </button>
        )}
        <button
          onClick={copyAll}
          disabled={image.lines.length === 0}
          title={copyAllError ?? undefined}
        >
          {copiedAll ? "Copied ✓" : copyAllError ? "Copy failed ⚠" : "Copy all text"}
        </button>
        <button onClick={onApprove} disabled={image.status === "approved"}>
          Approve image (A)
        </button>
        <button onClick={onNext}>Next (n)</button>
      </div>
      <span className="muted">j/k move · a approve line · Enter edit</span>
    </header>
  );
}
