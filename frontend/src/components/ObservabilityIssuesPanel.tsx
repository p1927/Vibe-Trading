import { useCallback, useEffect, useState } from "react";
import { api, type ObservabilityIssue } from "@/lib/api";

/** Open observability issues (D365): what the alert paths emit (INDmoney token expiry, capture-loop
 * stops, ...) listed with a Resolve button, from the one `/trade/observability/issues` API. */
export function ObservabilityIssuesPanel({ refreshKey }: { refreshKey?: number }) {
  const [issues, setIssues] = useState<ObservabilityIssue[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setIssues((await api.getObservabilityIssues()).issues);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load issues");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  const resolve = async (issueId: string) => {
    try {
      await api.resolveObservabilityIssue(issueId);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to resolve issue");
    }
  };

  if (!issues.length && !error) return null;
  return (
    <section aria-label="Open issues" className="mt-2 space-y-1.5">
      {error ? <p className="text-xs text-red-700 dark:text-red-300">{error}</p> : null}
      {issues.map((issue) => (
        <div key={issue.issue_id} className="flex items-start justify-between gap-3 rounded-lg border border-red-500/30 bg-red-500/5 px-3 py-2">
          <div className="min-w-0 text-sm">
            <p className="font-medium">
              <span className="text-[11px] uppercase text-muted-foreground">{issue.module}</span> {issue.summary || issue.event}
            </p>
            {issue.suggested_action ? <p className="text-xs text-muted-foreground">{issue.suggested_action}</p> : null}
            <p className="text-[11px] text-muted-foreground">
              {issue.count}x, last {new Date(issue.last_seen).toLocaleString()}
            </p>
          </div>
          <button
            type="button"
            onClick={() => void resolve(issue.issue_id)}
            className="shrink-0 rounded-lg border bg-background px-2.5 py-1 text-xs hover:bg-muted/50"
          >
            Resolve
          </button>
        </div>
      ))}
    </section>
  );
}
