"""Compilations at the pipeline level: a manifest as the identity of a run,
and `grill plan`, which writes the manifests of a playlist.

`grill <manifest.json>` runs the project `manifest.id` like any source; the
manifest's content becomes `ProjectState.compilation` when the project is
created, and a later run with a manifest that says otherwise is refused
(`pipeline.projects.open_project`) instead of rewriting a started project.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from grillmaster.core.json_artifact import write_model
from grillmaster.core.source_id import SourceId, is_url, platform_of_url
from grillmaster.project.state import Compilation
from grillmaster.sources.compilation import (
    CompilationError,
    groups_for_minutes,
    list_playlist,
    manifest_path,
    plan_groups,
    plan_manifests,
)
from grillmaster.sources.registry import source_platform
from grillmaster.sources.ytdlp import shared_options

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.config.load import LoadedConfig
    from grillmaster.core.source_id import Platform
    from grillmaster.sources.compilation import CompilationManifest, PlaylistEntry
    from grillmaster.sources.ytdlp import YtDlp

_ONE_SIZE = "Give exactly one of --episodes and --minutes"


def manifest_source(manifest: CompilationManifest) -> SourceId:
    """The run source of a manifest: its ID on its entries' platform."""
    return SourceId(manifest.platform, manifest.id)


def manifest_compilation(manifest: CompilationManifest) -> Compilation:
    return Compilation(
        title=manifest.title,
        entries=[source.video_id for source in manifest.sources],
        series=manifest.series,
        channel=manifest.channel,
        broadcast_date=manifest.broadcast_date,
    )


@dataclass(frozen=True, slots=True)
class PlanRequest:
    """What `grill plan` asks for; exactly one of `episodes` / `minutes`."""

    url: str
    id_prefix: str
    out_dir: Path
    episodes: int | None = None
    minutes: float | None = None
    title: str | None = None
    series: str | None = None
    channel: str | None = None
    items: str | None = None


@dataclass(frozen=True, slots=True)
class PlannedEpisode:
    manifest: CompilationManifest
    path: Path
    entries: tuple[PlaylistEntry, ...]

    @property
    def duration(self) -> float:
        return sum(entry.duration for entry in self.entries)


def plan_compilation(
    loaded: LoadedConfig, request: PlanRequest, *, ytdlp: YtDlp
) -> tuple[PlannedEpisode, ...]:
    """List the playlist and cut it into manifests; nothing is written."""
    if (request.episodes is None) == (request.minutes is None):
        raise CompilationError(_ONE_SIZE)
    platform = source_platform(_playlist_platform(request.url))
    playlist = list_playlist(
        ytdlp,
        request.url,
        options=shared_options(platform, loaded.config.paths.cookies),
        items=request.items,
    )
    durations = [entry.duration for entry in playlist.entries]
    groups = plan_groups(durations, _episodes(request, durations))
    title = request.title or playlist.title
    if title is None:
        raise CompilationError("The playlist has no title; pass --title")
    manifests = plan_manifests(
        playlist,
        groups,
        id_prefix=request.id_prefix,
        title=title,
        series=request.series,
        channel=request.channel,
    )
    return tuple(
        PlannedEpisode(
            manifest=manifest,
            path=manifest_path(request.out_dir, manifest),
            entries=tuple(playlist.entries[index] for index in group),
        )
        for group, manifest in zip(groups, manifests, strict=True)
    )


def write_plan(episodes: tuple[PlannedEpisode, ...]) -> None:
    """Write every manifest, or none when any already exists: a manifest
    that started a project is that project's identity, so it is never
    overwritten."""
    if existing := [episode.path for episode in episodes if episode.path.exists()]:
        raise CompilationError(
            "Manifests already exist; remove them to plan again:\n  "
            + "\n  ".join(str(path) for path in existing)
        )
    for episode in episodes:
        write_model(episode.path, episode.manifest)


def _episodes(request: PlanRequest, durations: list[float]) -> int:
    if request.episodes is not None:
        return request.episodes
    if request.minutes is not None:
        return groups_for_minutes(durations, request.minutes)
    raise CompilationError(_ONE_SIZE)


def _playlist_platform(url: str) -> Platform:
    if not is_url(url):
        raise CompilationError(f"Not a playlist URL: {url}")
    if (platform := platform_of_url(url)) is None:
        raise CompilationError(f"Not a supported platform's playlist: {url}")
    return platform
