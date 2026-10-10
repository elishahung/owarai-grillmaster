from __future__ import annotations

import io
import math
from pathlib import Path

import pytest
from tests.live_chat.conftest import golden_log

from grillmaster.live_chat import render
from grillmaster.live_chat.layout import ChatLayout
from grillmaster.live_chat.schema import TranslatedChatLog, TranslatedChatMessage

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "live_chat"


def _log() -> TranslatedChatLog:
    return TranslatedChatLog(
        messages=[
            TranslatedChatMessage(
                id=0, seconds=1.0, author="@a", text="池田", translation="池田{\\b1}"
            ),
            TranslatedChatMessage(
                id=1,
                seconds=2.0,
                author="@donor",
                text="",
                translation="",
                kind="paid",
                amount="¥800",
            ),
        ]
    )


def _document(log: TranslatedChatLog, layout: ChatLayout, duration: float) -> str:
    out = io.StringIO()
    render.write_document(log, duration, out, layout)
    return out.getvalue()


def test_states_scroll_older_messages_and_escape_user_text() -> None:
    ass = _document(_log(), ChatLayout.OVERLAY, 5.0)
    dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue:")]

    # Panel, then 3 events for state 1 and 3 + 4 (paid adds a highlight) for
    # state 2.
    assert len(dialogues) == 1 + 3 + 7
    assert dialogues[0].startswith("Dialogue: 0,0:00:00.00,0:00:05.00,")
    first_state = [d for d in dialogues if ",0:00:01.00,0:00:02.00," in d]
    assert all("\\fad(" in d for d in first_state)
    second_state = [d for d in dialogues if ",0:00:02.00,0:00:05.00," in d]
    # The older message slides up by the newcomer's height + gap.
    assert "\\move(" in next(d for d in second_state if "@a" in d)
    assert "¥800" in next(d for d in second_state if "@donor" in d)
    # Override braces in user text are neutralized.
    assert f"池田{chr(0xFF5B)}{chr(0xFF3C)}b1{chr(0xFF5D)}" in ass


def test_side_layout_draws_no_panel_background() -> None:
    ass = _document(_log(), ChatLayout.SIDE, 5.0)

    # Messages sit straight on the black column.
    assert "Dialogue: 0," not in ass
    assert r"\pos(1550," in ass


def test_long_text_wraps_and_truncates() -> None:
    lines = render.wrap("あ" * 200, render.BODY_FONT_SIZE, 300)

    assert len(lines) == render.MAX_BODY_LINES
    assert lines[-1].endswith("…")


@pytest.mark.parametrize("layout", [ChatLayout.SIDE, ChatLayout.OVERLAY])
def test_output_is_byte_identical_to_the_tuned_renderer(
    layout: ChatLayout, tmp_path: Path
) -> None:
    out = tmp_path / "chat.ass"

    render.write_chat_ass(golden_log(), 20.0, out, layout)

    # Rendered by the pre-refactor renderer from the same log.
    expected = _FIXTURES / f"panel_{layout}.ass"
    assert not out.read_bytes().startswith(b"\xef\xbb\xbf")
    assert out.read_text(encoding="utf-8") == expected.read_text(encoding="utf-8-sig")


def test_a_failed_render_leaves_the_previous_file(tmp_path: Path) -> None:
    out = tmp_path / "chat.ass"
    out.write_text("previous", encoding="utf-8")

    with pytest.raises(ValueError, match="NaN"):
        render.write_chat_ass(_log(), math.nan, out, ChatLayout.SIDE)

    assert out.read_text(encoding="utf-8") == "previous"
    assert [path.name for path in tmp_path.iterdir()] == ["chat.ass"]


def test_side_layout_letterboxes_the_picture_beside_the_chat() -> None:
    placement = render.chat_placement(ChatLayout.SIDE)

    assert placement.picture == render.PictureBox(x=0, y=108, width=1536, height=864)
    # Dialogue sits in the bottom bar, centred over the picture.
    assert placement.dialogue_style == "MarginR=394,MarginV=24"


def test_overlay_layout_keeps_the_full_frame() -> None:
    assert render.chat_placement(ChatLayout.OVERLAY) == render.ChatPlacement(
        picture=None, dialogue_style=None
    )


def test_layout_none_has_no_panel(tmp_path: Path) -> None:
    out = tmp_path / "chat.ass"
    with pytest.raises(ValueError, match="none"):
        render.write_chat_ass(_log(), 5.0, out, ChatLayout.NONE)
    with pytest.raises(ValueError, match="none"):
        render.chat_placement(ChatLayout.NONE)
    assert not out.exists()
