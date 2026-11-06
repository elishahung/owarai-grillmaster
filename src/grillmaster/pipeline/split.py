"""One long video as a chain of projects: `grill <src> --parts N`.

The source project runs through the download, then `split_project` cuts its
`full.mp4` into N equal sections, each seeded as its own project `<id>p<k>`:
metadata and download come over from the source, combine is done (`video.mp4`
and the shifted captions are written here, `ProjectState.section` records the
source timeline), so no part keeps a copy of the whole video. Once every part
exists the source project is removed and `SerialRun` takes over, each part
seeding the next.

Re-running the same command resumes: a part's `project.json` is written last,
so an interrupted split cuts only what is missing, and with every part in
place neither the download nor the cut runs again. Part IDs stay alphanumeric
because `SerialRun.resume_command` round-trips them through URLs.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.source_id import SourceId
from grillmaster.core.stage_key import StageKey
from grillmaster.media.ffmpeg import SubprocessFfmpegRunner
from grillmaster.media.probe import duration
from grillmaster.media.video import cut
from grillmaster.pipeline.registry import PIPELINE, Pipeline
from grillmaster.pipeline.runner import ProjectRun
from grillmaster.pipeline.serial import SerialRun
from grillmaster.project.layout import ProjectLayout
from grillmaster.project.state import (
    PartOrigin,
    ProjectState,
    Section,
    SideTasks,
    now,
)
from grillmaster.project.store import (
    load_state,
    locate_project,
    project_lock,
    remove_project,
    save_state,
)
from grillmaster.sources.official_subs import (
    normalize_official_subtitles,
    raw_caption_files,
)
from grillmaster.stages._common import tool_params
from grillmaster.stages.base import require

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from grillmaster.config.load import LoadedConfig
    from grillmaster.config.model import AppConfig
    from grillmaster.events.bus import EventSink
    from grillmaster.media.ffmpeg import FfmpegRunner
    from grillmaster.stages.base import RunOptions

_MIN_PARTS = 2
_PART_NOTE = (
    "This is part {part} of {parts} of one continuous video, so it may start "
    "and end mid-conversation."
)


class SplitError(RuntimeError):
    """A video cannot be split as asked; nothing was changed."""


def part_sources(source: SourceId, parts: int) -> tuple[SourceId, ...]:
    return tuple(
        SourceId(source.platform, f"{source.video_id}p{part}")
        for part in range(1, parts + 1)
    )


def part_sections(total: float, parts: int) -> tuple[Section, ...]:
    """Equal sections of `total` seconds; the first has no start and the last
    no end, so container rounding cannot lose either edge of the video."""
    length = total / parts
    return tuple(
        Section(
            start=index * length if index > 0 else None,
            end=(index + 1) * length if index < parts - 1 else None,
        )
        for index in range(parts)
    )


def split_project(
    layout: ProjectLayout,
    state: ProjectState,
    parts: int,
    *,
    ffmpeg: FfmpegRunner,
    projects_root: Path,
    config: AppConfig,
) -> None:
    """Seed the parts of the downloaded project at `layout` that do not exist yet."""
    if state.is_done(StageKey.COMBINE):
        raise SplitError(
            f"{state.id} is already past the download; run `grill reset {state.id} "
            "--from combine` to split it"
        )
    full = require(layout.full_video, StageKey.DOWNLOAD)
    sections = part_sections(duration(ffmpeg, full), parts)
    sources = part_sources(state.source_id, parts)
    for part, (source, section) in enumerate(zip(sources, sections, strict=True), 1):
        target = ProjectLayout.for_id(projects_root, source.video_id)
        if target.project_json.exists():
            continue
        started = time.monotonic()
        target.root.mkdir(parents=True, exist_ok=True)
        cut(ffmpeg, full, target.video, start=section.start, end=section.end)
        if config.features.official_subtitles:
            normalize_official_subtitles(
                raw_caption_files(layout.download_parts_dir),
                target.ja_official_srt,
                section_start=section.start,
                section_end=section.end,
            )
        if layout.poster.exists():
            shutil.copy2(layout.poster, target.poster)
        seeded = _part_state(state, source, section, part, parts)
        seeded.mark_done(
            StageKey.COMBINE,
            elapsed_s=time.monotonic() - started,
            params=tool_params("ffmpeg")(config),
        )
        save_state(target, seeded)
        logger.info(f"Seeded part {part}/{parts}: {target.root}")


def _part_state(
    state: ProjectState, source: SourceId, section: Section, part: int, parts: int
) -> ProjectState:
    note = _PART_NOTE.format(part=part, parts=parts)
    hint = state.translation_hint
    title = state.source.title
    return ProjectState(
        id=source.video_id,
        platform=source.platform,
        created_at=now(),
        name=f"{state.name}_{part}" if state.name else None,
        translation_hint=f"{hint}\n{note}" if hint else note,
        broadcast_date=state.broadcast_date,
        source=state.source.model_copy(
            update={"title": f"{title} Part {part}" if title else None}
        ),
        section=section,
        part_of=PartOrigin(id=state.id, part=part, parts=parts),
        stages={
            key: state.stages[key] for key in (StageKey.METADATA, StageKey.DOWNLOAD)
        },
        side_tasks=SideTasks(date_research=state.side_tasks.date_research),
    )


@dataclass(slots=True)
class SplitRun:
    """`grill <src> --parts N`: download, split, then the chain of parts.

    Item options come from `template`; its hint goes to the source project
    and from there, with the part note, to each part. Stateful like
    `SerialRun`: after a failure, calling `run` again (the dashboard's retry)
    continues the chain from the failed part.
    """

    template: RunOptions
    parts: int
    ffmpeg: FfmpegRunner = field(default_factory=SubprocessFfmpegRunner)
    serial: SerialRun | None = None

    def __post_init__(self) -> None:
        if self.parts < _MIN_PARTS:
            raise ValueError(f"--parts needs at least {_MIN_PARTS} parts")
        if self.template.break_after is not None:
            raise ValueError("--parts cannot stop at --break-after")
        if self.template.has_section:
            raise ValueError("--parts cannot be combined with --start/--to")

    def run(
        self,
        loaded: LoadedConfig,
        *,
        sinks: Sequence[EventSink],
        pipeline: Pipeline = PIPELINE,
    ) -> ProjectLayout | None:
        """Returns the last part's final layout; raises the failing step's error."""
        if self.serial is None:
            self._prepare(loaded, sinks, pipeline)
            self.serial = SerialRun(
                part_sources(self.template.source, self.parts),
                replace(self.template, hint=None),
            )
        return self.serial.run(loaded, sinks=sinks, pipeline=pipeline)

    def _prepare(
        self, loaded: LoadedConfig, sinks: Sequence[EventSink], pipeline: Pipeline
    ) -> None:
        source = self.template.source
        root = loaded.projects_root
        layout = ProjectLayout.for_id(root, source.video_id)
        if self._existing_parts(loaded) < self.parts:
            ProjectRun(
                loaded, replace(self.template, break_after=StageKey.DOWNLOAD), pipeline
            ).run(sinks)
            with project_lock(root, source.video_id):
                split_project(
                    layout,
                    load_state(layout),
                    self.parts,
                    ffmpeg=self.ffmpeg,
                    projects_root=root,
                    config=loaded.config,
                )
        if layout.project_json.is_file():
            with project_lock(root, source.video_id):
                remove_project(layout)
            logger.info(f"Removed {layout.root}; its parts hold the video now")

    def _existing_parts(self, loaded: LoadedConfig) -> int:
        """How many of the parts exist, local or archived. One ID past the last
        is checked too: it can only come from an earlier, larger split."""
        source = self.template.source
        count = 0
        for candidate in part_sources(source, self.parts + 1):
            location = locate_project(
                loaded.projects_root,
                candidate.video_id,
                archive_root=loaded.config.paths.archive,
            )
            if location is None:
                continue
            origin = load_state(location.layout).part_of
            if (
                origin is None
                or origin.id != source.video_id
                or origin.parts != self.parts
            ):
                raise SplitError(
                    f"{location.layout.root} is not part of a {self.parts}-part "
                    f"split of {source}; remove it to split differently"
                )
            count += 1
        return count
