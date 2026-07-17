import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Loader2, Newspaper, RefreshCw, ShieldAlert, ShieldCheck } from "lucide-react";
import { cn } from "@/lib/utils";
import { api, type IndexNewsImpactReport, type IndexPredictionArtifact } from "@/lib/api";

interface Props {
  horizonDays: number;
  pollMs?: number;
  monitorEnabled?: boolean;
  shockCalibration?: IndexPredictionArtifact["news_shock_calibration"];
}

function statusBadge(status?: string) {
  switch (status) {
    case "approved":
      return (
        <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-medium text-emerald-700 dark:text-emerald-400">
          <ShieldCheck className="h-3 w-3" /> Verified
        </span>
      );
    case "partial":
      return (
        <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-medium text-amber-700 dark:text-amber-400">
          <CheckCircle2 className="h-3 w-3" /> Partial
        </span>
      );
    case "rejected":
      return (
        <span className="inline-flex items-center gap-1 rounded-full bg-red-500/15 px-2 py-0.5 text-[10px] font-medium text-red-700 dark:text-red-400">
          <ShieldAlert className="h-3 w-3" /> Rejected
        </span>
      );
    default:
      return (
        <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
          {status ?? "pending"}
        </span>
      );
  }
}

function formatPts(n?: number) {
  if (n == null || Number.isNaN(n)) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(0)} pts`;
}

function consensusBadge(consensus?: {
  direction?: string;
  confidence?: number;
  ref_count?: number;
}) {
  if (!consensus?.direction) return null;
  const direction = consensus.direction;
  const tone =
    direction === "bullish"
      ? "text-emerald-700 dark:text-emerald-400 bg-emerald-500/10"
      : direction === "bearish"
        ? "text-red-700 dark:text-red-400 bg-red-500/10"
        : "text-muted-foreground bg-muted";
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium capitalize", tone)}>
      {direction}
      {consensus.confidence != null ? ` · ${Math.round(consensus.confidence * 100)}%` : ""}
      {consensus.ref_count ? ` · ${consensus.ref_count} refs` : ""}
    </span>
  );
}

export function NewsImpactPanel({ horizonDays, pollMs = 0, monitorEnabled, shockCalibration }: Props) {
  const [report, setReport] = useState<IndexNewsImpactReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showRejected, setShowRejected] = useState(false);

  const load = useCallback(
    async (refresh = false) => {
      setLoading(true);
      setError(null);
      try {
        const res = await api.getIndexPredictionNewsImpact(
          "NIFTY",
          refresh,
          horizonDays,
          showRejected,
        );
        setReport(res.report ?? null);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load news impact");
      } finally {
        setLoading(false);
      }
    },
    [horizonDays, showRejected],
  );

  useEffect(() => {
    void load(false);
  }, [load]);

  useEffect(() => {
    if (!monitorEnabled || !pollMs || pollMs < 30_000) return;
    const id = window.setInterval(() => void load(false), pollMs);
    return () => window.clearInterval(id);
  }, [load, monitorEnabled, pollMs]);

  const items = report?.items ?? [];
  const summary = report?.summary;
  const debate = report?.debate_summary;
  const rejectedCount = summary?.rejected_count ?? summary?.rejected_skipped ?? 0;
  const hubEmpty = report?.status === "hub_empty" || (!items.length && report?.status !== "ok");
  const pendingCount = (summary as { pending_count?: number } | undefined)?.pending_count ?? 0;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground">
        <div className="flex items-center gap-2">
          <Newspaper className="h-4 w-4" />
          <span>
            {summary?.approved_count ?? 0} verified · {summary?.partial_count ?? 0} partial
            {rejectedCount > 0 && !showRejected ? ` · ${rejectedCount} rejected (hidden)` : ""}
            {summary?.source === "hub_events" ? " · hub events" : ""}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <label className="inline-flex items-center gap-1.5 cursor-pointer">
            <input
              type="checkbox"
              checked={showRejected}
              onChange={(e) => setShowRejected(e.target.checked)}
              className="h-3 w-3"
            />
            Show rejected
          </label>
          <button
            type="button"
            onClick={() => void load(true)}
            disabled={loading}
            className="inline-flex items-center gap-1 rounded-md border px-2 py-1 hover:bg-muted/60 disabled:opacity-50"
          >
            {loading ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
            Ingest new
          </button>
        </div>
      </div>

      {debate?.view ? (
        <div className="rounded-lg border border-dashed bg-muted/20 px-3 py-2 text-[12px]">
          <span className="font-medium text-foreground">Agents: {debate.view}</span>
          {debate.excerpt ? (
            <p className="mt-1 text-muted-foreground line-clamp-2">{debate.excerpt}</p>
          ) : null}
        </div>
      ) : null}

      {shockCalibration?.topics && Object.keys(shockCalibration.topics).length ? (
        <div className="rounded-lg border bg-muted/10 px-3 py-2 text-[11px]">
          <p className="font-medium text-foreground">Calibrated topic shocks (reconciled ledger)</p>
          <p className="text-muted-foreground">
            {shockCalibration.reconciled_total ?? 0} matured stories · overlay{" "}
            {shockCalibration.news_event_overlay_status ?? "pending"}
          </p>
          <ul className="mt-2 space-y-1">
            {Object.entries(shockCalibration.topics)
              .filter(([, v]) => v.overlay_eligible)
              .slice(0, 6)
              .map(([topic, row]) => (
                <li key={topic} className="flex justify-between gap-2 tabular-nums">
                  <span className="capitalize">{topic.replace(/_/g, " ")}</span>
                  <span>
                    med err {row.median_calibration_error != null ? `${row.median_calibration_error.toFixed(2)}%` : "—"}
                    {" · "}n={row.sample_count}
                  </span>
                </li>
              ))}
          </ul>
        </div>
      ) : null}

      {error ? <p className="text-[11px] text-red-600 dark:text-red-400">{error}</p> : null}

      {!items.length && !loading ? (
        <div className="rounded-xl border border-dashed bg-muted/20 px-4 py-6 text-center text-[12px] text-muted-foreground space-y-2">
          <p>
            {hubEmpty
              ? "No verified headlines in hub yet."
              : "No headlines match the current filters."}
          </p>
          <p>
            Run analysis with <span className="font-medium text-foreground">Refresh all 50 constituents</span>{" "}
            checked to ingest per-stock news, or click <span className="font-medium text-foreground">Ingest new</span>{" "}
            for index-level NIFTY news only.
          </p>
          {pendingCount > 0 ? (
            <p className="text-[11px]">
              {pendingCount} staging ref{pendingCount === 1 ? "" : "s"} queued — distillation may still be running.
            </p>
          ) : null}
          <p className="text-[10px] opacity-80">
            Rejected clickbait stays in hub but is hidden unless you enable Show rejected.
          </p>
        </div>
      ) : null}

      <div className="grid gap-3">
        {items.map((item) => {
          const predicted = item.predicted ?? item.predicted_impact;
          const actual = item.actual ?? item.actual_impact;
          const verification = item.verification;
          const vStatus = verification?.status ?? item.verification_status;
          const facts = item.structured_summary?.facts ?? [];
          const eventMeta = item.event_meta ?? item.structured_summary?.event_meta;
          const consensus = item.consensus ?? eventMeta?.consensus;
          const eventTimeline = eventMeta?.timeline ?? [];
          const references = item.references ?? eventMeta?.references ?? [];
          const sources = item.sources ?? [];
          const attributionRows =
            sources.length > 0
              ? sources.map((s) => ({
                  key: `${s.vendor}-${s.url}`,
                  label: s.publisher || s.vendor,
                  url: s.url,
                }))
              : references.map((ref) => ({
                  key: ref.ref_id || ref.url || ref.raw_title,
                  label: ref.publisher || ref.vendor || "source",
                  url: ref.url,
                  subtitle: ref.raw_title,
                }));
          return (
            <article
              key={item.id ?? item.title}
              className="rounded-xl border bg-card p-4 shadow-sm"
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0 flex-1 space-y-1">
                  <h4 className="text-[13px] font-semibold leading-snug">{item.title}</h4>
                  {item.raw_headline && item.raw_headline !== item.title ? (
                    <p className="text-[10px] text-muted-foreground line-through opacity-70">
                      Headline: {item.raw_headline}
                    </p>
                  ) : null}
                  <p className="text-[11px] text-muted-foreground">
                    {item.source}
                    {sources.length > 1 ? ` · ${sources.length} sources merged` : ""}
                    {item.published_at ? ` · ${item.published_at.slice(0, 16).replace("T", " ")}` : ""}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                  {consensusBadge(consensus)}
                  {statusBadge(vStatus)}
                </div>
              </div>

              {item.content_summary ? (
                <p className="mt-2 text-[12px] leading-relaxed text-foreground/90">{item.content_summary}</p>
              ) : null}

              {facts.length ? (
                <ul className="mt-2 list-disc space-y-0.5 pl-4 text-[11px] text-muted-foreground">
                  {facts.slice(0, 3).map((f) => (
                    <li key={f}>{f}</li>
                  ))}
                </ul>
              ) : null}

              {attributionRows.length ? (
                <details className="mt-2 text-[10px] text-muted-foreground">
                  <summary className="cursor-pointer">
                    References ({attributionRows.length}
                    {sources.length > 1 ? " sources merged" : ""})
                  </summary>
                  <ul className="mt-1 space-y-0.5">
                    {attributionRows.map((row) => (
                      <li key={row.key}>
                        {row.label}
                        {"subtitle" in row && row.subtitle ? ` — ${row.subtitle.slice(0, 80)}` : ""}
                        {row.url ? ` · ${row.url.slice(0, 60)}` : ""}
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}

              {eventTimeline.length ? (
                <details className="mt-2 text-[10px] text-muted-foreground">
                  <summary className="cursor-pointer">Event timeline ({eventTimeline.length})</summary>
                  <ul className="mt-1 space-y-1">
                    {eventTimeline.slice(-5).map((entry, idx) => (
                      <li key={`${entry.at}-${idx}`}>
                        <span className="font-medium capitalize text-foreground/80">
                          {entry.kind ?? "update"}
                        </span>
                        {entry.at ? ` · ${entry.at.slice(0, 16).replace("T", " ")}` : ""}
                        {entry.summary ? ` — ${entry.summary.slice(0, 120)}` : ""}
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}

              <div className="mt-3 flex flex-wrap gap-1.5">
                {(item.tags?.topics ?? []).map((topic) => (
                  <span
                    key={`topic-${topic}`}
                    className="rounded-md bg-blue-500/10 px-2 py-0.5 text-[10px] font-medium text-blue-700 dark:text-blue-300"
                  >
                    {topic}
                  </span>
                ))}
                {(item.tags?.themes ?? []).map((theme) => (
                  <span
                    key={`theme-${theme}`}
                    className="rounded-md bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-800 dark:text-amber-300"
                  >
                    {theme}
                  </span>
                ))}
                {(item.tagged_factors ?? item.tags?.factors?.map((f) => ({ factor: f })) ?? []).map((t) => (
                  <span
                    key={t.factor}
                    className="rounded-md bg-muted px-2 py-0.5 text-[10px] font-medium"
                  >
                    {t.factor}
                  </span>
                ))}
              </div>

              {vStatus !== "rejected" ? (
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  <div className="rounded-lg bg-muted/40 px-3 py-2">
                    <p className="text-[10px] uppercase tracking-wide text-muted-foreground">Model predicted</p>
                    <p
                      className={cn(
                        "text-[14px] font-semibold tabular-nums",
                        (predicted?.nifty_points ?? 0) < 0
                          ? "text-red-600 dark:text-red-400"
                          : "text-emerald-700 dark:text-emerald-400",
                      )}
                    >
                      {formatPts(predicted?.nifty_points)}
                      {predicted?.return_pct != null
                        ? ` (${predicted.return_pct > 0 ? "+" : ""}${predicted.return_pct.toFixed(2)}%)`
                        : ""}
                    </p>
                    <p className="text-[10px] text-muted-foreground">
                      Horizon: {item.horizon_trading_days ?? horizonDays} sessions
                      {item.maturity_date ? ` → ${item.maturity_date}` : ""}
                    </p>
                  </div>
                  <div className="rounded-lg bg-muted/40 px-3 py-2">
                    <p className="text-[10px] uppercase tracking-wide text-muted-foreground">Actual (at maturity)</p>
                    <p className="text-[14px] font-semibold tabular-nums text-muted-foreground">
                      {actual ? formatPts(actual.nifty_points) : "Pending"}
                    </p>
                  </div>
                </div>
              ) : null}

              {(verification?.claims ?? []).length ? (
                <details className="mt-3 text-[11px]">
                  <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                    Data verification ({verification?.claims?.length} claims)
                  </summary>
                  <ul className="mt-2 space-y-1">
                    {verification?.claims?.map((c, i) => (
                      <li key={`${c.factor}-${i}`} className="flex gap-2">
                        <span
                          className={cn(
                            "shrink-0 font-medium",
                            c.verdict === "supported" && "text-emerald-600",
                            c.verdict === "contradicted" && "text-red-600",
                          )}
                        >
                          {c.verdict}
                        </span>
                        <span className="text-muted-foreground">
                          {c.factor}: {c.evidence}
                        </span>
                      </li>
                    ))}
                  </ul>
                  {verification?.approval_note ? (
                    <p className="mt-2 flex items-start gap-1 text-muted-foreground">
                      <ShieldAlert className="mt-0.5 h-3 w-3 shrink-0" />
                      {verification.approval_note}
                    </p>
                  ) : null}
                </details>
              ) : null}

              {item.confidence_note ? (
                <p className="mt-2 text-[10px] text-muted-foreground">{item.confidence_note}</p>
              ) : null}
            </article>
          );
        })}
      </div>
    </div>
  );
}
