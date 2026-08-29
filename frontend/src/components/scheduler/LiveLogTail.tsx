import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";

interface LogEntry {
  seq: number;
  at: number;
  message: string;
}

type TailStatus = "connecting" | "streaming" | "closed" | "error";

/**
 * Live-log-tail for one scheduled job's in-flight run.
 *
 * Mirrors AlphaZoo.tsx's `CompareView` raw-`EventSource` lifecycle (job-scoped,
 * mount/unmount-per-row) rather than the shared `useSSE` hook, which is wired
 * for the single main chat/dashboard connection and its fixed event-type
 * whitelist — a per-row tail that opens and closes with row expansion doesn't
 * fit that shape. Mount only when the row is expanded; unmounting closes the
 * connection.
 */
export function LiveLogTail({ streamUrl }: { streamUrl: () => Promise<string> }) {
  const { t } = useTranslation();
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [status, setStatus] = useState<TailStatus>("connecting");
  const [finalStatus, setFinalStatus] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const closedRef = useRef(false);

  useEffect(() => {
    closedRef.current = false;
    let cancelled = false;

    streamUrl()
      .then((url) => {
        if (cancelled || closedRef.current) return;
        const source = new EventSource(url);
        sourceRef.current = source;

        source.addEventListener("open", () => setStatus("streaming"));
        source.addEventListener("log", (e) => {
          try {
            const entry = JSON.parse((e as MessageEvent).data) as LogEntry;
            setEntries((prev) => [...prev, entry]);
            setStatus("streaming");
          } catch {
            /* ignore malformed frame */
          }
        });
        source.addEventListener("status", (e) => {
          try {
            const data = JSON.parse((e as MessageEvent).data) as { status?: string };
            setFinalStatus(data.status ?? null);
          } catch {
            /* ignore */
          }
          setStatus("closed");
          source.close();
          sourceRef.current = null;
        });
        source.addEventListener("error", () => {
          if (closedRef.current) return;
          setStatus("error");
          source.close();
          sourceRef.current = null;
        });
      })
      .catch(() => {
        if (!cancelled) setStatus("error");
      });

    return () => {
      cancelled = true;
      closedRef.current = true;
      sourceRef.current?.close();
      sourceRef.current = null;
    };
    // `streamUrl` is a fresh closure per render from the caller; re-running
    // this effect on identity change would reconnect on every re-render, so
    // it intentionally connects once per mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const el = containerRef.current;
    if (el && typeof el.scrollTo === "function") {
      el.scrollTo({ top: el.scrollHeight });
    }
  }, [entries]);

  return (
    <div className="w-full space-y-1.5 rounded-md border bg-muted/30 p-2">
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        {status === "connecting" && (
          <>
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
            {t("scheduled.liveLogConnecting")}
          </>
        )}
        {status === "streaming" && <>{t("scheduled.liveLogStreaming")}</>}
        {status === "closed" && (
          <>{t("scheduled.liveLogClosed", { status: finalStatus ?? "" })}</>
        )}
        {status === "error" && <>{t("scheduled.liveLogError")}</>}
      </div>
      <div
        ref={containerRef}
        className="max-h-48 overflow-y-auto rounded bg-background p-2 font-mono text-xs"
      >
        {entries.length === 0 ? (
          <p className="text-muted-foreground">{t("scheduled.liveLogEmpty")}</p>
        ) : (
          entries.map((entry) => (
            <p key={entry.seq} className="whitespace-pre-wrap break-words">
              {entry.message}
            </p>
          ))
        )}
      </div>
    </div>
  );
}
