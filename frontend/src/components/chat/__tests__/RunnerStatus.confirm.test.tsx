import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RunnerStatus } from "../RunnerStatus";
import type { LiveBrokerStatus, LiveStatus } from "@/lib/api";
import i18n from "@/i18n";

const apiMock = vi.hoisted(() => ({
  authorizeLive: vi.fn(),
  startLiveRunner: vi.fn(),
  stopLiveRunner: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: apiMock };
});
vi.mock("sonner", () => ({ toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() } }));

function runnerStatus(alive: boolean): LiveStatus {
  const broker: LiveBrokerStatus = {
    auth: { broker: "ibkr", oauth_token_present: true, is_live_broker: true, transport: "remote_mcp" },
    mandate: {
      expires_at: new Date(Date.now() + 86_400_000 * 3).toISOString(),
      limits: { max_order_notional_usd: 100, max_trades_per_day: 2, max_leverage: 1 },
    } as never,
    runner: { broker: "ibkr", alive, last_tick: null },
    halted: false,
  };
  return { global_halted: false, brokers: [broker] };
}

function open(alive: boolean) {
  const onRefresh = vi.fn();
  render(<RunnerStatus status={runnerStatus(alive)} onRefresh={onRefresh} />);
  fireEvent.click(screen.getByRole("button", { name: /connector runtime/i }));
  return onRefresh;
}

describe("RunnerStatus start/stop runner", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en");
    apiMock.authorizeLive.mockResolvedValue({ instruction: "x" });
    apiMock.startLiveRunner.mockResolvedValue({});
    apiMock.stopLiveRunner.mockResolvedValue({});
  });
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("starting the live runner asks first and sends nothing on cancel", async () => {
    open(false);
    fireEvent.click(screen.getByRole("button", { name: /start runner/i }));
    expect(apiMock.startLiveRunner).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(apiMock.startLiveRunner).not.toHaveBeenCalled();
  });

  it("confirming starts it exactly once even on a double click", async () => {
    open(false);
    fireEvent.click(screen.getByRole("button", { name: /start runner/i }));
    const dialog = await screen.findByRole("alertdialog");
    const confirmButton = dialog.querySelectorAll("button")[1] as HTMLButtonElement;
    fireEvent.click(confirmButton);
    fireEvent.click(confirmButton);
    await waitFor(() => expect(apiMock.startLiveRunner).toHaveBeenCalledTimes(1));
    await act(async () => {});
    expect(apiMock.startLiveRunner).toHaveBeenCalledTimes(1);
  });

  it("two quick clicks on Start runner open one prompt, not two starts", async () => {
    open(false);
    const start = screen.getByRole("button", { name: /start runner/i });
    fireEvent.click(start);
    fireEvent.click(start);
    expect(screen.getAllByRole("alertdialog")).toHaveLength(1);
    expect(apiMock.startLiveRunner).not.toHaveBeenCalled();
  });

  it("stopping a running runner needs no prompt (it only reduces risk)", async () => {
    open(true);
    fireEvent.click(screen.getByRole("button", { name: /stop runner/i }));
    await waitFor(() => expect(apiMock.stopLiveRunner).toHaveBeenCalledTimes(1));
  });
});
