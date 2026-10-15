import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TradePlanWidgetCard } from "@/components/chat/TradePlanWidgetCard";
import { api, type TradePlanWidget } from "@/lib/api";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), info: vi.fn(), error: vi.fn(), message: vi.fn() } }));

const planWidget = {
  type: "trade_plan.widget",
  widget_id: "w1",
  asset_type: "stock",
  underlying: "INFY",
  scenarios: [],
  recommended: {
    name: "buy_stock",
    legs: [{ symbol: "INFY", side: "BUY", quantity: 1, price: 100, segment: "EQUITY" }],
  },
} as unknown as TradePlanWidget;

const newsWidget = {
  type: "trade_plan.widget",
  widget_id: "w2",
  widget_kind: "news_event_scenario",
  underlying: "NIFTY",
  outcomes: [],
} as unknown as TradePlanWidget;

const paper = { mode: "paper", paper_env: false, live_allowed: true } as never;
const live = { mode: "live", paper_env: false, live_allowed: true } as never;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

describe("TradePlanWidgetCard order safety", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getPlanContext").mockResolvedValue({ monitor_enabled: false } as never);
  });
  afterEach(() => cleanup());

  it("switching the same card to a news scenario widget does not break the hook order", async () => {
    vi.spyOn(api, "getTradeExecutionMode").mockResolvedValue(paper);
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const { rerender } = render(<TradePlanWidgetCard widget={planWidget} />);
    await waitFor(() => expect(screen.getByText("Execute (Paper)")).toBeTruthy());
    expect(() => rerender(<TradePlanWidgetCard widget={newsWidget} />)).not.toThrow();
    expect(screen.getByText(/News scenario/)).toBeTruthy();
    expect(() => rerender(<TradePlanWidgetCard widget={planWidget} />)).not.toThrow();
    expect(errors.mock.calls.some((call) => /hook/i.test(String(call[0])))).toBe(false);
  });

  it("the execution mode shown is the latest read, not whichever answer arrives last", async () => {
    const first = deferred<never>();
    const second = deferred<never>();
    vi.spyOn(api, "getTradeExecutionMode")
      .mockReturnValueOnce(first.promise as never)
      .mockReturnValueOnce(second.promise as never);
    render(<TradePlanWidgetCard widget={planWidget} />);
    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    await act(async () => {
      second.resolve(live);
    });
    await waitFor(() => expect(screen.getByText("Execute (Live)")).toBeTruthy());
    await act(async () => {
      first.resolve(paper);
    });
    expect(screen.getByText("Execute (Live)")).toBeTruthy();
    expect(screen.queryByText("Execute (Paper)")).toBeNull();
  });

  it("offers no Execute button while the execution mode is still unknown", async () => {
    const pending = deferred<never>();
    vi.spyOn(api, "getTradeExecutionMode").mockReturnValue(pending.promise as never);
    render(<TradePlanWidgetCard widget={planWidget} />);
    expect(screen.queryByText(/^Execute/)).toBeNull();
    await act(async () => {
      pending.resolve(paper);
    });
    expect(await screen.findByText("Execute (Paper)")).toBeTruthy();
  });

  it("approving an autonomous plan asks first and fires once on a double click", async () => {
    vi.spyOn(api, "getTradeExecutionMode").mockResolvedValue(paper);
    const slow = deferred<never>();
    const approve = vi.spyOn(api, "approveAutonomousPlan").mockReturnValue(slow.promise as never);
    render(<TradePlanWidgetCard widget={planWidget} autonomousMode approvalState="pending" agentId="a1" />);
    fireEvent.click(await screen.findByText(/Approve & start watching/));
    expect(approve).not.toHaveBeenCalled();
    const confirmButton = await screen.findByRole("button", { name: "Approve plan" });
    fireEvent.click(confirmButton);
    fireEvent.click(confirmButton);
    await waitFor(() => expect(approve).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByText(/Approve & start watching/));
    expect(approve).toHaveBeenCalledTimes(1);
    await act(async () => {
      slow.resolve({ status: "ok" } as never);
    });
  });

  it("cancelling the approve confirm sends nothing", async () => {
    vi.spyOn(api, "getTradeExecutionMode").mockResolvedValue(paper);
    const approve = vi.spyOn(api, "approveAutonomousPlan").mockResolvedValue({ status: "ok" } as never);
    render(<TradePlanWidgetCard widget={planWidget} autonomousMode approvalState="pending" agentId="a1" />);
    fireEvent.click(await screen.findByText(/Approve & start watching/));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(approve).not.toHaveBeenCalled();
  });

  it("an executed basket does not stay 'Submitted' when the card is given another plan", async () => {
    vi.spyOn(api, "getTradeExecutionMode").mockResolvedValue(paper);
    vi.spyOn(api, "executeTradeBasket").mockResolvedValue({ message: "ok", execution_mode: "paper" } as never);
    const { rerender } = render(<TradePlanWidgetCard widget={planWidget} />);
    fireEvent.click(await screen.findByText("Execute (Paper)"));
    fireEvent.click(await screen.findByRole("button", { name: "Place paper basket" }));
    expect(await screen.findByText("Submitted")).toBeTruthy();
    rerender(<TradePlanWidgetCard widget={{ ...planWidget, widget_id: "w3" } as TradePlanWidget} />);
    await waitFor(() => expect(screen.getByText("Execute (Paper)")).toBeTruthy());
  });

  it.each(["javascript:alert(1)", "data:text/html,<b>x", "//evil.example/x", "/relative"])(
    "a widget link that is not an absolute http(s) url (%s) renders no anchor",
    async (bad) => {
      vi.spyOn(api, "getTradeExecutionMode").mockResolvedValue({ ...(paper as object), switch_url: bad } as never);
      const widget = { ...planWidget, meta: { strategy_builder_url: bad } } as unknown as TradePlanWidget;
      render(<TradePlanWidgetCard widget={widget} />);
      await screen.findByText("Execute (Paper)");
      expect(screen.queryByText("Open Strategy Builder")).toBeNull();
      expect(screen.queryByText("Switch in OpenAlgo")).toBeNull();
    },
  );

  it("a normal https builder link and switch link still render", async () => {
    vi.spyOn(api, "getTradeExecutionMode").mockResolvedValue({ ...(paper as object), switch_url: "http://127.0.0.1:5001/" } as never);
    const widget = { ...planWidget, meta: { strategy_builder_url: "https://trade.example/?tab=x" } } as unknown as TradePlanWidget;
    render(<TradePlanWidgetCard widget={widget} />);
    expect((await screen.findByText("Open Strategy Builder")).closest("a")).toHaveAttribute("href", "https://trade.example/?tab=x");
    expect(screen.getByText("Switch in OpenAlgo").closest("a")).toHaveAttribute("href", "http://127.0.0.1:5001/");
  });

  it("a ranked strategy without a tier is not printed as '(undefined)'", async () => {
    vi.spyOn(api, "getTradeExecutionMode").mockResolvedValue(paper);
    const widget = {
      ...planWidget,
      ranked_strategies: [
        { name: "a", score: 1 },
        { name: "b", score: 0.5 },
      ],
    } as unknown as TradePlanWidget;
    render(<TradePlanWidgetCard widget={widget} />);
    await screen.findByText(/Also ranked/);
    expect(document.body.textContent).not.toContain("undefined");
  });
});
