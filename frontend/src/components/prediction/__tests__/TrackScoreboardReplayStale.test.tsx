import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { TrackScoreboardReplaySection } from "../TrackScoreboardReplaySection";

const panel = vi.hoisted(() => vi.fn(() => null));
vi.mock("@/components/prediction/forecast-replay", () => ({ ForecastReplayPanel: panel }));

describe("TrackScoreboardReplaySection", () => {
  it("a stale cached scoreboard states why there is nothing to replay and asks the server for nothing", () => {
    // the real 2026-08-29 cache: the replay's backtest family lists no `track:<id>@backtest` source for it (422 unknown ids)
    const { container } = render(
      <TrackScoreboardReplaySection horizonDays={14} report={{ needs_refresh: true, as_of: "2026-08-29T13:29:45+00:00", schema_version: 5, horizon_days: 14 }} />,
    );
    const t = container.querySelector('[data-testid="backtest-replay-stale"]')?.textContent ?? "";
    expect(t).toContain("stale");
    expect(t).toContain("29 Aug 2026, 18:59 IST");
    expect(t).toContain("schema 5");
    expect(panel).not.toHaveBeenCalled();
  });

  it("a current scoreboard mounts the replay panel on the backtest tracks", () => {
    render(<TrackScoreboardReplaySection horizonDays={14} report={{ needs_refresh: false }} />);
    expect(panel).toHaveBeenCalled();
    const props = (panel.mock.calls[0] as unknown as [{ defaultSources: string[]; defaultProvenance: string[] }])[0];
    expect(props.defaultSources).toEqual(["track:quant_ridge@backtest", "track:macro_only@backtest"]);
    expect(props.defaultProvenance).toEqual(["backtest"]);
  });
});
