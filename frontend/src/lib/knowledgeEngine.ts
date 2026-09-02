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

export interface FactorTaxonomyEntry {
  factor_id: string;
  category: string;
  default_polarity: string;
  description: string;
}

export interface FactorListResponse {
  ok: boolean;
  count: number;
  results: FactorTaxonomyEntry[];
  error?: string;
}

export interface FactorDetailResponse {
  ok: boolean;
  factor_key: string;
  found: boolean;
  pedagogy: {
    label?: string;
    category?: string;
    expected_direction?: string;
    summary?: string;
    caveat?: string;
    india_caveat?: string;
    interpretation_rules?: string[];
  } | null;
  taxonomy: FactorTaxonomyEntry | null;
  interim_calibration: Record<string, unknown> | null;
  calibration_note: string;
  error?: string;
}

/**
 * financial-knowledge corpus curator (Part B of
 * .claude/backlog/items/2026-09-02-wiki-lifecycle-knowledge-bridge.md) — mirrors
 * `trade_integrations.knowledge_engine.curator.run_financial_knowledge_curation`'s
 * report shape verbatim.
 */
export interface FlaggedSource {
  path: string;
  reason: string;
}

export interface FlaggedWikiPage {
  path: string;
  lines: number;
}

export interface CorpusSizeBucket {
  files: number;
  bytes: number;
  files_delta: number;
  bytes_delta: number;
}

export interface FinancialKnowledgeCuratorReport {
  ok: boolean;
  skipped?: boolean;
  reason?: string;
  ran_at: string;
  sources?: {
    judged: number;
    remaining: number;
    flagged: FlaggedSource[];
    flagged_count: number;
  };
  distillation?: {
    scanned: number;
    flagged: FlaggedWikiPage[];
    flagged_count: number;
    line_threshold: number;
  };
  ingest?: {
    ok: boolean;
    skipped?: boolean;
    reason?: string;
    error?: string;
  };
  growth?: {
    buckets: Record<string, CorpusSizeBucket>;
  };
}

export interface FinancialKnowledgeStatusResponse {
  ok: boolean;
  has_run: boolean;
  report: FinancialKnowledgeCuratorReport | null;
  error?: string;
}
