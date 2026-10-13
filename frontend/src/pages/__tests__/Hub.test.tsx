import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Hub } from "@/pages/Hub";
import { api } from "@/lib/api";

vi.mock("@/components/NewsPipelineGraph", () => ({ NewsPipelineGraph: () => null }));
vi.mock("@/components/news/EventsCalendar", () => ({ EventsCalendar: () => null }));
vi.mock("@/components/news/FactorRegistryGaps", () => ({ FactorRegistryGaps: () => null }));
vi.mock("@/components/ObservabilityIssuesPanel", () => ({ ObservabilityIssuesPanel: () => null }));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getHubStatus: vi.fn(),
      getHubNewsPipelineConfig: vi.fn(),
      getObservabilitySummary: vi.fn(),
      updateHubNewsPipelineConfig: vi.fn(),
    },
  };
});

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

function status(hub: Record<string, unknown> = {}) {
  return { status: "ok", hub: { generated_at: "2026-10-13T00:00:00Z", gates: { hub_ready: true }, ...hub } };
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear(); // the schedule section's open/closed state persists there
  mocked.getHubStatus.mockResolvedValue(status());
  mocked.getHubNewsPipelineConfig.mockResolvedValue({ config: { relevance_min_confidence: 0.55 } });
  mocked.getObservabilitySummary.mockResolvedValue({ open_issue_count: 0 });
});

describe("Hub page", () => {
  it("D393: a closed LLM-Wiki is a deferral notice, never a paused pipeline", async () => {
    mocked.getHubStatus.mockResolvedValue(
      status({
        news_staging: {
          pipeline_paused: false,
          wiki_deferred: true,
          llm_wiki_ok: false,
          user_message: "Start LLM Wiki.app",
          queued: 3,
        },
      }),
    );
    render(<Hub />);
    const banner = await screen.findByTestId("wiki-deferred-banner");
    expect(banner).toHaveTextContent("Start LLM Wiki.app");
    expect(banner).toHaveTextContent(/news ingest, verification and story creation keep running/);
    expect(screen.queryByText("News distillation paused")).toBeNull();
  });

  it("a pause (missing adapter) never blames LLM-Wiki", async () => {
    mocked.getHubStatus.mockResolvedValue(
      status({ news_staging: { pipeline_paused: true, pause_reason: "no_generative_adapter", queued: 1 } }),
    );
    render(<Hub />);
    expect(await screen.findByText("News distillation paused")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/check LLM-Wiki/);
  });

  it("shows why the schedule card is missing when the config call fails", async () => {
    mocked.getHubNewsPipelineConfig.mockRejectedValue(new Error("config 503"));
    render(<Hub />);
    expect(await screen.findByRole("alert")).toHaveTextContent("News pipeline schedule: config 503");
    expect(screen.queryByText("News pipeline schedule")).toBeNull();
  });

  it("shows em-dashes, not zeros, for counts the hub did not report", async () => {
    mocked.getHubStatus.mockResolvedValue(status());
    render(<Hub />);
    expect(await screen.findByText(/— pending in queue/)).toBeInTheDocument();
    expect(screen.getByText(/— staging live/)).toBeInTheDocument();
    expect(screen.getByText(/Entity pipeline:\s*unknown/)).toBeInTheDocument();
    expect(
      screen.getByText((_, el) => el?.tagName === "P" && /Enabled:\s*unknown/.test(el.textContent ?? "")),
    ).toBeInTheDocument();
  });

  it("a real 0 stays 0 for a count the hub did report", async () => {
    mocked.getHubStatus.mockResolvedValue(status({ news_staging: { queued: 0 }, news_inventory: { pending_count: 0 } }));
    render(<Hub />);
    expect(await screen.findByText(/0 pending in queue/)).toBeInTheDocument();
  });

  it("lets min relevance confidence be set to 0 instead of snapping back to the default", async () => {
    render(<Hub />);
    fireEvent.click(await screen.findByText("Show schedule settings"));
    const field = (await screen.findByText("Min relevance confidence")).parentElement!.querySelector("input")!;
    fireEvent.change(field, { target: { value: "0" } });
    expect(field).toHaveValue(0);
  });

  it("a reload (the 30s poll runs the same load) does not overwrite an unsaved schedule draft", async () => {
    render(<Hub />);
    fireEvent.click(await screen.findByText("Show schedule settings"));
    const field = (await screen.findByText("Min relevance confidence")).parentElement!.querySelector("input")!;
    fireEvent.change(field, { target: { value: "0.9" } });
    mocked.getHubNewsPipelineConfig.mockResolvedValue({ config: { relevance_min_confidence: 0.1 } });
    const before = mocked.getHubNewsPipelineConfig.mock.calls.length;
    fireEvent.click(screen.getByText("Refresh"));
    await waitFor(() => expect(mocked.getHubNewsPipelineConfig.mock.calls.length).toBeGreaterThan(before));
    await new Promise((r) => setTimeout(r, 50));
    expect(field).toHaveValue(0.9);
  });
});
