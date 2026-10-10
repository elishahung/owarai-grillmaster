"""A run's project-local logs: `logs/run-<ts>.log` and `logs/events-<ts>.jsonl`.

The text log is a loguru file sink taking every record (DEBUG and up, each
tagged with the stage/task scope `install_log_context` puts in its `extra`);
the JSONL file is a `JsonlSink` on the run's event bus. Both are opened on
construction and hold their files until `close`. The archive step closes them
before it moves the project directory and then `relocate`s them, so the run
keeps appending to the same two files wherever the project now lives.
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
        self._started_at = started_at
        self._handler: int | None = None
        self._jsonl: JsonlSink | None = None
        self.relocate(layout)

    def relocate(self, layout: ProjectLayout) -> None:
        """(Re)open this run's logs under `layout`, appending; open ones are
        closed first."""
        self.close()
        self._handler = logger.add(
            layout.run_log(self._started_at),
            level="DEBUG",
            format=_format,
            encoding="utf-8",
        )
        self._jsonl = JsonlSink(layout.events_log(self._started_at))
        self._events.subscribe(self._jsonl)

    def close(self) -> None:
        """Stop logging into the project; later calls do nothing."""
        if self._jsonl is not None:
            self._events.unsubscribe(self._jsonl)
            self._jsonl.close()
            self._jsonl = None
        if self._handler is not None:
            logger.remove(self._handler)
            self._handler = None
