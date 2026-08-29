import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { api, ApiError, type ScheduledJobPreview } from "@/lib/api";

/**
 * Description + best-effort live preview for one scheduled job.
 *
 * Mirrors LiveLogTail's in-row expand convention (no portal/modal chrome —
 * closing is the parent row's chevron toggle) and AdvisoryCandidateDetailModal's
 * fetch-on-mount pattern (cancelled guard, loading/error/data state).
 */
export function ScheduledJobDetailPanel({ jobId }: { jobId: string }) {
  const { t } = useTranslation();
  const [data, setData] = useState<ScheduledJobPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .getScheduledRunPreview(jobId)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  return (
    <div className="w-full space-y-1.5 rounded-md border bg-muted/30 p-3 text-xs">
      {loading ? (
        <div className="flex items-center gap-1.5 text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          {t("scheduled.detailsLoading")}
        </div>
      ) : error ? (
        <p role="alert" className="text-danger">
          {error}
        </p>
      ) : data ? (
        <>
          <p className="text-muted-foreground">{data.description}</p>
          {data.preview_available ? (
            <div className="space-y-1">
              <p className="font-medium text-foreground">{t("scheduled.previewHeading")}</p>
              {data.preview_note && <p className="text-muted-foreground">{data.preview_note}</p>}
              {data.preview_items.length > 0 && (
                <ul className="list-disc space-y-0.5 pl-4">
                  {data.preview_items.map((item, index) => (
                    <li key={index} className="break-all">
                      {typeof item === "string" ? item : JSON.stringify(item)}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ) : data.preview_error ? (
            <p className="text-danger">
              {t("scheduled.previewError", { error: data.preview_error })}
            </p>
          ) : (
            <p className="italic text-muted-foreground">{t("scheduled.previewUnavailable")}</p>
          )}
        </>
      ) : null}
    </div>
  );
}
