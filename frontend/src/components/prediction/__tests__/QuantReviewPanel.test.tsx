import { render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { QuantReviewPanel } from "../QuantReviewPanel";
import { api } from "@/lib/api";

afterEach(() => vi.restoreAllMocks());

const review = (asOf: string, view: string) => ({ review: { as_of: asOf, model_prediction_view: view } });

describe("QuantReviewPanel", () => {
  it("shows the as-of time in IST with the zone label, not a raw ISO slice", async () => {
    vi.spyOn(api, "getIndexQuantReview").mockResolvedValue(review("2026-10-15T04:00:00Z", "up") as never);
    render(<QuantReviewPanel />);
    await waitFor(() => expect(screen.getByText(/as of 15 Oct 2026, 09:30 IST/)).toBeInTheDocument());
  });

  it("a slow review for the previous horizon never replaces the current one", async () => {
    let resolveOld!: (v: never) => void;
    vi.spyOn(api, "getIndexQuantReview")
      .mockReturnValueOnce(new Promise((res) => { resolveOld = res as never; }) as never)
      .mockResolvedValue(review("2026-10-15T04:00:00Z", "NEW-VIEW") as never);
    const { rerender } = render(<QuantReviewPanel horizonDays={7} />);
    rerender(<QuantReviewPanel horizonDays={14} />);
    await screen.findByText("NEW-VIEW");
    resolveOld(review("2026-10-15T04:00:00Z", "OLD-VIEW") as never);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("OLD-VIEW")).not.toBeInTheDocument();
  });
});
