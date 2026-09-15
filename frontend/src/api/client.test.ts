import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, detailFromBody, detectBox } from "./client";

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

const jsonResponse = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

describe("detectBox", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns the lines directly on a synchronous 200 (JOB_BACKEND=inline)", async () => {
    const lines = [{ id: 1, rec_text: "يدوي" }];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, lines)));

    await expect(detectBox(1, [[0, 0]])).resolves.toEqual(lines);
  });

  it("polls the job endpoint through pending states to the final result (JOB_BACKEND=celery)", async () => {
    const lines = [{ id: 2, rec_text: "test" }];
    const fetchMock = vi
      .fn()
      // the POST that dispatches the job
      .mockResolvedValueOnce(jsonResponse(202, { job_id: "job-1" }))
      // two pending polls, then the real result
      .mockResolvedValueOnce(jsonResponse(202, { status: "pending" }))
      .mockResolvedValueOnce(jsonResponse(202, { status: "pending" }))
      .mockResolvedValueOnce(jsonResponse(200, lines));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("setTimeout", (fn: () => void) => fn()); // no real waiting in tests

    await expect(detectBox(1, [[0, 0]])).resolves.toEqual(lines);
    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(fetchMock.mock.calls[1][0]).toBe("/api/jobs/job-1");
  });

  it("surfaces the job's mapped failure (e.g. a 422 no-match) instead of hanging", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(202, { job_id: "job-2" }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: "no text found in that region" }), {
          status: 422,
          headers: { "content-type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("setTimeout", (fn: () => void) => fn());

    await expect(detectBox(1, [[0, 0]])).rejects.toMatchObject({
      status: 422,
      detail: "no text found in that region",
    });
  });

  it("surfaces a non-202/200 response from the initial POST directly", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "image not found" }), {
          status: 404,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    await expect(detectBox(1, [[0, 0]])).rejects.toMatchObject({ status: 404 });
  });
});
