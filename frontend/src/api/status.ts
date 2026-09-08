import type { ImageStatus } from "./types";

const ACTIVE: ImageStatus[] = ["pending", "queued", "running"];

export const REVIEW_POLL_MS = 1500;

/** True while OCR may still change this image.
 *
 * Polling MUST stop at a terminal status: a poll response replaces
 * image.lines wholesale, and once lines exist the reviewer is editing them -
 * a late refresh would silently discard an in-flight correction.
 */
export const isProcessing = (status: ImageStatus | undefined): boolean =>
  status !== undefined && ACTIVE.includes(status);
