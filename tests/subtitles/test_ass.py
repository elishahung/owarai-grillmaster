from __future__ import annotations

from grillmaster.subtitles import ass

# The finalized dialogue header as the pre-refactor finalize module wrote it.
LEGACY_DIALOGUE_HEADER = """[Script Info]
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


def test_dialogue_header_is_byte_identical():
    assert ass.DIALOGUE_HEADER == LEGACY_DIALOGUE_HEADER


def test_script_header_layout():
    header = ass.script_header(["WrapStyle: 2"], ["Style: A", "Style: B"])
    assert header == (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 2\n\n"
        f"[V4+ Styles]\n{ass.STYLE_FORMAT}\nStyle: A\nStyle: B\n\n"
        f"[Events]\n{ass.EVENT_FORMAT}\n"
    )


def test_bgr_reverses_channels():
    assert ass.bgr("B8B8B8") == "B8B8B8"
    assert ass.bgr("E8A317") == "17A3E8"
