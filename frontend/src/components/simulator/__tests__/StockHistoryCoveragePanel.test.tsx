import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { StockHistoryCoveragePanel } from "../StockHistoryCoveragePanel";
import { api } from "@/lib/api";

vi.mock("@/components/scheduler/LiveLogTail", () => ({ LiveLogTail: () => null }));
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getHubStockHistoryCoverage: vi.fn(),
      getActiveHubStockHistoryBackfillRun: vi.fn(),
    },
  };
});

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

function status(bucket: string, present: boolean) {
  return { bucket, present, primary_source: "x", fetch_command: "cmd", fallback: null };
}

/** Bucket "a" has a status on the day; bucket "b" is listed in bucket_labels but the day carries no status for it. */
function report() {
  return {
    status: "ok",
    week_start: "2026-10-12",
    week_end: "2026-10-16",
    symbol: "NIFTY",
    is_complete: true,
    missing_days: [],
    bucket_labels: ["a", "b"],
    fetch_list: [],
    days: [
      {
        day: "2026-10-13",
        is_weekday: true,
        is_complete: true,
        present: ["a"],
        missing: [],
        buckets: { a: status("a", true) },
      },
    ],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocked.getHubStockHistoryCoverage.mockResolvedValue(report());
  mocked.getActiveHubStockHistoryBackfillRun.mockResolvedValue({ run: null });
});

describe("StockHistoryCoveragePanel day drawer", () => {
  it("does not claim every selected bucket is present when one has no status for the day", async () => {
    render(<StockHistoryCoveragePanel initialWeekStart="2026-10-12" />);
    fireEvent.click(await screen.findByRole("button", { name: "10-13" }));
    const unknown = await screen.findByTestId("day-unknown-buckets");
    expect(unknown).toHaveTextContent("No coverage status for 1 selected bucket(s) on 2026-10-13");
    expect(unknown).toHaveTextContent("b");
    expect(screen.queryByText(/Every selected bucket is present/)).toBeNull();
  });

  it("says every selected bucket is present only when each one has a present status", async () => {
    const full = report();
    full.days[0].buckets = { a: status("a", true), b: status("b", true) } as never;
    mocked.getHubStockHistoryCoverage.mockResolvedValue(full);
    render(<StockHistoryCoveragePanel initialWeekStart="2026-10-12" />);
    fireEvent.click(await screen.findByRole("button", { name: "10-13" }));
    await waitFor(() => expect(screen.getByText(/Every selected bucket is present on 2026-10-13/)).toBeInTheDocument());
    expect(screen.queryByTestId("day-unknown-buckets")).toBeNull();
  });
});
