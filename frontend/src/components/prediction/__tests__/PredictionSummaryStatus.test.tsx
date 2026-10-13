import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import type { IndexPredictionArtifact } from "@/lib/api";
import { bandSourceLabel, PredictionSummary } from "../PredictionSummary";

// Shapes recorded from the real hub artifact (2026-10-12 run) and the predictor's D401/D410 additions.
const base = {
  spot: 22363.35,
  as_of: "2026-10-12T09:56:54+00:00",
  prediction: {
    expected_return_pct: 1.406,
    range: { low: 22483, high: 22872, confidence: 0.35 },
    quant: { expected_return_pct: 1.3444 },
  },
} as unknown as IndexPredictionArtifact;

const withPred = (extra: Record<string, unknown>) =>
  ({ ...base, prediction: { ...base.prediction, ...extra } }) as unknown as IndexPredictionArtifact;
const text = (a: IndexPredictionArtifact) => render(<PredictionSummary artifact={a} />).container.textContent ?? "";

describe("PredictionSummary band", () => {
  it("never shows the ridge R2 clamp (range.confidence) as the band", () => {
    const t = text(base);
    expect(t).not.toContain("35%");
    expect(t).toContain("Range band (± half-width)");
    expect(t).toContain("width not recorded");
  });

  it("shows the recorded half-width and says it is the 80% conformal band", () => {
    const t = text(withPred({ range: { low: 21000, high: 23000, half_width_pct: 4.26, band_source: "walk_forward_oos" } }));
    expect(t).toContain("±4.26%");
    expect(t).toContain("80% conformal");
  });

  it("a prior width is called a prior, not a measurement", () => {
    expect(bandSourceLabel({ half_width_pct: 4.26, band_source: "prior" })).toContain("prior");
    expect(text(withPred({ range: { low: 1, high: 2, half_width_pct: 4.26, band_source: "prior" } }))).toContain("not measured");
  });
});

describe("PredictionSummary model inputs and debate", () => {
  it("lists the inputs the snapshot lacked (D410)", () => {
    const t = text(withPred({ model_missing_features: ["nifty_dividend_yield", "hyg"] }));
    expect(t).toContain("2 model inputs were missing");
    expect(t).toContain("nifty_dividend_yield, hyg");
    expect(text(withPred({ model_missing_features: [] }))).not.toContain("missing from this");
  });

  it("claims the 60/40 blend only when the debate stated a return, and shows its date and figure", () => {
    const t = text(withPred({ debate_merged: true, debate: { expected_return_pct: 7.9, view: "bearish" }, provenance: { debate_as_of: "2026-07-23T21:48:47Z" } }));
    expect(t).toContain("60% debate");
    expect(t).toContain("of 2026-07-23");
    expect(t).toContain("+7.90%");
  });

  it("a debate with no stated figure does not claim a blend", () => {
    const t = text(withPred({ debate_merged: true, debate: { expected_return_pct: null, view: "bearish" }, provenance: { direction: "quant" } }));
    expect(t).not.toContain("60% debate");
    expect(t).toContain("stated no return figure");
  });
});
