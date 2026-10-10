"""ASS canvas, typeface, margins, colours and script headers.

The dialogue subtitles (finalize) and the live-chat panel are burned in as
two ASS layers on the same 1920x1080 canvas in the same font, so both take
these values from here. Values are the tuned originals; changing one changes
every packaged video.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

PLAY_RES_X = 1920
PLAY_RES_Y = 1080
FONT_NAME = "源泉圓體月 M"
# Default style margins (left/right and bottom).
MARGIN_H = 10
MARGIN_V = 40

STYLE_FORMAT = (
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
    "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
    "MarginR, MarginV, Encoding"
)
EVENT_FORMAT = (
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
)

# The dialogue style: near-white text, black outline, translucent shadow,
# bottom-centred.
DIALOGUE_STYLE_NAME = "Default"
DIALOGUE_STYLE = (
    f"Style: {DIALOGUE_STYLE_NAME},{FONT_NAME},64,&H00FDFDFD,&H000000FF,"
    "&H00000000,&H7D000000,0,0,0,0,100,100,0,0,1,6,2,2,"
    f"{MARGIN_H},{MARGIN_H},{MARGIN_V},1"
)


def bgr(rgb: str) -> str:
    """ASS colour order for an `RRGGBB` hex colour: `BBGGRR`."""
    return rgb[4:6] + rgb[2:4] + rgb[0:2]


def script_header(info: Sequence[str], styles: Sequence[str]) -> str:
    """`[Script Info]` (after `ScriptType`), `[V4+ Styles]` and the
    `[Events]` format line; dialogue lines follow directly."""
    return "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            *info,
            "",
            "[V4+ Styles]",
            STYLE_FORMAT,
            *styles,
            "",
            "[Events]",
            EVENT_FORMAT,
            "",
        ]
    )


# The header of the finalized dialogue ASS.
DIALOGUE_HEADER = script_header(
    [
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        f"PlayResX: {PLAY_RES_X}",
        f"PlayResY: {PLAY_RES_Y}",
    ],
    [DIALOGUE_STYLE],
)
