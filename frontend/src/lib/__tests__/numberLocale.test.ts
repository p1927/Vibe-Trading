import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { formatNumber } from "../formatters";

// One number format (D306): Indian grouping, whatever the browser language is.
describe("formatNumber", () => {
  it("groups the Indian way with a fixed en-IN locale", () => {
    expect(formatNumber(1234567.891, { maximumFractionDigits: 2 })).toBe("12,34,567.89");
    expect(formatNumber(24000)).toBe("24,000");
    expect(formatNumber(-100000)).toBe("-1,00,000");
  });

  it("is an explicit placeholder for a missing or non-finite value, never 0", () => {
    for (const bad of [null, undefined, Number.NaN, Number.POSITIVE_INFINITY]) expect(formatNumber(bad)).toBe("—");
  });

  it("passes Intl options through", () => {
    expect(formatNumber(0.256, { style: "percent", maximumFractionDigits: 1 })).toBe("25.6%");
  });
});

// A bare toLocaleString()/Intl.NumberFormat(browser locale) renders 1,234,567 in one browser and 12,34,567
// in another: every number format must name its locale (or go through formatNumber).
describe("no browser-locale number formatting in src", () => {
  const root = join(__dirname, "../..");
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const full = join(dir, name);
      if (statSync(full).isDirectory()) {
        if (name !== "__tests__" && name !== "tests") walk(full);
      } else if (/\.tsx?$/.test(name)) files.push(full);
    }
  };
  walk(root);

  it("every toLocaleString / Intl.NumberFormat names a fixed locale", () => {
    const offenders: string[] = [];
    const re = /\.toLocaleString\(\s*(?:\)|undefined\b|\[)|Intl\.NumberFormat\(\s*(?:\)|undefined\b|\[|uiLocale\()/g;
    for (const file of files) {
      const text = readFileSync(file, "utf8");
      for (const m of text.matchAll(re)) {
        offenders.push(`${file.slice(root.length)}: ${text.slice(m.index!, m.index! + 60).split("\n")[0]}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
