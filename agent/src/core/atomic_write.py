"""THE durable file write of this repo (D306): unique temp in the destination directory, every byte written, fsync,
``os.replace``, directory fsync. Stdlib only: no dependency on Trade or model clients.

Vendored from Trade (this repo cannot import ``trade_integrations``; Trade's
``tests/test_vibetrading_atomic_write_vendored_equal.py`` pins the behaviour equal): ``context.hub.atomic_write_bytes``
(mode / destination permission bits / short writes) and the ``NonFiniteJSONError`` .. ``strict_dumps`` block of
``trade_integrations/finite.py`` (D479: stored JSON refuses a NaN / inf and names the field). Owned runtime state fails
loudly on I/O errors; ``on_fsync_error`` is the one explicit degradation (the diagnostic trace keeps a sidecar whose
fsync is unsupported).
"""
from __future__ import annotations

import json
import logging
import math
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_TRANSIENT_WINERRORS = (5, 32)  # ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION
_REPLACE_ATTEMPTS = 6
_REPLACE_BACKOFF = (0.025, 0.05, 0.1, 0.2, 0.4)  # seconds; len == attempts - 1


def _is_transient_windows_error(exc: OSError) -> bool:
    """True for the Windows access/sharing race on ``os.replace`` (``winerror`` is only set on Windows)."""
    return getattr(exc, "winerror", None) in _TRANSIENT_WINERRORS


def replace_with_retry(tmp: Path, target: Path) -> None:
    """``os.replace`` retried on the Windows concurrent-access race (a reader holding ``target`` open makes the
    rename fail with WinError 5/32). POSIX never raises these, so off-Windows this runs exactly once; a
    non-transient error re-raises immediately and the last transient one after the budget."""
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(tmp, target)
            return
        except OSError as exc:
            if not _is_transient_windows_error(exc) or attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_BACKOFF[attempt])


def fsync_directory(directory: Path) -> None:
    if os.name == "nt":  # a directory cannot be opened for fsync on Windows
        return
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    durable: bool = True,
    mode: int | None = None,
    in_place_fallback: bool = False,
    on_fsync_error: Callable[[OSError], None] | None = None,
) -> None:
    """Write ``data`` to ``path`` so a concurrent reader sees the old file or the new one, never a torn half, and a
    kill or full disk leaves no partial file under the real name and no stray temp.

    ``mode`` makes the file exactly that mode from the first byte (a secret is 0600, never 0644 for a moment); without
    it the destination's own mode is kept (a new file is 0644). ``durable=False`` skips both fsyncs.
    ``in_place_fallback``: when no temp file can be created beside ``path`` (a read-only parent, such as a bind-mounted
    ``.env``), write in place instead, still enforcing ``mode``; atomicity is given up for that one edge.
    ``on_fsync_error``: report an fsync failure (file or directory) and carry on instead of raising."""
    if mode is not None and (isinstance(mode, bool) or not isinstance(mode, int) or not 0 <= mode <= 0o777):
        raise ValueError("mode must be permission bits between 0 and 0o777")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o644 if mode is None else mode)
    except OSError:
        if not in_place_fallback:
            raise
        _write_in_place(path, data, mode)
        return
    try:
        if hasattr(os, "fchmod"):  # absent on Windows, where ACLs govern access
            if mode is not None:
                os.fchmod(fd, mode)
            else:
                try:
                    os.fchmod(fd, os.stat(path).st_mode & 0o7777)  # a 0600 file stays 0600
                except FileNotFoundError:
                    pass
        # `os.write` may write fewer bytes than asked (a full disk can end a write short without an error):
        # loop until every byte is down so a short write is never replaced over the good file as complete.
        pending = memoryview(data)
        while pending:
            written = os.write(fd, pending)
            if written <= 0:
                raise OSError("atomic write made no progress")
            pending = pending[written:]
        if durable:
            _fsync(lambda: os.fsync(fd), on_fsync_error)
    except BaseException:
        os.close(fd)
        tmp.unlink(missing_ok=True)  # a failed write leaves the previous file and no stray temp
        raise
    os.close(fd)
    try:
        if mode is not None and not hasattr(os, "fchmod"):
            try:
                os.chmod(tmp, mode)  # best effort where there is no fchmod
            except OSError:
                pass
        replace_with_retry(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if durable:
        try:
            fsync_directory(path.parent)
        except OSError as exc:  # the rename already landed: an unsupported directory fsync only narrows its durability window
            if on_fsync_error is not None:
                on_fsync_error(exc)
            else:
                logger.debug("directory fsync unsupported on %s", path.parent, exc_info=True)


def _fsync(call: Callable[[], None], on_error: Callable[[OSError], None] | None) -> None:
    try:
        call()
    except OSError as exc:
        if on_error is None:
            raise
        on_error(exc)


def _write_in_place(path: Path, data: bytes, mode: int | None) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644 if mode is None else mode)
    try:
        pending = memoryview(data)
        while pending:
            pending = pending[os.write(fd, pending):]
    finally:
        os.close(fd)
    if mode is not None:
        try:
            os.chmod(path, mode)
        except OSError:
            pass


#: ``atomic_write_*`` keywords for a file that holds a credential: 0600 from the first byte, and written in place when
#: the directory is read-only (a bind-mounted ``.env``) rather than not at all.
SECRET_WRITE: dict[str, Any] = {"mode": 0o600, "in_place_fallback": True}


def atomic_write_text(path: Path, text: str, **kwargs: Any) -> None:
    """``atomic_write_bytes`` for UTF-8 text (no newline translation)."""
    atomic_write_bytes(path, text.encode("utf-8"), **kwargs)


# ---- durable JSON: refuse a non-finite number at the writer (D479) ----------------------------------------------
# vendored verbatim from trade_integrations/finite.py, pinned equal by Trade's tests/test_vibetrading_atomic_write_vendored_equal.py
class NonFiniteJSONError(ValueError):
    """A payload bound for durable JSON holds NaN / +-inf / a pandas NA or NaT. `path` names the field."""


def _is_bad_scalar(v: Any) -> bool:
    if isinstance(v, float):
        return not math.isfinite(v)
    np, pd = sys.modules.get("numpy"), sys.modules.get("pandas")  # a numpy / pandas value implies the module is loaded
    if np is not None and isinstance(v, np.floating):
        return not math.isfinite(float(v))
    if np is not None and isinstance(v, np.datetime64):
        return bool(np.isnat(v))
    return pd is not None and (v is pd.NA or v is pd.NaT)


def non_finite_path(obj: Any, default: Callable[[Any], Any] | None = None, *, _path: str = "", _seen: frozenset = frozenset()) -> str | None:
    """`'rows[3].close = nan'` for the first non-finite number under `obj`, else None (what `json.dumps` would write
    as a bare NaN / Infinity token). `default` is the caller's `json.dumps(default=)` so an object it converts is walked too."""
    here = _path or "<payload>"
    if _is_bad_scalar(obj):
        return f"{here} = {obj!r}"
    if obj is None or isinstance(obj, (str, int, bool)) or id(obj) in _seen:
        return None
    seen = _seen | {id(obj)}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _is_bad_scalar(k):
                return f"{here} key {k!r}"
            hit = non_finite_path(v, default, _path=f"{_path}.{k}" if _path else str(k), _seen=seen)
            if hit:
                return hit
        return None
    if isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            hit = non_finite_path(v, default, _path=f"{_path}[{i}]", _seen=seen)
            if hit:
                return hit
        return None
    if default is not None:
        try:
            converted = default(obj)
        except (TypeError, ValueError, AttributeError):  # the walk only names a location; dumps reports the real conversion error
            return None
        return non_finite_path(converted, default, _path=_path, _seen=seen)
    return None


def strict_dumps(obj: Any, **kwargs: Any) -> str:
    """THE `json.dumps` for anything stored (a hub file, ledger, state, a JSON column), D479: a NaN / +-inf / pandas NA or
    NaT anywhere in `obj` raises `NonFiniteJSONError` naming the field, instead of persisting the bare token `NaN` that a
    browser `JSON.parse` rejects, and instead of nulling it. The producer turns a missing value into None (D410).
    Finite output is byte-identical to `json.dumps` with the same arguments."""
    if "allow_nan" in kwargs:
        raise TypeError("strict_dumps always refuses non-finite numbers; do not pass allow_nan")
    user_default = kwargs.get("default")
    if user_default is not None:
        def default(o: Any) -> Any:  # a default= like `str` would otherwise turn np.float32('nan') / pd.NA into a string
            if _is_bad_scalar(o):
                raise NonFiniteJSONError(repr(o))
            return user_default(o)

        kwargs["default"] = default
    try:
        return json.dumps(obj, allow_nan=False, **kwargs)
    except (ValueError, NonFiniteJSONError) as exc:
        where = non_finite_path(obj, user_default)
        if where is None:
            raise
        raise NonFiniteJSONError(f"refusing to store a non-finite number in JSON: {where} (a missing value is None, fixed where it is produced, D479/D410)") from exc


def write_json_atomic(
    path: Path,
    payload: Any,
    *,
    indent: int | None = 2,
    ensure_ascii: bool = False,
    sort_keys: bool = False,
    default: Callable[[Any], Any] | None = None,
    newline: bool = False,
    **write_kwargs: Any,
) -> None:
    """``strict_dumps`` + ``atomic_write_text``: THE writer of a stored JSON file. A NaN / inf anywhere in ``payload``
    raises ``NonFiniteJSONError`` naming the field and the previous file is left untouched. ``write_kwargs`` are
    ``atomic_write_bytes``'s (``mode``, ``durable``, ...)."""
    kwargs: dict[str, Any] = {"indent": indent, "ensure_ascii": ensure_ascii, "sort_keys": sort_keys}
    if default is not None:
        kwargs["default"] = default
    text = strict_dumps(payload, **kwargs) + ("\n" if newline else "")
    atomic_write_text(path, text, **write_kwargs)
