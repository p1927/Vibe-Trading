"""Tests for src.channelsui.gateway_services."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from src.channelsui.gateway_services import (
    GatewayServices,
    GatewaySessionManagerAdapter,
    MediaService,
    SimpleHttpRouter,
    TranscriptService,
    WebSocketTokenIssuer,
    WorkspaceScope,
    WorkspaceService,
    build_gateway_services,
)
from src.security.workspace_access import WorkspaceScopeError


# --- WebSocketTokenIssuer --------------------------------------------------


def test_issue_token_returns_token_and_ttl():
    issuer = WebSocketTokenIssuer()
    token, ttl = issuer.issue_token(ttl_s=60)
    assert isinstance(token, str) and token
    assert ttl == 60


def test_take_issued_token_consumes_exactly_once():
    issuer = WebSocketTokenIssuer()
    token, _ = issuer.issue_token()
    assert issuer.take_issued_token_if_valid(token) is True
    assert issuer.take_issued_token_if_valid(token) is False


def test_take_issued_token_rejects_unknown_or_empty():
    issuer = WebSocketTokenIssuer()
    assert issuer.take_issued_token_if_valid("nope") is False
    assert issuer.take_issued_token_if_valid(None) is False
    assert issuer.take_issued_token_if_valid("") is False


def test_take_issued_token_rejects_expired(monkeypatch):
    issuer = WebSocketTokenIssuer()
    token, _ = issuer.issue_token(ttl_s=1)
    monkeypatch.setattr(time, "time", lambda: issuer._issued[token] + 10)
    assert issuer.take_issued_token_if_valid(token) is False


def test_gc_drops_expired_entries_on_issue(monkeypatch):
    issuer = WebSocketTokenIssuer()
    stale, _ = issuer.issue_token(ttl_s=1)
    future = issuer._issued[stale] + 10
    monkeypatch.setattr(time, "time", lambda: future)
    issuer.issue_token(ttl_s=60)
    assert stale not in issuer._issued


def test_clear_empties_all_tokens():
    issuer = WebSocketTokenIssuer()
    issuer.issue_token()
    issuer.issue_token()
    issuer.clear()
    assert issuer._issued == {}


# --- WorkspaceScope ---------------------------------------------------------


def test_workspace_scope_payload_and_metadata_match():
    scope = WorkspaceScope(root="/tmp/ws", restrict_to_workspace=False)
    assert scope.payload() == {"root": "/tmp/ws", "restrict_to_workspace": False}
    assert scope.metadata() == scope.payload()


# --- WorkspaceService --------------------------------------------------------


def test_default_scope_uses_workspace_path(tmp_path):
    service = WorkspaceService(workspace_path=tmp_path)
    scope = service._default_scope()
    assert scope.root == str(tmp_path.resolve())
    assert scope.restrict_to_workspace is True


def test_scope_for_new_chat_falls_back_to_default(tmp_path):
    service = WorkspaceService(workspace_path=tmp_path)
    scope = service.scope_for_new_chat({}, controls_available=True)
    assert scope.root == str(tmp_path.resolve())


def test_scope_for_new_chat_uses_envelope_root(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    service = WorkspaceService(workspace_path=tmp_path)
    scope = service.scope_for_new_chat(
        {"workspace_scope": {"root": str(sub)}}, controls_available=True
    )
    assert scope.root == str(sub.resolve())


def test_scope_for_set_request_prefers_persisted_scope_over_default(tmp_path):
    service = WorkspaceService(workspace_path=tmp_path)
    persisted = WorkspaceScope(root=str(tmp_path / "other"), restrict_to_workspace=False)
    service.persist_scope("chat-1", persisted)
    scope = service.scope_for_set_request(
        {}, chat_id="chat-1", chat_running=False, controls_available=True
    )
    assert scope == persisted


def test_scope_for_message_returns_default_for_unknown_chat(tmp_path):
    service = WorkspaceService(workspace_path=tmp_path)
    scope = service.scope_for_message(
        {}, chat_id="unknown", chat_running=True, controls_available=True
    )
    assert scope.root == str(tmp_path.resolve())


def test_scope_from_envelope_rejects_non_dict_or_blank_root(tmp_path):
    service = WorkspaceService(workspace_path=tmp_path)
    assert service._scope_from_envelope({"workspace_scope": "nope"}) is None
    assert service._scope_from_envelope({"workspace_scope": {"root": ""}}) is None
    assert service._scope_from_envelope({}) is None


def test_scope_from_envelope_raises_when_root_escapes_restricted_workspace(tmp_path):
    outside = tmp_path.parent / f"outside-{tmp_path.name}"
    service = WorkspaceService(workspace_path=tmp_path, default_restrict_to_workspace=True)
    with pytest.raises(WorkspaceScopeError):
        service._scope_from_envelope({"workspace_scope": {"root": str(outside)}})


def test_scope_from_envelope_allows_escape_when_restrict_disabled(tmp_path):
    outside = tmp_path.parent / f"outside-{tmp_path.name}"
    service = WorkspaceService(workspace_path=tmp_path, default_restrict_to_workspace=False)
    scope = service._scope_from_envelope({"workspace_scope": {"root": str(outside)}})
    assert scope is not None
    assert scope.restrict_to_workspace is False


def test_scope_from_envelope_honors_explicit_restrict_flag(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    service = WorkspaceService(workspace_path=tmp_path)
    scope = service._scope_from_envelope(
        {"workspace_scope": {"root": str(sub), "restrict_to_workspace": False}}
    )
    assert scope.restrict_to_workspace is False


# --- SimpleHttpRouter --------------------------------------------------------


def test_workspace_controls_available_is_always_true():
    router = SimpleHttpRouter()
    assert router.workspace_controls_available(connection=None) is True


@pytest.mark.asyncio
async def test_dispatch_returns_404_json():
    router = SimpleHttpRouter()

    class _Conn:
        def respond(self, status, body):
            return status, body

    status, body = await router.dispatch(_Conn(), request=None)
    assert status == 404
    assert json.loads(body) == {"detail": "not found"}


# --- MediaService -------------------------------------------------------------


def test_rewrite_local_markdown_images_is_identity():
    media = MediaService()
    assert media.rewrite_local_markdown_images("hello ![x](y.png)") == "hello ![x](y.png)"


def test_sign_or_stage_media_path_for_existing_file(tmp_path):
    media = MediaService()
    file_path = tmp_path / "img.png"
    file_path.write_bytes(b"data")
    result = media.sign_or_stage_media_path(file_path)
    assert result == {"name": "img.png", "path": str(file_path.resolve())}


def test_sign_or_stage_media_path_for_missing_file(tmp_path):
    media = MediaService()
    assert media.sign_or_stage_media_path(tmp_path / "missing.png") is None


def test_sign_or_stage_media_path_for_directory(tmp_path):
    media = MediaService()
    assert media.sign_or_stage_media_path(tmp_path) is None


# --- TranscriptService --------------------------------------------------------


def test_client_turn_metadata_for_string_and_non_string():
    service = TranscriptService()
    assert service.client_turn_metadata("turn-1") == {"turn_id": "turn-1"}
    assert service.client_turn_metadata(None) == {}
    assert service.client_turn_metadata(123) == {}
    assert service.client_turn_metadata("") == {}


def test_append_user_message_defaults():
    service = TranscriptService()
    service.append_user_message("chat-1", "hi")
    assert service.events == [
        {
            "phase": "user",
            "chat_id": "chat-1",
            "content": "hi",
            "metadata": {},
            "media_paths": [],
            "cli_apps": [],
            "mcp_presets": [],
        }
    ]


def test_append_user_message_explicit_fields():
    service = TranscriptService()
    service.append_user_message(
        "chat-1",
        "hi",
        metadata={"a": 1},
        media_paths=["p"],
        cli_apps=["c"],
        mcp_presets=["m"],
    )
    event = service.events[0]
    assert event["metadata"] == {"a": 1}
    assert event["media_paths"] == ["p"]
    assert event["cli_apps"] == ["c"]
    assert event["mcp_presets"] == ["m"]


def test_prepare_and_append_defaults_and_overrides():
    service = TranscriptService()
    service.prepare_and_append("chat-1", {"k": "v"})
    service.prepare_and_append(
        "chat-1",
        {"k": "v2"},
        metadata={"m": 1},
        phase="assistant",
        include_source=True,
        transcript_overrides={"o": 1},
    )
    first, second = service.events
    assert first == {
        "phase": "",
        "chat_id": "chat-1",
        "payload": {"k": "v"},
        "metadata": {},
        "include_source": False,
        "transcript_overrides": {},
    }
    assert second["phase"] == "assistant"
    assert second["include_source"] is True
    assert second["transcript_overrides"] == {"o": 1}


# --- GatewaySessionManagerAdapter ---------------------------------------------


def test_adapter_delegates_unknown_attrs_to_wrapped_service():
    class _Service:
        marker = "value"

    adapter = GatewaySessionManagerAdapter(_Service())
    assert adapter.marker == "value"


def test_read_session_file_returns_empty_metadata_without_get_session():
    class _Service:
        pass

    adapter = GatewaySessionManagerAdapter(_Service())
    assert adapter.read_session_file("s1") == {"metadata": {}}


def test_read_session_file_returns_empty_metadata_when_session_missing():
    class _Service:
        def get_session(self, session_id):
            return None

    adapter = GatewaySessionManagerAdapter(_Service())
    assert adapter.read_session_file("s1") == {"metadata": {}}


def test_read_session_file_retries_with_suffix_after_colon():
    class _Session:
        def __init__(self, metadata):
            self.metadata = metadata

    class _Service:
        def get_session(self, session_id):
            if session_id == "sess-1":
                return _Session({"x": 1})
            return None

    adapter = GatewaySessionManagerAdapter(_Service())
    result = adapter.read_session_file("chat-1:sess-1")
    assert result["metadata"] == {"x": 1}


def test_read_session_file_uses_to_dict_when_available():
    class _Session:
        def to_dict(self):
            return {"config": {"metadata": {"nested": True}}}

    class _Service:
        def get_session(self, session_id):
            return _Session()

    adapter = GatewaySessionManagerAdapter(_Service())
    result = adapter.read_session_file("s1")
    assert result["metadata"] == {"nested": True}


def test_read_session_file_falls_back_to_plain_dunder_dict():
    class _Session:
        def __init__(self):
            self.metadata = {"plain": 1}

    class _Service:
        def get_session(self, session_id):
            return _Session()

    adapter = GatewaySessionManagerAdapter(_Service())
    result = adapter.read_session_file("s1")
    assert result["metadata"] == {"plain": 1}


def test_read_session_file_defaults_metadata_to_empty_dict_when_absent():
    class _Session:
        def to_dict(self):
            return {}

    class _Service:
        def get_session(self, session_id):
            return _Session()

    adapter = GatewaySessionManagerAdapter(_Service())
    result = adapter.read_session_file("s1")
    assert result["metadata"] == {}


# --- build_gateway_services ----------------------------------------------------


def test_build_gateway_services_filters_unknown_kwargs():
    services = build_gateway_services(unknown_field="x", runtime_surface="cli")
    assert isinstance(services, GatewayServices)
    assert services.runtime_surface == "cli"
    assert not hasattr(services, "unknown_field")


def test_build_gateway_services_wraps_bare_session_manager():
    class _Service:
        def get_session(self, session_id):
            return None

    built = build_gateway_services(session_manager=_Service())
    assert isinstance(built.session_manager, GatewaySessionManagerAdapter)


def test_build_gateway_services_leaves_adapter_shaped_session_manager_untouched():
    class _AlreadyAdapted:
        def read_session_file(self, session_id):
            return {"metadata": {}}

    original = _AlreadyAdapted()
    built = build_gateway_services(session_manager=original)
    assert built.session_manager is original


def test_build_gateway_services_derives_workspaces_from_workspace_path(tmp_path):
    built = build_gateway_services(workspace_path=tmp_path, default_restrict_to_workspace=False)
    assert isinstance(built.workspaces, WorkspaceService)
    assert built.workspaces.workspace_path == Path(tmp_path)
    assert built.workspaces.default_restrict_to_workspace is False


def test_build_gateway_services_respects_explicit_workspaces_override(tmp_path):
    explicit = WorkspaceService(workspace_path=tmp_path)
    built = build_gateway_services(workspace_path=tmp_path, workspaces=explicit)
    assert built.workspaces is explicit
