import { useEffect, useState } from "react";
import { getImage, updateImageStatus, updateLine } from "../api/client";
import { REVIEW_POLL_MS, isProcessing } from "../api/status";
import type { ImageDetailDto, LineDto } from "../api/types";
import { ImageCanvas } from "../components/ImageCanvas";
import { LineCrop } from "../components/LineCrop";
import { LineList } from "../components/LineList";
import { Toolbar } from "../components/Toolbar";
import { useReviewKeys } from "../hooks/useReviewKeys";
import { useSelection } from "../store/selection";

export function ReviewPage({
  imageId,
  onNextImage,
}: {
  imageId: number;
  onNextImage: () => void;
}) {
  const [image, setImage] = useState<ImageDetailDto | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { selectedId, select } = useSelection();

  useEffect(() => {
    setImage(null);
    setError(null);
    select(null);
    getImage(imageId)
      .then(setImage)
      .catch((e: Error) => setError(e.message));
  }, [imageId, select]);

  // OCR is often still queued or running when the reviewer clicks an image,
  // and the fetch above would then be the only one - which is why the old UI
  // needed an F5. Re-fetch until the status is terminal, then stop.
  useEffect(() => {
    if (!isProcessing(image?.status)) return;
    const timer = setInterval(() => {
      getImage(imageId)
        .then(setImage)
        .catch(() => {}); // a transient failure just means the next tick retries
    }, REVIEW_POLL_MS);
    return () => clearInterval(timer);
  }, [imageId, image?.status]);

  const replaceLine = (updated: LineDto) =>
    setImage((prev) =>
      prev === null
        ? prev
        : { ...prev, lines: prev.lines.map((l) => (l.id === updated.id ? updated : l)) },
    );

  async function approveImage() {
    if (image === null) return;
    const updated = await updateImageStatus(image.id, "approved");
    setImage((prev) => (prev === null ? prev : { ...prev, status: updated.status }));
    onNextImage();
  }

  useReviewKeys({
    lines: image?.lines ?? [],
    selectedId,
    select,
    onApproveLine: async (lineId) => replaceLine(await updateLine(lineId, { status: "approved" })),
    onApproveImage: approveImage,
    onNextImage,
  });

  if (error) return <p className="error">{error}</p>;
  if (!image) return <p className="empty">Loading…</p>;

  return (
    <div className="review-shell">
      <p className="processing-note" hidden={!isProcessing(image.status)}>
        OCR running — this view updates itself.
      </p>
      <Toolbar image={image} onApprove={approveImage} onNext={onNextImage} />
      <div className="review-split">
        <section className="pane pane-image">
          <ImageCanvas image={image} />
        </section>
        <section className="pane pane-text">
          <LineCrop image={image} line={image.lines.find((l) => l.id === selectedId) ?? null} />
          <LineList lines={image.lines} onChange={replaceLine} />
        </section>
      </div>
    </div>
  );
}
