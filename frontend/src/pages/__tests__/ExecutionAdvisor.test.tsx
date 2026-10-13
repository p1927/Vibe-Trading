import { render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { ExecutionAdvisor, adviceAge, STALE_ADVICE_MS } from "../ExecutionAdvisor";
import { api } from "@/lib/api";
import type { ExecutionAdvisorAdvisory } from "@/lib/options";

const NOW = Date.parse("2026-10-13T10:00:00Z");

function advice(overrides: Partial<ExecutionAdvisorAdvisory> = {}): ExecutionAdvisorAdvisory {
  return {
    symbol: "NIFTY24OCT25000CE",
    strategy_group_id: null,
    fsm_state: "trailing",
    action: "tighten_stop",
    reason: "confidence_dropped",
    advised_at: new Date(Date.now() - 5 * 60_000).toISOString(),
    recommended_stop_price: 101.5,
    entry_price: 100,
    entry_direction: "bullish",
    confidence_ema: 0.62,
    consecutive_flip_count: 1,
    ltp: 104,
    ...overrides,
  };
}

function mount(advisories: ExecutionAdvisorAdvisory[]) {
  vi.spyOn(api, "getExecutionAdvisorAdvisories").mockResolvedValue({
    ok: true,
    count: advisories.length,
    advisories,
    grouped: { ungrouped: advisories },
  });
  vi.spyOn(api, "getKnowledgeTrackRecord").mockRejectedValue(new Error("no track record"));
  return render(<ExecutionAdvisor />);
}

describe("adviceAge", () => {
  it("is fresh within the stale window and stale beyond it", () => {
    expect(adviceAge(new Date(NOW - 60_000).toISOString(), NOW)).toEqual({ label: "advised 1m ago", stale: false });
    expect(adviceAge(new Date(NOW - STALE_ADVICE_MS - 60_000).toISOString(), NOW).stale).toBe(true);
  });
  it("a timestamp slightly ahead of the browser clock reads 'just now', not a negative age", () => {
    expect(adviceAge(new Date(NOW + 90_000).toISOString(), NOW)).toEqual({ label: "advised just now", stale: false });
  });
  it("a missing or unparseable time is stale, never fresh", () => {
    expect(adviceAge(null, NOW).stale).toBe(true);
    expect(adviceAge("garbage", NOW).stale).toBe(true);
  });
});

describe("ExecutionAdvisor page", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows how old each advice is", async () => {
    mount([advice()]);
    expect(await screen.findByTestId("advice-age")).toHaveTextContent(/advised \d+m ago|advised just now/);
  });

  it("a position the sweep has not advised yet reads 'not yet advised', not a blank badge or a NaN", async () => {
    mount([
      { symbol: "BANKNIFTY24OCT52000PE", strategy_group_id: null, action: null, reason: "not_yet_advised", advised_at: null },
    ]);
    expect(await screen.findByText("not yet advised")).toBeInTheDocument();
    expect(screen.getByText(/no advice yet/)).toBeInTheDocument();
    expect(screen.queryByText(/NaN|undefined|null/)).not.toBeInTheDocument();
  });

  it("flags stale advice loudly", async () => {
    mount([advice({ advised_at: new Date(Date.now() - 5 * 3_600_000).toISOString() })]);
    expect(await screen.findByTestId("advice-age")).toHaveTextContent(/stale/);
  });
});
