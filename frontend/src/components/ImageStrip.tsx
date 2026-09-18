import { useEffect, useRef } from "react";
import type { ImageDto } from "../api/types";

export function ImageStrip({
  images,
  activeId,
  onPick,
  hasMore = false,
  onLoadMore,
}: {
  images: ImageDto[];
  activeId: number | null;
  onPick: (id: number) => void;
  hasMore?: boolean;
  onLoadMore?: () => void;
}) {
  const sentinel = useRef<HTMLDivElement | null>(null);

  // Scrolling to the end of the strip pulls the next page. Keyboard-driven
  // review is handled separately, by prefetching ahead of the active image.
  useEffect(() => {
    const node = sentinel.current;
    if (!node || !hasMore || !onLoadMore) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) onLoadMore();
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [hasMore, onLoadMore, images.length]);

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
      {hasMore && <div ref={sentinel} className="strip-sentinel" aria-hidden="true" />}
    </div>
  );
}
