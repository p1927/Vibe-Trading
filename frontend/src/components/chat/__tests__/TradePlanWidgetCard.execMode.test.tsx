import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TradePlanWidgetCard } from "@/components/chat/TradePlanWidgetCard";
import { api, type TradePlanWidget } from "@/lib/api";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

const widget = {
  type: "trade_plan.widget",
  widget_id: "w1",
  asset_type: "stock",
  underlying: "INFY",
  scenarios: [],
} as unknown as TradePlanWidget;

describe("TradePlanWidgetCard execution mode", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("says the mode is unknown (not Live) when the mode read fails", async () => {
    vi.spyOn(api, "getTradeExecutionMode").mockRejectedValue(new Error("HTTP 503"));
    render(<TradePlanWidgetCard widget={widget} />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Execution mode unknown (HTTP 503)"));
    expect(screen.queryByText("Execute (Live)")).toBeNull();
  });

  it("shows no alert when the mode loads", async () => {
    vi.spyOn(api, "getTradeExecutionMode").mockResolvedValue({
      mode: "paper",
      paper_env: false,
      live_allowed: true,
    } as never);
    render(<TradePlanWidgetCard widget={widget} />);
    await waitFor(() => expect(screen.getByText(/Execution mode follows OpenAlgo \(Analyze\)/)).toBeTruthy());
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
