import { describe, expect, it } from "vitest";
import { anyBatchInFlight, anyImageInFlight, batchInFlight } from "./activity";
import { batch, image } from "../test/factories";

describe("batchInFlight", () => {
  it("is false when every image reached a terminal status", () => {
    expect(batchInFlight(batch({ image_count: 3, done_count: 2, failed_count: 1 }))).toBe(false);
  });

  it("is true while images are unaccounted for", () => {
    expect(batchInFlight(batch({ image_count: 3, done_count: 1 }))).toBe(true);
  });

  it("treats an empty batch as idle, not busy", () => {
    expect(batchInFlight(batch({ image_count: 0 }))).toBe(false);
  });

  it("counts approved images as finished", () => {
    expect(batchInFlight(batch({ image_count: 2, done_count: 1, approved_count: 1 }))).toBe(false);
  });

  it("does not report work when counts overshoot the total", () => {
    // Counts come from a separate aggregate; a racing poll can read them
    // mid-transition. Overshoot must not read as negative work.
    expect(batchInFlight(batch({ image_count: 1, done_count: 2 }))).toBe(false);
  });
});

describe("anyBatchInFlight", () => {
  it("is false for an empty page", () => {
    expect(anyBatchInFlight([])).toBe(false);
  });

  it("is true when any single batch is working", () => {
    expect(
      anyBatchInFlight([
        batch({ id: 1, image_count: 1, done_count: 1 }),
        batch({ id: 2, image_count: 1 }),
      ]),
    ).toBe(true);
  });
});

describe("anyImageInFlight", () => {
  it("is true for pending, queued or running", () => {
    expect(anyImageInFlight([image({ status: "queued" })])).toBe(true);
    expect(anyImageInFlight([image({ status: "running" })])).toBe(true);
    expect(anyImageInFlight([image({ status: "pending" })])).toBe(true);
  });

  it("is false once everything is terminal", () => {
    expect(
      anyImageInFlight([
        image({ id: 1, status: "done" }),
        image({ id: 2, status: "approved" }),
        image({ id: 3, status: "failed" }),
      ]),
    ).toBe(false);
  });
});
