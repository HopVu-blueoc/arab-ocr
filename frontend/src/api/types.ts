export type ImageStatus = "pending" | "queued" | "running" | "done" | "failed" | "approved";
export type LineStatus = "unreviewed" | "approved" | "edited";

export interface LineDto {
  id: number;
  reading_order: number;
  rec_text: string;
  corrected_text: string | null;
  final_text: string;
  score: number;
  polygon: number[][];
  status: LineStatus;
}

export interface ImageDto {
  id: number;
  batch_id: number;
  filename: string;
  width: number;
  height: number;
  status: ImageStatus;
  error: string | null;
}

export interface ImageDetailDto extends ImageDto {
  lines: LineDto[];
}

export interface BatchDto {
  id: number;
  name: string;
  source_dir: string;
  created_at: string;
  image_count: number;
  done_count: number;
  approved_count: number;
  failed_count: number;
}

export type UploadFailureDto = { filename: string; reason: string };

export type UploadResultDto = {
  imported: number;
  skipped: number;
  failed: UploadFailureDto[];
};
