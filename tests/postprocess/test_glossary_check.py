from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from tests.fakes import FakeAgentRunner, frames_tool

from grillmaster.agents.errors import AgentOutputError, ValidationFailure
from grillmaster.agents.task import FilesOutput
from grillmaster.core.briefing import Briefing, TermMapping
from grillmaster.core.json_artifact import read_model, write_model
from grillmaster.core.model_spec import Role
from grillmaster.core.srt import SrtBlock
from grillmaster.core.tool_session import SrtCheckTool, ToolSession
from grillmaster.glossary.fixed import (
    FIXED_GLOSSARY_GUIDE_PATH,
    FIXED_GLOSSARY_PATH,
    FixedGlossary,
    GlossaryEntry,
)
from grillmaster.postprocess.errors import PostprocessError

if TYPE_CHECKING:
    from grillmaster.agents.task import AgentTask
from grillmaster.postprocess.glossary_check import (
    NO_SUSPECTS,
    TASK_NAME,
    GlossaryInputs,
    build_glossary_task,
    check_glossary,
    finish_glossary_check,
    render_suspect_list,
    suspect_blocks,
)

GLOSSARY = FixedGlossary(
    others=(
        GlossaryEntry(jp=("ギャロップ",), zh="Gallop"),
        GlossaryEntry(jp=("ロングコートダディ",), zh="Long Coat Daddy"),
    )
)

HAN_ONLY_SRT = """1
00:00:01,000 --> 00:00:02,000
這是純中文字幕

2
00:00:02,000 --> 00:00:03,000
完全沒有英文或假名
"""

BRIEFING = Briefing(
    summary="summary",
    characters=[],
    proper_nouns=[TermMapping(source="盛山", target="盛山")],
    glossary=[],
    catchphrases=[],
    tone_notes="tone",
    segment_summaries=[],
)


def block(text: str, index: int = 2) -> SrtBlock:
    return SrtBlock(index, "00:00:02,000 --> 00:00:03,000", text)


@pytest.mark.parametrize(
    ("text", "flagged"),
    [
        pytest.param("完全沒有英文或假名", False, id="han-only"),
        pytest.param("他在コーナー登場", True, id="kana-run"),
        pytest.param("他拿到A獎", False, id="single-latin"),
        pytest.param("這個ア沒問題", False, id="single-kana"),
        pytest.param("他在Gallop壓軸登場", False, id="exact-glossary-term"),
        pytest.param("他喜歡Long Coat的演出", True, id="partial-glossary-term"),
        pytest.param("他看了GallopXY節目", True, id="term-inside-larger-token"),
        pytest.param("Gallop和OK", True, id="other-run-left"),
    ],
)
def test_suspect_detection(text: str, flagged: bool):
    assert bool(suspect_blocks([block(text)], GLOSSARY)) is flagged


def test_suspect_list_rendering():
    assert render_suspect_list([]) == NO_SUSPECTS
    assert render_suspect_list([block("第一行\nコーナー", index=7)]) == (
        "- #7: 第一行 コーナー"
    )


@pytest.fixture
def inputs(tmp_path: Path) -> GlossaryInputs:
    stage = tmp_path / "work" / "11_glossary"
    stage.mkdir(parents=True)
    refined = tmp_path / "work" / "10_refine" / "refined.srt"
    refined.parent.mkdir(parents=True)
    refined.write_text(HAN_ONLY_SRT, encoding="utf-8")
    briefing = tmp_path / "work" / "08_prepass" / "briefing.json"
    write_model(briefing, BRIEFING)
    return GlossaryInputs(
        refined_srt=refined,
        ja_srt=tmp_path / "subs" / "ja.srt",
        briefing=briefing,
        official_srt=None,
        fixed_glossary=GLOSSARY,
        fixed_glossary_path=FIXED_GLOSSARY_PATH,
        fixed_glossary_guide_path=FIXED_GLOSSARY_GUIDE_PATH,
        workdir=stage,
        output_srt=stage / "checked.srt",
        report=stage / "report.md",
        briefing_candidate=stage / "briefing.candidate.json",
        corrected_briefing=stage / "briefing.json",
    )


@pytest.fixture
def tools(inputs: GlossaryInputs, tmp_path: Path) -> ToolSession:
    return ToolSession(
        project_root=tmp_path,
        frames=frames_tool(tmp_path, tmp_path / "work" / "11_glossary" / "frames"),
        check_srt=SrtCheckTool(reference_srt=inputs.refined_srt),
    )


def build(inputs: GlossaryInputs, tools: ToolSession):
    return build_glossary_task(
        inputs,
        session_dir=inputs.workdir / "session",
        tools=tools,
    )


def validate(inputs: GlossaryInputs, tools: ToolSession) -> None:
    task = build(inputs, tools)
    assert task.validate is not None
    task.validate((inputs.output_srt,))


def copy_refined(inputs: GlossaryInputs) -> None:
    shutil.copyfile(inputs.refined_srt, inputs.output_srt)


def test_task_shape(inputs: GlossaryInputs, tools: ToolSession):
    task = build(inputs, tools)

    assert task.name == TASK_NAME
    assert task.role is Role.POSTPROCESS
    assert task.workdir == inputs.workdir
    assert task.output == FilesOutput(
        (Path("checked.srt"),),
        optional=(Path("report.md"), Path("briefing.candidate.json")),
    )
    # The package directory holding the fixed glossary is never exposed.
    assert task.add_dirs == (tools.project_root,)
    assert task.tools is not None
    assert task.tools.check_srt is not None
    assert task.tools.check_srt.reference_srt == inputs.refined_srt
    assert task.tools is tools


def test_prompt_paths_and_sections(inputs: GlossaryInputs, tools: ToolSession):
    task = build(inputs, tools)

    for path in (
        inputs.refined_srt,
        inputs.ja_srt,
        inputs.briefing,
        inputs.workdir / "fixed_glossary.json",
        inputs.workdir / "fixed_glossary.md",
        inputs.output_srt,
    ):
        assert str(path) in task.instructions
    assert str(FIXED_GLOSSARY_PATH) not in task.instructions
    assert "`briefing.candidate.json`" in task.instructions
    for stale in ("video.", ".pre_pass", "pre_pass.raw", ".glossary_check"):
        assert stale not in task.instructions
    assert "Official CC reference" not in task.instructions
    assert task.prompt.startswith(
        "Priority suspect blocks (review these first; this is not the full "
        f"edit scope):\n{NO_SUSPECTS}"
    )
    assert "Glossary check is the terminology" in task.prompt


def test_official_reference_follows_the_template(
    inputs: GlossaryInputs, tools: ToolSession
):
    official = tools.project_root / "subs" / "ja.official.srt"
    task = build(
        replace(inputs, official_srt=official, program_instruction="GLOSSARY RULE"),
        tools,
    )

    assert str(official) in task.instructions
    reference = task.instructions.index("Official CC reference")
    assert task.instructions.index("Final state:") < reference
    assert reference < task.instructions.index("GLOSSARY RULE")


def test_suspects_reach_the_prompt(inputs: GlossaryInputs, tools: ToolSession):
    inputs.refined_srt.write_text(
        HAN_ONLY_SRT.replace("完全沒有英文或假名", "他在コーナー登場"), encoding="utf-8"
    )

    task = build(inputs, tools)

    assert "- #2: 他在コーナー登場" in task.prompt


def test_unchanged_copy_needs_no_report(inputs: GlossaryInputs, tools: ToolSession):
    copy_refined(inputs)

    validate(inputs, tools)
    outcome = finish_glossary_check(inputs)
    assert (outcome.srt_changed, outcome.briefing_corrected) == (False, False)


def test_changed_subtitles_require_a_report(inputs: GlossaryInputs, tools: ToolSession):
    inputs.output_srt.write_text(
        HAN_ONLY_SRT.replace("純中文字幕", "純中文台詞"), encoding="utf-8"
    )

    with pytest.raises(ValidationFailure, match=r"report\.md"):
        validate(inputs, tools)

    inputs.report.write_text("# report\n", encoding="utf-8")
    validate(inputs, tools)
    assert finish_glossary_check(inputs).srt_changed


def test_bom_alone_is_no_change(inputs: GlossaryInputs, tools: ToolSession):
    inputs.output_srt.write_text(HAN_ONLY_SRT, encoding="utf-8-sig")

    validate(inputs, tools)


def test_structural_divergence_is_rejected(inputs: GlossaryInputs, tools: ToolSession):
    inputs.output_srt.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\n這是純中文字幕\n", encoding="utf-8"
    )
    inputs.report.write_text("# report\n", encoding="utf-8")

    with pytest.raises(ValidationFailure, match="block count differs"):
        validate(inputs, tools)


def test_invalid_corrected_briefing_is_rejected(
    inputs: GlossaryInputs, tools: ToolSession
):
    copy_refined(inputs)
    inputs.briefing_candidate.write_text('{"summary": 123}', encoding="utf-8")
    inputs.report.write_text("# report\n", encoding="utf-8")

    with pytest.raises(ValidationFailure, match="schema"):
        validate(inputs, tools)


def test_briefing_correction_requires_a_report_and_is_kept(
    inputs: GlossaryInputs, tools: ToolSession
):
    copy_refined(inputs)
    corrected = BRIEFING.model_copy(update={"summary": "fixed"})
    write_model(inputs.briefing_candidate, corrected)

    with pytest.raises(ValidationFailure, match=r"briefing\.candidate\.json"):
        validate(inputs, tools)
    assert not inputs.corrected_briefing.exists()

    inputs.report.write_text("# report\n", encoding="utf-8")
    validate(inputs, tools)
    outcome = finish_glossary_check(inputs)
    assert outcome.briefing_corrected
    assert outcome.report_written
    assert read_model(inputs.corrected_briefing, Briefing) == corrected
    assert not inputs.briefing_candidate.exists()


def test_identical_briefing_copy_is_dropped(inputs: GlossaryInputs, tools: ToolSession):
    copy_refined(inputs)
    write_model(inputs.briefing_candidate, BRIEFING)

    validate(inputs, tools)
    outcome = finish_glossary_check(inputs)

    assert not outcome.briefing_corrected
    assert not inputs.corrected_briefing.exists()
    assert not inputs.briefing_candidate.exists()


def test_an_earlier_promotion_is_withdrawn_without_a_fix(inputs: GlossaryInputs):
    """A run accepted and promoted, then interrupted before the ledger, must
    not leave its correction behind when the next accepted run has none."""
    copy_refined(inputs)
    write_model(inputs.corrected_briefing, BRIEFING.model_copy(update={"summary": "x"}))

    outcome = finish_glossary_check(inputs)

    assert not outcome.briefing_corrected
    assert not inputs.corrected_briefing.exists()


def test_missing_inputs_fail_before_any_agent(
    inputs: GlossaryInputs, tools: ToolSession
):
    inputs.briefing.unlink()
    with pytest.raises(PostprocessError, match="briefing"):
        build(inputs, tools)

    inputs.refined_srt.unlink()
    with pytest.raises(PostprocessError, match="refined SRT"):
        build(inputs, tools)


def test_check_copies_the_glossary_for_the_session_only(
    inputs: GlossaryInputs, tools: ToolSession
):
    seen: list[bool] = []

    def agent(task: AgentTask[Any]) -> tuple[Path, ...]:
        seen.extend(copy.is_file() for copy in inputs.glossary_copies)
        copy_refined(inputs)
        return (inputs.output_srt,)

    outcome = check_glossary(
        inputs,
        FakeAgentRunner({TASK_NAME: agent}),
        session_dir=inputs.workdir / "session",
        tools=tools,
    )

    assert seen == [True, True]
    assert not any(copy.exists() for copy in inputs.glossary_copies)
    assert not outcome.srt_changed


def test_an_unaccepted_candidate_is_never_promoted(
    inputs: GlossaryInputs, tools: ToolSession
):
    def agent(task: AgentTask[Any]) -> tuple[Path, ...]:
        copy_refined(inputs)
        # A correction without the required report: rejected.
        write_model(
            inputs.briefing_candidate, BRIEFING.model_copy(update={"summary": "x"})
        )
        return (inputs.output_srt,)

    with pytest.raises(AgentOutputError):
        check_glossary(
            inputs,
            FakeAgentRunner({TASK_NAME: agent}),
            session_dir=inputs.workdir / "session",
            tools=tools,
        )

    assert not inputs.corrected_briefing.exists()
    assert not any(copy.exists() for copy in inputs.glossary_copies)
