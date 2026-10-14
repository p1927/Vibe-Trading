import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

export type AlphaJobStatus = "idle" | "submitting" | "streaming" | "done" | "error";

export interface AlphaJobProgress {
  n_done: number;
  n_total: number;
  current_alpha_id?: string;
}

interface Messages {
  /** Shown when the final `result` payload cannot be parsed. */
  unreadable: string;
  /** Fallback for a stream error without a server message. */
  streamError: string;
}

/**
 * The one lifecycle of an alpha bench / compare job (D306: BenchView and CompareView each carried a copy):
 * create the job, open its single-use-ticket SSE stream, follow `progress` / `result` / `done` / `error`.
 * A raw EventSource (not `useSSE`, which drops these event names). The stream is closed on unmount even when
 * the ticket request is still in flight.
 */
export function useAlphaJobStream<R>(messages: Messages) {
  const [status, setStatus] = useState<AlphaJobStatus>("idle");
  const [jobId, setJobId] = useState<string | null>(null);
  const [progress, setProgress] = useState<AlphaJobProgress | null>(null);
  const [result, setResult] = useState<R | null>(null);
  const sourceRef = useRef<EventSource | null>(null);
  // The synthetic EventSource `error` fired on the close that follows `done` must not surface as a failure.
  const doneRef = useRef(false);
  const unreadableRef = useRef(false);
  const unmountedRef = useRef(false);
  const messagesRef = useRef(messages);
  messagesRef.current = messages;

  useEffect(() => {
    unmountedRef.current = false;
    return () => {
      unmountedRef.current = true;
      sourceRef.current?.close();
      sourceRef.current = null;
    };
  }, []);

  const closeSource = (source: EventSource) => {
    source.close();
    if (sourceRef.current === source) sourceRef.current = null;
  };

  const attach = async (streamUrl: () => Promise<string>) => {
    setStatus("streaming");
    const url = await streamUrl();
    if (unmountedRef.current) return;
    const source = new EventSource(url);
    sourceRef.current = source;

    source.addEventListener("progress", (e) => {
      try {
        setProgress(JSON.parse((e as MessageEvent).data) as AlphaJobProgress);
      } catch {
        /* a malformed progress tick is not worth failing the run */
      }
    });
    source.addEventListener("result", (e) => {
      try {
        setResult(JSON.parse((e as MessageEvent).data) as R);
      } catch {
        // The result is the whole point of the run: a payload we cannot read is a failed run, not a quiet one.
        unreadableRef.current = true;
        toast.error(messagesRef.current.unreadable);
      }
    });
    source.addEventListener("done", () => {
      doneRef.current = true;
      setStatus(unreadableRef.current ? "error" : "done");
      closeSource(source);
    });
    source.addEventListener("error", (e) => {
      if (doneRef.current) {
        closeSource(source);
        return;
      }
      let msg = messagesRef.current.streamError;
      try {
        const data = JSON.parse((e as MessageEvent).data || "{}");
        if (typeof data.message === "string") msg = data.message;
      } catch {
        /* network-level error, no payload */
      }
      toast.error(msg);
      setStatus("error");
      closeSource(source);
    });
  };

  /** Creates the job and follows it. A failure to create/attach sets status "error" and is rethrown for the caller to report. */
  const start = useCallback(
    async (create: () => Promise<{ job_id: string }>, streamUrl: (jobId: string) => Promise<string>) => {
      setStatus("submitting");
      setProgress(null);
      setResult(null);
      doneRef.current = false;
      unreadableRef.current = false;
      sourceRef.current?.close();
      sourceRef.current = null;
      try {
        const res = await create();
        setJobId(res.job_id);
        await attach(() => streamUrl(res.job_id));
      } catch (err) {
        setStatus("error");
        throw err;
      }
    },
    // `attach` only reads refs and the stable setters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  return { status, jobId, progress, result, busy: status === "submitting" || status === "streaming", start };
}
