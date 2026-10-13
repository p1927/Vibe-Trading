import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import type { IndexPredictionHistoryRow } from "@/lib/api";
import { IndexFactorLedgerPanel } from "../IndexFactorLedgerPanel";

// The real ledger shape: a run at 19:00Z is the next IST day (the replay, the accuracy and the history bucket by it).
const row = (extra: Partial<IndexPredictionHistoryRow>): IndexPredictionHistoryRow => ({
  predicted_at: "2026-09-01T19:00:00+00:00", horizon_days: 14, spot_at_prediction: 24000, expected_return_pct: 1,
  implied_level: 24240, range_low: 23000, range_high: 25000, ...extra,
});

describe("history dates", () => {
  it("the ledger table shows the server's issue day, with the UTC time labelled, never the UTC date alone", () => {
    const t = render(<IndexFactorLedgerPanel daily={[row({ issue_day: "2026-09-02" })]} />).container.textContent ?? "";
    expect(t).toContain("2026-09-02 19:00 UTC");
    expect(t).not.toContain("2026-09-01");
  });

  it("a row without the server's day says so instead of guessing one from the timestamp", () => {
    const t = render(<IndexFactorLedgerPanel daily={[row({})]} />).container.textContent ?? "";
    expect(t).toContain("— 19:00 UTC");
    expect(t).not.toContain("2026-09-01");
  });
});
