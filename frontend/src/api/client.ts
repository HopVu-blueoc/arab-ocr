import type {
  BatchDto,
  ImageDetailDto,
  ImageDto,
  ImageStatus,
  LineDto,
  LineStatus,
} from "./types";

async function json<T>(input: string, init?: RequestInit): Promise<T> {
  const res = await fetch(input, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`${init?.method ?? "GET"} ${input} -> ${res.status}`);
  return (await res.json()) as T;
}

export const getBatches = () => json<BatchDto[]>("/api/batches");

export const deleteBatch = async (batchId: number): Promise<void> => {
  const res = await fetch(`/api/batches/${batchId}`, { method: "DELETE" });
  // 204 No Content has no body - json<T>() would throw trying to parse it.
  if (!res.ok) throw new Error(`DELETE /api/batches/${batchId} -> ${res.status}`);
};

export const createBatch = (name: string) =>
  json<BatchDto>("/api/batches", { method: "POST", body: JSON.stringify({ name }) });

export const getBatchImages = (batchId: number) =>
  json<ImageDto[]>(`/api/batches/${batchId}/images`);

export const getImage = (imageId: number) => json<ImageDetailDto>(`/api/images/${imageId}`);

export const imageFileUrl = (imageId: number) => `/api/images/${imageId}/file`;

export const updateLine = (
  lineId: number,
  patch: { corrected_text?: string | null; status?: LineStatus },
) => json<LineDto>(`/api/lines/${lineId}`, { method: "PATCH", body: JSON.stringify(patch) });

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
