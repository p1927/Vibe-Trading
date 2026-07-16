"""Create Vibe UI sessions and inject auto-paper prompts (visible in chat)."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def vibe_ui_base_url() -> str:
    return os.getenv("VIBE_FRONTEND_URL", "http://127.0.0.1:5899").rstrip("/")


def vibe_agent_ui_url(session_id: str) -> str:
    return f"{vibe_ui_base_url()}/agent?session={session_id}"


def _ensure_trade_stack() -> None:
    from src.trade.hub_bridge import ensure_trade_stack_path

    ensure_trade_stack_path()


async def prepare_fresh_vibe_turn(svc, session_id: str) -> None:
    """Cancel in-flight loop and fail stuck attempts so the next turn is a clean slate."""
    from src.session.models import Attempt, AttemptStatus

    svc.cancel_current(session_id)

    session_dir = svc.store.base_dir / session_id / "attempts"
    if not session_dir.is_dir():
        return
    for attempt_dir in session_dir.iterdir():
        if not attempt_dir.is_dir():
            continue
        attempt_file = attempt_dir / "attempt.json"
        if not attempt_file.is_file():
            continue
        try:
            import json

            attempt = Attempt.from_dict(json.loads(attempt_file.read_text(encoding="utf-8")))
        except Exception:
            continue
        if attempt.status == AttemptStatus.RUNNING:
            attempt.mark_failed("Superseded by fresh auto-paper turn")
            svc.store.update_attempt(attempt)


def resolve_or_create_vibe_session(
    svc,
    *,
    ticker: str,
    watchlist: list[str] | None = None,
    vibe_session_id: str | None = None,
    fresh_session: bool = False,
    title_suffix: str = "",
) -> str:
    """Return a Vibe chat session id bound to the paper trader."""
    _ensure_trade_stack()
    from trade_integrations.auto_paper.session_store import get_vibe_session_id, load_session, set_vibe_session_id
    from trade_integrations.auto_paper.vibe_research import paper_session_vibe_config

    symbols = watchlist or [ticker]
    cfg = paper_session_vibe_config(ticker=ticker, watchlist=list(symbols))

    if not fresh_session:
        for candidate in (vibe_session_id, get_vibe_session_id()):
            if not candidate:
                continue
            if svc.get_session(str(candidate)) is not None:
                set_vibe_session_id(str(candidate))
                return str(candidate)

    paper = load_session()
    turn = int(paper.get("bootstrap_turn") or 0) + 1
    suffix = title_suffix or (f" #{turn}" if turn > 1 else "")
    vibe_session = svc.create_session(
        title=f"auto-paper:{ticker}{suffix}",
        config=cfg,
    )
    set_vibe_session_id(vibe_session.session_id)
    paper = load_session()
    paper["bootstrap_turn"] = turn
    paper["vibe_session_id"] = vibe_session.session_id
    from trade_integrations.auto_paper.session_store import save_session

    save_session(paper)
    return vibe_session.session_id


async def inject_vibe_prompt(
    svc,
    *,
    session_id: str,
    prompt: str,
    fresh_turn: bool = True,
) -> dict[str, Any]:
    """Append a user message to Vibe chat and start a new agent attempt."""
    if fresh_turn:
        await prepare_fresh_vibe_turn(svc, session_id)
    result = await svc.send_message(session_id, prompt)
    return {
        "vibe_session_id": session_id,
        "ui_url": vibe_agent_ui_url(session_id),
        "message_id": result.get("message_id"),
        "attempt_id": result.get("attempt_id"),
    }


async def bootstrap_auto_paper_in_vibe(
    svc,
    *,
    prompt: str | None = None,
    ticker: str = "NIFTY",
    budget_inr: float = 20_000.0,
    watchlist: list[str] | None = None,
    max_daily_loss_inr: float = 2_000.0,
    goal: str | None = None,
    mandate: str | None = None,
    vibe_session_id: str | None = None,
    resume: bool = False,
    fresh_session: bool = False,
    dispatch: bool = True,
) -> dict[str, Any]:
    """
    Start or resume paper trading with a Vibe UI session + injected prompt.

    When ``resume`` is true, rebuilds continuity context and opens a fresh agent
    attempt (cancel stuck loops / running attempts first).
    """
    _ensure_trade_stack()
    from trade_integrations.auto_paper.agent_mandate import (
        DEFAULT_AUTONOMOUS_KICKOFF,
        build_resume_prompt,
    )
    from trade_integrations.auto_paper.mcp_actions import start_auto_paper
    from trade_integrations.auto_paper.session_store import load_session

    symbol = ticker.strip().upper()
    symbols = watchlist or [symbol]
    symbols = [s.strip().upper() for s in symbols if s.strip()]
    if symbol not in symbols:
        symbols.insert(0, symbol)

    paper = load_session()
    if resume:
        if not paper.get("enabled"):
            return {"status": "inactive", "message": "No active paper session to resume"}
        content = prompt or build_resume_prompt(
            ticker=symbol,
            crash_note="Continuing autonomous paper session — prior turn may have been interrupted.",
        )
        fresh_session = fresh_session or bool(paper.get("needs_fresh_vibe_session"))
    else:
        start_auto_paper(
            ticker=symbol,
            budget_inr=budget_inr,
            watchlist=symbols,
            max_daily_loss_inr=max_daily_loss_inr,
            goal=goal,
            mandate=mandate,
            agent_mode=True,
            vibe_session_id=vibe_session_id,
        )
        content = prompt or DEFAULT_AUTONOMOUS_KICKOFF.format(ticker=symbol, budget_inr=int(budget_inr))
        try:
            from src.scheduled_research.auto_paper_jobs import ensure_vibe_research_jobs

            ensure_vibe_research_jobs()
        except Exception:
            logger.debug("scheduler registration skipped", exc_info=True)

    sid = resolve_or_create_vibe_session(
        svc,
        ticker=symbol,
        watchlist=symbols,
        vibe_session_id=vibe_session_id,
        fresh_session=fresh_session,
        title_suffix=" resumed" if resume else "",
    )

    payload: dict[str, Any] = {
        "status": "resumed" if resume else "started",
        "primary_ticker": symbol,
        "watchlist": symbols,
    }

    if dispatch:
        turn = await inject_vibe_prompt(svc, session_id=sid, prompt=content, fresh_turn=True)
        payload.update(turn)
        payload["prompt_injected"] = True
    else:
        payload.update({"vibe_session_id": sid, "ui_url": vibe_agent_ui_url(sid), "prompt_injected": False})
        payload["resume_prompt"] = content

    paper = load_session()
    paper["needs_fresh_vibe_session"] = False
    paper["vibe_session_id"] = sid
    from trade_integrations.auto_paper.session_store import save_session

    save_session(paper)
    payload["paper_session"] = {
        "enabled": paper.get("enabled"),
        "halted": paper.get("halted"),
        "vibe_session_id": sid,
    }
    return payload
