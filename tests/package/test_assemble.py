from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from tests.package.conftest import PackageFfmpeg

from grillmaster.core.briefing import Briefing, TermMapping
from grillmaster.live_chat.layout import ChatLayout
from grillmaster.live_chat.render import PictureBox
from grillmaster.live_chat.schema import TranslatedChatLog, TranslatedChatMessage
from grillmaster.package.assemble import (
    build_burn_plan,
    copy_reports,
    deliverable_dir,
    require_inputs,
    write_info,
)
from grillmaster.package.errors import PackageError
from grillmaster.package.render import BurnPlan

if TYPE_CHECKING:
    from pathlib import Path

BRIEFING = Briefing(
    summary="demo",
    characters=[],
    proper_nouns=[TermMapping(source="松本", target="松本")],
    glossary=[],
    catchphrases=[],
    tone_notes="",
    segment_summaries=[],
)


def test_info_puts_titles_first_then_the_briefing_view(tmp_path: Path):
    titles = {"titles": [{"title": "爆笑", "reason": "r"}]}

    target = write_info(tmp_path, titles=titles, briefing=BRIEFING)

    info = json.loads(target.read_text(encoding="utf-8"))
    assert target == tmp_path / "info.json"
    assert next(iter(info)) == "titles"
    assert info["summary"] == "demo"
    assert info["proper_nouns"] == {"松本": "松本"}


def test_info_without_titles_is_the_briefing(tmp_path: Path):
    info = json.loads(
        write_info(tmp_path, titles=None, briefing=BRIEFING).read_text("utf-8")
    )
    assert info == BRIEFING.prompt_dict()


def test_reports_copy_only_what_exists(tmp_path: Path):
    source = tmp_path / "refine.md"
    source.write_text("refine report", encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()

    copies = copy_reports(
        target, {"refine.md": source, "glossary_check.md": tmp_path / "missing.md"}
    )

    assert copies == [target / "refine.md"]
    assert (target / "refine.md").read_text(encoding="utf-8") == "refine report"


def test_deliverable_dir_replaces_an_old_folder(tmp_path: Path):
    destination = tmp_path / "package" / "demo"
    destination.mkdir(parents=True)
    (destination / "stale.mp4").write_bytes(b"old")

    with deliverable_dir(destination) as target:
        (target / "video.mp4").write_bytes(b"new")

    assert sorted(path.name for path in destination.iterdir()) == ["video.mp4"]


def test_deliverable_dir_is_removed_when_the_body_fails(tmp_path: Path):
    destination = tmp_path / "package" / "demo"

    def fill_then_fail() -> None:
        with deliverable_dir(destination) as target:
            (target / "cover.png").write_bytes(b"cover")
            raise RuntimeError("render failed")

    with pytest.raises(RuntimeError):
        fill_then_fail()
    assert not destination.exists()
    assert list(destination.parent.iterdir()) == []


def test_deliverable_dir_builds_under_a_staging_name(tmp_path: Path):
    destination = tmp_path / "package" / "demo"

    with deliverable_dir(destination) as target:
        assert target == tmp_path / "package" / "demo.partial"
        assert not destination.exists()
        (target / "video.mp4").write_bytes(b"new")

    assert sorted(path.name for path in destination.parent.iterdir()) == ["demo"]


def test_a_failed_repackage_keeps_the_previous_deliverable(tmp_path: Path):
    destination = tmp_path / "package" / "demo"
    destination.mkdir(parents=True)
    (destination / "video.mp4").write_bytes(b"old")

    def interrupted() -> None:
        with deliverable_dir(destination) as target:
            (target / "video.mp4").write_bytes(b"half")
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        interrupted()

    assert (destination / "video.mp4").read_bytes() == b"old"
    assert sorted(path.name for path in destination.parent.iterdir()) == ["demo"]


def test_a_stale_staging_folder_is_cleared_first(tmp_path: Path):
    destination = tmp_path / "package" / "demo"
    stale = tmp_path / "package" / "demo.partial"
    stale.mkdir(parents=True)
    (stale / "leftover.mp4").write_bytes(b"crash")

    with deliverable_dir(destination) as target:
        assert list(target.iterdir()) == []


def test_require_inputs_names_the_missing_file(tmp_path: Path):
    present = tmp_path / "video.mp4"
    present.write_bytes(b"video")
    with pytest.raises(PackageError, match=r"cht\.ass"):
        require_inputs(present, tmp_path / "cht.ass")


# --- burn plan (ported from the legacy chat burn plan) --------------------------


def write_chat(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    log = TranslatedChatLog(
        messages=[
            TranslatedChatMessage(
                id=0, seconds=1.0, author="@a", text="池田", translation="池田"
            )
        ]
    )
    path.write_text(log.model_dump_json(), encoding="utf-8")


def plan_for(project: Path, layout: ChatLayout) -> BurnPlan:
    return build_burn_plan(
        PackageFfmpeg({project / "video.mp4": 4.0}),
        video=project / "video.mp4",
        dialogue=project / "subs" / "cht.ass",
        chat=project / "subs" / "chat.cht.json",
        chat_ass=project / "work" / "package" / "chat.ass",
        layout=layout,
    )


def test_dialogue_alone_without_chat_or_with_layout_none(project: Path):
    dialogue_only = BurnPlan.dialogue(project / "subs" / "cht.ass")
    assert plan_for(project, ChatLayout.SIDE) == dialogue_only
    write_chat(project / "subs" / "chat.cht.json")
    assert plan_for(project, ChatLayout.NONE) == dialogue_only
    assert not (project / "work" / "package" / "chat.ass").exists()


def test_side_layout_letterboxes_the_picture_beside_the_chat(project: Path):
    write_chat(project / "subs" / "chat.cht.json")

    plan = plan_for(project, ChatLayout.SIDE)

    assert plan.picture == PictureBox(x=0, y=108, width=1536, height=864)
    # Chat under the dialogue; the dialogue sits in the bottom bar, centred
    # over the picture.
    assert [layer.filter(project) for layer in plan.layers] == [
        "subtitles=work/package/chat.ass",
        "subtitles=subs/cht.ass:force_style='MarginR=394,MarginV=24'",
    ]
    ass = (project / "work" / "package" / "chat.ass").read_text(encoding="utf-8-sig")
    # Messages sit straight on the black column: no panel background.
    assert "Dialogue: 0," not in ass
    assert r"\pos(1550," in ass


def test_overlay_layout_keeps_the_full_frame(project: Path):
    write_chat(project / "subs" / "chat.cht.json")

    plan = plan_for(project, ChatLayout.OVERLAY)

    assert plan.canvas_filter is None
    assert [layer.filter(project) for layer in plan.layers] == [
        "subtitles=work/package/chat.ass",
        "subtitles=subs/cht.ass",
    ]


def test_unreadable_chat_falls_back_to_dialogue_only(project: Path):
    chat = project / "subs" / "chat.cht.json"
    chat.write_text("not json", encoding="utf-8")
    assert plan_for(project, ChatLayout.SIDE) == BurnPlan.dialogue(
        project / "subs" / "cht.ass"
    )


def test_undecodable_chat_falls_back_to_dialogue_only(project: Path):
    chat = project / "subs" / "chat.cht.json"
    chat.write_bytes(b'{"messages": ["\xff\xfe"]}')
    assert plan_for(project, ChatLayout.SIDE) == BurnPlan.dialogue(
        project / "subs" / "cht.ass"
    )
