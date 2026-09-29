"""get_env_config must never import while holding its lock (import-lock ABBA deadlock)."""

from __future__ import annotations

import sys
import types

from src.config import accessor


def test_env_schema_import_happens_outside_lock(monkeypatch):
    seen: list[bool] = []

    class _Cfg:
        pass

    class _Mod(types.ModuleType):
        def __getattr__(self, name):
            if name == "EnvConfig":
                seen.append(accessor._lock.locked())
                return _Cfg
            raise AttributeError(name)

    monkeypatch.setitem(sys.modules, "src.config.env_schema", _Mod("src.config.env_schema"))
    monkeypatch.setattr(accessor, "_instance", None)
    assert isinstance(accessor.get_env_config(), _Cfg)
    assert seen == [False]
