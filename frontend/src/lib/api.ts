import { authHeaders, withAuthTicket } from "@/lib/apiAuth";
import { resolveApiBase } from "@/lib/apiBase";

const BASE = resolveApiBase();

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export const AUTH_REQUIRED_MESSAGE =
  "Remote API access requires an API key. Add it in Settings, or run the backend on localhost for local-only use.";

export function isAuthRequiredError(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
}

export interface CorrelationResponse {
  labels: string[];
  matrix: number[][];
}

async function errorFromResponse(res: Response): Promise<ApiError> {
  let detail = `HTTP ${res.status}`;
  try {
    const body = await res.json();
    detail = body.detail || body.message || detail;
  } catch { /* ignore */ }
  if (res.status === 401 || res.status === 403) {
    detail = AUTH_REQUIRED_MESSAGE;
  }
  return new ApiError(detail, res.status);
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const { headers, ...rest } = options ?? {};
  const mergedHeaders: Record<string, string> = { "Content-Type": "application/json", ...authHeaders() };
  if (headers) {
    new Headers(headers).forEach((value, key) => {
      mergedHeaders[key] = value;
    });
  }
  const res = await fetch(`${BASE}${path}`, {
    ...rest,
    headers: mergedHeaders,
  });
  if (!res.ok) {
    throw await errorFromResponse(res);
  }
  const text = await res.text();
  if (!text) return {} as T;

  const contentType = res.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    const preview = text.slice(0, 80).replace(/\s+/g, " ");
    const looksLikeHtml = /^\s*</.test(text);
    const hint = looksLikeHtml
      ? " Open http://127.0.0.1:5899 for the UI (Vite) or restart the API on port 8899."
      : "";
    throw new ApiError(
      `Expected JSON from ${path}, got ${contentType || "unknown content type"}: ${preview}${hint}`,
      res.status,
    );
  }

  return JSON.parse(text) as T;
}

export interface UploadResult {
  status: string;
  file_path: string;
  filename: string;
}

async function uploadFile(file: File): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${BASE}/upload`, { method: "POST", headers: authHeaders(), body: form });
  if (!res.ok) {
    throw await errorFromResponse(res);
  }
  return res.json();
}

function appendQueryParam(url: string, key: string, value: string): string {
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}${encodeURIComponent(key)}=${encodeURIComponent(value)}`;
}

function parseSseChunk(
  buffer: string,
  onEvent: (eventType: string, data: Record<string, unknown>) => void,
): string {
  const parts = buffer.split("\n\n");
  const remainder = parts.pop() ?? "";
  for (const block of parts) {
    if (!block.trim()) continue;
    let eventType = "message";
    let dataLine = "";
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) eventType = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLine += line.slice(5).trim();
    }
    if (!dataLine) continue;
    try {
      onEvent(eventType, JSON.parse(dataLine) as Record<string, unknown>);
    } catch {
      /* ignore malformed chunk */
    }
  }
  return remainder;
}

export interface StreamIndexPredictionHandlers {
  onLog?: (entry: PipelineLogEntry) => void;
  onDone?: (artifact: IndexPredictionArtifact) => void;
  onError?: (message: string) => void;
}

export interface StreamExternalPredictionsHandlers {
  onLog?: (entry: PipelineLogEntry) => void;
  onDone?: (snapshot: ExternalPredictionSnapshot) => void;
  onError?: (message: string) => void;
}

async function consumeExternalPredictionsSse(
  res: Response,
  handlers: StreamExternalPredictionsHandlers,
): Promise<boolean> {
  if (!res.body) {
    throw new ApiError("Empty stream body", res.status);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let gotDone = false;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = parseSseChunk(buffer, (eventType, data) => {
      if (eventType === "log" && data.entry) {
        handlers.onLog?.(data.entry as PipelineLogEntry);
        return;
      }
      if (eventType === "done" && data.snapshot) {
        gotDone = true;
        handlers.onDone?.(data.snapshot as ExternalPredictionSnapshot);
        return;
      }
      if (eventType === "error") {
        gotDone = true;
        handlers.onError?.(String(data.message ?? "Refresh failed"));
      }
    });
  }
  return gotDone;
}

async function fetchExternalPredictionsRefreshJobSnapshot(
  jobId: string,
): Promise<{ status?: string; snapshot?: ExternalPredictionSnapshot; error?: string } | null> {
  try {
    const res = await fetch(
      `${BASE}/trade/index-prediction/external-predictions/refresh/${encodeURIComponent(jobId)}`,
      { headers: authHeaders() },
    );
    if (!res.ok) return null;
    const payload = (await res.json()) as {
      job?: { status?: string; snapshot?: ExternalPredictionSnapshot; error?: string };
    };
    return payload.job ?? null;
  } catch {
    return null;
  }
}

async function recoverExternalPredictionsJobFromPoll(
  jobId: string,
  handlers: StreamExternalPredictionsHandlers,
): Promise<boolean> {
  const job = await fetchExternalPredictionsRefreshJobSnapshot(jobId);
  if (!job) return false;
  if (job.status === "done" && job.snapshot) {
    handlers.onDone?.(job.snapshot);
    return true;
  }
  if (job.status === "error") {
    handlers.onError?.(job.error || "Refresh failed");
    return true;
  }
  return false;
}

export async function streamExternalPredictionsJob(
  jobId: string,
  handlers: StreamExternalPredictionsHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(
      `${BASE}/trade/index-prediction/external-predictions/refresh/${encodeURIComponent(jobId)}/stream`,
      {
        headers: authHeaders(),
        signal,
      },
    );
  } catch (err) {
    const hint =
      BASE.includes(":8899") || !BASE
        ? " Check the Vibe API is running on port 8899."
        : " Check your network connection and API URL.";
    throw new ApiError(
      `Network error reaching refresh stream.${hint} ${err instanceof Error ? err.message : ""}`.trim(),
      0,
    );
  }
  if (!res.ok) {
    throw await errorFromResponse(res);
  }
  const gotDone = await consumeExternalPredictionsSse(res, handlers);
  if (!gotDone) {
    const recovered = await recoverExternalPredictionsJobFromPoll(jobId, handlers);
    if (recovered) return;
    handlers.onError?.("Refresh stream ended without a result — the server may have timed out.");
  }
}

async function consumeIndexPredictionSse(
  res: Response,
  handlers: StreamIndexPredictionHandlers,
): Promise<boolean> {
  if (!res.body) {
    throw new ApiError("Empty stream body", res.status);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let gotDone = false;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = parseSseChunk(buffer, (eventType, data) => {
      if (eventType === "log" && data.entry) {
        handlers.onLog?.(data.entry as PipelineLogEntry);
        return;
      }
      if (eventType === "done" && data.artifact) {
        gotDone = true;
        handlers.onDone?.(data.artifact as IndexPredictionArtifact);
        return;
      }
      if (eventType === "error") {
        gotDone = true;
        handlers.onError?.(String(data.message ?? "Analysis failed"));
      }
    });
  }
  return gotDone;
}

async function fetchIndexPredictionRunJobSnapshot(
  jobId: string,
  signal?: AbortSignal,
): Promise<IndexPredictionRunJobSnapshot | null> {
  try {
    const res = await fetch(
      `${BASE}/trade/index-prediction/run/${encodeURIComponent(jobId)}`,
      { headers: authHeaders(), signal },
    );
    if (!res.ok) return null;
    const payload = (await res.json()) as IndexPredictionRunJobResponse;
    return payload.job ?? null;
  } catch {
    return null;
  }
}

const ACTIVE_PREDICTION_JOB_STATUSES = new Set(["queued", "running"]);
const POLL_REATTACH_MS = 3000;
const POLL_REATTACH_MAX_MS = 45 * 60 * 1000;

function sleepMs(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const onAbort = () => {
      window.clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      reject(new DOMException("Aborted", "AbortError"));
    };
    const timer = window.setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

async function pollIndexPredictionJobUntilDone(
  jobId: string,
  handlers: StreamIndexPredictionHandlers,
  signal?: AbortSignal,
  options?: { onReconnecting?: (reconnecting: boolean) => void; skipLogsBefore?: number },
): Promise<void> {
  const started = Date.now();
  let lastLogCount = Math.max(0, options?.skipLogsBefore ?? 0);
  options?.onReconnecting?.(true);
  try {
    while (Date.now() - started < POLL_REATTACH_MAX_MS) {
      if (signal?.aborted) return;
      const job = await fetchIndexPredictionRunJobSnapshot(jobId, signal);
      if (job?.logs && job.logs.length > lastLogCount) {
        for (let i = lastLogCount; i < job.logs.length; i += 1) {
          handlers.onLog?.(job.logs[i]!);
        }
        lastLogCount = job.logs.length;
      }
      if (job?.status === "done" && job.artifact) {
        handlers.onDone?.(job.artifact);
        return;
      }
      if (job?.status === "error") {
        handlers.onError?.(job.error || "Analysis failed");
        return;
      }
      if (job?.status && !ACTIVE_PREDICTION_JOB_STATUSES.has(job.status)) {
        handlers.onError?.("Analysis ended unexpectedly");
        return;
      }
      await sleepMs(POLL_REATTACH_MS, signal);
    }
    handlers.onError?.("Analysis timed out waiting for completion (45 min)");
  } catch (err) {
    if (signal?.aborted) return;
    throw err;
  } finally {
    options?.onReconnecting?.(false);
  }
}

async function recoverIndexPredictionJobFromPoll(
  jobId: string,
  handlers: StreamIndexPredictionHandlers,
): Promise<boolean> {
  const job = await fetchIndexPredictionRunJobSnapshot(jobId);
  if (!job) return false;
  if (job.status === "done" && job.artifact) {
    handlers.onDone?.(job.artifact);
    return true;
  }
  if (job.status === "error") {
    handlers.onError?.(job.error || "Analysis failed");
    return true;
  }
  return false;
}

export async function streamIndexPredictionJob(
  jobId: string,
  handlers: StreamIndexPredictionHandlers,
  signal?: AbortSignal,
  options?: { onReconnecting?: (reconnecting: boolean) => void; skipLogsBefore?: number },
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(
      `${BASE}/trade/index-prediction/run/${encodeURIComponent(jobId)}/stream`,
      {
        headers: authHeaders(),
        signal,
      },
    );
  } catch (err) {
    const hint =
      BASE.includes(":8899") || !BASE
        ? " Check the Vibe API is running on port 8899."
        : " Check your network connection and API URL.";
    throw new ApiError(
      `Network error reaching analysis stream.${hint} ${err instanceof Error ? err.message : ""}`.trim(),
      0,
    );
  }
  if (!res.ok) {
    throw await errorFromResponse(res);
  }
  const gotDone = await consumeIndexPredictionSse(res, handlers);
  if (!gotDone) {
    const recovered = await recoverIndexPredictionJobFromPoll(jobId, handlers);
    if (recovered) return;
    const job = await fetchIndexPredictionRunJobSnapshot(jobId);
    if (job?.status && ACTIVE_PREDICTION_JOB_STATUSES.has(job.status)) {
      await pollIndexPredictionJobUntilDone(jobId, handlers, signal, {
        onReconnecting: options?.onReconnecting,
        skipLogsBefore: options?.skipLogsBefore ?? job.logs?.length ?? 0,
      });
      return;
    }
    handlers.onError?.(
      "Analysis stream ended without a result — the server may have timed out. Try without “Refresh all constituents”.",
    );
  }
}

export async function streamIndexPredictionRun(
  body: RunIndexPredictionRequest,
  handlers: StreamIndexPredictionHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const now = () => new Date().toISOString();
  let res: Response;
  try {
    res = await fetch(`${BASE}/trade/index-prediction/run/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    const hint =
      BASE.includes(":8899") || !BASE
        ? " Check the Vibe API is running on port 8899."
        : " Check your network connection and API URL.";
    throw new ApiError(
      `Network error reaching analysis API.${hint} ${err instanceof Error ? err.message : ""}`.trim(),
      0,
    );
  }

  if (!res.ok) {
    // Older API builds lack SSE stream route — fall back to blocking POST /run.
    if (res.status === 404 || res.status === 405) {
      handlers.onLog?.({
        stage: "start",
        message: "Streaming unavailable — running full analysis via standard API…",
        level: "warn",
        at: now(),
      });
      if (body.refresh_constituents) {
        handlers.onLog?.({
          stage: "constituents",
          message: "Refreshing all 50 constituents — this can take several minutes…",
          level: "info",
          at: now(),
        });
      }
      let fallback: Response;
      try {
        fallback = await fetch(`${BASE}/trade/index-prediction/run`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...authHeaders() },
          body: JSON.stringify(body),
          signal,
        });
      } catch (err) {
        throw new ApiError(
          `Network error during analysis.${body.refresh_constituents ? " Try unchecking “Refresh all constituents” for a faster run (~1 min)." : ""} ${err instanceof Error ? err.message : ""}`.trim(),
          0,
        );
      }
      if (!fallback.ok) {
        throw await errorFromResponse(fallback);
      }
      const payload = (await fallback.json()) as IndexPredictionResponse;
      if (payload.status !== "ok" || !payload.artifact) {
        handlers.onError?.(payload.message || "Analysis failed");
        return;
      }
      handlers.onLog?.({
        stage: "done",
        message: "Analysis complete",
        level: "info",
        at: now(),
      });
      handlers.onDone?.(payload.artifact);
      return;
    }
    throw await errorFromResponse(res);
  }

  const gotDone = await consumeIndexPredictionSse(res, handlers);
  if (!gotDone) {
    handlers.onError?.(
      "Analysis stream ended without a result — the server may have timed out. Try without “Refresh all constituents”.",
    );
  }
}

export const api = {
  uploadFile,
  getCorrelation: (codes: string, days: number, method: "pearson" | "spearman") =>
    request<CorrelationResponse>(
      `/correlation?codes=${encodeURIComponent(codes)}&days=${encodeURIComponent(String(days))}&method=${encodeURIComponent(method)}`,
    ),
  listRuns: (limit?: number) => request<RunListItem[]>(`/runs${limit ? `?limit=${encodeURIComponent(String(limit))}` : ""}`),
  getRun: (id: string, params: RunDetailParams = {}) => {
    const q = new URLSearchParams();
    if (params.chart_payload) q.set("chart_payload", params.chart_payload);
    if (params.chart_symbol) q.set("chart_symbol", params.chart_symbol);
    const qs = q.toString();
    return request<RunData>(`/runs/${id}${qs ? `?${qs}` : ""}`);
  },
  getRunCode: (id: string) => request<Record<string, string>>(`/runs/${id}/code`),
  getRunPine: (id: string) => request<PineScriptResult>(`/runs/${id}/pine`),
  listSessions: () => request<SessionItem[]>("/sessions"),
  createSession: (title?: string) => request<SessionItem>("/sessions", { method: "POST", body: JSON.stringify({ title: title || "" }) }),
  deleteSession: (sid: string) => request<{ status: string }>(`/sessions/${sid}`, { method: "DELETE" }),
  renameSession: (sid: string, title: string) => request<{ status: string }>(`/sessions/${sid}`, { method: "PATCH", body: JSON.stringify({ title }) }),
  sendMessage: (sid: string, content: string) => request<{ message_id: string; attempt_id: string }>(`/sessions/${sid}/messages`, { method: "POST", body: JSON.stringify({ content }) }),
  cancelSession: (sid: string) => request<{ status: string }>(`/sessions/${sid}/cancel`, { method: "POST" }),
  getSessionMessages: (sid: string) => request<MessageItem[]>(`/sessions/${sid}/messages`),
  getSessionProvenance: (sid: string) =>
    request<ProvenanceListResponse>(`/sessions/${sid}/provenance`),
  createGoal: (sid: string, body: CreateGoalRequest) =>
    request<GoalSnapshot>(`/sessions/${sid}/goal`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getGoal: (sid: string) => request<GoalSnapshot>(`/sessions/${sid}/goal`),
  updateGoal: (sid: string, body: UpdateGoalRequest) =>
    request<UpdateGoalResponse>(`/sessions/${sid}/goal`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  addGoalEvidence: (sid: string, body: AddGoalEvidenceRequest) =>
    request<AddGoalEvidenceResponse>(`/sessions/${sid}/goal/evidence`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateGoalStatus: (sid: string, body: UpdateGoalStatusRequest) =>
    request<UpdateGoalStatusResponse>(`/sessions/${sid}/goal/status`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  // Returns the bare stream URL (no auth in the query string). The SSE ticket
  // is minted per connect/reconnect inside useSSE (tickets are single-use, so
  // baking one into a cached URL would break reconnection).
  sseUrl: (sid: string, options?: { replay?: "active" }) => {
    let url = `${BASE}/sessions/${sid}/events`;
    if (options?.replay) url = appendQueryParam(url, "replay", options.replay);
    return url;
  },

  // Swarm API
  listSwarmPresets: () => request<SwarmPreset[]>("/swarm/presets"),
  createSwarmRun: (preset_name: string, user_vars: Record<string, string>) =>
    request<{ id: string; status: string }>("/swarm/runs", {
      method: "POST",
      body: JSON.stringify({ preset_name, user_vars }),
    }),
  listSwarmRuns: () => request<SwarmRunSummary[]>("/swarm/runs"),
  getSwarmRun: (id: string) => request<Record<string, unknown>>(`/swarm/runs/${id}`),
  swarmSseUrl: (id: string) => withAuthTicket(`${BASE}/swarm/runs/${id}/events`),
  cancelSwarmRun: (id: string) =>
    request<{ status: string }>(`/swarm/runs/${id}/cancel`, { method: "POST" }),
  retrySwarmRun: (id: string) =>
    request<{ id: string; status: string; preset_name: string }>(`/swarm/runs/${id}/retry`, { method: "POST" }),
  getLLMSettings: () => request<LLMSettings>("/settings/llm"),
  updateLLMSettings: (settings: UpdateLLMSettingsRequest) =>
    request<LLMSettings>("/settings/llm", {
      method: "PUT",
      body: JSON.stringify(settings),
    }),
  getDataSourceSettings: () => request<DataSourceSettings>("/settings/data-sources"),
  updateDataSourceSettings: (settings: UpdateDataSourceSettingsRequest) =>
    request<DataSourceSettings>("/settings/data-sources", {
      method: "PUT",
      body: JSON.stringify(settings),
    }),
  getChannelStatus: () => request<ChannelRuntimeStatus>("/channels/status"),
  startChannels: () => request<ChannelRuntimeActionResponse>("/channels/start", { method: "POST" }),
  stopChannels: () => request<ChannelRuntimeActionResponse>("/channels/stop", { method: "POST" }),
  runChannelPairingCommand: (body: ChannelPairingCommandRequest) =>
    request<ChannelPairingCommandResponse>("/channels/pairing/command", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // Alpha Zoo API
  listAlphas: (params: AlphaListParams = {}) => {
    const q = new URLSearchParams();
    if (params.zoo) q.set("zoo", params.zoo);
    if (params.theme) q.set("theme", params.theme);
    if (params.universe) q.set("universe", params.universe);
    if (params.limit !== undefined) q.set("limit", String(params.limit));
    const qs = q.toString();
    return request<AlphaListResponse>(`/alpha/list${qs ? `?${qs}` : ""}`);
  },
  getAlpha: (alphaId: string) =>
    request<AlphaDetailResponse>(`/alpha/${encodeURIComponent(alphaId)}`),
  createAlphaBench: (body: AlphaBenchRequest) =>
    request<{ status: string; job_id: string }>("/alpha/bench", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  alphaBenchStreamUrl: (jobId: string) =>
    withAuthTicket(`${BASE}/alpha/bench/${encodeURIComponent(jobId)}/stream`),
  createAlphaCompare: (body: AlphaCompareRequest) =>
    request<{ status: string; job_id: string }>("/alpha/compare", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  alphaCompareStreamUrl: (jobId: string) =>
    withAuthTicket(`${BASE}/alpha/compare/${encodeURIComponent(jobId)}/stream`),

  // Connector runtime channel — privileged surface actions (NOT agent tools).
  // commit is the ONLY action that writes a mandate; halt trips the kill switch.
  commitMandate: (body: CommitMandateRequest) =>
    request<CommitMandateResponse>("/mandate/commit", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  listAutonomousAgents: () =>
    request<AutonomousAgentsListResponse>("/autonomous-agents"),
  getAutonomousAgent: (agentId: string) =>
    request<AutonomousAgentInstance>(`/autonomous-agents/${encodeURIComponent(agentId)}`),
  commitAutonomousAgent: (body: CommitAutonomousAgentRequest) =>
    request<CommitAutonomousAgentResponse>("/autonomous-agents/commit", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getOrchestratorSession: () =>
    request<OrchestratorSessionResponse>("/autonomous-agents/orchestrator/session", {
      method: "POST",
    }),
  getLatestAutonomousProposal: (orchestratorSessionId: string) =>
    request<{ status: string; proposal: AutonomousAgentProposal | null }>(
      `/autonomous-agents/proposals/latest?orchestrator_session_id=${encodeURIComponent(orchestratorSessionId)}`,
    ),
  pauseAutonomousAgent: (agentId: string) =>
    request<{ status: string; agent: AutonomousAgentInstance }>(
      `/autonomous-agents/${encodeURIComponent(agentId)}/pause`,
      { method: "POST" },
    ),
  resumeAutonomousAgent: (agentId: string) =>
    request<{ status: string; agent: AutonomousAgentInstance }>(
      `/autonomous-agents/${encodeURIComponent(agentId)}/resume`,
      { method: "POST" },
    ),
  stopAutonomousAgent: (agentId: string) =>
    request<{ status: string; agent: AutonomousAgentInstance }>(
      `/autonomous-agents/${encodeURIComponent(agentId)}/stop`,
      { method: "POST" },
    ),
  deleteAutonomousAgent: (agentId: string) =>
    request<{ status: string; deleted: string }>(
      `/autonomous-agents/${encodeURIComponent(agentId)}`,
      { method: "DELETE" },
    ),
  approveAutonomousPlan: (agentId: string) =>
    request<{ status: string; agent: AutonomousAgentInstance }>(
      `/autonomous-agents/${encodeURIComponent(agentId)}/approve-plan`,
      { method: "POST" },
    ),
  rejectAutonomousPlan: (agentId: string, note?: string) =>
    request<{ status: string; agent: AutonomousAgentInstance }>(
      `/autonomous-agents/${encodeURIComponent(agentId)}/reject-plan`,
      { method: "POST", body: JSON.stringify({ note: note || "" }) },
    ),
  clearAllAutonomousAgents: () =>
    request<ClearAllAutonomousAgentsResponse>("/autonomous-agents/clear-all", {
      method: "POST",
    }),

  haltLive: (session_id?: string, broker?: string, reason?: string) =>
    request<HaltLiveResponse>("/live/halt", {
      method: "POST",
      body: JSON.stringify({ session_id, broker, reason }),
    }),
  resumeLive: (session_id?: string, broker?: string) =>
    request<HaltLiveResponse>("/live/resume", {
      method: "POST",
      body: JSON.stringify({ session_id, broker }),
    }),
  // Read the persistent runtime status across all authorized brokers (SPEC §7.5).
  // Polled by the RunnerStatus panel; a plain authenticated GET, never a chat message.
  getLiveStatus: (signal?: AbortSignal) => request<LiveStatus>("/live/status", { signal }),
  getTradingConnectors: (signal?: AbortSignal) =>
    request<TradingConnectorsResponse>("/trading/connectors", { signal }),
  selectTradingConnector: (profileId: string) =>
    request<SelectTradingConnectorResponse>("/trading/connectors/select", {
      method: "POST",
      body: JSON.stringify({ profile_id: profileId }),
    }),
  checkTradingConnector: (profileId: string, signal?: AbortSignal) =>
    request<TradingConnectorCheckResponse>(
      `/trading/connectors/${encodeURIComponent(profileId)}/check`,
      { signal },
    ),
  authorizeLive: (broker: string) =>
    request<LiveAuthorizeResponse>("/live/authorize", {
      method: "POST",
      body: JSON.stringify({ broker }),
    }),
  // Start/stop the persistent runner (SPEC §7.5). Privileged surface actions, not agent tools.
  startLiveRunner: (broker: string) =>
    request<LiveRunnerResponse>("/live/runner/start", {
      method: "POST",
      body: JSON.stringify({ broker }),
    }),
  stopLiveRunner: (broker: string) =>
    request<LiveRunnerResponse>("/live/runner/stop", {
      method: "POST",
      body: JSON.stringify({ broker }),
    }),

  executeTradeBasket: (body: ExecuteTradeBasketRequest) =>
    request<ExecuteTradeBasketResponse>("/trade/execute-basket", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  fetchTradeCharges: (body: TradeChargesRequest) =>
    request<TradeChargesResponse>("/trade/charges", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getTradeWidget: (widgetId: string) =>
    request<TradePlanWidget>(`/trade/widget/${encodeURIComponent(widgetId)}`),
  getTradeExecutionMode: () => request<TradeExecutionMode>("/trade/execution-mode"),
  getPlanContext: (ticker: string) =>
    request<PlanContextResponse>(`/trade/plan-context/${encodeURIComponent(ticker)}`),
  getHubPlan: (ticker: string, asset = "options", refresh = false) =>
    request<HubPlanResponse>(
      `/trade/hub-plan?ticker=${encodeURIComponent(ticker)}&asset=${encodeURIComponent(asset)}&refresh=${refresh}`,
    ),
  getAgentDebate: (ticker: string) =>
    request<AgentDebateResponse>(`/trade/agent-debate?ticker=${encodeURIComponent(ticker)}`),
  runAgentDebate: (body: RunAgentDebateRequest) =>
    request<AgentDebateResponse>("/trade/run-debate", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getIndexPrediction: (ticker = "NIFTY", horizonDays?: number, refresh = false) => {
    const params = new URLSearchParams({ ticker });
    if (horizonDays != null) params.set("horizon_days", String(horizonDays));
    if (refresh) params.set("refresh", "true");
    return request<IndexPredictionResponse>(`/trade/index-prediction?${params}`);
  },
  runIndexPrediction: (body: RunIndexPredictionRequest) =>
    request<IndexPredictionResponse>("/trade/index-prediction/run", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  startIndexPredictionRun: (body: RunIndexPredictionRequest) =>
    request<IndexPredictionRunStartResponse>("/trade/index-prediction/run/start", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  runIndexForecastLab: (
    ticker = "NIFTY",
    horizonDays?: number,
    mode: "tracks_only" | "combine" = "tracks_only",
    persist = true,
  ) =>
    request<IndexForecastLabResponse>("/trade/index-prediction/forecast-lab", {
      method: "POST",
      body: JSON.stringify({
        ticker,
        horizon_days: horizonDays,
        mode,
        persist,
        use_hub_cache: true,
      }),
    }),
  getActiveIndexPredictionRun: (ticker = "NIFTY") =>
    request<IndexPredictionRunActiveResponse>(
      `/trade/index-prediction/run/active?ticker=${encodeURIComponent(ticker)}`,
    ),
  getIndexPredictionRunJob: (jobId: string) =>
    request<IndexPredictionRunJobResponse>(
      `/trade/index-prediction/run/${encodeURIComponent(jobId)}`,
    ),
  cancelIndexPredictionRun: (jobId: string) =>
    request<{ status: string; message?: string }>(
      `/trade/index-prediction/run/${encodeURIComponent(jobId)}/cancel`,
      { method: "POST" },
    ),
  streamIndexPredictionJob: streamIndexPredictionJob,
  getIndexPredictionFactors: () =>
    request<IndexFactorCatalogResponse>("/trade/index-prediction/factors"),
  streamIndexPredictionRun: streamIndexPredictionRun,
  simulateIndexPrediction: (body: SimulateIndexPredictionRequest) =>
    request<SimulateIndexPredictionResponse>("/trade/index-prediction/simulate", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getIndexPlaygroundContext: (ticker = "NIFTY", refresh = false) => {
    const params = new URLSearchParams({ ticker });
    if (refresh) params.set("refresh", "true");
    return request<IndexPlaygroundContextResponse>(
      `/trade/index-prediction/playground-context?${params}`,
    );
  },
  refreshIndexPrediction: (body: RefreshIndexPredictionRequest) =>
    request<IndexPredictionRefreshResponse>("/trade/index-prediction/refresh", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getIndexPredictionHistory: (ticker = "NIFTY", limit = 50, horizonDays?: number, dailyLast = true) => {
    const params = new URLSearchParams({
      ticker,
      limit: String(limit),
      daily_last: String(dailyLast),
    });
    if (horizonDays != null) params.set("horizon_days", String(horizonDays));
    return request<IndexPredictionHistoryResponse>(`/trade/index-prediction/history?${params}`);
  },
  getIndexFactorHistory: (ticker = "NIFTY", days = 90, factors?: string[]) => {
    const params = new URLSearchParams({ ticker, days: String(days) });
    if (factors?.length) params.set("factors", factors.join(","));
    return request<IndexFactorHistoryResponse>(`/trade/index-prediction/factor-history?${params}`);
  },
  getIndexDerivativesHistory: (days = 365, factors?: string[]) => {
    const factorList =
      factors ?? ["nifty_pcr", "fii_net_5d", "dii_net_5d", "fii_fut_long_short_ratio"];
    const params = new URLSearchParams({ ticker: "NIFTY", days: String(days), factors: factorList.join(",") });
    return request<IndexFactorHistoryResponse>(`/trade/index-prediction/factor-history?${params}`);
  },
  getIndexDayAttribution: (date: string, days = 365) =>
    request<DayAttributionResponse>(
      `/trade/index-prediction/day-attribution?date=${encodeURIComponent(date)}&days=${days}`,
    ),
  getConstituentHistory: (symbol: string, days = 90, weight?: number) => {
    const params = new URLSearchParams({ symbol, days: String(days) });
    if (weight != null && Number.isFinite(weight)) params.set("weight", String(weight));
    return request<ConstituentHistoryResponse>(
      `/trade/index-prediction/constituent-history?${params}`,
    );
  },
  getIndexPredictionSnapshots: (ticker = "NIFTY", limit = 10) =>
    request<IndexPredictionSnapshotsResponse>(
      `/trade/index-prediction/snapshots?ticker=${encodeURIComponent(ticker)}&limit=${limit}`,
    ),
  getIndexPredictionBacktest: (
    ticker = "NIFTY",
    refresh = false,
    days = 180,
    horizonDays?: number,
    includeBottomUp = false,
  ) => {
    const params = new URLSearchParams({
      ticker,
      refresh: String(refresh),
      days: String(days),
      include_bottom_up: String(includeBottomUp),
    });
    if (horizonDays != null) params.set("horizon_days", String(horizonDays));
    return request<IndexBacktestResponse>(`/trade/index-prediction/backtest?${params}`);
  },
  getIndexTrackScoreboard: (
    ticker = "NIFTY",
    refresh = false,
    days = 365,
    horizonDays?: number,
    evalStep = 5,
    cacheOnly = false,
  ) => {
    const params = new URLSearchParams({
      ticker,
      refresh: String(refresh),
      days: String(days),
      eval_step: String(evalStep),
    });
    if (horizonDays != null) params.set("horizon_days", String(horizonDays));
    if (cacheOnly) params.set("cache_only", "true");
    return request<IndexTrackScoreboardResponse>(`/trade/index-prediction/track-scoreboard?${params}`);
  },
  getIndexPredictionMissAnalysis: (ticker = "NIFTY", refresh = false, days = 365, horizonDays?: number) => {
    const params = new URLSearchParams({
      ticker,
      refresh: String(refresh),
      days: String(days),
    });
    if (horizonDays != null) params.set("horizon_days", String(horizonDays));
    return request<IndexMissAnalysisResponse>(`/trade/index-prediction/miss-analysis?${params}`);
  },
  runIndexPredictionMissAnalysis: (ticker = "NIFTY", days = 365, horizonDays?: number) => {
    const params = new URLSearchParams({ ticker, days: String(days) });
    if (horizonDays != null) params.set("horizon_days", String(horizonDays));
    return request<IndexMissAnalysisResponse>(`/trade/index-prediction/miss-analysis/run?${params}`, {
      method: "POST",
    });
  },
  getIndexQuantReview: (ticker = "NIFTY", refresh = false, horizonDays?: number) => {
    const params = new URLSearchParams({ ticker, refresh: String(refresh) });
    if (horizonDays != null) params.set("horizon_days", String(horizonDays));
    return request<IndexQuantReviewResponse>(`/trade/index-prediction/quant-review?${params}`);
  },
  runIndexQuantReview: (ticker = "NIFTY", horizonDays?: number) =>
    request<IndexQuantReviewResponse>("/trade/index-prediction/quant-review/run", {
      method: "POST",
      body: JSON.stringify({ ticker, horizon_days: horizonDays ?? 14, refresh: true }),
    }),
  getIndexPredictionDataAudit: (ticker = "NIFTY", refresh = false, days = 365, horizonDays = 14) => {
    const params = new URLSearchParams({
      ticker,
      refresh: String(refresh),
      days: String(days),
      horizon_days: String(horizonDays),
    });
    return request<IndexDataAuditResponse>(`/trade/index-prediction/data-audit?${params}`);
  },
  getIndexPredictionCounterfactual: (ticker = "NIFTY", refresh = false, days = 365, horizonDays = 14) => {
    const params = new URLSearchParams({
      ticker,
      refresh: String(refresh),
      days: String(days),
      horizon_days: String(horizonDays),
    });
    return request<IndexCounterfactualResponse>(`/trade/index-prediction/counterfactual?${params}`);
  },
  getIndexPredictionNewsImpact: (
    ticker = "NIFTY",
    refresh = false,
    horizonDays = 14,
    includeRejected = false,
  ) => {
    const params = new URLSearchParams({
      ticker,
      refresh: String(refresh),
      horizon_days: String(horizonDays),
      include_rejected: String(includeRejected),
    });
    return request<IndexNewsImpactResponse>(`/trade/index-prediction/news-impact?${params}`);
  },
  createNewsScenarioSession: (body: NewsScenarioSessionRequest) =>
    request<NewsScenarioSessionResponse>("/trade/index-prediction/news-scenarios/session", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  patchNewsScenarioSession: (sessionId: string, body: NewsScenarioSessionPatchRequest) =>
    request<NewsScenarioSessionResponse>(
      `/trade/index-prediction/news-scenarios/session/${encodeURIComponent(sessionId)}`,
      {
        method: "PATCH",
        body: JSON.stringify(body),
      },
    ),
  getNewsScenario: (scenarioId: string, ticker = "NIFTY") =>
    request<NewsEventScenarioResponse>(
      `/trade/index-prediction/news-scenarios/${encodeURIComponent(scenarioId)}?ticker=${encodeURIComponent(ticker)}`,
    ),
  listRecentNewsScenarios: (ticker = "NIFTY", limit = 10) =>
    request<NewsEventScenarioResponse>(
      `/trade/index-prediction/news-scenarios/recent?ticker=${encodeURIComponent(ticker)}&limit=${encodeURIComponent(String(limit))}`,
    ),
  getExternalPredictions: (ticker = "NIFTY", horizonDays = 14) =>
    request<ExternalPredictionsResponse>(
      `/trade/index-prediction/external-predictions?ticker=${encodeURIComponent(ticker)}&horizon_days=${encodeURIComponent(String(horizonDays))}`,
    ),
  refreshExternalPredictions: (ticker = "NIFTY", horizonDays = 14) =>
    request<ExternalPredictionsResponse>("/trade/index-prediction/external-predictions/refresh", {
      method: "POST",
      body: JSON.stringify({ ticker, horizon_days: horizonDays }),
    }),
  startExternalPredictionsRefresh: (ticker = "NIFTY", horizonDays = 14, signal?: AbortSignal) =>
    request<ExternalPredictionsRefreshStartResponse>(
      "/trade/index-prediction/external-predictions/refresh/start",
      {
        method: "POST",
        body: JSON.stringify({ ticker, horizon_days: horizonDays }),
        signal,
      },
    ),
  getActiveExternalPredictionsRefresh: (ticker = "NIFTY", horizonDays = 14, signal?: AbortSignal) =>
    request<ExternalPredictionsRefreshActiveResponse>(
      `/trade/index-prediction/external-predictions/refresh/active?ticker=${encodeURIComponent(ticker)}&horizon_days=${encodeURIComponent(String(horizonDays))}`,
      { signal },
    ),
  getExternalPredictionsRefreshJob: (jobId: string, signal?: AbortSignal) =>
    request<ExternalPredictionsRefreshJobResponse>(
      `/trade/index-prediction/external-predictions/refresh/${encodeURIComponent(jobId)}`,
      { signal },
    ),
  streamExternalPredictionsJob: streamExternalPredictionsJob,
  streamExternalPredictionsRefresh: async (
    ticker = "NIFTY",
    horizonDays = 14,
    handlers: StreamExternalPredictionsHandlers,
    signal?: AbortSignal,
  ) => {
    const res = await fetch(`${BASE}/trade/index-prediction/external-predictions/refresh/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ ticker, horizon_days: horizonDays }),
      signal,
    });
    if (!res.ok) {
      throw await errorFromResponse(res);
    }
    const gotDone = await consumeExternalPredictionsSse(res, handlers);
    if (!gotDone) {
      handlers.onError?.("Refresh stream ended without a result");
    }
  },
  listExternalPredictionSources: (ticker = "NIFTY", watchlistedOnly = false) =>
    request<ExternalPredictionSourcesResponse>(
      `/trade/index-prediction/external-predictions/sources?ticker=${encodeURIComponent(ticker)}&watchlisted_only=${watchlistedOnly}`,
    ),
  addExternalPredictionSource: (body: ExternalPredictionSourceRequest, ticker = "NIFTY") =>
    request<ExternalPredictionSourcesResponse>(
      `/trade/index-prediction/external-predictions/sources?ticker=${encodeURIComponent(ticker)}`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  removeExternalPredictionSource: (sourceId: string, ticker = "NIFTY") =>
    request<ExternalPredictionSourcesResponse>(
      `/trade/index-prediction/external-predictions/sources/${encodeURIComponent(sourceId)}?ticker=${encodeURIComponent(ticker)}`,
      { method: "DELETE" },
    ),
  discoverExternalPredictionSources: (ticker = "NIFTY", limit = 12) =>
    request<ExternalPredictionSourcesResponse>(
      `/trade/index-prediction/external-predictions/discover?ticker=${encodeURIComponent(ticker)}&limit=${encodeURIComponent(String(limit))}`,
    ),
  getIndexPredictionJobs: () =>
    request<IndexPredictionJobsResponse>("/trade/index-prediction/jobs"),
  pauseIndexPredictionJob: (jobId: string) =>
    request<IndexPredictionJobsResponse>(`/trade/index-prediction/jobs/${encodeURIComponent(jobId)}/pause`, {
      method: "POST",
    }),
  resumeIndexPredictionJob: (jobId: string) =>
    request<IndexPredictionJobsResponse>(`/trade/index-prediction/jobs/${encodeURIComponent(jobId)}/resume`, {
      method: "POST",
    }),
  getCaptureRegistry: (entityId = "NIFTY") =>
    request<CaptureRegistryResponse>(`/trade/capture-registry?entity_id=${encodeURIComponent(entityId)}`),
  updateCaptureRegistry: (body: CaptureRegistryUpdateRequest) =>
    request<CaptureRegistryResponse>("/trade/capture-registry", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  runCaptureBackfill: (body: CaptureRegistryBackfillRequest) =>
    request<Record<string, unknown>>("/trade/capture-registry/backfill", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  runCaptureIntraday: (entityId = "NIFTY") =>
    request<Record<string, unknown>>(
      `/trade/capture-registry/intraday?entity_id=${encodeURIComponent(entityId)}`,
      { method: "POST" },
    ),
  getHubStatus: (entityId = "NIFTY") =>
    request<HubStatusResponse>(`/trade/hub/status?entity_id=${encodeURIComponent(entityId)}`),
  getHubNewsPipelineConfig: () =>
    request<HubNewsPipelineConfigResponse>("/trade/hub/news-pipeline/config"),
  updateHubNewsPipelineConfig: (body: HubNewsPipelineConfigUpdate) =>
    request<HubNewsPipelineConfigResponse>("/trade/hub/news-pipeline/config", {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  runHubNewsIngest: (body: HubNewsIngestRequest = { mode: "full" }) =>
    request<HubStagingDrainResponse>("/trade/hub/news-pipeline/ingest", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  runHubNewsMaintenance: (entityId = "NIFTY", lookbackDays = 365) =>
    request<HubStagingDrainResponse>(
      `/trade/hub/news-pipeline/maintenance?entity_id=${encodeURIComponent(entityId)}&lookback_days=${encodeURIComponent(String(lookbackDays))}`,
      { method: "POST" },
    ),
  discardHubNews: (body: HubNewsDiscardRequest) =>
    request<HubNewsDiscardResponse>("/trade/hub/news/discard", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  undoHubNewsDiscard: (body: HubNewsDiscardUndoRequest) =>
    request<HubNewsDiscardResponse>("/trade/hub/news/discard/undo", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listHubDiscardedNews: (entityId = "NIFTY", limit = 50) =>
    request<HubNewsDiscardedListResponse>(
      `/trade/hub/news/discarded?entity_id=${encodeURIComponent(entityId)}&limit=${encodeURIComponent(String(limit))}`,
    ),
  drainHubStaging: (entityId = "NIFTY", limit = 20) =>
    request<HubStagingDrainResponse>(
      `/trade/hub/staging/drain?entity_id=${encodeURIComponent(entityId)}&limit=${encodeURIComponent(String(limit))}`,
      { method: "POST" },
    ),
  bootstrapAutoPaper: (body: AutoPaperBootstrapRequest) =>
    request<AutoPaperBootstrapResponse>("/trade/auto-paper/bootstrap", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  resumeAutoPaper: (body?: AutoPaperResumeRequest) =>
    request<AutoPaperBootstrapResponse>("/trade/auto-paper/resume", {
      method: "POST",
      body: JSON.stringify(body ?? { dispatch: true, fresh_session: true }),
    }),
};

// --- Swarm types ---

export interface SwarmPreset {
  name: string;
  title: string;
  description: string;
  agent_count: number;
  variables: { name: string; description: string; required: boolean }[];
}

export interface SwarmRunSummary {
  id: string;
  preset_name: string;
  status: string;
  created_at: string;
  task_count: number;
  completed_count: number;
}

export interface LLMProviderOption {
  name: string;
  label: string;
  api_key_env?: string | null;
  base_url_env: string;
  default_model: string;
  default_base_url: string;
  api_key_required: boolean;
  auth_type?: string;
  login_command?: string | null;
}

export interface LLMSettings {
  provider: string;
  model_name: string;
  base_url: string;
  api_key_env?: string | null;
  api_key_configured: boolean;
  api_key_hint?: string | null;
  api_key_required: boolean;
  temperature: number;
  timeout_seconds: number;
  max_retries: number;
  reasoning_effort: string;
  sse_timeout_seconds: number;
  env_path: string;
  providers: LLMProviderOption[];
}

export interface UpdateLLMSettingsRequest {
  provider: string;
  model_name: string;
  base_url: string;
  api_key?: string;
  clear_api_key?: boolean;
  temperature: number;
  timeout_seconds: number;
  max_retries: number;
  reasoning_effort?: string;
}

export interface DataSourceSettings {
  tushare_token_configured: boolean;
  tushare_token_hint?: string | null;
  baostock_supported: boolean;
  baostock_installed: boolean;
  baostock_message: string;
  env_path: string;
}

export interface UpdateDataSourceSettingsRequest {
  tushare_token?: string;
  clear_tushare_token?: boolean;
}

export interface ChannelAdapterStatus {
  name: string;
  display_name: string;
  configured: boolean;
  enabled: boolean;
  available: boolean;
  loaded: boolean;
  running: boolean;
  error?: string;
  install_hint?: string;
}

export interface ChannelRuntimeStatus {
  running: boolean;
  inbound_queue: number;
  outbound_queue: number;
  session_count: number;
  channels: Record<string, ChannelAdapterStatus>;
}

export interface ChannelRuntimeActionResponse extends ChannelRuntimeStatus {
  status: string;
}

export interface ChannelPairingCommandRequest {
  channel: string;
  command: string;
}

export interface ChannelPairingCommandResponse {
  channel: string;
  reply: string;
}

// --- Types matching backend API contracts ---

export interface RunListItem {
  run_id: string;
  status: string;
  created_at: string;
  prompt?: string;
  total_return?: number;
  sharpe?: number;
  codes?: string[];
  start_date?: string;
  end_date?: string;
}

export interface RunDetailParams {
  chart_payload?: "summary";
  chart_symbol?: string;
}

export interface PriceBar {
  time: string;
  timestamp?: string;
  code?: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface TradeMarker {
  time: string;
  timestamp?: string;
  code?: string;
  side: "BUY" | "SELL";
  price: number;
  qty?: number;
  reason?: string;
  text?: string;
}

export interface EquityPoint {
  time: string;
  equity: string | number;
  drawdown: string | number;
}

export interface ValidationData {
  monte_carlo?: {
    actual_sharpe: number;
    actual_max_dd: number;
    p_value_sharpe: number;
    p_value_max_dd: number;
    simulated_sharpe_mean: number;
    simulated_sharpe_std: number;
    simulated_sharpe_p5: number;
    simulated_sharpe_p95: number;
    n_simulations: number;
    n_trades: number;
    error?: string;
  };
  bootstrap?: {
    observed_sharpe: number;
    ci_lower: number;
    ci_upper: number;
    median_sharpe: number;
    prob_positive: number;
    confidence: number;
    n_bootstrap: number;
    error?: string;
  };
  walk_forward?: {
    n_windows: number;
    windows: Array<{
      window: number;
      start: string;
      end: string;
      return: number;
      sharpe: number;
      max_dd: number;
      trades: number;
      win_rate: number;
    }>;
    profitable_windows: number;
    consistency_rate: number;
    return_mean: number;
    return_std: number;
    sharpe_mean: number;
    sharpe_std: number;
    error?: string;
  };
}

export interface RunData {
  status: string;
  run_id: string;
  prompt?: string;
  elapsed_seconds?: number;
  run_directory?: string;
  run_stage?: string;
  run_context?: Record<string, unknown>;

  metrics?: BacktestMetrics;
  artifacts?: ArtifactInfo[];
  run_card?: RunCard;
  validation?: ValidationData;

  chart_symbols?: string[];
  price_series?: Record<string, PriceBar[]>;
  indicator_series?: Record<string, Record<string, IndicatorPoint[]>>;
  trade_markers?: TradeMarker[];
  equity_curve?: EquityPoint[];
  trade_log?: Array<Record<string, string>>;
  run_logs?: Array<{ source?: string; line_number?: number; message?: string }>;
}

export interface RunCard {
  schema_version?: string;
  generated_at?: string;
  run_dir?: string;
  backtest?: Record<string, unknown>;
  reproducibility?: Record<string, unknown>;
  data_sources?: string[];
  metrics?: Record<string, unknown>;
  validation?: unknown;
  warnings?: string[];
  artifacts?: RunCardArtifact[];
  [key: string]: unknown;
}

export interface RunCardArtifact {
  path: string;
  size_bytes: number;
  sha256: string;
}

export interface BacktestMetrics {
  final_value: number;
  total_return: number;
  annual_return: number;
  max_drawdown: number;
  sharpe: number;
  win_rate: number;
  trade_count: number;
  [key: string]: number;
}


export interface IndicatorPoint {
  time: string;
  value: number;
}

export interface ArtifactInfo {
  name: string;
  path: string;
  type: string;
  size: number;
  exists: boolean;
}

export interface PineScriptResult {
  exists: boolean;
  content: string | null;
}

export interface SessionItem {
  session_id: string;
  title?: string;
  status?: string;
  created_at?: string;
  updated_at?: string;
  last_attempt_id?: string;
}

// --- Goal types ---

export type GoalStatus =
  | "active"
  | "paused"
  | "waiting_user"
  | "needs_refresh"
  | "insufficient_evidence"
  | "compliance_blocked"
  | "blocked"
  | "budget_limited"
  | "usage_limited"
  | "complete"
  | "cancelled"
  | "superseded";

export type GoalRiskTier =
  | "research_general"
  | "market_specific_short_term"
  | "personalized_advice_or_position_sizing";

export interface GoalRecord {
  goal_id: string;
  session_id: string;
  status: GoalStatus;
  objective: string;
  ui_summary: string;
  source: string;
  protocol: string;
  risk_tier: GoalRiskTier;
  token_budget?: number | null;
  tokens_used: number;
  turn_budget?: number | null;
  turns_used: number;
  time_budget_seconds?: number | null;
  time_used_seconds: number;
  budget_wrapup_sent: boolean;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
  recap?: string | null;
}

export interface GoalClaim {
  claim_id: string;
  goal_id: string;
  session_id: string;
  claim_type: string;
  text: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface GoalCriterion {
  criterion_id: string;
  goal_id: string;
  session_id: string;
  text: string;
  required: boolean;
  status: string;
  freshness_requirement?: string | null;
  protocol_step?: string | null;
  created_at: string;
  updated_at: string;
}

export interface GoalEvidence {
  evidence_id: string;
  goal_id: string;
  session_id: string;
  text: string;
  criterion_id?: string | null;
  claim_id?: string | null;
  evidence_type: string;
  tool_call_id?: string | null;
  run_id?: string | null;
  source_provider?: string | null;
  source_type?: string | null;
  source_uri?: string | null;
  symbol_universe: string[];
  benchmark: string[];
  timeframe?: string | null;
  method?: string | null;
  assumptions: Record<string, unknown>;
  artifact_path?: string | null;
  artifact_hash?: string | null;
  retrieved_at: string;
  data_as_of?: string | null;
  freshness_status: string;
  verification_status: string;
  confidence?: string | null;
  caveat?: string | null;
  contradicts_claim_ids: string[];
  created_at: string;
}

export interface GoalSnapshot {
  goal: GoalRecord;
  claims: GoalClaim[];
  criteria: GoalCriterion[];
  evidence: GoalEvidence[];
  evidence_count: number;
}

export interface CreateGoalRequest {
  objective: string;
  criteria?: string[];
  ui_summary?: string;
  protocol?: string;
  risk_tier?: GoalRiskTier;
  token_budget?: number;
  turn_budget?: number;
  time_budget_seconds?: number;
}

export interface AddGoalEvidenceRequest {
  goal_id: string;
  expected_goal_id: string;
  text: string;
  criterion_id?: string | null;
  claim_id?: string | null;
  evidence_type?: string;
  tool_call_id?: string | null;
  run_id?: string | null;
  source_provider?: string | null;
  source_type?: string | null;
  source_uri?: string | null;
  symbol_universe?: string[];
  benchmark?: string[];
  timeframe?: string | null;
  method?: string | null;
  assumptions?: Record<string, unknown>;
  artifact_path?: string | null;
  artifact_hash?: string | null;
  data_as_of?: string | null;
  confidence?: string | null;
  caveat?: string | null;
  contradicts_claim_ids?: string[];
}

export interface UpdateGoalRequest {
  goal_id: string;
  expected_goal_id: string;
  objective?: string;
  ui_summary?: string;
}

export interface UpdateGoalResponse {
  goal: GoalRecord;
  snapshot: GoalSnapshot;
}

export interface AddGoalEvidenceResponse {
  evidence: GoalEvidence;
  snapshot: GoalSnapshot;
}

export interface GoalAuditRowRequest {
  criterion_id: string;
  result: string;
  evidence_ids?: string[];
  notes?: string;
}

export interface UpdateGoalStatusRequest {
  goal_id: string;
  expected_goal_id: string;
  status: GoalStatus;
  audit?: GoalAuditRowRequest[];
  recap?: string | null;
}

export interface UpdateGoalStatusResponse {
  goal: GoalRecord;
  snapshot: GoalSnapshot;
}

// --- Alpha Zoo types ---

export interface AlphaListParams {
  zoo?: string;
  theme?: string;
  universe?: string;
  limit?: number;
}

export interface AlphaSummary {
  id: string;
  zoo: string;
  theme: string[];
  universe: string[];
  nickname?: string;
  decay_horizon?: number | null;
  min_warmup_bars?: number | null;
  requires_sector?: boolean;
}

export interface AlphaListResponse {
  status: string;
  alphas: AlphaSummary[];
  total: number;
  returned: number;
  truncated: boolean;
}

export interface AlphaDetail {
  id: string;
  zoo: string;
  module_path?: string;
  meta: Record<string, unknown>;
}

export interface AlphaDetailResponse {
  status: string;
  alpha: AlphaDetail;
  source_code: string;
}

export interface AlphaBenchRequest {
  zoo: string;
  universe: string;
  period: string;
  top?: number;
}

export interface AlphaBenchTopRow {
  id: string;
  ic_mean: number;
  ir: number;
  theme: string[];
  formula_latex: string;
  category: "alive" | "reversed" | "dead";
}

export interface AlphaBenchResult {
  alive: number;
  reversed: number;
  dead: number;
  skipped?: number;
  top5_by_ir: AlphaBenchTopRow[];
  dead_examples: AlphaBenchTopRow[];
  by_theme: Record<string, { alive: number; reversed: number; dead: number }>;
}

export interface AlphaCompareRequest {
  alpha_ids: string[];
  universe: string;
  period: string;
  /** One of: ir | ic_mean | ic_positive_ratio | ic_count (default ir). */
  sort?: string;
}

export interface AlphaCompareRow {
  rank: number;
  id: string;
  zoo: string;
  ic_mean: number;
  ic_std: number;
  ir: number;
  ic_positive_ratio: number;
  ic_count: number;
  /** `delta_<sort>_vs_best` — gap to the top-ranked alpha on the active metric. */
  [deltaKey: string]: number | string;
}

export interface AlphaCompareSkip {
  id: string;
  reason: string;
}

export interface AlphaCompareResult {
  universe: string;
  period: string;
  sort: string;
  n_compared: number;
  n_skipped: number;
  winner: string;
  ranking: AlphaCompareRow[];
  skipped: AlphaCompareSkip[];
}

// --- Trade plan widget (OpenAlgo / trade-stack) ---

export interface TradePlanScenario {
  name: string;
  probability?: number | string;
  trigger?: string;
  strategy_hint?: string;
}

export interface TradePlanRecommended {
  name?: string;
  tier?: string;
  score?: number;
  rationale?: string;
  legs?: TradePlanLeg[];
  target?: number;
  stop?: number;
  max_profit?: number;
  max_loss?: number;
  net_max_profit?: number;
  net_max_loss?: number;
}

export interface RankedStrategy {
  name?: string;
  tier?: string;
  score?: number;
  rationale?: string;
  max_profit?: number;
  max_loss?: number;
  net_max_profit?: number;
  net_max_loss?: number;
}

export interface TradePlanStrategyVariant {
  recommended?: TradePlanRecommended;
  payoff?: TradePlanWidget["payoff"];
  charges?: TradePlanWidget["charges"];
  payoff_over_time?: { samples?: Array<{ days_to_expiry?: number; pnl: number; net_pnl?: number }> };
  implementation_steps?: TradePlanWidget["implementation_steps"];
}

export interface TradePlanLeg {
  side?: string;
  symbol?: string;
  quantity?: number;
  price?: number;
  strike?: number;
  option_type?: string;
}

export interface TradePlanStaleness {
  status: "fresh" | "stale" | "broken" | "monitor_off" | string;
  reasons?: string[];
  spot_drift_pct?: number | null;
}

export interface TradePlanLiveContext {
  spot?: number | null;
  plan_spot?: number | null;
  fetched_at?: string;
}

export interface PlanContextResponse {
  ticker?: string;
  monitor_enabled: boolean;
  staleness?: TradePlanStaleness;
  live_context?: TradePlanLiveContext;
  material_news_count?: number;
  open_position?: boolean;
}

export type WidgetPresentationMode =
  | "options_strategy"
  | "index_outlook"
  | "stock_trade";

export interface NewsScenarioDateRange {
  start?: string;
  end?: string;
}

export interface NewsScenarioPathPoint {
  day?: number;
  date?: string;
  spot?: number;
  return_pct?: number;
}

export interface NewsScenarioBaseline {
  spot?: number | null;
  expected_return_pct?: number | null;
  bottom_up_return_pct?: number | null;
  macro_delta_pct?: number | null;
  range?: { low?: number; high?: number };
  path?: NewsScenarioPathPoint[];
  equation_ref?: { bottom_up?: number; macro_delta?: number; overlay?: number | null };
}

export interface NewsScenarioOutcome {
  id?: string;
  label?: string;
  intensity?: string;
  probability_hint?: string;
  expected_return_pct?: number | null;
  macro_delta_pct?: number | null;
  bottom_up_return_pct?: number | null;
  range?: { low?: number; high?: number };
  path?: NewsScenarioPathPoint[];
  contributors?: Array<Record<string, unknown>>;
  factor_overrides_applied?: Record<string, unknown>;
}

export interface NewsScenarioFanBand {
  low?: number | null;
  high?: number | null;
  low_path?: NewsScenarioPathPoint[];
  high_path?: NewsScenarioPathPoint[];
}

export interface NewsScenarioSessionRequest {
  ticker?: string;
  pipeline_as_of: string;
  horizon_days?: number;
  session_id?: string | null;
}

export interface NewsScenarioSessionPatchRequest {
  date_range?: NewsScenarioDateRange | null;
  selected_outcome_id?: string | null;
  active_draft_id?: string | null;
  active_scenario_id?: string | null;
}

export interface NewsScenarioSessionResponse {
  status: string;
  session_id: string;
  pipeline_as_of: string;
  ticker: string;
  message?: string;
}

export interface NewsEventScenarioResponse {
  status: string;
  ticker: string;
  scenario?: Record<string, unknown> | null;
  scenarios?: Array<Record<string, unknown>>;
  message?: string;
}

export interface ExternalPredictionTarget {
  low?: number | null;
  mid?: number | null;
  high?: number | null;
}

export interface ExternalPredictionRecord {
  source_id: string;
  symbol?: string;
  horizon_days?: number;
  as_of?: string;
  published_at?: string;
  spot_at_fetch?: number | null;
  target?: ExternalPredictionTarget;
  target_date?: string;
  direction?: "bullish" | "bearish" | "neutral";
  expected_return_pct?: number | null;
  rationale_bullets?: string[];
  confidence?: "high" | "medium" | "low";
  provenance?: { url?: string; title?: string; snippet?: string; summary?: string; horizon_days?: number };
  extraction?: { model?: string; extracted_at?: string };
  fetch_status?: "ok" | "stale" | "not_found" | "error";
  error_message?: string;
}

export interface ExternalPredictionSource {
  id: string;
  display_name: string;
  kind?: "media" | "broker" | "global_bank";
  search_queries?: string[];
  domains?: string[];
  watchlisted?: boolean;
  discovered_at?: string | null;
  added_by?: "seed" | "user" | "discover";
  removable?: boolean;
}

export interface ExternalPredictionSnapshot {
  symbol?: string;
  horizon_days?: number;
  fetched_at?: string;
  cache_ttl_hours?: number;
  is_stale?: boolean;
  sources?: ExternalPredictionSource[];
  predictions?: ExternalPredictionRecord[];
  internal_forecast?: Record<string, unknown> | null;
}

export interface ExternalPredictionsResponse {
  status: string;
  ticker: string;
  snapshot?: ExternalPredictionSnapshot | null;
  message?: string;
}

export interface ExternalPredictionsRefreshStartResponse {
  status: string;
  job_id: string;
  job_status: string;
  reused?: boolean;
}

export interface ExternalPredictionsRefreshJobSnapshot {
  job_id: string;
  status: string;
  ticker?: string;
  horizon_days?: number;
  created_at?: string | null;
  error?: string | null;
  logs?: PipelineLogEntry[];
  snapshot?: ExternalPredictionSnapshot | null;
}

export interface ExternalPredictionsRefreshActiveResponse {
  status: string;
  job?: ExternalPredictionsRefreshJobSnapshot | null;
}

export interface ExternalPredictionsRefreshJobResponse {
  status: string;
  job?: ExternalPredictionsRefreshJobSnapshot | null;
}

export interface ExternalPredictionSourcesResponse {
  status: string;
  ticker: string;
  sources?: ExternalPredictionSource[];
  candidates?: Array<Record<string, unknown>>;
  message?: string;
}

export interface ExternalPredictionSourceRequest {
  id?: string;
  display_name: string;
  domains?: string[];
  search_queries?: string[];
  kind?: string;
}

export interface TradePlanWidget {
  type: "trade_plan.widget";
  widget_id: string;
  widget_kind?: string;
  presentation_mode?: WidgetPresentationMode;
  widget_intent?: string;
  asset_type?: "options" | "stock" | "index";
  underlying: string;
  instrument_type?: string;
  market?: string;
  spot?: number | null;
  expiry?: string;
  agent_recommended_strategy?: string;
  strategy_variants?: Record<string, TradePlanStrategyVariant>;
  prediction?: {
    view?: string;
    iv_regime?: string;
    confidence?: number;
    horizon_days?: number;
    expected_return_pct?: number;
    range?: { low?: number; high?: number };
    provenance?: {
      direction?: string;
      range?: string;
      targets?: string;
      debate_as_of?: string;
      quant_source?: string;
    };
    signals?: Record<string, unknown>;
  };
  scenarios?: TradePlanScenario[];
  ranked_strategies?: RankedStrategy[];
  recommended?: TradePlanRecommended;
  browse_summary?: { spot?: number };
  payoff?: {
    samples?: Array<{ spot: number; pnl: number; net_pnl?: number }>;
    gross_max_profit?: number;
    gross_max_loss?: number;
    net_max_profit?: number;
    net_max_loss?: number;
    breakevens?: unknown;
  };
  payoff_over_time?: { samples?: Array<{ days_to_expiry?: number; pnl: number; net_pnl?: number }> };
  charges?: {
    per_leg?: Array<Record<string, unknown>>;
    net_debit_credit?: number;
    round_trip_charges?: number;
    total?: Record<string, unknown>;
  };
  implementation_steps?: Array<{
    step?: number;
    action?: string;
    description?: string;
    mcp_tool?: string | null;
    payload?: Record<string, unknown>;
  }>;
  plan_status?: "ready" | "partial" | "incomplete" | string;
  data_warnings?: string[];
  staleness?: TradePlanStaleness;
  live_context?: TradePlanLiveContext;
  error?: string;
  regime?: Record<string, unknown>;
  factor_explanation?: {
    method?: string;
    macro_delta_pct?: number;
    contributors?: Array<Record<string, unknown>>;
  };
  factor_sensitivity?: Array<Record<string, unknown>>;
  event_impact_curves?: Array<Record<string, unknown>>;
  constituent_signals?: Array<Record<string, unknown>>;
  accuracy?: Record<string, unknown>;
  meta?: {
    strategy_builder_url?: string;
    strategy_builder_pnl_url?: string;
    strategy_builder_execute_url?: string;
    superseded?: boolean;
  };
  date_range?: NewsScenarioDateRange;
  event?: Record<string, unknown>;
  baseline?: NewsScenarioBaseline;
  outcomes?: NewsScenarioOutcome[];
  fan_band?: NewsScenarioFanBand;
  selected_outcome_id?: string | null;
  scenario_id?: string;
  pipeline_as_of?: string;
}

export interface ExecuteTradeBasketRequest {
  widget_id?: string;
  orders?: Array<Record<string, unknown>>;
  strategy?: string;
}

export interface ExecuteTradeBasketResponse {
  status: string;
  results?: Array<Record<string, unknown>>;
  message?: string;
  execution_mode?: string;
}

export interface TradeChargesRequest {
  legs: Array<Record<string, unknown>>;
  spot?: number;
  broker_preset?: string;
  include_exit?: boolean;
}

export interface TradeChargesResponse {
  status: string;
  charges?: TradePlanWidget["charges"];
  message?: string;
}

export interface TradeExecutionMode {
  mode: "paper" | "live" | string;
  analyze_mode: boolean;
  paper_env: boolean;
  live_allowed?: boolean;
  switch_url?: string;
}

export interface AutoPaperBootstrapRequest {
  prompt?: string | null;
  ticker?: string;
  budget_inr?: number;
  watchlist?: string[];
  resume?: boolean;
  fresh_session?: boolean;
  dispatch?: boolean;
  vibe_session_id?: string;
}

export interface AutoPaperResumeRequest {
  vibe_session_id?: string;
  dispatch?: boolean;
  fresh_session?: boolean;
  prompt?: string;
}

export interface AutoPaperBootstrapResponse {
  status: string;
  vibe_session_id?: string;
  ui_url?: string;
  attempt_id?: string;
  message_id?: string;
  prompt_injected?: boolean;
  paper_session?: Record<string, unknown>;
}

export interface PlanPrediction {
  view?: string | null;
  iv_regime?: string | null;
  confidence?: number | null;
  expected_move_pct?: number | null;
  expected_return_pct?: number | null;
  bottom_up_return_pct?: number | null;
  macro_delta_pct?: number | null;
  range?: { low?: number; high?: number; confidence?: number } | null;
  equation?: {
    form?: string;
    coefficients?: Record<string, number>;
    intercept?: number;
    r2_walk_forward?: number;
  } | null;
  top_drivers?: Array<Record<string, unknown>>;
  pcr?: number | null;
  source?: string | null;
  reconciled_with_scenarios?: boolean;
  scenario_anchor_return_pct?: number | null;
  raw_expected_return_pct?: number | null;
  raw_macro_delta_pct?: number | null;
  reconciliation_blend?: { model_weight?: number; scenario_weight?: number };
  momentum_coverage?: { with_momentum?: number; total?: number; coverage_pct?: number };
  direction_hit_rate_oos?: number | null;
  direction_hit_rate_walk_forward?: number | null;
  direction_model_score?: number | null;
  direction_confidence?: number | null;
  direction_confidence_raw?: number | null;
  direction_view?: string | null;
  direction_eval_count?: number | null;
  sign_conflict?: boolean;
  debate_merged?: boolean;
  quant?: {
    expected_return_pct?: number | null;
    macro_delta_pct?: number | null;
    view?: string | null;
  } | null;
  data_quality_warning?: {
    gate?: string;
    min_pct?: number | null;
    threshold_pct?: number | null;
    message?: string;
    macro_trust_multiplier?: number | null;
  } | null;
  flow_coverage?: {
    passes_gate?: boolean;
    min_pct?: number | null;
  } | null;
  cause_stress_index?: number | null;
  cause_stress_label?: string | null;
  channel_attribution?: Record<string, number> | null;
}

export interface IndexGlobalFactor {
  factor?: string;
  label?: string;
  value?: number;
  z_score?: number | null;
  source?: string;
}

export interface IndexFactorContributor {
  factor?: string;
  label?: string;
  contribution_pct?: number;
  share_of_macro?: number;
  index_points?: number;
  marginal_impact_pct?: number;
  contribution_index_pts?: number;
  value?: number;
  share_of_total_equation?: number;
  correlation_caveat?: boolean;
}

export interface IndexScenario {
  event?: string;
  outcome?: string;
  label?: string;
  description?: string;
  index_range?: number[];
  probability?: number;
  midpoint_return_pct?: number;
}

export interface IndexUpcomingEvent {
  date?: string;
  days_from_now?: number;
  event_type?: string;
  label?: string;
  symbol?: string;
  weight?: number;
  sector?: string;
  impact?: string;
  category?: string;
}

export interface IndexSimulationResult {
  expected_return_pct?: number;
  baseline_return_pct?: number;
  macro_delta_pct?: number;
  bottom_up_return_pct?: number;
  index_level?: number;
  baseline_index_level?: number;
  horizon_days?: number;
  range?: { low?: number; high?: number };
  view?: string;
  factor_explanation?: { contributors?: IndexFactorContributor[] };
  baseline_factor_explanation?: { contributors?: IndexFactorContributor[] };
  factor_sensitivity?: Array<Record<string, unknown>>;
  factor_overrides?: Record<string, number>;
  cascade_applied?: CascadeAppliedRow[];
  cascade_method?: "heuristic" | "data_calibrated";
  cascade_regime?: "calm" | "elevated" | "crisis";
  cascade_calibration_as_of?: string | null;
  forecast_path?: ForecastPathPoint[];
}

export interface CascadeAppliedRow {
  factor?: string;
  before?: number;
  after?: number;
  reason?: string;
  source?: "heuristic" | "var" | "blended";
  var_implied_after?: number;
  heuristic_after?: number;
}

export interface CascadeCalibrationSummary {
  status?: string;
  as_of?: string;
  method?: string;
  regime?: "calm" | "elevated" | "crisis";
  blend_alpha?: number;
}

export interface ForecastPathPoint {
  day?: number;
  baseline_level?: number;
  scenario_level?: number;
  baseline_return_pct?: number;
  scenario_return_pct?: number;
}

export interface IndexRegime {
  label?: string;
  regime?: string;
  india_vix?: number;
  trend_20d?: string;
  [key: string]: unknown;
}

export interface SectorBreadth {
  mean_sentiment?: number | null;
  by_sector?: Record<string, number>;
  sector_count?: number;
  [key: string]: unknown;
}

export interface IndexAccuracy {
  sample_count?: number;
  mae_pct?: number | null;
  mae_14d_pct?: number | null;
  direction_hit_rate?: number | null;
  direction_hit_rate_walk_forward?: number | null;
  direction_hit_rate_ledger?: number | null;
  direction_hit_rate_14d?: number | null;
  window_days?: number;
  retrained?: boolean;
  [key: string]: unknown;
}

export interface ConstituentEvent {
  symbol?: string;
  company?: string;
  type?: string;
  purpose?: string;
  description?: string;
  date?: string;
  source?: string;
}

export interface ConstituentFactor {
  type?: string;
  factor?: string;
  macro_link?: string;
  score?: number;
  headline?: string;
  date?: string;
  impact?: string;
  event?: string;
  note?: string;
  source?: string;
}

export interface ConstituentSignal {
  symbol?: string;
  weight?: number;
  sector?: string;
  sentiment_score?: number | null;
  contribution_to_index_pct?: number | null;
  events?: ConstituentEvent[];
  factors?: ConstituentFactor[];
  [key: string]: unknown;
}

export interface IndexPredictionArtifact extends Omit<HubPlanArtifact, "regime" | "accuracy"> {
  asset_type?: "index";
  spot_source?: string | null;
  spot_error?: string | null;
  horizon?: { name?: string; days?: number };
  regime?: IndexRegime;
  global_factors?: IndexGlobalFactor[];
  sector_breadth?: SectorBreadth;
  factor_explanation?: {
    contributors?: IndexFactorContributor[];
    method?: string;
    macro_delta_pct?: number;
    ridge_macro_delta_pct?: number;
    attribution_rescaled?: boolean;
    attribution_disclaimer?: string;
    multicollinearity_warning?: boolean;
    channel_attribution?: Record<string, number> | null;
    correlated_pairs?: Array<{
      factor_a?: string;
      factor_b?: string;
      correlation?: number;
    }>;
  };
  factor_sensitivity?: Array<Record<string, unknown>>;
  event_impact_curves?: Array<Record<string, unknown>>;
  upcoming_events?: IndexUpcomingEvent[];
  constituent_signals?: ConstituentSignal[];
  top_factors?: IndexFactorContributor[];
  accuracy?: IndexAccuracy;
  cascade_calibration?: CascadeCalibrationSummary;
  event_overlay?: {
    return_pct?: number;
    active_topics?: Array<{ topic?: string; contribution_pct?: number; sample_count?: number }>;
    method?: string;
    calibration_as_of?: string;
  };
  news_shock_calibration?: {
    news_event_features_status?: string;
    news_event_overlay_status?: string;
    reconciled_total?: number;
    topics?: Record<
      string,
      {
        sample_count?: number;
        median_calibration_error?: number;
        calibrated_shock_pct?: number;
        overlay_eligible?: boolean;
      }
    >;
  };
  stage_errors?: string[];
  pipeline_log?: PipelineLogEntry[];
}

export interface PipelineLogEntry {
  stage: string;
  message: string;
  level: string;
  detail?: Record<string, unknown>;
  at: string;
}

export interface IndexFactorCatalogEntry {
  key: string;
  label: string;
  category: string;
  source: string;
  role: string;
  data_quality?: string | null;
}

export interface IndexFactorCatalogResponse {
  status: string;
  macro_and_technical?: IndexFactorCatalogEntry[];
  bottom_up?: IndexFactorCatalogEntry[];
  constituent_research?: IndexFactorCatalogEntry[];
  constituent_market_data?: IndexFactorCatalogEntry[];
  news_and_sentiment?: IndexFactorCatalogEntry[];
  derivatives?: IndexFactorCatalogEntry[];
  pipeline_modules?: IndexFactorCatalogEntry[];
  model_layers?: IndexFactorCatalogEntry[];
  total_macro_keys?: number;
  message?: string;
}

export interface IndexPredictionResponse {
  status: string;
  ticker?: string;
  artifact?: IndexPredictionArtifact | null;
  message?: string;
}

export interface RunIndexPredictionRequest {
  ticker?: string;
  horizon_days?: number;
  refresh_constituents?: boolean;
  run_forecast_lab?: boolean;
}

export interface IndexForecastLabResponse {
  status: string;
  ticker?: string;
  result?: Record<string, unknown> | null;
  message?: string;
  artifact?: IndexPredictionArtifact | null;
}

export interface IndexPredictionRunStartResponse {
  status: string;
  job_id: string;
  job_status: string;
  reused?: boolean;
}

export interface IndexPredictionRunJobSnapshot {
  job_id: string;
  status: string;
  ticker?: string;
  horizon_days?: number | null;
  refresh_constituents?: boolean;
  run_forecast_lab?: boolean;
  created_at?: string | null;
  error?: string | null;
  logs?: PipelineLogEntry[];
  artifact?: IndexPredictionArtifact | null;
  current_stage?: string | null;
  last_log_at?: string | null;
  last_log_message?: string | null;
  stage_elapsed_ms?: number | null;
  current_track_id?: string | null;
}

export interface IndexPredictionRunActiveResponse {
  status: string;
  job?: IndexPredictionRunJobSnapshot | null;
}

export interface IndexPredictionRunJobResponse {
  status: string;
  job?: IndexPredictionRunJobSnapshot | null;
}

export interface SimulateIndexPredictionRequest {
  ticker?: string;
  horizon_days?: number;
  factor_overrides?: Record<string, number>;
  primary_factor?: string;
  primary_shock_pct?: number;
  cascade?: boolean;
  event_preset_id?: string;
}

export interface SimulateIndexPredictionResponse {
  status: string;
  ticker?: string;
  simulation?: IndexSimulationResult;
  message?: string;
}

export interface PlaygroundTrigger {
  id?: string;
  title?: string;
  label?: string;
  source?: string;
  kind?: string;
  date?: string;
  days_from_now?: number;
  event_type?: string;
  primary_factor?: string;
  suggested_factors?: string[];
  suggested_shock_pct?: number;
  why?: string;
  probability?: number | null;
  event_preset_id?: string;
  factor_shocks?: Record<string, number>;
  keywords?: string[];
}

export interface PlaygroundRankedFactor {
  factor?: string;
  label?: string;
  contribution_pct?: number;
  value?: number;
  share_of_macro?: number;
}

export interface IndexPlaygroundContext {
  ticker?: string;
  as_of?: string;
  spot?: number;
  horizon_days?: number;
  headlines?: PlaygroundTrigger[];
  events?: PlaygroundTrigger[];
  factor_news?: Record<string, PlaygroundTrigger[]>;
  cascade_downstream?: Record<string, Array<{ factor: string; multiplier: number; mode: string }>>;
  ranked_factors?: PlaygroundRankedFactor[];
  event_impact_curves?: Array<Record<string, unknown>>;
  global_factors?: Record<string, number>;
  baseline_return_pct?: number;
  cascade_calibration?: CascadeCalibrationSummary;
}

export interface IndexPlaygroundContextResponse {
  status: string;
  ticker?: string;
  context?: IndexPlaygroundContext;
  message?: string;
}

export interface RefreshIndexPredictionRequest {
  ticker?: string;
  horizon_days?: number;
  force?: boolean;
}

export interface IndexPredictionRefreshResponse {
  status: string;
  ticker?: string;
  reason?: string;
  artifact?: IndexPredictionArtifact | null;
  message?: string;
}

export interface IndexPredictionHistoryRow {
  predicted_at: string;
  horizon_days: number;
  spot_at_prediction: number;
  expected_return_pct: number;
  implied_level: number;
  range_low: number;
  range_high: number;
  actual_return_pct?: number | null;
  direction_correct?: boolean | null;
  horizon_name?: string;
  bottom_up_return_pct?: number | null;
  macro_delta_pct?: number | null;
  refresh?: string;
}

export interface IndexPredictionHistoryMeta {
  unique_days?: number;
  intraday_revisions?: number;
  granularity?: string;
  needs_more_days?: boolean;
}

export interface IndexPredictionHistoryResponse {
  status: string;
  ticker?: string;
  rows?: IndexPredictionHistoryRow[];
  daily?: IndexPredictionHistoryRow[];
  intraday?: IndexPredictionHistoryRow[];
  meta?: IndexPredictionHistoryMeta;
  message?: string;
}

export interface IndexFactorHistoryPoint {
  date: string;
  factor?: string;
  value?: number;
  [key: string]: string | number | undefined;
}

export interface IndexFactorHistoryResponse {
  status: string;
  ticker?: string;
  series?: IndexFactorHistoryPoint[];
  factors?: string[];
  coverage?: Record<string, number>;
  coverage_notes?: string[];
  message?: string;
}

export interface ConstituentHistoryPoint {
  date: string;
  sentiment_score?: number | null;
  contribution_proxy_pct?: number | null;
  return_7d_pct?: number | null;
  close?: number | null;
  source?: string;
}

export interface ConstituentHistoryResponse {
  status: string;
  symbol?: string;
  days?: number;
  snapshot_count?: number;
  has_research_archive?: boolean;
  points?: ConstituentHistoryPoint[];
  message?: string;
}

export interface IndexPredictionSnapshot {
  as_of?: string;
  spot?: number;
  expected_return_pct?: number;
  bottom_up_return_pct?: number;
  macro_delta_pct?: number;
  view?: string;
  constituent_count?: number;
  path?: string;
  horizon_days?: number;
  range_low?: number;
  range_high?: number;
}

export interface IndexPredictionSnapshotsResponse {
  status: string;
  ticker?: string;
  snapshots?: IndexPredictionSnapshot[];
  message?: string;
}

export interface IndexBacktestFactorAudit {
  factor?: string;
  label?: string;
  rows_present?: number;
  rows_total?: number;
  coverage_pct?: number;
  is_static?: boolean;
  in_macro_keys?: boolean;
}

export interface IndexBacktestDailyEval {
  date?: string;
  spot?: number;
  realized_1d_pct?: number;
  predicted_return_pct?: number;
  actual_forward_return_pct?: number;
  error_pct?: number;
  direction_correct?: boolean;
  macro_delta_pct?: number;
  macro_raw_pct?: number;
  maturity_date?: string;
  miss_category?: string;
  learning_note?: string;
  factor_delta_horizon?: Array<{
    factor?: string;
    label?: string;
    t0?: number;
    t1?: number;
    delta?: number;
    change_pct?: number;
  }>;
  factor_snapshot_t0?: Record<string, number>;
  factor_snapshot_t1?: Record<string, number>;
  headlines_at_maturity?: Array<{ title?: string; source?: string }>;
  causal_hypotheses?: CausalHypothesis[];
  factor_drivers?: Array<{
    factor?: string;
    label?: string;
    prev?: number;
    current?: number;
    change_pct?: number;
  }>;
  calendar_events?: Array<{ type?: string; event?: string; description?: string }>;
  calendar_events_at_maturity?: Array<{ type?: string; event?: string; description?: string }>;
  implied_level?: number;
}

export interface IndexBacktestConstituentMover {
  symbol?: string;
  weight_pct?: number;
  return_1d_pct?: number;
  index_contribution_pct?: number;
  headlines?: Array<{ title?: string; source?: string }>;
}

export interface IndexBacktestDrawdown {
  date?: string;
  spot?: number;
  realized_1d_pct?: number;
  factor_drivers?: Array<{
    factor?: string;
    label?: string;
    prev?: number;
    current?: number;
    change_pct?: number;
  }>;
  calendar_events?: Array<{ type?: string; event?: string; description?: string }>;
  constituent_movers?: IndexBacktestConstituentMover[];
  worst_contributors?: IndexBacktestConstituentMover[];
  causal_hypotheses?: CausalHypothesis[];
  index_headlines?: Array<{ title?: string; source?: string }>;
}

export interface IndexBacktestReport {
  status?: string;
  scope?: "macro_only" | "hybrid" | string;
  as_of?: string;
  ticker?: string;
  horizon_days?: number;
  history_start?: string;
  history_end?: string;
  history_rows?: number;
  eval_count?: number;
  metrics?: {
    mae_pct?: number | null;
    direction_hit_rate?: number | null;
    macro_only_direction_hit_rate?: number | null;
    hybrid_direction_hit_rate?: number | null;
    hybrid_eval_count?: number | null;
    in_sample_mae_pct?: number | null;
    in_sample_r2?: number | null;
    in_sample_direction_hit_rate?: number | null;
  };
  factor_audit?: IndexBacktestFactorAudit[];
  factor_correlations?: Array<{ factor?: string; label?: string; corr_forward_return?: number }>;
  major_drawdowns?: IndexBacktestDrawdown[];
  nifty_series?: Array<{ date: string; close: number; realized_1d_pct?: number | null }>;
  daily_evaluations?: IndexBacktestDailyEval[];
  limitations?: string[];
}

export interface CausalHypothesis {
  title?: string;
  explanation?: string;
  category?: string;
  confidence?: number;
  linked_factors?: string[];
  evidence?: string[];
  source?: string;
}

export interface IndexDayAttribution {
  status?: string;
  date?: string;
  close?: number;
  realized_1d_pct?: number | null;
  factor_drivers?: Array<{
    factor?: string;
    label?: string;
    prev?: number;
    current?: number;
    change_pct?: number;
  }>;
  calendar_events?: Array<{ type?: string; event?: string; description?: string }>;
  narrative?: string[];
  causal_hypotheses?: CausalHypothesis[];
  index_headlines?: Array<{ title?: string; source?: string }>;
  constituent_headlines?: Array<{ title?: string; source?: string; symbol?: string }>;
}

export interface DayAttributionResponse {
  status: string;
  date?: string;
  attribution?: IndexDayAttribution;
  message?: string;
}

export interface IndexPredictionJob {
  id?: string;
  label?: string;
  description?: string;
  schedule?: string;
  status?: string;
  paused?: boolean;
  stale_running?: boolean;
  enabled?: boolean;
  job_type?: string;
  next_run_at?: number;
  last_run_at?: number | null;
  last_error?: string | null;
  last_result_summary?: Record<string, unknown> | null;
  consecutive_failures?: number;
}

export interface IndexPredictionNewsPipelineHealth {
  queued?: number;
  oldest_pending_seconds?: number;
  pipeline_paused?: boolean;
  pause_reason?: string;
  minimax_configured?: boolean;
  worker_last?: Record<string, unknown> | null;
  error?: string;
}

export interface IndexPredictionJobsResponse {
  status: string;
  env?: {
    vibe_trading_enable_scheduler?: boolean;
    index_research_enable_scheduler?: boolean;
    index_monitor_enable_scheduler?: boolean;
  };
  master_scheduler_env_enabled?: boolean;
  master_scheduler_running?: boolean;
  executor_is_running?: boolean;
  news_pipeline?: IndexPredictionNewsPipelineHealth;
  jobs?: IndexPredictionJob[];
  job?: IndexPredictionJob | null;
  message?: string;
}

export interface CaptureFactorTreeGroup {
  category: string;
  factors: Array<{
    key: string;
    label?: string;
    category?: string;
    source?: string;
    role?: string;
    tier: "capture" | "scalar" | "ephemeral";
  }>;
}

export interface CaptureRegistryEntity {
  id: string;
  kind?: string;
  capture_enabled?: boolean;
  factor_groups?: string[];
  schedules?: Record<string, string>;
  retention_days?: Record<string, number>;
}

export interface CaptureRegistryResponse {
  status: string;
  registry?: {
    version?: number;
    entities?: CaptureRegistryEntity[];
    updated_at?: string;
  };
  factor_tree?: CaptureFactorTreeGroup[];
  stats?: {
    entity_id?: string;
    capture_enabled?: boolean;
    total_rows?: number;
    channel?: {
      date?: string;
      hub_hits?: number;
      vendor_fetches?: number;
      by_series?: Record<string, { hub_hits?: number; vendor_fetches?: number }>;
    };
    series?: Record<
      string,
      { path?: string; days?: number; rows?: number; last_capture_at?: string | null }
    >;
  };
  coverage?: {
    entity_id?: string;
    series?: Record<string, { days_captured?: number; fill_rate_pct?: number }>;
  };
  message?: string;
}

export interface CaptureRegistryUpdateRequest {
  entity_id?: string;
  patch: {
    capture_enabled?: boolean;
    factor_groups?: string[];
    retention_days?: Record<string, number>;
    schedules?: Record<string, string>;
  };
}

export interface CaptureRegistryBackfillRequest {
  entity_id?: string;
  days?: number;
}

export interface HubNewsReference {
  ref_id?: string;
  title?: string;
  url?: string;
  source?: string;
  published_at?: string;
  vendor?: string;
  publisher?: string;
}

export interface HubNewsItem {
  id?: string;
  ref_id?: string;
  event_id?: string;
  title?: string;
  summary?: string;
  url?: string;
  source?: string;
  published_at?: string;
  created_at?: string;
  ticker?: string;
  provenance?: "staging" | "distilled" | string;
  verification_status?: string;
  market_impact_status?: string;
  event_kind?: string;
  parent_event_id?: string | null;
  sources?: HubNewsReference[];
  references?: HubNewsReference[];
  ref_count?: number;
  timeline?: Array<{ at?: string; kind?: string; summary?: string }>;
  consensus?: { direction?: string; confidence?: number; ref_count?: number };
  predicted_impact?: Record<string, unknown>;
  actual_impact?: Record<string, unknown>;
  tags?: { topics?: string[]; themes?: string[]; factors?: string[] };
}

export interface HubDiscardedNewsItem {
  discard_id?: string;
  id?: string;
  ref_id?: string;
  event_id?: string;
  title?: string;
  url?: string;
  reason?: string;
  source_kind?: string;
  discarded_at?: string;
  expires_at?: string;
  provenance?: "discarded" | string;
  ticker?: string;
  relevance?: Record<string, unknown>;
}

export interface HubStatusPayload {
  generated_at?: string;
  entity_id?: string;
  hub_dir?: string;
  paths?: Record<string, string>;
  news_pipeline?: Record<string, unknown>;
  news_staging?: {
    entity_pipeline_enabled?: boolean;
    pipeline_paused?: boolean;
    pause_reason?: string;
    minimax_configured?: boolean;
    queued?: number;
    by_ticker?: Array<{ ticker: string; queued: number }>;
    worker_last?: {
      processed?: number;
      errors?: number;
      created?: number;
      updated?: number;
      finished_at?: string;
    };
  };
  news_inventory?: {
    pending_count?: number;
    union_count?: number;
    staging_in_union?: number;
    distilled_in_union?: number;
    discarded_count?: number;
    items?: HubNewsItem[];
    staging_queue?: HubNewsItem[];
    discarded_items?: HubDiscardedNewsItem[];
  };
  verified_news?: Record<
    string,
    { total?: number; by_status?: Record<string, number> }
  >;
  index_research?: {
    present?: boolean;
    as_of?: string;
    horizon?: { name?: string; days?: number };
    last_pipeline_stage?: string;
    last_pipeline_message?: string;
  };
  constituent_cache?: {
    total?: number;
    fresh?: number;
    stale?: number;
    missing?: number;
  };
  capture?: {
    stats?: CaptureRegistryResponse["stats"];
    coverage?: CaptureRegistryResponse["coverage"];
  };
  factor_coverage?: {
    trading_days?: number;
    min_pct?: number;
    passes_gate?: boolean;
    start?: string;
    end?: string;
  };
}

export interface HubStatusResponse {
  status: string;
  hub?: HubStatusPayload;
  message?: string;
}

export interface HubStagingDrainResponse {
  status: string;
  summary?: Record<string, number | string | boolean | Record<string, unknown>>;
  message?: string;
}

export interface HubNewsPipelineConfig {
  ticker?: string;
  full_ingest_cron?: string;
  light_ingest_cron?: string;
  light_ingest_enabled?: boolean;
  entity_drain_cron?: string;
  entity_maintenance_cron?: string;
  entity_drain_continuous_cron?: string;
  entity_drain_continuous_enabled?: boolean;
  entity_backpressure_threshold?: number;
  full_ingest_sources?: string;
  light_ingest_sources?: string;
  full_lookback_days?: number;
  light_lookback_days?: number;
  entity_batch_size?: number;
  cluster_threshold?: number;
  relevance_gate_enabled?: boolean;
  relevance_min_confidence?: number;
  relevance_rule_first?: boolean;
  discard_retention_days?: number;
  wiki_search_enabled?: boolean;
  wiki_search_top_k?: number;
  wiki_search_max_per_pass?: number;
  wiki_search_min_score?: number;
  config_path?: string;
  ingest_modes?: {
    full?: { label?: string; sources?: string; lookback_days?: number; cron?: string };
    light?: {
      label?: string;
      sources?: string;
      lookback_days?: number;
      cron?: string;
      enabled?: boolean;
    };
  };
  scheduler_sync?: Record<string, unknown>;
}

export interface HubNewsPipelineConfigResponse {
  status: string;
  config?: HubNewsPipelineConfig;
  message?: string;
}

export interface HubNewsPipelineConfigUpdate {
  full_ingest_cron?: string;
  light_ingest_cron?: string;
  light_ingest_enabled?: boolean;
  entity_drain_cron?: string;
  entity_maintenance_cron?: string;
  entity_drain_continuous_cron?: string;
  entity_drain_continuous_enabled?: boolean;
  entity_backpressure_threshold?: number;
  full_ingest_sources?: string;
  light_ingest_sources?: string;
  full_lookback_days?: number;
  light_lookback_days?: number;
  entity_batch_size?: number;
  cluster_threshold?: number;
  relevance_gate_enabled?: boolean;
  relevance_min_confidence?: number;
  relevance_rule_first?: boolean;
  discard_retention_days?: number;
  wiki_search_enabled?: boolean;
  wiki_search_top_k?: number;
  wiki_search_max_per_pass?: number;
  wiki_search_min_score?: number;
}

export interface HubNewsDiscardRequest {
  entity_id?: string;
  item_id: string;
  source_kind?: "staging" | "distilled" | string;
  reason?: string;
  discard_similar?: boolean;
}

export interface HubNewsDiscardUndoRequest {
  entity_id?: string;
  discard_id: string;
}

export interface HubNewsDiscardResponse {
  status: string;
  discarded_count?: number;
  discard_ids?: string[];
  discarded?: Record<string, unknown>[];
  similar_preview?: { similar_count?: number; items?: Array<{ id?: string; title?: string }> };
  message?: string;
}

export interface HubNewsDiscardedListResponse {
  status: string;
  items?: HubDiscardedNewsItem[];
  count?: number;
  message?: string;
}

export interface HubNewsIngestRequest {
  mode?: "full" | "light" | string;
  ticker?: string;
  sources?: string;
  lookback_days?: number;
}

export interface IndexBacktestResponse {
  status: string;
  ticker?: string;
  report?: IndexBacktestReport | null;
  message?: string;
}

export interface IndexTrackChartActualPoint {
  date: string;
  actual_pct?: number | null;
  close?: number | null;
}

export interface IndexTrackChartTrackPoint {
  date: string;
  predicted_pct?: number;
  error_pct?: number;
  direction_hit?: boolean;
}

export interface IndexTrackChartSeries {
  track_id: string;
  label?: string;
  points?: IndexTrackChartTrackPoint[];
}

export interface IndexTrackChartLivePoint {
  date: string;
  spot?: number | null;
  tracks?: Record<string, number>;
  is_live?: boolean;
}

export interface IndexTrackChartPayload {
  horizon_days?: number;
  eval_dates?: string[];
  actual_series?: IndexTrackChartActualPoint[];
  track_series?: IndexTrackChartSeries[];
  nifty_close_series?: Array<{ date: string; close: number }>;
  live_point?: IndexTrackChartLivePoint | null;
  track_ids?: string[];
}

export interface IndexTrackMetrics {
  track_id?: string;
  eval_count?: number;
  mae_pct?: number | null;
  direction_hit_rate?: number | null;
  direction_hit_count?: number | null;
  direction_miss_count?: number | null;
  backtest_eligible?: boolean;
}

export interface IndexTrackPromotionVerdict {
  promoted?: boolean;
  direction_hit_rate?: number;
  delta_vs_quant_pp?: number;
  eval_count?: number;
  insufficient_evidence?: boolean;
}

export interface IndexTrackPromotion {
  eval_count?: number;
  quant_direction_hit_rate?: number;
  verdicts?: Record<string, IndexTrackPromotionVerdict>;
  promoted_combiners?: string[];
  auto_promote_allowed?: boolean;
  min_eval_count_required?: number;
}

export interface IndexTrackLiveSnapshot {
  as_of?: string;
  spot?: number | null;
  forecast_tracks?: Record<
    string,
    { expected_return_pct?: number; view?: string; available?: boolean }
  >;
  cause_stress_index?: number | null;
  cause_stress_label?: string | null;
  active_combiner?: string | null;
  headline_source?: string | null;
  combiner_preview?: Record<string, unknown> | null;
}

export interface IndexTrackScoreboardReport {
  status?: string;
  message?: string;
  ticker?: string;
  horizon_days?: number;
  history_days?: number;
  history_start?: string;
  history_end?: string;
  history_rows?: number;
  eval_count?: number;
  hybrid_eval_count?: number;
  limitations?: string[];
  tracks?: Record<string, IndexTrackMetrics>;
  combiners?: Record<string, IndexTrackMetrics>;
  chart?: IndexTrackChartPayload | null;
  promotion?: IndexTrackPromotion | null;
  live?: IndexTrackLiveSnapshot | null;
  live_enrichment_error?: string | null;
  live_enrichment_note?: string | null;
  nifty_series?: Array<{ date: string; close: number; realized_1d_pct?: number | null }>;
  daily_evaluations?: Array<{
    date: string;
    track_id: string;
    predicted_pct?: number;
    actual_pct?: number;
    error_pct?: number;
    direction_hit?: boolean;
    close?: number;
    implied_level?: number | null;
  }>;
  track_catalog?: Record<
    string,
    {
      label?: string;
      implementation?: string;
      backtest_eligible?: boolean;
      metrics?: IndexTrackMetrics;
    }
  >;
  schema_version?: number;
  needs_refresh?: boolean;
  as_of?: string;
}

export interface IndexTrackScoreboardResponse {
  status: string;
  ticker?: string;
  report?: IndexTrackScoreboardReport | null;
  message?: string;
}

export interface NewsImpactVerificationClaim {
  claim?: string;
  factor?: string;
  verdict?: string;
  evidence?: string;
  data_as_of?: string;
}

export interface NewsSourceAttribution {
  vendor?: string;
  publisher?: string;
  url?: string;
  fetched_at?: string;
}

export interface NewsArticleTags {
  publish_day?: string;
  symbols?: string[];
  topics?: string[];
  factors?: string[];
  themes?: string[];
  flat?: string[];
}

export interface NewsImpactItem {
  id?: string;
  published_at?: string;
  title?: string;
  raw_headline?: string;
  url?: string;
  source?: string;
  content_summary?: string;
  structured_summary?: {
    facts?: string[];
    entities?: string[];
    implied_factors?: string[];
    event_meta?: NewsImpactItem["event_meta"];
  };
  verification?: {
    status?: string;
    verified_at?: string;
    claims?: NewsImpactVerificationClaim[];
    approval_note?: string;
  };
  status?: string;
  tagged_factors?: Array<{ factor?: string; confidence?: number; method?: string }>;
  tags?: NewsArticleTags;
  sources?: NewsSourceAttribution[];
  verification_status?: string;
  horizon_trading_days?: number;
  maturity_date?: string | null;
  predicted?: {
    return_pct?: number;
    nifty_points?: number;
    model?: string;
  };
  predicted_impact?: {
    return_pct?: number;
    nifty_points?: number;
    model?: string;
  };
  actual?: {
    return_pct?: number;
    nifty_points?: number;
    attribution_share_pct?: number;
  } | null;
  actual_impact?: {
    return_pct?: number;
    nifty_points?: number;
    attribution_share_pct?: number;
  } | null;
  timeline?: Array<{ day?: number; label?: string; nifty_level?: number }>;
  event_meta?: {
    event_id?: string;
    distilled?: boolean;
    ref_count?: number;
    timeline?: Array<{
      at?: string;
      kind?: string;
      summary?: string;
      publisher?: string;
      raw_title?: string;
    }>;
    references?: Array<{
      ref_id?: string;
      publisher?: string;
      vendor?: string;
      url?: string;
      raw_title?: string;
      raw_summary?: string;
      published_at?: string;
    }>;
    consensus?: {
      direction?: string;
      confidence?: number;
      ref_count?: number;
      narrative?: string;
      topics?: string[];
      factors?: string[];
    };
  };
  consensus?: {
    direction?: string;
    confidence?: number;
    ref_count?: number;
    narrative?: string;
  };
  references?: Array<{
    ref_id?: string;
    publisher?: string;
    vendor?: string;
    url?: string;
    raw_title?: string;
    raw_summary?: string;
    published_at?: string;
  }>;
  confidence_note?: string;
}

export interface IndexNewsImpactReport {
  status?: string;
  as_of?: string;
  ticker?: string;
  horizon_days?: number;
  spot?: number;
  debate_summary?: {
    view?: string;
    confidence?: number;
    excerpt?: string;
  } | null;
  items?: NewsImpactItem[];
  summary?: {
    live_count?: number;
    approved_count?: number;
    partial_count?: number;
    rejected_count?: number;
    rejected_skipped?: number;
    pending_count?: number;
    hint?: string;
    source?: string;
  };
}

export interface IndexNewsImpactResponse {
  status: string;
  ticker?: string;
  report?: IndexNewsImpactReport | null;
  message?: string;
}

export interface IndexMissAnalysisReport {
  status?: string;
  as_of?: string;
  ticker?: string;
  horizon_days?: number;
  eval_count?: number;
  summary?: {
    direction_hit_rate?: number | null;
    mae_pct?: number | null;
    miss_count?: number;
    hit_count?: number;
    miss_categories?: Record<string, number>;
    top_miss_patterns?: Array<{
      category?: string;
      count?: number;
      example_dates?: string[];
      action?: string;
    }>;
  };
  misses?: IndexMissAnalysisRow[];
  hits_sample?: IndexMissAnalysisRow[];
}

export interface IndexMissAnalysisRow {
  prediction_date?: string;
  maturity_date?: string;
  predicted_return_pct?: number;
  actual_return_pct?: number;
  direction_correct?: boolean;
  miss_category?: string;
  learning_note?: string;
  factor_delta_horizon?: Array<{
    factor?: string;
    label?: string;
    t0?: number;
    t1?: number;
    delta?: number;
    change_pct?: number;
  }>;
  headlines_at_maturity?: Array<{ title?: string; source?: string }>;
  causal_hypotheses?: CausalHypothesis[];
}

export interface IndexMissAnalysisResponse {
  status: string;
  ticker?: string;
  report?: IndexMissAnalysisReport | null;
  message?: string;
}

export interface IndexQuantReviewReport {
  ticker?: string;
  as_of?: string;
  horizon_days?: number;
  horizon_name?: string;
  disclaimer?: string;
  review_confidence?: number;
  model_prediction_view?: string;
  model_expected_return_pct?: number;
  active_strategy_profile?: string;
  technical_interpretation?: string;
  technical_readings?: Record<string, number>;
  ta_consensus?: {
    direction?: string;
    confidence?: number;
    key_levels_note?: string;
  };
  strategy_profile?: Record<string, unknown>;
  surprises?: Array<{ kind?: string; message?: string; category?: string }>;
  disagreements_with_forecast?: Array<{ type?: string; detail?: string }>;
  data_freshness?: Record<string, unknown>;
}

export interface IndexQuantReviewResponse {
  status: string;
  ticker?: string;
  review?: IndexQuantReviewReport | null;
  message?: string;
}

export interface IndexDataAuditResponse {
  status: string;
  ticker?: string;
  report?: Record<string, unknown> | null;
  message?: string;
}

export interface IndexCounterfactualRow {
  prediction_date?: string;
  maturity_date?: string;
  predicted_t0_pct?: number;
  actual_return_pct?: number;
  residual_pct?: number;
  explained_by_drift_pct?: number;
  unexplained_pct?: number;
  classification?: string | null;
  t0_contributions?: Array<{ term?: string; contribution_pct?: number }>;
  drift_contributions?: Array<{ term?: string; delta_contribution_pct?: number }>;
}

export interface IndexCounterfactualReport {
  status?: string;
  eval_count?: number;
  summary?: {
    direction_hit_rate?: number | null;
    miss_count?: number;
    mapping_error_count?: number;
    drift_dominant_count?: number;
    cap_artifact_count?: number;
    top_drift_factors?: Array<{ term?: string; abs_drift_sum?: number }>;
  };
  rows?: IndexCounterfactualRow[];
  misses?: IndexCounterfactualRow[];
}

export interface IndexCounterfactualResponse {
  status: string;
  ticker?: string;
  report?: IndexCounterfactualReport | null;
  message?: string;
}

export interface ProvenanceSource {
  ref_id: string;
  session_id: string;
  display_name: string;
  summary: string;
  category?: string;
  provider?: string;
  source_type?: string;
  attempt_id?: string | null;
  tool_name?: string | null;
  retrieved_at?: string;
  data_as_of?: string | null;
  freshness_status?: string;
  artifact_path?: string | null;
  source_uri?: string | null;
  raw_data?: string;
}

export interface ProvenanceListResponse {
  sources: ProvenanceSource[];
}

export interface PlanStaleness {
  status?: "fresh" | "stale" | "broken" | string;
  reasons?: string[];
  suggested_action?: string;
  plan_spot?: number | null;
  live_spot?: number | null;
  spot_drift_pct?: number | null;
  age_minutes?: number | null;
  as_of?: string | null;
}

export interface HubPlanArtifact {
  ticker?: string;
  underlying?: string;
  asset_type?: string;
  as_of?: string;
  expiry?: string | null;
  spot?: number | null;
  staleness?: PlanStaleness | null;
  plan_status?: "ready" | "partial" | "incomplete" | string;
  data_warnings?: string[];
  stage_errors?: string[];
  prediction?: PlanPrediction | null;
  events?: Array<Record<string, unknown>>;
  scenarios?: TradePlanScenario[];
  ranked_strategies?: Array<Record<string, unknown>>;
  recommended?: TradePlanWidget["recommended"];
  recommended_name?: string;
  recommended_rationale?: string;
  recommended_tier?: string;
  recommended_score?: number;
  recommended_legs?: TradePlanLeg[];
  max_profit?: number | null;
  max_loss?: number | null;
  horizon?: { name?: string; days?: number };
  regime?: Record<string, unknown>;
  global_factors?: IndexGlobalFactor[];
  sector_breadth?: Record<string, unknown>;
  factor_explanation?: { contributors?: IndexFactorContributor[] };
  factor_sensitivity?: Array<Record<string, unknown>>;
  event_impact_curves?: Array<Record<string, unknown>>;
  constituent_signals?: Array<Record<string, unknown>>;
  top_factors?: IndexFactorContributor[];
  accuracy?: Record<string, unknown>;
}

export interface HubPlanResponse {
  status: string;
  ticker?: string;
  asset_type?: string;
  artifact?: HubPlanArtifact | null;
  message?: string;
}

export interface AgentDebateArtifact {
  ticker?: string;
  rating?: string;
  trade_date?: string;
  as_of?: string;
  investment_debate?: {
    bull_summary?: string;
    bear_summary?: string;
    judge_decision?: string;
  };
  risk_debate?: {
    aggressive_summary?: string;
    conservative_summary?: string;
    neutral_summary?: string;
    judge_decision?: string;
  };
  final_trade_decision?: string;
}

export interface AgentDebateResponse {
  status: string;
  ticker?: string;
  running?: boolean;
  debate?: AgentDebateArtifact | null;
  message?: string;
}

export interface RunAgentDebateRequest {
  ticker: string;
  asset_type?: string;
  session_id?: string;
  refresh?: boolean;
}

// --- Connector runtime channel types ---

/** One mandate profile inside a `mandate.proposal` event (SPEC Consent §1). */
export interface MandateProfile {
  ordinal: number;
  label: string;
  /** Concrete ticker list, or a structural universe descriptor (e.g. "tech_sector"). */
  universe: string[] | string;
  max_order_usd: number;
  daily_trade_cap: number;
  /** "none" for cash-only, otherwise a leverage descriptor/multiple. */
  leverage: string | number;
  instruments: string[];
  notes?: string;
}

/** Account block of a `mandate.proposal` event. */
export interface MandateProposalAccount {
  broker: string;
  type: string;
  funded_by: string;
}

/** Payload of the `mandate.proposal` SSE event (SPEC Consent §1). */
export interface MandateProposal {
  type?: string;
  proposal_id: string;
  session_id?: string;
  intent_normalized?: string;
  account?: MandateProposalAccount;
  ceilings_ref?: string;
  profiles: MandateProfile[];
  funding_note?: string;
  halt_note?: string;
  /** Present only when this proposal was triggered by a mandate breach (SPEC Consent §3). */
  reauth_for?: { breach_id?: string } | null;
}

/** Payload of the `mandate.committed` SSE event (SPEC Consent §1 COMMIT). */
export interface MandateCommitted {
  proposal_id?: string;
  mandate_id?: string;
  consent_record_id?: string;
  selected_ordinal?: number;
  broker?: string;
  /** Resolved limits, surfaced for the compact active-mandate badge. */
  max_order_usd?: number;
  daily_trade_cap?: number;
  expires_at?: string;
}

/** Payload of the `live.halted` SSE event (SPEC Consent §4). */
export interface LiveHalted {
  broker?: string | null;
  tripped_at?: string;
  by?: string;
  reason?: string;
}

/** Payload of the `live.action` SSE event (SPEC Consent §5 audit notify). */
export interface LiveAction {
  audit_id?: string;
  ts?: string;
  kind: string;
  intent_normalized?: string;
  outcome?: string;
  broker?: string;
  remote_tool?: string;
  error?: string | null;
}

export interface CommitMandateRequest {
  broker: string;
  proposal_id: string;
  selected_ordinal: number;
  /** Present only on the adjust path (SPEC Consent §3); null otherwise. */
  adjustments?: Record<string, unknown> | null;
  /** Explicit affirmative consent; the surface sets it on the user's click. */
  consent_ack: boolean;
  session_id?: string;
  account_ref?: string;
  lifetime_days?: number;
}

export interface CommitMandateResponse {
  mandate_id: string;
  consent_record_id: string;
  selected_ordinal?: number;
  broker?: string;
  max_order_usd?: number;
  daily_trade_cap?: number;
  expires_at?: string;
}

export interface AutonomousAgentConstraints {
  mode?: string;
  budget_inr?: number;
  max_daily_loss_inr?: number;
  confidence_threshold?: number;
  market_hours_only?: boolean;
  max_open_positions?: number;
}

export interface AutonomousAgentMandateSummary {
  holding_period?: string;
  flatten_policy?: string;
  product_type?: string;
  revision_policy?: string;
  confidence_threshold?: number;
  allowed_instruments?: string[];
}

export interface AutonomousAgentRuntime {
  mandate_summary?: AutonomousAgentMandateSummary;
  alert_rules_summary?: Record<string, unknown>;
  bootstrap_status?: "pending" | "running" | "done" | "failed" | null;
  bootstrap_error?: string | null;
  scheduler_health?: "ok" | "stale" | "disabled" | "unknown" | "initializing" | "bootstrap_failed";
  market_open?: boolean;
  nautilus_watch_enabled?: boolean;
  nautilus_process_alive?: boolean;
  nautilus_state?: "node_on" | "poll_ok" | "expected" | "stale" | "off";
  nautilus_bound_agent_id?: string | null;
  nautilus_registry_agent_ids?: string[];
  nautilus_in_registry?: boolean;
  watch_path?: string;
  watch_configured?: boolean;
  position_tracked?: boolean;
  handoff_active?: boolean;
  paper_session_linked?: boolean;
  last_decision?: Record<string, unknown> | null;
  last_revision_at?: string | null;
  last_bridge_alert_at?: string | null;
  open_positions?: number | null;
}

export interface AutonomousStackHealth {
  nautilus_watch_enabled?: boolean;
  nautilus_process_alive?: boolean;
  nautilus_state?: "node_on" | "poll_ok" | "expected" | "stale" | "off";
  nautilus_bound_agent_id?: string | null;
  nautilus_registry_agent_ids?: string[];
  scheduler_health?: string;
  market_open?: boolean;
  paper_session_enabled?: boolean;
}

export interface AutonomousAgentSchedules {
  watch_ms?: number;
  research_ms?: number;
}

export interface AutonomousAgentThesis {
  direction?: string;
  strategy?: string;
  confidence?: number;
  rationale?: string;
  updated_at?: string;
}

export interface AutonomousAgentInstance {
  id: string;
  type?: string;
  name: string;
  status: string;
  pause_reason?: "user" | "infra" | null;
  infra_pending?: string[];
  vibe_session_id?: string;
  symbols: string[];
  mandate?: string;
  mandate_config?: Record<string, unknown>;
  constraints?: AutonomousAgentConstraints;
  schedules?: AutonomousAgentSchedules;
  alert_rules?: Record<string, unknown>;
  thesis?: AutonomousAgentThesis;
  last_watch_at?: string | null;
  last_full_reasoning_at?: string | null;
  last_revision_at?: string | null;
  last_decision?: Record<string, unknown> | null;
  streaming?: boolean;
  bootstrap_status?: "pending" | "running" | "awaiting_plan_approval" | "done" | "failed" | "plan_rejected" | null;
  bootstrap_error?: string | null;
  plan_approved_at?: string | null;
  plan_approval_required?: boolean;
  watch_spec?: { rules?: Array<Record<string, unknown>>; strategy?: string };
  runtime?: AutonomousAgentRuntime;
  created_at?: string;
}

export interface AutonomousAgentProposal {
  type: "autonomous_agent.proposal";
  proposal_id: string;
  status: string;
  missing_fields?: string[];
  symbols: string[];
  name: string;
  mandate?: string;
  constraints?: AutonomousAgentConstraints;
  schedules?: AutonomousAgentSchedules;
  alert_rules?: Record<string, unknown>;
  session_id?: string;
  orchestrator_session_id?: string;
  expires_at_ms?: number;
  execution_market?: "IN" | "US";
  execution_backend?: "openalgo" | "alpaca";
  routing_errors?: string[];
  routing_warnings?: string[];
  stack_health?: AutonomousStackHealth;
  mandate_config?: Record<string, unknown>;
  committed_agent_id?: string;
  superseded?: boolean;
  superseded_by?: string;
}

export interface AutonomousAgentsListResponse {
  agents: AutonomousAgentInstance[];
  stack_health?: AutonomousStackHealth;
}

export interface ClearAllAutonomousAgentsResponse {
  status: string;
  stopped: string[];
  deleted: string[];
  remaining_count: number;
  flatten?: {
    openalgo?: { status?: string; remaining_positions?: number; error?: string } | null;
    alpaca?: Array<{ symbol: string; status?: string; error?: string }>;
  };
  auto_paper_stopped?: boolean;
  artifacts_cleared?: Record<string, number>;
  nautilus?: Record<string, unknown>;
  errors?: Array<{ agent_id: string; phase: string; error: string }>;
}

export interface CommitAutonomousAgentRequest {
  proposal_id: string;
  consent_ack: boolean;
  session_id?: string;
}

export interface CommitAutonomousAgentResponse {
  status: string;
  agent: AutonomousAgentInstance;
  vibe_session_id: string;
  paper_session_warnings?: string[];
  already_committed?: boolean;
  infra_paused?: boolean;
}

export interface OrchestratorSessionResponse {
  session_id: string;
  title: string;
}

export interface HaltLiveResponse {
  halted: boolean;
  broker?: string | null;
  reason: string;
  sentinel: string;
}

export interface LiveAuthorizeRequest {
  broker: string;
}

export interface LiveAuthorizeResponse {
  broker: string;
  connector_profile: string;
  oauth_token_present: boolean;
  instruction: string;
  note?: string;
}

/** Mandate limits surfaced inside a `GET /live/status` broker entry (SPEC §7.5). */
export interface LiveMandateLimits {
  max_order_notional_usd?: number;
  max_total_exposure_usd?: number;
  max_leverage?: number;
  max_trades_per_day?: number;
  allowed_instruments?: string[];
  account_funding_usd?: number;
  [key: string]: unknown;
}

/** Active mandate block of a `GET /live/status` broker entry. */
export interface LiveMandateStatus {
  broker?: string;
  mandate_id?: string;
  account_ref?: string;
  created_at?: string;
  limits?: LiveMandateLimits;
  /** ISO timestamp the mandate auto-expires (SPEC §7.5 #7 proactive expiry). */
  expires_at?: string;
  expires_in_seconds?: number | null;
  expired?: boolean;
}

/** Runner liveness block of a `GET /live/status` broker entry (SPEC §7.5 #3). */
export interface LiveRunnerLiveness {
  broker?: string;
  alive: boolean;
  /** Unix epoch seconds of the last heartbeat tick; null if the runner never started. */
  last_tick?: number | string | null;
  last_tick_age_seconds?: number | null;
}

export interface LiveBrokerAuthStatus {
  broker: string;
  oauth_token_present: boolean;
  is_live_broker: boolean;
}

/** Built-in trading connector profile (`GET /trading/connectors`). */
export interface TradingConnectorProfile {
  id: string;
  connector: string;
  label: string;
  environment: string;
  transport: string;
  capabilities: string[];
  readonly: boolean;
  config: Record<string, unknown>;
  notes: string;
  selected: boolean;
}

export interface TradingConnectorsResponse {
  selected_profile: string;
  profiles: TradingConnectorProfile[];
}

export interface SelectTradingConnectorResponse {
  status: string;
  selected_profile: string;
}

export interface TradingConnectorCheckResponse {
  status: string;
  profile_id?: string;
  connector?: string;
  environment?: string;
  transport?: string;
  error?: string;
  analyze_mode?: boolean;
  broker?: string;
  broker_display?: string;
  host?: string;
  switch_url?: string;
  warning?: string;
  token_sync_warning?: string;
  token_sync_ok?: boolean;
  [key: string]: unknown;
}

/** One broker entry in the `GET /live/status` response. */
export interface LiveBrokerStatus {
  auth: LiveBrokerAuthStatus;
  mandate?: LiveMandateStatus | null;
  runner: LiveRunnerLiveness;
  halted: boolean;
}

/** Response of `GET /live/status` (SPEC §7.5 runner status panel + C2). */
export interface LiveStatus {
  brokers: LiveBrokerStatus[];
  global_halted: boolean;
}

/** Response of `POST /live/runner/start|stop`. */
export interface LiveRunnerResponse {
  broker: string;
  started?: boolean;
  already_running?: boolean;
  stopped?: boolean;
  was_running?: boolean;
}

export interface MessageItem {
  message_id: string;
  session_id: string;
  role: string;
  content: string;
  created_at: string;
  linked_attempt_id?: string;
  metadata?: Record<string, unknown>;
}
