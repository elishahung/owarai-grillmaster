"""Convert translated SRT subtitles to styled ASS format.

Applies Traditional Chinese subtitle punctuation rules aligned with the
Netflix TC style guide:

- Strip leading/trailing ``，``/``、``/``；``/``。`` plus surrounding
  whitespace from each line (Netflix forbids terminal commas/periods at
  line endings).
- Collapse any run of ellipsis characters — 3+ half-width ``.``, one or
  more full-width ``…`` (U+2026) or ``⋯`` (U+22EF), or a mixed
  sequence — into a single ``…``.
- Strip ``[\\s，、；。]+`` immediately before a closing dialogue quote
  ``」`` or ``』`` (same rule as line edges, applied to the dialogue's
  inner end). ``？``/``！``/``…`` before the quote are preserved.
- Convert any remaining (mid-line) ``。`` to ``，`` for smoother visual
  flow — bare ``。`` mid-subtitle reads awkwardly.
- Preserve mid-sentence ``，``/``、``/``；`` and all other punctuation
  (``？``/``！``/``「」``/``『』``/``（）``/``《》``/``：``).
"""

import re
from pathlib import Path
from typing import Iterable

from loguru import logger

from services.srt import SrtBlock, parse_srt, serialize_srt

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,源泉圓體月 M,64,&H00FDFDFD,&H000000FF,&H00000000,&H7D000000,0,0,0,0,100,100,0,0,1,6,2,2,10,10,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

_LINE_EDGE_PUNCT = re.compile(r"^[\s，、；。]+|[\s，、；。]+$")
_ELLIPSIS_RUN = re.compile(r"(?:\.{3,}|[…⋯])+")
_QUOTE_TAIL_PUNCT = re.compile(r"[\s，、；。]+(?=[」』])")
_SRT_TIMECODE = re.compile(
    r"^\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*$"
)


def _clean_line(line: str) -> str:
    line = _LINE_EDGE_PUNCT.sub("", line)
    line = _ELLIPSIS_RUN.sub("…", line)
    line = _QUOTE_TAIL_PUNCT.sub("", line)
    return line.replace("。", "，")


def _clean_text(text: str) -> str:
    return "\n".join(_clean_line(line) for line in text.split("\n"))


def _format_ass_time(h: str, m: str, s: str, ms: str) -> str:
    # ASS uses centiseconds with single-digit hour: H:MM:SS.cc.
    # Aegisub truncates the millisecond → centisecond conversion.
    return f"{int(h)}:{m}:{s}.{int(ms) // 10:02d}"


def _srt_timecode_to_ass(srt_timecode: str) -> tuple[str, str]:
    match = _SRT_TIMECODE.match(srt_timecode)
    if not match:
        raise ValueError(f"Invalid SRT timecode: {srt_timecode!r}")
    sh, sm, ss, sms, eh, em, es, ems = match.groups()
    return _format_ass_time(sh, sm, ss, sms), _format_ass_time(eh, em, es, ems)


def _block_to_dialogue(block: SrtBlock) -> str:
    start, end = _srt_timecode_to_ass(block.timecode)
    text = _clean_text(block.text).replace("\n", "\\N")
    return f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}"


def _render(blocks: Iterable[SrtBlock]) -> str:
    dialogue_lines = [_block_to_dialogue(b) for b in blocks]
    return ASS_HEADER + "\n".join(dialogue_lines) + "\n"


def convert_file(
    input_path: str | Path,
    output_path: str | Path,
    finalized_srt_path: str | Path | None = None,
) -> None:
    """Read an SRT file, clean Chinese punctuation, and write a styled ASS file.

    When ``finalized_srt_path`` is provided, also writes a player-friendly SRT
    with the same punctuation cleanup applied to each block's text. The SRT
    output is intended for devices that don't support ASS.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)

    srt_text = input_path.read_text(encoding="utf-8")
    blocks = parse_srt(srt_text)
    ass_text = _render(blocks)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(ass_text, encoding="utf-8")
    logger.success(f"Converted SRT to ASS: {output_path}")

    if finalized_srt_path is not None:
        srt_out = Path(finalized_srt_path)
        cleaned_blocks = [
            b.model_copy(update={"text": _clean_text(b.text)}) for b in blocks
        ]
        srt_out.parent.mkdir(parents=True, exist_ok=True)
        srt_out.write_text(serialize_srt(cleaned_blocks), encoding="utf-8")
        logger.success(f"Wrote finalized SRT: {srt_out}")
