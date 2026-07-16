"""Scheduled auto paper trading jobs — Vibe agent autonomy (LiveRunner-style)."""

from __future__ import annotations

import asyncio
import logging
import sys
import time

from src.scheduled_research.models import JobStatus, ScheduledResearchJob, validate_schedule

logger = logging.getLogger(__name__)

JOB_TYPE_AUTO_PAPER_INTRADAY = "auto_paper_intraday_tick"
JOB_TYPE_AUTO_PAPER_AGENT = "auto_paper_agent_turn"
JOB_TYPE_SCHEDULER_HEALTH = "auto_paper_scheduler_health"
JOB_TYPE_SESSION_CLOSE_FLATTEN = "auto_paper_session_close_flatten"
AUTO_PAPER_JOB_TYPES = frozenset(
    {
        JOB_TYPE_AUTO_PAPER_INTRADAY,
        JOB_TYPE_AUTO_PAPER_AGENT,
        JOB_TYPE_SCHEDULER_HEALTH,
        JOB_TYPE_SESSION_CLOSE_FLATTEN,
    }
)

DEFAULT_AUTO_PAPER_POLL_CRON = "*/5 * * * *"
AUTO_PAPER_AGENT_JOB_ID = "auto-paper-agent-turn"
AUTO_PAPER_INTRADAY_JOB_ID = "auto-paper-intraday"
AUTO_PAPER_THESIS_BREAK_JOB_ID = "auto-paper-thesis-break"
AUTO_PAPER_SCHEDULER_HEALTH_JOB_ID = "auto-paper-scheduler-health"
AUTO_PAPER_SESSION_CLOSE_FLATTEN_JOB_ID = "auto-paper-session-close-flatten"
AUTO_PAPER_SCHEDULER_JOB_IDS = (
    AUTO_PAPER_AGENT_JOB_ID,
    AUTO_PAPER_INTRADAY_JOB_ID,
    AUTO_PAPER_THESIS_BREAK_JOB_ID,
    AUTO_PAPER_SCHEDULER_HEALTH_JOB_ID,
    AUTO_PAPER_SESSION_CLOSE_FLATTEN_JOB_ID,
)


def is_auto_paper_scheduler_enabled() -> bool:
    _ensure_trade_integrations_on_path()
    from trade_integrations.auto_paper.config import get_auto_paper_config
    from trade_integrations.auto_paper.session_store import load_session

    cfg = get_auto_paper_config()
    session = load_session()
    return bool(cfg.enable_scheduler or cfg.enabled or session.get("enabled"))


def _ensure_trade_integrations_on_path() -> None:
    from pathlib import Path

    trade_root = Path(__file__).resolve().parents[4]
    integrations = trade_root / "integrations"
    if integrations.is_dir() and str(integrations) not in sys.path:
        sys.path.insert(0, str(integrations))


def run_auto_paper_job_sync(config: dict | None = None) -> dict:
    _ensure_trade_integrations_on_path()
    from trade_integrations.auto_paper.config import is_auto_paper_active
    from trade_integrations.auto_paper.engine import run_auto_paper_tick
    from trade_integrations.auto_paper.session_store import load_session
    from trade_integrations.execution.enforce import is_bridge_autonomous_agent

    session = load_session()
    agent_id = str(session.get("autonomous_agent_id") or "").strip()
    if is_bridge_autonomous_agent(agent_id) or session.get("nautilus_bridge_mode"):
        return {"skipped": True, "reason": "nautilus_bridge_owns_execution"}

    if not is_auto_paper_active() and not is_auto_paper_scheduler_enabled():
        return {"skipped": True, "reason": "auto_paper_disabled"}

    dry_run = bool((config or {}).get("dry_run"))
    result = run_auto_paper_tick(dry_run=dry_run)
    logger.info("auto paper deterministic tick: %s", result.get("status"))
    return result


def _resolve_vibe_session(svc, ticker: str, job_config: dict):
    """Reuse persistent Vibe session for context continuity (LiveRunner pattern)."""
    _ensure_trade_integrations_on_path()
    from trade_integrations.auto_paper.audit import write_paper_action
    from trade_integrations.auto_paper.session_store import get_vibe_session_id, load_session, set_vibe_session_id
    from trade_integrations.auto_paper.vibe_research import paper_session_vibe_config

    existing_id = get_vibe_session_id()
    if existing_id:
        existing = svc.get_session(existing_id)
        if existing is not None:
            return existing

    session = load_session()
    watchlist = session.get("watchlist") or [ticker]
    session_cfg = paper_session_vibe_config(ticker=ticker, watchlist=list(watchlist))
    session_cfg.update(dict(job_config or {}))

    vibe_session = svc.create_session(
        title=f"auto-paper-trader:{ticker}",
        config=session_cfg,
    )
    set_vibe_session_id(vibe_session.session_id)
    write_paper_action(
        "turn_dispatched",
        detail={"vibe_session_id": vibe_session.session_id, "ticker": ticker, "new_session": True},
    )
    return vibe_session


async def dispatch_auto_paper_agent_turn(
    job: ScheduledResearchJob,
    *,
    prompt_override: str | None = None,
) -> None:
    """Dispatch one autonomous agent turn via PaperTradingAgentRunner."""
    _ensure_trade_integrations_on_path()
    from trade_integrations.auto_paper.agent_mandate import is_agent_session_active
    from trade_integrations.auto_paper.audit import write_paper_action
    from trade_integrations.auto_paper.config import get_auto_paper_config
    from trade_integrations.auto_paper.engine import is_market_session_open
    from trade_integrations.auto_paper.runner import PaperTradingAgentRunner, make_inprocess_agent_caller, resolve_runner
    from trade_integrations.auto_paper.session_store import load_session, save_session

    if not is_agent_session_active():
        logger.info("auto paper agent turn skipped: session inactive or halted")
        return

    session = load_session()
    agent_id = str(session.get("autonomous_agent_id") or "").strip()
    if agent_id or session.get("nautilus_bridge_mode"):
        _ensure_trade_integrations_on_path()
        from trade_integrations.execution.enforce import is_bridge_autonomous_agent

        if is_bridge_autonomous_agent(agent_id) or session.get("nautilus_bridge_mode"):
            logger.info("auto paper agent turn skipped: Nautilus bridge owns watch for %s", agent_id or "bridge session")
            return

    cfg = get_auto_paper_config()
    if not is_market_session_open(cfg):
        logger.info("auto paper agent turn skipped: outside market hours")
        return

    if prompt_override:
        host = sys.modules.get("api_server") or sys.modules.get("agent.api_server")
        if host is None:
            logger.warning("api_server not loaded — falling back to deterministic tick")
            await asyncio.to_thread(run_auto_paper_job_sync, job.config)
            return
        svc = host._get_session_service()
        if not svc:
            await asyncio.to_thread(run_auto_paper_job_sync, job.config)
            return
        session = load_session()
        ticker = str(session.get("primary_ticker") or (session.get("watchlist") or ["NIFTY"])[0])
        vibe_session = _resolve_vibe_session(svc, ticker, job.config or {})
        from src.trade.auto_paper_bootstrap import prepare_fresh_vibe_turn

        await prepare_fresh_vibe_turn(svc, vibe_session.session_id)
        await svc.send_message(vibe_session.session_id, prompt_override)
        return

    session = load_session()
    ticker = str(session.get("primary_ticker") or (session.get("watchlist") or ["NIFTY"])[0])

    caller = make_inprocess_agent_caller()
    runner = resolve_runner(vibe_url=None)
    if caller is not None:
        runner = PaperTradingAgentRunner(agent_caller=caller, fallback_deterministic=True)
        if not session.get("vibe_session_id"):
            host = sys.modules.get("api_server") or sys.modules.get("agent.api_server")
            if host is not None:
                svc = host._get_session_service()
                if svc is not None:
                    vibe_session = _resolve_vibe_session(svc, ticker, job.config or {})
                    session = load_session()
                    session["vibe_session_id"] = vibe_session.session_id
                    save_session(session)

    result = await runner.run_once()
    write_paper_action("scheduler_turn", detail=result.to_dict())
    logger.info("auto paper agent turn: %s (%s)", result.outcome, result.reason or "ok")

    if result.outcome == "reconcile_unsafe":
        session = load_session()
        session["halted"] = True
        session["halt_reason"] = result.reason
        save_session(session)


def run_scheduler_health_sync() -> dict:
    _ensure_trade_integrations_on_path()
    from trade_integrations.auto_paper.audit import write_paper_action
    from trade_integrations.auto_paper.config import get_auto_paper_config
    from trade_integrations.auto_paper.session_store import load_session

    session = load_session()
    if not session.get("enabled"):
        return {"status": "disabled"}

    cfg = get_auto_paper_config()
    last = session.get("last_agent_turn_at")
    stale = True
    if last:
        from datetime import datetime, timezone

        try:
            last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            age_min = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60.0
            stale_after = max(10.0, (cfg.poll_interval_ms or 300_000) / 60_000 * 2)
            stale = age_min > stale_after
        except ValueError:
            stale = True

    health = "stale" if stale else "ok"
    if stale and session.get("agent_mode", True):
        ensure_agent_job_registered()
        write_paper_action("scheduler_stale", detail={"last_agent_turn_at": last, "health": health})
    return {"status": health, "last_agent_turn_at": last}


def run_session_close_flatten_sync() -> dict:
    _ensure_trade_integrations_on_path()
    from trade_integrations.auto_paper.audit import write_paper_action
    from trade_integrations.auto_paper.mandate_config import mandate_config_from_session
    from trade_integrations.auto_paper.openalgo_client import OpenAlgoClient
    from trade_integrations.auto_paper.outcome_ledger import append_outcome
    from trade_integrations.auto_paper.session_store import load_session
    from trade_integrations.monitor.execution_ledger import list_open_entries

    session = load_session()
    if not session.get("enabled"):
        return {"skipped": True, "reason": "session_inactive"}

    mc = mandate_config_from_session(session)
    if not mc.needs_session_close_flatten():
        return {"skipped": True, "reason": "flatten_not_in_mandate"}

    open_entries = list_open_entries()
    if not open_entries:
        return {"skipped": True, "reason": "no_open_positions"}

    client = OpenAlgoClient()
    if not client.ensure_analyzer_mode():
        return {"status": "error", "reason": "analyzer_mode_failed"}

    result = client.close_all_positions(strategy="auto_paper_session_close")
    append_outcome(
        symbol=str(session.get("primary_ticker") or "NIFTY"),
        strategy=(session.get("lifecycle") or {}).get("active_strategy"),
        action="EXIT",
        intent_source="session_close_flatten",
        agent_id=session.get("autonomous_agent_id"),
        mandate_snapshot=mc.to_dict(),
    )
    write_paper_action("session_close_flatten", detail={"positions_closed": len(open_entries), "result": result})
    return {"status": "flattened", "positions": len(open_entries), "result": result}


def register_mandate_scheduler_jobs(store, mandate) -> int:
    """Register mandate-specific cron jobs (health + optional session-close flatten)."""
    from trade_integrations.auto_paper.mandate_config import scheduled_actions_for

    created = 0
    actions = scheduled_actions_for(mandate)
    now_ms = int(time.time() * 1000)

    if "scheduler_health" in actions:
        health_job = ScheduledResearchJob(
            id=AUTO_PAPER_SCHEDULER_HEALTH_JOB_ID,
            prompt="Auto paper scheduler health check",
            schedule="*/5 * * * *",
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={"job_type": JOB_TYPE_SCHEDULER_HEALTH},
        )
        store.upsert(health_job)
        created += 1

    if "session_close_flatten" in actions:
        flatten_job = ScheduledResearchJob(
            id=AUTO_PAPER_SESSION_CLOSE_FLATTEN_JOB_ID,
            prompt="Session close flatten (mandate-driven)",
            schedule="10 15 * * 1-5",
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={"job_type": JOB_TYPE_SESSION_CLOSE_FLATTEN},
        )
        store.upsert(flatten_job)
        created += 1

    if created:
        logger.info("registered %s mandate scheduler jobs", created)
    return created


async def dispatch_auto_paper_job(job: ScheduledResearchJob) -> None:
    job_type = str(job.config.get("job_type") or "")
    if job_type == JOB_TYPE_AUTO_PAPER_AGENT:
        await dispatch_auto_paper_agent_turn(job)
        return
    if job_type == JOB_TYPE_SCHEDULER_HEALTH:
        await asyncio.to_thread(run_scheduler_health_sync)
        return
    if job_type == JOB_TYPE_SESSION_CLOSE_FLATTEN:
        await asyncio.to_thread(run_session_close_flatten_sync)
        return
    await asyncio.to_thread(run_auto_paper_job_sync, job.config)


def dispatch_auto_paper_job_sync(job: ScheduledResearchJob) -> None:
    job_type = str(job.config.get("job_type") or "")
    if job_type == JOB_TYPE_AUTO_PAPER_AGENT:
        asyncio.run(dispatch_auto_paper_agent_turn(job))
        return
    if job_type == JOB_TYPE_SCHEDULER_HEALTH:
        run_scheduler_health_sync()
        return
    if job_type == JOB_TYPE_SESSION_CLOSE_FLATTEN:
        run_session_close_flatten_sync()
        return
    run_auto_paper_job_sync(job.config)


def register_auto_paper_agent_job(store, *, poll_cron: str | None = None) -> int:
    _ensure_trade_integrations_on_path()
    from trade_integrations.auto_paper.config import get_auto_paper_config

    cfg = get_auto_paper_config()
    cron = (poll_cron or cfg.poll_cron or DEFAULT_AUTO_PAPER_POLL_CRON).strip()
    validate_schedule(cron)

    now_ms = int(time.time() * 1000)
    job = ScheduledResearchJob(
        id=AUTO_PAPER_AGENT_JOB_ID,
        prompt="Autonomous intraday paper trading agent turn",
        schedule=cron,
        next_run_at=now_ms,
        status=JobStatus.PENDING,
        created_at=now_ms,
        config={"job_type": JOB_TYPE_AUTO_PAPER_AGENT, "autonomous": True},
    )
    store.upsert(job)
    logger.info("registered auto paper agent job %s (%s)", job.id, job.schedule)
    return 1


def register_default_auto_paper_job(store) -> int:
    if not is_auto_paper_scheduler_enabled():
        return 0

    _ensure_trade_integrations_on_path()
    from trade_integrations.auto_paper.session_store import load_session

    session = load_session()
    if session.get("enabled") and session.get("agent_mode", True):
        return register_auto_paper_agent_job(store)

    from trade_integrations.auto_paper.config import get_auto_paper_config

    cfg = get_auto_paper_config()
    poll_cron = cfg.poll_cron or DEFAULT_AUTO_PAPER_POLL_CRON
    validate_schedule(poll_cron)

    job = ScheduledResearchJob(
        id="auto-paper-intraday",
        prompt="Automated intraday paper trading tick",
        schedule=poll_cron,
        next_run_at=int(time.time() * 1000),
        status=JobStatus.PENDING,
        created_at=int(time.time() * 1000),
        config={"job_type": JOB_TYPE_AUTO_PAPER_INTRADAY},
    )
    if store.get(job.id) is not None:
        return 0
    store.upsert(job)
    logger.info("registered default auto paper job %s (%s)", job.id, job.schedule)
    return 1


def ensure_agent_job_registered() -> bool:
    try:
        from src.scheduled_research.store import ScheduledResearchJobStore

        store = ScheduledResearchJobStore()
        register_auto_paper_agent_job(store)
        return True
    except Exception:
        logger.exception("failed to register auto paper agent job")
        return False


def ensure_vibe_research_jobs() -> dict[str, bool]:
    """Register auto-paper agent turn job only (options monitor is env-global, not per session)."""
    result = {"agent_job": False}
    try:
        from src.scheduled_research.store import ScheduledResearchJobStore

        store = ScheduledResearchJobStore()
        register_auto_paper_agent_job(store)
        result["agent_job"] = True
    except Exception:
        logger.exception("failed to register auto paper agent job")

    return result


def unregister_auto_paper_scheduler_jobs() -> dict[str, bool]:
    """Remove paper-trading cron jobs from the Vibe scheduler store."""
    removed: dict[str, bool] = {}
    try:
        from trade_integrations.auto_paper.scheduler_cleanup import remove_auto_paper_scheduler_jobs

        return remove_auto_paper_scheduler_jobs()
    except ImportError:
        pass

    try:
        from src.scheduled_research.store import ScheduledResearchJobStore

        store = ScheduledResearchJobStore()
        for job_id in AUTO_PAPER_SCHEDULER_JOB_IDS:
            removed[job_id] = store.delete(job_id)
        logger.info("unregistered auto paper scheduler jobs: %s", removed)
    except Exception:
        logger.exception("failed to unregister auto paper scheduler jobs")
    return removed


async def dispatch_thesis_break_agent_turn(ticker: str, widget_id: str, reasons: list[str]) -> None:
    """Enqueue urgent agent turn when options monitor detects thesis break."""
    _ensure_trade_integrations_on_path()
    from trade_integrations.auto_paper.agent_mandate import build_thesis_break_prompt, is_agent_session_active
    from trade_integrations.auto_paper.session_store import load_session

    if not is_agent_session_active():
        return

    session = load_session()
    if not session.get("autonomous", True):
        return

    prompt = build_thesis_break_prompt(ticker=ticker, widget_id=widget_id, reasons=reasons)
    job = ScheduledResearchJob(
        id="auto-paper-thesis-break",
        prompt=prompt,
        schedule="60000",
        next_run_at=int(time.time() * 1000),
        status=JobStatus.PENDING,
        created_at=int(time.time() * 1000),
        config={"job_type": JOB_TYPE_AUTO_PAPER_AGENT, "urgent": True},
    )
    await dispatch_auto_paper_agent_turn(job, prompt_override=prompt)
