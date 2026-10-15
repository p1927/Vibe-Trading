import { describe, expect, it } from "vitest";
import { buildMetricsCsv, buildTradesCsv, escapeCsvField } from "../csv";

// A trade's `reason` or a symbol is model/data text: opened in a spreadsheet, a leading = + - @ is a formula.
describe("csv export", () => {
  it("neutralises a formula-looking text cell with a leading apostrophe", () => {
    for (const text of ['=HYPERLINK("http://x","y")', "+cmd|' /C calc'!A0", "@SUM(1+1)", "-2+3", "\tcmd", "\rcmd"]) {
      expect(escapeCsvField(text).replace(/^"/, "")).toMatch(/^'/);
    }
  });

  it("leaves real numbers and ordinary text untouched, including negatives and exponents", () => {
    expect(escapeCsvField("-5.25")).toBe("-5.25");
    expect(escapeCsvField(-5.25)).toBe("-5.25");
    expect(escapeCsvField("+3")).toBe("+3");
    expect(escapeCsvField("1e-7")).toBe("1e-7");
    expect(escapeCsvField("RELIANCE.NS")).toBe("RELIANCE.NS");
    expect(escapeCsvField("exit - stop loss")).toBe("exit - stop loss");
  });

  it("still quotes commas, quotes and newlines, and writes null/undefined as an empty cell", () => {
    expect(escapeCsvField('a,"b"')).toBe('"a,""b"""');
    expect(escapeCsvField("line1\nline2")).toBe('"line1\nline2"');
    expect(escapeCsvField(null)).toBe("");
    expect(escapeCsvField(undefined)).toBe("");
  });

  it("applies the guard to every cell of the trades and metrics files", () => {
    const trades = buildTradesCsv([{ code: "X", reason: "=1+1", pnl: "-3.5" }]);
    expect(trades.split("\n")[1]).toBe("X,'=1+1,-3.5");
    expect(buildMetricsCsv({ sharpe: 1.2 } as never)).toBe("metric,value\nsharpe,1.2");
    expect(buildTradesCsv([])).toBe("");
  });
});
