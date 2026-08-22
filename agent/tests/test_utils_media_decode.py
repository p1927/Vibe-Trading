"""Tests for src.utils.media_decode.save_base64_data_url."""

from __future__ import annotations

import base64

import pytest

from src.utils.media_decode import FileSizeExceeded, save_base64_data_url


def _data_url(mime: str, raw: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def test_saves_decoded_png_with_correct_extension(tmp_path):
    raw = b"\x89PNG\r\n\x1a\n fake png bytes"
    path = save_base64_data_url(_data_url("image/png", raw), tmp_path)
    assert path.parent == tmp_path
    assert path.suffix == ".png"
    assert path.read_bytes() == raw


def test_mime_type_matching_is_case_insensitive(tmp_path):
    raw = b"hello world"
    path = save_base64_data_url(_data_url("IMAGE/JPEG", raw), tmp_path)
    assert path.suffix == ".jpg"


def test_rejects_malformed_data_url(tmp_path):
    with pytest.raises(ValueError, match="expected data:"):
        save_base64_data_url("not-a-data-url", tmp_path)


def test_rejects_unsupported_mime_type(tmp_path):
    raw_url = _data_url("application/zip", b"PK\x03\x04")
    with pytest.raises(ValueError, match="unsupported data URL MIME type"):
        save_base64_data_url(raw_url, tmp_path)


def test_rejects_invalid_base64_payload(tmp_path):
    with pytest.raises(ValueError, match="invalid base64 payload"):
        save_base64_data_url("data:image/png;base64,not-valid-base64!!!", tmp_path)


def test_enforces_max_bytes(tmp_path):
    raw = b"x" * 100
    url = _data_url("text/plain", raw)
    with pytest.raises(FileSizeExceeded):
        save_base64_data_url(url, tmp_path, max_bytes=10)


def test_max_bytes_zero_disables_limit(tmp_path):
    raw = b"x" * 100
    url = _data_url("text/plain", raw)
    path = save_base64_data_url(url, tmp_path, max_bytes=0)
    assert path.read_bytes() == raw


def test_creates_output_dir_if_missing(tmp_path):
    output_dir = tmp_path / "nested" / "dir"
    assert not output_dir.exists()
    path = save_base64_data_url(_data_url("text/plain", b"hi"), output_dir)
    assert path.exists()
    assert output_dir.exists()
