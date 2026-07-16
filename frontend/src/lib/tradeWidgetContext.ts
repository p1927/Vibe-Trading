import type { TradePlanLeg } from "@/lib/api";
import { formatLegLine } from "@/lib/tradePlanLegs";

export interface TradeWidgetAdjustment {
  widget_id: string;
  underlying: string;
  agent_recommended: string;
  strategy_name: string;
  original_legs: TradePlanLeg[];
  adjusted_legs: TradePlanLeg[];
  payoff_summary?: {
    max_profit?: number;
    max_loss?: number;
    breakevens?: number[];
  };
}

let activeAdjustment: TradeWidgetAdjustment | null = null;

export function setTradeWidgetAdjustment(adj: TradeWidgetAdjustment | null): void {
  activeAdjustment = adj;
}

export function getTradeWidgetAdjustment(): TradeWidgetAdjustment | null {
  return activeAdjustment;
}

function legsEqual(a: TradePlanLeg[], b: TradePlanLeg[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((leg, i) => {
    const other = b[i];
    return (
      leg.side === other.side &&
      leg.strike === other.strike &&
      leg.option_type === other.option_type &&
      leg.symbol === other.symbol &&
      leg.price === other.price &&
      leg.quantity === other.quantity
    );
  });
}

export function isTradeWidgetModified(adj: TradeWidgetAdjustment | null): boolean {
  if (!adj) return false;
  return !legsEqual(adj.original_legs, adj.adjusted_legs);
}

function describeLegChanges(original: TradePlanLeg[], adjusted: TradePlanLeg[]): string[] {
  const changes: string[] = [];
  const n = Math.max(original.length, adjusted.length);
  for (let i = 0; i < n; i++) {
    const o = original[i];
    const a = adjusted[i];
    if (!o && a) {
      changes.push(`+ added leg: ${formatLegLine(a)}`);
      continue;
    }
    if (o && !a) {
      changes.push(`- removed leg: ${formatLegLine(o)}`);
      continue;
    }
    if (!o || !a) continue;
    if (o.strike !== a.strike) {
      changes.push(
        `${o.side} ${o.option_type} strike ${o.strike ?? "?"} → ${a.strike ?? "?"}`,
      );
    } else if (
      o.side !== a.side ||
      o.option_type !== a.option_type ||
      o.price !== a.price ||
      o.quantity !== a.quantity
    ) {
      changes.push(`${formatLegLine(o)} → ${formatLegLine(a)}`);
    }
  }
  return changes;
}

/** Hidden context block prepended to the user's chat message for the agent. */
export function formatTradeWidgetContextBlock(): string | null {
  const adj = activeAdjustment;
  if (!adj || !isTradeWidgetModified(adj)) return null;

  const changes = describeLegChanges(adj.original_legs, adj.adjusted_legs);
  const originalLines = adj.original_legs.map((l) => `- ${formatLegLine(l)}`).join("\n");
  const adjustedLines = adj.adjusted_legs.map((l) => `- ${formatLegLine(l)}`).join("\n");
  const payoff = adj.payoff_summary;
  const payoffLines = payoff
    ? [
        payoff.max_profit != null ? `max profit (est.): ₹${Math.round(payoff.max_profit)}` : null,
        payoff.max_loss != null ? `max loss (est.): ₹${Math.round(payoff.max_loss)}` : null,
        payoff.breakevens?.length
          ? `breakevens (est.): ${payoff.breakevens.map((b) => b.toFixed(0)).join(", ")}`
          : null,
      ]
        .filter(Boolean)
        .join("\n")
    : "";

  return [
    "[trade_widget_context]",
    `widget_id: ${adj.widget_id}`,
    `underlying: ${adj.underlying}`,
    `agent_recommended_strategy: ${adj.agent_recommended}`,
    `active_strategy: ${adj.strategy_name}`,
    "",
    "original_legs (agent proposal):",
    originalLines || "- (none)",
    "",
    "user_adjusted_legs (current widget state):",
    adjustedLines || "- (none)",
    "",
    "leg_changes:",
    ...(changes.length ? changes.map((c) => `- ${c}`) : ["- (none)"]),
    payoffLines ? `\nadjusted_payoff:\n${payoffLines}` : "",
    "[/trade_widget_context]",
    "",
    "The user adjusted the trade plan widget legs above. Compare their setup to your original recommendation and respond to their message below.",
  ]
    .filter((line) => line !== "")
    .join("\n");
}
