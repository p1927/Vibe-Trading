import { useEffect, useState } from "react";
import { api, type IndexLedgerMissesResponse } from "@/lib/api";
import { PredictionMissAnalysisPanel } from "@/components/prediction/PredictionMissAnalysisPanel";

/** Fetch of the live ledger's post-mortem (RCA, counterfactual, surprise_flag): "how much we missed and why". */
function useLedgerMisses() {
  const [res, setRes] = useState<IndexLedgerMissesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    api
      .getIndexLedgerMisses()
      .then((r) => live && setRes(r))
      .catch((e) => live && setError(e instanceof Error ? e.message : "Ledger misses request failed"));
    return () => {
      live = false;
    };
  }, []);
  return { res, error };
}

/** Full table: the one miss panel over the live ledger's real misses. */
export function LedgerMissesPanel() {
  const { res, error } = useLedgerMisses();
  return (
    <PredictionMissAnalysisPanel
      report={res?.report ?? null}
      counterfactual={res?.counterfactual}
      loading={!res && !error}
      error={error ?? (res?.status === "empty" ? "No reconciled wrong-direction call in the ledger yet." : null)}
    />
  );
}

/** One-line digest for dashboards. */
export function LedgerMissesSummary() {
  const { res, error } = useLedgerMisses();
  const s = res?.report?.summary;
  const top = Object.entries(s?.miss_categories ?? {}).sort((a, b) => b[1] - a[1])[0];
  return (
    <div className="rounded-xl border border-border/60 bg-card p-3 text-[11px] shadow-sm">
      <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
        Live ledger misses
      </p>
      {s ? (
        <p className="mt-1 tabular-nums">
          {s.miss_count} wrong calls, mean miss {s.mae_pct?.toFixed(2) ?? "—"}%, {s.surprise_count ?? 0} flagged surprise
          {top ? `, mostly ${top[0].replace(/_/g, " ")}` : ""}
        </p>
      ) : (
        <p className="mt-1 text-muted-foreground">{error ?? (res ? "None reconciled yet." : "Loading…")}</p>
      )}
    </div>
  );
}
