import { useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw, Cpu } from "lucide-react";
import { cn } from "@/lib/utils";
import { api, type ModelAdapter } from "@/lib/api";

function StatCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border bg-card p-4 shadow-sm">
      <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">{title}</p>
      <div className="mt-2">{children}</div>
    </div>
  );
}

type Draft = {
  priority: number;
  fallback_adapter_id: string;
  rpm: number;
  max_concurrent: number;
};

function draftFromAdapter(adapter: ModelAdapter): Draft {
  return {
    priority: adapter.priority,
    fallback_adapter_id: adapter.fallback_adapter_id ?? "",
    rpm: adapter.rate_limit.rpm,
    max_concurrent: adapter.rate_limit.max_concurrent,
  };
}

export function ModelAdapters() {
  const [adapters, setAdapters] = useState<ModelAdapter[]>([]);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.getModelAdapters();
      const list = res.adapters ?? [];
      setAdapters(list);
      setDrafts(Object.fromEntries(list.map((a) => [a.adapter_id, draftFromAdapter(a)])));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const patchDraft = (adapterId: string, patch: Partial<Draft>) => {
    setDrafts((prev) => ({ ...prev, [adapterId]: { ...prev[adapterId], ...patch } }));
  };

  const toggleEnabled = async (adapter: ModelAdapter) => {
    setBusy(adapter.adapter_id);
    setError(null);
    try {
      const res = await api.updateModelAdapter(adapter.adapter_id, { enabled: !adapter.enabled });
      const list = res.adapters ?? [];
      setAdapters(list);
      setDrafts(Object.fromEntries(list.map((a) => [a.adapter_id, draftFromAdapter(a)])));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const saveDraft = async (adapter: ModelAdapter) => {
    const draft = drafts[adapter.adapter_id];
    if (!draft) return;
    setBusy(adapter.adapter_id);
    setError(null);
    try {
      const res = await api.updateModelAdapter(adapter.adapter_id, {
        priority: draft.priority,
        fallback_adapter_id: draft.fallback_adapter_id || null,
        rate_limit: { rpm: draft.rpm, max_concurrent: draft.max_concurrent },
      });
      const list = res.adapters ?? [];
      setAdapters(list);
      setDrafts(Object.fromEntries(list.map((a) => [a.adapter_id, draftFromAdapter(a)])));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const byKind = (kind: string) => adapters.filter((a) => a.kind === kind);

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Cpu className="h-5 w-5 text-muted-foreground" />
          <h1 className="text-lg font-semibold">Model Adapters</h1>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
        >
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
          Refresh
        </button>
      </div>
      <p className="text-sm text-muted-foreground">
        LLM/embedding providers used by the news pipeline. Highest-priority enabled adapter of
        each kind is used first; disabling one walks its fallback chain automatically.
      </p>

      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-300">
          {error}
        </div>
      )}

      {(["generative", "embedding"] as const).map((kind) => {
        const list = byKind(kind);
        if (!list.length) return null;
        return (
          <div key={kind} className="space-y-3">
            <h2 className="text-sm font-semibold capitalize text-muted-foreground">{kind} adapters</h2>
            <div className="grid gap-3 sm:grid-cols-2">
              {list.map((adapter) => {
                const draft = drafts[adapter.adapter_id] ?? draftFromAdapter(adapter);
                const isBusy = busy === adapter.adapter_id;
                return (
                  <StatCard key={adapter.adapter_id} title={adapter.adapter_id}>
                    <div className="space-y-3">
                      <div className="flex items-center justify-between">
                        <div>
                          <p className="text-sm font-medium">{adapter.provider}</p>
                          <p className="text-xs text-muted-foreground">{adapter.model}</p>
                        </div>
                        <label className="flex items-center gap-1.5 text-xs">
                          <input
                            type="checkbox"
                            checked={adapter.enabled}
                            disabled={isBusy}
                            onChange={() => toggleEnabled(adapter)}
                            className="rounded border-border"
                          />
                          <span className={cn(adapter.enabled ? "text-green-600 dark:text-green-400" : "text-muted-foreground")}>
                            {adapter.enabled ? "enabled" : "disabled"}
                          </span>
                        </label>
                      </div>

                      <div className="grid grid-cols-2 gap-2">
                        <label className="space-y-1 text-xs">
                          <span className="text-muted-foreground">Priority</span>
                          <input
                            type="number"
                            value={draft.priority}
                            onChange={(e) => patchDraft(adapter.adapter_id, { priority: Number(e.target.value) })}
                            className="w-full rounded border border-border bg-background px-2 py-1 text-sm"
                          />
                        </label>
                        <label className="space-y-1 text-xs">
                          <span className="text-muted-foreground">Fallback adapter</span>
                          <select
                            value={draft.fallback_adapter_id}
                            onChange={(e) => patchDraft(adapter.adapter_id, { fallback_adapter_id: e.target.value })}
                            className="w-full rounded border border-border bg-background px-2 py-1 text-sm"
                          >
                            <option value="">(none)</option>
                            {adapters
                              .filter((a) => a.kind === adapter.kind && a.adapter_id !== adapter.adapter_id)
                              .map((a) => (
                                <option key={a.adapter_id} value={a.adapter_id}>
                                  {a.adapter_id}
                                </option>
                              ))}
                          </select>
                        </label>
                        <label className="space-y-1 text-xs">
                          <span className="text-muted-foreground">Requests / min</span>
                          <input
                            type="number"
                            value={draft.rpm}
                            onChange={(e) => patchDraft(adapter.adapter_id, { rpm: Number(e.target.value) })}
                            className="w-full rounded border border-border bg-background px-2 py-1 text-sm"
                          />
                        </label>
                        <label className="space-y-1 text-xs">
                          <span className="text-muted-foreground">Max concurrent</span>
                          <input
                            type="number"
                            value={draft.max_concurrent}
                            onChange={(e) => patchDraft(adapter.adapter_id, { max_concurrent: Number(e.target.value) })}
                            className="w-full rounded border border-border bg-background px-2 py-1 text-sm"
                          />
                        </label>
                      </div>

                      <button
                        onClick={() => saveDraft(adapter)}
                        disabled={isBusy}
                        className="w-full rounded-lg border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
                      >
                        {isBusy ? "Saving…" : "Save"}
                      </button>
                    </div>
                  </StatCard>
                );
              })}
            </div>
          </div>
        );
      })}

      {!loading && !adapters.length && (
        <p className="text-sm text-muted-foreground">No model adapters registered.</p>
      )}
    </div>
  );
}
