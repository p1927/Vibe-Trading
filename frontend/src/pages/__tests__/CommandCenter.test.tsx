import { act, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CommandCenter } from "@/pages/CommandCenter";
import { api } from "@/lib/api";

vi.mock("@/components/board/PnlForecastBandChart", () => ({ PnlForecastBandChart: () => null }));
vi.mock("@/components/charts/IndexEventsForecastChart", () => ({
  IndexEventsForecastChart: (p: { spot: number; expectedReturnPct: number }) => (
    <div data-testid="forecast-chart">{`${p.spot}/${p.expectedReturnPct}`}</div>
  ),
}));
vi.mock("@/components/charts/PriorDayPriceStrip", () => ({ PriorDayPriceStrip: () => null }));
vi.mock("@/components/prediction/NewsImpactPanel", () => ({ NewsImpactPanel: () => null }));
vi.mock("@/components/news/EventsCalendar", () => ({ EventsCalendar: () => null }));

// useSSE is the transport; the page's own logic (per-leg errors, placeholders) is what is tested.
const sse = vi.hoisted(() => ({ handlers: {} as Record<string, (d: Record<string, unknown>) => void>, status: "connected" }));
vi.mock("@/hooks/useSSE", () => ({
  useSSE: () => ({
    connect: (_url: string, handlers: typeof sse.handlers) => {
      sse.handlers = handlers;
    },
    disconnect: () => {},
    onStatusChange: (cb: (s: string) => void) => cb(sse.status),
  }),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listAutonomousAgents: vi.fn(),
      getHubIndexHistoryDays: vi.fn().mockResolvedValue({ status: "ok", days: [] }),
      commandCenterStreamUrl: vi.fn().mockReturnValue("http://test/stream"),
    },
  };
});

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;
const renderPage = () =>
  render(
    <MemoryRouter>
      <CommandCenter />
    </MemoryRouter>,
  );

beforeEach(() => {
  sse.status = "connected";
  mocked.listAutonomousAgents.mockResolvedValue({ agents: [{ id: "a1", name: "Alpha" }] });
});

describe("CommandCenter page", () => {
  it("a failed agents call is an error, not 'No autonomous agents exist yet.'", async () => {
    mocked.listAutonomousAgents.mockRejectedValue(new Error("agents 500"));
    renderPage();
    expect(await screen.findByText(/Could not load the autonomous agents: agents 500/)).toBeInTheDocument();
    expect(screen.queryByText("No autonomous agents exist yet.")).toBeNull();
  });

  it("says 'No autonomous agents exist yet.' only after the list loaded empty", async () => {
    mocked.listAutonomousAgents.mockResolvedValue({ agents: [] });
    renderPage();
    expect(await screen.findByText("No autonomous agents exist yet.")).toBeInTheDocument();
  });

  it("one leg recovering does not hide another leg's error", async () => {
    renderPage();
    await screen.findByText("Alpha");
    act(() => sse.handlers.prediction_error({ message: "prediction down" }));
    act(() => sse.handlers.positions({ groups: [], skipped: [] }));
    expect(screen.getByRole("alert")).toHaveTextContent("prediction: prediction down");
    act(() => sse.handlers.prediction({ artifact: null }));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("a dead stream says disconnected, not 'Connecting…' forever", async () => {
    sse.status = "disconnected";
    renderPage();
    await screen.findByText("Alpha");
    expect(screen.getAllByText("Live feed disconnected — no data.").length).toBeGreaterThan(0);
    expect(screen.queryByText("Connecting…")).toBeNull();
  });

  it("does not draw a 0-level 0% forecast when the artifact has no spot or expected return", async () => {
    renderPage();
    await screen.findByText("Alpha");
    act(() => sse.handlers.prediction({ artifact: { as_of: "2026-10-13", prediction: {}, upcoming_events: [] } }));
    expect(screen.queryByTestId("forecast-chart")).toBeNull();
    expect(screen.getByText(/Forecast chart unavailable/)).toBeInTheDocument();
  });

  it("draws the chart with the real values when they are present, including a real 0% return", async () => {
    renderPage();
    await screen.findByText("Alpha");
    act(() =>
      sse.handlers.prediction({
        artifact: { as_of: "2026-10-13", spot: 25000, prediction: { expected_return_pct: 0 }, upcoming_events: [] },
      }),
    );
    expect(screen.getByTestId("forecast-chart")).toHaveTextContent("25000/0");
  });

  it("total P&L is unknown (not 0) when a group has no observed trajectory", async () => {
    renderPage();
    await screen.findByText("Alpha");
    act(() =>
      sse.handlers.positions({
        groups: [{ underlying: "NIFTY", expiry_days: 3, trajectory: [], pnl_forecast_band: [] }],
        skipped: [],
      }),
    );
    expect(screen.getByText(/a group has no observed P&L/)).toBeInTheDocument();
  });
});
