import { useEffect, useState } from "react";
import { getImage } from "../api/client";
import type { ImageDetailDto, LineDto } from "../api/types";
import { ImageCanvas } from "../components/ImageCanvas";
import { LineList } from "../components/LineList";

export function ReviewPage({ imageId }: { imageId: number }) {
  const [image, setImage] = useState<ImageDetailDto | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setImage(null);
    getImage(imageId)
      .then(setImage)
      .catch((e: Error) => setError(e.message));
  }, [imageId]);

  const replaceLine = (updated: LineDto) =>
    setImage((prev) =>
      prev === null
        ? prev
        : { ...prev, lines: prev.lines.map((l) => (l.id === updated.id ? updated : l)) },
    );

  if (error) return <p className="error">{error}</p>;
  if (!image) return <p className="empty">Loading…</p>;

  return (
    <div className="review-split">
      <section className="pane pane-image">
        <ImageCanvas image={image} />
      </section>
      <section className="pane pane-text">
        <h2>{image.filename}</h2>
        <LineList lines={image.lines} onChange={replaceLine} />
      </section>
    </div>
  );
}
