import type { IndexPredictionArtifact } from "@/lib/api";

const CHANNEL_LABELS: Record<string, string> = {
  valuation_pct: "Valuation",
  liquidity_spread_pct: "Liquidity & spreads",
  energy_pct: "Energy",
  fx_rates_pct: "FX & rates",
  global_risk_pct: "Global risk",
  vol_pct: "Volatility",
  flows_pct: "Institutional flows",
  technical_pct: "Technical",
  sentiment_news_pct: "Sentiment & news",
  unexplained_pct: "Unexplained",
};

function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(digits)}%`;
}

interface Props {
  artifact: IndexPredictionArtifact;
}

export function CauseAttributionPanel({ artifact }: Props) {
  const pred = artifact.prediction || {};
  const channels = pred.channel_attribution;
  if (!channels || typeof channels !== "object") return null;

  const rows = Object.entries(channels)
    .filter(([key]) => !key.startsWith("_"))
    .map(([key, value]) => ({
      key,
      label: CHANNEL_LABELS[key] ?? key.replace(/_/g, " "),
      value: Number(value),
    }))
    .filter((row) => Number.isFinite(row.value))
    .sort((a, b) => Math.abs(b.value) - Math.abs(a.value));

  if (!rows.length) return null;

  const maxAbs = Math.max(...rows.map((r) => Math.abs(r.value)), 0.01);
  const coverage = channels._coverage;

  return (
    <div className="space-y-3 rounded-xl border bg-card p-4 shadow-sm">
      <div>
        <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
          Cause channel attribution
        </p>
        <p className="mt-1 text-[11px] text-muted-foreground">
          Ridge marginal share by macro channel — from forecast lab when{" "}
          <span className="font-mono">INDEX_PREDICTION_LAB_ENABLED=1</span>. Not the live headline merge.
        </p>
      </div>

      <ul className="space-y-2">
        {rows.map((row) => {
          const widthPct = Math.min(100, (Math.abs(row.value) / maxAbs) * 100);
          const tone =
            row.value > 0
              ? "bg-emerald-500/70"
              : row.value < 0
                ? "bg-red-500/70"
                : "bg-muted";
          return (
            <li key={row.key} className="grid grid-cols-[9rem_1fr_4rem] items-center gap-2 text-[11px]">
              <span className="text-muted-foreground">{row.label}</span>
              <div className="h-2 overflow-hidden rounded-full bg-muted/60">
                <div className={`h-full rounded-full ${tone}`} style={{ width: `${widthPct}%` }} />
              </div>
              <span className="text-right tabular-nums">{fmtPct(row.value, 3)}</span>
            </li>
          );
        })}
      </ul>

      {coverage != null && Number.isFinite(Number(coverage)) ? (
        <p className="text-[10px] text-muted-foreground">
          Channel coverage {Math.round(Number(coverage) * 100)}% of mapped macro factors.
        </p>
      ) : null}
    </div>
  );
}
