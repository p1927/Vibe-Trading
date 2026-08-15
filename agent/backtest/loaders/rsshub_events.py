"""Hub-backed event/sentiment provider with point-in-time safeguards.

A news / announcement / sentiment provider that runs parallel to the Tushare
fundamental layer (:mod:`backtest.loaders.tushare_fundamentals`). Per the Hub
boundary contract (``docs/news-hub-bridge.md``: "Anyone who needs news ... must
use [news_hub_bridge]"), this module no longer talks to a self-hosted RSSHub
instance directly — it reads normalised, deduped, fact-checked headlines back
from ``trade_integrations.dataflows.news_hub_bridge.query_with_staging``,
normalises each item into the ``event-driven`` skill schema (``ts_code,
knowable_date, event_type, score, source, summary``), and attaches a
point-in-time-safe ``event_score`` column to daily price frames.

Point-in-time discipline (mirrors the fundamentals enricher): every item is
stamped with a *knowable date* — the date a backtest could first have acted on
it (items published after the close roll to the next session) — and only items
with ``knowable_date <= as_of`` are ever returned. No feed item can leak into a
bar dated before it became knowable.

Scoring is pluggable. A deterministic, dependency-free lexicon scorer is used by
default (the Hub does not expose a pre-scored sentiment field); callers may pass
any ``scorer(title, summary) -> float`` (e.g. an LLM judge, as the
``event-driven`` skill describes) to override it.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_TRADE_ROOT = Path(__file__).resolve().parents[4]
_INTEGRATIONS = _TRADE_ROOT / "integrations"
if _INTEGRATIONS.is_dir() and str(_INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(_INTEGRATIONS))

# Hub query is bounded by a generous limit rather than an exact count: unlike
# the old per-feed RSSHub fetch (one HTTP call per feed x code), a single Hub
# query already returns verified + staged items across a `since..until` window,
# and callers care about "everything knowable by as_of", not a page size. 500
# comfortably covers any realistic per-code lookback without being unbounded.
_HUB_QUERY_LIMIT = 500

#: Hour (local to the feed timestamps) at/after which a publication is treated as
#: knowable only on the next calendar day, matching the event-driven skill's
#: "released after market close -> use the next trading day" rule.
DEFAULT_CLOSE_CUTOFF_HOUR = 16

#: Canonical column order for the tidy event frame.
EVENT_COLUMNS: tuple[str, ...] = (
    "ts_code",
    "knowable_date",
    "event_type",
    "score",
    "source",
    "summary",
)

ScorerFn = Callable[[str, str], float]


class EventProviderError(Exception):
    """Base error for event-provider failures."""


class UnknownFeedError(EventProviderError):
    """Raised when a requested feed name is not registered."""


#: Supported ``code_style`` values for per-symbol routes.
CODE_STYLES: frozenset[str] = frozenset({"raw", "exchange_prefix", "bare"})


def format_code_for_route(code: str, style: str) -> str:
    """Convert a backtest code (e.g. ``"600519.SH"``) to a route's expected form.

    Retained from the RSSHub-route era: feed configs still declare a
    ``code_style`` for the per-symbol shape they were written against, even
    though the Hub path itself no longer builds route URLs.

    Args:
        code: Dotted instrument code (``"600519.SH"``); passed through if it has
            no ``.`` suffix.
        style: One of :data:`CODE_STYLES` — ``"raw"`` (unchanged), ``"bare"``
            (symbol only, ``"600519"``), or ``"exchange_prefix"`` (``"SH600519"``).

    Returns:
        The code formatted for the route.

    Raises:
        ValueError: If ``style`` is not a recognised code style.
    """
    if style not in CODE_STYLES:
        raise ValueError(f"Unknown code_style {style!r}; expected one of {sorted(CODE_STYLES)}")
    if style == "raw":
        return code
    symbol, _, suffix = code.partition(".")
    if style == "bare":
        return symbol
    return f"{suffix.upper()}{symbol}" if suffix else symbol


@dataclass(frozen=True)
class FeedSpec:
    """Machine-readable metadata for one configured event feed.

    Attributes:
        name: Stable feed identifier used in configs (e.g. ``"sina_announcements"``).
        route_template: Historical RSSHub route template (kept for config
            back-compat / documentation only — the Hub path no longer builds
            URLs from it).
        event_type: Event class emitted for items from this feed; one of the
            event-driven taxonomy (``earnings``/``macro``/``policy``/
            ``sentiment``/``insider``/``technical_break``). Used as the
            fallback ``event_type`` when the Hub item carries no usable
            ``event_kind``.
        code_style: Historical per-symbol code shape (kept for back-compat);
            one of :data:`CODE_STYLES`.
    """

    name: str
    route_template: str
    event_type: str
    code_style: str = "raw"

    def __post_init__(self) -> None:
        if self.code_style not in CODE_STYLES:
            raise ValueError(
                f"FeedSpec {self.name!r}: unknown code_style {self.code_style!r}; "
                f"expected one of {sorted(CODE_STYLES)}"
            )

    @property
    def is_per_symbol(self) -> bool:
        """Whether the route is parameterised by instrument code."""
        return "{code}" in self.route_template


def feed_specs_from_config(entries: Iterable[Mapping[str, Any]]) -> list[FeedSpec]:
    """Build :class:`FeedSpec` objects from backtest-config dicts.

    Each entry must carry ``name``, ``route_template`` and ``event_type``; an
    optional ``code_style`` defaults to ``"raw"``. There is intentionally no
    built-in feed catalogue — feeds are always declared explicitly in config
    (or passed to the provider constructor).

    Args:
        entries: Iterable of mapping objects, one per feed.

    Returns:
        Parsed feed specs.

    Raises:
        ValueError: If an entry is missing a required key or has a duplicate name.
    """
    specs: list[FeedSpec] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("each event feed must be a mapping with name/route_template/event_type")
        missing = [k for k in ("name", "route_template", "event_type") if not str(entry.get(k, "")).strip()]
        if missing:
            raise ValueError(f"event feed is missing required field(s): {', '.join(missing)}")
        name = str(entry["name"]).strip()
        if name in seen:
            raise ValueError(f"duplicate event feed name: {name!r}")
        seen.add(name)
        specs.append(
            FeedSpec(
                name=name,
                route_template=str(entry["route_template"]).strip(),
                event_type=str(entry["event_type"]).strip(),
                code_style=str(entry.get("code_style", "raw")).strip() or "raw",
            )
        )
    return specs


#: No built-in feed catalogue: feeds are always supplied explicitly (via config
#: ``event_feeds`` or the provider constructor).
DEFAULT_FEEDS: tuple[FeedSpec, ...] = ()

# ── Default dependency-free lexicon scorer ───────────────────────────────────

_POSITIVE_TERMS: frozenset[str] = frozenset(
    {
        "beat",
        "beats",
        "surge",
        "surges",
        "soar",
        "record",
        "growth",
        "profit",
        "upgrade",
        "upgraded",
        "outperform",
        "bullish",
        "approval",
        "approved",
        "win",
        "wins",
        "buyback",
        "dividend",
        "expansion",
        "breakthrough",
    }
)
_NEGATIVE_TERMS: frozenset[str] = frozenset(
    {
        "miss",
        "misses",
        "plunge",
        "plunges",
        "fall",
        "falls",
        "loss",
        "losses",
        "downgrade",
        "downgraded",
        "underperform",
        "bearish",
        "fraud",
        "probe",
        "lawsuit",
        "recall",
        "default",
        "bankruptcy",
        "halt",
        "warning",
        "decline",
    }
)


def default_lexicon_scorer(title: str, summary: str) -> float:
    """Score text in ``[-1, 1]`` by net positive/negative term frequency.

    A deterministic, dependency-free baseline. It is intentionally simple — the
    provider is the data layer; richer scoring (e.g. an LLM judge) is plugged in
    via the ``scorer`` argument on :meth:`RSSHubEventProvider.query_events`.

    Args:
        title: Item headline.
        summary: Item summary/description (may be empty).

    Returns:
        Sentiment score in ``[-1.0, 1.0]``; ``0.0`` when no terms match.
    """
    tokens = f"{title} {summary}".lower().replace("/", " ").split()
    if not tokens:
        return 0.0
    pos = sum(1 for tok in tokens if tok.strip(".,;:!?\"'()") in _POSITIVE_TERMS)
    neg = sum(1 for tok in tokens if tok.strip(".,;:!?\"'()") in _NEGATIVE_TERMS)
    hits = pos + neg
    if hits == 0:
        return 0.0
    return max(-1.0, min(1.0, (pos - neg) / hits))


def _knowable_date(published: pd.Timestamp, cutoff_hour: int) -> pd.Timestamp:
    """Return the date an item becomes actionable (post-cutoff rolls to next day).

    Args:
        published: Parsed publication timestamp (tz dropped to naive local).
        cutoff_hour: Hour at/after which publication rolls to the next day.

    Returns:
        Normalised (midnight) timestamp of the knowable date.
    """
    stamp = published.tz_localize(None) if published.tzinfo is not None else published
    base = stamp.normalize()
    if stamp.hour >= cutoff_hour:
        base = base + pd.Timedelta(days=1)
    return base


def _clean_summary(text: str | None) -> str:
    """Collapse whitespace and strip commas so the value is CSV-safe."""
    if not text:
        return ""
    return " ".join(text.replace(",", " ").split())


# Taxonomy values event_type/event_kind is expected to take. Anything outside
# this set from the Hub is treated as "not a usable taxonomy value" and the
# feed's configured event_type is used as a fallback instead.
_EVENT_TAXONOMY: frozenset[str] = frozenset(
    {"earnings", "macro", "policy", "sentiment", "insider", "technical_break"}
)


class RSSHubEventProvider:
    """Point-in-time-safe event/sentiment provider backed by the news Hub.

    Attributes:
        feeds: Registered feed specs keyed by name.
        close_cutoff_hour: Hour after which items roll to the next knowable day.
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        feeds: Iterable[FeedSpec] | None = None,
        client: Any | None = None,
        close_cutoff_hour: int = DEFAULT_CLOSE_CUTOFF_HOUR,
    ) -> None:
        """Initialise the provider.

        Args:
            base_url: Vestigial — retained only so existing callers that pass
                a (now-unused) RSSHub base URL don't break; the Hub path makes
                no HTTP calls of its own.
            feeds: Feed specs to register; defaults to :data:`DEFAULT_FEEDS`.
            client: Vestigial — retained for signature back-compat; unused.
            close_cutoff_hour: Knowable-date roll cutoff (0-23).
        """
        specs = tuple(feeds) if feeds is not None else DEFAULT_FEEDS
        self.feeds: dict[str, FeedSpec] = {spec.name: spec for spec in specs}
        self.close_cutoff_hour = close_cutoff_hour
        self.base_url = base_url
        self._client = client

    def is_available(self) -> bool:
        """Whether the Hub (``trade_integrations``) can be located/imported."""
        if (_INTEGRATIONS / "trade_integrations").is_dir():
            return True
        try:
            import trade_integrations  # noqa: F401
        except ImportError:
            return False
        return True

    def list_feeds(self) -> list[str]:
        """Return registered feed names in stable order."""
        return sorted(self.feeds)

    def describe_feed(self, feed: str) -> FeedSpec:
        """Return the spec for a registered feed.

        Raises:
            UnknownFeedError: If ``feed`` is not registered.
        """
        try:
            return self.feeds[feed]
        except KeyError as exc:
            raise UnknownFeedError(f"Unknown RSSHub feed: {feed}") from exc

    def query_events(
        self,
        codes: Iterable[str],
        *,
        as_of: str | pd.Timestamp,
        feeds: Iterable[str] | None = None,
        scorer: ScorerFn | None = None,
    ) -> pd.DataFrame:
        """Fetch and normalise Hub headlines, dropping anything not yet knowable.

        Args:
            codes: Instrument codes to query.
            as_of: Point-in-time boundary; items with ``knowable_date > as_of``
                are excluded (no look-ahead).
            feeds: Feed names to query; defaults to all registered feeds. Only
                used to validate config and to supply a fallback
                ``event_type`` — the Hub path itself queries once per code,
                not once per feed.
            scorer: Optional ``scorer(title, summary) -> float`` override;
                defaults to :func:`default_lexicon_scorer`.

        Returns:
            Tidy frame with :data:`EVENT_COLUMNS`, sorted by
            ``(ts_code, knowable_date)`` and de-duplicated.
        """
        from trade_integrations.dataflows import news_hub_bridge as hub
        from trade_integrations.dataflows.news_hub_bridge import hub_ticker_for_symbol

        score_fn = scorer or default_lexicon_scorer
        feed_names = list(feeds) if feeds is not None else self.list_feeds()
        specs = [self.describe_feed(name) for name in feed_names]
        # A code can be queried against multiple feeds; the Hub has no hard
        # 1:1 feed->event_type mapping, so the first requested feed's
        # event_type is the fallback used for any item without a usable
        # event_kind. When no feeds are configured, fall back to "sentiment".
        fallback_event_type = specs[0].event_type if specs else "sentiment"

        as_of_date = pd.Timestamp(as_of).normalize()
        until = as_of_date.strftime("%Y-%m-%d")

        code_list = list(codes)
        rows: list[dict[str, Any]] = []
        attempted = 0
        failed = 0
        for code in code_list:
            attempted += 1
            market, ticker = hub_ticker_for_symbol(code)
            try:
                items = hub.query_with_staging(
                    ticker=ticker,
                    market=market,
                    until=until,
                    limit=_HUB_QUERY_LIMIT,
                )
            except Exception as exc:  # noqa: BLE001 - counted, surfaced below if all fail
                logger.warning("Hub query failed for %s (%s/%s): %s", code, market, ticker, exc)
                failed += 1
                continue
            for item in items:
                rows.extend(self._item_to_rows(item, code, fallback_event_type, score_fn))

        # A configured-but-fully-unreachable Hub must fail loudly rather than
        # silently scoring every bar 0.0 (which reads as "sentiment considered, no
        # signal"). A reachable query that simply returned no items is legitimate.
        if attempted and failed == attempted:
            raise EventProviderError(
                f"All {attempted} Hub event query(ies) failed for codes={code_list}; "
                f"verify the news Hub is reachable (feeds={feed_names})"
            )

        if not rows:
            return pd.DataFrame(columns=EVENT_COLUMNS)

        frame = pd.DataFrame(rows, columns=list(EVENT_COLUMNS))
        # Belt-and-suspenders: the Hub's `until` filters on published_at, which
        # is not the same as knowable_date once the close-cutoff-hour rollover
        # is applied, so this client-side filter must stay.
        frame = frame[frame["knowable_date"] <= as_of_date]
        frame = frame.drop_duplicates(subset=["ts_code", "knowable_date", "event_type", "summary"])
        frame = frame.sort_values(["ts_code", "knowable_date"]).reset_index(drop=True)
        return frame

    def _item_to_rows(
        self,
        item: Mapping[str, Any],
        code: str,
        fallback_event_type: str,
        score_fn: ScorerFn,
    ) -> list[dict[str, Any]]:
        """Map one Hub headline dict into zero or one :data:`EVENT_COLUMNS` row.

        Returns an empty list when the item carries no usable publish
        timestamp (nothing to compute ``knowable_date`` from).
        """
        title = str(item.get("title") or "")
        summary = _clean_summary(
            item.get("content_summary") or item.get("summary") or item.get("title") or ""
        )
        published_raw = item.get("published_at") or item.get("publish_day")
        if not published_raw:
            return []
        try:
            published = pd.Timestamp(published_raw)
        except (TypeError, ValueError):
            return []
        if pd.isna(published):
            return []

        event_kind = item.get("event_kind")
        event_type = (
            event_kind
            if isinstance(event_kind, str) and event_kind.strip() and event_kind.strip() in _EVENT_TAXONOMY
            else fallback_event_type
        )

        source_label = f"hub:{item.get('source')}" if item.get("source") else "hub"

        return [
            {
                "ts_code": code,
                "knowable_date": _knowable_date(published, self.close_cutoff_hour),
                "event_type": event_type,
                "score": float(score_fn(title, summary)),
                "source": source_label,
                "summary": summary or title,
            }
        ]


def enrich_price_frames_with_events(
    data_map: dict[str, pd.DataFrame],
    provider: RSSHubEventProvider,
    *,
    as_of: str | pd.Timestamp,
    feeds: Iterable[str] | None = None,
    decay_lambda: float = 0.1,
    lookback: int = 30,
    min_abs_score: float = 0.0,
    scorer: ScorerFn | None = None,
) -> dict[str, pd.DataFrame]:
    """Attach a point-in-time-safe ``event_score`` column to price frames.

    For each bar ``t`` the score aggregates only events whose ``knowable_date``
    falls in ``(t - lookback, t]``, exponentially decayed by age — the same
    formula as the ``event-driven`` skill — so no future item can influence an
    earlier bar. Two columns are added: ``event_score`` (decayed sum, clipped to
    ``[-1, 1]``) and ``event_count`` (events in the window).

    Args:
        data_map: Mapping ``{code: OHLCV DataFrame}`` (DatetimeIndex).
        provider: Configured :class:`RSSHubEventProvider`.
        as_of: Point-in-time boundary passed through to the provider.
        feeds: Feed names to query; defaults to all registered feeds.
        decay_lambda: Exponential decay per day (higher decays faster).
        lookback: Window in days; older events are excluded.
        min_abs_score: Drop events with ``|score|`` below this threshold.
        scorer: Optional scoring override forwarded to the provider.

    Returns:
        A new mapping with the same frames plus ``event_score``/``event_count``.
    """
    if not data_map:
        return data_map

    codes = list(data_map)
    events = provider.query_events(codes, as_of=as_of, feeds=feeds, scorer=scorer)
    enriched = {code: frame.copy() for code, frame in data_map.items()}
    if events.empty:
        for frame in enriched.values():
            frame["event_score"] = 0.0
            frame["event_count"] = 0
        return enriched

    if min_abs_score > 0.0:
        events = events[events["score"].abs() >= min_abs_score]

    for code, frame in enriched.items():
        rows = events[events["ts_code"] == code]
        bar_dates = pd.DatetimeIndex(pd.to_datetime(frame.index)).normalize()
        scores = pd.Series(0.0, index=frame.index)
        counts = pd.Series(0, index=frame.index)
        if not rows.empty and len(frame) > 0:
            ev_dates = rows["knowable_date"].to_numpy(dtype="datetime64[ns]")
            ev_scores = rows["score"].to_numpy(dtype=float)
            window = np.timedelta64(lookback, "D")
            for pos, bar in enumerate(bar_dates):
                bar64 = np.datetime64(bar, "ns")
                mask = (ev_dates <= bar64) & (ev_dates > bar64 - window)
                if not mask.any():
                    continue
                ages = (bar64 - ev_dates[mask]) / np.timedelta64(1, "D")
                decayed = float((ev_scores[mask] * np.exp(-decay_lambda * ages)).sum())
                scores.iloc[pos] = max(-1.0, min(1.0, decayed))
                counts.iloc[pos] = int(mask.sum())
        frame["event_score"] = scores
        frame["event_count"] = counts
    return enriched
