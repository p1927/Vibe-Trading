/** Map saved NewsEventScenarioDoc to TradePlanWidget for canvas reload. */
import type { TradePlanWidget } from "@/lib/api";

export function scenarioDocToWidget(scenario: Record<string, unknown>): TradePlanWidget {
  const baseline = (scenario.baseline ?? {}) as TradePlanWidget["baseline"];
  return {
    type: "trade_plan.widget",
    widget_kind: "news_event_scenario",
    widget_id: `ns_reload_${String(scenario.scenario_id ?? "unknown")}`,
    asset_type: "index",
    underlying: String(scenario.ticker ?? "NIFTY"),
    instrument_type: "index",
    market: "IN",
    spot: baseline?.spot ?? undefined,
    date_range: scenario.date_range as TradePlanWidget["date_range"],
    event: scenario.event as TradePlanWidget["event"],
    baseline,
    outcomes: (scenario.outcomes ?? []) as TradePlanWidget["outcomes"],
    fan_band: scenario.fan_band as TradePlanWidget["fan_band"],
    scenario_id: String(scenario.scenario_id ?? ""),
    pipeline_as_of: String(scenario.pipeline_as_of ?? ""),
    plan_status: scenario.status === "partial" ? "partial" : "ready",
  };
}
