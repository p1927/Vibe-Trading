import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ModelAdapters } from "@/pages/ModelAdapters";
import { api } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, getModelAdapters: vi.fn(), updateModelAdapter: vi.fn() } };
});

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

function adapter(over: Record<string, unknown> = {}) {
  return {
    adapter_id: "nvidia-generative",
    kind: "generative",
    provider: "nvidia",
    model: "m",
    enabled: true,
    priority: 5,
    fallback_adapter_id: "minimax-generative",
    rate_limit: { rpm: 10, max_concurrent: 1 },
    ...over,
  };
}
const minimax = () =>
  adapter({ adapter_id: "minimax-generative", provider: "minimax", priority: 9, fallback_adapter_id: "nvidia-generative" });

beforeEach(() => {
  vi.clearAllMocks();
  mocked.getModelAdapters.mockResolvedValue({ status: "ok", adapters: [minimax(), adapter()] });
  mocked.updateModelAdapter.mockResolvedValue({ status: "ok", adapters: [minimax(), adapter()] });
});

describe("ModelAdapters page", () => {
  it("offers only the providers the registry returns (D394: no Anthropic/OpenRouter option)", async () => {
    render(<ModelAdapters />);
    await screen.findAllByText("Save");
    expect(document.body.textContent).not.toMatch(/anthropic|openrouter|claude/i);
    const options = [...document.querySelectorAll("option")].map((o) => o.textContent);
    expect(options.sort()).toEqual(["(none)", "(none)", "minimax-generative", "nvidia-generative"].sort());
  });

  it("shows MiniMax's request rate as read-only (shared account pace) and never sends it", async () => {
    render(<ModelAdapters />);
    await screen.findAllByText("Save");
    const rpmFields = screen.getAllByText("Requests / min").map((l) => l.parentElement!.querySelector("input")!);
    expect(rpmFields.filter((f) => f.disabled)).toHaveLength(1);
    fireEvent.click(screen.getAllByText("Save")[0]);
    await waitFor(() => expect(mocked.updateModelAdapter).toHaveBeenCalled());
    const [id, body] = mocked.updateModelAdapter.mock.calls[0];
    expect(id).toBe("minimax-generative");
    expect(body.rate_limit).toEqual({ max_concurrent: 1 });
  });

  it.each([
    ["0", /at least 1/],
    ["1.5", /whole number/],
    ["", /at least 1/],
  ])("blocks Save for requests/min %j instead of sending it", async (value, message) => {
    render(<ModelAdapters />);
    await screen.findAllByText("Save");
    const nvidiaRpm = screen
      .getAllByText("Requests / min")
      .map((l) => l.parentElement!.querySelector("input")!)
      .find((f) => !f.disabled)!;
    fireEvent.change(nvidiaRpm, { target: { value } });
    expect(screen.getByRole("alert")).toHaveTextContent(message);
    const save = screen.getAllByText("Save")[1] as HTMLButtonElement;
    expect(save).toBeDisabled();
  });

  it("sends an explicit null when the fallback is cleared to (none)", async () => {
    render(<ModelAdapters />);
    await screen.findAllByText("Save");
    const selects = document.querySelectorAll("select");
    fireEvent.change(selects[1], { target: { value: "" } });
    fireEvent.click(screen.getAllByText("Save")[1]);
    await waitFor(() => expect(mocked.updateModelAdapter).toHaveBeenCalled());
    expect(mocked.updateModelAdapter.mock.calls[0][1].fallback_adapter_id).toBeNull();
  });

  it("warns when no generative adapter is enabled", async () => {
    mocked.getModelAdapters.mockResolvedValue({
      status: "ok",
      adapters: [{ ...minimax(), enabled: false }, { ...adapter(), enabled: false }],
    });
    render(<ModelAdapters />);
    expect(await screen.findByText(/No generative adapter is enabled/)).toBeInTheDocument();
  });

  it("shows the load error instead of an empty page", async () => {
    mocked.getModelAdapters.mockRejectedValue(new Error("adapters 502"));
    render(<ModelAdapters />);
    expect(await screen.findByText("adapters 502")).toBeInTheDocument();
  });
});
