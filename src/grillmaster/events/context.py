"""Which stage and agent task the current code runs on behalf of.

Kept in contextvars, so it follows the call stack rather than the thread:
worker pools pass it on with `contextvars.copy_context().run(...)`. The
loguru patcher copies it into every record's `extra`, which is how log lines
are attributed to a stage or task.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Iterator
    from contextlib import AbstractContextManager

    from loguru import Record

_stage: ContextVar[str | None] = ContextVar("grill_stage", default=None)
_task: ContextVar[str | None] = ContextVar("grill_task", default=None)


def stage_scope(key: str) -> AbstractContextManager[None]:
    return _scope(_stage, key)


def task_scope(name: str) -> AbstractContextManager[None]:
    return _scope(_task, name)


def current_stage() -> str | None:
    return _stage.get()


def current_task() -> str | None:
    return _task.get()


def patch_record(record: Record) -> None:
    """Loguru patcher: `extra["stage"]` / `extra["task"]` from the scopes."""
    record["extra"]["stage"] = _stage.get()
    record["extra"]["task"] = _task.get()


def add_sink(sink: Any, **options: Any) -> int:
    """`logger.add` for every sink of the process (ruff bans the direct call).

    `diagnose=False`: a traceback never prints its frames' local values, which
    held the Azure key in the ASR stage's frames.
    """
    return logger.add(sink, diagnose=False, **options)  # noqa: TID251


def install_log_context() -> None:
    """Make every loguru record carry the current stage and task.

    Replaces any patcher configured earlier on the global logger.
    """
    logger.configure(patcher=patch_record)


@contextmanager
def _scope(var: ContextVar[str | None], value: str) -> Iterator[None]:
    token = var.set(value)
    try:
        yield
    finally:
        var.reset(token)
