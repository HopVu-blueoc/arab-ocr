import { isProcessing } from "./status";
import type { BatchDto, ImageDto } from "./types";

/** True while this batch still has images OCR hasn't finished with.
 *
 * Derived by subtraction rather than by asking for the in-flight count: the
 * API reports totals per terminal status, and anything unaccounted for is
 * still pending, queued or running.
 */
export const batchInFlight = (b: BatchDto): boolean =>
  b.image_count - b.done_count - b.approved_count - b.failed_count > 0;

/** Whether the loaded page of batches contains work in progress.
 *
 * Loaded pages only. A busy batch the user has not paged to will not hold the
 * sidebar at the fast interval - which is exactly why the idle state slows the
 * poll rather than stopping it.
 */
export const anyBatchInFlight = (items: BatchDto[]): boolean => items.some(batchInFlight);

export const anyImageInFlight = (items: ImageDto[]): boolean =>
  items.some((i) => isProcessing(i.status));
