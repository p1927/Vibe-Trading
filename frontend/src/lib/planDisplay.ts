import type { HubPlanArtifact, TradePlanLeg } from "@/lib/api";

const VIEW_LABELS: Record<string, string> = {
  event_volatility: "Range / event volatility",
  bullish: "Bullish bias",
  bearish: "Bearish bias",
  neutral: "Neutral / range-bound",
  high_iv: "Elevated implied volatility",
  low_iv: "Low implied volatility",
};

export function formatStrategyName(name?: string | null): string {
  if (!name) return "";
  return name.replace(/_/g, " ");
}

export function formatViewLabel(view?: string | null): string {
  if (!view) return "";
  const key = view.toLowerCase();
  return VIEW_LABELS[key] ?? view.replace(/_/g, " ");
}

function formatLeg(leg: TradePlanLeg): string {
  const side = (leg.side || "").toUpperCase() === "SELL" ? "Sell" : "Buy";
  const type = (leg.option_type || "CE").toUpperCase();
  const strike = leg.strike != null ? `@ ${leg.strike}` : "";
  const qty = leg.quantity != null ? ` × ${leg.quantity}` : "";
  return `${side} ${type}${strike}${qty}`;
}

export function formatLegsSummary(legs?: TradePlanLeg[] | null): string {
  if (!legs?.length) return "";
  return legs.map(formatLeg).join(" · ");
}

export function inferPlanStatus(artifact: HubPlanArtifact): "ready" | "partial" | "incomplete" {
  const name = artifact.recommended_name ?? artifact.recommended?.name;
  const ranked = artifact.ranked_strategies?.length ?? 0;
  if (name && ranked > 0) return "ready";
  if (ranked > 0 || name) return "partial";
  return "incomplete";
}

export function buildPlanHeadline(artifact: HubPlanArtifact): string {
  const name = artifact.recommended_name ?? artifact.recommended?.name;
  if (name) {
    return `Recommended options strategy: ${formatStrategyName(name)}`;
  }
  const hint = artifact.scenarios?.[0]?.strategy_hint;
  if (hint) {
    return `No ranked pick yet — scenario suggests: ${formatStrategyName(hint)}`;
  }
  if (artifact.asset_type === "stock") {
    return "Stock trade plan";
  }
  return "Options trade plan (incomplete)";
}

export function buildPlanSummary(artifact: HubPlanArtifact): string {
  const rec = artifact.recommended;
  const rationale = artifact.recommended_rationale ?? rec?.rationale;
  const legs = formatLegsSummary(rec?.legs as TradePlanLeg[] | undefined);

  if (rationale) {
    const parts = [rationale];
    if (legs) parts.push(`Legs: ${legs}.`);
    if (rec?.max_profit != null && rec?.max_loss != null) {
      parts.push(`Max profit ₹${Math.round(rec.max_profit).toLocaleString()} · Max loss ₹${Math.round(Math.abs(rec.max_loss)).toLocaleString()}.`);
    }
    return parts.join(" ");
  }

  if (artifact.data_warnings?.length) {
    return artifact.data_warnings[0];
  }

  const view = formatViewLabel(artifact.prediction?.view);
  const move = artifact.prediction?.expected_move_pct;
  if (view && move != null) {
    return `${view} on ${artifact.underlying ?? "this symbol"} — expected move about ±${Number(move).toFixed(1)}% into expiry. Live option chain is needed to rank specific strategies.`;
  }

  if (artifact.asset_type === "stock" && artifact.prediction?.view) {
    return `Directional view: ${formatViewLabel(artifact.prediction.view)}. Ask the agent for a refreshed stock plan with entry rationale.`;
  }

  return "Research loaded but no concrete strategy recommendation yet. Ask the agent to refresh the plan with live chain data.";
}

export function shouldShowConfidence(confidence?: number | null): boolean {
  return confidence != null && Number.isFinite(confidence) && confidence > 0.05;
}
