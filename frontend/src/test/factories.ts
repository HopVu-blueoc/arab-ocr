import type { BatchDto, ImageDto } from "../api/types";

export const batch = (overrides: Partial<BatchDto> = {}): BatchDto => ({
  id: 1,
  name: "batch",
  source_dir: "batch-1",
  created_at: "2026-01-01T00:00:00",
  image_count: 0,
  done_count: 0,
  approved_count: 0,
  failed_count: 0,
  ...overrides,
});

export const image = (overrides: Partial<ImageDto> = {}): ImageDto => ({
  id: 1,
  batch_id: 1,
  filename: "a.png",
  width: 100,
  height: 100,
  status: "done",
  error: null,
  ...overrides,
});
