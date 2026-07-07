"""Regression tests for the extracted API infrastructure modules."""

from __future__ import annotations

import api_server
from src.api import security, models, helpers, state


def test_security_reexports():
    assert api_server.require_auth is security.require_auth
    assert api_server.require_event_stream_auth is security.require_event_stream_auth
    assert api_server.require_local_or_auth is security.require_local_or_auth
    assert api_server.require_settings_write_auth is security.require_settings_write_auth
    assert api_server._parse_cors_origins is security._parse_cors_origins
    assert api_server._is_loopback_bind_host is security._is_loopback_bind_host
    assert api_server._is_local_client is security._is_local_client
    assert api_server._configured_api_key is security._configured_api_key


def test_models_reexports():
    assert api_server.Artifact is models.Artifact
    assert api_server.BacktestMetrics is models.BacktestMetrics
    assert api_server.RAGSelection is models.RAGSelection
    assert api_server.RunInfo is models.RunInfo
    assert api_server.RunResponse is models.RunResponse


def test_helpers_reexports():
    assert api_server.RUNS_DIR is helpers.RUNS_DIR
    assert api_server.SESSIONS_DIR is helpers.SESSIONS_DIR
    assert api_server.ENV_PATH is helpers.ENV_PATH
    assert api_server._is_spa_html_route is helpers._is_spa_html_route
    assert api_server._read_env_values is helpers._read_env_values
    assert api_server._validate_path_param is helpers._validate_path_param


def test_state_reexports():
    assert api_server._get_session_service is state._get_session_service


def test_api_key_monkeypatch(monkeypatch):
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    monkeypatch.setattr(api_server, "_API_KEY", "test-secret")
    assert security._configured_api_key() == "test-secret"


def test_no_circular_imports():
    import importlib
    for mod_name in ["src.api.security", "src.api.models", "src.api.helpers", "src.api.state"]:
        importlib.import_module(mod_name)


def test_api_server_is_thin_assembler():
    import inspect
    source = inspect.getsource(api_server)
    total_lines = len(source.splitlines())
    assert total_lines < 400, f"api_server.py has {total_lines} lines, expected < 400"
