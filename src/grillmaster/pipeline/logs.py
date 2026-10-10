"""A run's project-local logs: `logs/run-<ts>.log` and `logs/events-<ts>.jsonl`.

The text log is a loguru file sink taking every record (DEBUG and up, each
tagged with the stage/task scope `install_log_context` puts in its `extra`);
the JSONL file is a `JsonlSink` on the run's event bus. Both are opened on
construction and hold their files until `close`, which the archive step
calls before it moves the project directory.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.events.sinks import JsonlSink

if TYPE_CHECKING:
    from datetime import datetime

    from loguru import Record

    from grillmaster.events.bus import EventBus
    from grillmaster.project.layout import ProjectLayout

_TEXT_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {extra[_scope]}{message}\n"
    "{exception}"
)


def _format(record: Record) -> str:
    extra = record["extra"]
    scope = "/".join(part for part in (extra.get("stage"), extra.get("task")) if part)
    extra["_scope"] = f"[{scope}] " if scope else ""
    return _TEXT_FORMAT


class ProjectLogs:
    def __init__(
        self, events: EventBus, layout: ProjectLayout, started_at: datetime
    ) -> None:
        self._events = events
        self._handler: int | None = logger.add(
            layout.run_log(started_at),
            level="DEBUG",
            format=_format,
            encoding="utf-8",
        )
        self._jsonl: JsonlSink | None = JsonlSink(layout.events_log(started_at))
        events.subscribe(self._jsonl)

    def close(self) -> None:
        """Stop logging into the project; later calls do nothing."""
        if self._jsonl is not None:
            self._events.unsubscribe(self._jsonl)
            self._jsonl.close()
            self._jsonl = None
        if self._handler is not None:
            logger.remove(self._handler)
            self._handler = None
