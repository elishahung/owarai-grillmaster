"""Filesystem helpers shared by every artifact writer."""

from __future__ import annotations

import errno
import os
import shutil
import sys
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
BACKUP_SUFFIX = ".old"
# How often `exclusive_lock` retries a lock someone else holds.
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


# Written into a staging directory once its body succeeded: a `<name>.partial`
# holding it is a finished build whose swap failed, never a crash leftover.
COMPLETE_MARKER = ".complete"


class SwapError(OSError):
    """A finished staging directory could not be swapped into place, or one
    kept from an earlier failed swap is in the way; the message names it and
    the destination is left as it was."""


@contextmanager
def staged_dir(destination: Path, *, create: bool = True) -> Iterator[Path]:
    """A staging directory that replaces `destination` when the body succeeds.

    On entry, a `<name>.old` left by a crash mid-swap (destination missing)
    is restored; a staging directory a crash left mid-build is removed, but
    one holding `COMPLETE_MARKER` (a finished build kept by a failed swap)
    raises `SwapError` naming it: the user closes whatever held the
    destination and swaps it in by hand, or deletes it. With `create` the
    body gets the staging directory empty; otherwise only its parent exists
    (for `shutil.copytree`). When the body fails, the staging directory is
    removed and `destination` is untouched. Then the marker is written and
    the swap renames an existing `destination` aside to `<name>.old`, moves
    the staging directory in and removes the old one and the marker (best
    effort, with a warning); when a rename fails (a Windows lock), the old directory is
    restored, the staging directory is kept with its marker, and the raised
    `SwapError` names it.
    """
    staging = _clear_staging(destination)
    if create:
        staging.mkdir(parents=True)
    else:
        staging.parent.mkdir(parents=True, exist_ok=True)
    try:
        yield staging
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    (staging / COMPLETE_MARKER).touch()
    _swap_dir(staging, destination)


def check_replaceable(destination: Path) -> None:
    """Raise `SwapError` when `staged_dir(destination)` would fail: a kept
    finished build is in the way, or an existing `destination` cannot be
    renamed (a file in it held open), so a long build fails before it
    starts. Probes by renaming it to `<name>.old` and back."""
    _clear_staging(destination)
    if not destination.exists():
        return
    try:
        backup = _park_aside(destination)
    except OSError as error:
        raise SwapError(
            f"{destination} cannot be replaced ({error}); close whatever holds "
            "a file in it open and retry"
        ) from error
    backup.rename(destination)


def _clear_staging(destination: Path) -> Path:
    """Undo what a crash left around `destination`; returns its (absent)
    staging directory. Refuses a kept finished build (see `staged_dir`)."""
    _restore_backup(destination)
    staging = staging_dir(destination)
    if (staging / COMPLETE_MARKER).exists():
        raise SwapError(
            f"{staging} is a finished build that could not replace "
            f"{destination}: close whatever holds a file in {destination} open, "
            f"then move it into place by hand (deleting its {COMPLETE_MARKER}) "
            "or delete it, and retry"
        )
    if staging.exists():
        logger.warning(f"Removing a stale staging directory: {staging}")
        shutil.rmtree(staging)
    return staging


def _backup_dir(destination: Path) -> Path:
    """Where the swap parks the directory it replaces (`<name>.old`)."""
    return destination.with_name(f"{destination.name}{BACKUP_SUFFIX}")


def _restore_backup(destination: Path) -> None:
    """Put back the `<name>.old` a crashed swap left while `destination` is
    missing."""
    backup = _backup_dir(destination)
    if backup.exists() and not destination.exists():
        logger.warning(f"Restoring {destination} from an interrupted swap: {backup}")
        backup.rename(destination)


def _park_aside(destination: Path) -> Path:
    """Rename the existing `destination` to `<name>.old` (replacing a stale
    one) and return that path."""
    backup = _backup_dir(destination)
    if backup.exists():
        shutil.rmtree(backup)
    destination.rename(backup)
    return backup


def _swap_dir(staging: Path, destination: Path) -> None:
    backup: Path | None = None
    try:
        if destination.exists():
            logger.info(f"Replacing the existing {destination}")
            backup = _park_aside(destination)
        staging.replace(destination)
    except OSError as error:
        try:
            _restore_backup(destination)
        except OSError as restore_error:
            logger.error(
                f"Could not restore {destination} from {_backup_dir(destination)} "
                f"({restore_error}); the next staged build restores it"
            )
        raise SwapError(
            f"Could not move {staging} into place as {destination} ({error}); "
            f"the finished copy is kept at {staging}"
        ) from error
    # The swap succeeded; a marker left inside is only clutter.
    marker = destination / COMPLETE_MARKER
    try:
        marker.unlink()
    except OSError as error:
        logger.warning(f"Could not remove {marker} ({error}); delete it by hand")
    if backup is not None:
        try:
            shutil.rmtree(backup)
        except OSError as error:
            logger.warning(f"Could not remove the replaced {backup}: {error}")


@contextmanager
def exclusive_lock(path: Path, *, timeout: float) -> Iterator[None]:
    """Hold an OS-level exclusive lock on the file `path` while the body runs.

    The lock belongs to an open handle, so the OS drops it when its holder
    exits, crash included: a stale lock never blocks. Every holder (another
    process, or another call in this one) excludes the others; a held lock
    is retried for up to `timeout` seconds (0: tried once), then
    `TimeoutError` names the file. The file (and its parent) is created on
    first use and stays: deleting it would let a waiter lock an orphan.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    try:
        deadline = time.monotonic() + timeout
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"{path} stayed locked by another holder for {timeout:g}s"
                )
            time.sleep(_LOCK_POLL_S)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


# What locking a lock someone else holds fails with: EACCES from Windows
# `_locking`, EWOULDBLOCK (= EAGAIN) from POSIX `flock`.
_LOCK_HELD_ERRNOS = frozenset({errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK})


def _try_lock(fd: int) -> bool:
    try:
        _lock_nonblocking(fd)
    except OSError as error:
        if error.errno in _LOCK_HELD_ERRNOS:
            return False
        raise
    return True


if sys.platform == "win32":
    import msvcrt

    # One byte at offset 0 (past the end is fine) stands for the file.
    def _lock_nonblocking(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock_nonblocking(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
