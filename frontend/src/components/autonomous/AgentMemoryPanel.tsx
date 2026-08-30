import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import {
  api,
  type MemoryCommitInfo,
  type MemoryEntryDetail,
  type MemoryEntrySummary,
} from "@/lib/api";
import { cn } from "@/lib/utils";

// Per-agent memory viewing/curation panel — 2026-08-29-memory-management-frontend-ui.
// Backed by /memory/entries (2026-08-29-memory-management-http-api). Deliberately no
// auto-poll: a human reviewing memory is not watching a live feed, and every mutating
// action (edit/archive) already refreshes the list itself.

function fmtDate(epochSeconds: number): string {
  if (!epochSeconds) return "—";
  return new Date(epochSeconds * 1000).toLocaleString();
}

function fmtAge(epochSeconds: number): string {
  if (!epochSeconds) return "—";
  const days = (Date.now() / 1000 - epochSeconds) / 86400;
  if (days < 1) return "<1d ago";
  return `${Math.floor(days)}d ago`;
}

function EntryRow({
  entry,
  selected,
  onSelect,
}: {
  entry: MemoryEntrySummary;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "w-full rounded-lg border px-3 py-2 text-left text-sm transition-colors",
        selected
          ? "border-primary/60 bg-primary/5"
          : "border-border/60 bg-card hover:border-border",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="truncate font-medium text-foreground">{entry.title}</span>
        <span className="shrink-0 rounded border px-1.5 py-0.5 text-[10px] text-muted-foreground">
          {entry.memory_type}
        </span>
      </div>
      <div className="mt-1 truncate text-xs text-muted-foreground">{entry.description}</div>
      <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[10px] text-muted-foreground">
        <span>updated {fmtDate(entry.updated_at)}</span>
        <span>·</span>
        <span
          className={cn(entry.importance < 0.2 && "font-medium text-amber-700 dark:text-amber-300")}
          title="Ebbinghaus-decay importance score — quality_score decayed by time since last access, boosted by access_count"
        >
          importance {entry.importance.toFixed(2)}
        </span>
        <span>·</span>
        <span title="Human-assigned quality score at write time">
          quality {entry.quality_score.toFixed(2)}
        </span>
        <span>·</span>
        <span>accessed {entry.access_count}× (last {fmtAge(entry.last_accessed)})</span>
        {entry.agent_id == null && (
          <span className="rounded border border-amber-500/40 px-1 py-0.5 text-amber-700 dark:text-amber-300">
            unscoped / legacy
          </span>
        )}
      </div>
    </button>
  );
}

function HistoryPanel({ entryId }: { entryId: string }) {
  const [commits, setCommits] = useState<MemoryCommitInfo[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string[]>([]);
  const [diff, setDiff] = useState<string | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setCommits(null);
    setDiff(null);
    setSelected([]);
    api
      .getMemoryEntryHistory(entryId)
      .then((res) => {
        if (!cancelled) setCommits(res.commits);
      })
      .catch(() => {
        if (!cancelled) toast.error("Failed to load history for this entry.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [entryId]);

  const toggleSelect = (sha: string) => {
    setDiff(null);
    setSelected((prev) => {
      if (prev.includes(sha)) return prev.filter((s) => s !== sha);
      const next = [...prev, sha];
      return next.length > 2 ? next.slice(1) : next;
    });
  };

  const viewDiff = useCallback(() => {
    if (selected.length !== 2 || !commits) return;
    // commits are newest-first: pick the earlier one as "from".
    const indexOf = (sha: string) => commits.findIndex((c) => c.sha === sha);
    const [a, b] = selected;
    const [fromSha, toSha] = indexOf(a) > indexOf(b) ? [a, b] : [b, a];
    setDiffLoading(true);
    api
      .getMemoryEntryDiff(entryId, fromSha, toSha)
      .then((res) => setDiff(res.diff))
      .catch(() => toast.error("Failed to load diff."))
      .finally(() => setDiffLoading(false));
  }, [selected, commits, entryId]);

  if (loading) return <div className="p-4 text-sm text-muted-foreground">Loading history…</div>;
  if (!commits || commits.length === 0) {
    return (
      <div className="p-4 text-sm text-muted-foreground">
        No version history yet — this entry predates git versioning, or hasn't been
        edited/re-committed since.
      </div>
    );
  }

  return (
    <div className="space-y-3 p-3">
      <p className="text-xs text-muted-foreground">
        Select two commits to view a diff between them.
      </p>
      <ul className="space-y-1.5">
        {commits.map((c) => (
          <li key={c.sha}>
            <label
              className={cn(
                "flex cursor-pointer items-start gap-2 rounded-lg border px-2.5 py-1.5 text-xs",
                selected.includes(c.sha)
                  ? "border-primary/60 bg-primary/5"
                  : "border-border/50 bg-card",
              )}
            >
              <input
                type="checkbox"
                checked={selected.includes(c.sha)}
                onChange={() => toggleSelect(c.sha)}
                className="mt-0.5"
              />
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-[10px] text-muted-foreground">
                    {c.sha.slice(0, 8)}
                  </span>
                  <span className="text-muted-foreground">{c.date}</span>
                </div>
                <div className="truncate text-foreground/90">{c.message}</div>
              </div>
            </label>
          </li>
        ))}
      </ul>
      <button
        type="button"
        disabled={selected.length !== 2 || diffLoading}
        onClick={viewDiff}
        className="rounded-lg border px-3 py-1.5 text-xs font-medium disabled:opacity-40"
      >
        {diffLoading ? "Loading diff…" : "View diff"}
      </button>
      {diff != null && (
        <pre className="max-h-72 overflow-auto rounded-lg border border-border/60 bg-muted/40 p-3 text-[11px] leading-relaxed">
          {diff || "(no textual difference)"}
        </pre>
      )}
    </div>
  );
}

function EntryDetail({
  entry,
  onChanged,
  onArchived,
}: {
  entry: MemoryEntryDetail;
  onChanged: (updated: MemoryEntryDetail) => void;
  onArchived: () => void;
}) {
  const [view, setView] = useState<"content" | "history">("content");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(entry.body);
  const [saving, setSaving] = useState(false);
  const [archiving, setArchiving] = useState(false);

  useEffect(() => {
    setDraft(entry.body);
    setEditing(false);
  }, [entry.id, entry.body]);

  const save = useCallback(() => {
    setSaving(true);
    api
      .updateMemoryEntry(entry.id, { body: draft })
      .then((updated) => {
        onChanged(updated);
        setEditing(false);
        toast.success("Memory entry updated.");
      })
      .catch(() => toast.error("Failed to save edit."))
      .finally(() => setSaving(false));
  }, [entry.id, draft, onChanged]);

  const archive = useCallback(() => {
    if (!window.confirm(`Clear "${entry.title}" from active memory? It stays recoverable in git history and under archive/.`)) {
      return;
    }
    setArchiving(true);
    api
      .archiveMemoryEntry(entry.id)
      .then(() => {
        toast.success("Memory entry archived.");
        onArchived();
      })
      .catch(() => toast.error("Failed to archive entry."))
      .finally(() => setArchiving(false));
  }, [entry.id, entry.title, onArchived]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border/60 px-3 py-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-foreground">{entry.title}</div>
          <div className="truncate text-xs text-muted-foreground">{entry.description}</div>
        </div>
        <div className="flex shrink-0 gap-1">
          <button
            type="button"
            onClick={() => setView("content")}
            className={cn(
              "rounded-lg border px-2 py-1 text-xs",
              view === "content" ? "border-primary/60 bg-primary/5" : "border-border/60",
            )}
          >
            Content
          </button>
          <button
            type="button"
            onClick={() => setView("history")}
            className={cn(
              "rounded-lg border px-2 py-1 text-xs",
              view === "history" ? "border-primary/60 bg-primary/5" : "border-border/60",
            )}
          >
            History
          </button>
        </div>
      </div>

      {view === "content" ? (
        <div className="flex min-h-0 flex-1 flex-col p-3">
          {editing ? (
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              className="min-h-0 flex-1 resize-none rounded-lg border border-border/60 bg-background p-2 font-mono text-xs"
            />
          ) : (
            <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap rounded-lg border border-border/60 bg-muted/30 p-2 text-xs">
              {entry.body}
            </pre>
          )}
          <div className="mt-2 flex shrink-0 items-center gap-2">
            {editing ? (
              <>
                <button
                  type="button"
                  disabled={saving}
                  onClick={save}
                  className="rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50"
                >
                  {saving ? "Saving…" : "Save"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setDraft(entry.body);
                    setEditing(false);
                  }}
                  className="rounded-lg border px-3 py-1.5 text-xs"
                >
                  Cancel
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={() => setEditing(true)}
                className="rounded-lg border px-3 py-1.5 text-xs font-medium"
              >
                Edit
              </button>
            )}
            <button
              type="button"
              disabled={archiving}
              onClick={archive}
              className="ml-auto rounded-lg border border-red-500/40 px-3 py-1.5 text-xs font-medium text-red-600 disabled:opacity-50 dark:text-red-400"
            >
              {archiving ? "Clearing…" : "Clear from memory"}
            </button>
          </div>
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-auto">
          <HistoryPanel entryId={entry.id} />
        </div>
      )}
    </div>
  );
}

const SORT_OPTIONS: Array<{ value: "updated" | "importance" | "last_accessed"; label: string }> = [
  { value: "updated", label: "Recently updated" },
  { value: "importance", label: "Lowest importance first (find stale)" },
  { value: "last_accessed", label: "Least recently accessed first" },
];

export function AgentMemoryPanel({ agentId }: { agentId: string }) {
  const [entries, setEntries] = useState<MemoryEntrySummary[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<MemoryEntryDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailRetryToken, setDetailRetryToken] = useState(0);
  const [sort, setSort] = useState<"updated" | "importance" | "last_accessed">("updated");

  const loadList = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .listAgentMemory({ agentId, sort })
      .then((res) => setEntries(res.entries))
      .catch(() => setError("Failed to load memory entries for this agent."))
      .finally(() => setLoading(false));
  }, [agentId, sort]);

  useEffect(() => {
    loadList();
    setSelectedId(null);
    setDetail(null);
  }, [loadList]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      setDetailError(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    setDetailError(null);
    api
      .getMemoryEntry(selectedId)
      .then((res) => {
        if (!cancelled) setDetail(res);
      })
      .catch(() => {
        if (!cancelled) {
          setDetailError("Failed to load this entry.");
          toast.error("Failed to load entry.");
        }
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId, detailRetryToken]);

  const handleArchived = useCallback(() => {
    setSelectedId(null);
    setDetail(null);
    loadList();
  }, [loadList]);

  const [invalidating, setInvalidating] = useState(false);
  const invalidateCache = useCallback(() => {
    setInvalidating(true);
    api
      .invalidateMemoryCache()
      .then((res) => {
        toast.success(
          res.reindexed > 0
            ? `Search index rebuilt (${res.reindexed} ${res.reindexed === 1 ? "entry" : "entries"}).`
            : "Cache invalidated.",
        );
        loadList();
      })
      .catch(() => toast.error("Failed to invalidate cache."))
      .finally(() => setInvalidating(false));
  }, [loadList]);

  return (
    <div className="grid h-full min-h-0 grid-cols-1 gap-3 p-3 md:grid-cols-[minmax(0,320px)_1fr]">
      <div className="flex min-h-0 flex-col gap-2 overflow-auto">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-foreground">
            Memory {entries ? `(${entries.length})` : ""}
          </h3>
          <div className="flex gap-1">
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as typeof sort)}
              className="rounded-lg border bg-card px-2 py-1 text-xs text-muted-foreground"
              title="Sort — surfaces low-importance / long-unaccessed entries for staleness review"
            >
              {SORT_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={loadList}
              className="rounded-lg border px-2 py-1 text-xs text-muted-foreground hover:text-foreground"
            >
              Refresh
            </button>
            <button
              type="button"
              disabled={invalidating}
              onClick={invalidateCache}
              title="Force a full search-index rebuild from disk"
              className="rounded-lg border px-2 py-1 text-xs text-muted-foreground hover:text-foreground disabled:opacity-50"
            >
              {invalidating ? "Invalidating…" : "Invalidate cache"}
            </button>
          </div>
        </div>
        {loading && <div className="text-sm text-muted-foreground">Loading…</div>}
        {error && <div className="text-sm text-red-600 dark:text-red-400">{error}</div>}
        {!loading && !error && entries?.length === 0 && (
          <div className="text-sm text-muted-foreground">
            No memory entries for this agent yet.
          </div>
        )}
        {entries?.map((e) => (
          <EntryRow
            key={e.id}
            entry={e}
            selected={e.id === selectedId}
            onSelect={() => setSelectedId(e.id)}
          />
        ))}
      </div>
      <div className="min-h-0 rounded-xl border border-border/60 bg-card">
        {!selectedId && (
          <div className="flex h-full items-center justify-center p-6 text-sm text-muted-foreground">
            Select an entry to view, edit, or clear it.
          </div>
        )}
        {selectedId && detailLoading && (
          <div className="flex h-full items-center justify-center p-6 text-sm text-muted-foreground">
            Loading…
          </div>
        )}
        {selectedId && !detailLoading && detailError && (
          <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center text-sm">
            <span className="text-red-600 dark:text-red-400">{detailError}</span>
            <button
              type="button"
              onClick={() => setDetailRetryToken((n) => n + 1)}
              className="rounded-lg border px-3 py-1.5 text-xs font-medium"
            >
              Retry
            </button>
          </div>
        )}
        {selectedId && !detailLoading && !detailError && detail && (
          <EntryDetail entry={detail} onChanged={setDetail} onArchived={handleArchived} />
        )}
      </div>
    </div>
  );
}
