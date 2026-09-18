import type { UploadResultDto } from "./types";

const IMAGE_SUFFIXES = [".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"];

/** Ceiling on files per request, independent of size.
 *
 *  Bytes are the real constraint, but a request of 5,000 tiny thumbnails is
 *  still one failure that loses all of them, and gives the progress bar
 *  nothing to advance between. */
export const UPLOAD_CHUNK_SIZE = 20;

/** Bytes of multipart framing per part: boundary, Content-Disposition,
 *  Content-Type, CRLFs, plus room for a long filename. Deliberately generous -
 *  underestimating here is what produces a 413. */
export const PART_OVERHEAD_BYTES = 512;

export function chunk<T>(items: T[], size: number): T[][] {
  if (size < 1) throw new Error("chunk size must be at least 1");
  const groups: T[][] = [];
  for (let i = 0; i < items.length; i += size) groups.push(items.slice(i, i + size));
  return groups;
}

/** Pack files into requests that fit the proxy's whole-body limit.
 *
 *  Counting files instead of bytes is what caused the 413: 20 photos of 3MB
 *  each are individually well under the per-file cap and together three times
 *  the body limit.
 *
 *  A single file bigger than the budget still gets its own request rather than
 *  being dropped silently - the server rejects it with a reason the user can
 *  read.
 */
export function chunkByBytes(
  files: File[],
  maxRequestBytes: number,
  maxPerRequest: number = UPLOAD_CHUNK_SIZE,
): File[][] {
  if (maxRequestBytes < 1) throw new Error("byte budget must be at least 1");

  const groups: File[][] = [];
  let current: File[] = [];
  let used = 0;

  for (const file of files) {
    const cost = file.size + PART_OVERHEAD_BYTES;
    const wouldOverflow = current.length > 0 && used + cost > maxRequestBytes;
    if (wouldOverflow || current.length >= maxPerRequest) {
      groups.push(current);
      current = [];
      used = 0;
    }
    current.push(file);
    used += cost;
  }
  if (current.length > 0) groups.push(current);
  return groups;
}

/** A folder pick returns everything in the tree - .DS_Store, sidecar files,
 *  thumbnails. Filter client-side so the server is not asked about junk. */
export function imageFilesFrom(list: FileList | null): File[] {
  return Array.from(list ?? []).filter((file) =>
    IMAGE_SUFFIXES.some((suffix) => file.name.toLowerCase().endsWith(suffix)),
  );
}

export function summarizeUpload(result: UploadResultDto): string {
  const parts = [`${result.imported} uploaded`];
  if (result.skipped > 0) parts.push(`${result.skipped} skipped (already imported)`);
  if (result.failed.length > 0) {
    const detail = result.failed.map((f) => `${f.filename} (${f.reason})`).join(", ");
    parts.push(`${result.failed.length} rejected: ${detail}`);
  }
  return parts.join(" · ");
}

/** A 413 is the proxy refusing the body before the app ever sees it, so there
 *  is no per-file detail to report - say something the user can act on
 *  instead of surfacing a bare status code. */
export function uploadErrorMessage(status: number, batchId: number): string {
  if (status === 413) {
    return "upload rejected as too large - try fewer or smaller images per batch";
  }
  return `POST /api/batches/${batchId}/images -> ${status}`;
}

/** Upload one chunk. XMLHttpRequest rather than fetch(): fetch cannot report
 *  upload progress at all, and a folder of scene photos takes long enough
 *  that a bar is the difference between "working" and "frozen". */
export function uploadImages(
  batchId: number,
  files: File[],
  onProgress: (fraction: number) => void,
): Promise<UploadResultDto> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    for (const file of files) form.append("files", file, file.name);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/batches/${batchId}/images`);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText) as UploadResultDto);
      } else {
        reject(new Error(uploadErrorMessage(xhr.status, batchId)));
      }
    };
    xhr.onerror = () => reject(new Error("upload failed: network error"));
    xhr.send(form);
  });
}
