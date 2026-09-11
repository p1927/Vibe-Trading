"""Per-source consecutive-empty-run streaks for hub-news ingest jobs.

A named ingest source (``rss``, ``web_search_global``, ``marketaux`` ...) that fetches nothing on
every cycle used to look exactly like a quiet day: its stats read zero and the run reported
``completed``. That is how Moneycontrol's dead feeds went unnoticed for months
(.claude/backlog/archive/items/2026-09-07-moneycontrol-rss-dead-login-consent-silent-zero.md).

Trade's ``run_hub_news_ingest`` now reports ``sources_empty`` per run: the sources that ran and
fetched nothing, where an error counts as nothing. A quiet cycle still fetches (the feed returns
the same entries, the search returns results that dedup to zero queued), so it is never in that
list. This module counts, per job, how many consecutive runs each source has spent in that list,
in the ``job.config`` scratch key ``_source_zero_streaks``. That is the D11
``_recent_durations_ms`` pattern: the executor persists ``config`` with every run, and default-job
registration merges ``config``, so the counts survive restarts.

Once a source reaches ``STALE_AFTER_CONSECUTIVE_EMPTY_RUNS`` it is listed, with its count, in the
scratch key ``_sources_stale`` and in the run's result summary as ``sources_stale``.
``/scheduled-runs`` serves ``config``, and ``.claude/check_dev_ports.py`` reports the scratch key
as a warning. This is a signal only. It never skips, pauses or fails a source or a job: what to do
about a dead source stays a human decision, as it was for Moneycontrol.

One threshold serves every job. The count is of that job's own runs, so it already scales with
the job's cadence: three runs is 45 minutes for a 15-minute ``-tight`` job and three days for a
daily ``-full`` job. And because the metric is "fetched nothing", not "queued nothing", a quiet
cycle never adds to it. See
.claude/backlog/items/2026-09-08-ingest-per-source-consecutive-zero-health-signal.md.
"""

from __future__ import annotations

from typing import Any

from src.scheduled_research.models import ScheduledResearchJob

SOURCE_ZERO_STREAKS_CONFIG_KEY = "_source_zero_streaks"
SOURCES_STALE_CONFIG_KEY = "_sources_stale"
STALE_AFTER_CONSECUTIVE_EMPTY_RUNS = 3


def _prior_streaks(job: ScheduledResearchJob) -> dict[str, int]:
    raw = job.config.get(SOURCE_ZERO_STREAKS_CONFIG_KEY)
    if not isinstance(raw, dict):
        return {}
    return {
        str(name): count
        for name, count in raw.items()
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0
    }


def record_source_zero_streaks(
    job: ScheduledResearchJob, result: dict[str, Any] | None, *, summary_config_key: str
) -> dict[str, int]:
    """Update *job*'s per-source empty-run streaks from one ingest *result*; return the stale ones.

    Mutates ``job.config``. ``summary_config_key`` is the scratch key the run's result summary
    was just attached under, so ``sources_stale`` is added to that same summary.

    Leaves the streaks untouched when the result carries no ``sources_empty`` list, because then
    nothing was observed. That covers a run that errored or was gated before any source ran, and
    a Trade build that predates the field.
    """
    if not isinstance(result, dict):
        return {}
    empty_raw = result.get("sources_empty")
    sources = result.get("sources")
    if not isinstance(empty_raw, list) or not isinstance(sources, dict):
        return {}
    empty = {str(name) for name in empty_raw}
    prior = _prior_streaks(job)

    streaks: dict[str, int] = {}
    for raw_name, stats in sources.items():
        name = str(raw_name)
        if isinstance(stats, dict) and "skipped" in stats:
            # Not asked this run (e.g. the light-mode guard): no evidence either way.
            if name in prior:
                streaks[name] = prior[name]
            continue
        streaks[name] = prior.get(name, 0) + 1 if name in empty else 0
    # A source no longer in the job's results (dropped from its config) is forgotten.
    job.config[SOURCE_ZERO_STREAKS_CONFIG_KEY] = streaks

    stale = {
        name: count
        for name, count in sorted(streaks.items())
        if count >= STALE_AFTER_CONSECUTIVE_EMPTY_RUNS
    }
    if stale:
        job.config[SOURCES_STALE_CONFIG_KEY] = stale
        summary = job.config.get(summary_config_key)
        job.config[summary_config_key] = {
            **(summary if isinstance(summary, dict) else {}),
            "sources_stale": stale,
        }
    else:
        job.config.pop(SOURCES_STALE_CONFIG_KEY, None)
    return stale
