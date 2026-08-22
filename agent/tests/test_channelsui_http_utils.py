"""Tests for src.channelsui.http_utils."""

from __future__ import annotations

import pytest

from src.channelsui.http_utils import (
    normalize_config_path,
    parse_and_validate_url,
    parse_request_path,
    query_first,
    read_uploaded_file,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", "/"),
        ("foo", "/foo"),
        ("/foo", "/foo"),
        ("/foo/", "/foo"),
        ("//foo//bar//", "/foo/bar"),
        ("/", "/"),
        ("  /foo  ", "/foo"),
    ],
)
def test_normalize_config_path(raw, expected):
    assert normalize_config_path(raw) == expected


def test_parse_request_path_splits_route_and_query():
    route, query = parse_request_path("/ws/agent?token=abc&mode=live")
    assert route == "/ws/agent"
    assert query == {"token": ["abc"], "mode": ["live"]}


def test_parse_request_path_defaults_to_root():
    route, query = parse_request_path("")
    assert route == "/"
    assert query == {}


def test_query_first_returns_first_value():
    assert query_first({"token": ["a", "b"]}, "token") == "a"


def test_query_first_returns_none_for_missing_key():
    assert query_first({}, "token") is None


def test_parse_and_validate_url_rejects_non_http_scheme():
    ok, err = parse_and_validate_url("ftp://example.com/file")
    assert ok is False
    assert "http" in err.lower()


def test_parse_and_validate_url_rejects_missing_domain():
    ok, err = parse_and_validate_url("https://")
    assert ok is False


def test_parse_and_validate_url_allows_loopback_when_flagged():
    ok, err = parse_and_validate_url("http://localhost:8000/health", allow_loopback=True)
    assert ok is True
    assert err == ""


def test_parse_and_validate_url_rejects_loopback_by_default():
    ok, err = parse_and_validate_url("http://localhost:8000/health")
    assert ok is False


@pytest.mark.asyncio
async def test_read_uploaded_file_no_args_returns_empty_bytes():
    assert await read_uploaded_file() == b""


@pytest.mark.asyncio
async def test_read_uploaded_file_passthrough_bytes():
    assert await read_uploaded_file(b"raw bytes") == b"raw bytes"


@pytest.mark.asyncio
async def test_read_uploaded_file_bytearray_converted_to_bytes():
    result = await read_uploaded_file(bytearray(b"raw bytes"))
    assert result == b"raw bytes"
    assert isinstance(result, bytes)


@pytest.mark.asyncio
async def test_read_uploaded_file_sync_read_returning_str():
    class Obj:
        def read(self):
            return "hello"

    assert await read_uploaded_file(Obj()) == b"hello"


@pytest.mark.asyncio
async def test_read_uploaded_file_async_read_returning_bytes():
    class Obj:
        async def read(self):
            return b"async bytes"

    assert await read_uploaded_file(Obj()) == b"async bytes"


@pytest.mark.asyncio
async def test_read_uploaded_file_object_without_read_raises():
    with pytest.raises(TypeError, match="does not provide read"):
        await read_uploaded_file(object())


@pytest.mark.asyncio
async def test_read_uploaded_file_bad_read_return_type_raises():
    class Obj:
        def read(self):
            return 12345

    with pytest.raises(TypeError, match="did not return bytes"):
        await read_uploaded_file(Obj())
