import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { Agent } from "../Agent";
import { useAgentStore } from "@/stores/agent";
import { ApiError } from "@/lib/api";
import { useLiveStatus } from "@/hooks/useLiveStatus";

const apiMock = vi.hoisted(() => ({
  getGoal: vi.fn(),
  getLLMSettings: vi.fn(),
  getRun: vi.fn(),
  getSessionMessages: vi.fn(),
  getLiveStatus: vi.fn(),
  getTradingConnectors: vi.fn(),
  checkTradingConnector: vi.fn(),
  getSessionProvenance: vi.fn(),
  uploadFile: vi.fn(),
  createSession: vi.fn(),
  sendMessage: vi.fn(),
  sseUrl: vi.fn((sid: string) => `/sessions/${sid}/events`),
}));

const sseMock = vi.hoisted(() => ({
  connect: vi.fn(),
  disconnect: vi.fn(),
  onStatusChange: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: apiMock };
});
vi.mock("@/hooks/useSSE", () => ({ useSSE: () => sseMock }));
vi.mock("@/components/chat/ModelRuntimeBar", () => ({ ModelRuntimeBar: () => null }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => { resolve = res; });
  return { promise, resolve };
}

const EMPTY_STATUS = { global_halted: false, brokers: [] };

beforeEach(() => {
  vi.clearAllMocks();
  useAgentStore.getState().reset();
  apiMock.getGoal.mockResolvedValue(null);
  apiMock.getLLMSettings.mockResolvedValue({ sse_timeout_seconds: 90, providers: [] });
  apiMock.getSessionMessages.mockResolvedValue([]);
  apiMock.getLiveStatus.mockResolvedValue(EMPTY_STATUS);
  apiMock.getTradingConnectors.mockResolvedValue({ selected_profile: null, profiles: [] });
  apiMock.checkTradingConnector.mockResolvedValue({ status: "ok" });
  apiMock.getSessionProvenance.mockResolvedValue({ sources: [] });
});

describe("useLiveStatus (the one /live/status poller)", () => {
  it("drops a response that arrives after a newer request was issued", async () => {
    const slow = deferred<typeof EMPTY_STATUS>();
    apiMock.getLiveStatus
      .mockReturnValueOnce(slow.promise) // mount poll, resolves last
      .mockResolvedValueOnce({ global_halted: true, brokers: [] }); // forced refresh, resolves first
    const { result } = renderHook(() => useLiveStatus());
    await act(async () => { await result.current.refresh(); });
    expect(result.current.liveStatus?.global_halted).toBe(true);
    await act(async () => { slow.resolve({ global_halted: false, brokers: [] }); });
    expect(result.current.liveStatus?.global_halted).toBe(true); // the stale "not halted" must not win
  });

  it("marks the snapshot stale on a transport error and hides on 404", async () => {
    const { result } = renderHook(() => useLiveStatus());
    await waitFor(() => expect(result.current.liveStatus).not.toBeNull());
    apiMock.getLiveStatus.mockRejectedValueOnce(new Error("boom"));
    await act(async () => { await result.current.refresh(); });
    expect(result.current.error).toBe("boom");
    expect(result.current.liveStatus).not.toBeNull(); // last snapshot kept, but flagged
    apiMock.getLiveStatus.mockRejectedValueOnce(new ApiError("nope", 404));
    await act(async () => { await result.current.refresh(); });
    expect(result.current.unavailable).toBe(true);
    expect(result.current.liveStatus).toBeNull();
  });

  it("polls /live/status once per Agent mount, not once per surface", async () => {
    const router = createMemoryRouter([{ path: "/", element: <Agent /> }], { initialEntries: ["/"] });
    render(<RouterProvider router={router} />);
    await waitFor(() => expect(apiMock.getLiveStatus).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 50));
    expect(apiMock.getLiveStatus).toHaveBeenCalledTimes(1);
  });
});

describe("Agent page", () => {
  it("adds one run_complete card per stored backtest reply (no duplicate row)", async () => {
    apiMock.getSessionMessages.mockResolvedValue([
      {
        message_id: "m1",
        session_id: "s1",
        role: "assistant",
        content: "Strategy execution completed.",
        created_at: "2026-08-01T00:00:00Z",
        tool_trail: [],
        metadata: { run_id: "run-1", metrics: { sharpe: 1.2 } },
      },
    ]);
    const router = createMemoryRouter([{ path: "/", element: <Agent /> }], { initialEntries: ["/?session=s1"] });
    render(<RouterProvider router={router} />);
    await waitFor(() => expect(useAgentStore.getState().messages.length).toBeGreaterThan(0));
    const cards = useAgentStore.getState().messages.filter((m) => m.type === "run_complete");
    expect(cards).toHaveLength(1);
  });

  it("sends an uploaded attachment with the prompt (button disabled state and Enter alike)", async () => {
    apiMock.uploadFile.mockResolvedValue({ filename: "notes.txt", file_path: "/uploads/notes.txt" });
    apiMock.createSession.mockResolvedValue({ session_id: "s9" });
    apiMock.sendMessage.mockResolvedValue({ attempt_id: "a1" });
    const router = createMemoryRouter([{ path: "/", element: <Agent /> }], { initialEntries: ["/"] });
    const { container } = render(<RouterProvider router={router} />);

    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: [new File(["x"], "notes.txt", { type: "text/plain" })] } });
    await screen.findByText("notes.txt");

    const box = screen.getByRole("textbox");
    fireEvent.change(box, { target: { value: "summarise this" } });
    fireEvent.keyDown(box, { key: "Enter" });

    await waitFor(() => expect(apiMock.sendMessage).toHaveBeenCalled());
    const sent = String(apiMock.sendMessage.mock.calls[0][1]);
    expect(sent).toContain("[Uploaded files: ");
    expect(sent).toContain("/uploads/notes.txt");
    expect(sent.endsWith("summarise this")).toBe(true);
    // The composer badge is cleared after send; the one left is the sent message's own attachment chip.
    await waitFor(() => expect(screen.queryAllByText("notes.txt")).toHaveLength(1));
  });
});
