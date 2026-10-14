import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { formatIst, formatIstDate, formatIstTime } from "../istTime";

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

  it("can drop the seconds", () => {
    expect(formatIstTime("2026-10-15T04:00:05Z", false)).toBe("09:30 IST");
    expect(formatIstTime("nope", false)).toBe("—");
  });
});

describe("formatIstDate", () => {
  it("is the IST calendar day of an instant", () => {
    expect(formatIstDate("2026-10-14T20:00:00Z")).toBe("15 Oct 2026"); // 01:30 IST next day
    expect(formatIstDate("2026-10-15T04:00:00Z", false)).toBe("15 Oct");
    expect(formatIstDate(Date.UTC(2026, 9, 15, 4))).toBe("15 Oct 2026");
  });

  it("keeps a bare YYYY-MM-DD on that day", () => {
    expect(formatIstDate("2026-10-15")).toBe("15 Oct 2026");
    expect(formatIstDate("2026-01-01", false)).toBe("1 Jan");
  });

  it("is a placeholder for missing or unreadable values", () => {
    for (const bad of [null, undefined, "", "x", Number.NaN]) expect(formatIstDate(bad)).toBe("—");
  });
});

// One way to show a time (D306): no component formats a Date in the browser's zone. A new
// toLocale{Date,Time}String / new Date(..).toLocaleString / getHours call must name its timeZone or use lib/istTime.
describe("no browser-zone date rendering in src", () => {
  const root = join(__dirname, "../..");
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const full = join(dir, name);
      if (statSync(full).isDirectory()) {
        if (name !== "__tests__" && name !== "tests") walk(full);
      } else if (/\.tsx?$/.test(name) && !full.endsWith("lib/istTime.ts")) files.push(full);
    }
  };
  walk(root);

  it("every date/time locale format names a timeZone", () => {
    const offenders: string[] = [];
    const re = /\.toLocale(?:Date|Time)String\(|new Date\([^()]*\)\.toLocaleString\(|\.getHours\(|\.getMinutes\(/g;
    for (const file of files) {
      const text = readFileSync(file, "utf8");
      for (const m of text.matchAll(re)) {
        const window = text.slice(m.index!, m.index! + 200);
        if (!/timeZone/.test(window)) offenders.push(`${file.slice(root.length)}: ${text.slice(m.index!, m.index! + 60).split("\n")[0]}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
