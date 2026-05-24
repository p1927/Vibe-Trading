import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Check,
  Circle,
  Loader2,
  Plus,
  RefreshCcw,
  Target,
  X,
} from "lucide-react";
import type { GoalCriterion, GoalSnapshot } from "@/lib/api";

interface GoalDrawerProps {
  open: boolean;
  snapshot: GoalSnapshot | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
  onRefresh: () => void;
  onStart: (objective: string) => Promise<void>;
  onAddEvidence: (criterionId: string, text: string) => Promise<void>;
}

function isCriterionStatusMet(status: string): boolean {
  return !["", "pending", "open", "unsatisfied"].includes(status.toLowerCase());
}

function criterionEvidenceCount(snapshot: GoalSnapshot | null, criterionId: string): number {
  if (!snapshot) return 0;
  return snapshot.evidence.filter((item) => item.criterion_id === criterionId).length;
}

function statusLabel(status: string): string {
  return status.replace(/_/g, " ");
}

function criterionIndexLabel(criterion: GoalCriterion, index: number): string {
  const step = criterion.protocol_step?.match(/step_(\d+)/)?.[1];
  return step || String(index + 1);
}

export function getGoalProgress(snapshot: GoalSnapshot | null): { met: number; total: number; label: string } {
  const total = snapshot?.criteria.length ?? 0;
  const met = snapshot?.criteria.filter((item) => isCriterionStatusMet(item.status)).length ?? 0;
  return {
    met,
    total,
    label: total > 0 ? `${met}/${total}` : "",
  };
}

export function GoalDrawer({
  open,
  snapshot,
  loading,
  error,
  onClose,
  onRefresh,
  onStart,
  onAddEvidence,
}: GoalDrawerProps) {
  const [objective, setObjective] = useState("");
  const [evidenceText, setEvidenceText] = useState("");
  const [criterionId, setCriterionId] = useState("");
  const [submitting, setSubmitting] = useState<"start" | "evidence" | null>(null);

  const criteria = useMemo(() => snapshot?.criteria ?? [], [snapshot]);
  const progress = useMemo(() => getGoalProgress(snapshot), [snapshot]);

  useEffect(() => {
    if (criteria.length === 0 && criterionId) {
      setCriterionId("");
      return;
    }
    if (!criterionId && criteria.length > 0) {
      setCriterionId(criteria[0].criterion_id);
    }
    if (criterionId && criteria.length > 0 && !criteria.some((item) => item.criterion_id === criterionId)) {
      setCriterionId(criteria[0].criterion_id);
    }
  }, [criterionId, criteria]);

  if (!open) return null;

  const handleStart = async () => {
    const trimmed = objective.trim();
    if (!trimmed) return;
    setSubmitting("start");
    try {
      await onStart(trimmed);
      setObjective("");
    } finally {
      setSubmitting(null);
    }
  };

  const handleAddEvidence = async () => {
    const trimmed = evidenceText.trim();
    if (!criterionId || !trimmed) return;
    setSubmitting("evidence");
    try {
      await onAddEvidence(criterionId, trimmed);
      setEvidenceText("");
    } finally {
      setSubmitting(null);
    }
  };

  return (
    <aside
      aria-label="Goal panel"
      className="fixed right-6 top-20 z-40 flex max-h-[calc(100vh-7.5rem)] w-[min(390px,calc(100vw-2rem))] flex-col overflow-hidden rounded-xl border bg-background/95 shadow-2xl backdrop-blur-xl"
    >
      <div className="border-b px-4 py-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Target className="h-4 w-4 text-primary" aria-hidden="true" />
              <h2 className="text-sm font-semibold text-foreground">Research Goal</h2>
            </div>
            {snapshot ? (
              <p className="mt-2 line-clamp-3 text-xs leading-relaxed text-muted-foreground">
                {snapshot.goal.objective}
              </p>
            ) : (
              <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                Attach a research goal to keep this session evidence-driven.
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            aria-label="Close goal panel"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        <div className="mt-3 flex items-center justify-between gap-3">
          {snapshot ? (
            <span className="inline-flex h-6 items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 text-[11px] font-medium capitalize text-emerald-600 dark:text-emerald-400">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" aria-hidden="true" />
              {statusLabel(snapshot.goal.status)}
            </span>
          ) : (
            <span className="inline-flex h-6 items-center gap-1.5 rounded-full border bg-muted/50 px-2.5 text-[11px] font-medium text-muted-foreground">
              <Circle className="h-2.5 w-2.5" aria-hidden="true" />
              No goal
            </span>
          )}
          <button
            type="button"
            onClick={onRefresh}
            className="inline-flex h-7 items-center gap-1.5 rounded-md border bg-background px-2 text-[11px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
            disabled={loading}
          >
            {loading ? (
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
            ) : (
              <RefreshCcw className="h-3 w-3" aria-hidden="true" />
            )}
            Refresh
          </button>
        </div>
      </div>

      <div className="grid gap-4 overflow-auto p-4">
        {error && (
          <div className="flex gap-2 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-xs leading-relaxed text-destructive">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span>{error}</span>
          </div>
        )}

        {snapshot && (
          <section className="grid gap-2">
            <div className="flex items-center justify-between text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              <span>Acceptance Criteria</span>
              <span className="font-mono tracking-normal text-foreground">{progress.label} met</span>
            </div>
            <div className="grid gap-2">
              {criteria.map((criterion, index) => {
                const count = criterionEvidenceCount(snapshot, criterion.criterion_id);
                const met = isCriterionStatusMet(criterion.status);
                return (
                  <div
                    key={criterion.criterion_id}
                    className="grid grid-cols-[1.25rem_minmax(0,1fr)_auto] items-start gap-2 rounded-lg border bg-muted/20 p-2.5"
                  >
                    <span
                      className={[
                        "mt-0.5 inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px]",
                        met ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" : "bg-muted text-muted-foreground",
                      ].join(" ")}
                    >
                      {met ? <Check className="h-3 w-3" aria-hidden="true" /> : criterionIndexLabel(criterion, index)}
                    </span>
                    <div className="min-w-0">
                      <div className="text-xs font-medium leading-snug text-foreground">
                        {criterion.text}
                      </div>
                      <div className="mt-1 text-[11px] text-muted-foreground">
                        {statusLabel(criterion.status)}
                      </div>
                    </div>
                    <span className="rounded-full border px-2 py-0.5 text-[10px] text-muted-foreground">
                      {count} ev
                    </span>
                  </div>
                );
              })}
            </div>
          </section>
        )}

        {snapshot && criteria.length > 0 && (
          <section className="grid gap-2">
            <div className="flex items-center justify-between text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              <span>Add Evidence</span>
              <span className="font-mono tracking-normal text-foreground">{snapshot.evidence_count} total</span>
            </div>
            <div className="grid gap-2 rounded-lg border border-primary/25 bg-primary/5 p-2.5">
              <select
                value={criterionId}
                onChange={(event) => setCriterionId(event.target.value)}
                className="h-8 rounded-md border bg-background px-2 text-xs outline-none focus:ring-2 focus:ring-primary/25"
                aria-label="Evidence criterion"
              >
                {criteria.map((criterion, index) => (
                  <option key={criterion.criterion_id} value={criterion.criterion_id}>
                    {criterionIndexLabel(criterion, index)}. {criterion.text}
                  </option>
                ))}
              </select>
              <textarea
                value={evidenceText}
                onChange={(event) => setEvidenceText(event.target.value)}
                aria-label="Evidence note"
                placeholder="Paste a concise evidence note or artifact summary."
                className="min-h-20 resize-y rounded-md border bg-background px-3 py-2 text-xs leading-relaxed outline-none placeholder:text-muted-foreground focus:ring-2 focus:ring-primary/25"
              />
              <button
                type="button"
                onClick={handleAddEvidence}
                disabled={!evidenceText.trim() || !criterionId || submitting === "evidence"}
                className="inline-flex items-center gap-1.5 justify-self-end rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
              >
                {submitting === "evidence" ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                ) : (
                  <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                )}
                Attach evidence
              </button>
            </div>
          </section>
        )}

        <section className="grid gap-2">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            Start New Goal
          </div>
          <div className="grid gap-2">
            <textarea
              value={objective}
              onChange={(event) => setObjective(event.target.value)}
              aria-label="Research goal"
              placeholder="Research whether SOL relative strength is beta or idiosyncratic flow."
              className="min-h-20 resize-y rounded-md border bg-background px-3 py-2 text-xs leading-relaxed outline-none placeholder:text-muted-foreground focus:ring-2 focus:ring-primary/25"
            />
            <button
              type="button"
              onClick={handleStart}
              disabled={!objective.trim() || submitting === "start"}
              className="inline-flex items-center gap-1.5 justify-self-end rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
            >
              {submitting === "start" ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <Target className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              Start goal
            </button>
          </div>
        </section>
      </div>
    </aside>
  );
}
