import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ResearchContextPanel } from "../ResearchContextPanel";

describe("ResearchContextPanel units", () => {
  it("scenario probability and score are fractions; a non-numeric move is a dash, not NaN", () => {
    render(
      <ResearchContextPanel
        underlying="NIFTY"
        recommendedName="Iron Condor"
        recommendedTier="act"
        recommendedScore={1}
        prediction={{ view: "neutral", confidence: 0.9, expected_move_pct: "n/a" as unknown as number }}
        scenarios={[{ name: "base", probability: 0.01, trigger: "t", strategy_hint: "iron_condor" }]}
      />,
    );
    expect(screen.getByText(/^1% likely$/)).toBeInTheDocument();
    expect(screen.queryByText(/NaN/)).toBeNull();
    expect(screen.getByText(/±—%/)).toBeInTheDocument();
  });
});
