from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from tests.fakes import FakeAgentRunner, Rounds, frames_tool, make_briefing

from grillmaster.agents.adapters.base import Capability
from grillmaster.agents.errors import AgentOutputError, AgentTransientError
from grillmaster.agents.schema import strict_json_schema
from grillmaster.agents.task import SchemaOutput
from grillmaster.core.briefing import Briefing, SegmentSummary, TermMapping
from grillmaster.core.json_artifact import read_model, write_model
from grillmaster.core.model_spec import Role
from grillmaster.core.srt import SrtBlock, read_srt_file, write_srt_file
from grillmaster.core.tool_session import ToolSession, WatchClipTool
from grillmaster.glossary.fixed import (
    FIXED_GLOSSARY_GUIDE_PATH,
    FIXED_GLOSSARY_PATH,
    FixedGlossary,
)
from grillmaster.postprocess.errors import PostprocessError
from grillmaster.postprocess.proofread import (
    TASK_NAME,
    BriefingChange,
    BriefingField,
    BriefingPatch,
    Fix,
    FixCategory,
    ProofreadAnswer,
    ProofreadInputs,
    build_proofread_task,
    proofread_subtitles,
    render_report,
)

if TYPE_CHECKING:
    from pathlib import Path

TIMECODES = [f"00:00:0{i},000 --> 00:00:0{i},500" for i in range(1, 7)]
JA = ["一", "二", "三", "四", "五", "コーナー"]
MERGED = ["甲", "乙", "丙", "丁", "戊", "己"]
REFINED = ["甲", "乙改", "丙", "丁", "戊改", "己"]
CHECKED = ["甲", "乙改", "丙二\n第二行", "丁", "戊二", "コーナー"]
BRIEFING = make_briefing((1, 6), names=("長岡",))
CORRECTED = make_briefing((1, 6), names=("Nakaoki",))


def blocks(texts: list[str]) -> list[SrtBlock]:
    return [
        SrtBlock(index, timecode, text)
        for index, (timecode, text) in enumerate(
            zip(TIMECODES, texts, strict=True), start=1
        )
    ]


def fix(
    block: int = 3,
    current: str = "丙二 / 第二行",
    revised: tuple[str, ...] = ("丙三", "第二行"),
    category: FixCategory = FixCategory.MISTRANSLATION,
    evidence: str | None = None,
) -> Fix:
    return Fix(
        block=block,
        current=current,
        revised=list(revised),
        category=category,
        reason="原因",
        evidence=evidence,
    )


def patch(**fields: object) -> BriefingPatch:
    return BriefingPatch.model_validate(dict.fromkeys(BriefingField) | fields)


NO_PATCH = patch()
NOUNS_PATCH = patch(proper_nouns=CORRECTED.proper_nouns)


def change(field: BriefingField = BriefingField.PROPER_NOUNS) -> BriefingChange:
    return BriefingChange(
        field=field, item="jp0", reason="字卡", evidence="frame_1.jpg"
    )


def answer(
    *fixes: Fix,
    briefing: BriefingPatch = NO_PATCH,
    changes: tuple[BriefingChange, ...] = (),
) -> ProofreadAnswer:
    return ProofreadAnswer(
        fixes=list(fixes),
        briefing=briefing,
        briefing_changes=list(changes),
        summary="已核對。",
    )


@pytest.fixture
def inputs(tmp_path: Path) -> ProofreadInputs:
    work = tmp_path / "work"
    stage = work / "12_proofread"
    stage.mkdir(parents=True)
    paths = {
        "ja": tmp_path / "subs" / "ja.voiced.srt",
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
    briefing = work / "08_prepass" / "briefing.json"
    write_model(briefing, BRIEFING)
    return ProofreadInputs(
        checked_srt=paths["checked"],
        refined_srt=paths["refined"],
        merged_srt=paths["merged"],
        ja_voiced_srt=paths["ja"],
        official_srt=None,
        briefing=briefing,
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
        output_answer=stage / "answer.json",
        output_srt=stage / "proofread.srt",
        output_briefing=stage / "briefing.json",
        report=stage / "report.md",
    )


@pytest.fixture
def tools(tmp_path: Path) -> ToolSession:
    return ToolSession(
        project_root=tmp_path,
        frames=frames_tool(tmp_path),
        check_srt=None,
        watch_clip=None,
    )


def run(
    inputs: ProofreadInputs, tools: ToolSession, output: object
) -> tuple[FakeAgentRunner, ProofreadAnswer]:
    agents = FakeAgentRunner({TASK_NAME: output})
    accepted = proofread_subtitles(
        inputs, agents, session_dir=inputs.workdir / "session", tools=tools
    )
    return agents, accepted


def test_the_dossier_goes_inline_and_to_disk(
    inputs: ProofreadInputs, tools: ToolSession
):
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
    assert BRIEFING.model_dump_json(indent=2) in task.prompt


def test_the_answer_schema_is_strict():
    schema = strict_json_schema(ProofreadAnswer)
    assert schema["properties"]["briefing"]["required"] == list(BriefingField)


def test_the_patch_covers_every_briefing_field():
    assert list(BriefingField) == list(Briefing.model_fields)
    assert list(BriefingPatch.model_fields) == list(Briefing.model_fields)


def test_fixes_and_the_briefing_are_applied_and_reported(
    inputs: ProofreadInputs, tools: ToolSession
):
    agents, accepted = run(
        inputs, tools, answer(fix(), briefing=NOUNS_PATCH, changes=(change(),))
    )

    assert len(accepted.fixes) == 1
    proofread = read_srt_file(inputs.output_srt)
    assert [block.text for block in proofread] == [
        "甲",
        "乙改",
        "丙三\n第二行",
        "丁",
        "戊二",
        "コーナー",
    ]
    assert [block.timecode for block in proofread] == TIMECODES
    assert read_model(inputs.output_briefing, Briefing) == CORRECTED
    # The input briefing is never rewritten.
    assert read_model(inputs.briefing, Briefing) == BRIEFING
    report = inputs.report.read_text(encoding="utf-8")
    assert "已核對。" in report
    assert "| 3 | 丙二 / 第二行 | 丙三 / 第二行 | 原因 |  |" in report
    assert "## Briefing (1)" in report
    assert "| proper_nouns | jp0 / 長岡 | jp0 / Nakaoki |" in report
    assert "| proper_nouns | jp0 | 字卡 | frame_1.jpg |" in report
    answer_json = json.loads(inputs.output_answer.read_text(encoding="utf-8"))
    assert answer_json["summary"] == "已核對。"
    # The fixed-glossary copies live only for the session.
    assert not any(path.exists() for path in inputs.glossary_copies)
    task = agents.task(TASK_NAME)
    assert task.role is Role.PROOFREAD
    assert isinstance(task.output, SchemaOutput)
    assert task.requires == frozenset({Capability.WEB_SEARCH})
    assert task.add_dirs == (tools.project_root,)


def test_an_empty_answer_copies_the_subtitles_and_keeps_the_briefing(
    inputs: ProofreadInputs, tools: ToolSession
):
    run(inputs, tools, answer())

    assert read_srt_file(inputs.output_srt) == read_srt_file(inputs.checked_srt)
    assert not inputs.output_briefing.exists()
    assert "## Briefing" not in inputs.report.read_text(encoding="utf-8")


def test_a_field_resent_unchanged_is_no_change(
    inputs: ProofreadInputs, tools: ToolSession
):
    run(inputs, tools, answer(briefing=patch(summary=BRIEFING.summary)))

    assert not inputs.output_briefing.exists()


def test_revised_lines_are_stripped_once():
    assert fix(revised=(" 丙三 ", "第二行\t")).revised == ["丙三", "第二行"]


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(answer(fix(block=9)), id="unknown-block"),
        pytest.param(answer(fix(current="別的")), id="wrong-quote"),
        pytest.param(answer(fix(revised=("丙二", "第二行"))), id="unchanged"),
        pytest.param(answer(fix(revised=("丙三", " "))), id="empty-line"),
        pytest.param(answer(fix(revised=())), id="no-lines"),
        pytest.param(answer(fix(revised=("丙三 / 第二行",))), id="table-line-break"),
        pytest.param(answer(fix(revised=("丙三\n第二行",))), id="embedded-newline"),
        pytest.param(
            answer(fix(category=FixCategory.NAME_TERM)), id="name-without-evidence"
        ),
        pytest.param(answer(changes=(change(),)), id="change-without-patch"),
        pytest.param(
            answer(
                briefing=patch(proper_nouns=BRIEFING.proper_nouns),
                changes=(change(),),
            ),
            id="patch-unchanged",
        ),
        pytest.param(answer(briefing=NOUNS_PATCH), id="change-not-explained"),
        pytest.param(
            answer(
                briefing=NOUNS_PATCH,
                changes=(change(), change(BriefingField.CHARACTERS)),
            ),
            id="explained-field-unchanged",
        ),
        pytest.param(
            answer(
                briefing=patch(
                    proper_nouns=[
                        TermMapping(source="jp0", target="A"),
                        TermMapping(source="jp0", target="B"),
                    ]
                ),
                changes=(change(),),
            ),
            id="invalid-briefing",
        ),
        pytest.param(
            answer(
                briefing=patch(
                    segment_summaries=[
                        SegmentSummary(from_index=1, to_index=5, summary="新")
                    ]
                ),
                changes=(change(BriefingField.SEGMENT_SUMMARIES),),
            ),
            id="segment-range-moved",
        ),
    ],
)
def test_a_bad_answer_costs_a_repair_round(
    inputs: ProofreadInputs, tools: ToolSession, bad: ProofreadAnswer
):
    good = answer(
        fix(category=FixCategory.NAME_TERM, evidence="ja #3"),
        briefing=NOUNS_PATCH,
        changes=(change(),),
    )
    _, accepted = run(inputs, tools, Rounds(bad, good))

    assert len(accepted.fixes) == 1
    assert read_srt_file(inputs.output_srt)[2].text == "丙三\n第二行"
    assert read_model(inputs.output_briefing, Briefing) == CORRECTED


def test_a_repeated_block_is_rejected(inputs: ProofreadInputs, tools: ToolSession):
    with pytest.raises(AgentOutputError):
        run(inputs, tools, answer(fix(), fix(revised=("丙四",))))


def test_a_failed_session_leaves_no_applied_copy(
    inputs: ProofreadInputs, tools: ToolSession
):
    inputs.output_srt.write_text("stale", encoding="utf-8")
    inputs.output_briefing.write_text("stale", encoding="utf-8")

    with pytest.raises(AgentTransientError):
        run(inputs, tools, AgentTransientError("boom"))

    assert not inputs.output_srt.exists()
    assert not inputs.output_briefing.exists()
    assert not any(path.exists() for path in inputs.glossary_copies)


def test_versions_that_do_not_align_fail(inputs: ProofreadInputs, tools: ToolSession):
    write_srt_file(inputs.refined_srt, blocks(REFINED)[:5])

    with pytest.raises(PostprocessError, match=r"refined SRT .*block count"):
        run(inputs, tools, answer())


def test_the_official_captions_join_the_instructions(
    inputs: ProofreadInputs, tools: ToolSession, tmp_path: Path
):
    official = tmp_path / "subs" / "ja.official.srt"
    task = build_proofread_task(
        replace(
            inputs,
            official_srt=official,
            program_instruction="Keep 說.",
            frames_dirs=(),
        ),
        blocks(CHECKED),
        BRIEFING,
        aligned="",
        history="",
        session_dir=tmp_path,
        tools=tools,
    )

    assert str(official) in task.instructions
    assert "Keep 說." in task.instructions
    assert "  - (none)" in task.instructions
    assert "## Aligned table (6 blocks)" in task.prompt


def test_the_watch_clip_tool_brings_its_guidance(
    inputs: ProofreadInputs, tools: ToolSession, tmp_path: Path
):
    def build(session: ToolSession) -> str:
        return build_proofread_task(
            inputs,
            blocks(CHECKED),
            BRIEFING,
            aligned="",
            history="",
            session_dir=tmp_path,
            tools=session,
        ).prompt

    watch_clip = WatchClipTool(
        video=tmp_path / "video.mp4",
        clips_dir=tmp_path / "watch_clip",
        model="muse-spark-1.3-contributor",
        effort="high",
        speech_phrases=(),
        env_file=tmp_path / ".env",
        timeout_s=60.0,
        max_span_s=300.0,
    )
    assert "`watch_clip(start, end)`" not in build(tools)
    assert "`watch_clip(start, end)`" in build(
        tools.model_copy(update={"watch_clip": watch_clip})
    )


def test_report_cells_stay_in_their_column():
    report = render_report(
        answer(fix(revised=("A|B",))), {3: "丙二\n第二行"}, BRIEFING, BRIEFING
    )
    assert "A\\|B" in report
