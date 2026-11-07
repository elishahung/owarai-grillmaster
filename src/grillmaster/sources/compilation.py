"""Compilations: several platform videos translated as one project.

A manifest (`grill <manifest.json>`) names the project ID, its title and the
videos the download joins end to end, in order. `grill plan` lists a
playlist (`list_playlist`), cuts it into groups of balanced length
(`plan_groups`) and writes one manifest per group (`plan_manifests`).
Manifests are strict: an unknown key, a bad entry or entries from two
platforms fail loudly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from pydantic import Field, ValidationError, field_validator

from grillmaster.core.json_artifact import read_model
from grillmaster.core.models import StrictModel
from grillmaster.core.source_id import Platform, SourceId, parse_source
from grillmaster.sources.errors import SourceError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from grillmaster.sources.ytdlp import YtDlp

MANIFEST_SUFFIX = ".json"
# A group becomes one manifest, and a manifest joins at least two videos.
MIN_GROUP_SIZE = 2
_MIN_ID_DIGITS = 2


class CompilationError(SourceError):
    """A manifest or a playlist cannot become a compilation as asked."""


class CompilationManifest(StrictModel):
    """One compilation project: `id` becomes the project ID (alphanumeric,
    like a `--parts` part ID, since serial resume commands round-trip IDs
    through URLs), `title` its name, `entries` the videos as IDs or URLs."""

    id: str = Field(pattern=r"^[A-Za-z0-9]+$")
    title: str = Field(min_length=1)
    series: str | None = None
    channel: str | None = None
    broadcast_date: date | None = None
    entries: list[str] = Field(min_length=MIN_GROUP_SIZE)

    @field_validator("entries")
    @classmethod
    def _one_platform_no_repeats(cls, entries: list[str]) -> list[str]:
        sources = [parse_source(entry) for entry in entries]
        platforms = {source.platform for source in sources}
        if len(platforms) > 1:
            raise ValueError(
                f"entries mix platforms ({', '.join(sorted(platforms))}); "
                "a compilation joins videos of one platform"
            )
        ids = [source.video_id for source in sources]
        if repeated := sorted(
            {video_id for video_id in ids if ids.count(video_id) > 1}
        ):
            raise ValueError(f"entries repeat {', '.join(repeated)}")
        return entries

    @property
    def sources(self) -> tuple[SourceId, ...]:
        return tuple(parse_source(entry) for entry in self.entries)

    @property
    def platform(self) -> Platform:
        return self.sources[0].platform


def is_manifest_path(text: str) -> bool:
    """Whether a run source names a manifest file rather than a video."""
    return text.lower().endswith(MANIFEST_SUFFIX)


def load_manifest(path: Path) -> CompilationManifest:
    """The manifest at `path` (written with `core.json_artifact.write_model`)."""
    try:
        return read_model(path, CompilationManifest)
    except OSError as error:
        raise CompilationError(f"Cannot read the manifest {path}: {error}") from error
    except ValidationError as error:
        raise CompilationError(f"Invalid manifest {path}:\n{error}") from error


# --- planning ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlaylistEntry:
    source: SourceId
    title: str
    duration: float


@dataclass(frozen=True, slots=True)
class Playlist:
    title: str | None
    entries: tuple[PlaylistEntry, ...]


def list_playlist(
    ytdlp: YtDlp, url: str, *, options: Mapping[str, Any], items: str | None = None
) -> Playlist:
    """The playlist's videos with titles and lengths, from one flat
    extraction (no per-video request); `items` is yt-dlp's `playlist_items`.

    A video without a length or a title (private, removed, not yet
    premiered) fails the listing, naming each, instead of being left out.
    """
    from yt_dlp.utils import DownloadError  # noqa: PLC0415 - heavy

    flat: dict[str, Any] = {**options, "extract_flat": "in_playlist"}
    if items is not None:
        flat["playlist_items"] = items
    try:
        info = ytdlp.extract_info(url, flat, download=False)
    except DownloadError as error:
        raise CompilationError(f"Cannot list {url}: {error}") from error
    raw_entries = info.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise CompilationError(f"{url} lists no videos")
    entries: list[PlaylistEntry] = []
    unusable: list[str] = []
    for position, raw in enumerate(raw_entries, 1):
        entry = _playlist_entry(raw)
        if entry is None:
            unusable.append(f"#{position} {_describe(raw)}")
        else:
            entries.append(entry)
    if unusable:
        raise CompilationError(
            f"{url} has videos without a title or length:\n  " + "\n  ".join(unusable)
        )
    title = info.get("title")
    return Playlist(
        title=title if isinstance(title, str) and title else None,
        entries=tuple(entries),
    )


def _playlist_entry(raw: object) -> PlaylistEntry | None:
    if not isinstance(raw, dict):
        return None
    url = raw.get("url") or raw.get("webpage_url")
    title = raw.get("title")
    duration = raw.get("duration")
    if not isinstance(url, str) or not isinstance(title, str) or not title:
        return None
    if isinstance(duration, bool) or not isinstance(duration, int | float):
        return None
    if duration <= 0:
        return None
    try:
        source = parse_source(url)
    except ValueError:
        return None
    return PlaylistEntry(source=source, title=title, duration=float(duration))


def _describe(raw: object) -> str:
    if isinstance(raw, dict):
        for key in ("url", "id", "title"):
            value = raw.get(key)
            if isinstance(value, str) and value:
                return value
    return repr(raw)


def plan_groups(durations: Sequence[float], groups: int) -> list[range]:
    """Cut `durations` (in order) into `groups` contiguous runs of at least
    `MIN_GROUP_SIZE` items whose totals stay as close to the mean as
    possible (least squared deviation), as index ranges."""
    count = len(durations)
    if groups < 1:
        raise CompilationError("A plan needs at least one group")
    if count < groups * MIN_GROUP_SIZE:
        raise CompilationError(
            f"{count} videos cannot fill {groups} groups of at least {MIN_GROUP_SIZE}"
        )
    target = sum(durations) / groups
    prefix = [0.0]
    for value in durations:
        prefix.append(prefix[-1] + value)
    inf = float("inf")
    # cost[g][i]: best cost of the first i items in g groups; cut[g][i] the
    # start of the last group in that solution.
    cost = [[inf] * (count + 1) for _ in range(groups + 1)]
    cut = [[0] * (count + 1) for _ in range(groups + 1)]
    cost[0][0] = 0.0
    for group in range(1, groups + 1):
        for end in range(group * MIN_GROUP_SIZE, count + 1):
            for start in range((group - 1) * MIN_GROUP_SIZE, end - MIN_GROUP_SIZE + 1):
                previous = cost[group - 1][start]
                if previous == inf:
                    continue
                total = previous + (prefix[end] - prefix[start] - target) ** 2
                if total < cost[group][end]:
                    cost[group][end] = total
                    cut[group][end] = start
    ranges: list[range] = []
    end = count
    for group in range(groups, 0, -1):
        start = cut[group][end]
        ranges.append(range(start, end))
        end = start
    return ranges[::-1]


def groups_for_minutes(durations: Sequence[float], minutes: float) -> int:
    """How many groups bring each closest to `minutes` long (at least one)."""
    if minutes <= 0:
        raise CompilationError("--minutes must be positive")
    return max(1, round(sum(durations) / (minutes * 60)))


def plan_manifests(
    playlist: Playlist,
    groups: Sequence[range],
    *,
    id_prefix: str,
    title: str,
    series: str | None = None,
    channel: str | None = None,
) -> list[CompilationManifest]:
    """One manifest per group: IDs `<prefix><k>` zero-padded alike, titles
    `<title> #<k>`, entries as the platform's canonical URLs."""
    digits = max(_MIN_ID_DIGITS, len(str(len(groups))))
    try:
        return [
            CompilationManifest(
                id=f"{id_prefix}{number:0{digits}d}",
                title=f"{title} #{number}",
                series=series,
                channel=channel,
                entries=[playlist.entries[index].source.url for index in group],
            )
            for number, group in enumerate(groups, 1)
        ]
    except ValidationError as error:
        raise CompilationError(f"Cannot plan the manifests:\n{error}") from error


def manifest_path(directory: Path, manifest: CompilationManifest) -> Path:
    return directory / f"{manifest.id}{MANIFEST_SUFFIX}"
