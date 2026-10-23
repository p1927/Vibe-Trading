"""Durable native text replacement, extracted from SessionStore's unique-temp writer.

Owned runtime state fails loudly on I/O errors. Diagnostic trace writes retain their different,
explicit flush-only degradation policy. This owner has no dependency on Trade or model clients.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile


def fsync_directory(directory: Path) -> None:
    if os.name == "nt":  # Match the existing pending-action portability contract.
        return
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)
