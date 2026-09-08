import type { ImageDto } from "../api/types";

export function ImageStrip({
  images,
  activeId,
  onPick,
}: {
  images: ImageDto[];
  activeId: number | null;
  onPick: (id: number) => void;
}) {
  return (
    <div className="image-strip">
      {images.map((img) => (
        <button
          key={img.id}
          className={`strip-item status-${img.status} ${img.id === activeId ? "strip-active" : ""}`}
          onClick={() => onPick(img.id)}
          title={img.error ? `${img.filename} — ${img.error}` : `${img.filename} (${img.status})`}
        >
          <span className="strip-item-name">{img.filename}</span>
        </button>
      ))}
    </div>
  );
}
