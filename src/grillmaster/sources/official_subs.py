"""Platform closed captions (字幕放送) normalized into the official SRT.

yt-dlp writes raw caption files next to the numbered video parts
(`<part>.<lang>.srt`, e.g. `0.ja.srt`). Their timestamps refer to the full
video, so a section run shifts them by the cut start; the stream-copy cut
snaps to keyframes, so the shifted timeline is approximate (fine for a
ground-truth reference).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.srt import SrtBlock, read_srt_file, reindex, write_srt_file
from grillmaster.core.timecode import TimeRange, format_timecode_line
from grillmaster.sources.errors import SourceError

if TYPE_CHECKING:
    from pathlib import Path

_PREFERRED_LANGUAGE = "ja"


def raw_caption_files(parts_dir: Path) -> list[Path]:
    """The caption files yt-dlp wrote beside the video parts."""
    return sorted(parts_dir.glob("*.srt"))


def normalize_official_subtitles(
    raw_paths: list[Path],
    output: Path,
    *,
    section_start: float | None = None,
    section_end: float | None = None,
) -> bool:
    """Write the official SRT from the downloaded captions; `True` when written.

    Nothing is written, normally, when the platform had no captions, when
    they belong to several video parts (whose timeline would the reference
    use?) or when none overlaps the section. Language variants of one part
    (`ja` + `ja-JP`) are duplicates; the exact `ja` one is preferred.
    Captions that were downloaded but cannot be used raise `SourceError`.
    """
    if not raw_paths:
        logger.info("The platform published no captions for this video")
        return False
    parts = {path.name.split(".", 1)[0] for path in raw_paths}
    if len(parts) > 1:
        logger.info(
            "Captions belong to several video parts; no official subtitles: "
            f"{', '.join(path.name for path in raw_paths)}"
        )
        return False
    part = parts.pop()
    raw_path = next(
        (
            path
            for path in raw_paths
            if path.name == f"{part}.{_PREFERRED_LANGUAGE}.srt"
        ),
        min(raw_paths, key=lambda path: path.name),
    )
    if len(raw_paths) > 1:
        logger.info(f"Using caption variant {raw_path.name} of {len(raw_paths)}")
    try:
        blocks = read_srt_file(raw_path)
    except ValueError as error:
        raise SourceError(
            f"Unreadable platform captions {raw_path}: {error}"
        ) from error
    if not blocks:
        raise SourceError(f"Platform captions {raw_path} contain no subtitles")
    if section_start is not None or section_end is not None:
        blocks = shift_to_section(blocks, section_start or 0.0, section_end)
        if not blocks:
            logger.info("No platform caption overlaps the section")
            return False
    write_srt_file(output, reindex(blocks))
    logger.success(
        f"Official subtitles: {output.name} ({len(blocks)} blocks from {raw_path.name})"
    )
    return True


def shift_to_section(
    blocks: list[SrtBlock], section_start: float, section_end: float | None
) -> list[SrtBlock]:
    """Keep blocks overlapping the section, rebased onto its start."""
    section = TimeRange(
        section_start, float("inf") if section_end is None else section_end
    )
    shifted: list[SrtBlock] = []
    for block in blocks:
        span = block.time_range
        if not span.overlaps(section):
            continue
        timecode = format_timecode_line(
            span.start - section_start, span.end - section_start
        )
        shifted.append(SrtBlock(block.index, timecode, block.text))
    return shifted
