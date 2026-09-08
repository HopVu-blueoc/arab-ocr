import { useEffect, useState } from "react";
import "./styles.css";
import { getBatchImages } from "./api/client";
import type { ImageDto } from "./api/types";
import { BatchList } from "./components/BatchList";
import { ImageStrip } from "./components/ImageStrip";
import { ReviewPage } from "./pages/ReviewPage";

export default function App() {
  const [batchId, setBatchId] = useState<number | null>(null);
  const [images, setImages] = useState<ImageDto[]>([]);
  const [imageId, setImageId] = useState<number | null>(null);

  useEffect(() => {
    if (batchId === null) return;
    const load = () =>
      getBatchImages(batchId).then((rows) => {
        setImages(rows);
        setImageId((current) => current ?? rows[0]?.id ?? null);
      });
    load();
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [batchId]);

  const nextImage = () => {
    const index = images.findIndex((img) => img.id === imageId);
    const next = images[index + 1];
    if (next) setImageId(next.id);
  };

  return (
    <div className="app-shell">
      <BatchList
        activeId={batchId}
        onPick={(id) => {
          setBatchId(id);
          setImageId(null);
        }}
        onDeleted={(id) => {
          // The deleted batch was the one open in the review pane - there is
          // nothing left to show, so return to the empty state rather than
          // keep polling a batch that no longer exists.
          if (id === batchId) {
            setBatchId(null);
            setImages([]);
            setImageId(null);
          }
        }}
      />
      <main className="app-main">
        <ImageStrip images={images} activeId={imageId} onPick={setImageId} />
        {imageId === null ? (
          <p className="empty">Pick a batch, then an image.</p>
        ) : (
          <ReviewPage imageId={imageId} onNextImage={nextImage} />
        )}
      </main>
    </div>
  );
}
