import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { JobsPanel } from "@/components/scheduler/JobsPanel";
import type { ScheduledRun, SchedulerRegistryEntry } from "@/lib/api";

// jsdom has no EventSource; JobsPanel mounts LiveLogTail when a row is
// expanded — this just needs enough of the interface for it not to throw.
class FakeEventSource {
  addEventListener() {}
  close() {}
}

beforeEach(() => {
  vi.stubGlobal("EventSource", FakeEventSource);
});

function run(overrides: Partial<ScheduledRun> = {}): ScheduledRun {
  return {
    id: "job-a",
    prompt: "pre-open scan",
    schedule: "30 23 * * 1-5",
    next_run_at: 1_790_000_000_000,
    status: "pending",
    created_at: 1_780_000_000_000,
    last_run_at: null,
    consecutive_failures: 0,
    last_error: null,
    failure_kind: null,
    config: {},
    timezone: "UTC",
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

function baseProps(overrides: Partial<React.ComponentProps<typeof JobsPanel>> = {}) {
  return {
    runs: [] as ScheduledRun[],
    registryEntries: [] as SchedulerRegistryEntry[],
    busyId: null,
    bulkResumeBusy: false,
    actionError: null,
    expandedLogKey: null,
    setExpandedLogKey: vi.fn(),
    onTogglePause: vi.fn(),
    onToggleRegistryPause: vi.fn(),
    onCancelRun: vi.fn(),
    onTriggerRun: vi.fn(),
    onResumeAllAutoPaused: vi.fn(),
    streamUrlFor: () => () => Promise.resolve("http://test/stream"),
    formatNextRun: (r: ScheduledRun) => `Next run: ${r.next_run_at}`,
    ...overrides,
  };
}

describe("JobsPanel bucket ordering", () => {
  it("sorts running, then paused, then always-on recorder, then idle openalgo", () => {
    const runningJob = run({ id: "running", status: "running" });
    const pausedJob = run({ id: "paused", paused: true });
    const recorder = registryEntry({ id: "recorder-1", source: "stock_simulator" });
    const openalgoJob = registryEntry({
      id: "openalgo-1",
      source: "openalgo",
      section: "flow",
      label: "flow job",
    });

    render(
      <JobsPanel
        {...baseProps({ runs: [pausedJob, runningJob], registryEntries: [openalgoJob, recorder] })}
      />,
    );

    const items = screen.getAllByRole("listitem").map((li) => li.textContent ?? "");

    const order = items.map((text) => {
      if (text.includes("us/index")) return "recorder";
      if (text.includes("flow job")) return "openalgo";
      return "run";
    });
    // Two "run" rows (paused, running) must both precede the recorder row,
    // which must precede the openalgo row.
    const recorderPos = order.indexOf("recorder");
    const openalgoPos = order.indexOf("openalgo");
    const runPositions = order.reduce<number[]>((acc, kind, i) => {
      if (kind === "run") acc.push(i);
      return acc;
    }, []);
    expect(Math.max(...runPositions)).toBeLessThan(recorderPos);
    expect(recorderPos).toBeLessThan(openalgoPos);
  });
});

describe("JobsPanel auto-pause banner", () => {
  it("shows the banner with the correct count when jobs are auto-paused", () => {
    const jobs = [
      run({ id: "a", paused: true, auto_paused_reason: "auto-paused: recovered on stack boot" }),
      run({ id: "b", paused: true, auto_paused_reason: "auto-paused: recovered on stack boot" }),
      run({ id: "c", paused: false }),
    ];
    render(<JobsPanel {...baseProps({ runs: jobs })} />);

    expect(screen.getByText(/2 jobs auto-paused by a restart/)).toBeInTheDocument();
  });

  it("hides the banner when no jobs are auto-paused", () => {
    render(<JobsPanel {...baseProps({ runs: [run({ id: "a", paused: true })] })} />);

    expect(screen.queryByText(/auto-paused by a restart/)).not.toBeInTheDocument();
  });

  it("clicking Resume all calls onResumeAllAutoPaused", () => {
    const onResumeAllAutoPaused = vi.fn();
    render(
      <JobsPanel
        {...baseProps({
          runs: [run({ id: "a", paused: true, auto_paused_reason: "restart" })],
          onResumeAllAutoPaused,
        })}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Resume all/ }));

    expect(onResumeAllAutoPaused).toHaveBeenCalledTimes(1);
  });
});

describe("JobsPanel Run now button", () => {
  it("shows Run now only for an unpaused, non-running run", () => {
    render(<JobsPanel {...baseProps({ runs: [run({ id: "a" })] })} />);

    expect(screen.getByRole("button", { name: /Run .* now/ })).toBeInTheDocument();
  });

  it("hides Run now for a paused run", () => {
    render(<JobsPanel {...baseProps({ runs: [run({ id: "a", paused: true })] })} />);

    expect(screen.queryByRole("button", { name: /Run .* now/ })).not.toBeInTheDocument();
  });

  it("hides Run now for a running run", () => {
    render(<JobsPanel {...baseProps({ runs: [run({ id: "a", status: "running" })] })} />);

    expect(screen.queryByRole("button", { name: /Run .* now/ })).not.toBeInTheDocument();
  });

  it("hides Run now for registry entries (controls.trigger_now is false today)", () => {
    render(
      <JobsPanel
        {...baseProps({ registryEntries: [registryEntry(), registryEntry({ source: "openalgo", section: "flow" })] })}
      />,
    );

    expect(screen.queryByRole("button", { name: /Run .* now/ })).not.toBeInTheDocument();
  });
});

describe("JobsPanel Delete button", () => {
  it("never renders a Delete button", () => {
    render(
      <JobsPanel
        {...baseProps({ runs: [run({ id: "a" })], registryEntries: [registryEntry()] })}
      />,
    );

    expect(screen.queryByRole("button", { name: /Delete/ })).not.toBeInTheDocument();
  });
});

describe("JobsPanel mixed sources", () => {
  it("renders read-only stock_simulator rows and controllable openalgo rows together", () => {
    render(
      <JobsPanel
        {...baseProps({
          registryEntries: [
            registryEntry(),
            registryEntry({
              id: "C:flow:1",
              source: "openalgo",
              section: "flow",
              label: "flow job",
              controls: { pause: true, resume: false, cancel: false, delete: false, trigger_now: false },
            }),
          ],
        })}
      />,
    );

    expect(screen.getByText("us/index")).toBeInTheDocument();
    expect(screen.getByText("flow job")).toBeInTheDocument();
    expect(screen.getByText("read-only")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Pause/ })).toBeInTheDocument();
  });
});
