"""R9b P3 leaves: archive never overwrites, unreadable .env fails loud, no duplicate schema field."""
import inspect
import os

import pytest

from src.config import env_schema
from src.config.bootstrap import _parse_env_file
from src.memory.hierarchy import archive_destination


def test_archive_destination_never_collides(tmp_path):
    arch = tmp_path / "archive"
    arch.mkdir()
    src = tmp_path / "t.md"
    first = archive_destination(tmp_path, arch, src)
    first.write_text("old")
    second = archive_destination(tmp_path, arch, src)
    assert second != first and not second.exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_unreadable_env_layer_raises(tmp_path):
    p = tmp_path / ".env"
    p.write_text("A=1")
    p.chmod(0)
    try:
        with pytest.raises(OSError):
            _parse_env_file(p)
    finally:
        p.chmod(0o600)


def test_http_proxy_field_declared_once():
    assert inspect.getsource(env_schema).count("vibe_trading_disable_http_proxy:") == 1
