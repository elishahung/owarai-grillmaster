"""Non-interactive sinks: a compact console log and a replayable JSONL file."""

from __future__ import annotations

import dataclasses
import json
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, assert_never

from loguru import logger

from grillmaster.events.context import current_stage, current_task
from grillmaster.events.types import (
    AgentActivity,
    AgentSessionFinished,
    AgentSessionStarted,
    BatchItemStarted,
    LogLine,
    ProgressAdvanced,
    ProgressFinished,
    ProgressStarted,
    RunFinished,
    RunOutcome,
    RunStarted,
    SessionOutcome,
    StepCompleted,
    StepFailed,
    StepSkipped,
    StepStarted,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from types import TracebackType

    from grillmaster.events.types import Event


class ConsoleSink:
    """Logs each event as one compact loguru line.

    `LogLine` events are dropped: they originate from loguru, so logging them
    again would duplicate (or loop through a loguru-to-bus bridge).
    """

    def emit(self, event: Event) -> None:
        described = describe(event)
        if described is not None:
            level, message = described
            logger.log(level, message)


def describe(event: Event) -> tuple[str, str] | None:  # noqa: PLR0911
    """`(loguru level, message)` for an event; `None` when it is not logged."""
    match event:
        case RunStarted(project=project, plan=plan):
            enabled = sum(entry.enabled for entry in plan)
            return "INFO", f"Run {project}: {enabled}/{len(plan)} steps planned"
        case RunFinished(outcome=RunOutcome.COMPLETED):
            return "SUCCESS", "Run completed"
        case RunFinished(error=error):
            return "ERROR", f"Run failed: {error}"
        case BatchItemStarted(index=index, total=total, source=source):
            return "INFO", f"Batch item {index}/{total}: {source}"
        case StepStarted(key=key):
            return "INFO", f"[{key}] started"
        case StepCompleted(key=key, elapsed=elapsed, result=result):
            suffix = f": {result}" if result else ""
            return "SUCCESS", f"[{key}] done in {elapsed:.1f}s{suffix}"
        case StepSkipped(key=key, reason=reason):
            return "INFO", f"[{key}] skipped ({reason})"
        case StepFailed(key=key, error=error):
            return "ERROR", f"[{key}] failed: {error}"
        case ProgressStarted(label=label, total=total):
            size = f" ({total:g})" if total is not None else ""
            return "DEBUG", f"{label}{size}"
        case ProgressAdvanced() | ProgressFinished():
            return None
        case AgentSessionStarted(
            task=task, backend=backend, model=model, effort=effort
        ):
            spec = f"{backend}/{model}" + (f"/{effort}" if effort else "")
            return "INFO", f"<{task}> session started on {spec}"
        case AgentActivity(task=task, kind=kind, summary=summary):
            return "DEBUG", f"<{task}> {kind}: {summary}"
        case AgentSessionFinished(
            task=task, outcome=outcome, elapsed=elapsed, repairs=repairs
        ):
            level = "INFO" if outcome is SessionOutcome.OK else "WARNING"
            repaired = f", {repairs} repair(s)" if repairs else ""
            return level, f"<{task}> session {outcome} in {elapsed:.1f}s{repaired}"
        case LogLine():
            return None
        case _:
            assert_never(event)


class JsonlSink:
    """Appends one JSON object per event: `ts`, `type`, `stage`, `task`, then
    the event's fields.

    `stage` / `task` come from the emitting thread's scopes (sinks run
    synchronously there); an event's own `stage` / `task` fields win. Every
    event except the high-volume `AgentActivity` flushes the file, so it can
    be tailed or replayed after a crash with at most trailing activity lines
    lost. Thread-safe; call `close` (or use it as a context manager), after
    which events are dropped.
    """

    def __init__(
        self, path: Path, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or _utc_now
        self._lock = threading.Lock()
        self._handle = path.open("a", encoding="utf-8", newline="\n")

    def emit(self, event: Event) -> None:
        line = json.dumps(
            {
                "ts": self._clock().isoformat(),
                "type": type(event).__name__,
                "stage": current_stage(),
                "task": current_task(),
                # `asdict` recurses into nested dataclasses and containers.
                **dataclasses.asdict(event),
            },
            ensure_ascii=False,
            default=str,
        )
        with self._lock:
            # Side tasks may still report after the run closed the log.
            if self._handle.closed:
                return
            self._handle.write(line + "\n")
            if not isinstance(event, AgentActivity):
                self._handle.flush()

    def close(self) -> None:
        with self._lock:
            self._handle.close()

    def __enter__(self) -> JsonlSink:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _utc_now() -> datetime:
    return datetime.now(UTC)
