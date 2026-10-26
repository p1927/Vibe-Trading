import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { RunDetail } from "../RunDetail";

const apiMock = vi.hoisted(() => ({
  getRun: vi.fn(),
  getRunCode: vi.fn(),
  getRunFactor: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMock }));
vi.mock("@/components/charts/CandlestickChart", () => ({ CandlestickChart: () => <div /> }));
vi.mock("@/components/charts/EquityChart", () => ({ EquityChart: () => <div /> }));
vi.mock("@/components/charts/StrategyResearchDashboard", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/charts/StrategyResearchDashboard")>();
  return { ...actual, StrategyResearchDashboard: () => <div /> };
});

function renderRunDetail(path: string) {
  const router = createMemoryRouter([{ path: "/runs/:runId", element: <RunDetail /> }], { initialEntries: [path] });
  render(<RouterProvider router={router} />);
}

describe("RunDetail missing readings", () => {
  beforeEach(() => {
    apiMock.getRun.mockReset();
    apiMock.getRunCode.mockReset();
    apiMock.getRunCode.mockResolvedValue({});
  });
  afterEach(() => cleanup());

  it("a null risk X-ray reading is a dash, never 0.0% or a crash", async () => {
    apiMock.getRun.mockResolvedValue({
      status: "success",
      run_id: "xr",
      prompt: "Null xray",
      risk_xray: {
        inputs: { weights: { AAPL: null } },
        concentration: { hhi: null, effective_n: null },
        volatility: { annualized_vol: null },
        drawdown: { max_drawdown: null },
      },
    });
    renderRunDetail("/runs/xr");
    await screen.findByText("Null xray");
    fireEvent.click(screen.getByRole("tab", { name: "Portfolio Studio" }));
    const vol = (await screen.findByText("Annualized Vol")).parentElement!;
    expect(vol).toHaveTextContent("—");
    expect(vol).not.toHaveTextContent("0.0%");
    expect(screen.getByText("Max Drawdown").parentElement).not.toHaveTextContent("0.0%");
    expect(screen.getByText("AAPL").parentElement).not.toHaveTextContent("0.0%");
  });

  it("a rebalance turnover of null is a dash, not 0.0%", async () => {
    apiMock.getRun.mockResolvedValue({
      status: "success",
      run_id: "rb",
      prompt: "Null turnover",
      rebalance_notes: {
        rebalances: [{ date: "2026-02-02", turnover: null, entries: [], exits: [] }],
        summary: { target_change_count: 1, turnover_total: null, turnover_mean: null, turnover_max: null },
      },
    });
    renderRunDetail("/runs/rb");
    await screen.findByText("Null turnover");
    fireEvent.click(screen.getByRole("tab", { name: "Portfolio Studio" }));
    await screen.findByText("Rebalance Notes");
    expect(document.body.textContent).not.toContain("0.0%");
  });

  it("a run with elapsed_seconds 0 does not print a stray 0 in the header", async () => {
    apiMock.getRun.mockResolvedValue({ status: "success", run_id: "eab", prompt: "Zero elapsed", elapsed_seconds: 0 });
    renderRunDetail("/runs/eab");
    await screen.findByText("Zero elapsed");
    const header = screen.getByText("RUN eab").closest("div.border-b")!;
    expect(header.textContent).not.toMatch(/(^|[^\d.])0(?![\d.s%])/);
  });

  it("a code file that contains a markdown fence stays one code block", async () => {
    apiMock.getRun.mockResolvedValue({ status: "success", run_id: "cf", prompt: "Code fence" });
    apiMock.getRunCode.mockResolvedValue({ "strategy.py": 'DOC = """\n```\nINSIDE_AFTER_FENCE\n```\n"""\n' });
    renderRunDetail("/runs/cf");
    await screen.findByText("Code fence");
    fireEvent.click(screen.getByRole("tab", { name: /Code/ }));
    const block = await screen.findByText(/INSIDE_AFTER_FENCE/);
    expect(block.closest("pre")).not.toBeNull();
  });
});
