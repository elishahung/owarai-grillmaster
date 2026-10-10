"""Filesystem helpers shared by every artifact writer."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Iterator

# A directory is built as `<name>.partial` (`staged_dir`); the one it replaces
# waits as `<name>.old` until the new one is in place.
STAGING_SUFFIX = ".partial"
_BACKUP_SUFFIX = ".old"
# How often `exclusive_lock` retries a lock file someone else holds.
_LOCK_POLL_S = 0.05

# Windows refuses to replace a file another process holds open (a TUI preview,
# an antivirus scan); those handles are short-lived, so retry briefly.
_REPLACE_ATTEMPTS = 5
_REPLACE_BACKOFF_S = 0.05


def partial_path(path: Path) -> Path:
    """The dot-prefixed temporary name a tool writes `path` under before
    renaming it into place (`.<name>.partial`)."""
    return path.with_name(f".{path.name}.partial")


def atomic_write_text(path: Path, text: str) -> None:
    """Write `text` so readers never observe a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        _replace(Path(tmp), path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _replace(source: Path, target: Path) -> None:
    for attempt in range(1, _REPLACE_ATTEMPTS + 1):
        try:
            source.replace(target)
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS:
                raise
            time.sleep(_REPLACE_BACKOFF_S * attempt)
        else:
            return


def staging_dir(destination: Path) -> Path:
    """Where `staged_dir` builds `destination` (`<name>.partial`)."""
    return destination.with_name(f"{destination.name}{STAGING_SUFFIX}")


@contextmanager
def staged_dir(destination: Path, *, create: bool = True) -> Iterator[Path]:
    """A staging directory that replaces `destination` when the body succeeds.

    A staging directory left by a crash is removed first. With `create` the
    body gets it empty; otherwise only its parent exists (for
    `shutil.copytree`). When the body or the swap fails, the staging
    directory is removed and `destination` is untouched. The swap renames an
    existing `destination` aside to `<name>.old`, restores it when the final
    rename fails, and removes it (best effort, with a warning) afterwards.
    """
    staging = staging_dir(destination)
    if staging.exists():
        logger.warning(f"Removing a stale staging directory: {staging}")
        shutil.rmtree(staging)
    if create:
        staging.mkdir(parents=True)
    else:
        staging.parent.mkdir(parents=True, exist_ok=True)
    try:
        yield staging
        _swap_dir(staging, destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _swap_dir(staging: Path, destination: Path) -> None:
    backup = destination.with_name(f"{destination.name}{_BACKUP_SUFFIX}")
    if destination.exists():
        logger.info(f"Replacing the existing {destination}")
        if backup.exists():
            shutil.rmtree(backup)
        destination.rename(backup)
    try:
        staging.replace(destination)
    except BaseException:
        if backup.exists():
            backup.rename(destination)
        raise
    if backup.exists():
        try:
            shutil.rmtree(backup)
        except OSError as error:
            logger.warning(f"Could not remove the replaced {backup}: {error}")


@contextmanager
def exclusive_lock(path: Path, *, timeout: float) -> Iterator[None]:
    """Hold the lock file `path` while the body runs.

    The file is created with `O_CREAT | O_EXCL`, so only one holder (thread
    or process) gets it; others retry for up to `timeout` seconds, then
    raise `TimeoutError`. A holder that crashed leaves the file behind, and
    the error says to delete it.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, PermissionError) as error:
            # Windows denies access to a lock file its holder is deleting.
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"{path} stayed locked for {timeout:g}s ({error.strerror}); "
                    "delete it if no other run holds it"
                ) from error
            time.sleep(_LOCK_POLL_S)
        else:
            break
    os.close(fd)
    try:
        yield
    finally:
        path.unlink(missing_ok=True)
