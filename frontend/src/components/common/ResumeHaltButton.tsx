import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, RotateCcw } from "lucide-react";
import type { LiveHaltRecord } from "@/lib/api";
import { formatIst } from "@/lib/istTime";

/** Who / why / when a halt was tripped, or an explicit "attribution unknown" (never a blank). */
export function haltDetail(
  record: LiveHaltRecord | null | undefined,
  t: (key: never, options?: Record<string, unknown>) => string,
): string {
  const parts: string[] = [];
  if (record?.by) parts.push(t("agent.haltTrippedBy" as never, { by: record.by }));
  if (record?.reason) parts.push(t("agent.haltTrippedReason" as never, { reason: record.reason }));
  if (record?.tripped_at) {
    const when = new Date(record.tripped_at);
    parts.push(t("agent.haltTrippedAt" as never, {
      at: Number.isNaN(when.getTime()) ? record.tripped_at : formatIst(when),
    }));
  }
  return parts.length > 0 ? parts.join(" · ") : t("agent.haltAttributionUnknown" as never);
}

/**
 * The one way a live-trading halt is lifted (D306): a halt (kill switch, risk engine, daily loss, manual) is only ever
 * cleared by a human, after a second, explicit confirmation. The Agent chat panel, the Autonomous hub banner and an
 * agent card with a trading halt all render this; nothing in the UI clears a halt on a single click.
 */
export function ResumeHaltButton({
  onConfirm,
  resuming = false,
  label,
  prompt,
}: {
  onConfirm: () => void | Promise<unknown>;
  resuming?: boolean;
  /** The first-step button text (default: "Resume connector"). */
  label?: string;
  /** What the confirmation says is being cleared (default: the global kill switch). */
  prompt?: string;
}) {
  const { t } = useTranslation();
  const [confirming, setConfirming] = useState(false);
  if (!confirming) {
    return (
      <button
        type="button"
        onClick={() => setConfirming(true)}
        disabled={resuming}
        className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40"
        title={t("agent.resumeConnectorTitle")}
      >
        <RotateCcw className="h-3 w-3" />
        {label ?? t("agent.resumeConnector")}
      </button>
    );
  }
  return (
    <>
      <span className="text-xs text-foreground">{prompt ?? t("agent.resumeConfirmPrompt" as never)}</span>
      <button
        type="button"
        onClick={() => {
          setConfirming(false);
          void onConfirm();
        }}
        disabled={resuming}
        className="inline-flex items-center gap-1.5 rounded-lg border border-destructive/40 bg-destructive/5 px-2.5 py-1 text-xs font-medium text-destructive transition-colors hover:bg-destructive/10 disabled:opacity-40"
      >
        {resuming ? <Loader2 className="h-3 w-3 animate-spin" /> : <RotateCcw className="h-3 w-3" />}
        {t("agent.resumeConfirm" as never)}
      </button>
      <button
        type="button"
        onClick={() => setConfirming(false)}
        className="inline-flex items-center rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
      >
        {t("agent.resumeKeepHalted" as never)}
      </button>
    </>
  );
}
