import { useCallback, useEffect, useState } from "react";
import "./styles.css";
import { ApiError, getBatchImages } from "./api/client";
import { anyImageInFlight } from "./api/activity";
import { shouldFetchNextPage } from "./api/paging";
import { pollErrorMessage } from "./api/poll";
import type { ImageDto } from "./api/types";
import { BatchList } from "./components/BatchList";
import { ImageStrip } from "./components/ImageStrip";
import { ReviewPage } from "./pages/ReviewPage";
import { usePagedPoll } from "./hooks/usePagedPoll";

const IMAGE_PAGE_SIZE = 100;
// Enough strip for a long review session without unbounded growth; cheaper
// than pulling in a virtualisation dependency.
const MAX_STRIP_ITEMS = 1000;

const imageKey = (img: ImageDto) => img.id;

export default function App() {
  const [batchId, setBatchId] = useState<number | null>(null);
  const [imageId, setImageId] = useState<number | null>(null);
  const [missingBatch, setMissingBatch] = useState(false);

  const fetchPage = useCallback(
    (cursor: string | null) =>
      getBatchImages(batchId as number, { limit: IMAGE_PAGE_SIZE, cursor }),
    [batchId],
  );

  const onPollError = useCallback((error: unknown) => {
    // The batch was deleted elsewhere - retrying forever would never recover.
    if (error instanceof ApiError && error.status === 404) setMissingBatch(true);
  }, []);

  const {
    items: images,
    hasMore,
    loadMore,
    failures,
  } = usePagedPoll<ImageDto>({
    fetchPage,
    itemKey: imageKey,
    hasWork: anyImageInFlight,
    enabled: batchId !== null && !missingBatch,
    resetKey: batchId,
    maxItems: MAX_STRIP_ITEMS,
    onError: onPollError,
  });

  // Fall back to the first image rather than storing a redundant selection:
  // the strip highlights this and ReviewPage renders it.
  const activeId = imageId ?? images[0]?.id ?? null;
  const activeIndex = images.findIndex((img) => img.id === activeId);

  // Pull the next page before the reviewer reaches the end of the loaded
  // strip, or "next image" would silently stop at the page boundary.
  useEffect(() => {
    if (shouldFetchNextPage(activeIndex, images.length, hasMore)) void loadMore();
  }, [activeIndex, images.length, hasMore, loadMore]);

  const nextImage = () => {
    const next = images[activeIndex + 1];
    if (next) setImageId(next.id);
  };

  const pollError = missingBatch
    ? "This batch no longer exists."
    : pollErrorMessage(failures);

  return (
    <div className="app-shell">
      <BatchList
        activeId={batchId}
        onPick={(id) => {
          setBatchId(id);
          setImageId(null);
          setMissingBatch(false);
        }}
        onDeleted={(id) => {
          // The deleted batch was the one open in the review pane - there is
          // nothing left to show, so return to the empty state rather than
          // keep polling a batch that no longer exists.
          if (id === batchId) {
            setBatchId(null);
            setImageId(null);
            setMissingBatch(false);
          }
        }}
      />
      <main className="app-main">
        {pollError && <p className="poll-error">{pollError}</p>}
        <ImageStrip
          images={images}
          activeId={activeId}
          onPick={setImageId}
          hasMore={hasMore}
          onLoadMore={loadMore}
        />
        {activeId === null ? (
          <p className="empty">Pick a batch, then an image.</p>
        ) : (
          <ReviewPage imageId={activeId} onNextImage={nextImage} />
        )}
      </main>
    </div>
  );
}
