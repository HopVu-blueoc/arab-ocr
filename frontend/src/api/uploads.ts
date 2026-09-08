import type { UploadResultDto } from "./types";

const IMAGE_SUFFIXES = [".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"];

/** Files per request. Keeps one failed request from losing a whole folder,
 *  and gives the progress bar something to advance between. */
export const UPLOAD_CHUNK_SIZE = 20;

export function chunk<T>(items: T[], size: number): T[][] {
  if (size < 1) throw new Error("chunk size must be at least 1");
  const groups: T[][] = [];
  for (let i = 0; i < items.length; i += size) groups.push(items.slice(i, i + size));
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
        reject(new Error(`POST /api/batches/${batchId}/images -> ${xhr.status}`));
      }
    };
    xhr.onerror = () => reject(new Error("upload failed: network error"));
    xhr.send(form);
  });
}
