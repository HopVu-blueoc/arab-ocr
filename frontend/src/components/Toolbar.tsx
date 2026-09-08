import type { ImageDetailDto } from "../api/types";

export function Toolbar({
  image,
  onApprove,
  onNext,
}: {
  image: ImageDetailDto;
  onApprove: () => void;
  onNext: () => void;
}) {
  const reviewed = image.lines.filter((l) => l.status !== "unreviewed").length;
  return (
    <header className="toolbar">
      <strong className="toolbar-filename" title={image.filename}>
        {image.filename}
      </strong>
      <span className="muted">
        {reviewed}/{image.lines.length} lines touched · {image.status}
      </span>
      <div className="toolbar-actions">
        <button onClick={onApprove} disabled={image.status === "approved"}>
          Approve image (A)
        </button>
        <button onClick={onNext}>Next (n)</button>
      </div>
      <span className="muted">j/k move · a approve line · Enter edit</span>
    </header>
  );
}
