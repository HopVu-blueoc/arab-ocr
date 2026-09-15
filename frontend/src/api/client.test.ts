import { describe, expect, it } from "vitest";
import { ApiError, detailFromBody } from "./client";

describe("detailFromBody", () => {
  it("pulls FastAPI's detail out, instead of discarding the body", () => {
    expect(detailFromBody(JSON.stringify({ detail: "batch not found" }))).toBe("batch not found");
  });

  it("returns null for a proxy's HTML error page", () => {
    expect(detailFromBody("<html><body>502 Bad Gateway</body></html>")).toBeNull();
  });

  it("returns null for an empty body", () => {
    expect(detailFromBody("")).toBeNull();
  });

  it("returns null when detail is not a string", () => {
    // FastAPI validation errors put a list here; it is not a user-facing message.
    expect(detailFromBody(JSON.stringify({ detail: [{ loc: ["query", "limit"] }] }))).toBeNull();
  });
});

describe("ApiError", () => {
  it("keeps the status so callers can branch on it", () => {
    const err = new ApiError(404, "GET /api/batches/1/images", "batch not found");
    expect(err.status).toBe(404);
    expect(err.message).toContain("batch not found");
    expect(err instanceof Error).toBe(true);
  });

  it("reads sensibly with no detail", () => {
    expect(new ApiError(503, "GET /api/batches", null).message).toBe("GET /api/batches -> 503");
  });
});
