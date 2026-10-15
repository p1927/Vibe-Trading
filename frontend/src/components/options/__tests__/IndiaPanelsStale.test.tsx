import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { IndiaSelectorPanel } from "../IndiaSelectorPanel";
import { IndiaPopOverlayPanel } from "../IndiaPopOverlayPanel";
import { IndiaStrategyPanel } from "../IndiaStrategyPanel";
import { api } from "@/lib/api";

vi.mock("@/components/charts/PopOverlayChart", () => ({ PopOverlayChart: () => null }));

// While the next ticker is being scored (up to ~3 minutes) the previous ticker's numbers must not stay on
// screen as if they were current, and an older answer must never replace a newer request's.

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

afterEach(() => vi.restoreAllMocks());

const candidate = (name: string) => ({
  name, legs: [], max_profit: 100, max_loss: 50, probability_of_profit: 0.5, expected_pnl: 10, entry_iv: 0.2,
  risk_reward_ratio: 2, pop_per_risk: 0.1, meets_target: true, event_risks: [],
});
const selectorData = (ticker: string, name: string) => ({
  ok: true, ticker, expiry_date: "2026-10-29", expiry_days: 14, horizon_days: 7, underlying_ltp: 24000,
  target_profit: 1000, candidates: [candidate(name)],
});

describe("IndiaSelectorPanel", () => {
  it("clears the previous ticker's candidates while the next load runs (no stale Approve button)", async () => {
    const second = deferred<never>();
    vi.spyOn(api, "getIndiaOptionsSelector")
      .mockResolvedValueOnce(selectorData("NIFTY", "Iron Condor NIFTY") as never)
      .mockReturnValueOnce(second.promise as never);
    render(<IndiaSelectorPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Load" }));
    await screen.findByText("Iron Condor NIFTY");
    fireEvent.click(screen.getByRole("button", { name: "Load" }));
    await waitFor(() => expect(screen.queryByText("Iron Condor NIFTY")).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    second.resolve(selectorData("NIFTY", "Bull Put NIFTY") as never);
    await screen.findByText("Bull Put NIFTY");
  });
});

describe("IndiaPopOverlayPanel", () => {
  it("clears the previous result while the next load runs", async () => {
    const second = deferred<never>();
    const data = (spot: number) => ({
      ok: true, ticker: "NIFTY", underlying_ltp: spot, expiry_date: "2026-10-29", horizon_days: 7,
      forecast_quantiles: {}, overlay: [], event_risks: [],
    });
    vi.spyOn(api, "getIndiaOptionsPopOverlay")
      .mockResolvedValueOnce(data(11111) as never)
      .mockReturnValueOnce(second.promise as never);
    render(<IndiaPopOverlayPanel source={"live" as never} />);
    fireEvent.click(screen.getByRole("button", { name: "Load" }));
    await screen.findByText(/11,111/);
    fireEvent.click(screen.getByRole("button", { name: "Load" }));
    await waitFor(() => expect(screen.queryByText(/11,111/)).not.toBeInTheDocument());
    second.resolve(data(22222) as never);
    await screen.findByText(/22,222/);
  });
});

describe("IndiaStrategyPanel", () => {
  const research = (spot: number, asOf: string) => ({
    ok: true,
    data: { underlying: "NIFTY", as_of: asOf, instrument_type: "index", expiry: "2026-10-29", spot, atm_strike: spot, pcr: 1, ranked_strategies: [], recommended: {}, charges: {} },
  });

  it("clears the previous result while the next load runs", async () => {
    const second = deferred<never>();
    vi.spyOn(api, "getIndiaOptionsResearch")
      .mockResolvedValueOnce(research(11111, "2026-10-15T04:00:00Z") as never)
      .mockReturnValueOnce(second.promise as never);
    render(<IndiaStrategyPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Load" }));
    await screen.findAllByText(/11,111/);
    fireEvent.click(screen.getByRole("button", { name: "Load" }));
    await waitFor(() => expect(screen.queryAllByText(/11,111/)).toHaveLength(0));
    second.resolve(research(22222, "2026-10-15T04:00:00Z") as never);
    await screen.findAllByText(/22,222/);
  });

  it("shows as-of in IST with the zone label, not a raw ISO slice", async () => {
    vi.spyOn(api, "getIndiaOptionsResearch").mockResolvedValue(research(24000, "2026-10-15T04:00:00Z") as never);
    render(<IndiaStrategyPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Load" }));
    expect(await screen.findByTestId("india-strategy-as-of")).toHaveTextContent("15 Oct 2026, 09:30 IST");
  });
});
