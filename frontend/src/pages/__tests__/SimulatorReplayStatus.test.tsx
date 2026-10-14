import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Simulator } from "@/pages/Simulator";
import { api } from "@/lib/api";

// Every api call the page makes on mount stays pending except the replay-status read under test.
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  const pending = () => new Promise(() => {});
  const status = vi.fn();
  return {
    ...actual,
    api: new Proxy(actual.api as Record<string, unknown>, {
      get: (target, prop: string) =>
        prop === "getReplayStatus" ? status : typeof target[prop] === "function" ? vi.fn(pending) : target[prop],
    }),
  };
});

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

describe("Simulator replay-status reconcile", () => {
  beforeEach(() => vi.clearAllMocks());

  it("a failed status read after an armed read flags the Armed badge as unconfirmed", async () => {
    mocked.getReplayStatus.mockResolvedValueOnce({
      replay: { mode: "replay", clock: { replay_date: "2024-04-15" } },
    });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(
      <MemoryRouter>
        <Simulator />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByTestId("simulator-arm")).toHaveTextContent("Armed · 2024-04-15"));
    expect(screen.queryByTestId("simulator-replay-status-stale")).toBeNull();
    mocked.getReplayStatus.mockRejectedValue(new Error("service restarting"));
    await vi.advanceTimersByTimeAsync(20_500);
    expect(await screen.findByTestId("simulator-replay-status-stale")).toHaveTextContent("service restarting");
    vi.useRealTimers();
  });
});
