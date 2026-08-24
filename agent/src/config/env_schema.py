"""Single source of truth for all Vibe-Trading environment variable defaults.

This module defines Pydantic models for every environment variable consumed by
the Vibe-Trading agent, grouped by functional category.  Each field carries the
correct type, default value, and env-var alias so that ``EnvConfig()`` with no
arguments reads from ``os.environ`` and applies the documented defaults.

The schema replaces the ~207 scattered ``os.getenv`` calls across 66 files with
a validated, typed, centrally-managed configuration layer.

Usage::

    from src.config.env_schema import EnvConfig

    cfg = EnvConfig()          # reads os.environ, applies defaults
    cfg.llm.timeout_seconds    # 120
    cfg.data.tushare_token     # ""
"""

from __future__ import annotations

import os

from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

__all__ = [
    "EnvConfig",
    "LLMConfig",
    "DataConfig",
    "APIConfig",
    "SwarmConfig",
    "AgentTuningConfig",
    "PathConfig",
    "OcrConfig",
    "MemoryConfig",
    "TradeConfig",
]


# ---------------------------------------------------------------------------
# Boolean coercion helper
# ---------------------------------------------------------------------------


def _parse_env_bool(v: Any) -> Any:
    """Coerce environment string values to ``bool``.

    Accepts ``"1"``, ``"true"``, ``"yes"``, ``"on"`` (case-insensitive) as
    ``True`` and ``"0"``, ``"false"``, ``"no"``, ``"off"``, ``""`` as ``False``.
    Non-string values pass through for Pydantic's built-in coercion.
    """
    if isinstance(v, str):
        low = v.strip().lower()
        if low in {"1", "true", "yes", "on"}:
            return True
        if low in {"0", "false", "no", "off", ""}:
            return False
    return v


EnvBool = Annotated[bool, BeforeValidator(_parse_env_bool)]


def _parse_responses_api_bool(v: Any) -> Any:
    """Enable Responses transport only for the documented literal ``true``."""
    if isinstance(v, str):
        return v.strip() == "true"
    return v


ResponsesApiBool = Annotated[bool, BeforeValidator(_parse_responses_api_bool)]


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class _EnvBase(BaseModel):
    """Base for environment-variable-backed config sub-models.

    Each sub-model reads missing fields from ``os.environ`` using the field
    alias (the UPPER_SNAKE_CASE env-var name).  Explicit constructor arguments
    always take precedence over environment values.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _load_from_env(cls, data: Any) -> Any:
        """Populate missing fields from ``os.environ`` using field aliases.

        Invalid numeric env values are silently dropped so Pydantic applies
        the field default instead of raising a ``ValidationError``.
        """
        if not isinstance(data, dict):
            return data
        result = dict(data)
        for field_name, field_info in cls.model_fields.items():
            alias = field_info.alias
            # Skip fields already provided (by Python name or alias).
            if field_name in result or (alias and alias in result):
                continue
            # Read from environment using the UPPER_SNAKE_CASE alias.
            if alias and alias in os.environ:
                env_val = os.environ[alias]
                # Safe coercion for numeric fields: drop unparseable values
                # so Pydantic falls back to the field default.
                annotation = field_info.annotation
                if annotation is int and isinstance(env_val, str):
                    try:
                        env_val = int(env_val)
                    except ValueError:
                        continue
                elif annotation is float and isinstance(env_val, str):
                    try:
                        env_val = float(env_val)
                    except ValueError:
                        continue
                result[alias] = env_val
        return result


# ---------------------------------------------------------------------------
# LLM Provider
# ---------------------------------------------------------------------------


class LLMConfig(_EnvBase):
    """LLM provider and generation parameters.

    Sources: ``src/providers/llm.py``, ``src/providers/chat.py``,
    ``src/providers/openai_codex.py``, ``src/preflight.py``.
    """

    langchain_provider: str = Field(alias="LANGCHAIN_PROVIDER", default="openai")
    langchain_model_name: str = Field(alias="LANGCHAIN_MODEL_NAME", default="")
    langchain_temperature: float = Field(alias="LANGCHAIN_TEMPERATURE", default=0.0)
    anthropic_max_tokens: int | None = Field(alias="ANTHROPIC_MAX_TOKENS", default=None, gt=0)
    timeout_seconds: int = Field(alias="TIMEOUT_SECONDS", default=120)
    max_retries: int = Field(alias="MAX_RETRIES", default=2)
    langchain_reasoning_effort: str = Field(alias="LANGCHAIN_REASONING_EFFORT", default="")
    langchain_use_responses_api: ResponsesApiBool | None = Field(
        alias="LANGCHAIN_USE_RESPONSES_API", default=None
    )
    vibe_trading_deepseek_adapter: str = Field(alias="VIBE_TRADING_DEEPSEEK_ADAPTER", default="auto")
    moonshot_user_agent: str = Field(alias="MOONSHOT_USER_AGENT", default="")
    vibe_trading_disable_http_proxy: EnvBool = Field(
        alias="VIBE_TRADING_DISABLE_HTTP_PROXY", default=False,
    )
    openai_codex_base_url: str = Field(
        alias="OPENAI_CODEX_BASE_URL",
        default="https://chatgpt.com/backend-api/codex/responses",
    )
    openai_model: str = Field(alias="OPENAI_MODEL", default="")
    vibe_trading_disable_http_proxy: EnvBool = Field(
        alias="VIBE_TRADING_DISABLE_HTTP_PROXY", default=False,
    )


# ---------------------------------------------------------------------------
# Data Sources
# ---------------------------------------------------------------------------


class DataConfig(_EnvBase):
    """Market-data source credentials and tuning.

    Sources: ``backtest/loaders/*.py``, ``src/tools/web_search_tool.py``,
    ``src/tools/iwencai_tool.py``, ``src/tools/fred_macro_tool.py``.
    """

    tushare_token: str = Field(alias="TUSHARE_TOKEN", default="")
    ccxt_exchange: str = Field(alias="CCXT_EXCHANGE", default="binance")
    ccxt_timeout_ms: int = Field(alias="CCXT_TIMEOUT_MS", default=15000)
    ccxt_fetch_budget_s: float = Field(alias="CCXT_FETCH_BUDGET_S", default=60.0)
    futu_host: str = Field(alias="FUTU_HOST", default="127.0.0.1")
    futu_port: int = Field(alias="FUTU_PORT", default=11111)
    finnhub_api_key: str = Field(alias="FINNHUB_API_KEY", default="")
    alphavantage_api_key: str = Field(alias="ALPHAVANTAGE_API_KEY", default="")
    tiingo_api_key: str = Field(alias="TIINGO_API_KEY", default="")
    fmp_api_key: str = Field(alias="FMP_API_KEY", default="")
    fred_api_key: str = Field(alias="FRED_API_KEY", default="")
    vibe_trading_iwencai_key: str = Field(alias="VIBE_TRADING_IWENCAI_KEY", default="")
    vibe_trading_sec_ua: str = Field(alias="VIBE_TRADING_SEC_UA", default="")
    # 13F scan bounds. No gt=0 constraint: a non-positive override must fall
    # back to the default like any other bad value, and a constraint would
    # raise a ValidationError at config load instead.
    vibe_trading_sec_13f_max_xml_mb: float = Field(
        alias="VIBE_TRADING_SEC_13F_MAX_XML_MB", default=25.0
    )
    vibe_trading_sec_13f_budget_s: float = Field(
        alias="VIBE_TRADING_SEC_13F_BUDGET_S", default=120.0
    )
    vibe_trading_sec_ftd_url: str = Field(alias="VIBE_TRADING_SEC_FTD_URL", default="")
    vibe_trading_sec_ftd_files: int = Field(alias="VIBE_TRADING_SEC_FTD_FILES", default=1)
    vibe_trading_openalex_mailto: str = Field(alias="VIBE_TRADING_OPENALEX_MAILTO", default="")
    vibe_tw_stock_db: str = Field(alias="VIBE_TW_STOCK_DB", default="")
    vibe_trading_data_cache: EnvBool = Field(alias="VIBE_TRADING_DATA_CACHE", default=False)
    vibe_trading_data_cache_root: str = Field(alias="VIBE_TRADING_DATA_CACHE_ROOT", default="")
    aliyun_iqs_api_key: str = Field(alias="ALIYUN_IQS_API_KEY", default="")
    qveris_api_key: str = Field(alias="QVERIS_API_KEY", default="")
    qveris_base_url: str = Field(alias="QVERIS_BASE_URL", default="")
    tickerall_api_key: str = Field(alias="TICKERALL_API_KEY", default="")
    tickerall_account_id: str = Field(alias="TICKERALL_ACCOUNT_ID", default="")
    tickerall_base_url: str = Field(alias="TICKERALL_BASE_URL", default="")
    rsshub_base_url: str = Field(alias="RSSHUB_BASE_URL", default="")
    dashscope_api_key: str = Field(alias="DASHSCOPE_API_KEY", default="")
    longbridge_app_key: str = Field(alias="LONGBRIDGE_APP_KEY", default="")
    longbridge_app_secret: str = Field(alias="LONGBRIDGE_APP_SECRET", default="")
    longbridge_access_token: str = Field(alias="LONGBRIDGE_ACCESS_TOKEN", default="")
    etoro_api_key: str = Field(alias="ETORO_API_KEY", default="")
    etoro_user_key: str = Field(alias="ETORO_USER_KEY", default="")


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------


class OcrConfig(_EnvBase):
    """OCR engine selection and model configuration.

    Sources: ``src/tools/ocr/engine.py``, ``src/tools/ocr/llm_vision_ocr.py``.
    """

    vibe_trading_ocr_engine: str = Field(alias="VIBE_TRADING_OCR_ENGINE", default="auto")
    vibe_trading_ocr_llm_model: str = Field(alias="VIBE_TRADING_OCR_LLM_MODEL", default="")

    @model_validator(mode="before")
    @classmethod
    def _alias_legacy_env_vars(cls, data: dict) -> dict:
        """Backward compat: VIBE_TRADING_OCR_QWEN_MODEL → VIBE_TRADING_OCR_LLM_MODEL.

        Issue #547 requires a deprecation warning when the legacy env var is
        present. Pydantic alias validation runs before field assignment, so we
        read the old env var here and forward it to the new field.
        """
        import logging

        old_val = os.getenv("VIBE_TRADING_OCR_QWEN_MODEL", "")
        new_val = (
            data.get("vibe_trading_ocr_llm_model")
            or os.getenv("VIBE_TRADING_OCR_LLM_MODEL", "")
        )
        if old_val and not new_val:
            logging.getLogger(__name__).warning(
                "VIBE_TRADING_OCR_QWEN_MODEL is deprecated; "
                "use VIBE_TRADING_OCR_LLM_MODEL instead. "
                "Alias will be removed in a future release."
            )
            data["vibe_trading_ocr_llm_model"] = old_val
        return data


# ---------------------------------------------------------------------------
# API Server
# ---------------------------------------------------------------------------


class APIConfig(_EnvBase):
    """API server authentication, CORS, and security toggles.

    Sources: ``src/api/security.py``, ``src/api/state.py``,
    ``mcp_server.py``, ``src/tools/path_utils.py``.
    """

    api_auth_key: str = Field(alias="API_AUTH_KEY", default="")
    vibe_trading_api_key: str = Field(alias="VIBE_TRADING_API_KEY", default="")
    cors_origins: str = Field(alias="CORS_ORIGINS", default="")
    # Additive, unlike CORS_ORIGINS: these origins are appended to the loopback
    # defaults instead of replacing them. Used to admit a hosted console (e.g.
    # OpenBB Workspace) without discarding the local-dev origins.
    vibe_trading_extra_cors_origins: str = Field(
        alias="VIBE_TRADING_EXTRA_CORS_ORIGINS", default="",
    )
    api_allowed_hosts: str = Field(alias="API_ALLOWED_HOSTS", default="")
    # Comma-separated Host/Origin allow-list for the network MCP transports
    # (--transport sse / http). Empty means loopback-only (127.0.0.1,
    # localhost), which blocks DNS-rebinding while keeping local use working.
    vibe_trading_mcp_allowed_hosts: str = Field(
        alias="VIBE_TRADING_MCP_ALLOWED_HOSTS", default="",
    )
    enable_session_runtime: EnvBool = Field(alias="ENABLE_SESSION_RUNTIME", default=True)
    vibe_trading_trust_docker_loopback: EnvBool = Field(
        alias="VIBE_TRADING_TRUST_DOCKER_LOOPBACK", default=False,
    )
    # Ship the Content-Security-Policy as Report-Only instead of enforcing it.
    # The policy is enforced by default; this is a rollback switch for a
    # deployment that serves extra assets the stock policy does not cover
    # (a reverse proxy injecting a script, a customized frontend build).
    vibe_trading_csp_report_only: EnvBool = Field(
        alias="VIBE_TRADING_CSP_REPORT_ONLY", default=False,
    )
    vibe_trading_enable_shell_tools: EnvBool = Field(
        alias="VIBE_TRADING_ENABLE_SHELL_TOOLS", default=False,
    )
    vibe_trading_allowed_file_roots: str = Field(
        alias="VIBE_TRADING_ALLOWED_FILE_ROOTS", default="",
    )
    vibe_trading_allowed_write_roots: str = Field(
        alias="VIBE_TRADING_ALLOWED_WRITE_ROOTS", default="",
    )
    vibe_trading_allowed_run_roots: str = Field(
        alias="VIBE_TRADING_ALLOWED_RUN_ROOTS", default="",
    )
    vibe_trading_api_url: str = Field(
        alias="VIBE_TRADING_API_URL", default="http://127.0.0.1:8000",
    )
    futu_trade_pwd_md5: str = Field(alias="FUTU_TRADE_PWD_MD5", default="")


# ---------------------------------------------------------------------------
# Swarm
# ---------------------------------------------------------------------------


class SwarmConfig(_EnvBase):
    """Multi-agent swarm execution parameters.

    Sources: ``src/swarm/worker.py``, ``src/tools/swarm_tool.py``,
    ``src/swarm/runtime.py``, ``src/swarm/grounding.py``.
    """

    swarm_worker_timeout: int = Field(alias="SWARM_WORKER_TIMEOUT", default=300)
    swarm_worker_max_iter: int = Field(alias="SWARM_WORKER_MAX_ITER", default=50)
    swarm_max_workers: int = Field(alias="SWARM_MAX_WORKERS", default=4)
    swarm_timeout: int = Field(alias="SWARM_TIMEOUT", default=1800)
    swarm_heartbeat_interval_s: float = Field(alias="SWARM_HEARTBEAT_INTERVAL_S", default=3.0)
    swarm_stream_retry_delay_s: float = Field(alias="SWARM_STREAM_RETRY_DELAY_S", default=1.0)
    swarm_grounding_max_symbols: int = Field(alias="SWARM_GROUNDING_MAX_SYMBOLS", default=8)


# ---------------------------------------------------------------------------
# Agent Tuning
# ---------------------------------------------------------------------------


class AgentTuningConfig(_EnvBase):
    """Agent loop tuning, content-filter, scheduler, and feature flags.

    Sources: ``src/agent/loop.py``, ``src/providers/content_filter.py``,
    ``src/scheduled_research/executor.py``, ``src/live/order_guard.py``,
    ``src/factors/_backend.py``, ``src/factors/bench_runner.py``,
    ``src/tools/web_search_tool.py``, ``api_server.py``.
    """

    token_threshold: int = Field(alias="TOKEN_THRESHOLD", default=40000)
    vt_heartbeat_interval_s: float = Field(alias="VT_HEARTBEAT_INTERVAL_S", default=3.0)
    vt_reasoning_delta_min_interval_s: float = Field(
        alias="VT_REASONING_DELTA_MIN_INTERVAL_S", default=1.0,
    )
    vt_stream_retry_delay_s: float = Field(alias="VT_STREAM_RETRY_DELAY_S", default=1.0)
    vibe_trading_tool_timeout_seconds: float = Field(
        alias="VIBE_TRADING_TOOL_TIMEOUT_SECONDS", default=1800.0,
    )
    vibe_trading_llm_timeout_seconds: float = Field(
        alias="VIBE_TRADING_LLM_TIMEOUT_SECONDS", default=300.0,
    )
    vibe_trading_run_stall_timeout_seconds: float = Field(
        alias="VIBE_TRADING_RUN_STALL_TIMEOUT_SECONDS", default=1800.0,
    )
    vibe_trading_goal_max_continuations: int = Field(
        alias="VIBE_TRADING_GOAL_MAX_CONTINUATIONS", default=3,
    )
    vibe_trading_sse_timeout: int = Field(alias="VIBE_TRADING_SSE_TIMEOUT", default=90)
    content_filter_warning_threshold: float = Field(
        alias="CONTENT_FILTER_WARNING_THRESHOLD", default=0.05,
    )
    vibe_trading_enable_advisory: EnvBool = Field(
        alias="VIBE_TRADING_ENABLE_ADVISORY", default=False,
    )
    vibe_trading_enable_scheduler: EnvBool = Field(
        alias="VIBE_TRADING_ENABLE_SCHEDULER", default=False,
    )
    index_research_enable_scheduler: EnvBool = Field(
        alias="INDEX_RESEARCH_ENABLE_SCHEDULER", default=False,
    )
    index_monitor_enable_scheduler: EnvBool = Field(
        alias="INDEX_MONITOR_ENABLE_SCHEDULER", default=False,
    )
    vibe_trading_scheduler_max_consecutive_failures: int = Field(
        alias="VIBE_TRADING_SCHEDULER_MAX_CONSECUTIVE_FAILURES", default=3,
    )
    vibe_trading_scheduler_retry_base_delay_ms: int = Field(
        alias="VIBE_TRADING_SCHEDULER_RETRY_BASE_DELAY_MS", default=60_000,
    )
    vibe_trading_scheduler_retry_max_delay_ms: int = Field(
        alias="VIBE_TRADING_SCHEDULER_RETRY_MAX_DELAY_MS", default=3_600_000,
    )
    vibe_trading_channels_auto_start: EnvBool = Field(
        alias="VIBE_TRADING_CHANNELS_AUTO_START", default=False,
    )
    vibe_trading_disable_bottleneck: EnvBool = Field(
        alias="VIBE_TRADING_DISABLE_BOTTLENECK", default=False,
    )
    vibe_trading_bench_workers: int = Field(alias="VIBE_TRADING_BENCH_WORKERS", default=0)
    vibe_trading_search_backends: str = Field(alias="VIBE_TRADING_SEARCH_BACKENDS", default="")
    vibe_trading_slash_arg_max: int = Field(alias="VIBE_TRADING_SLASH_ARG_MAX", default=600)
    vibe_trading_search_bing_fallback: EnvBool = Field(
        alias="VIBE_TRADING_SEARCH_BING_FALLBACK", default=True,
    )
    vibe_live_authorize_timeout_s: int = Field(
        alias="VIBE_LIVE_AUTHORIZE_TIMEOUT_SECONDS", default=300,
    )


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


class PathConfig(_EnvBase):
    """File-system path overrides and session-level MCP trust.

    Sources: ``src/hypotheses/registry.py``, ``src/goal/store.py``,
    ``src/config/loader.py``.
    """

    vibe_trading_hypotheses_path: str = Field(alias="VIBE_TRADING_HYPOTHESES_PATH", default="")
    vibe_trading_goal_db_path: str = Field(alias="VIBE_TRADING_GOAL_DB_PATH", default="")
    vibe_trading_playbook_dir: str = Field(alias="VIBE_TRADING_PLAYBOOK_DIR", default="")
    vibe_trading_swarm_agent_config: str = Field(
        alias="VIBE_TRADING_SWARM_AGENT_CONFIG", default="",
    )
    allow_session_mcp_servers: EnvBool = Field(alias="ALLOW_SESSION_MCP_SERVERS", default=False)
    vibe_trading_theme: str = Field(alias="VIBE_TRADING_THEME", default="")
    vibe_goal_session_id: str = Field(alias="VIBE_GOAL_SESSION_ID", default="")
    vibe_trading_strategy_store_db_path: str = Field(
        alias="VIBE_TRADING_STRATEGY_STORE_DB_PATH", default="",
    )
    vibe_trading_strategy_discovery_db_path: str = Field(
        alias="VIBE_TRADING_STRATEGY_DISCOVERY_DB_PATH", default="",
    )


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


_PRESET_FLAGS: dict[str, dict[str, bool]] = {
    "off": {
        "quality_enabled": False,
        "gc_enabled": False,
        "decay_enabled": False,
        "hierarchy_enabled": False,
        "links_enabled": False,
        "compression_enabled": False,
        "fts_index_enabled": False,
    },
    "on": {
        "quality_enabled": True,
        "gc_enabled": True,
        "decay_enabled": True,
        "hierarchy_enabled": False,
        "links_enabled": False,
        "compression_enabled": False,
        "fts_index_enabled": False,
    },
    "full": {
        "quality_enabled": True,
        "gc_enabled": True,
        "decay_enabled": True,
        "hierarchy_enabled": True,
        "links_enabled": True,
        "compression_enabled": True,
        "fts_index_enabled": True,
    },
}

# Mapping from field name to environment variable alias
_MEMORY_FLAG_ALIASES: dict[str, str] = {
    "quality_enabled": "VT_MEMORY_QUALITY",
    "gc_enabled": "VT_MEMORY_GC",
    "decay_enabled": "VT_MEMORY_DECAY",
    "hierarchy_enabled": "VT_MEMORY_HIERARCHY",
    "links_enabled": "VT_MEMORY_LINKS",
    "compression_enabled": "VT_MEMORY_COMPRESSION",
    "fts_index_enabled": "VT_MEMORY_FTS_INDEX",
}


class MemoryConfig(_EnvBase):
    """Memory lifecycle feature flags.

    Use VT_MEMORY=off|on|full as a business-friendly preset.
    Individual VT_MEMORY_* flags can override the preset baseline.
    """

    # Preset: business-friendly one-liner
    preset: str = Field(default="off", alias="VT_MEMORY")

    # Tier 1 flags
    quality_enabled: EnvBool = Field(
        default=False,
        alias="VT_MEMORY_QUALITY",
        description="Enable quality scoring and reinforcement",
    )
    gc_enabled: EnvBool = Field(
        default=False,
        alias="VT_MEMORY_GC",
        description="Enable garbage collection cycle",
    )
    decay_enabled: EnvBool = Field(
        default=False,
        alias="VT_MEMORY_DECAY",
        description="Enable importance decay computation",
    )

    # Tier 2 flags
    hierarchy_enabled: EnvBool = Field(
        default=False, alias="VT_MEMORY_HIERARCHY",
        description="Enable hierarchical directory routing",
    )
    links_enabled: EnvBool = Field(
        default=False, alias="VT_MEMORY_LINKS",
        description="Enable BM25 semantic linking",
    )
    compression_enabled: EnvBool = Field(
        default=False, alias="VT_MEMORY_COMPRESSION",
        description="Enable auto-compression pipeline",
    )
    fts_index_enabled: EnvBool = Field(
        default=False, alias="VT_MEMORY_FTS_INDEX",
        description="Enable SQLite FTS5 search index",
    )

    @model_validator(mode="before")
    @classmethod
    def _apply_preset(cls, data: Any) -> Any:
        """Apply VT_MEMORY preset as baseline; explicit flags override."""
        if not isinstance(data, dict):
            return data

        # Determine preset value from env or constructor data
        preset_val = (
            os.environ.get("VT_MEMORY")
            or data.get("VT_MEMORY")
            or data.get("preset")
            or "off"
        )
        preset_val = preset_val.strip().lower()
        if preset_val not in _PRESET_FLAGS:
            preset_val = "off"

        baseline = _PRESET_FLAGS[preset_val]

        # For each flag: use explicit env var if set, otherwise apply preset
        for field_name, env_alias in _MEMORY_FLAG_ALIASES.items():
            # If user explicitly set this env var, don't override
            if os.environ.get(env_alias) is not None:
                continue
            # If explicitly passed in constructor data, don't override
            if field_name in data or env_alias in data:
                continue
            # Apply preset baseline using field name
            data[field_name] = baseline[field_name]

        return data


# ---------------------------------------------------------------------------
# Trade stack (fork-only): scheduled-research jobs, guards, OpenAlgo connector
# ---------------------------------------------------------------------------


class TradeConfig(_EnvBase):
    """Fork-only trade-stack integration settings.

    Sources: ``src/api/runtime_activity.py``, ``src/api/scheduled_startup.py``,
    ``src/api/trade_routes.py``, ``src/scheduled_research/*_jobs.py``,
    ``src/scheduled_research/staleness.py``, ``src/session/service.py``,
    ``src/trade/*_guard.py``, ``src/trade/hub_bridge.py``,
    ``src/trade/index_prediction_run_jobs.py``, ``src/trade/recording_jobs.py``,
    ``src/trading/connectors/openalgo/sdk.py``.

    Every field here is deliberately kept as ``str`` (rather than a coerced
    ``bool``/``int``/``float``) so that each call site's own truthy-set /
    numeric-parsing logic — which varies from site to site and in several
    cases differs from the canonical ``{"1","true","yes","on"}`` set — keeps
    running unmodified against the raw string. This is a mechanical read-path
    migration, not a behavior change.
    """

    # --- api/runtime_activity.py, api/scheduled_startup.py -----------------
    stack_dev: str = Field(alias="STACK_DEV", default="")

    # --- api/trade_routes.py, trading/connectors/openalgo/sdk.py -----------
    openalgo_host: str = Field(alias="OPENALGO_HOST", default="http://127.0.0.1:5001")
    openalgo_api_key: str = Field(alias="OPENALGO_API_KEY", default="")
    openalgo_paper_mode: str = Field(alias="OPENALGO_PAPER_MODE", default="true")

    # --- scheduled_research/autonomous_agent_jobs.py ------------------------
    autonomous_agents_enable_scheduler: str = Field(
        alias="AUTONOMOUS_AGENTS_ENABLE_SCHEDULER", default="1",
    )
    autonomous_research_on_schedule: str = Field(
        alias="AUTONOMOUS_RESEARCH_ON_SCHEDULE", default="",
    )

    # --- scheduled_research/capture_jobs.py ---------------------------------
    hub_capture_enable_scheduler: str = Field(alias="HUB_CAPTURE_ENABLE_SCHEDULER", default="")
    hub_capture_intraday_cron: str = Field(
        alias="HUB_CAPTURE_INTRADAY_CRON", default="0 10,13,15 * * 1-5",
    )
    hub_capture_factor_snapshot_cron: str = Field(
        alias="HUB_CAPTURE_FACTOR_SNAPSHOT_CRON", default="*/30 9-16 * * 1-5",
    )

    # --- scheduled_research/hub_calibration_jobs.py -------------------------
    hub_calibration_enable_scheduler: str = Field(
        alias="HUB_CALIBRATION_ENABLE_SCHEDULER", default="",
    )
    hub_calibration_unified: str = Field(alias="HUB_CALIBRATION_UNIFIED", default="true")
    hub_morning_calibration_cron: str = Field(
        alias="HUB_MORNING_CALIBRATION_CRON", default="0 6 * * *",
    )
    hub_evening_maintenance_cron: str = Field(
        alias="HUB_EVENING_MAINTENANCE_CRON", default="35 18 * * *",
    )

    # --- scheduled_research/index_jobs.py -----------------------------------
    index_research_snapshot_cron: str = Field(
        alias="INDEX_RESEARCH_SNAPSHOT_CRON", default="0 18 * * *",
    )
    index_research_full_cron: str = Field(
        alias="INDEX_RESEARCH_FULL_CRON", default="0 8 * * 1",
    )
    stock_history_coverage_sweep_cron: str = Field(
        alias="STOCK_HISTORY_COVERAGE_SWEEP_CRON", default="0 19 * * *",
    )
    news_quality_eval_cron: str = Field(alias="NEWS_QUALITY_EVAL_CRON", default="0 2 * * *")
    global_macro_eod_refresh_cron: str = Field(
        alias="GLOBAL_MACRO_EOD_REFRESH_CRON", default="15 19 * * *",
    )
    oi_snapshot_cron: str = Field(
        alias="OI_SNAPSHOT_CRON", default="45 15 * * 1-5",
    )
    pump_dump_proxy_cron: str = Field(
        alias="PUMP_DUMP_PROXY_CRON", default="50 15 * * 1-5",
    )
    max_pain_bhavcopy_cron: str = Field(
        alias="MAX_PAIN_BHAVCOPY_CRON", default="30 17 * * 1-5",
    )
    reinference_tick_cron: str = Field(
        alias="REINFERENCE_TICK_CRON", default="*/15 9-16 * * 1-5",
    )
    constituent_volume_snapshot_cron: str = Field(
        alias="CONSTITUENT_VOLUME_SNAPSHOT_CRON", default="*/15 9-16 * * 1-5",
    )
    hub_news_full_ingest_cron: str = Field(
        alias="HUB_NEWS_FULL_INGEST_CRON", default="0 7 * * *",
    )
    hub_news_full_sources: str = Field(alias="HUB_NEWS_FULL_SOURCES", default="all")
    hub_news_light_sources: str = Field(alias="HUB_NEWS_LIGHT_SOURCES", default="rss")
    hub_news_entity_cron: str = Field(alias="HUB_NEWS_ENTITY_CRON", default="35 18 * * *")
    hub_news_entity_maintenance_cron: str = Field(
        alias="HUB_NEWS_ENTITY_MAINTENANCE_CRON", default="0 4 * * *",
    )
    index_monitor_poll_cron: str = Field(alias="INDEX_MONITOR_POLL_CRON", default="*/5 * * * *")

    # --- scheduled_research/options_jobs.py ---------------------------------
    options_monitor_enable_scheduler: str = Field(
        alias="OPTIONS_MONITOR_ENABLE_SCHEDULER", default="",
    )
    options_auto_dispatch_thesis_break: str = Field(
        alias="OPTIONS_AUTO_DISPATCH_THESIS_BREAK", default="1",
    )
    options_monitor_poll_cron: str = Field(
        alias="OPTIONS_MONITOR_POLL_CRON", default="*/5 * * * *",
    )

    # --- scheduled_research/staleness.py ------------------------------------
    scheduled_research_startup_grace_ms: str = Field(
        alias="SCHEDULED_RESEARCH_STARTUP_GRACE_MS", default="30000",
    )
    scheduled_research_fresh_defer_ms: str = Field(
        alias="SCHEDULED_RESEARCH_FRESH_DEFER_MS", default="1800000",
    )
    scheduled_research_stale_running_ms: str = Field(
        alias="SCHEDULED_RESEARCH_STALE_RUNNING_MS", default="2700000",
    )
    index_plan_refresh_stale_ms: str = Field(
        alias="INDEX_PLAN_REFRESH_STALE_MS", default="600000",
    )
    scheduled_research_dispatch_timeout_ms: str = Field(
        alias="SCHEDULED_RESEARCH_DISPATCH_TIMEOUT_MS", default="2700000",
    )
    scheduled_research_watchdog_interval_ms: str = Field(
        alias="SCHEDULED_RESEARCH_WATCHDOG_INTERVAL_MS", default="60000",
    )
    scheduled_research_failure_threshold: str = Field(
        alias="SCHEDULED_RESEARCH_FAILURE_THRESHOLD", default="3",
    )

    # --- scheduled_research/trade_data_jobs.py ------------------------------
    trade_data_enable_scheduler: str = Field(alias="TRADE_DATA_ENABLE_SCHEDULER", default="")
    trade_fills_export_cron: str = Field(
        alias="TRADE_FILLS_EXPORT_CRON", default="0 19 * * *",
    )
    research_history_archive_cron: str = Field(
        alias="RESEARCH_HISTORY_ARCHIVE_CRON", default="35 18 * * *",
    )
    nse_macro_refresh_cron: str = Field(alias="NSE_MACRO_REFRESH_CRON", default="15 6 * * *")
    nse_repo_consistency_cron: str = Field(
        alias="NSE_REPO_CONSISTENCY_CRON", default="30 5 * * *",
    )

    # --- session/service.py --------------------------------------------------
    vibe_attempt_prefetch_timeout_s: str = Field(
        alias="VIBE_ATTEMPT_PREFETCH_TIMEOUT_S", default="120",
    )

    # --- trade/autonomous_decision_guard.py, bootstrap_finalize_guard.py,
    #     orchestrator_propose_guard.py, widget_guard.py --------------------
    autonomous_decision_guard_enabled: str = Field(
        alias="AUTONOMOUS_DECISION_GUARD_ENABLED", default="true",
    )
    bootstrap_finalize_guard_enabled: str = Field(
        alias="BOOTSTRAP_FINALIZE_GUARD_ENABLED", default="true",
    )
    orchestrator_propose_guard_enabled: str = Field(
        alias="ORCHESTRATOR_PROPOSE_GUARD_ENABLED", default="true",
    )
    options_widget_guard_enabled: str = Field(
        alias="OPTIONS_WIDGET_GUARD_ENABLED", default="true",
    )

    # --- trade/hub_bridge.py --------------------------------------------------
    trade_stack_root: str = Field(alias="TRADE_STACK_ROOT", default="")
    trade_integrations_skip_apply: str | None = Field(
        alias="TRADE_INTEGRATIONS_SKIP_APPLY", default=None,
    )
    options_auto_widget_on_prefetch: str = Field(
        alias="OPTIONS_AUTO_WIDGET_ON_PREFETCH", default="false",
    )
    index_auto_widget_on_prefetch: str = Field(
        alias="INDEX_AUTO_WIDGET_ON_PREFETCH", default="false",
    )

    # --- trade/index_prediction_run_jobs.py -----------------------------------
    index_prediction_stale_log_seconds: str = Field(
        alias="INDEX_PREDICTION_STALE_LOG_SECONDS", default="1800",
    )
    index_prediction_run_wall_clock_seconds: str = Field(
        alias="INDEX_PREDICTION_RUN_WALL_CLOCK_SECONDS", default="2700",
    )
    index_prediction_refresh_wall_clock_seconds: str = Field(
        alias="INDEX_PREDICTION_REFRESH_WALL_CLOCK_SECONDS", default="5400",
    )
    index_prediction_queued_no_pid_seconds: str = Field(
        alias="INDEX_PREDICTION_QUEUED_NO_PID_SECONDS", default="60",
    )
    index_prediction_watchdog_interval_seconds: str = Field(
        alias="INDEX_PREDICTION_WATCHDOG_INTERVAL_SECONDS", default="60",
    )

    # --- trade/recording_jobs.py ----------------------------------------------
    recording_run_wall_clock_seconds: str = Field(
        alias="RECORDING_RUN_WALL_CLOCK_SECONDS", default=str(8 * 60 * 60),
    )
    recording_queued_no_pid_seconds: str = Field(
        alias="RECORDING_QUEUED_NO_PID_SECONDS", default="60",
    )

    # --- trade/external_predictions_run_jobs.py --------------------------------
    external_predictions_run_wall_clock_seconds: str = Field(
        alias="EXTERNAL_PREDICTIONS_RUN_WALL_CLOCK_SECONDS", default="1800",
    )
    external_predictions_queued_no_pid_seconds: str = Field(
        alias="EXTERNAL_PREDICTIONS_QUEUED_NO_PID_SECONDS", default="60",
    )

    # --- trade/job_watchdog.py ---------------------------------------------
    job_watchdog_interval_seconds: str = Field(
        alias="JOB_WATCHDOG_INTERVAL_SECONDS", default="60",
    )


# ---------------------------------------------------------------------------
# Top-level composition
# ---------------------------------------------------------------------------


class EnvConfig(_EnvBase):
    """Root configuration model composing all environment variable groups.

    Instantiating with no arguments reads every recognised env var from
    ``os.environ`` and applies the documented defaults for missing values::

        cfg = EnvConfig()
        cfg.llm.timeout_seconds   # 120
        cfg.swarm.swarm_timeout   # 1800
    """

    llm: LLMConfig = Field(default_factory=LLMConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    api: APIConfig = Field(default_factory=APIConfig)
    swarm: SwarmConfig = Field(default_factory=SwarmConfig)
    agent_tuning: AgentTuningConfig = Field(default_factory=AgentTuningConfig)
    paths: PathConfig = Field(default_factory=PathConfig)
    ocr: OcrConfig = Field(default_factory=OcrConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    trade: TradeConfig = Field(default_factory=TradeConfig)

    @model_validator(mode="after")
    def _resolve_api_key_alias(self) -> "EnvConfig":
        """Copy ``VIBE_TRADING_API_KEY`` to ``api_auth_key`` when only the alias is set.

        The CLI historically reads ``VIBE_TRADING_API_KEY`` first and falls back to
        ``API_AUTH_KEY`` (``cli/_legacy.py:2834``).  The API server only reads
        ``API_AUTH_KEY`` (``src/api/security.py:126``).  This validator closes
        the semantic gap so both surfaces agree.
        """
        if self.api.vibe_trading_api_key and not self.api.api_auth_key:
            self.api.api_auth_key = self.api.vibe_trading_api_key
        return self
