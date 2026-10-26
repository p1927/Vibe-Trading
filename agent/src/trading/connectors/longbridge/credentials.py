"""Atomic credential resolution for the Longbridge connector."""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping

from src.config.accessor import get_env_config
from src.config.paths import get_runtime_root

_CREDENTIAL_FIELDS = ("app_key", "app_secret", "access_token")
_RUNTIME_FILENAME = "longbridge.json"

CredentialSource = Literal["environment", "runtime_file", "keyring"]
_RuntimeFileState = Literal["absent", "valid", "invalid"]


@dataclass(frozen=True)
class LongbridgeCredentials:
    """A complete Longbridge credential set."""

    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)
    access_token: str = field(repr=False)


@dataclass(frozen=True)
class CredentialResolution:
    """Credential resolution result containing only field-level diagnostics."""

    credentials: LongbridgeCredentials | None
    source: CredentialSource | None
    missing_fields: tuple[str, ...]
    conflict_fields: tuple[str, ...]


class LongbridgeCredentialError(RuntimeError):
    """Raised when no single safe, complete credential source can be selected."""

    def __init__(self, code: str, fields: tuple[str, ...]) -> None:
        self.code = code
        self.fields = fields
        super().__init__(f"{code}: {', '.join(fields)}")


def resolve_longbridge_credentials(
    runtime_root: Path | None = None,
    *,
    paper: bool = False,
) -> CredentialResolution:
    """Resolve credentials atomically from environment or the legacy runtime file.

    ``paper=True`` fails closed (CEO decision, 2026-10-14): Longbridge's access token
    selects the account and nothing in the API tells paper from live, so the paper
    profile needs its own ``LONGBRIDGE_PAPER_ACCESS_TOKEN`` (env only) and refuses to
    resolve if that is missing or equals a live token.
    """
    environment = _environment_values()
    runtime_file, runtime_file_state = _runtime_file_values(
        runtime_root if runtime_root is not None else get_runtime_root()
    )
    if paper:
        return _resolve_paper(environment, runtime_file)

    environment_missing = _missing_fields(environment)
    file_missing = _missing_fields(runtime_file)

    if 0 < len(environment_missing) < len(_CREDENTIAL_FIELDS):
        return CredentialResolution(
            credentials=None,
            source="environment",
            missing_fields=environment_missing,
            conflict_fields=(),
        )

    if not environment_missing:
        if not file_missing:
            conflict_fields = tuple(
                field
                for field in _CREDENTIAL_FIELDS
                if not hmac.compare_digest(environment[field].encode(), runtime_file[field].encode())
            )
            if conflict_fields:
                return CredentialResolution(
                    credentials=None,
                    source=None,
                    missing_fields=(),
                    conflict_fields=conflict_fields,
                )
        return CredentialResolution(
            credentials=LongbridgeCredentials(**environment),
            source="environment",
            missing_fields=(),
            conflict_fields=(),
        )

    if 0 < len(file_missing) < len(_CREDENTIAL_FIELDS):
        return CredentialResolution(
            credentials=None,
            source="runtime_file",
            missing_fields=file_missing,
            conflict_fields=(),
        )

    if not file_missing:
        return CredentialResolution(
            credentials=LongbridgeCredentials(**runtime_file),
            source="runtime_file",
            missing_fields=(),
            conflict_fields=(),
        )

    return CredentialResolution(
        credentials=None,
        source="runtime_file" if runtime_file_state == "invalid" else None,
        missing_fields=_CREDENTIAL_FIELDS,
        conflict_fields=(),
    )


def _resolve_paper(
    environment: Mapping[str, str], runtime_file: Mapping[str, str]
) -> CredentialResolution:
    """App key/secret are the developer app (shared); only the paper token may open the paper account."""
    paper_token = _normalize(get_env_config().data.longbridge_paper_access_token)
    values = {
        "app_key": environment["app_key"] or runtime_file["app_key"],
        "app_secret": environment["app_secret"] or runtime_file["app_secret"],
        "access_token": paper_token,
    }
    missing = _missing_fields(values)
    if missing:
        fields = tuple("paper_access_token" if f == "access_token" else f for f in missing)
        return CredentialResolution(None, "environment", fields, ())
    live_tokens = (environment["access_token"], runtime_file["access_token"])
    if any(t and hmac.compare_digest(t.encode(), paper_token.encode()) for t in live_tokens):
        return CredentialResolution(None, None, (), ("paper_access_token",))
    return CredentialResolution(LongbridgeCredentials(**values), "environment", (), ())


def require_longbridge_credentials(
    runtime_root: Path | None = None,
    *,
    paper: bool = False,
) -> LongbridgeCredentials:
    """Return resolved credentials or raise a field-only diagnostic error."""
    resolution = resolve_longbridge_credentials(runtime_root, paper=paper)
    if resolution.credentials is not None:
        return resolution.credentials
    if resolution.conflict_fields:
        raise LongbridgeCredentialError("credentials_conflict", resolution.conflict_fields)
    code = "credentials_missing" if resolution.source is None else "credentials_partial"
    raise LongbridgeCredentialError(code, resolution.missing_fields)


def _environment_values() -> dict[str, str]:
    data = get_env_config().data
    return {
        "app_key": _normalize(data.longbridge_app_key),
        "app_secret": _normalize(data.longbridge_app_secret),
        "access_token": _normalize(data.longbridge_access_token),
    }


def _runtime_file_values(
    runtime_root: Path,
) -> tuple[dict[str, str], _RuntimeFileState]:
    path = runtime_root / _RUNTIME_FILENAME
    empty = {field: "" for field in _CREDENTIAL_FIELDS}
    if not path.exists():
        return empty, "absent"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return empty, "invalid"
    if not isinstance(payload, Mapping):
        return empty, "invalid"
    return (
        {field: _normalize(payload.get(field)) for field in _CREDENTIAL_FIELDS},
        "valid",
    )


def _missing_fields(values: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(field for field in _CREDENTIAL_FIELDS if not values[field])


def _normalize(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
