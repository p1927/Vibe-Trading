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
  staleness: { status: "fresh" },
} as unknown as TradePlanWidget;

describe("TradePlanWidgetCard live plan check", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getTradeExecutionMode").mockResolvedValue({ mode: "paper", paper_env: false, live_allowed: true } as never);
  });

  it("a failed live check says so instead of keeping the creation-time 'Plan current'", async () => {
    vi.spyOn(api, "getPlanContext").mockRejectedValue(new Error("HTTP 503"));
    render(<TradePlanWidgetCard widget={widget} />);
    await waitFor(() => expect(screen.getByTestId("plan-live-check-failed")).toHaveTextContent("HTTP 503"));
    expect(screen.queryByText(/Plan current/)).toBeNull();
  });

  it("a working live check keeps showing 'Plan current'", async () => {
    vi.spyOn(api, "getPlanContext").mockResolvedValue({ monitor_enabled: true, staleness: { status: "fresh" } } as never);
    render(<TradePlanWidgetCard widget={widget} />);
    await waitFor(() => expect(screen.getByText(/Plan current/)).toBeTruthy());
    expect(screen.queryByTestId("plan-live-check-failed")).toBeNull();
  });
});
