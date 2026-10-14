import { describe, expect, it, vi } from "vitest";
import { pollTrackScoreboardJob } from "@/lib/trackScoreboardUtils";
import type { IndexTrackScoreboardReport } from "@/lib/api";

const report = (state?: string, error?: string) =>
  ({ refresh_job: state ? { job_id: "j", state, error } : null }) as unknown as IndexTrackScoreboardReport;
const wait = async () => {};
const base = { intervalMs: 1, maxPolls: 4, isCurrent: () => true, wait };

describe("pollTrackScoreboardJob", () => {
  it("stops when the job leaves 'running' and publishes every report it read", async () => {
    const reads = [report("running"), report("running"), report("done")];
    const fetchReport = vi.fn(async () => ({ report: reads.shift() }));
    const seen: unknown[] = [];
    const out = await pollTrackScoreboardJob(fetchReport, { ...base, onReport: (r) => seen.push(r) });
    expect(out).toBe("done");
    expect(fetchReport).toHaveBeenCalledTimes(3);
    expect(seen).toHaveLength(3);
  });

  it("a job stuck on 'running' ends at the ceiling with a visible timeout, not forever", async () => {
    const fetchReport = vi.fn(async () => ({ report: report("running") }));
    await expect(pollTrackScoreboardJob(fetchReport, { ...base, maxPolls: 3 })).rejects.toThrow(/still running after 3 checks/);
    expect(fetchReport).toHaveBeenCalledTimes(3);
  });

  it("a failed job throws its server error", async () => {
    const fetchReport = async () => ({ report: report("failed", "boom") });
    await expect(pollTrackScoreboardJob(fetchReport, base)).rejects.toThrow("boom");
  });

  it("a superseded poll (horizon changed / unmounted) stops quietly without publishing", async () => {
    let current = true;
    const fetchReport = vi.fn(async () => {
      current = false; // the user switched horizon while this read was in flight
      return { report: report("running") };
    });
    const onReport = vi.fn();
    const out = await pollTrackScoreboardJob(fetchReport, { ...base, isCurrent: () => current, onReport });
    expect(out).toBe("superseded");
    expect(fetchReport).toHaveBeenCalledTimes(1);
    expect(onReport).not.toHaveBeenCalled();
  });

  it("a read with no report counts as not-running and ends the loop", async () => {
    const out = await pollTrackScoreboardJob(async () => ({}), base);
    expect(out).toBe("done");
  });
});
