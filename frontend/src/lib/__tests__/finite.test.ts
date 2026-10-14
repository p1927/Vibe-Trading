import { describe, expect, it } from "vitest";

import { finiteOrNull, lastFinite } from "../finite";

describe("finiteOrNull", () => {
  it("keeps real numbers, including zero and negatives", () => {
    expect(finiteOrNull(0)).toBe(0);
    expect(finiteOrNull(-12.5)).toBe(-12.5);
    expect(finiteOrNull("42")).toBe(42);
  });

  it("maps missing and non-finite readings to null, never to zero", () => {
    for (const bad of [null, undefined, "", "abc", NaN, Infinity, -Infinity]) {
      expect(finiteOrNull(bad)).toBeNull();
    }
  });
});

describe("lastFinite", () => {
  it("skips trailing gaps", () => {
    expect(lastFinite([1, 2, null, null])).toBe(2);
  });

  it("is null when every reading is a gap or the series is empty", () => {
    expect(lastFinite([null, null])).toBeNull();
    expect(lastFinite([])).toBeNull();
  });
});
