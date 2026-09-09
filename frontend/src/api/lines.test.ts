import { expect, it } from "vitest";
import { joinFinalText } from "./lines";
import type { LineDto } from "./types";

const line = (overrides: Partial<LineDto>): LineDto => ({
  id: 1,
  reading_order: 0,
  rec_text: "",
  corrected_text: null,
  final_text: "",
  score: 1,
  polygon: [],
  status: "unreviewed",
  ...overrides,
});

it("joins each line's final text, in order, one per line", () => {
  const lines = [
    line({ id: 1, reading_order: 0, final_text: "مرحبا" }),
    line({ id: 2, reading_order: 1, final_text: "بالعالم" }),
  ];
  expect(joinFinalText(lines)).toBe("مرحبا\nبالعالم");
});

it("skips lines with empty final text rather than leaving blank lines", () => {
  const lines = [
    line({ id: 1, final_text: "أول" }),
    line({ id: 2, final_text: "" }),
    line({ id: 3, final_text: "  " }),
    line({ id: 4, final_text: "ثاني" }),
  ];
  expect(joinFinalText(lines)).toBe("أول\nثاني");
});

it("returns an empty string for no lines", () => {
  expect(joinFinalText([])).toBe("");
});
