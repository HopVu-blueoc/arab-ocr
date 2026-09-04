import { describe, expect, it } from "vitest";
import { polygonToPoints } from "./polygon";

describe("polygonToPoints", () => {
  it("formats an SVG points attribute in image pixel space", () => {
    expect(
      polygonToPoints([
        [60, 40],
        [600, 40],
        [600, 100],
        [60, 100],
      ]),
    ).toBe("60,40 600,40 600,100 60,100");
  });

  it("handles float coordinates", () => {
    expect(polygonToPoints([[1.5, 2.25]])).toBe("1.5,2.25");
  });
});
