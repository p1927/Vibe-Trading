/**
 * Knowledge engine (module 8) — API contract types for the read-only
 * `GET /knowledge/*` routes. Mirrors `trade_integrations.knowledge_engine.query`'s
 * return shapes verbatim; no client-side transformation of the data.
 */

export interface WikiEntry {
  score: number;
  slug: string;
  type: string;
  title: string;
  tags: string[];
  related: string[];
  sources: string[];
  summary: string;
}

export interface WikiListResponse {
  ok: boolean;
  count: number;
  results: WikiEntry[];
  error?: string;
}

export interface WikiPage {
  ok: boolean;
  slug: string;
  found: boolean;
  type?: string;
  title?: string;
  tags?: string[];
  related?: string[];
  sources?: string[];
  content?: string;
  error?: string;
}

export interface StrategyEntry {
  key: string;
  score: number;
  label: string;
  logic?: string;
  mechanics?: string;
  when?: string;
  market_view?: string;
  risk_profile?: string;
  horizon_fit?: string;
  indicators_to_watch?: string[];
  [extra: string]: unknown;
}

export interface StrategyListResponse {
  ok: boolean;
  count: number;
  results: StrategyEntry[];
  error?: string;
}

export interface NewsDerivedConcept {
  score: number;
  tactic_kind?: string;
  trigger_context?: string;
  instrument?: string;
  text: string;
  tags?: string[];
  source_citation?: string;
  [extra: string]: unknown;
}

export interface NewsDerivedConceptsResponse {
  ok: boolean;
  count: number;
  results: NewsDerivedConcept[];
  error?: string;
}

export interface TrackRecordResponse {
  ok: boolean;
  ticker: string;
  scope: string;
  sample_count: number;
  eval_count: number;
  window_days: number;
  mae_pct?: number | null;
  mae_14d_pct?: number | null;
  direction_hit_rate?: number | null;
  direction_hit_rate_14d?: number | null;
  strategy_performance: Record<string, unknown> | null;
  error?: string;
}
