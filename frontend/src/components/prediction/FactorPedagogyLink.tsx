import { useEffect, useState } from "react";
import { BookOpen, ChevronDown, Loader2 } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { FactorDetailResponse } from "@/lib/knowledgeEngine";

interface Props {
  factorKey: string;
  label: string;
}

/**
 * Contextual, read-only factor explanation. It lives beside the selected
 * forecast driver so learning never becomes a separate browsing task.
 */
export function FactorPedagogyLink({ factorKey, label }: Props) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<FactorDetailResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setOpen(false);
    setDetail(null);
    setError(null);
    setLoading(false);
  }, [factorKey]);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (!next || detail || loading) return;
    setLoading(true);
    setError(null);
    void api
      .getKnowledgeFactor(factorKey)
      .then((response) => setDetail(response))
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Factor detail is unavailable.");
      })
      .finally(() => setLoading(false));
  };

  const pedagogy = detail?.pedagogy;
  const taxonomy = detail?.taxonomy;
  const rules = pedagogy?.interpretation_rules ?? [];

  return (
    <div className="border-t border-dashed pt-2">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className="inline-flex items-center gap-1.5 text-[10px] font-medium text-foreground/75 underline-offset-4 hover:text-foreground hover:underline"
      >
        <BookOpen className="h-3 w-3" />
        Why {label} matters
        <ChevronDown className={`h-3 w-3 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open ? (
        <div className="mt-2 border-l-2 border-primary/35 pl-3 text-[11px] leading-relaxed text-muted-foreground">
          {loading ? <p className="flex items-center gap-1.5"><Loader2 className="h-3 w-3 animate-spin" /> Loading factor context…</p> : null}
          {error ? <p className="text-red-600 dark:text-red-400">{error}</p> : null}
          {detail && !detail.found ? <p>No reference context is available for this factor yet.</p> : null}
          {detail?.found ? (
            <>
              <p className="font-medium text-foreground">{pedagogy?.summary ?? taxonomy?.description}</p>
              {pedagogy?.caveat ? <p className="mt-1">Watch for: {pedagogy.caveat}</p> : null}
              {pedagogy?.india_caveat ? <p className="mt-1">In India: {pedagogy.india_caveat}</p> : null}
              {rules.length ? (
                <ul className="mt-1.5 list-disc space-y-0.5 pl-4">
                  {rules.slice(0, 3).map((rule) => <li key={rule}>{rule}</li>)}
                </ul>
              ) : null}
              {taxonomy ? (
                <p className="mt-1.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                  {taxonomy.category.replace(/_/g, " ")} · {taxonomy.default_polarity.replace(/_/g, " ")}
                </p>
              ) : null}
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
