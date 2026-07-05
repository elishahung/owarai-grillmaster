"""Normalization of platform closed captions fetched at download time.

yt-dlp writes raw subtitle files next to the numbered video parts (e.g.
``0.ja.srt``). This module reparses them into the canonical official-subtitle
SRT consumed by the translate pipeline as a ground-truth reference. Timestamps
refer to the full video, so section runs shift them by the requested cut start;
the stream-copy cut snaps to keyframes, so the shifted timeline is approximate
(a prompt-side caveat, acceptable for reference use).
"""

from pathlib import Path

from loguru import logger

from services.media import MediaProcessor
from services.srt import SrtBlock, format_timecode, parse_srt, serialize_srt


def normalize_official_subtitle(
    raw_paths: list[Path],
    output_path: Path,
    section_start: float | None = None,
    section_end: float | None = None,
) -> bool:
    """Normalize the downloaded platform subtitle into the official SRT.

    Requires all raw subtitle files to belong to a single video part
    (multi-part projects are not supported); language variants of that part
    are duplicates and the exact `ja` one is preferred. Raw files are deleted
    after a successful write, mirroring how combine_videos consumes its
    inputs. Returns True when the official SRT was written.
    """
    if not raw_paths:
        logger.info(
            "No platform subtitles were downloaded; official subtitle "
            "reference unavailable for this project"
        )
        return False
    # Subtitle files are named `<part>.<lang>.srt` next to the numbered video
    # parts. Multiple video parts are unsupported (whose timeline would the
    # reference use?), but multiple language variants of the same part
    # (e.g. `ja` + `ja-JP`) are duplicates — pick one, preferring exact `ja`.
    parts = {path.name.split(".", 1)[0] for path in raw_paths}
    if len(parts) > 1:
        logger.warning(
            "Subtitles from multiple video parts found; skipping official "
            f"subtitle normalization: {sorted(p.name for p in raw_paths)}"
        )
        return False
    part = next(iter(parts))
    raw_path = next(
        (p for p in raw_paths if p.name == f"{part}.ja.srt"),
        min(raw_paths, key=lambda p: p.name),
    )
    if len(raw_paths) > 1:
        logger.info(
            f"Multiple subtitle language variants found; using "
            f"{raw_path.name} out of {sorted(p.name for p in raw_paths)}"
        )
    blocks = parse_srt(raw_path.read_text(encoding="utf-8"))
    if section_start is not None or section_end is not None:
        blocks = _shift_to_section(blocks, section_start or 0.0, section_end)
    if not blocks:
        logger.warning(
            f"No usable subtitle blocks in {raw_path.name}; skipping official "
            "subtitle normalization"
        )
        return False

    blocks = [
        SrtBlock(index=i, timecode=block.timecode, text=block.text)
        for i, block in enumerate(blocks, start=1)
    ]
    output_path.write_text(serialize_srt(blocks), encoding="utf-8")
    for path in raw_paths:  # unused language variants are duplicates
        path.unlink()
    logger.success(
        f"Official subtitle normalized: {output_path.name} "
        f"({len(blocks)} blocks from {raw_path.name})"
    )
    return True


def _shift_to_section(
    blocks: list[SrtBlock],
    section_start: float,
    section_end: float | None,
) -> list[SrtBlock]:
    """Keep blocks overlapping the section and rebase timestamps to its start."""
    shifted: list[SrtBlock] = []
    for block in blocks:
        time_range = MediaProcessor.parse_timecode_line(block.timecode)
        if time_range.end_seconds <= section_start:
            continue
        if section_end is not None and time_range.start_seconds >= section_end:
            continue
        shifted.append(
            SrtBlock(
                index=block.index,
                timecode=(
                    f"{format_timecode(time_range.start_seconds - section_start)}"
                    " --> "
                    f"{format_timecode(time_range.end_seconds - section_start)}"
                ),
                text=block.text,
            )
        )
    return shifted
