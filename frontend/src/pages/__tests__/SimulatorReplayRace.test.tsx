import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Simulator } from "@/pages/Simulator";
import { api } from "@/lib/api";

// Every api call stays pending except the two replay reads under test.
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  const pending = () => new Promise(() => {});
  const status = vi.fn();
  const start = vi.fn();
  return {
    ...actual,
    api: new Proxy(actual.api as Record<string, unknown>, {
      get: (target, prop: string) =>
        prop === "getReplayStatus"
          ? status
          : prop === "startReplay"
            ? start
            : typeof target[prop] === "function"
              ? vi.fn(pending)
              : target[prop],
    }),
  };
});
// The real clock panel owns the Stop control; a stub keeps this test about the page's reconcile.
vi.mock("@/components/simulator/SimulatorReplayClock", () => ({
  SimulatorReplayClock: ({ onStop }: { onStop: () => void }) => (
    <button data-testid="stub-stop" onClick={onStop}>
      stop
    </button>
  ),
}));

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;
const ARMED = { replay: { mode: "replay", clock: { replay_date: "2024-04-15" } } };
const LIVE = { replay: { mode: "live" } };
const deferred = <T,>() => {
  let resolve!: (v: T) => void;
  let reject!: (e: Error) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
};

async function mountArmed() {
  mocked.getReplayStatus.mockResolvedValueOnce(ARMED);
  vi.useFakeTimers({ shouldAdvanceTime: true });
  render(
    <MemoryRouter>
      <Simulator />
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByTestId("simulator-arm")).toHaveTextContent("Armed · 2024-04-15"));
}

describe("Simulator replay-status reconcile is latest-wins", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => vi.useRealTimers());

  it("a poll sent before Stop that answers after it does not re-arm the badge", async () => {
    await mountArmed();
    const late = deferred<typeof ARMED>();
    mocked.getReplayStatus.mockReturnValueOnce(late.promise);
    await vi.advanceTimersByTimeAsync(20_500); // poll tick sent while still armed
    fireEvent.click(screen.getByTestId("stub-stop"));
    await waitFor(() => expect(screen.getByTestId("simulator-arm")).not.toHaveTextContent("Armed"));
    await act(async () => late.resolve(ARMED));
    expect(screen.getByTestId("simulator-arm")).not.toHaveTextContent("Armed");
  });

  it("a poll sent before Arm that answers 'live' after it does not clear the Armed badge", async () => {
    await mountArmed();
    fireEvent.click(screen.getByTestId("stub-stop"));
    await waitFor(() => expect(screen.getByTestId("simulator-arm")).not.toHaveTextContent("Armed"));
    const late = deferred<typeof LIVE>();
    mocked.getReplayStatus.mockReturnValueOnce(late.promise);
    await vi.advanceTimersByTimeAsync(20_500);
    mocked.startReplay.mockResolvedValue(ARMED);
    fireEvent.click(screen.getByTestId("simulator-arm"));
    await waitFor(() => expect(screen.getByTestId("simulator-arm")).toHaveTextContent("Armed"));
    await act(async () => late.resolve(LIVE));
    expect(screen.getByTestId("simulator-arm")).toHaveTextContent("Armed");
  });

  it("a poll that fails after a newer one answered does not flag the badge unconfirmed", async () => {
    await mountArmed();
    const slow = deferred<typeof ARMED>();
    mocked.getReplayStatus.mockReturnValueOnce(slow.promise).mockResolvedValue(ARMED);
    await vi.advanceTimersByTimeAsync(20_500); // slow tick
    await vi.advanceTimersByTimeAsync(20_000); // newer tick answers
    await act(async () => slow.reject(new Error("old poll died")));
    expect(screen.queryByTestId("simulator-replay-status-stale")).toBeNull();
  });
});
