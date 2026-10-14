from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from tests.fakes import FakeAgentRunner, Rounds, frames_tool

from grillmaster.agents.adapters.base import Capability
from grillmaster.agents.errors import AgentOutputError, AgentTransientError
from grillmaster.agents.task import SchemaOutput
from grillmaster.core.model_spec import Role
from grillmaster.core.srt import SrtBlock, read_srt_file, write_srt_file
from grillmaster.core.tool_session import ToolSession
from grillmaster.glossary.fixed import (
    FIXED_GLOSSARY_GUIDE_PATH,
    FIXED_GLOSSARY_PATH,
    FixedGlossary,
)
from grillmaster.postprocess.errors import PostprocessError
from grillmaster.postprocess.review import (
    TASK_NAME,
    BriefingNote,
    FindingCategory,
    ReviewAnswer,
    ReviewFinding,
    ReviewInputs,
    build_review_task,
    render_report,
    review_subtitles,
)

if TYPE_CHECKING:
    from pathlib import Path

TIMECODES = [f"00:00:0{i},000 --> 00:00:0{i},500" for i in range(1, 7)]
JA = ["一", "二", "三", "四", "五", "コーナー"]
MERGED = ["甲", "乙", "丙", "丁", "戊", "己"]
REFINED = ["甲", "乙改", "丙", "丁", "戊改", "己"]
CHECKED = ["甲", "乙改", "丙二\n第二行", "丁", "戊二", "コーナー"]


def blocks(texts: list[str]) -> list[SrtBlock]:
    return [
        SrtBlock(index, timecode, text)
        for index, (timecode, text) in enumerate(
            zip(TIMECODES, texts, strict=True), start=1
        )
    ]


def finding(
    block: int = 3,
    current: str = "丙二 / 第二行",
    revised: tuple[str, ...] = ("丙三", "第二行"),
    category: FindingCategory = FindingCategory.MISTRANSLATION,
    evidence: str | None = None,
) -> ReviewFinding:
    return ReviewFinding(
        block=block,
        current=current,
        revised=list(revised),
        category=category,
        reason="原因",
        evidence=evidence,
    )


def answer(
    *findings: ReviewFinding, notes: tuple[BriefingNote, ...] = ()
) -> ReviewAnswer:
    return ReviewAnswer(
        findings=list(findings), briefing_notes=list(notes), summary="已核對。"
    )


@pytest.fixture
def inputs(tmp_path: Path) -> ReviewInputs:
    work = tmp_path / "work"
    stage = work / "12_review"
    stage.mkdir(parents=True)
    paths = {
        "ja": tmp_path / "subs" / "ja.srt",
        "merged": work / "09_chunks" / "merged.srt",
        "refined": work / "10_refine" / "refined.srt",
        "checked": work / "11_glossary" / "checked.srt",
    }
    for key, texts in (
        ("ja", JA),
        ("merged", MERGED),
        ("refined", REFINED),
        ("checked", CHECKED),
    ):
        write_srt_file(paths[key], blocks(texts))
    return ReviewInputs(
        checked_srt=paths["checked"],
        refined_srt=paths["refined"],
        merged_srt=paths["merged"],
        ja_srt=paths["ja"],
        official_srt=None,
        briefing=work / "08_prepass" / "briefing.json",
        refine_report=work / "10_refine" / "report.md",
        glossary_report=work / "11_glossary" / "report.md",
        frames_dirs=(work / "08_prepass" / "frames",),
        seams=(3,),
        fixed_glossary=FixedGlossary(),
        fixed_glossary_path=FIXED_GLOSSARY_PATH,
        fixed_glossary_guide_path=FIXED_GLOSSARY_GUIDE_PATH,
        workdir=stage,
        aligned=stage / "aligned.tsv",
        history=stage / "history.md",
        findings=stage / "findings.json",
        output_srt=stage / "reviewed.srt",
        report=stage / "report.md",
    )


@pytest.fixture
def tools(tmp_path: Path) -> ToolSession:
    return ToolSession(
        project_root=tmp_path, frames=frames_tool(tmp_path), check_srt=None
    )


def run(
    inputs: ReviewInputs, tools: ToolSession, output: object
) -> tuple[FakeAgentRunner, int]:
    agents = FakeAgentRunner({TASK_NAME: output})
    accepted = review_subtitles(
        inputs, agents, session_dir=inputs.workdir / "session", tools=tools
    )
    return agents, len(accepted.findings)


def test_the_dossier_goes_inline_and_to_disk(inputs: ReviewInputs, tools: ToolSession):
    agents, _ = run(inputs, tools, answer())

    aligned = inputs.aligned.read_text(encoding="utf-8")
    rows = aligned.splitlines()
    assert rows[0] == "index\tstart\tja\tzh"
    assert rows[3] == "3\t00:00:03,000\t三\t丙二 / 第二行"
    assert len(rows) == 7
    history = inputs.history.read_text(encoding="utf-8")
    # Earlier versions only: the current text is in the aligned table.
    assert history.splitlines()[:5] == [
        (
            "4 of 6 blocks changed after chunk translation (current text: the "
            "aligned table):"
        ),
        "- #2: chunk「乙」 → refine",
        "- #3: chunk「丙」 → glossary",
        "- #5: chunk「戊」 → refine「戊改」 → glossary",
        "- #6: chunk「己」 → glossary",
    ]
    assert "Chunk seams (no chunk saw its neighbours): after #3" in history
    assert "Blocks still carrying Latin or kana: #6" in history
    task = agents.task(TASK_NAME)
    assert aligned in task.prompt
    assert history in task.prompt
    assert f"`{inputs.frames_dirs[0]}`" in task.instructions


def test_findings_are_applied_and_reported(inputs: ReviewInputs, tools: ToolSession):
    note = BriefingNote(field="name_zh", current="甲", proposed="乙", evidence="url")
    agents, applied = run(inputs, tools, answer(finding(), notes=(note,)))

    assert applied == 1
    reviewed = read_srt_file(inputs.output_srt)
    assert [block.text for block in reviewed] == [
        "甲",
        "乙改",
        "丙三\n第二行",
        "丁",
        "戊二",
        "コーナー",
    ]
    assert [block.timecode for block in reviewed] == TIMECODES
    report = inputs.report.read_text(encoding="utf-8")
    assert "已核對。" in report
    assert "| 3 | 丙二 / 第二行 | 丙三 / 第二行 | 原因 |  |" in report
    assert "Briefing notes（未套用）" in report
    assert json.loads(inputs.findings.read_text(encoding="utf-8"))["summary"] == (
        "已核對。"
    )
    # The fixed-glossary copies live only for the session.
    assert not any(path.exists() for path in inputs.glossary_copies)
    task = agents.task(TASK_NAME)
    assert task.role is Role.REVIEW
    assert isinstance(task.output, SchemaOutput)
    assert task.requires == frozenset({Capability.WEB_SEARCH})
    assert task.add_dirs == (tools.project_root,)


def test_an_empty_answer_copies_the_checked_subtitles(
    inputs: ReviewInputs, tools: ToolSession
):
    run(inputs, tools, answer())

    assert read_srt_file(inputs.output_srt) == read_srt_file(inputs.checked_srt)


def test_revised_lines_are_stripped_once():
    assert finding(revised=(" 丙三 ", "第二行\t")).revised == ["丙三", "第二行"]


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(finding(block=9), id="unknown-block"),
        pytest.param(finding(current="別的"), id="wrong-quote"),
        pytest.param(finding(revised=("丙二", "第二行")), id="unchanged"),
        pytest.param(finding(revised=("丙三", " ")), id="empty-line"),
        pytest.param(finding(revised=()), id="no-lines"),
        pytest.param(finding(revised=("丙三 / 第二行",)), id="table-line-break"),
        pytest.param(finding(revised=("丙三\n第二行",)), id="embedded-newline"),
        pytest.param(
            finding(category=FindingCategory.NAME_TERM), id="name-without-evidence"
        ),
    ],
)
def test_a_bad_finding_costs_a_repair_round(
    inputs: ReviewInputs, tools: ToolSession, bad: ReviewFinding
):
    good = finding(category=FindingCategory.NAME_TERM, evidence="ja #3")
    _, applied = run(inputs, tools, Rounds(answer(bad), answer(good)))

    assert applied == 1
    assert read_srt_file(inputs.output_srt)[2].text == "丙三\n第二行"


def test_a_repeated_block_is_rejected(inputs: ReviewInputs, tools: ToolSession):
    with pytest.raises(AgentOutputError):
        run(inputs, tools, answer(finding(), finding(revised=("丙四",))))


def test_a_failed_session_leaves_no_applied_copy(
    inputs: ReviewInputs, tools: ToolSession
):
    inputs.output_srt.write_text("stale", encoding="utf-8")

    with pytest.raises(AgentTransientError):
        run(inputs, tools, AgentTransientError("boom"))

    assert not inputs.output_srt.exists()
    assert not any(path.exists() for path in inputs.glossary_copies)


def test_versions_that_do_not_align_fail(inputs: ReviewInputs, tools: ToolSession):
    write_srt_file(inputs.refined_srt, blocks(REFINED)[:5])

    with pytest.raises(PostprocessError, match=r"refined SRT .*block count"):
        run(inputs, tools, answer())


def test_the_official_captions_join_the_instructions(
    inputs: ReviewInputs, tools: ToolSession, tmp_path: Path
):
    official = tmp_path / "subs" / "ja.official.srt"
    task = build_review_task(
        replace(
            inputs,
            official_srt=official,
            program_instruction="Keep 說.",
            frames_dirs=(),
        ),
        blocks(CHECKED),
        aligned="",
        history="",
        session_dir=tmp_path,
        tools=tools,
    )

    assert str(official) in task.instructions
    assert "Keep 說." in task.instructions
    assert "  - (none)" in task.instructions
    assert "## Aligned table (6 blocks)" in task.prompt


def test_report_cells_stay_in_their_column():
    report = render_report(answer(finding(revised=("A|B",))), {3: "丙二\n第二行"})
    assert "A\\|B" in report
