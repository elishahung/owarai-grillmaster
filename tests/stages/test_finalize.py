from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from grillmaster.core.briefing import Briefing, TermMapping
from grillmaster.core.json_artifact import write_model
from grillmaster.core.stage_key import StageKey
from grillmaster.pipeline.stage import MissingArtifactError
from grillmaster.stages import finalize

if TYPE_CHECKING:
    from tests.stages.conftest import MakeContext

    from grillmaster.project.layout import ProjectLayout


def briefing(*targets: str) -> Briefing:
    return Briefing(
        summary="",
        characters=[],
        proper_nouns=[
            TermMapping(source=f"jp{i}", target=target)
            for i, target in enumerate(targets)
        ],
        glossary=[],
        catchphrases=[],
        tone_notes="",
        segment_summaries=[],
    )


@pytest.fixture
def project(layout: ProjectLayout) -> ProjectLayout:
    layout.glossary_checked_srt.parent.mkdir(parents=True)
    # Agent-written: may carry a BOM.
    layout.glossary_checked_srt.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\n他是Bob啦。\n", encoding="utf-8-sig"
    )
    write_model(layout.prepass_briefing, briefing())
    return layout


def test_writes_both_deliverables(make_context: MakeContext, project: ProjectLayout):
    finalize.STAGE.run(make_context(StageKey.FINALIZE))

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
    write_model(project.glossary_briefing, briefing("Bob"))

    finalize.STAGE.run(make_context(StageKey.FINALIZE))

    assert "他是 Bob 啦" in project.cht_srt.read_text(encoding="utf-8")


def test_missing_briefing_fails(make_context: MakeContext, project: ProjectLayout):
    project.prepass_briefing.unlink()

    with pytest.raises(MissingArtifactError, match="--from prepass"):
        finalize.STAGE.run(make_context(StageKey.FINALIZE))
    assert not project.cht_srt.exists()


def test_declares_the_subtitle_deliverables(layout: ProjectLayout):
    assert finalize.STAGE.key is StageKey.FINALIZE
    assert finalize.STAGE.outputs(layout) == (layout.cht_srt, layout.cht_ass)
