import { useCallback, useEffect, useState } from "react";
import { Check, Loader2, RefreshCw, X, Undo2 } from "lucide-react";
import {
  api,
  type FactorRegistryGapCandidate,
  type FactorRegistryGapStatus,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Review queue for factor names the news attribution pass could not place in the factor
 * registry (DECISION 08's `registry_gap_candidates`).
 *
 * The queue is refreshed by the pipeline itself at the end of every staging batch, so this
 * component only reads and decides -- it never triggers a rescan.
 *
 * IMPORTANT, and the reason `accepted_means` is rendered rather than hidden: "Add" does NOT
 * create a factor. `factors.registry` is built from Python source and a real FactorSpec needs a
 * category and a source binding no UI click can supply. Accepting queues the name for a
 * hand-written spec. The copy here has to keep saying that.
 */

const TABS: { key: FactorRegistryGapStatus; label: string }[] = [
  { key: "pending", label: "Pending" },
  { key: "accepted", label: "Queued" },
  { key: "ignored", label: "Ignored" },
];

function relTime(value?: string | null): string {
  if (!value) return "—";
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return "—";
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export function FactorRegistryGaps() {
  const [tab, setTab] = useState<FactorRegistryGapStatus>("pending");
  const [rows, setRows] = useState<FactorRegistryGapCandidate[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [acceptedMeans, setAcceptedMeans] = useState("");
  const [loading, setLoading] = useState(false);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (status: FactorRegistryGapStatus) => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.listFactorRegistryGaps(status, 200);
      setRows(res.candidates ?? []);
      setCounts(res.counts ?? {});
      setAcceptedMeans(res.accepted_means ?? "");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(tab);
  }, [load, tab]);

  const decide = useCallback(
    async (candidate: string, decision: "accepted" | "ignored" | "reset") => {
      setBusyKey(candidate);
      setError(null);
      try {
        await api.decideFactorRegistryGap({ candidate, decision });
        // Reload rather than patching locally: the decision moves the row out of this tab, and
        // the counts on the other tabs change too.
        await load(tab);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusyKey(null);
      }
    },
    [load, tab],
  );

  return (
    <div className="rounded-xl border bg-card p-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Factor registry gaps
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            Factor names the news pipeline could not match to a registered factor, ranked by how
            many distinct stories asked for them.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load(tab)}
          disabled={loading}
          className="flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] hover:bg-muted/50 disabled:opacity-50"
        >
          <RefreshCw className={cn("h-3 w-3", loading && "animate-spin")} />
          Refresh
        </button>
      </div>

      <div className="mt-3 flex gap-1">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            className={cn(
              "rounded-md px-2 py-1 text-[11px] font-medium",
              tab === t.key ? "bg-primary text-primary-foreground" : "border hover:bg-muted/50",
            )}
          >
            {t.label}
            <span className="ml-1 opacity-70">{counts[t.key] ?? 0}</span>
          </button>
        ))}
      </div>

      {tab === "accepted" && acceptedMeans ? (
        <p className="mt-3 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-900 dark:text-amber-200">
          These are {acceptedMeans}.
        </p>
      ) : null}

      {error ? (
        <p className="mt-3 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-[11px] text-red-800 dark:text-red-200">
          {error}
        </p>
      ) : null}

      <div className="mt-3 space-y-2">
        {loading && !rows.length ? (
          <p className="flex items-center gap-2 px-1 py-6 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading…
          </p>
        ) : !rows.length ? (
          <p className="rounded-lg border border-dashed bg-muted/20 px-4 py-6 text-center text-sm text-muted-foreground">
            {tab === "pending"
              ? "Nothing to review. Unresolved factor names appear here after a news pipeline batch."
              : `No ${tab} candidates.`}
          </p>
        ) : (
          rows.map((row) => (
            <div
              key={row.candidate}
              className="flex flex-wrap items-start justify-between gap-3 rounded-lg border px-3 py-2"
            >
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <code className="rounded bg-muted px-1.5 py-0.5 text-[12px] font-medium">
                    {row.candidate}
                  </code>
                  <span className="text-[10px] text-muted-foreground">
                    {row.distinct_ref_count ?? 0} stories · {row.occurrence_count ?? 0} mentions ·
                    last {relTime(row.last_seen)}
                  </span>
                </div>
                {row.sample_titles?.length ? (
                  <ul className="mt-1 space-y-0.5">
                    {row.sample_titles.slice(0, 2).map((title) => (
                      <li key={title} className="truncate text-[11px] text-muted-foreground">
                        {title}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
              <div className="flex shrink-0 gap-1">
                {tab === "pending" ? (
                  <>
                    <button
                      type="button"
                      disabled={busyKey === row.candidate}
                      onClick={() => void decide(row.candidate, "accepted")}
                      title="Queue this name for a hand-written FactorSpec. Does not modify the registry."
                      className="flex items-center gap-1 rounded-md border border-emerald-500/50 px-2 py-1 text-[11px] text-emerald-700 hover:bg-emerald-500/10 disabled:opacity-50 dark:text-emerald-300"
                    >
                      <Check className="h-3 w-3" /> Add
                    </button>
                    <button
                      type="button"
                      disabled={busyKey === row.candidate}
                      onClick={() => void decide(row.candidate, "ignored")}
                      title="Model noise — stop showing this name."
                      className="flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] text-muted-foreground hover:bg-muted/50 disabled:opacity-50"
                    >
                      <X className="h-3 w-3" /> Ignore
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    disabled={busyKey === row.candidate}
                    onClick={() => void decide(row.candidate, "reset")}
                    title="Return this name to the pending queue."
                    className="flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] text-muted-foreground hover:bg-muted/50 disabled:opacity-50"
                  >
                    <Undo2 className="h-3 w-3" /> Undo
                  </button>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
