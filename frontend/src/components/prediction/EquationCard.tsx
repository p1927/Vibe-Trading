import { useMemo, useState } from "react";
import type { PlanPrediction } from "@/lib/api";

interface Props {
  prediction?: PlanPrediction | null;
  spot?: number | null;
  horizonDays?: number;
}

const TERM_LABELS: Record<string, string> = {
  oil_brent: "Brent",
  oil_wti: "WTI",
  usd_inr: "USD/INR",
  india_vix: "India VIX",
  sp500: "S&P 500",
  us_10y: "US 10Y",
  fii_net_5d: "FII net 5d",
  dii_net_5d: "DII net 5d",
  nifty_pcr: "PCR",
};

function labelTerm(name: string): string {
  if (name === "1") return "intercept";
  const base = name.replace(/\^2$/, "").replace(/ /g, " × ");
  return base
    .split(" × ")
    .map((part) => TERM_LABELS[part] || part)
    .join(" × ");
}

function fmtLevel(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

export function EquationCard({ prediction, spot, horizonDays = 14 }: Props) {
  const [showAll, setShowAll] = useState(false);
  const eq = prediction?.equation;

  const coeffs = eq?.coefficients || {};
  const sorted = useMemo(
    () => Object.entries(coeffs).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])),
    [coeffs],
  );
  const visible = showAll ? sorted : sorted.slice(0, 8);

  const impliedLevel = useMemo(() => {
    if (spot == null || !Number.isFinite(spot)) return null;
    const ret = prediction?.expected_return_pct;
    if (ret == null || !Number.isFinite(ret)) return null;
    return spot * (1 + ret / 100);
  }, [spot, prediction?.expected_return_pct]);

  const expandedPoly = useMemo(() => {
    const intercept = eq?.intercept ?? 0;
    const parts = sorted
      .filter(([name]) => name !== "1")
      .slice(0, 6)
      .map(([name, val]) => {
        const sign = val >= 0 ? "+" : "−";
        return `${sign} ${Math.abs(val).toFixed(4)}·${labelTerm(name)}`;
      });
    return `ΔNifty_${horizonDays}d ≈ ${intercept.toFixed(4)} ${parts.join(" ")}`.trim();
  }, [eq?.intercept, sorted, horizonDays]);

  if (!eq?.form && !eq?.coefficients) {
    return (
      <div className="rounded-xl border bg-card p-4 text-[12px] text-muted-foreground shadow-sm">
        Polynomial macro overlay not trained yet. Run factor snapshot + calibration, then refresh analysis.
      </div>
    );
  }

  return (
    <div className="rounded-xl border bg-card p-4 shadow-sm">
      <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
        Prediction equation
      </p>
      <p className="mt-2 font-mono text-[11px] leading-relaxed text-muted-foreground">{eq.form}</p>
      <p className="mt-2 font-mono text-[12px] leading-relaxed">{expandedPoly}</p>
      <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <div className="text-[11px]">
          <span className="text-muted-foreground">Implied Nifty ({horizonDays}d)</span>
          <p className="text-lg font-semibold tabular-nums">{fmtLevel(impliedLevel)}</p>
        </div>
        <div className="text-[11px]">
          <span className="text-muted-foreground">Expected return</span>
          <p className="text-lg font-semibold tabular-nums">
            {prediction?.expected_return_pct != null
              ? `${prediction.expected_return_pct >= 0 ? "+" : ""}${prediction.expected_return_pct.toFixed(2)}%`
              : "—"}
          </p>
        </div>
        <div className="text-[11px]">
          <span className="text-muted-foreground">Bottom-up block</span>
          <p className="font-medium tabular-nums">
            {prediction?.bottom_up_return_pct != null
              ? `${prediction.bottom_up_return_pct >= 0 ? "+" : ""}${prediction.bottom_up_return_pct.toFixed(2)}%`
              : "—"}
          </p>
        </div>
        <div className="text-[11px]">
          <span className="text-muted-foreground">Macro polynomial</span>
          <p className="font-medium tabular-nums">
            {prediction?.macro_delta_pct != null
              ? `${prediction.macro_delta_pct >= 0 ? "+" : ""}${prediction.macro_delta_pct.toFixed(2)}%`
              : "—"}
          </p>
        </div>
      </div>
      <div className="mt-2 text-[11px]">
        <span className="text-muted-foreground">Walk-forward R² </span>
        <span className="font-medium tabular-nums">
          {eq.r2_walk_forward != null ? eq.r2_walk_forward.toFixed(3) : "—"}
        </span>
      </div>
      {visible.length > 0 ? (
        <>
          <ul className="mt-3 space-y-1 border-t pt-3 text-[11px]">
            {visible.map(([name, val]) => (
              <li key={name} className="flex justify-between gap-2">
                <span className="truncate text-muted-foreground">{labelTerm(name)}</span>
                <span className="shrink-0 font-medium tabular-nums">{val.toFixed(4)}</span>
              </li>
            ))}
          </ul>
          {sorted.length > 8 ? (
            <button
              type="button"
              onClick={() => setShowAll((v) => !v)}
              className="mt-2 text-[10px] text-primary hover:underline"
            >
              {showAll ? "Show top 8" : `Show all ${sorted.length} terms`}
            </button>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
