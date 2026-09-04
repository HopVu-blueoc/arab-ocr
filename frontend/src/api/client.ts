import type { BatchDto, ImageDetailDto, ImageDto, LineDto, LineStatus } from "./types";

async function json<T>(input: string, init?: RequestInit): Promise<T> {
  const res = await fetch(input, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`${init?.method ?? "GET"} ${input} -> ${res.status}`);
  return (await res.json()) as T;
}

export const getBatches = () => json<BatchDto[]>("/api/batches");

export const createBatch = (name: string, sourceDir: string) =>
  json<BatchDto>("/api/batches", {
    method: "POST",
    body: JSON.stringify({ name, source_dir: sourceDir }),
  });

export const getBatchImages = (batchId: number) =>
  json<ImageDto[]>(`/api/batches/${batchId}/images`);

export const getImage = (imageId: number) => json<ImageDetailDto>(`/api/images/${imageId}`);

export const imageFileUrl = (imageId: number) => `/api/images/${imageId}/file`;

export const updateLine = (
  lineId: number,
  patch: { corrected_text?: string | null; status?: LineStatus },
) => json<LineDto>(`/api/lines/${lineId}`, { method: "PATCH", body: JSON.stringify(patch) });
