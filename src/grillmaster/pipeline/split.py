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
from grillmaster.project.state import PartOrigin, ProjectState, Section, SideTasks
from grillmaster.project.store import (
    load_state,
    locate_project,
    project_lock,
    remove_project,
    save_state,
)
from grillmaster.stages.base import require
from grillmaster.stages.combine import STAGE as COMBINE
from grillmaster.stages.combine import write_official_subtitles

if TYPE_CHECKING:
    from collections.abc import Sequence

    from grillmaster.config.load import LoadedConfig
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


def part_source(source: SourceId, part: int) -> SourceId:
    return SourceId(source.platform, f"{source.video_id}p{part}")


def part_sources(source: SourceId, parts: int) -> tuple[SourceId, ...]:
    return tuple(part_source(source, part) for part in range(1, parts + 1))


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
    loaded: LoadedConfig, source: SourceId, parts: int, *, ffmpeg: FfmpegRunner
) -> None:
    """Seed the parts of the downloaded project of `source` that do not exist yet."""
    layout = ProjectLayout.for_id(loaded.projects_root, source.video_id)
    state = load_state(layout)
    _refuse_past_download(state)
    full = require(layout.full_video, StageKey.DOWNLOAD)
    sections = part_sections(duration(ffmpeg, full), parts)
    for part, section in enumerate(sections, 1):
        seeded = _part_state(state, section, part, parts)
        target = ProjectLayout.for_id(loaded.projects_root, seeded.id)
        if target.project_json.exists():
            continue
        started = time.monotonic()
        target.root.mkdir(parents=True, exist_ok=True)
        write_official_subtitles(loaded.config, layout, target, section)
        cut(ffmpeg, full, target.video, start=section.start, end=section.end)
        if layout.poster.exists():
            shutil.copy2(layout.poster, target.poster)
        seeded.mark_done(
            StageKey.COMBINE,
            elapsed_s=time.monotonic() - started,
            params=COMBINE.params(loaded.config),
        )
        save_state(target, seeded)
        logger.info(f"Seeded part {part}/{parts}: {target.root}")


def _refuse_compilation(state: ProjectState) -> None:
    """A compilation's segments and identity have no place in a part."""
    if state.compilation is not None:
        raise SplitError(f"{state.id} is a compilation; --parts cannot split it")


def _refuse_past_download(state: ProjectState) -> None:
    """A source project that went on has work a split would discard."""
    _refuse_compilation(state)
    if state.is_done(StageKey.COMBINE):
        raise SplitError(
            f"{state.id} is already past the download; run `grill reset {state.id} "
            "--from combine` first"
        )


def _part_state(
    state: ProjectState, section: Section, part: int, parts: int
) -> ProjectState:
    note = _PART_NOTE.format(part=part, parts=parts)
    hint = state.translation_hint
    title = state.source.title
    seeded = ProjectState.create(
        part_source(state.source_id, part),
        translation_hint=f"{hint}\n{note}" if hint else note,
    )
    seeded.name = f"{state.name}_{part}" if state.name else None
    seeded.broadcast_date = state.broadcast_date
    seeded.source = state.source.model_copy(
        update={"title": f"{title} Part {part}" if title else None}
    )
    seeded.section = section
    seeded.part_of = PartOrigin(id=state.id, parts=parts)
    seeded.stages = {
        key: state.stages[key] for key in (StageKey.METADATA, StageKey.DOWNLOAD)
    }
    seeded.side_tasks = SideTasks(date_research=state.side_tasks.date_research)
    return seeded


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
    serial: SerialRun | None = field(default=None, init=False)

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
        if layout.project_json.is_file():
            # Before the download: resumed by its ID, a compilation reaches here.
            _refuse_compilation(load_state(layout))
        if not self._all_parts_exist(loaded):
            # The source run stops at the download, so its own check stops there
            # too; a run the parts could not finish fails before downloading.
            pipeline.check(self.template, loaded.config, loaded.secrets)
            ProjectRun(
                loaded, replace(self.template, break_after=StageKey.DOWNLOAD), pipeline
            ).run(sinks)
            with project_lock(root, source.video_id):
                split_project(loaded, source, self.parts, ffmpeg=self.ffmpeg)
        if layout.project_json.is_file():
            with project_lock(root, source.video_id):
                _refuse_past_download(load_state(layout))
                remove_project(layout)
            logger.info(f"Removed {layout.root}; its parts hold the video now")

    def _all_parts_exist(self, loaded: LoadedConfig) -> bool:
        """Whether every part exists, local or archived; a project under a part
        ID that is not from this split is refused. One ID past the last is
        checked too: it can only come from an earlier, larger split."""
        source = self.template.source
        expected = PartOrigin(id=source.video_id, parts=self.parts)
        found = 0
        for candidate in part_sources(source, self.parts + 1):
            location = locate_project(
                loaded.projects_root,
                candidate.video_id,
                archive_root=loaded.config.paths.archive,
            )
            if location is None:
                continue
            if load_state(location.layout).part_of != expected:
                raise SplitError(
                    f"{location.layout.root} is not part of a {self.parts}-part "
                    f"split of {source}; remove it to split differently"
                )
            found += 1
        return found == self.parts
