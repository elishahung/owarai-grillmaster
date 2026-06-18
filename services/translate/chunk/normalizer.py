"""Post-translation SRT normalization helpers."""

from services.srt import SrtBlock


def normalize_translated_blocks(blocks: list[SrtBlock]) -> list[SrtBlock]:
    """Normalize translated block text without changing timing metadata."""
    return [
        SrtBlock(
            index=block.index,
            timecode=block.timecode,
            text=_remove_empty_speaker_dash_lines(block.text),
        )
        for block in blocks
    ]


def _remove_empty_speaker_dash_lines(text: str) -> str:
    """Remove speaker-marker lines that contain only a dash."""
    return "\n".join(
        line for line in text.splitlines() if line.strip() != "-"
    )
