import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { IndexPredictionArtifact } from "@/lib/api";

/*
 * FactorImpactWorkbench is the one factor what-if UI (the CausalFactorExplorer folded into it): these pin
 * the features it carried across - history mode, pinned key drivers, research note, downstream effects,
 * per-factor linked news - and that a failed context request is shown, not an empty list.
 */
vi.mock("@/components/charts/CausalFactorHistoryChart", () => ({
  CausalFactorHistoryChart: ({ activeFactor }: { activeFactor: string }) => <div data-testid="history-chart">{activeFactor}</div>,
}));
vi.mock("@/components/prediction/FactorImpactInteractiveChart", () => ({ FactorImpactInteractiveChart: () => <div data-testid="sim-chart" /> }));
vi.mock("@/components/prediction/FactorPedagogyLink", () => ({ FactorPedagogyLink: () => null }));
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, simulateIndexPrediction: vi.fn(), getIndexPlaygroundContext: vi.fn() } };
});

import { api } from "@/lib/api";
import { FactorImpactWorkbench } from "../FactorImpactWorkbench";

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;
let asOf = 0;
const artifact = () =>
  ({
    ticker: "NIFTY",
    as_of: `2026-10-14T0${asOf++}:00:00`, // a new key per test: the playground context is cached per as_of
    spot: 25000,
    prediction: { expected_return_pct: 1 },
    factor_explanation: { contributors: [{ factor: "oil_brent", label: "Brent", value: 80 }] },
  }) as unknown as IndexPredictionArtifact;

beforeEach(() => vi.clearAllMocks());

describe("FactorImpactWorkbench carries the explorer's features", () => {
  it("selects the pinned key drivers, shows the History chart, downstream effects and linked news", async () => {
    mocked.getIndexPlaygroundContext.mockResolvedValue({
      context: {
        headlines: [],
        events: [],
        factor_news: { oil_brent: [{ id: "n1", title: "OPEC cuts output", kind: "headline", source: "wire" }] },
      },
    });
    const { container } = render(<FactorImpactWorkbench artifact={artifact()} horizonDays={14} factorHistory={[]} />);
    expect(await screen.findByText("OPEC cuts output")).toBeInTheDocument();
    const options = [...container.querySelectorAll("select option")].map((o) => o.getAttribute("value"));
    expect(options).toContain("fii_net_5d"); // pinned, though the model did not rank it
    expect(screen.getByText(/Typical downstream effects/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "History" }));
    expect(screen.getByTestId("history-chart")).toHaveTextContent("oil_brent");
    expect(screen.queryByTestId("sim-chart")).toBeNull();
  });

  it("a failed context request says so instead of showing an empty news list", async () => {
    mocked.getIndexPlaygroundContext.mockRejectedValue(new Error("hub offline"));
    render(<FactorImpactWorkbench artifact={artifact()} horizonDays={14} />);
    expect(await screen.findByText(/failed to load \(hub offline\)/)).toBeInTheDocument();
  });
});
