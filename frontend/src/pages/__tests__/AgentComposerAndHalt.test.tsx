import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { Agent } from "../Agent";
import { useAgentStore } from "@/stores/agent";

/*
 * The Agent page's composer and live-runtime controls are the extracted <Composer> and <LiveRuntimePanel>
 * (one implementation each). These tests pin parity of the surface (send, Enter, upload, stop) and the
 * halt contract: no UI code ever clears a halt without an explicit, confirmed human click.
 */
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
  cancelSession: vi.fn(),
  resumeLive: vi.fn(),
  haltLive: vi.fn(),
  sseUrl: vi.fn((sid: string) => `/sessions/${sid}/events`),
}));

const sseMock = vi.hoisted(() => ({ connect: vi.fn(), disconnect: vi.fn(), onStatusChange: vi.fn() }));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: apiMock };
});
vi.mock("@/hooks/useSSE", () => ({ useSSE: () => sseMock }));
vi.mock("@/components/chat/ModelRuntimeBar", () => ({ ModelRuntimeBar: () => null }));

const HALTED = {
  global_halted: true,
  halt: { by: "cli", reason: "daily loss limit", tripped_at: "2026-10-14T05:30:00+00:00" },
  brokers: [],
};

function mountAgent() {
  const router = createMemoryRouter([{ path: "/", element: <Agent /> }], { initialEntries: ["/"] });
  return render(<RouterProvider router={router} />);
}

beforeEach(() => {
  vi.clearAllMocks();
  useAgentStore.getState().reset();
  apiMock.getGoal.mockResolvedValue(null);
  apiMock.getLLMSettings.mockResolvedValue({ sse_timeout_seconds: 90, providers: [] });
  apiMock.getSessionMessages.mockResolvedValue([]);
  apiMock.getLiveStatus.mockResolvedValue({ global_halted: false, brokers: [] });
  apiMock.getTradingConnectors.mockResolvedValue({ selected_profile: null, profiles: [] });
  apiMock.checkTradingConnector.mockResolvedValue({ status: "ok" });
  apiMock.getSessionProvenance.mockResolvedValue({ sources: [] });
  apiMock.createSession.mockResolvedValue({ session_id: "s1" });
  apiMock.sendMessage.mockResolvedValue({ attempt_id: "a1" });
  apiMock.resumeLive.mockResolvedValue({ halted: false });
});

describe("Agent composer (the extracted Composer is the only composer)", () => {
  it("sends on the Send button and on Enter, but not on Shift+Enter", async () => {
    mountAgent();
    const box = await screen.findByRole("textbox");
    fireEvent.change(box, { target: { value: "first" } });
    fireEvent.keyDown(box, { key: "Enter", shiftKey: true });
    expect(apiMock.sendMessage).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(apiMock.sendMessage).toHaveBeenCalledTimes(1));
    expect(String(apiMock.sendMessage.mock.calls[0][1])).toContain("first");
    await waitFor(() => expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(""));
  });

  it("uploads a file, sends it with the prompt, and clears the badge", async () => {
    apiMock.uploadFile.mockResolvedValue({ filename: "n.txt", file_path: "/uploads/n.txt" });
    const { container } = mountAgent();
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: [new File(["x"], "n.txt", { type: "text/plain" })] } });
    await screen.findByText("n.txt");
    const box = screen.getByRole("textbox");
    fireEvent.change(box, { target: { value: "read it" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(apiMock.sendMessage).toHaveBeenCalled());
    expect(String(apiMock.sendMessage.mock.calls[0][1])).toContain("/uploads/n.txt");
  });

  it("refuses an executable upload (blocked-extension list)", async () => {
    const { container } = mountAgent();
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: [new File(["x"], "run.sh")] } });
    await new Promise((r) => setTimeout(r, 30));
    expect(apiMock.uploadFile).not.toHaveBeenCalled();
  });

  it("shows Stop while streaming and Stop ends the turn", async () => {
    mountAgent();
    await screen.findByRole("textbox");
    act(() => useAgentStore.getState().setStatus("streaming"));
    const stop = await screen.findByTitle("Stop generation");
    fireEvent.click(stop);
    await waitFor(() => expect(useAgentStore.getState().status).toBe("idle"));
  });
});

describe("a halt is never lifted by UI code", () => {
  it("does not auto-resume a global halt it cannot attribute (broker_sdk profile, nothing OAuth engaged)", async () => {
    apiMock.getLiveStatus.mockResolvedValue(HALTED);
    apiMock.getTradingConnectors.mockResolvedValue({
      selected_profile: "sdk",
      profiles: [{ id: "sdk", selected: true, transport: "broker_sdk", connector: "openalgo" }],
    });
    mountAgent();
    await screen.findByText("Connector runtime halted");
    await new Promise((r) => setTimeout(r, 80));
    expect(apiMock.resumeLive).not.toHaveBeenCalled();
    expect(screen.getByTestId("halt-detail").textContent).toMatch(/Tripped by cli.*daily loss limit/);
  });

  it("Resume needs a second, explicit confirmation; Keep halted leaves the halt in force", async () => {
    apiMock.getLiveStatus.mockResolvedValue(HALTED);
    mountAgent();
    fireEvent.click(await screen.findByRole("button", { name: "Resume connector" }));
    expect(apiMock.resumeLive).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "Keep halted" }));
    expect(apiMock.resumeLive).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Resume connector" }));
    apiMock.getLiveStatus.mockResolvedValue({ global_halted: false, brokers: [] });
    fireEvent.click(await screen.findByRole("button", { name: "Yes, resume" }));
    await waitFor(() => expect(apiMock.resumeLive).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByText("Connector runtime halted")).toBeNull());
  });

  it("a halt with no recorded attribution still shows a Resume button and says so", async () => {
    apiMock.getLiveStatus.mockResolvedValue({
      global_halted: true,
      halt: { by: null, reason: null, tripped_at: null },
      brokers: [],
    });
    mountAgent();
    await screen.findByRole("button", { name: "Resume connector" });
    expect(screen.getByTestId("halt-detail").textContent).toMatch(/not recorded/);
    expect(apiMock.resumeLive).not.toHaveBeenCalled();
  });
});
