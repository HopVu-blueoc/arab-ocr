import type {
  BatchDto,
  ImageDetailDto,
  ImageDto,
  ImageStatus,
  LineDto,
  LineStatus,
  PageDto,
} from "./types";

/** An HTTP failure that kept the status, so callers can branch on it.
 *
 * A poll needs to tell "the batch was deleted" (404: stop) from "the server
 * is struggling" (503: back off and retry).
 */
export class ApiError extends Error {
  status: number;
  url: string;
  detail: string | null;

  constructor(status: number, url: string, detail: string | null) {
    super(detail ? `${url} -> ${status}: ${detail}` : `${url} -> ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.url = url;
    this.detail = detail;
  }
}

/** Pull FastAPI's {"detail": ...} out of an error body, if it is there.
 *
 * Pure and body-in so it is testable without a fetch stub or a DOM.
 */
export function detailFromBody(body: string): string | null {
  try {
    const parsed = JSON.parse(body);
    if (parsed && typeof parsed.detail === "string") return parsed.detail;
  } catch {
    // An HTML error page from a proxy - nothing useful to show the user.
  }
  return null;
}

async function json<T>(input: string, init?: RequestInit): Promise<T> {
  const res = await fetch(input, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    throw new ApiError(res.status, `${init?.method ?? "GET"} ${input}`, detailFromBody(await res.text()));
  }
  return (await res.json()) as T;
}

const pageQuery = (limit?: number, cursor?: string | null) => {
  const params = new URLSearchParams();
  if (limit !== undefined) params.set("limit", String(limit));
  if (cursor) params.set("cursor", cursor);
  const query = params.toString();
  return query ? `?${query}` : "";
};

export const getBatches = (opts: { limit?: number; cursor?: string | null } = {}) =>
  json<PageDto<BatchDto>>(`/api/batches${pageQuery(opts.limit, opts.cursor)}`);

export const deleteBatch = async (batchId: number): Promise<void> => {
  const res = await fetch(`/api/batches/${batchId}`, { method: "DELETE" });
  // 204 No Content has no body - json<T>() would throw trying to parse it.
  if (!res.ok) {
    throw new ApiError(res.status, `DELETE /api/batches/${batchId}`, null);
  }
};

export const createBatch = (name: string) =>
  json<BatchDto>("/api/batches", { method: "POST", body: JSON.stringify({ name }) });

export const getBatchImages = (
  batchId: number,
  opts: { limit?: number; cursor?: string | null } = {},
) =>
  json<PageDto<ImageDto>>(
    `/api/batches/${batchId}/images${pageQuery(opts.limit, opts.cursor)}`,
  );

export const getImage = (imageId: number) => json<ImageDetailDto>(`/api/images/${imageId}`);

export const imageFileUrl = (imageId: number) => `/api/images/${imageId}/file`;

export const updateLine = (
  lineId: number,
  patch: { corrected_text?: string | null; status?: LineStatus },
) => json<LineDto>(`/api/lines/${lineId}`, { method: "PATCH", body: JSON.stringify(patch) });

export const detectBox = (imageId: number, polygon: number[][]) =>
  json<LineDto[]>(`/api/images/${imageId}/lines/detect-box`, {
    method: "POST",
    body: JSON.stringify({ polygon }),
  });

export const deleteLine = (lineId: number) =>
  json<LineDto[]>(`/api/lines/${lineId}`, { method: "DELETE" });

export const updateImageStatus = (imageId: number, status: ImageStatus) =>
  json<ImageDto>(`/api/images/${imageId}`, { method: "PATCH", body: JSON.stringify({ status }) });

export const retryImage = (imageId: number) =>
  json<ImageDto>(`/api/images/${imageId}/retry`, { method: "POST" });

export const exportBatch = (batchId: number, format: "jsonl" | "txt") =>
  json<{ path: string; count: number }>(`/api/batches/${batchId}/export`, {
    method: "POST",
    body: JSON.stringify({ format }),
  });

export { uploadImages } from "./uploads";
