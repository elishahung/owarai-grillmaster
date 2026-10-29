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


# --- line breaking ----------------------------------------------------------

QUOTES = "那不是『抱怨碎碎念鐵郎』，而是『碎碎念阿鐵』要對年輕人的段子開砲了"


def test_a_line_wider_than_its_room_breaks_after_the_nearest_pause():
    assert ass.LineBreaks(64, uneven=0.35).broken([QUOTES], 1900) == (
        "那不是『抱怨碎碎念鐵郎』，",
        "而是『碎碎念阿鐵』要對年輕人的段子開砲了",
    )


def test_a_part_never_ends_on_opening_punctuation():
    # 20 % from an even share leaves no pause in reach.
    first, _ = ass.LineBreaks(64, uneven=0.2).broken([QUOTES], 1900)
    assert first[-1] not in ass.OPENING


def test_a_part_never_starts_with_closing_punctuation():
    # An even share ends right before the closing bracket.
    line = "一" * 21 + "」" + "一" * 19
    _, second = ass.LineBreaks(64, uneven=0.2).broken([line], 1900)
    assert second[0] not in ass.CLOSING


def test_a_slash_ends_its_part():
    line = (
        "大家上節目時都是拚命流汗，就算結果不好 / 也會不斷表現出「我想上電視」的企圖心"
    )
    assert ass.LineBreaks(64, uneven=0.35).broken([line], 1900) == (
        "大家上節目時都是拚命流汗，就算結果不好 /",
        "也會不斷表現出「我想上電視」的企圖心",
    )


def test_a_cjk_line_too_wide_is_drawn_as_one_row_past_both_edges():
    [(x0, _, x1, _)] = ass.dialogue_rows("一" * 40, ass.DIALOGUE_MARGINS)
    assert x0 < 0
    assert x1 > ass.PLAY_RES_X


def test_a_line_too_wide_wraps_at_its_spaces_into_even_rows():
    rows = ass.dialogue_rows("abc " * 60, ass.DIALOGUE_MARGINS)
    assert len(rows) == 5
    assert all(x0 > 0 for x0, _, _, _ in rows)
