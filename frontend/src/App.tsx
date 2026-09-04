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

  return (
    <div className="app-shell">
      <BatchList
        onPick={(id) => {
          setBatchId(id);
          setImageId(null);
        }}
      />
      <main className="app-main">
        <ImageStrip images={images} activeId={imageId} onPick={setImageId} />
        {imageId === null ? (
          <p className="empty">Pick a batch, then an image.</p>
        ) : (
          <ReviewPage imageId={imageId} />
        )}
      </main>
    </div>
  );
}
