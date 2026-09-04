import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Scheduled } from "@/pages/Scheduled";
import { ApiError, api, type ScheduledRun, type SchedulerRegistryEntry, type VerdictRecord } from "@/lib/api";

function renderScheduled(initialPath = "/scheduled") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Scheduled />
    </MemoryRouter>,
  );
}

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listScheduledRuns: vi.fn(),
      createScheduledRun: vi.fn(),
      deleteScheduledRun: vi.fn(),
      pauseScheduledRun: vi.fn(),
      resumeScheduledRun: vi.fn(),
      cancelScheduledRun: vi.fn(),
      triggerScheduledRun: vi.fn(),
      getScheduledRunPreview: vi.fn(),
      listSchedulerRegistry: vi.fn(),
      pauseSchedulerRegistryEntry: vi.fn(),
      resumeSchedulerRegistryEntry: vi.fn(),
      triggerSchedulerRegistryEntry: vi.fn(),
      pauseStockSimSchedulerEntry: vi.fn(),
      resumeStockSimSchedulerEntry: vi.fn(),
      triggerStockSimSchedulerEntry: vi.fn(),
      scheduledRunStreamUrl: vi.fn(),
    },
  };
});

const mocked = api as unknown as {
  listScheduledRuns: ReturnType<typeof vi.fn>;
  createScheduledRun: ReturnType<typeof vi.fn>;
  deleteScheduledRun: ReturnType<typeof vi.fn>;
  pauseScheduledRun: ReturnType<typeof vi.fn>;
  resumeScheduledRun: ReturnType<typeof vi.fn>;
  cancelScheduledRun: ReturnType<typeof vi.fn>;
  triggerScheduledRun: ReturnType<typeof vi.fn>;
  getScheduledRunPreview: ReturnType<typeof vi.fn>;
  listSchedulerRegistry: ReturnType<typeof vi.fn>;
  pauseSchedulerRegistryEntry: ReturnType<typeof vi.fn>;
  resumeSchedulerRegistryEntry: ReturnType<typeof vi.fn>;
  triggerSchedulerRegistryEntry: ReturnType<typeof vi.fn>;
  pauseStockSimSchedulerEntry: ReturnType<typeof vi.fn>;
  resumeStockSimSchedulerEntry: ReturnType<typeof vi.fn>;
  triggerStockSimSchedulerEntry: ReturnType<typeof vi.fn>;
  scheduledRunStreamUrl: ReturnType<typeof vi.fn>;
};

// jsdom has no EventSource; LiveLogTail only needs enough of the interface to
// mount without throwing — its own live-stream behavior is covered by
// LiveLogTail.test.tsx (a plain event-driven unit test of the component).
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onopen: (() => void) | null = null;
  listeners: Record<string, ((e: unknown) => void)[]> = {};
  closed = false;
  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, handler: (e: unknown) => void) {
    (this.listeners[type] ??= []).push(handler);
  }
  close() {
    this.closed = true;
  }
}

function run(overrides: Partial<ScheduledRun> = {}): ScheduledRun {
  return {
    id: "auckland-scan",
    prompt: "pre-open scan of NZX names",
    schedule: "30 23 * * 1-5",
    next_run_at: 1_790_000_000_000,
    status: "pending",
    created_at: 1_780_000_000_000,
    last_run_at: null,
    consecutive_failures: 0,
    last_error: null,
    failure_kind: null,
    config: {},
    timezone: "Pacific/Auckland",
    delivery_channel: null,
    delivery_target: null,
    delivery_status: "none",
    delivery_error: null,
    delivery_updated_at: null,
    last_verdict: null,
    paused: false,
    auto_paused_reason: null,
    section: "general",
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocked.listScheduledRuns.mockResolvedValue([]);
  mocked.listSchedulerRegistry.mockResolvedValue({ status: "ok", entries: [], sources: {} });
  mocked.scheduledRunStreamUrl.mockResolvedValue("http://test/scheduled-runs/x/stream");
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});

describe("Scheduled page", () => {
  it("renders the stored local cadence and timezone without UTC conversion", async () => {
    mocked.listScheduledRuns.mockResolvedValue([run()]);
    renderScheduled();

    expect(await screen.findByText("Mon–Fri at 23:30")).toBeInTheDocument();
    const row = screen.getByRole("listitem");
    expect(within(row).getByText("Pacific/Auckland")).toBeInTheDocument();
    expect(within(row).getByText("pre-open scan of NZX names")).toBeInTheDocument();
  });

  it("shows legacy timezone-less jobs as UTC", async () => {
    mocked.listScheduledRuns.mockResolvedValue([run({ timezone: null, schedule: "0 9 * * *" })]);
    renderScheduled();

    expect(await screen.findByText("Daily at 09:00")).toBeInTheDocument();
    expect(within(screen.getByRole("listitem")).getByText("UTC")).toBeInTheDocument();
  });

  it("creates a job from the wall-clock composer", async () => {
    mocked.createScheduledRun.mockResolvedValue(run());
    renderScheduled();
    await screen.findByText(/No scheduled runs yet/);

    fireEvent.change(screen.getByLabelText("Research prompt"), {
      target: { value: "scan the pre-open movers" },
    });
    fireEvent.change(screen.getByLabelText("Local time"), { target: { value: "23:30" } });
    fireEvent.submit(screen.getByRole("button", { name: /Schedule run/ }));

    await waitFor(() =>
      expect(mocked.createScheduledRun).toHaveBeenCalledWith(
        expect.objectContaining({
          prompt: "scan the pre-open movers",
          schedule: "30 23 * * 1-5",
          timezone: expect.any(String),
        }),
      ),
    );
  });

  it("surfaces a validation error from the backend", async () => {
    mocked.createScheduledRun.mockRejectedValue(
      new ApiError("timezone 'Not/AZone' is not a recognized IANA timezone key", 422),
    );
    renderScheduled();
    await screen.findByText(/No scheduled runs yet/);

    fireEvent.change(screen.getByLabelText("Research prompt"), {
      target: { value: "scan" },
    });
    fireEvent.submit(screen.getByRole("button", { name: /Schedule run/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "not a recognized IANA timezone",
    );
  });

  it("deletes only after an explicit confirm click, and cancel disarms", async () => {
    mocked.listScheduledRuns.mockResolvedValue([run()]);
    mocked.deleteScheduledRun.mockResolvedValue(undefined);
    renderScheduled();

    fireEvent.click(await screen.findByRole("button", { name: /Delete scheduled run/ }));
    expect(mocked.deleteScheduledRun).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("button", { name: /Confirm deleting/ })).not.toBeInTheDocument();
    expect(mocked.deleteScheduledRun).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /Delete scheduled run/ }));
    fireEvent.click(screen.getByRole("button", { name: /Confirm deleting/ }));
    await waitFor(() =>
      expect(mocked.deleteScheduledRun).toHaveBeenCalledWith("auckland-scan"),
    );
  });

  it("blocks a whitespace-only prompt client-side", async () => {
    renderScheduled();
    await screen.findByText(/No scheduled runs yet/);

    fireEvent.change(screen.getByLabelText("Research prompt"), {
      target: { value: "   " },
    });
    fireEvent.submit(screen.getByRole("button", { name: /Schedule run/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enter a research prompt",
    );
    expect(mocked.createScheduledRun).not.toHaveBeenCalled();
  });
});


describe("briefing delivery", () => {
  it("keeps delivery off unless a channel is typed", async () => {
    renderScheduled();
    fireEvent.change(screen.getByLabelText(/prompt/i), {
      target: { value: "pre-open scan" },
    });
    fireEvent.click(screen.getByRole("button", { name: /schedule|create/i }));

    await waitFor(() => expect(mocked.createScheduledRun).toHaveBeenCalled());
    const body = mocked.createScheduledRun.mock.calls[0][0];
    expect(body.delivery_channel).toBeNull();
    expect(body.delivery_target).toBeNull();
  });

  it("refuses a channel with no target instead of sending nowhere", async () => {
    renderScheduled();
    fireEvent.change(screen.getByLabelText(/prompt/i), {
      target: { value: "pre-open scan" },
    });
    fireEvent.change(screen.getByLabelText(/deliver to channel/i), {
      target: { value: "telegram" },
    });
    fireEvent.click(screen.getByRole("button", { name: /schedule|create/i }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(mocked.createScheduledRun).not.toHaveBeenCalled();
  });

  it("sends the channel and target it was given", async () => {
    renderScheduled();
    fireEvent.change(screen.getByLabelText(/prompt/i), {
      target: { value: "pre-open scan" },
    });
    fireEvent.change(screen.getByLabelText(/deliver to channel/i), {
      target: { value: " telegram " },
    });
    fireEvent.change(screen.getByLabelText(/channel target/i), {
      target: { value: " chat-9 " },
    });
    fireEvent.click(screen.getByRole("button", { name: /schedule|create/i }));

    await waitFor(() => expect(mocked.createScheduledRun).toHaveBeenCalled());
    const body = mocked.createScheduledRun.mock.calls[0][0];
    expect(body.delivery_channel).toBe("telegram");
    expect(body.delivery_target).toBe("chat-9");
  });

  it("shows a monitor's delivery state, and shows nothing when it has none", async () => {
    mocked.listScheduledRuns.mockResolvedValue([
      run({ id: "with", delivery_channel: "telegram", delivery_status: "sent" }),
      run({ id: "without" }),
    ]);
    renderScheduled();

    expect(await screen.findByText(/delivered to telegram/i)).toBeInTheDocument();
    expect(screen.queryAllByText(/delivered to/i)).toHaveLength(1);
  });

  it("surfaces why a delivery failed rather than only that it did", async () => {
    mocked.listScheduledRuns.mockResolvedValue([
      run({
        delivery_channel: "telegram",
        delivery_status: "failed",
        delivery_error: "channel unreachable",
      }),
    ]);
    renderScheduled();

    expect(await screen.findByText(/channel unreachable/i)).toBeInTheDocument();
  });
});

describe("pause, resume, and cancel controls", () => {
  it("pauses a running-schedule job and shows the paused badge after refresh", async () => {
    mocked.listScheduledRuns
      .mockResolvedValueOnce([run({ id: "job-a", paused: false })])
      .mockResolvedValueOnce([run({ id: "job-a", paused: true })]);
    mocked.pauseScheduledRun.mockResolvedValue(run({ id: "job-a", paused: true }));
    renderScheduled();

    fireEvent.click(await screen.findByRole("button", { name: /Pause scheduled run/ }));

    await waitFor(() => expect(mocked.pauseScheduledRun).toHaveBeenCalledWith("job-a"));
    expect(await screen.findByText("Paused")).toBeInTheDocument();
  });

  it("resumes a paused job", async () => {
    mocked.listScheduledRuns.mockResolvedValue([run({ id: "job-a", paused: true })]);
    mocked.resumeScheduledRun.mockResolvedValue(run({ id: "job-a", paused: false }));
    renderScheduled();

    fireEvent.click(await screen.findByRole("button", { name: /Resume scheduled run/ }));

    await waitFor(() => expect(mocked.resumeScheduledRun).toHaveBeenCalledWith("job-a"));
  });

  it("shows a distinct auto-paused badge with the system reason, not a plain paused badge", async () => {
    mocked.listScheduledRuns.mockResolvedValue([
      run({ id: "job-a", paused: true, auto_paused_reason: "auto-paused: recovered on stack boot (stale running)" }),
    ]);
    renderScheduled();

    expect(await screen.findByText("Auto-paused (restart)")).toBeInTheDocument();
    expect(screen.queryByText("Paused")).not.toBeInTheDocument();
  });

  it("only offers cancel for a currently running job", async () => {
    mocked.listScheduledRuns.mockResolvedValue([run({ id: "job-a", status: "running" })]);
    mocked.cancelScheduledRun.mockResolvedValue(run({ id: "job-a", status: "cancelled" }));
    renderScheduled();

    fireEvent.click(await screen.findByRole("button", { name: /Cancel the running execution/ }));

    await waitFor(() => expect(mocked.cancelScheduledRun).toHaveBeenCalledWith("job-a"));
  });

  it("does not offer cancel for a non-running job", async () => {
    mocked.listScheduledRuns.mockResolvedValue([run({ id: "job-a", status: "pending" })]);
    renderScheduled();

    await screen.findByRole("listitem");
    expect(screen.queryByRole("button", { name: /Cancel the running execution/ })).not.toBeInTheDocument();
  });
});

describe("live-log-tail", () => {
  it("does not open a stream until the Logs toggle is clicked", async () => {
    mocked.listScheduledRuns.mockResolvedValue([run({ id: "job-a" })]);
    renderScheduled();

    await screen.findByRole("listitem");
    expect(mocked.scheduledRunStreamUrl).not.toHaveBeenCalled();
  });

  it("clicking Logs mounts the tail and requests a stream URL for that job", async () => {
    mocked.listScheduledRuns.mockResolvedValue([run({ id: "job-a" })]);
    renderScheduled();

    fireEvent.click(await screen.findByRole("button", { name: /Toggle live logs/i }));

    await waitFor(() => {
      expect(mocked.scheduledRunStreamUrl).toHaveBeenCalledWith("job-a");
    });
  });

  it("clicking Logs again collapses the tail and closes the connection", async () => {
    mocked.listScheduledRuns.mockResolvedValue([run({ id: "job-a" })]);
    renderScheduled();

    const toggle = await screen.findByRole("button", { name: /Toggle live logs/i });
    fireEvent.click(toggle);
    await waitFor(() => expect(FakeEventSource.instances.length).toBe(1));

    fireEvent.click(toggle);

    expect(screen.queryByText("Connecting...")).not.toBeInTheDocument();
  });

  it("expanding a second row's log tail collapses the first", async () => {
    mocked.listScheduledRuns.mockResolvedValue([run({ id: "job-a" }), run({ id: "job-b" })]);
    renderScheduled();

    const toggles = await screen.findAllByRole("button", { name: /Toggle live logs/i });
    fireEvent.click(toggles[0]);
    await waitFor(() => expect(mocked.scheduledRunStreamUrl).toHaveBeenCalledWith("job-a"));

    fireEvent.click(toggles[1]);
    await waitFor(() => expect(mocked.scheduledRunStreamUrl).toHaveBeenCalledWith("job-b"));

    // Only one tail is ever mounted at a time.
    expect(screen.getAllByText("Connecting...")).toHaveLength(1);
  });
});

describe("job details panel", () => {
  it("does not fetch a preview until the Details toggle is clicked", async () => {
    mocked.listScheduledRuns.mockResolvedValue([run({ id: "job-a" })]);
    renderScheduled();

    await screen.findByRole("listitem");
    expect(mocked.getScheduledRunPreview).not.toHaveBeenCalled();
  });

  it("clicking Details fetches and renders the description and preview items", async () => {
    mocked.listScheduledRuns.mockResolvedValue([run({ id: "job-a" })]);
    mocked.getScheduledRunPreview.mockResolvedValue({
      description: "Fetches news from RSS into hub news staging.",
      preview_available: true,
      preview_items: ["https://example.com/feed.xml"],
      preview_note: "mode=light, market=IN, ticker=NIFTY",
      preview_error: null,
    });
    renderScheduled();

    fireEvent.click(await screen.findByRole("button", { name: /Toggle details/i }));

    await waitFor(() => {
      expect(mocked.getScheduledRunPreview).toHaveBeenCalledWith("job-a");
    });
    expect(await screen.findByText("Fetches news from RSS into hub news staging.")).toBeInTheDocument();
    expect(await screen.findByText("https://example.com/feed.xml")).toBeInTheDocument();
  });

  it("shows an unavailable message when the job type has no live preview", async () => {
    mocked.listScheduledRuns.mockResolvedValue([run({ id: "job-a" })]);
    mocked.getScheduledRunPreview.mockResolvedValue({
      description: "Runs the morning hub calibration orchestrator.",
      preview_available: false,
      preview_items: [],
      preview_note: null,
      preview_error: null,
    });
    renderScheduled();

    fireEvent.click(await screen.findByRole("button", { name: /Toggle details/i }));

    expect(await screen.findByText(/Live preview isn.t available/i)).toBeInTheDocument();
  });

  it("clicking Details again collapses the panel", async () => {
    mocked.listScheduledRuns.mockResolvedValue([run({ id: "job-a" })]);
    mocked.getScheduledRunPreview.mockResolvedValue({
      description: "Runs the morning hub calibration orchestrator.",
      preview_available: false,
      preview_items: [],
      preview_note: null,
      preview_error: null,
    });
    renderScheduled();

    const toggle = await screen.findByRole("button", { name: /Toggle details/i });
    fireEvent.click(toggle);
    await screen.findByText("Runs the morning hub calibration orchestrator.");

    fireEvent.click(toggle);

    expect(screen.queryByText("Runs the morning hub calibration orchestrator.")).not.toBeInTheDocument();
  });
});

describe("section grouping", () => {
  it("shows a tab per section present in the list, with counts", async () => {
    mocked.listScheduledRuns.mockResolvedValue([
      run({ id: "a", section: "prediction" }),
      run({ id: "b", section: "prediction" }),
      run({ id: "c", section: "options" }),
    ]);
    renderScheduled();

    await screen.findByText("All (3)");
    expect(screen.getByText("Prediction (2)")).toBeInTheDocument();
    expect(screen.getByText("Options (1)")).toBeInTheDocument();
    expect(screen.queryByText(/Trade Data/)).not.toBeInTheDocument();
  });

  it("filters the list to the selected section", async () => {
    mocked.listScheduledRuns.mockResolvedValue([
      run({ id: "a", prompt: "index job", section: "prediction" }),
      run({ id: "b", prompt: "options job", section: "options" }),
    ]);
    renderScheduled();
    await screen.findByText("All (2)");

    fireEvent.click(screen.getByText("Options (1)"));

    expect(screen.getByText("options job")).toBeInTheDocument();
    expect(screen.queryByText("index job")).not.toBeInTheDocument();
  });

  it("preselects the section named in the URL (deep link from the Prediction panel)", async () => {
    mocked.listScheduledRuns.mockResolvedValue([
      run({ id: "a", prompt: "index job", section: "prediction" }),
      run({ id: "b", prompt: "options job", section: "options" }),
    ]);
    renderScheduled("/scheduled?section=prediction");

    expect(await screen.findByText("index job")).toBeInTheDocument();
    expect(screen.queryByText("options job")).not.toBeInTheDocument();
  });
});

describe("viewMode toggle", () => {
  it("shows the Scheduler view by default", async () => {
    mocked.listScheduledRuns.mockResolvedValue([run({ id: "a", section: "prediction" })]);
    renderScheduled();

    await screen.findByText("pre-open scan of NZX names");
    expect(screen.getByRole("tab", { name: "Scheduler" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByText("All (1)")).toBeInTheDocument();
  });

  it("switching to Jobs hides the section tab strip and shows the Jobs list", async () => {
    mocked.listScheduledRuns.mockResolvedValue([run({ id: "a", section: "prediction" })]);
    renderScheduled();
    await screen.findByText("pre-open scan of NZX names");

    fireEvent.click(screen.getByRole("tab", { name: "Jobs" }));

    expect(screen.queryByText("All (1)")).not.toBeInTheDocument();
    expect(screen.getByText(/Running now/)).toBeInTheDocument();
  });

  it("preselects Jobs view from the URL", async () => {
    mocked.listScheduledRuns.mockResolvedValue([run({ id: "a" })]);
    renderScheduled("/scheduled?view=jobs");

    await screen.findByText(/Running now/);
    expect(screen.getByRole("tab", { name: "Jobs" })).toHaveAttribute("aria-selected", "true");
  });

  it("toggling views never leaves more than one live-log connection open", async () => {
    mocked.listScheduledRuns.mockResolvedValue([run({ id: "a", status: "running" })]);
    renderScheduled("/scheduled?view=jobs");
    await screen.findByText(/Running now/);

    fireEvent.click(screen.getByLabelText(/Toggle live logs/));
    await waitFor(() =>
      expect(FakeEventSource.instances.filter((es) => !es.closed)).toHaveLength(1),
    );

    fireEvent.click(screen.getByRole("tab", { name: "Scheduler" }));
    await waitFor(() =>
      expect(FakeEventSource.instances.filter((es) => !es.closed)).toHaveLength(1),
    );

    fireEvent.click(screen.getByRole("tab", { name: "Jobs" }));
    await waitFor(() =>
      expect(FakeEventSource.instances.filter((es) => !es.closed)).toHaveLength(1),
    );
  });
});

function registryEntry(overrides: Partial<SchedulerRegistryEntry> = {}): SchedulerRegistryEntry {
  return {
    id: "B:recorder:us:index",
    source: "stock_simulator",
    section: "recorder:us",
    label: "us/index",
    description: null,
    schedule_kind: "recorder_interval",
    schedule_display: "every 300s (estimated)",
    enabled: true,
    status: "idle",
    cancel_requested: false,
    next_run_at: 1_790_000_300_000,
    last_run_at: 1_790_000_000_000,
    last_error: null,
    auto_paused_reason: null,
    supports_live_log: false,
    live_log_stream_url: null,
    controls: { pause: false, resume: false, cancel: false, delete: false, trigger_now: false },
    ...overrides,
  };
}

describe("cross-service registry entries (Mechanism B)", () => {
  it("shows a read-only recorder row grouped under its own dynamic section", async () => {
    mocked.listSchedulerRegistry.mockResolvedValue({
      status: "ok",
      entries: [registryEntry()],
      sources: { stock_simulator: { status: "ok" } },
    });
    renderScheduled();

    await screen.findByText("All (1)");
    expect(screen.getByText("Recorder (US) (1)")).toBeInTheDocument();
    expect(screen.getByText("us/index")).toBeInTheDocument();
    expect(screen.getByText("read-only")).toBeInTheDocument();
  });

  it("degrades to no extra rows when the cross-service call fails", async () => {
    mocked.listSchedulerRegistry.mockRejectedValue(new Error("unreachable"));
    mocked.listScheduledRuns.mockResolvedValue([run({ id: "a" })]);
    renderScheduled();

    // The page's own jobs still render; no crash, no dangling error state
    // from the registry call.
    expect(await screen.findByText("All (1)")).toBeInTheDocument();
    expect(screen.queryByText(/Recorder/)).not.toBeInTheDocument();
  });

  it("filtering to a recorder section hides scheduled-run rows and vice versa", async () => {
    mocked.listScheduledRuns.mockResolvedValue([run({ id: "a", prompt: "index job", section: "prediction" })]);
    mocked.listSchedulerRegistry.mockResolvedValue({
      status: "ok",
      entries: [registryEntry()],
      sources: { stock_simulator: { status: "ok" } },
    });
    renderScheduled();
    await screen.findByText("All (2)");

    fireEvent.click(screen.getByText("Recorder (US) (1)"));
    expect(screen.getByText("us/index")).toBeInTheDocument();
    expect(screen.queryByText("index job")).not.toBeInTheDocument();
  });

  it("shows a Logs toggle once the recorder has a live_log_stream_url", async () => {
    mocked.listSchedulerRegistry.mockResolvedValue({
      status: "ok",
      entries: [
        registryEntry({
          supports_live_log: true,
          live_log_stream_url: "http://sim.example.com/scheduler-runs/us/stream?token=tok",
        }),
      ],
      sources: { stock_simulator: { status: "ok" } },
    });
    renderScheduled();

    expect(await screen.findByText("us/index")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Toggle live logs/i })).toBeInTheDocument();
  });

  it("clicking the recorder's Logs toggle mounts the tail without an api round-trip", async () => {
    mocked.listSchedulerRegistry.mockResolvedValue({
      status: "ok",
      entries: [
        registryEntry({
          supports_live_log: true,
          live_log_stream_url: "http://sim.example.com/scheduler-runs/us/stream?token=tok",
        }),
      ],
      sources: { stock_simulator: { status: "ok" } },
    });
    renderScheduled();

    fireEvent.click(await screen.findByRole("button", { name: /Toggle live logs/i }));

    await waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
    expect(FakeEventSource.instances[0].url).toBe(
      "http://sim.example.com/scheduler-runs/us/stream?token=tok",
    );
    // Mechanism B's URL is already absolute (stamped server-side) — no
    // ticket-minting round-trip through vibetrading-agent's api.ts needed.
    expect(mocked.scheduledRunStreamUrl).not.toHaveBeenCalled();
  });

  it("does not show a Logs toggle when live_log_stream_url is absent", async () => {
    mocked.listSchedulerRegistry.mockResolvedValue({
      status: "ok",
      entries: [registryEntry()],
      sources: { stock_simulator: { status: "ok" } },
    });
    renderScheduled();

    await screen.findByText("us/index");
    expect(screen.queryByRole("button", { name: /Toggle live logs/i })).not.toBeInTheDocument();
  });

  it("shows Pause for an active, pausable stock_simulator category", async () => {
    mocked.listSchedulerRegistry.mockResolvedValue({
      status: "ok",
      entries: [registryEntry({ controls: { pause: true, resume: false, cancel: false, delete: false, trigger_now: true } })],
      sources: { stock_simulator: { status: "ok" } },
    });
    renderScheduled();

    expect(await screen.findByRole("button", { name: /pause/i })).toBeInTheDocument();
    expect(screen.queryByText("read-only")).not.toBeInTheDocument();
  });

  it("pausing an active stock_simulator category calls the API with the full composite id, not a stripped one", async () => {
    mocked.listSchedulerRegistry.mockResolvedValue({
      status: "ok",
      entries: [registryEntry({ controls: { pause: true, resume: false, cancel: false, delete: false, trigger_now: true } })],
      sources: { stock_simulator: { status: "ok" } },
    });
    mocked.pauseStockSimSchedulerEntry.mockResolvedValue({ status: "ok" });
    renderScheduled();

    fireEvent.click(await screen.findByRole("button", { name: /pause/i }));

    await waitFor(() => {
      expect(mocked.pauseStockSimSchedulerEntry).toHaveBeenCalledWith("B:recorder:us:index");
    });
    expect(mocked.pauseSchedulerRegistryEntry).not.toHaveBeenCalled();
  });

  it("resuming a paused stock_simulator category calls the resume API", async () => {
    mocked.listSchedulerRegistry.mockResolvedValue({
      status: "ok",
      entries: [
        registryEntry({
          enabled: false,
          status: "paused",
          schedule_display: "paused (was every 300s)",
          controls: { pause: false, resume: true, cancel: false, delete: false, trigger_now: false },
        }),
      ],
      sources: { stock_simulator: { status: "ok" } },
    });
    mocked.resumeStockSimSchedulerEntry.mockResolvedValue({ status: "ok" });
    renderScheduled();

    fireEvent.click(await screen.findByRole("button", { name: /resume/i }));

    await waitFor(() => {
      expect(mocked.resumeStockSimSchedulerEntry).toHaveBeenCalledWith("B:recorder:us:index");
    });
  });

  it("shows and dispatches Run now for a triggerable stock_simulator category", async () => {
    mocked.listSchedulerRegistry.mockResolvedValue({
      status: "ok",
      entries: [registryEntry({ controls: { pause: true, resume: false, cancel: false, delete: false, trigger_now: true } })],
      sources: { stock_simulator: { status: "ok" } },
    });
    mocked.triggerStockSimSchedulerEntry.mockResolvedValue({ status: "ok" });
    renderScheduled();

    fireEvent.click(await screen.findByRole("button", { name: /run.*now/i }));

    await waitFor(() => {
      expect(mocked.triggerStockSimSchedulerEntry).toHaveBeenCalledWith("B:recorder:us:index");
    });
  });

  it("does not show Run now when trigger_now is false", async () => {
    mocked.listSchedulerRegistry.mockResolvedValue({
      status: "ok",
      entries: [registryEntry()],
      sources: { stock_simulator: { status: "ok" } },
    });
    renderScheduled();

    await screen.findByText("us/index");
    expect(screen.queryByRole("button", { name: /run.*now/i })).not.toBeInTheDocument();
  });
});

function openalgoEntry(overrides: Partial<SchedulerRegistryEntry> = {}): SchedulerRegistryEntry {
  return {
    id: "C:flow:wf_1",
    source: "openalgo",
    section: "flow",
    label: "wf_1",
    description: null,
    schedule_kind: "apscheduler_trigger",
    schedule_display: "interval[0:05:00]",
    enabled: true,
    status: "idle",
    cancel_requested: false,
    next_run_at: 1_790_000_300_000,
    last_run_at: null,
    last_error: null,
    auto_paused_reason: null,
    supports_live_log: false,
    live_log_stream_url: null,
    controls: { pause: true, resume: true, cancel: false, delete: false, trigger_now: false },
    ...overrides,
  };
}

describe("cross-service registry entries (Mechanism C)", () => {
  it("shows an active openalgo job under its own dynamic section with a Pause button", async () => {
    mocked.listSchedulerRegistry.mockResolvedValue({
      status: "ok",
      entries: [openalgoEntry()],
      sources: { openalgo: { status: "ok" } },
    });
    renderScheduled();

    await screen.findByText("All (1)");
    expect(screen.getByText("Flow (1)")).toBeInTheDocument();
    expect(screen.getByText("wf_1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /pause/i })).toBeInTheDocument();
    expect(screen.queryByText("read-only")).not.toBeInTheDocument();
  });

  it("pausing an active openalgo job calls the API with the raw source and job id", async () => {
    mocked.listSchedulerRegistry.mockResolvedValue({
      status: "ok",
      entries: [openalgoEntry()],
      sources: { openalgo: { status: "ok" } },
    });
    mocked.pauseSchedulerRegistryEntry.mockResolvedValue({ status: "ok" });
    renderScheduled();

    fireEvent.click(await screen.findByRole("button", { name: /pause/i }));

    await waitFor(() => {
      expect(mocked.pauseSchedulerRegistryEntry).toHaveBeenCalledWith("flow", "wf_1");
    });
  });

  it("resuming a paused (disabled) openalgo job calls the resume API", async () => {
    mocked.listSchedulerRegistry.mockResolvedValue({
      status: "ok",
      entries: [openalgoEntry({ id: "C:historify:sched_2", section: "historify", enabled: false })],
      sources: { openalgo: { status: "ok" } },
    });
    mocked.resumeSchedulerRegistryEntry.mockResolvedValue({ status: "ok" });
    renderScheduled();

    fireEvent.click(await screen.findByRole("button", { name: /resume/i }));

    await waitFor(() => {
      expect(mocked.resumeSchedulerRegistryEntry).toHaveBeenCalledWith("historify", "sched_2");
    });
  });

  it("triggering an openalgo job calls the trigger API with the raw source and job id", async () => {
    mocked.listSchedulerRegistry.mockResolvedValue({
      status: "ok",
      entries: [openalgoEntry({ controls: { pause: true, resume: true, cancel: false, delete: false, trigger_now: true } })],
      sources: { openalgo: { status: "ok" } },
    });
    mocked.triggerSchedulerRegistryEntry.mockResolvedValue({ status: "ok" });
    renderScheduled();

    fireEvent.click(await screen.findByRole("button", { name: /run.*now/i }));

    await waitFor(() => {
      expect(mocked.triggerSchedulerRegistryEntry).toHaveBeenCalledWith("flow", "wf_1");
    });
  });

  it("a read-only stock_simulator row and a controllable openalgo row can coexist", async () => {
    mocked.listSchedulerRegistry.mockResolvedValue({
      status: "ok",
      entries: [registryEntry(), openalgoEntry()],
      sources: { stock_simulator: { status: "ok" }, openalgo: { status: "ok" } },
    });
    renderScheduled();

    await screen.findByText("All (2)");
    expect(screen.getByText("read-only")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /pause/i })).toBeInTheDocument();
  });

  it("shows a Logs toggle for a flow/historify job once it has a live_log_stream_url", async () => {
    mocked.listSchedulerRegistry.mockResolvedValue({
      status: "ok",
      entries: [
        openalgoEntry({
          supports_live_log: true,
          live_log_stream_url:
            "http://openalgo.example.com/api/v1/scheduler/registry/flow/wf_1/stream?apikey=tok",
        }),
      ],
      sources: { openalgo: { status: "ok" } },
    });
    renderScheduled();

    expect(await screen.findByText("wf_1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Toggle live logs/i })).toBeInTheDocument();
  });

  it("clicking a flow job's Logs toggle mounts the tail against its absolute URL", async () => {
    mocked.listSchedulerRegistry.mockResolvedValue({
      status: "ok",
      entries: [
        openalgoEntry({
          supports_live_log: true,
          live_log_stream_url:
            "http://openalgo.example.com/api/v1/scheduler/registry/flow/wf_1/stream?apikey=tok",
        }),
      ],
      sources: { openalgo: { status: "ok" } },
    });
    renderScheduled();

    fireEvent.click(await screen.findByRole("button", { name: /Toggle live logs/i }));

    await waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
    expect(FakeEventSource.instances[0].url).toBe(
      "http://openalgo.example.com/api/v1/scheduler/registry/flow/wf_1/stream?apikey=tok",
    );
  });

  it("does not show a Logs toggle for strategy/chartink/python_strategy jobs yet", async () => {
    mocked.listSchedulerRegistry.mockResolvedValue({
      status: "ok",
      entries: [openalgoEntry({ section: "strategy", id: "C:strategy:job_1" })],
      sources: { openalgo: { status: "ok" } },
    });
    renderScheduled();

    await screen.findByText("wf_1");
    expect(screen.queryByRole("button", { name: /Toggle live logs/i })).not.toBeInTheDocument();
  });
});

function verdict(overrides: Partial<VerdictRecord> = {}): VerdictRecord {
  return {
    session_id: "sess-1",
    recorded_at: 1_789_000_000_000,
    parse: "ok",
    outcome: "FLAT",
    items: [{ symbol: "600519.SH", state: "FLAT", reason: "band held" }],
    previous: null,
    ...overrides,
  };
}

describe("Scheduled page verdict cell", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.listScheduledRuns.mockResolvedValue([]);
  });

  it("renders the latest verdict with its delta and the recorded time", async () => {
    mocked.listScheduledRuns.mockResolvedValue([
      run({
        last_verdict: verdict({
          outcome: "DRIFT",
          items: [{ symbol: "600519.SH", state: "DRIFT", reason: "band crossed" }],
          previous: verdict({ session_id: "sess-0", outcome: "FLAT", recorded_at: 1_788_000_000_000 }),
        }),
      }),
    ]);
    renderScheduled();

    expect(await screen.findByText(/600519.SH DRIFT/)).toBeInTheDocument();
    expect(screen.getByText(/FLAT → DRIFT/)).toBeInTheDocument();
    expect(screen.getByText(/as of/)).toBeInTheDocument();
  });

  it("shows an explicit empty state when no run has recorded one", async () => {
    mocked.listScheduledRuns.mockResolvedValue([run({ last_verdict: null })]);
    renderScheduled();

    expect(await screen.findByText("No verdict yet")).toBeInTheDocument();
  });

  it("renders nothing for ad-hoc monitors permanently at no_verdict_section", async () => {
    mocked.listScheduledRuns.mockResolvedValue([run({ last_verdict: verdict({ parse: "no_verdict_section", items: [] }) })]);
    renderScheduled();

    await screen.findByRole("listitem");
    expect(screen.queryByText(/No verdict yet/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("verdict-line-auckland-scan")).toBeNull();
  });

  it("never shows a wrong verdict: a malformed section reads as unreadable", async () => {
    mocked.listScheduledRuns.mockResolvedValue([run({ last_verdict: verdict({ parse: "contract_violation", items: [] }) })]);
    renderScheduled();

    expect(await screen.findByText("Latest verdict unreadable")).toBeInTheDocument();
  });

  it("reads an empty item list as a real 'no calls' answer", async () => {
    mocked.listScheduledRuns.mockResolvedValue([run({ last_verdict: verdict({ items: [] }) })]);
    renderScheduled();

    expect(await screen.findByText(/No calls/)).toBeInTheDocument();
  });
});
