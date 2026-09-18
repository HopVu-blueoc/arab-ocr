import { describe, expect, it } from "vitest";
import { ACTIVE_POLL_MS, IDLE_POLL_MS, MAX_BACKOFF_MS, nextPollDelay, pollErrorMessage } from "./poll";

describe("nextPollDelay", () => {
  it("does not poll a hidden tab at all", () => {
    expect(nextPollDelay({ hasWork: true, visible: false })).toBeNull();
  });

  it("polls fast while OCR is running", () => {
    expect(nextPollDelay({ hasWork: true, visible: true })).toBe(ACTIVE_POLL_MS);
  });

  it("backs off but keeps polling when idle", () => {
    // Not null: there is no push channel, so a batch created elsewhere would
    // never appear if an idle list stopped polling entirely.
    expect(nextPollDelay({ hasWork: false, visible: true })).toBe(IDLE_POLL_MS);
  });

  it("backs off exponentially as failures repeat", () => {
    const one = nextPollDelay({ hasWork: true, visible: true, consecutiveFailures: 1 })!;
    const two = nextPollDelay({ hasWork: true, visible: true, consecutiveFailures: 2 })!;
    expect(one).toBe(ACTIVE_POLL_MS * 2);
    expect(two).toBeGreaterThan(one);
  });

  it("never backs off past the ceiling", () => {
    // The active base only doubles to 48s, so the ceiling binds on the idle
    // path - what matters is that neither can grow without bound.
    expect(
      nextPollDelay({ hasWork: true, visible: true, consecutiveFailures: 99 })!,
    ).toBeLessThanOrEqual(MAX_BACKOFF_MS);
    expect(nextPollDelay({ hasWork: false, visible: true, consecutiveFailures: 99 })).toBe(
      MAX_BACKOFF_MS,
    );
  });

  it("still does not poll a hidden tab that is also failing", () => {
    expect(nextPollDelay({ hasWork: true, visible: false, consecutiveFailures: 5 })).toBeNull();
  });
});

describe("pollErrorMessage", () => {
  it("stays quiet about a single dropped request", () => {
    expect(pollErrorMessage(0)).toBeNull();
    expect(pollErrorMessage(1)).toBeNull();
  });

  it("speaks up once failures persist", () => {
    expect(pollErrorMessage(2)).toBeTypeOf("string");
  });
});
