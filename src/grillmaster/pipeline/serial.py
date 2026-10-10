"""Serial runs: several sources back to back, each seeding the next.

Every project's final directory (its archived one when `[paths] archive` is
set) becomes the next project's `--parent`, so names and terms stay
consistent across episodes; the pre-pass reads the parent's briefing
through `ProjectLayout(parent).effective_briefing()`. The
chain stops at the first failure and logs the command that resumes it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.events.bus import EventBus
from grillmaster.events.types import BatchItemStarted
from grillmaster.pipeline.registry import PIPELINE, Pipeline
from grillmaster.pipeline.runner import ProjectRun
from grillmaster.project.errors import ArchivedProjectError
from grillmaster.project.layout import ProjectLayout

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from grillmaster.config.load import LoadedConfig
    from grillmaster.core.source_id import SourceId
    from grillmaster.events.bus import EventSink
    from grillmaster.stages.base import RunOptions


@dataclass(slots=True)
class SerialRun:
    """Run `sources` in order with the flags of `template`.

    Item `i` runs `template` with `source=sources[i]` and `parent` set to
    the previous item's final directory (`template.parent` for the first).
    Stateful on purpose: after a failure, calling `run` again (the
    dashboard's retry) continues from the failed project, which is itself
    resumable, instead of restarting the chain whose earlier projects may
    already be archived away. A project whose packaging failed after its
    archive move (its `ProjectRun.archived`) counts as done (its source is
    refused once archived): the chain continues after it and `grill package`
    finishes it. So does an item whose source was already archived before
    the chain reached it (`ArchivedProjectError`, raised before any work):
    its archived directory seeds the next item.
    """

    sources: tuple[SourceId, ...]
    template: RunOptions
    position: int = 0
    parent: Path | None = None

    def __post_init__(self) -> None:
        # Fail on a bad chain before the first project spends anything.
        if not self.sources:
            raise ValueError("A serial run needs at least one source")
        counts = Counter(source.video_id for source in self.sources)
        if duplicates := sorted(vid for vid, count in counts.items() if count > 1):
            raise ValueError(f"Duplicate sources: {', '.join(duplicates)}")
        if self.template.break_after is not None:
            raise ValueError("A serial run cannot stop at --break-after")
        if self.parent is None:
            self.parent = self.template.parent

    def run(
        self,
        loaded: LoadedConfig,
        *,
        sinks: Sequence[EventSink],
        pipeline: Pipeline = PIPELINE,
    ) -> ProjectLayout | None:
        """Run the remaining projects; returns the last one's final layout
        (`None` when none remained). Raises the failing project's error."""
        total = len(self.sources)
        bus = EventBus(sinks)
        final: ProjectLayout | None = None
        while self.position < total:
            source = self.sources[self.position]
            bus.emit(BatchItemStarted(self.position + 1, total, str(source)))
            options = replace(self.template, source=source, parent=self.parent)
            item = ProjectRun(loaded, options, pipeline)
            try:
                final = item.run(sinks)
            except ArchivedProjectError as error:
                logger.info(
                    f"Serial {self.position + 1}/{total}: {source} is already "
                    f"archived at {error.root}; continuing after it"
                )
                final = ProjectLayout(error.root)
            except BaseException:
                if item.archived is not None:
                    self.parent = item.archived.root
                    self.position += 1
                resume = (
                    f" Resume with the original flags plus: {self.resume_command()}"
                    if self.position < total
                    else ""
                )
                logger.error(f"Serial run stopped at {source}.{resume}")
                raise
            self.parent = final.root
            self.position += 1
            logger.success(
                f"Serial {self.position}/{total} complete: {source} -> {final.root}"
            )
        return final

    def resume_command(self) -> str:
        """The `grill serial` line continuing the chain from the current item
        (without the run flags, which the user repeats). Sources are full
        URLs: a bare ID may parse as another platform (an Abema slot ID
        starting with `sh` reads as TVer)."""
        parts = [
            "grill",
            "serial",
            *(source.url for source in self.sources[self.position :]),
        ]
        if self.parent is not None:
            parts += ["--parent", str(self.parent)]
        return " ".join(f'"{part}"' if " " in part else part for part in parts)
