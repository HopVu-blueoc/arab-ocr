import { describe, expect, it } from "vitest";
import {
  PART_OVERHEAD_BYTES,
  UPLOAD_CHUNK_SIZE,
  chunk,
  chunkByBytes,
  imageFilesFrom,
  summarizeUpload,
  uploadErrorMessage,
} from "./uploads";

const named = (...names: string[]) => names.map((name) => ({ name })) as unknown as FileList;

const MB = 1024 * 1024;
const sized = (...sizes: number[]) =>
  sizes.map((size, i) => ({ name: `f${i}.png`, size })) as unknown as File[];

describe("chunk", () => {
  it("splits into groups of at most size", () => {
    expect(chunk([1, 2, 3, 4, 5], 2)).toEqual([[1, 2], [3, 4], [5]]);
  });

  it("returns nothing for an empty list", () => {
    expect(chunk([], 20)).toEqual([]);
  });

  it("rejects a size below one rather than looping forever", () => {
    expect(() => chunk([1], 0)).toThrow(/at least 1/);
  });
});

describe("chunkByBytes", () => {
  it("keeps the regression case under the limit", () => {
    // The original bug: 20 photos of 3MB each, individually legal, packed into
    // one request by count and rejected as a 32MB-over body.
    const groups = chunkByBytes(sized(...Array(20).fill(3 * MB)), 32 * MB);
    for (const group of groups) {
      const bytes = group.reduce((sum, f) => sum + f.size + PART_OVERHEAD_BYTES, 0);
      expect(bytes).toBeLessThanOrEqual(32 * MB);
    }
    expect(groups.flat()).toHaveLength(20);
  });

  it("never drops or duplicates a file", () => {
    const files = sized(1 * MB, 5 * MB, 2 * MB, 9 * MB, 1 * MB);
    const flat = chunkByBytes(files, 10 * MB).flat();
    expect(flat).toEqual(files);
  });

  it("gives an oversize file its own request rather than dropping it", () => {
    // The server rejects it with a readable reason; silently discarding it
    // would leave the user wondering where the file went.
    const groups = chunkByBytes(sized(1 * MB, 50 * MB, 1 * MB), 10 * MB);
    expect(groups.some((g) => g.length === 1 && g[0].size === 50 * MB)).toBe(true);
    expect(groups.flat()).toHaveLength(3);
  });

  it("accounts for multipart overhead, not just file bytes", () => {
    // Two files that sum to exactly the budget still need framing bytes.
    const groups = chunkByBytes(sized(5 * MB, 5 * MB), 10 * MB);
    expect(groups).toHaveLength(2);
  });

  it("still caps the number of files per request", () => {
    const groups = chunkByBytes(sized(...Array(50).fill(1)), 100 * MB);
    expect(Math.max(...groups.map((g) => g.length))).toBeLessThanOrEqual(UPLOAD_CHUNK_SIZE);
    expect(groups.flat()).toHaveLength(50);
  });

  it("returns nothing for an empty list", () => {
    expect(chunkByBytes([], 10 * MB)).toEqual([]);
  });

  it("rejects a budget below one rather than looping forever", () => {
    expect(() => chunkByBytes(sized(1), 0)).toThrow(/at least 1/);
  });
});

describe("uploadErrorMessage", () => {
  it("explains a 413 instead of showing a bare status", () => {
    expect(uploadErrorMessage(413, 1)).toMatch(/too large/);
  });

  it("falls back to the status for anything else", () => {
    expect(uploadErrorMessage(500, 7)).toBe("POST /api/batches/7/images -> 500");
  });
});

describe("imageFilesFrom", () => {
  it("keeps images and drops everything else", () => {
    const files = imageFilesFrom(named("a.png", "b.JPG", ".DS_Store", "notes.pdf", "c.webp"));
    expect(files.map((f) => f.name)).toEqual(["a.png", "b.JPG", "c.webp"]);
  });

  it("handles a null list", () => {
    expect(imageFilesFrom(null)).toEqual([]);
  });
});

describe("summarizeUpload", () => {
  it("reports the happy path", () => {
    expect(summarizeUpload({ imported: 3, skipped: 0, failed: [] })).toBe("3 uploaded");
  });

  it("explains why duplicates were skipped", () => {
    expect(summarizeUpload({ imported: 1, skipped: 2, failed: [] })).toBe(
      "1 uploaded · 2 skipped (already imported)",
    );
  });

  it("names every rejected file and its reason", () => {
    const text = summarizeUpload({
      imported: 0,
      skipped: 0,
      failed: [{ filename: "x.png", reason: "not a readable image" }],
    });
    expect(text).toBe("0 uploaded · 1 rejected: x.png (not a readable image)");
  });
});
