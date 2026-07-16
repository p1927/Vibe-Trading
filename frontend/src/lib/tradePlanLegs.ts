import type { TradePlanLeg, TradePlanWidget } from "@/lib/api";
import {
  buildOptionSymbol,
  computePayoff,
  daysToExpiry,
  daysToYears,
  nearestLegDays,
  type PayoffResult,
  type StrategyLeg,
} from "@/lib/strategyMath";

export interface PnlOverTimeSample {
  days_to_expiry: number;
  pnl: number;
  net_pnl?: number;
}

export function normalizeStrategyKey(name: string): string {
  return name.trim().toLowerCase().replace(/\s+/g, "_").replace(/-/g, "_");
}

export function inferStrikeStep(underlying: string, legs: TradePlanLeg[]): number {
  const sym = underlying.toUpperCase();
  if (sym.includes("BANKNIFTY") || sym.includes("SENSEX")) return 100;
  const strikes = legs
    .map((l) => l.strike)
    .filter((s): s is number => typeof s === "number" && Number.isFinite(s))
    .sort((a, b) => a - b);
  if (strikes.length >= 2) {
    const diffs: number[] = [];
    for (let i = 1; i < strikes.length; i++) {
      const d = strikes[i] - strikes[i - 1];
      if (d > 0) diffs.push(d);
    }
    if (diffs.length) return Math.min(...diffs);
  }
  return 50;
}

export function tradePlanLegsToStrategyLegs(
  legs: TradePlanLeg[],
  underlying: string,
  expiry: string,
  lotSize = 1,
): StrategyLeg[] {
  return legs.map((leg, i) => {
    const side = leg.side?.toUpperCase() === "SELL" ? "SELL" : "BUY";
    const optionType = leg.option_type?.toUpperCase() === "PE" ? "PE" : "CE";
    const strike = leg.strike ?? 0;
    const qty = leg.quantity ?? lotSize;
    const symbol =
      leg.symbol ||
      (strike > 0 ? buildOptionSymbol(underlying, expiry, strike, optionType) : "");
    return {
      id: `leg-${i}`,
      segment: "OPTION",
      side,
      lots: 1,
      lotSize: qty,
      expiry,
      strike: strike || undefined,
      optionType,
      price: leg.price ?? 0,
      iv: 0,
      active: true,
      symbol,
    };
  });
}

export function strategyLegsToTradePlanLegs(legs: StrategyLeg[]): TradePlanLeg[] {
  return legs.map((leg) => ({
    side: leg.side,
    symbol: leg.symbol,
    quantity: leg.lots * leg.lotSize,
    price: leg.price,
    strike: leg.strike,
    option_type: leg.optionType,
  }));
}

export function formatLegLine(leg: TradePlanLeg | StrategyLeg): string {
  const side = "side" in leg ? leg.side : undefined;
  const strike = leg.strike;
  const opt =
    "option_type" in leg
      ? leg.option_type
      : "optionType" in leg
        ? leg.optionType
        : undefined;
  const symbol = leg.symbol;
  const price = leg.price;
  const qty =
    "quantity" in leg
      ? leg.quantity
      : "lots" in leg && "lotSize" in leg
        ? leg.lots * leg.lotSize
        : undefined;
  const parts = [side, qty ? `${qty}×` : null, opt, strike, symbol, price != null ? `@ ₹${price}` : null]
    .filter(Boolean)
    .join(" ");
  return parts.trim();
}

export function computeWidgetPayoff(
  legs: StrategyLeg[],
  spot: number,
  expiry: string,
  atmIv = 18,
): PayoffResult {
  if (!spot || legs.length === 0) {
    return { samples: [], maxProfit: 0, maxLoss: 0, breakevens: [], zeroCrossings: [] };
  }
  const range: [number, number] = [spot * 0.9, spot * 1.1];
  const nearestDays = nearestLegDays(legs) || daysToExpiry(expiry) || 5;
  return computePayoff(legs, spot, nearestDays, 0, range, 240, 0, atmIv);
}

export function resolveWidgetSpot(widget: TradePlanWidget): number | null {
  if (widget.spot != null && Number(widget.spot) > 0) return Number(widget.spot);
  const browse = (widget as TradePlanWidget & { browse_summary?: { spot?: number } }).browse_summary;
  if (browse?.spot != null && Number(browse.spot) > 0) return Number(browse.spot);
  const samples = widget.payoff?.samples || [];
  for (const s of samples) {
    if (s.spot != null && Number(s.spot) > 0) return Number(s.spot);
  }
  return null;
}

export function widgetPayoffInputs(widget: TradePlanWidget, legs: StrategyLeg[]) {
  const spot = resolveWidgetSpot(widget) ?? 0;
  const expiry = widget.expiry || "";
  const atmIv =
    Number((widget.prediction as { atm_iv?: number } | undefined)?.atm_iv) ||
    (widget.prediction?.iv_regime === "high" ? 22 : widget.prediction?.iv_regime === "low" ? 12 : 18);
  const nearestDays = nearestLegDays(legs) || daysToExpiry(expiry) || 5;
  return {
    spot,
    expiry,
    atmIv,
    tYears: daysToYears(nearestDays),
    payoff: computeWidgetPayoff(legs, spot, expiry, atmIv),
  };
}

function legPnlAtDte(leg: StrategyLeg, underlying: number, timeFraction: number): number {
  const sideMult = leg.side === "BUY" ? 1 : -1;
  const qty = leg.lots * leg.lotSize;
  const premium = leg.price || 0;
  const strike = leg.strike || 0;
  const opt = leg.optionType || "CE";
  const intrinsic =
    opt === "CE" ? Math.max(0, underlying - strike) : Math.max(0, strike - underlying);
  const extrinsic = Math.max(0, premium - intrinsic);
  const decayed = extrinsic * Math.sqrt(Math.max(0, Math.min(1, timeFraction)));
  const mark = intrinsic + decayed;
  return sideMult * qty * (mark - premium);
}

/** Theta-decay P&L curve at current spot (mirrors backend compute_payoff_over_time). */
export function computePnlOverTimeSamples(
  legs: StrategyLeg[],
  spot: number,
  expiry: string,
  entryCharges = 0,
  points = 6,
): PnlOverTimeSample[] {
  if (spot <= 0 || legs.length === 0) return [];
  const totalDays = Math.max(1, daysToExpiry(expiry) || nearestLegDays(legs) || 7);
  const samples: PnlOverTimeSample[] = [];
  for (let i = 0; i < points; i++) {
    const dte =
      points > 1 ? Math.round(totalDays * (1 - i / (points - 1))) : 0;
    const timeFrac = totalDays ? dte / totalDays : 0;
    const pnl = legs.reduce((sum, leg) => sum + legPnlAtDte(leg, spot, timeFrac), 0);
    samples.push({
      days_to_expiry: dte,
      pnl: Math.round(pnl * 100) / 100,
      net_pnl: Math.round((pnl - entryCharges) * 100) / 100,
    });
  }
  return samples;
}
