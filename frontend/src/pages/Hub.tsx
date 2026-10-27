import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { Database, ExternalLink, Loader2, Newspaper, RefreshCw, Search } from "lucide-react";
import { cn } from "@/lib/utils";
import { api, type HubDiscardedNewsItem, type HubNewsItem, type HubNewsPipelineConfig, type HubStatusQuery, type HubStatusResponse, type ObservabilitySummaryResponse } from "@/lib/api";
import { safeGet, safeSet } from "@/lib/storage";
import { RepeatIntervalPicker } from "@/components/RepeatIntervalPicker";
import { ObservabilityIssuesPanel } from "@/components/ObservabilityIssuesPanel";
import { NewsPipelineGraph } from "@/components/NewsPipelineGraph";
import { EventsCalendar } from "@/components/news/EventsCalendar";
import { FactorRegistryGaps } from "@/components/news/FactorRegistryGaps";
import { NewsPipelineStatusStrip } from "@/components/news/NewsPipelineStatusStrip";
import { formatIst } from "@/lib/istTime";
import { useConfirm } from "@/components/common/useConfirm";
import { safeHttpUrl } from "@/lib/safeUrl";

type NewsFilter = "all" | "staging" | "distilled" | "discarded";

const NEWS_FILTER_LABEL_KEYS = {
  all: "hub.filterAll",
  staging: "hub.filterStaging",
  distilled: "hub.filterDistilled",
  discarded: "hub.filterDiscarded",
} as const satisfies Record<NewsFilter, string>;

/** A number field's parsed value, or `fallback` only when the text is not a number (so 0 is a real 0). */
function numberOr(raw: string, fallback: number, parse: (text: string) => number): number {
  const n = parse(raw);
  return Number.isNaN(n) ? fallback : n;
}

function matchesNewsProvenance(provenance: string | undefined, filter: NewsFilter): boolean {
  if (filter === "all") return true;
  const value = (provenance || "").toLowerCase();
  if (filter === "staging") return value === "staging";
  if (filter === "distilled") return value === "distilled" || value === "distilled_event";
  return value === filter;
}

function StatCard({
  title,
  children,
  className,
}: {
  title: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("rounded-xl border bg-card p-4 shadow-sm", className)}>
      <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">{title}</p>
      <div className="mt-2">{children}</div>
    </div>
  );
}

function provenanceBadge(t: TFunction, provenance?: string) {
  if (provenance === "discarded") {
    return (
      <span className="rounded-full bg-red-500/15 px-2 py-0.5 text-[10px] font-medium text-red-800 dark:text-red-200">
        {t("hub.provDiscarded")}
      </span>
    );
  }
  if (provenance === "staging") {
    return (
      <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-medium text-amber-800 dark:text-amber-200">
        {t("hub.provStaging")}
      </span>
    );
  }
  return (
    <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-medium text-emerald-700 dark:text-emerald-300">
      {t("hub.provDistilled")}
    </span>
  );
}

function statusBadge(t: TFunction, status?: string) {
  const s = (status || "pending").toLowerCase();
  if (s === "approved") {
    return (
      <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] text-emerald-700 dark:text-emerald-300">
        {t("hub.statusVerified")}
      </span>
    );
  }
  if (s === "partial") {
    return (
      <span className="rounded-full bg-blue-500/10 px-2 py-0.5 text-[10px] text-blue-700 dark:text-blue-300">
        {t("hub.statusPartial")}
      </span>
    );
  }
  if (s === "pending") {
    return (
      <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
        {t("hub.statusPendingDistill")}
      </span>
    );
  }
  return (
    <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">{s}</span>
  );
}

const IMPACT_LABEL_KEYS = {
  observed: "hub.impactObserved",
  predicted: "hub.impactPredicted",
  claimed: "hub.impactClaimed",
  unverified: "hub.impactUnverified",
  no_forecast: "hub.impactNoForecast",
} as const;

function marketImpactBadge(t: TFunction, status?: string) {
  const s = (status || "unverified").toLowerCase();
  const tone =
    s === "observed"
      ? "text-emerald-700 dark:text-emerald-400 bg-emerald-500/10"
      : s === "predicted"
        ? "text-blue-700 dark:text-blue-400 bg-blue-500/10"
        : s === "claimed"
          ? "text-amber-700 dark:text-amber-400 bg-amber-500/10"
          : // An explicit "no_forecast" (Decision 14 of 2026-09-06-news-impact-constant:
            // the engine ran and declined to predict) is a distinct state from "unverified"
            // (nothing has looked at it yet), so it gets its own neutral-but-deliberate tone
            // rather than sharing the do-nothing muted default.
            s === "no_forecast"
            ? "text-slate-700 dark:text-slate-300 bg-slate-500/10 ring-1 ring-slate-500/20"
            : "text-muted-foreground bg-muted";
  return (
    <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-medium capitalize", tone)}>
      {s in IMPACT_LABEL_KEYS ? t(IMPACT_LABEL_KEYS[s as keyof typeof IMPACT_LABEL_KEYS]) : s}
    </span>
  );
}

function predictionHiddenBadge(t: TFunction, item: HubNewsItem) {
  if (item.provenance === "staging") return null;
  const refCount = item.ref_count ?? item.references?.length ?? item.sources?.length ?? 1;
  if (refCount >= 2) return null;
  return (
    <span className="rounded-full bg-violet-500/10 px-2 py-0.5 text-[10px] font-medium text-violet-800 dark:text-violet-200">
      {t("hub.hiddenFromPrediction")}
    </span>
  );
}

function consensusBadge(consensus?: {
  direction?: string;
  confidence?: number;
  ref_count?: number;
}) {
  if (!consensus?.direction) return null;
  const direction = consensus.direction;
  const tone =
    direction === "bullish"
      ? "text-emerald-700 dark:text-emerald-400 bg-emerald-500/10"
      : direction === "bearish"
        ? "text-red-700 dark:text-red-400 bg-red-500/10"
        : "text-muted-foreground bg-muted";
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium capitalize", tone)}>
      {direction}
      {consensus.confidence != null ? ` · ${Math.round(consensus.confidence * 100)}%` : ""}
    </span>
  );
}

function directionHintBadge(hint?: string) {
  const value = (hint || "unclear").toLowerCase();
  const tone =
    value.includes("bull") || value === "up" || value === "positive"
      ? "text-emerald-700 dark:text-emerald-400 bg-emerald-500/10"
      : value.includes("bear") || value === "down" || value === "negative"
        ? "text-red-700 dark:text-red-400 bg-red-500/10"
        : "text-muted-foreground bg-muted";
  return (
    <span className={cn("rounded-full px-1.5 py-0.5 text-[10px] font-medium capitalize", tone)}>
      {value}
    </span>
  );
}

function enrichmentModeBadge(t: TFunction, mode?: string) {
  if (!mode) return null;
  const label = mode === "full" ? t("hub.modeFullArticle") : mode === "snippet_fallback" ? t("hub.modeSnippet") : mode;
  return (
    <span className="rounded-full bg-sky-500/10 px-2 py-0.5 text-[10px] font-medium text-sky-800 dark:text-sky-200">
      {label}
    </span>
  );
}

function NewsEnrichmentPanel({ item }: { item: HubNewsItem }) {
  const { t } = useTranslation();
  const causes = item.cause_indicators ?? [];
  const futureEvents = item.future_events ?? [];
  const opinions = item.article_opinions ?? [];
  const modes = item.enrichment_modes ?? [];

  if (!causes.length && !futureEvents.length && !opinions.length && !modes.length) {
    return null;
  }

  return (
    <div className="mt-3 space-y-2 rounded-lg border border-dashed bg-muted/10 p-2.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          {t("hub.enrichment")}
        </span>
        {modes.map((mode) => (
          <span key={mode}>{enrichmentModeBadge(t, mode)}</span>
        ))}
      </div>

      {causes.length ? (
        <div>
          <p className="mb-1 text-[10px] font-medium text-muted-foreground">{t("hub.causeIndicators")}</p>
          <ul className="space-y-1.5">
            {causes.slice(0, 6).map((cause, idx) => (
              <li
                key={`${cause.factor || "cause"}-${idx}`}
                className="rounded-md border bg-background/80 px-2 py-1.5 text-[11px]"
              >
                <div className="flex flex-wrap items-center gap-1.5">
                  {cause.factor ? (
                    <span className="font-medium text-foreground">{cause.factor}</span>
                  ) : null}
                  {cause.direction_hint ? directionHintBadge(cause.direction_hint) : null}
                </div>
                {cause.mechanism ? (
                  <p className="mt-0.5 leading-snug text-foreground/90 line-clamp-2">{cause.mechanism}</p>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {futureEvents.length ? (
        <div>
          <p className="mb-1 text-[10px] font-medium text-muted-foreground">{t("hub.futureTimeline")}</p>
          <ul className="space-y-1.5">
            {futureEvents.slice(0, 6).map((event, idx) => (
              <li
                key={`${event.event || "event"}-${event.expected_date || idx}`}
                className="rounded-md border bg-background/80 px-2 py-1.5 text-[11px]"
              >
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                  <span className="font-medium text-foreground">{event.event || t("hub.upcomingEvent")}</span>
                  {event.expected_date ? (
                    <span className="text-[10px] tabular-nums text-muted-foreground">{event.expected_date}</span>
                  ) : event.timeline_phrase ? (
                    <span className="text-[10px] text-muted-foreground">{event.timeline_phrase}</span>
                  ) : null}
                </div>
                {event.index_impact_mechanism ? (
                  <p className="mt-0.5 leading-snug text-muted-foreground line-clamp-2">
                    {event.index_impact_mechanism}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {opinions.length ? (
        <details className="text-[11px]">
          <summary className="cursor-pointer text-amber-800 hover:text-amber-900 dark:text-amber-200">
            {t("hub.articleOpinions", { n: opinions.length })}
          </summary>
          <ul className="mt-1 space-y-1 pl-1">
            {opinions.slice(0, 5).map((opinion, idx) => (
              <li key={`opinion-${idx}`} className="rounded border border-amber-500/20 bg-amber-500/5 px-2 py-1">
                <p className="text-foreground/90 line-clamp-2">{opinion.text || opinion.kind || t("hub.opinionFallback")}</p>
                {opinion.reason_discarded ? (
                  <p className="text-[10px] text-muted-foreground">{opinion.reason_discarded}</p>
                ) : null}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

function NewsRow({
  item,
  onDiscard,
  onDiscardSimilar,
  discarding,
}: {
  item: HubNewsItem;
  onDiscard?: (item: HubNewsItem) => void;
  onDiscardSimilar?: (item: HubNewsItem) => void;
  discarding?: boolean;
}) {
  const { t } = useTranslation();
  const references = item.references?.length ? item.references : item.sources ?? [];
  const link = safeHttpUrl(item.url || references[0]?.url);
  const timeline = item.timeline ?? [];

  return (
    <article className="rounded-lg border bg-background/60 p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-1.5">
            {provenanceBadge(t, item.provenance)}
            {statusBadge(t, item.verification_status)}
            {item.market_impact_status ? marketImpactBadge(t, item.market_impact_status) : null}
            {consensusBadge(item.consensus)}
            {predictionHiddenBadge(t, item)}
            {item.ticker ? (
              <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                {item.ticker}
              </span>
            ) : null}
            {(item.ref_count ?? 0) > 1 ? (
              <span className="text-[10px] tabular-nums text-muted-foreground">
                {t("hub.refsN", { n: item.ref_count })}
              </span>
            ) : null}
          </div>
          <h3 className="text-[13px] font-semibold leading-snug">{item.title || t("hub.untitled")}</h3>
          <p className="text-[11px] text-muted-foreground">
            {item.source || t("hub.unknownSource")}
            {item.published_at ? ` · ${formatIst(item.published_at)}` : ""}
            {item.event_id ? ` · ${item.event_id.slice(0, 8)}` : item.ref_id ? ` · ${item.ref_id}` : ""}
          </p>
        </div>
        {link ? (
          <a
            href={safeHttpUrl(link)}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex shrink-0 items-center gap-1 rounded-md border px-2 py-1 text-[10px] hover:bg-muted/50"
          >
            {t("hub.open")} <ExternalLink className="h-3 w-3" />
          </a>
        ) : null}
        {onDiscard ? (
          <div className="flex shrink-0 flex-col gap-1">
            <button
              type="button"
              disabled={discarding}
              onClick={() => onDiscard(item)}
              className="rounded-md border border-red-500/30 px-2 py-1 text-[10px] text-red-700 hover:bg-red-500/10 disabled:opacity-50 dark:text-red-300"
            >
              {t("hub.discard")}
            </button>
            {onDiscardSimilar ? (
              <button
                type="button"
                disabled={discarding}
                onClick={() => onDiscardSimilar(item)}
                className="rounded-md border px-2 py-1 text-[10px] hover:bg-muted/50 disabled:opacity-50"
              >
                {t("hub.discardSimilar")}
              </button>
            ) : null}
          </div>
        ) : null}
      </div>

      {item.summary ? (
        <p className="mt-2 text-[12px] leading-relaxed text-foreground/90 line-clamp-3">{item.summary}</p>
      ) : null}

      <NewsEnrichmentPanel item={item} />

      {references.length ? (
        <details className="mt-2 text-[11px]">
          <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
            {t("hub.referencesN", { n: references.length })}
          </summary>
          <ul className="mt-1 space-y-1 pl-1">
            {references.map((ref, idx) => {
              const refUrl = safeHttpUrl(ref.url);
              const label = ref.title || ref.publisher || ref.source || ref.vendor || ref.ref_id || t("hub.refFallback", { n: idx + 1 });
              const refCauses = ref.cause_indicators?.length ?? 0;
              const refFuture = ref.future_events?.length ?? 0;
              return (
                <li key={`${ref.ref_id || ref.url || idx}`} className="space-y-1">
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <span className="font-medium text-foreground/90">{label}</span>
                    {ref.enrichment_mode ? enrichmentModeBadge(t, ref.enrichment_mode) : null}
                    {refCauses ? (
                      <span className="text-[10px] text-muted-foreground">{t("hub.causesN", { n: refCauses })}</span>
                    ) : null}
                    {refFuture ? (
                      <span className="text-[10px] text-muted-foreground">{t("hub.upcomingN", { n: refFuture })}</span>
                    ) : null}
                    {refUrl ? (
                      <a
                        href={safeHttpUrl(refUrl)}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="truncate text-blue-600 hover:underline dark:text-blue-400"
                      >
                        {refUrl}
                      </a>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ul>
        </details>
      ) : null}

      {timeline.length ? (
        <details className="mt-2 text-[11px]">
          <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
            {t("hub.eventTimeline", { n: timeline.length })}
          </summary>
          <ul className="mt-1 space-y-1 pl-1">
            {timeline.map((entry, idx) => (
              <li key={`${entry.at || idx}-${entry.kind || "entry"}`} className="text-foreground/90">
                <span className="font-medium capitalize">{entry.kind || t("hub.timelineUpdate")}</span>
                {entry.at ? (
                  <span className="text-muted-foreground"> · {formatIst(entry.at)}</span>
                ) : null}
                {entry.summary ? <p className="text-muted-foreground line-clamp-2">{entry.summary}</p> : null}
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      {(item.tags?.topics?.length ?? 0) > 0 || (item.tags?.factors?.length ?? 0) > 0 ? (
        <div className="mt-2 flex flex-wrap gap-1">
          {(item.tags?.topics ?? []).slice(0, 4).map((topic) => (
            <span key={topic} className="rounded bg-blue-500/10 px-1.5 py-0.5 text-[10px] text-blue-700 dark:text-blue-300">
              {topic}
            </span>
          ))}
          {(item.tags?.factors ?? []).slice(0, 4).map((factor) => (
            <span key={factor} className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
              {factor}
            </span>
          ))}
        </div>
      ) : null}
    </article>
  );
}

function relevanceBadge(t: TFunction, score?: number, provisional?: boolean) {
  if (typeof score !== "number") return null;
  const tone =
    score > 0.7
      ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
      : score >= 0.4
        ? "bg-amber-500/15 text-amber-800 dark:text-amber-200"
        : "bg-muted text-muted-foreground";
  return (
    <span
      className={cn("rounded-full px-2 py-0.5 text-[10px] font-semibold tabular-nums", tone)}
      title={provisional ? t("hub.relevanceProvisionalTitle") : t("hub.relevanceTitle")}
    >
      {score.toFixed(2)}
      {provisional ? "*" : ""}
    </span>
  );
}

function RankedNewsCard({
  item,
  expanded,
  onToggleExpand,
  onDiscard,
  onDiscardSimilar,
  discarding,
}: {
  item: HubNewsItem;
  expanded: boolean;
  onToggleExpand: () => void;
  onDiscard?: (item: HubNewsItem) => void;
  onDiscardSimilar?: (item: HubNewsItem) => void;
  discarding?: boolean;
}) {
  const { t } = useTranslation();
  const members = item.group_members ?? [];
  const sourceCount = item.source_count ?? members.length ?? 0;
  const link = safeHttpUrl(item.url || item.references?.[0]?.url || item.sources?.[0]?.url);
  const extraSources = Math.max(0, sourceCount - 1);

  return (
    <article className="flex flex-col rounded-lg border bg-background/60 p-3">
      <div className="flex flex-wrap items-center gap-1.5">
        {relevanceBadge(t, item.market_relevance_score, item.market_relevance_provisional)}
        {provenanceBadge(t, item.provenance)}
        {item.ticker ? (
          <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            {item.ticker}
          </span>
        ) : null}
      </div>
      <h3 className="mt-1.5 text-[13px] font-semibold leading-snug line-clamp-2">
        {item.title || t("hub.untitled")}
      </h3>
      <p className="mt-1 text-[11px] text-muted-foreground">
        {item.source || t("hub.unknownSource")}
        {item.published_at ? ` · ${formatIst(item.published_at)}` : ""}
      </p>
      {item.summary ? (
        <p className="mt-1.5 text-[12px] leading-relaxed text-foreground/90 line-clamp-1">
          {item.summary}
        </p>
      ) : null}

      {extraSources > 0 ? (
        <button
          type="button"
          onClick={onToggleExpand}
          className="mt-2 self-start rounded-md border px-2 py-0.5 text-[10px] text-muted-foreground hover:bg-muted/50"
        >
          {expanded ? t("hub.hideSources") : t(extraSources > 1 ? "hub.moreSourcesOther" : "hub.moreSourceOne", { n: extraSources })}
        </button>
      ) : null}

      {expanded && members.length ? (
        <ul className="mt-2 space-y-1 border-t pt-2 text-[11px]">
          {members.map((member, idx) => (
            <li key={member.ref_id || `${member.url}-${idx}`} className="truncate">
              <span className="text-muted-foreground">{member.source || t("hub.sourceFallback")} · </span>
              {safeHttpUrl(member.url) ? (
                <a
                  href={safeHttpUrl(member.url)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-blue-600 hover:underline dark:text-blue-400"
                >
                  {member.title || member.url}
                </a>
              ) : (
                <span>{member.title || t("hub.untitled")}</span>
              )}
            </li>
          ))}
        </ul>
      ) : null}

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {link ? (
          <a
            href={safeHttpUrl(link)}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[10px] hover:bg-muted/50"
          >
            {t("hub.open")} <ExternalLink className="h-3 w-3" />
          </a>
        ) : null}
        {onDiscard ? (
          <button
            type="button"
            disabled={discarding}
            onClick={() => onDiscard(item)}
            className="rounded-md border border-red-500/30 px-2 py-1 text-[10px] text-red-700 hover:bg-red-500/10 disabled:opacity-50 dark:text-red-300"
          >
            {t("hub.discard")}
          </button>
        ) : null}
        {onDiscardSimilar ? (
          <button
            type="button"
            disabled={discarding}
            onClick={() => onDiscardSimilar(item)}
            className="rounded-md border px-2 py-1 text-[10px] hover:bg-muted/50 disabled:opacity-50"
          >
            {t("hub.discardSimilar")}
          </button>
        ) : null}
      </div>
    </article>
  );
}

function DiscardedRow({
  item,
  onUndo,
  busy,
}: {
  item: HubDiscardedNewsItem;
  onUndo?: (item: HubDiscardedNewsItem) => void;
  busy?: boolean;
}) {
  const { t } = useTranslation();
  const expired = item.expires_at ? new Date(item.expires_at).getTime() < Date.now() : false;
  return (
    <article className="rounded-lg border border-dashed bg-muted/20 p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-1.5">
            {provenanceBadge(t, "discarded")}
            {item.source_kind ? (
              <span className="text-[10px] text-muted-foreground">{item.source_kind}</span>
            ) : null}
          </div>
          <h3 className="text-[13px] font-semibold leading-snug">{item.title || t("hub.untitled")}</h3>
          <p className="text-[11px] text-muted-foreground">
            {item.reason || t("hub.discardedFallback")}
            {item.discarded_at ? ` · ${formatIst(item.discarded_at)}` : ""}
          </p>
          {item.expires_at ? (
            <p className="text-[10px] text-muted-foreground">
              {t("hub.expires", { date: item.expires_at.slice(0, 10) })}
              {expired ? ` ${t("hub.expired")}` : ""}
            </p>
          ) : null}
        </div>
        {onUndo ? (
          <button
            type="button"
            disabled={busy || expired}
            onClick={() => onUndo(item)}
            className="rounded-md border px-2 py-1 text-[10px] hover:bg-muted/50 disabled:opacity-50"
          >
            {t("hub.undo")}
          </button>
        ) : null}
      </div>
      {safeHttpUrl(item.url) ? (
        <a
          href={safeHttpUrl(item.url)}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-2 inline-block truncate text-[11px] text-blue-600 hover:underline dark:text-blue-400"
        >
          {item.url}
        </a>
      ) : null}
    </article>
  );
}

export function Hub() {
  const { t } = useTranslation();
  const [confirm, confirmDialog] = useConfirm();
  const [data, setData] = useState<HubStatusResponse | null>(null);
  const [obsSummary, setObsSummary] = useState<ObservabilitySummaryResponse | null>(null);
  const [pipelineConfig, setPipelineConfig] = useState<HubNewsPipelineConfig | null>(null);
  const [pipelineDraft, setPipelineDraft] = useState<HubNewsPipelineConfig | null>(null);
  const pipelineBaseline = useRef<HubNewsPipelineConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [secondaryError, setSecondaryError] = useState<string | null>(null);
  const [newsFilter, setNewsFilter] = useState<NewsFilter>("all");
  const [showQueue, setShowQueue] = useState(false);
  const [scheduleExpanded, setScheduleExpanded] = useState(() => safeGet("vibe-hub-schedule-expanded") === "1");
  const [newsView, setNewsView] = useState<"list" | "pipeline" | "events">("list");
  const [newsSection, setNewsSection] = useState<"ranked" | "all">("ranked");
  const [searchInput, setSearchInput] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [newsPage, setNewsPage] = useState(1);
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});

  const NEWS_PAGE_SIZE = 20;
  const ALL_NEWS_WINDOW_HOURS = 24;

  const [maintainerSummary, setMaintainerSummary] = useState<string | null>(null);

  const newsQuery = useMemo<HubStatusQuery>(() => {
    const query: HubStatusQuery = {
      page: newsPage,
      pageSize: NEWS_PAGE_SIZE,
      sort: newsSection === "all" ? "time" : "relevance",
    };
    if (searchQuery) query.search = searchQuery;
    if (newsSection === "all") query.windowHours = ALL_NEWS_WINDOW_HOURS;
    if (newsFilter !== "all" && newsFilter !== "discarded") query.provenance = newsFilter;
    return query;
  }, [newsFilter, newsPage, newsSection, searchQuery]);

  // A slower response for a previous page/search must not overwrite the current one.
  const loadSeq = useRef(0);
  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    setError(null);
    try {
      // The config and the issue count are secondary reads: their failure must not blank the page,
      // but it must show (a missing schedule card used to look like "no schedule exists").
      const secondaryErrors: string[] = [];
      const secondary = <T,>(label: string, call: Promise<T>) =>
        call.catch((err: unknown) => {
          secondaryErrors.push(`${label}: ${err instanceof Error ? err.message : String(err)}`);
          return null;
        });
      const [res, cfgRes, obsRes] = await Promise.all([
        api.getHubStatus("NIFTY", newsQuery),
        secondary(t("hub.secondaryScheduleLabel"), api.getHubNewsPipelineConfig()),
        secondary(t("hub.secondaryIssueLabel"), api.getObservabilitySummary()),
      ]);
      if (seq !== loadSeq.current) return;
      setData(res);
      setObsSummary(obsRes);
      setSecondaryError(secondaryErrors.length ? t("hub.couldNotLoad", { list: secondaryErrors.join("; ") }) : null);
      // Never clobber an unsaved draft with the 30s poll.
      const cfg = cfgRes?.config;
      if (cfg) {
        setPipelineConfig(cfg);
        setPipelineDraft((draft) => {
          if (draft) return draft;
          pipelineBaseline.current = cfg;
          return cfg;
        });
      }
    } catch (err) {
      if (seq !== loadSeq.current) return;
      setError(err instanceof Error ? err.message : t("hub.loadFailed"));
    } finally {
      if (seq === loadSeq.current) setLoading(false);
    }
  }, [newsQuery, t]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => {
      void load();
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    const timer = window.setTimeout(() => setSearchQuery(searchInput.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  // Any change to what is being listed invalidates the current page offset.
  useEffect(() => {
    setNewsPage(1);
  }, [searchQuery, newsFilter, newsSection, showQueue]);

  useEffect(() => {
    safeSet("vibe-hub-schedule-expanded", scheduleExpanded ? "1" : "0");
  }, [scheduleExpanded]);

  const hub = data?.hub;
  const sourceAvailability = hub?.source_availability;
  const newsMaintainer = hub?.news_maintainer;
  const staging = hub?.news_staging;
  const newsInventory = hub?.news_inventory;
  const verified = hub?.verified_news;
  const indexResearch = hub?.index_research;
  const constituentCache = hub?.constituent_cache;
  const capture = hub?.capture;
  const factorCoverage = hub?.factor_coverage;
  // hub_news_pipeline_status()'s registry-gap counts, already carried by /trade/hub/status.
  // Read here so the payload has an actual renderer rather than being transported and dropped.
  const registryGapPending = hub?.news_pipeline?.factor_registry_gaps?.pending_count ?? 0;

  const newsPageMeta = data?.news_page;

  // "discarded" and "raw queue only" are unpaginated server-side payloads, so
  // those two views page on the client; everything else uses the server page.
  const clientPaged = newsFilter === "discarded" || showQueue;

  const newsSource = useMemo(() => {
    if (newsFilter === "discarded") {
      return (newsInventory?.discarded_items ?? []) as HubDiscardedNewsItem[];
    }
    if (showQueue) {
      const queue = (newsInventory?.staging_queue ?? []).filter((item) =>
        matchesNewsProvenance(item.provenance, newsFilter),
      );
      const needle = searchQuery.toLowerCase();
      return needle
        ? queue.filter((item) =>
            `${item.title ?? ""} ${item.summary ?? ""}`.toLowerCase().includes(needle),
          )
        : queue;
    }
    return newsInventory?.items ?? [];
  }, [
    newsFilter,
    newsInventory?.discarded_items,
    newsInventory?.items,
    newsInventory?.staging_queue,
    searchQuery,
    showQueue,
  ]);

  const filteredNews = useMemo(() => {
    if (!clientPaged) return newsSource;
    const start = (newsPage - 1) * NEWS_PAGE_SIZE;
    return newsSource.slice(start, start + NEWS_PAGE_SIZE);
  }, [clientPaged, newsPage, newsSource]);

  const totalNewsCount = clientPaged ? newsSource.length : newsPageMeta?.total_count ?? newsSource.length;
  const hasMoreNews = clientPaged
    ? newsPage * NEWS_PAGE_SIZE < newsSource.length
    : Boolean(newsPageMeta?.has_more);
  const newsRangeStart = totalNewsCount === 0 ? 0 : (newsPage - 1) * NEWS_PAGE_SIZE + 1;
  const newsRangeEnd = Math.min(newsPage * NEWS_PAGE_SIZE, totalNewsCount);

  const toggleGroup = (key: string) =>
    setExpandedGroups((prev) => ({ ...prev, [key]: !prev[key] }));

  const resolveItemId = (item: HubNewsItem) =>
    item.ref_id || item.event_id || item.id || "";

  const resolveSourceKind = (item: HubNewsItem) =>
    item.provenance === "staging" || String(item.ref_id || "").startsWith("ref:") ? "staging" : "distilled";

  const discardNews = async (item: HubNewsItem, discardSimilar = false) => {
    const itemId = resolveItemId(item);
    if (!itemId) return;
    if (
      discardSimilar &&
      !(await confirm({
        title: t("hub.discardSimilarTitle"),
        description: t("hub.discardSimilarDesc"),
        confirmLabel: t("hub.discard"),
        tone: "destructive",
      }))
    ) {
      return;
    }
    setBusy(discardSimilar ? "discard-similar" : "discard");
    setError(null);
    try {
      await api.discardHubNews({
        entity_id: "NIFTY",
        item_id: itemId,
        source_kind: resolveSourceKind(item),
        discard_similar: discardSimilar,
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("hub.discardFailed"));
    } finally {
      setBusy(null);
    }
  };

  const undoDiscard = async (item: HubDiscardedNewsItem) => {
    const discardId = item.discard_id || item.id;
    if (!discardId) return;
    setBusy("undo-discard");
    setError(null);
    try {
      await api.undoHubNewsDiscard({ entity_id: "NIFTY", discard_id: discardId });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("hub.undoFailed"));
    } finally {
      setBusy(null);
    }
  };

  const drainStaging = async () => {
    setBusy("drain");
    setError(null);
    try {
      const res = await api.drainHubStaging("NIFTY", 20);
      if (res.status === "paused") {
        setError(res.message || t("hub.drainPaused"));
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("hub.drainFailed"));
    } finally {
      setBusy(null);
    }
  };

  const runIngest = async (mode: "full" | "light") => {
    setBusy(`ingest-${mode}`);
    setError(null);
    try {
      const res = await api.runHubNewsIngest({ mode, ticker: "NIFTY" });
      if (res.status === "paused") {
        setError(res.message || t("hub.ingestPaused"));
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : t(mode === "full" ? "hub.ingestFullFailed" : "hub.ingestLightFailed"));
    } finally {
      setBusy(null);
    }
  };

  const runMaintenance = async () => {
    setBusy("maintenance");
    setError(null);
    setMaintainerSummary(null);
    try {
      const res = await api.runHubNewsMaintenance("NIFTY", 365);
      if (res.status === "paused") {
        setError(res.message || t("hub.maintainerPaused"));
      } else if (res.summary?.had_errors) {
        setError(t("hub.maintainerErrors"));
      }
      const summary = res.summary;
      if (summary && typeof summary === "object") {
        const compact = summary.compact_events as Record<string, unknown> | undefined;
        const parts = [
          compact?.groups_merged != null ? t("hub.partMerged", { n: String(compact.groups_merged) }) : null,
          compact?.rows_removed != null ? t("hub.partRemoved", { n: String(compact.rows_removed) }) : null,
          summary.repair && typeof summary.repair === "object"
            ? t("hub.partRepaired", { n: String((summary.repair as Record<string, unknown>).repaired ?? 0) })
            : null,
        ].filter(Boolean);
        if (parts.length) {
          setMaintainerSummary(t("hub.maintainerSummary", { parts: parts.join(" · ") }));
        }
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("hub.maintainerFailed"));
    } finally {
      setBusy(null);
    }
  };

  const savePipelineConfig = async () => {
    if (!pipelineDraft) return;
    setBusy("save-pipeline");
    setError(null);
    try {
      const changes = {
        full_ingest_cron: pipelineDraft.full_ingest_cron,
        light_ingest_cron: pipelineDraft.light_ingest_cron,
        entity_drain_cron: pipelineDraft.entity_drain_cron,
        entity_maintenance_cron: pipelineDraft.entity_maintenance_cron,
        entity_drain_continuous_cron: pipelineDraft.entity_drain_continuous_cron,
        entity_backpressure_threshold: pipelineDraft.entity_backpressure_threshold,
        full_ingest_sources: pipelineDraft.full_ingest_sources,
        light_ingest_sources: pipelineDraft.light_ingest_sources,
        full_lookback_days: pipelineDraft.full_lookback_days,
        light_lookback_days: pipelineDraft.light_lookback_days,
        entity_batch_size: pipelineDraft.entity_batch_size,
        cluster_threshold: pipelineDraft.cluster_threshold,
        relevance_gate_enabled: pipelineDraft.relevance_gate_enabled,
        relevance_min_confidence: pipelineDraft.relevance_min_confidence,
        relevance_rule_first: pipelineDraft.relevance_rule_first,
        discard_retention_days: pipelineDraft.discard_retention_days,
        wiki_search_enabled: pipelineDraft.wiki_search_enabled,
        wiki_search_top_k: pipelineDraft.wiki_search_top_k,
        wiki_search_max_per_pass: pipelineDraft.wiki_search_max_per_pass,
        wiki_search_min_score: pipelineDraft.wiki_search_min_score,
      };
      const patch = Object.fromEntries(
        Object.entries(changes).filter(([key, value]) =>
          value !== pipelineBaseline.current?.[key as keyof HubNewsPipelineConfig]),
      );
      const res = await api.updateHubNewsPipelineConfig(patch);
      if (res.config) {
        setPipelineConfig(res.config);
        setPipelineDraft(res.config);
        pipelineBaseline.current = res.config;
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t("hub.savePipelineFailed"));
    } finally {
      setBusy(null);
    }
  };

  const patchDraft = (patch: Partial<HubNewsPipelineConfig>) => {
    setPipelineDraft((prev) => ({ ...(prev ?? {}), ...patch }));
  };

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 md:p-6">
      {confirmDialog}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold tracking-tight">{t("hub.title")}</h1>
            {(obsSummary?.open_issue_count ?? 0) > 0 ? (
              <span
                className="rounded-full bg-red-500/15 px-2.5 py-0.5 text-[11px] font-medium text-red-800 dark:text-red-200"
                title={t("hub.openIssuesTitle")}
              >
                {t((obsSummary?.open_issue_count ?? 0) === 1 ? "hub.openIssueOne" : "hub.openIssueOther", { n: obsSummary?.open_issue_count })}
              </span>
            ) : null}
          </div>
          <ObservabilityIssuesPanel refreshKey={obsSummary?.open_issue_count} />
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            {t("hub.subtitle")}
          </p>
          {hub?.generated_at ? (
            <p className="mt-1 text-[11px] text-muted-foreground">
              {t("hub.updated", { when: formatIst(hub.generated_at) })}
            </p>
          ) : null}
        </div>
        <button
          type="button"
          onClick={() => {
            setLoading(true);
            void load();
          }}
          className="inline-flex h-9 items-center gap-1.5 rounded-lg border bg-background px-3 text-sm hover:bg-muted/50"
        >
          <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          {t("hub.refresh")}
        </button>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      {secondaryError ? (
        <p role="alert" className="text-sm text-destructive">
          {secondaryError}
        </p>
      ) : null}
      {maintainerSummary ? (
        <p className="text-sm text-muted-foreground">{maintainerSummary}</p>
      ) : null}

      {staging?.pipeline_paused ? (
        <div
          role="status"
          className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-950 dark:text-amber-100"
        >
          <p className="font-medium">{t("hub.distillationPaused")}</p>
          <p className="mt-1 text-[13px] leading-relaxed opacity-90">
            {staging.pause_reason || t("hub.pauseReasonDefault")}
          </p>
          <p className="mt-2 text-[12px] tabular-nums opacity-80">
            {t("hub.headlinesWaiting", { n: newsInventory?.pending_count ?? staging.queued ?? "—" })}
          </p>
        </div>
      ) : null}

      {staging?.wiki_deferred ? (
        <div
          role="status"
          data-testid="wiki-deferred-banner"
          className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-950 dark:text-amber-100"
        >
          <p className="font-medium">{t("hub.wikiNotRunning")}</p>
          <p className="mt-1 text-[13px] leading-relaxed opacity-90">
            {staging.user_message || t("hub.wikiDefaultMessage")}{" "}
            {t("hub.wikiDeferredNote")}
          </p>
        </div>
      ) : null}

      {(hub?.data_warnings?.length ?? 0) > 0 ? (
        <div
          role="status"
          className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-950 dark:text-amber-100"
        >
          <p className="font-medium">{t("hub.dataDegradation")}</p>
          <ul className="mt-1 list-disc space-y-1 pl-4 text-[13px] leading-relaxed opacity-90">
            {hub?.data_warnings?.map((warning, idx) => <li key={idx}>{warning}</li>)}
          </ul>
        </div>
      ) : null}

      {loading && !hub ? (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          {t("hub.loadingHub")}
        </div>
      ) : null}

      {pipelineDraft ? (
        <StatCard title={t("hub.scheduleTitle")} className="col-span-full">
          <details
            open={scheduleExpanded}
            onToggle={(e) => setScheduleExpanded((e.target as HTMLDetailsElement).open)}
          >
            <summary className="cursor-pointer select-none text-[12px] font-medium text-foreground">
              {scheduleExpanded ? t("hub.hideSchedule") : t("hub.showSchedule")}
            </summary>
            <p className="mb-3 mt-2 text-[12px] text-muted-foreground">
              {t("hub.scheduleHelp")}
            </p>
            <div className="grid gap-4 md:grid-cols-2">
            <label className="space-y-1 text-sm">
              <span className="text-[11px] text-muted-foreground">{t("hub.lblFullCron")}</span>
              <RepeatIntervalPicker
                value={pipelineDraft.full_ingest_cron ?? ""}
                onChange={(v) => patchDraft({ full_ingest_cron: v })}
              />
            </label>
            <label className="space-y-1 text-sm">
              <span className="text-[11px] text-muted-foreground">{t("hub.lblLightCron")}</span>
              <RepeatIntervalPicker
                value={pipelineDraft.light_ingest_cron ?? ""}
                onChange={(v) => patchDraft({ light_ingest_cron: v })}
              />
            </label>
            <label className="space-y-1 text-sm">
              <span className="text-[11px] text-muted-foreground">{t("hub.lblDrainCron")}</span>
              <RepeatIntervalPicker
                value={pipelineDraft.entity_drain_cron ?? ""}
                onChange={(v) => patchDraft({ entity_drain_cron: v })}
              />
            </label>
            <label className="space-y-1 text-sm">
              <span className="text-[11px] text-muted-foreground">
                {t("hub.lblMaintainerCron")}
              </span>
              <RepeatIntervalPicker
                value={pipelineDraft.entity_maintenance_cron ?? ""}
                onChange={(v) => patchDraft({ entity_maintenance_cron: v })}
              />
            </label>
            <label className="space-y-1 text-sm">
              <span className="text-[11px] text-muted-foreground">{t("hub.lblContinuousCron")}</span>
              <RepeatIntervalPicker
                value={pipelineDraft.entity_drain_continuous_cron ?? ""}
                onChange={(v) => patchDraft({ entity_drain_continuous_cron: v })}
              />
            </label>
            <label className="space-y-1 text-sm">
              <span className="text-[11px] text-muted-foreground">{t("hub.lblBatchSize")}</span>
              <input
                type="number"
                min={20}
                max={500}
                value={pipelineDraft.entity_batch_size ?? 200}
                onChange={(e) => patchDraft({ entity_batch_size: numberOr(e.target.value, 200, (v) => parseInt(v, 10)) })}
                className="w-full rounded-md border bg-background px-2 py-1.5 font-mono text-[12px]"
              />
            </label>
            <label className="space-y-1 text-sm">
              <span className="text-[11px] text-muted-foreground">{t("hub.lblClusterThreshold")}</span>
              <input
                type="number"
                min={0.5}
                max={0.99}
                step={0.01}
                value={pipelineDraft.cluster_threshold ?? 0.8}
                onChange={(e) => patchDraft({ cluster_threshold: numberOr(e.target.value, 0.8, parseFloat) })}
                className="w-full rounded-md border bg-background px-2 py-1.5 font-mono text-[12px]"
              />
            </label>
            <label className="space-y-1 text-sm">
              <span className="text-[11px] text-muted-foreground">{t("hub.lblFullSources")}</span>
              <input
                type="text"
                value={pipelineDraft.full_ingest_sources ?? "all"}
                onChange={(e) => patchDraft({ full_ingest_sources: e.target.value })}
                className="w-full rounded-md border bg-background px-2 py-1.5 font-mono text-[12px]"
              />
            </label>
            <label className="space-y-1 text-sm">
              <span className="text-[11px] text-muted-foreground">{t("hub.lblLightSources")}</span>
              <input
                type="text"
                value={pipelineDraft.light_ingest_sources ?? "rss,watcher"}
                onChange={(e) => patchDraft({ light_ingest_sources: e.target.value })}
                className="w-full rounded-md border bg-background px-2 py-1.5 font-mono text-[12px]"
              />
            </label>
            <label className="flex items-center gap-2 self-end text-sm">
              <input
                type="checkbox"
                checked={pipelineDraft.relevance_gate_enabled ?? true}
                onChange={(e) => patchDraft({ relevance_gate_enabled: e.target.checked })}
                className="rounded border-border"
              />
              {t("hub.lblRelevanceGate")}
            </label>
            <label className="space-y-1 text-sm">
              <span className="text-[11px] text-muted-foreground">{t("hub.lblMinRelevance")}</span>
              <input
                type="number"
                min={0}
                max={1}
                step={0.05}
                value={pipelineDraft.relevance_min_confidence ?? 0.55}
                onChange={(e) =>
                  patchDraft({ relevance_min_confidence: numberOr(e.target.value, 0.55, parseFloat) })
                }
                className="w-full rounded-md border bg-background px-2 py-1.5 font-mono text-[12px]"
              />
            </label>
            <label className="flex items-center gap-2 self-end text-sm md:col-span-2">
              <input
                type="checkbox"
                checked={pipelineDraft.wiki_search_enabled ?? true}
                onChange={(e) => patchDraft({ wiki_search_enabled: e.target.checked })}
                className="rounded border-border"
              />
              {t("hub.lblWikiDedup")}
            </label>
            <label className="space-y-1 text-sm">
              <span className="text-[11px] text-muted-foreground">{t("hub.lblWikiTopK")}</span>
              <input
                type="number"
                min={1}
                max={20}
                value={pipelineDraft.wiki_search_top_k ?? 5}
                onChange={(e) => patchDraft({ wiki_search_top_k: numberOr(e.target.value, 5, (v) => parseInt(v, 10)) })}
                disabled={!(pipelineDraft.wiki_search_enabled ?? true)}
                className="w-full rounded-md border bg-background px-2 py-1.5 font-mono text-[12px] disabled:opacity-50"
              />
            </label>
            <label className="space-y-1 text-sm">
              <span className="text-[11px] text-muted-foreground">{t("hub.lblWikiQueries")}</span>
              <input
                type="number"
                min={1}
                max={500}
                value={pipelineDraft.wiki_search_max_per_pass ?? 150}
                onChange={(e) =>
                  patchDraft({ wiki_search_max_per_pass: numberOr(e.target.value, 150, (v) => parseInt(v, 10)) })
                }
                disabled={!(pipelineDraft.wiki_search_enabled ?? true)}
                className="w-full rounded-md border bg-background px-2 py-1.5 font-mono text-[12px] disabled:opacity-50"
              />
            </label>
            <label className="space-y-1 text-sm">
              <span className="text-[11px] text-muted-foreground">{t("hub.lblWikiMinScore")}</span>
              <input
                type="number"
                min={0.5}
                max={0.99}
                step={0.01}
                value={pipelineDraft.wiki_search_min_score ?? 0.75}
                onChange={(e) =>
                  patchDraft({ wiki_search_min_score: numberOr(e.target.value, 0.75, parseFloat) })
                }
                disabled={!(pipelineDraft.wiki_search_enabled ?? true)}
                className="w-full rounded-md border bg-background px-2 py-1.5 font-mono text-[12px] disabled:opacity-50"
              />
            </label>
            </div>
          </details>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => void runIngest("full")}
              className="rounded-md border px-3 py-1.5 text-[12px] hover:bg-muted/50 disabled:opacity-50"
            >
              {busy === "ingest-full" ? t("hub.runningFull") : t("hub.btnRunFull")}
            </button>
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => void runIngest("light")}
              className="rounded-md border px-3 py-1.5 text-[12px] hover:bg-muted/50 disabled:opacity-50"
            >
              {busy === "ingest-light" ? t("hub.runningLight") : t("hub.btnRunLight")}
            </button>
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => void runMaintenance()}
              className="rounded-md border px-3 py-1.5 text-[12px] hover:bg-muted/50 disabled:opacity-50"
            >
              {busy === "maintenance" ? t("hub.runningMaintainer") : t("hub.btnRunMaintainer")}
            </button>
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => void savePipelineConfig()}
              className="rounded-md bg-primary px-3 py-1.5 text-[12px] text-primary-foreground hover:opacity-90 disabled:opacity-50"
            >
              {busy === "save-pipeline" ? t("hub.saving") : t("hub.btnSaveSchedule")}
            </button>
            {pipelineConfig?.config_path ? (
              <span className="text-[10px] text-muted-foreground">{t("hub.configPath", { path: pipelineConfig.config_path })}</span>
            ) : null}
          </div>
        </StatCard>
      ) : null}

      <StatCard title={t("hub.newsAndReferences")} className="col-span-full">
        <div className="mb-3 inline-flex rounded-lg border p-0.5 text-[11px]">
          {(["list", "pipeline", "events"] as const).map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => setNewsView(key)}
              className={cn(
                "rounded-md px-2.5 py-1 capitalize",
                newsView === key ? "bg-muted font-medium" : "text-muted-foreground",
              )}
            >
              {key === "pipeline" ? t("hub.viewPipeline") : key === "events" ? t("hub.viewEvents") : t("hub.viewList")}
              {/* Sourced from hub.news_pipeline (already on the wire from /trade/hub/status),
                  not a second request -- the point is to show there is something to review
                  without having to open the tab first. */}
              {key === "pipeline" && registryGapPending > 0 ? (
                <span className="ml-1.5 rounded-full bg-amber-500/20 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800 dark:text-amber-200">
                  {registryGapPending}
                </span>
              ) : null}
            </button>
          ))}
        </div>

        {newsView === "list" ? (
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <div className="relative min-w-[220px] flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <input
                type="text"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder={t("hub.searchPlaceholder")}
                className="w-full rounded-full border bg-background py-1.5 pl-9 pr-3 text-[12px] outline-none focus:ring-1 focus:ring-ring"
              />
            </div>
            <div className="inline-flex rounded-lg border p-0.5 text-[11px]">
              {(["ranked", "all"] as const).map((key) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setNewsSection(key)}
                  className={cn(
                    "rounded-md px-2.5 py-1",
                    newsSection === key ? "bg-muted font-medium" : "text-muted-foreground",
                  )}
                >
                  {key === "ranked" ? t("hub.sectionRanked") : t("hub.sectionAll", { hours: ALL_NEWS_WINDOW_HOURS })}
                </button>
              ))}
            </div>
          </div>
        ) : null}

        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Newspaper className="h-4 w-4 text-muted-foreground" />
            <span className="font-semibold tabular-nums text-amber-700 dark:text-amber-300">
              {t("hub.pendingInQueue", { n: newsInventory?.pending_count ?? staging?.queued ?? "—" })}
            </span>
            <span className="text-muted-foreground">·</span>
            <span className="tabular-nums">
              {t("hub.stagingLive", { n: newsInventory?.staging_in_union ?? "—" })}
            </span>
            <span className="text-muted-foreground">·</span>
            <span className="tabular-nums">
              {t("hub.distilledN", { n: newsInventory?.distilled_in_union ?? "—" })}
            </span>
            <span className="text-muted-foreground">·</span>
            <span className="tabular-nums text-red-700 dark:text-red-300">
              {t("hub.discardedN", { n: newsInventory?.discarded_count ?? "—" })}
            </span>
          </div>
          {newsView === "list" ? (
            <div className="flex flex-wrap items-center gap-2">
              <div className="inline-flex rounded-lg border p-0.5 text-[11px]">
                {(["all", "staging", "distilled", "discarded"] as const).map((key) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setNewsFilter(key)}
                    className={cn(
                      "rounded-md px-2 py-1 capitalize",
                      newsFilter === key ? "bg-muted font-medium" : "text-muted-foreground",
                    )}
                  >
                    {t(NEWS_FILTER_LABEL_KEYS[key])}
                  </button>
                ))}
              </div>
              <label className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <input
                  type="checkbox"
                  checked={showQueue}
                  onChange={(e) => setShowQueue(e.target.checked)}
                  className="rounded border-border"
                />
                {t("hub.rawQueueOnly")}
              </label>
              <button
                type="button"
                disabled={busy !== null}
                onClick={() => void drainStaging()}
                className="rounded-md border px-2 py-1 text-[11px] hover:bg-muted/50 disabled:opacity-50"
              >
                {busy === "drain" ? t("hub.draining") : t("hub.drainStaging")}
              </button>
            </div>
          ) : null}
        </div>

        {newsView === "pipeline" ? (
          <div className="space-y-3">
            <NewsPipelineStatusStrip status={hub?.news_pipeline} />
            <NewsPipelineGraph entityId="NIFTY" />
            {/* Sits under the pipeline graph because it is pipeline output: the queue is
                refreshed by process_staging_batch, not by this page. */}
            <FactorRegistryGaps />
          </div>
        ) : newsView === "events" ? (
          <EventsCalendar />
        ) : !filteredNews.length ? (
          <p className="rounded-lg border border-dashed bg-muted/20 px-4 py-8 text-center text-sm text-muted-foreground">
            {newsFilter === "discarded" ? t("hub.emptyDiscarded") : t("hub.emptyNews")}
          </p>
        ) : newsFilter === "discarded" ? (
          <div className="grid gap-2 md:grid-cols-2">
            {(filteredNews as HubDiscardedNewsItem[]).slice(0, 40).map((item) => (
              <DiscardedRow
                key={item.discard_id || item.id || item.title}
                item={item}
                onUndo={(row) => void undoDiscard(row)}
                busy={busy !== null}
              />
            ))}
          </div>
        ) : newsSection === "all" ? (
          <div className="grid gap-2 md:grid-cols-2">
            {(filteredNews as HubNewsItem[]).map((item) => (
              <NewsRow
                key={item.id || item.ref_id || item.title}
                item={item}
                onDiscard={(row) => void discardNews(row, false)}
                onDiscardSimilar={(row) => void discardNews(row, true)}
                discarding={busy !== null}
              />
            ))}
          </div>
        ) : (
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {(filteredNews as HubNewsItem[]).map((item) => {
              const key = item.id || item.ref_id || item.event_id || item.title || "";
              return (
                <RankedNewsCard
                  key={key}
                  item={item}
                  expanded={Boolean(expandedGroups[key])}
                  onToggleExpand={() => toggleGroup(key)}
                  onDiscard={(row) => void discardNews(row, false)}
                  onDiscardSimilar={(row) => void discardNews(row, true)}
                  discarding={busy !== null}
                />
              );
            })}
          </div>
        )}

        {newsView === "list" && filteredNews.length ? (
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground">
            <span className="tabular-nums">
              {t("hub.rangeOf", { start: newsRangeStart, end: newsRangeEnd, total: totalNewsCount })}
            </span>
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                disabled={newsPage <= 1}
                onClick={() => setNewsPage((prev) => Math.max(1, prev - 1))}
                className="rounded-md border px-2 py-1 hover:bg-muted/50 disabled:opacity-40"
              >
                {t("hub.previous")}
              </button>
              <span className="tabular-nums">{t("hub.pageN", { n: newsPage })}</span>
              <button
                type="button"
                disabled={!hasMoreNews}
                onClick={() => setNewsPage((prev) => prev + 1)}
                className="rounded-md border px-2 py-1 hover:bg-muted/50 disabled:opacity-40"
              >
                {t("hub.next")}
              </button>
            </div>
          </div>
        ) : null}
      </StatCard>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <StatCard title={t("hub.cardStagingQueue")}>
          <div className="space-y-1 text-sm">
            <p>
              <span className="text-muted-foreground">{t("hub.queued")}</span>{" "}
              <span className="font-semibold tabular-nums">{staging?.queued ?? "—"}</span>
            </p>
            <p className="text-[11px] text-muted-foreground">
              {t("hub.entityPipeline", {
                state:
                  staging?.entity_pipeline_enabled === undefined
                    ? t("hub.unknown")
                    : staging.entity_pipeline_enabled
                      ? t("hub.on")
                      : t("hub.off"),
              })}
            </p>
            {staging?.worker_last ? (
              <p className="text-[11px] text-muted-foreground">
                {t("hub.lastWorker", { processed: staging.worker_last.processed ?? "—" })}
                {(staging.worker_last.errors ?? 0) > 0 ? t("hub.workerErrors", { n: staging.worker_last.errors }) : ""}
              </p>
            ) : null}
          </div>
          {(staging?.by_ticker?.length ?? 0) > 0 ? (
            <ul className="mt-3 space-y-1 text-[11px]">
              {staging?.by_ticker?.slice(0, 8).map((row) => (
                <li key={row.ticker} className="flex justify-between gap-2">
                  <span>{row.ticker}</span>
                  <span className="tabular-nums text-muted-foreground">{row.queued}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </StatCard>

        <StatCard title={t("hub.cardVerified")}>
          {verified ? (
            <ul className="space-y-2 text-sm">
              {Object.entries(verified).map(([ticker, row]) => (
                <li key={ticker}>
                  <span className="font-medium">{ticker}</span>
                  <span className="ml-2 tabular-nums text-muted-foreground">{t("hub.recordsN", { n: row.total })}</span>
                  {row.by_status ? (
                    <p className="text-[11px] text-muted-foreground">
                      {Object.entries(row.by_status)
                        .map(([k, v]) => `${k}: ${String(v)}`)
                        .join(" · ")}
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">{t("hub.noVerified")}</p>
          )}
        </StatCard>

        <StatCard title={t("hub.cardIndexResearch")}>
          {indexResearch?.present ? (
            <div className="space-y-1 text-sm">
              <p>
                <span className="text-muted-foreground">{t("hub.asOf")}</span>{" "}
                {indexResearch.as_of ? formatIst(indexResearch.as_of) : "—"}
              </p>
              <p>
                <span className="text-muted-foreground">{t("hub.horizon")}</span>{" "}
                {t("hub.horizonValue", { days: indexResearch.horizon?.days ?? "—", name: indexResearch.horizon?.name ?? "?" })}
              </p>
              <p className="text-[11px] text-muted-foreground">
                {t("hub.lastStage", { stage: indexResearch.last_pipeline_stage ?? "—" })}
                {indexResearch.last_pipeline_message ? ` — ${indexResearch.last_pipeline_message}` : ""}
              </p>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">{t("hub.noIndexSnapshot")}</p>
          )}
        </StatCard>

        <StatCard title={t("hub.cardConstituentCache")}>
          {constituentCache ? (
            <div className="grid grid-cols-2 gap-2 text-sm">
              <div>
                <p className="text-[11px] text-muted-foreground">{t("hub.fresh")}</p>
                <p className="text-lg font-semibold tabular-nums text-emerald-600 dark:text-emerald-400">
                  {constituentCache.fresh}
                </p>
              </div>
              <div>
                <p className="text-[11px] text-muted-foreground">{t("hub.stale")}</p>
                <p className="text-lg font-semibold tabular-nums text-amber-600 dark:text-amber-400">
                  {constituentCache.stale}
                </p>
              </div>
              <div>
                <p className="text-[11px] text-muted-foreground">{t("hub.missing")}</p>
                <p className="text-lg font-semibold tabular-nums">{constituentCache.missing}</p>
              </div>
              <div>
                <p className="text-[11px] text-muted-foreground">{t("hub.total")}</p>
                <p className="text-lg font-semibold tabular-nums">{constituentCache.total}</p>
              </div>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">{t("hub.notReported")}</p>
          )}
        </StatCard>

        <StatCard title={t("hub.cardCaptureRegistry")}>
          <div className="flex items-start gap-2">
            <Database className="mt-0.5 h-4 w-4 text-muted-foreground" aria-hidden />
            <div className="space-y-1 text-sm">
              <p>
                <span className="text-muted-foreground">{t("hub.enabled")}</span>{" "}
                {capture?.stats?.capture_enabled === undefined ? t("hub.unknown") : capture.stats.capture_enabled ? t("hub.yes") : t("hub.no")}
              </p>
              {capture?.stats?.channel ? (
                <p className="text-[11px] text-muted-foreground">
                  {t("hub.hubHits", {
                    hits: capture.stats.channel.hub_hits ?? "—",
                    fetches: capture.stats.channel.vendor_fetches ?? "—",
                  })}
                </p>
              ) : null}
            </div>
          </div>
        </StatCard>

        <StatCard title={t("hub.cardFactorCoverage")}>
          {factorCoverage ? (
            <div className="space-y-1 text-sm">
              <p>
                <span className="text-muted-foreground">{t("hub.gateMin")}</span>{" "}
                <span className="font-semibold tabular-nums">{factorCoverage.min_pct == null ? "—" : `${factorCoverage.min_pct}%`}</span>
                {" · "}
                {factorCoverage.passes_gate ? t("hub.pass") : t("hub.fail")}
              </p>
              <p className="text-[11px] text-muted-foreground">
                {t("hub.tradingDays", { n: factorCoverage.trading_days ?? "—" })}
                {factorCoverage.start && factorCoverage.end
                  ? ` · ${factorCoverage.start} → ${factorCoverage.end}`
                  : ""}
              </p>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">{t("hub.notReported")}</p>
          )}
        </StatCard>

        <StatCard title={t("hub.cardSourceHealth")}>
          {(sourceAvailability?.length ?? 0) > 0 ? (
            <ul className="max-h-40 space-y-1 overflow-y-auto text-[11px]">
              {sourceAvailability?.slice(0, 12).map((row) => (
                <li key={`${row.vendor}:${row.capability}`} className="flex justify-between gap-2">
                  <span className="truncate">
                    {row.vendor}
                    {row.capability ? ` · ${row.capability}` : ""}
                  </span>
                  <span
                    className={cn(
                      "shrink-0 capitalize tabular-nums",
                      row.status === "available"
                        ? "text-emerald-600 dark:text-emerald-400"
                        : row.status === "rate_limited"
                          ? "text-amber-600 dark:text-amber-400"
                          : "text-red-600 dark:text-red-400",
                    )}
                  >
                    {row.status ?? t("hub.unknown")}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">{t("hub.noSourceData")}</p>
          )}
        </StatCard>

        <StatCard title={t("hub.cardLastMaintainer")}>
          {newsMaintainer?.present ? (
            <div className="space-y-1 text-sm">
              <p>
                <span className="text-muted-foreground">{t("hub.statusLabel")}</span>{" "}
                {newsMaintainer.had_errors ? (
                  <span className="text-amber-700 dark:text-amber-300">{t("hub.statusErrors")}</span>
                ) : (
                  <span className="text-emerald-700 dark:text-emerald-300">{t("hub.statusOk")}</span>
                )}
              </p>
              <p className="text-[11px] text-muted-foreground">
                {t("hub.maintainerMode", { mode: newsMaintainer.mode ?? t("hub.maintainerModeDefault"), n: newsMaintainer.stages?.length ?? 0 })}
              </p>
              <p className="text-[11px] text-muted-foreground">
                {newsMaintainer.finished_at
                  ? t("hub.finishedAt", { when: formatIst(newsMaintainer.finished_at) })
                  : t("hub.noFinishTime")}
              </p>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">{t("hub.noMaintenance")}</p>
          )}
        </StatCard>
      </div>

      {hub?.paths ? (
        <StatCard title={t("hub.cardHubPaths")}>
          <ul className="space-y-1 font-mono text-[11px] text-muted-foreground">
            {Object.entries(hub.paths).map(([key, path]) => (
              <li key={key}>
                <span className="text-foreground">{key}</span>: {path}
              </li>
            ))}
          </ul>
        </StatCard>
      ) : null}
    </div>
  );
}

export default Hub;
