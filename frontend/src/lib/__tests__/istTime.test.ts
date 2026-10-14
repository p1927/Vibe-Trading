import { describe, expect, it } from "vitest";
import { formatIst, formatIstTime } from "../istTime";

// Market data for the Indian markets is stamped in IST; a browser in another zone must not shift it.
describe("formatIst", () => {
  it("renders an ISO instant in IST with an explicit zone label, whatever the browser zone is", () => {
    expect(formatIst("2026-10-15T04:00:00Z")).toBe("15 Oct 2026, 09:30 IST");
    expect(formatIst("2026-10-15T23:00:00Z")).toBe("16 Oct 2026, 04:30 IST"); // crosses the IST date line
    expect(formatIst(Date.UTC(2026, 9, 15, 4, 0, 0))).toBe("15 Oct 2026, 09:30 IST");
    expect(formatIst(new Date("2026-10-15T04:00:00+05:30"))).toBe("15 Oct 2026, 04:00 IST");
  });

  it("is an explicit placeholder, never 'Invalid Date', for a missing or unreadable value", () => {
    for (const bad of [null, undefined, "", "not a date", Number.NaN]) {
      expect(formatIst(bad)).toBe("—");
    }
  });
});

describe("formatIstTime", () => {
  it("renders the IST wall-clock time with seconds", () => {
    expect(formatIstTime("2026-10-15T04:00:05Z")).toBe("09:30:05 IST");
    expect(formatIstTime(undefined)).toBe("—");
  });
});
