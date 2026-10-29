from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from grillmaster.core.briefing import Briefing, Character, TermMapping
from grillmaster.core.srt import SrtBlock
from grillmaster.glossary.fixed import (
    FixedGlossary,
    GlossaryEntry,
    TalentUnit,
    load_fixed_glossary,
)
from grillmaster.subtitles.ass import (
    DIALOGUE_HEADER,
    DIALOGUE_MARGINS,
    DIALOGUE_OUTLINE,
    dialogue_rows,
)
from grillmaster.subtitles.finalize import (
    briefing_name_units,
    build_name_spacer,
    clean_text,
    curated_name_units,
    dialogue_line,
    fit_text,
    name_units,
    render_ass,
    write_finalized,
)

if TYPE_CHECKING:
    from pathlib import Path


# --- punctuation ------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("你好，今天天氣不錯", "你好，今天天氣不錯", id="mid-comma-kept"),
        pytest.param("今天天氣不錯。", "今天天氣不錯", id="trailing-period"),
        pytest.param("蘋果、橘子、香蕉", "蘋果、橘子、香蕉", id="enumeration-comma"),
        pytest.param("一；二；三", "一；二；三", id="semicolon"),
        pytest.param(
            "你好，今天天氣不錯。蘋果、橘子、香蕉。",
            "你好，今天天氣不錯，蘋果、橘子、香蕉",
            id="combined",
        ),
        pytest.param("第一句。第二句。", "第一句，第二句", id="mid-period-to-comma"),
        pytest.param("今晚的嘉賓是...", "今晚的嘉賓是…", id="halfwidth-ellipsis"),
        pytest.param("等等....再說", "等等…再說", id="four-dots"),
        pytest.param("好啊……", "好啊…", id="fullwidth-ellipsis-run"),
        pytest.param("等等………再說", "等等…再說", id="fullwidth-ellipsis-mid"),
        pytest.param("混雜...…再說", "混雜…再說", id="mixed-ellipsis-1"),
        pytest.param("混雜…...再說", "混雜…再說", id="mixed-ellipsis-2"),
        pytest.param("好啊⋯", "好啊…", id="midline-ellipsis"),
        pytest.param("好啊⋯⋯", "好啊…", id="midline-ellipsis-run"),
        pytest.param("混雜⋯…再說", "混雜…再說", id="midline-and-fullwidth"),
        pytest.param("混雜⋯...再說", "混雜…再說", id="midline-and-dots"),
        pytest.param("好啊…", "好啊…", id="single-ellipsis"),
        pytest.param("真的嗎？太好了！", "真的嗎？太好了！", id="question-exclaim"),
        pytest.param("「結論：很好吃……」", "「結論：很好吃…」", id="quote-ellipsis"),
        pytest.param("（旁白）", "（旁白）", id="parens"),
        pytest.param("「沒問題。」", "「沒問題」", id="quote-tail-period"),
        pytest.param("「閃到腰，」", "「閃到腰」", id="quote-tail-comma"),
        pytest.param("「怎麼啦？」", "「怎麼啦？」", id="quote-tail-question"),
        pytest.param("「太好了！」", "「太好了！」", id="quote-tail-exclaim"),
        pytest.param(
            "結果他說：「好，沒問題，站起來。」",
            "結果他說：「好，沒問題，站起來」",
            id="quote-mid-kept",
        ),
        pytest.param("『嵌套。』", "『嵌套』", id="nested-quote"),
        pytest.param("『嵌套？』", "『嵌套？』", id="nested-quote-question"),
        pytest.param(
            "「閃到腰，\n痛得要命。」", "「閃到腰\n痛得要命」", id="quote-lines"
        ),
        pytest.param("你好，", "你好", id="trailing-comma"),
        pytest.param("，你好", "你好", id="leading-comma"),
        pytest.param("第一行，\n第二行。", "第一行\n第二行", id="per-line"),
        pytest.param(
            "竟然。 太誇張了吧。", "竟然，太誇張了吧", id="space-after-period"
        ),
        pytest.param("好帥。 很有型耶。", "好帥，很有型耶", id="space-after-period-2"),
        pytest.param(
            "真的嗎？ 太好了！", "真的嗎？太好了！", id="space-after-question"
        ),
        pytest.param("好 ， 壞", "好，壞", id="spaces-around-comma"),
        pytest.param(
            "第一句。 第二句。\n好， 啊", "第一句，第二句\n好，啊", id="spaces-lines"
        ),
        pytest.param("- 晚安", "-晚安", id="dash-space"),
        pytest.param("-  晚安", "-晚安", id="dash-spaces"),
        pytest.param("-\t晚安", "-晚安", id="dash-tab"),
        pytest.param("- 晚安\n- 這裡是大家", "-晚安\n-這裡是大家", id="dash-lines"),
        pytest.param(" - 晚安", "-晚安", id="dash-leading-space"),
        pytest.param("-是 Shampoo", "-是 Shampoo", id="dash-compliant"),
        pytest.param("-- 等一下", "-- 等一下", id="interruption-dash"),
        pytest.param("－哎呀，各位辛苦了", "-哎呀，各位辛苦了", id="fullwidth-dash"),
        pytest.param(
            "－哎呀\n－那是當然", "-哎呀\n-那是當然", id="fullwidth-dash-lines"
        ),
        pytest.param("－ 哎呀", "-哎呀", id="fullwidth-dash-space"),
        pytest.param("— 對啊", "-對啊", id="em-dash"),
        pytest.param("–對啊", "-對啊", id="en-dash"),  # noqa: RUF001
    ],
)
def test_clean_text(text: str, expected: str):
    assert clean_text(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("解散了。／那當然", "-解散了\n-那當然"),  # noqa: RUF001
        ("太悲慘了，／好沉重啊", "-太悲慘了\n-好沉重啊"),  # noqa: RUF001
        ("誰啊？/ 山內很厲害", "-誰啊？\n-山內很厲害"),
        ("就算結果不好 / 也會", "-就算結果不好\n-也會"),
        ("-剛才不是這樣，/ - 他在生氣", "-剛才不是這樣\n-他在生氣"),
        ("-- 等一下 / 好", "-- 等一下\n-好"),
        ("誰啊? / 山內", "-誰啊?\n-山內"),
        ("因為... / 抱歉", "-因為…\n-抱歉"),
        ("「好」／對啊", "-「好」\n-對啊"),  # noqa: RUF001
        ("能看到海\n／咦，在並行嗎？", "-能看到海\n-咦，在並行嗎？"),  # noqa: RUF001
        ("那當然／", "那當然"),  # noqa: RUF001
    ],
)
def test_a_turn_change_slash_becomes_dialogue_lines(text: str, expected: str):
    assert clean_text(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("知道嗎？/ 是！/ 很好", "知道嗎？／是！／很好"),  # noqa: RUF001
        ("平常會看嗎，/ 姑且會看啦\n會看", "平常會看嗎／姑且會看啦\n會看"),  # noqa: RUF001
        ("-其實是這樣\n-是這樣嗎？ / 沒錯", "-其實是這樣\n-是這樣嗎？／沒錯"),  # noqa: RUF001
    ],
)
def test_turn_changes_past_two_lines_keep_one_slash(text: str, expected: str):
    assert clean_text(text) == expected


@pytest.mark.parametrize(
    "text",
    ["Peace（和平／剪刀手）", "機率 1/2", "AC / DC"],  # noqa: RUF001
)
def test_slashes_between_words_stay(text: str):
    assert clean_text(text) == text


# --- over-wide lines --------------------------------------------------------


def test_a_line_wider_than_the_canvas_is_broken_without_its_row_final_comma():
    text = (
        "在半準決賽被淘汰的話，只要影片觀看次數第一，就能拿外卡進準決賽，這賽制現在也有"
    )
    assert fit_text(text) == (
        "在半準決賽被淘汰的話，只要影片觀看次數第一\n就能拿外卡進準決賽，這賽制現在也有"
    )


def test_a_line_that_fits_is_left_alone():
    assert fit_text("你好，世界\n第二行") == "你好，世界\n第二行"
    assert fit_text("第一行\n\n第二行") == "第一行\n\n第二行"


def test_every_finalized_row_is_one_drawn_row_inside_the_canvas():
    # A share of 65 characters rounds up past the room.
    for text in ["一" * 65, "-手肘其實已經好了吧？\n-" + "所以我說啊，" * 7]:
        fitted = fit_text(text)
        rows = dialogue_rows(fitted, DIALOGUE_MARGINS)
        assert len(rows) == len(fitted.splitlines())
        assert all(
            x1 - x0 - 2 * DIALOGUE_OUTLINE <= DIALOGUE_MARGINS.room
            for x0, _, x1, _ in rows
        )


# --- ASS events -------------------------------------------------------------


@pytest.mark.parametrize(
    ("timecode", "expected"),
    [
        pytest.param(
            "00:00:01,500 --> 00:00:02,750", "0:00:01.50,0:00:02.75", id="basic"
        ),
        pytest.param(
            "01:23:45,678 --> 12:00:00,000", "1:23:45.67,12:00:00.00", id="hours"
        ),
        pytest.param(
            "00:00:00,999 --> 00:00:01,000", "0:00:00.99,0:00:01.00", id="truncate"
        ),
    ],
)
def test_dialogue_times(timecode: str, expected: str):
    line = dialogue_line(SrtBlock(1, timecode, "字"))
    assert line == f"Dialogue: 0,{expected},Default,,0,0,0,,字"


def test_dialogue_soft_breaks_and_empty_text():
    timecode = "00:00:01,000 --> 00:00:02,000"
    assert dialogue_line(SrtBlock(1, timecode, "第一行\n第二行")).endswith(
        ",,第一行\\N第二行"
    )
    assert dialogue_line(SrtBlock(1, timecode, "")) == (
        "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,"
    )


def test_dialogue_rejects_an_invalid_timecode():
    with pytest.raises(ValueError, match="timecode"):
        dialogue_line(SrtBlock(1, "garbage", "字"))


def test_render_ass_uses_the_dialogue_header():
    blocks = [SrtBlock(1, "00:00:01,000 --> 00:00:02,000", "你好，世界")]
    assert render_ass(blocks) == (
        DIALOGUE_HEADER
        + "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,你好，世界\n"
    )


# --- name spacing -----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "units", "expected"),
    [
        pytest.param("以及空前Meteor的茶屋。", ["空前Meteor"], "以及 空前Meteor 的茶屋。", id="mixed-unit"),
        pytest.param("他是Long Coat Daddy啦", ["Long Coat Daddy"], "他是 Long Coat Daddy 啦", id="inner-spaces-kept"),
        pytest.param("是Diane。", ["Diane"], "是 Diane。", id="before-cjk-punct"),
        pytest.param("是Diane.", ["Diane"], "是 Diane.", id="before-ascii-punct"),
        pytest.param("那是水川Katamari？", ["水川Katamari"], "那是 水川Katamari？", id="before-question"),
        pytest.param("Diane 的 Yusuke。", ["Diane", "Yusuke"], "Diane 的 Yusuke。", id="line-start"),
        pytest.param("那是水川Katamari", ["水川Katamari"], "那是 水川Katamari", id="line-end"),
        pytest.param("原田竟然比  水川Katamari  還心動", ["水川Katamari"], "原田竟然比 水川Katamari 還心動", id="extra-spaces"),
        pytest.param("難道是森本桑？", ["森本", "Diane"], "難道是森本桑？", id="pure-han-untouched"),
        pytest.param("永野以前說過", ["永野"], "永野以前說過", id="pure-han-only"),
        pytest.param("原田竟然比\n水川Katamari還讓人心動？", ["水川Katamari"], "原田竟然比\n水川Katamari 還讓人心動？", id="second-line-start"),
        pytest.param("以及空前Meteor的茶屋。", [], "以及空前Meteor的茶屋。", id="no-units"),
        pytest.param("是Diane津田。", ["Diane", "Diane津田"], "是 Diane津田。", id="longest-wins"),
        pytest.param("是Diane的。", ["Diane", "Diane津田"], "是 Diane 的。", id="shorter-unit"),
        pytest.param("他是金屬 Bat的", ["金屬Bat"], "他是 金屬Bat 的", id="split-unit-rejoined"),
        pytest.param("Imadei 醬很強", ["Imadei醬"], "Imadei醬 很強", id="split-suffix-rejoined"),
        pytest.param("他是金屬  Bat", ["金屬Bat"], "他是 金屬Bat", id="split-doubled"),
        pytest.param("這是Long  Coat Daddy真強", ["Long Coat Daddy"], "這是 Long Coat Daddy 真強", id="mangled-inner"),
        pytest.param("這是LongCoatDaddy真強", ["Long Coat Daddy"], "這是 Long Coat Daddy 真強", id="despaced"),
        pytest.param("上一屆王者 Two Tribe Takanori", ["Two Tribe", "Takanori"], "上一屆王者 Two Tribe Takanori", id="adjacent-names-end"),
        pytest.param("Two Tribe Takanori 很強", ["Two Tribe", "Takanori"], "Two Tribe Takanori 很強", id="adjacent-names-start"),
        pytest.param("這是 Two Tribe Takanori。", ["Two Tribe", "Takanori"], "這是 Two Tribe Takanori。", id="adjacent-names-mid"),
        pytest.param("金屬是Bat嗎", ["金屬Bat"], "金屬是Bat嗎", id="no-merge-across-text"),
    ],
)  # fmt: skip
def test_name_spacing(text: str, units: list[str], expected: str):
    assert build_name_spacer(units)(text) == expected


def test_briefing_units_are_its_agreed_renderings():
    briefing = Briefing(
        summary="",
        characters=[Character(name_jp="嶋佐", name_zh="嶋佐和也", role_note="")],
        proper_nouns=[
            TermMapping(source="クーマイメテオ", target="空前Meteor"),
            TermMapping(source="茶屋", target="茶屋"),
        ],
        glossary=[TermMapping(source="ボケ", target="裝傻")],
        catchphrases=[],
        tone_notes="",
        segment_summaries=[],
    )

    assert set(briefing_name_units(briefing)) == {
        "空前Meteor",
        "茶屋",
        "嶋佐和也",
        "裝傻",
    }


def test_curated_units_are_only_mixed_names():
    glossary = FixedGlossary(
        talents=(
            TalentUnit(
                members=(GlossaryEntry(jp=("水川かたまり",), zh="水川Katamari"),)
            ),
        ),
        others=(
            GlossaryEntry(jp=("ダイアン",), zh="Diane"),
            GlossaryEntry(jp=("金属バット",), zh="金屬球棒"),
        ),
    )

    assert curated_name_units(glossary) == ["水川Katamari"]


def test_bundled_glossary_yields_mixed_names():
    units = curated_name_units(load_fixed_glossary())

    assert len(units) > 5
    assert "水川Katamari" in units
    assert not {"金屬球棒", "Diane", "THE SECOND"} & set(units)


# --- deliverables -----------------------------------------------------------

EMPTY_BRIEFING = Briefing(
    summary="",
    characters=[Character(name_jp="森本", name_zh="森本", role_note="")],
    proper_nouns=[TermMapping(source="クーマイメテオ", target="空前Meteor")],
    glossary=[],
    catchphrases=[],
    tone_notes="",
    segment_summaries=[],
)


def test_write_finalized_spaces_then_cleans(tmp_path: Path):
    blocks = [
        SrtBlock(1, "00:00:01,000 --> 00:00:02,000", "以及空前Meteor的茶屋。"),
        SrtBlock(2, "00:00:03,000 --> 00:00:04,000", "難道是森本桑？"),
        SrtBlock(3, "00:00:05,000 --> 00:00:06,000", "我推薦的是水川Katamari 桑啊。"),
    ]
    ass_path = tmp_path / "subs" / "cht.ass"
    srt_path = tmp_path / "subs" / "cht.srt"

    write_finalized(
        blocks,
        units=name_units(EMPTY_BRIEFING, load_fixed_glossary()),
        ass_path=ass_path,
        srt_path=srt_path,
    )

    assert srt_path.read_text(encoding="utf-8") == (
        "1\n00:00:01,000 --> 00:00:02,000\n以及 空前Meteor 的茶屋\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\n難道是森本桑？\n\n"
        # Curated, not in the briefing; the trailing 桑 keeps its space.
        "3\n00:00:05,000 --> 00:00:06,000\n我推薦的是 水川Katamari 桑啊\n"
    )
    ass = ass_path.read_text(encoding="utf-8")
    assert ass.startswith(DIALOGUE_HEADER)
    assert "Default,,0,0,0,,以及 空前Meteor 的茶屋\n" in ass
    assert "森本 桑" not in ass
