from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.fakes import make_briefing

from grillmaster.core.json_artifact import write_model
from grillmaster.core.stage_key import StageKey
from grillmaster.stages import finalize
from grillmaster.stages.base import MissingArtifactError

if TYPE_CHECKING:
    from tests.stages.conftest import MakeContext

    from grillmaster.project.layout import ProjectLayout


@pytest.fixture
def project(layout: ProjectLayout) -> ProjectLayout:
    layout.glossary_checked_srt.parent.mkdir(parents=True)
    # Agent-written: may carry a BOM.
    layout.glossary_checked_srt.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\n他是Bob啦。\n", encoding="utf-8-sig"
    )
    write_model(layout.prepass_briefing, make_briefing())
    return layout


def test_writes_both_deliverables(make_context: MakeContext, project: ProjectLayout):
    result = finalize.STAGE.run(make_context(StageKey.FINALIZE))

    assert result == "subs/cht.ass, subs/cht.srt"

    assert project.cht_srt.read_text(encoding="utf-8") == (
        "1\n00:00:01,000 --> 00:00:02,000\n他是Bob啦\n"
    )
    assert project.cht_ass.read_text(encoding="utf-8").endswith(
        "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,他是Bob啦\n"
    )
    assert not project.work_dir(StageKey.FINALIZE).exists()


def test_spaces_names_of_the_effective_briefing(
    make_context: MakeContext, project: ProjectLayout
):
    write_model(project.glossary_briefing, make_briefing(names=["Bob"]))

    finalize.STAGE.run(make_context(StageKey.FINALIZE))

    assert "他是 Bob 啦" in project.cht_srt.read_text(encoding="utf-8")


def test_missing_briefing_fails(make_context: MakeContext, project: ProjectLayout):
    project.prepass_briefing.unlink()

    with pytest.raises(MissingArtifactError, match="--from prepass"):
        finalize.STAGE.run(make_context(StageKey.FINALIZE))
    assert not project.cht_srt.exists()
