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

export interface LimitsDto {
  max_file_bytes: number;
  max_request_bytes: number;
}

export const getLimits = () => json<LimitsDto>("/api/limits");

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

const JOB_POLL_MS = 500;
const JOB_POLL_MAX_ATTEMPTS = 120; // 60s - well past any single region's OCR time

/** Poll GET /api/jobs/{id} until it leaves the pending state.
 *
 * 202 with {"status": "pending"} means "still running"; anything else is the
 * task's real outcome, success or the mapped failure, exactly as if the
 * original POST had answered synchronously.
 */
async function pollJob<T>(jobId: string): Promise<T> {
  for (let attempt = 0; attempt < JOB_POLL_MAX_ATTEMPTS; attempt++) {
    const res = await fetch(`/api/jobs/${jobId}`);
    if (res.status !== 202) {
      if (!res.ok) {
        throw new ApiError(res.status, `GET /api/jobs/${jobId}`, detailFromBody(await res.text()));
      }
      return (await res.json()) as T;
    }
    await new Promise((resolve) => setTimeout(resolve, JOB_POLL_MS));
  }
  throw new ApiError(504, `GET /api/jobs/${jobId}`, "timed out waiting for region detection");
}

/** Under JOB_BACKEND=inline the server still answers 200 directly (small
 *  deployments, and every test in this repo); under celery it answers 202
 *  with a job id to poll. Region OCR moved off the API process (see
 *  app/routers/images.py:detect_box) - it has no GPU in the deployed
 *  topology, so this may now take a real round trip instead of returning
 *  inline. */
export async function detectBox(imageId: number, polygon: number[][]): Promise<LineDto[]> {
  const res = await fetch(`/api/images/${imageId}/lines/detect-box`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ polygon }),
  });
  if (res.status === 202) {
    const { job_id } = (await res.json()) as { job_id: string };
    return pollJob<LineDto[]>(job_id);
  }
  if (!res.ok) {
    throw new ApiError(
      res.status,
      `POST /api/images/${imageId}/lines/detect-box`,
      detailFromBody(await res.text()),
    );
  }
  return (await res.json()) as LineDto[];
}

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
