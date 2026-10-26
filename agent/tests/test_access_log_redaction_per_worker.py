"""Every worker (reload child included) redacts ?ticket= / ?api_key= from uvicorn's access log."""

from __future__ import annotations

import logging

from src.api import lifecycle
from src.api.security import _AccessLogRedactionFilter


def _filters(name: str) -> list[logging.Filter]:
    return [f for f in logging.getLogger(name).filters if isinstance(f, _AccessLogRedactionFilter)]


def test_process_logging_setup_installs_the_redaction_filter_once(monkeypatch):
    monkeypatch.setattr("trade_integrations.observability.logging_config.configure_trade_logging", lambda: None)
    for name in ("uvicorn.access", "uvicorn.error", "uvicorn"):
        for f in _filters(name):
            logging.getLogger(name).removeFilter(f)

    lifecycle._configure_process_logging()
    lifecycle._configure_process_logging()  # a second worker boot in one process must not stack filters

    assert [len(_filters(n)) for n in ("uvicorn.access", "uvicorn.error", "uvicorn")] == [1, 1, 1]
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "GET %s HTTP/1.1" %d',
                            ("127.0.0.1", "/stream?ticket=SECRET123&x=1", 200), None)
    for f in logging.getLogger("uvicorn.access").filters:
        f.filter(rec)
    assert "SECRET123" not in rec.getMessage() and "ticket=***REDACTED***" in rec.getMessage()
