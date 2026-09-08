import { describe, expect, it } from "vitest";
import { chunk, imageFilesFrom, summarizeUpload } from "./uploads";

const named = (...names: string[]) => names.map((name) => ({ name })) as unknown as FileList;

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
