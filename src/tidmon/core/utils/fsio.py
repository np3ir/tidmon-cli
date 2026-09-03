"""Crash-safe filesystem writes — adapted from tiddl-elvigilante
(tiddl/core/utils/fsio.py::atomic_write_bytes).

A plain `open(path, "w")` truncates the target before writing, so a crash, full
disk, or power loss mid-write leaves a truncated/empty file. For secrets
(auth.json) that silently wipes the user's session; for config.json it corrupts
settings. `atomic_write_bytes` writes to a temp file in the SAME directory,
flushes + fsyncs it, sets restrictive POSIX permissions on the still-private
temp file, and only then publishes it with `os.replace` (atomic on every
platform tidmon supports). The chmod happens BEFORE publish so the data is never
briefly world-readable.
"""
from __future__ import annotations

import os
import tempfile
from logging import getLogger
from pathlib import Path
from typing import Optional

log = getLogger(__name__)


def atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    chmod_posix: Optional[int] = None,
) -> None:
    """Atomically write `data` to `path` (temp-in-same-dir + fsync + os.replace).

    `chmod_posix`, when given, is applied to the temp file's descriptor BEFORE
    it is published, so the final file is never briefly world-readable. Ignored
    on non-POSIX platforms (no os.fchmod)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    fd_owned_by_file_object = False
    try:
        if chmod_posix is not None and os.name == "posix":
            try:
                os.fchmod(fd, chmod_posix)
            except OSError:
                pass
        with os.fdopen(fd, "wb") as f:
            fd_owned_by_file_object = True
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        # Close the descriptor if fdopen never took ownership, then remove the
        # leftover temp file (Windows cannot unlink a still-open file).
        if not fd_owned_by_file_object:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, text: str, *, chmod_posix: Optional[int] = None) -> None:
    """UTF-8 text convenience wrapper over :func:`atomic_write_bytes`."""
    atomic_write_bytes(path, text.encode("utf-8"), chmod_posix=chmod_posix)
