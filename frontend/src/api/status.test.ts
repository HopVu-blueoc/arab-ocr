import { expect, it } from "vitest";
import { isProcessing } from "./status";

it("polls only while the image has not reached a terminal status", () => {
  expect(isProcessing("pending")).toBe(true);
  expect(isProcessing("queued")).toBe(true);
  expect(isProcessing("running")).toBe(true);
  expect(isProcessing("done")).toBe(false);
  expect(isProcessing("failed")).toBe(false);
  expect(isProcessing("approved")).toBe(false);
  expect(isProcessing(undefined)).toBe(false);
});
