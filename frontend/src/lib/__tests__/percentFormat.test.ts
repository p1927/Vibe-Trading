import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { formatFractionPercent, formatSignedPercent } from "../formatters";

// One percent format (D306): the unit comes from the helper you call, never from the magnitude.
describe("percent helpers", () => {
  it("signed percent-scale", () => {
    expect(formatSignedPercent(1.234)).toBe("+1.23%");
    expect(formatSignedPercent(-0.5, 1)).toBe("-0.5%");
    expect(formatSignedPercent(0)).toBe("+0.00%");
  });

  it("fraction scale: 1 is 100%, 0.01 is 1%", () => {
    expect(formatFractionPercent(1)).toBe("100%");
    expect(formatFractionPercent(0.01)).toBe("1%");
    expect(formatFractionPercent(0.1234, 1)).toBe("12.3%");
  });

  it("missing is a placeholder, never 0", () => {
    for (const bad of [null, undefined, Number.NaN]) {
      expect(formatSignedPercent(bad)).toBe("—");
      expect(formatFractionPercent(bad, 0, "-")).toBe("-");
    }
  });
});

describe("no per-file percent formatter copies in src", () => {
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const full = join(dir, name);
      if (statSync(full).isDirectory()) {
        if (name !== "__tests__" && name !== "tests") walk(full);
      } else if (/\.tsx?$/.test(name)) files.push(full);
    }
  };
  walk(join(__dirname, "../.."));

  it("nobody redefines fmtPct", () => {
    const offenders = files.filter((f) => /\b(?:function|const)\s+fmtPct\s*[(=]/.test(readFileSync(f, "utf8")));
    expect(offenders).toEqual([]);
  });
});
