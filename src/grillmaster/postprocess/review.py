"""Final review: an optional read-only pass over the glossary-checked SRT.

Python builds the dossier the agent must read in full and sends it inline
(a file that size costs paged reads, a turn each): `aligned.tsv`, every
block's Japanese and current Chinese side by side, and `history.md`, the
earlier versions of each block a pass changed, the chunk seams and the
blocks still carrying Latin or kana. Both are also written to the stage
directory for inspection. The agent answers with findings through the schema
instead of rewriting the file, so it pays only for what it changes and
cannot break the skeleton. A finding must quote the block's current text
(anchoring it to a real block) and a `name_term` one must cite evidence; a
violation is a repair round.

Python then applies the accepted findings to a copy of the checked SRT and
renders the report from them, so the report is exactly what was applied.
Briefing notes are reported, never applied.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, field_validator

from grillmaster.agents.adapters.base import Capability
from grillmaster.agents.errors import ValidationFailure
from grillmaster.agents.task import AgentTask, SchemaOutput
from grillmaster.core.fs import atomic_write_text
from grillmaster.core.json_artifact import write_model
from grillmaster.core.model_spec import Role
from grillmaster.core.prompts import (
    frames_guidance,
    join_sections,
    load_prompt,
    render_program_instruction,
    render_template,
)
from grillmaster.core.srt import SrtBlock, write_srt_file
from grillmaster.postprocess._shared import (
    PROMPTS,
    read_reference,
    session_copies,
    session_copy_paths,
)
from grillmaster.postprocess.errors import PostprocessError
from grillmaster.postprocess.glossary_check import suspect_blocks
from grillmaster.subtitles.structure import check_aligned

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from grillmaster.agents.runner import AgentRunner
    from grillmaster.core.tool_session import ToolSession
    from grillmaster.glossary.fixed import FixedGlossary

TASK_NAME = "review"

# How the dossier writes a line break inside one block.
LINE_BREAK = " / "


class FindingCategory(StrEnum):
    NAME_TERM = "name_term"
    MISTRANSLATION = "mistranslation"
    OMISSION = "omission"
    REGRESSION = "regression"
    CONSISTENCY = "consistency"
    OTHER = "other"


class ReviewFinding(BaseModel):
    block: int
    # The block's text as the agent read it; anchors the finding.
    current: str
    revised: list[str]
    category: FindingCategory
    reason: str
    evidence: str | None

    @field_validator("revised")
    @classmethod
    def _strip_lines(cls, lines: list[str]) -> list[str]:
        return [line.strip() for line in lines]


class BriefingNote(BaseModel):
    field: str
    current: str
    proposed: str
    evidence: str


class ReviewAnswer(BaseModel):
    findings: list[ReviewFinding]
    briefing_notes: list[BriefingNote]
    summary: str


@dataclass(frozen=True, slots=True)
class ReviewInputs:
    """Everything the review reads and writes.

    `checked_srt` is reviewed; `merged_srt` and `refined_srt` are the
    earlier versions for the history; `seams` are the last block indexes of
    every chunk but the final one; `frames_dirs` are the existing directories
    of pre-sampled frames. `workdir` is the agent's cwd; the dossier copies
    (`aligned`, `history`) and the outputs (`findings`, `output_srt`,
    `report`) lie inside it.
    """

    checked_srt: Path
    refined_srt: Path
    merged_srt: Path
    ja_srt: Path
    # Normalized platform captions; `None` when the project has none.
    official_srt: Path | None
    briefing: Path
    refine_report: Path
    glossary_report: Path
    frames_dirs: tuple[Path, ...]
    seams: tuple[int, ...]
    fixed_glossary: FixedGlossary
    fixed_glossary_path: Path
    fixed_glossary_guide_path: Path
    workdir: Path
    aligned: Path
    history: Path
    findings: Path
    output_srt: Path
    report: Path
    # The program's configured review text; empty when none.
    program_instruction: str = ""

    @property
    def glossary_copies(self) -> tuple[Path, Path]:
        """Where the session's copies of the fixed glossary and its guide go."""
        glossary, guide = session_copy_paths(
            self.workdir, self.fixed_glossary_path, self.fixed_glossary_guide_path
        )
        return glossary, guide


def review_subtitles(
    inputs: ReviewInputs,
    agents: AgentRunner,
    *,
    session_dir: Path,
    tools: ToolSession,
) -> ReviewAnswer:
    """Run the review, write its outputs and return the accepted answer.

    Raises `PostprocessError` when an input is missing or the versions do
    not align, and lets agent errors through; the fixed-glossary copies are
    removed either way. A previous attempt's applied SRT is removed first,
    so it exists only after an accepted session.
    """
    inputs.output_srt.unlink(missing_ok=True)
    checked = read_reference(inputs.checked_srt, "glossary-checked SRT")
    aligned, history = write_dossier(inputs, checked)
    task = build_review_task(
        inputs,
        checked,
        aligned=aligned,
        history=history,
        session_dir=session_dir,
        tools=tools,
    )
    with session_copies(
        inputs.workdir, inputs.fixed_glossary_path, inputs.fixed_glossary_guide_path
    ):
        answer = agents.run(task).output
    write_model(inputs.findings, answer)
    atomic_write_text(inputs.report, render_report(answer, current_texts(checked)))
    write_srt_file(inputs.output_srt, apply_findings(checked, answer))
    return answer


# --- dossier ------------------------------------------------------------------


def flat(text: str) -> str:
    """A block's text on one line, its line breaks written `LINE_BREAK`."""
    return LINE_BREAK.join(line.strip() for line in text.splitlines())


def current_texts(checked: list[SrtBlock]) -> dict[int, str]:
    return {block.index: block.text for block in checked}


def write_dossier(inputs: ReviewInputs, checked: list[SrtBlock]) -> tuple[str, str]:
    """`aligned.tsv` and `history.md`, written and returned; `PostprocessError`
    when a version does not keep the checked SRT's skeleton."""
    versions = {
        "ja.srt": read_reference(inputs.ja_srt, "Japanese SRT"),
        "merged chunk SRT": read_reference(inputs.merged_srt, "merged chunk SRT"),
        "refined SRT": read_reference(inputs.refined_srt, "refined SRT"),
    }
    for name, blocks in versions.items():
        if problems := check_aligned(checked, blocks):
            raise PostprocessError(
                f"{name} does not align with the checked SRT: {'; '.join(problems)}"
            )
    aligned = render_aligned(versions["ja.srt"], checked)
    history = render_history(
        versions["merged chunk SRT"],
        versions["refined SRT"],
        checked,
        seams=inputs.seams,
        suspects=[
            block.index for block in suspect_blocks(checked, inputs.fixed_glossary)
        ],
    )
    atomic_write_text(inputs.aligned, aligned)
    atomic_write_text(inputs.history, history)
    return aligned, history


def render_aligned(ja: list[SrtBlock], checked: list[SrtBlock]) -> str:
    rows = ["index\tstart\tja\tzh"]
    rows += [
        f"{zh.index}\t{zh.timecode.split(' --> ')[0]}\t{flat(source.text)}\t{flat(zh.text)}"
        for source, zh in zip(ja, checked, strict=True)
    ]
    return "\n".join(rows) + "\n"


def render_history(
    merged: list[SrtBlock],
    refined: list[SrtBlock],
    checked: list[SrtBlock],
    *,
    seams: tuple[int, ...],
    suspects: list[int],
) -> str:
    """The earlier versions of each changed block (the current text is in the
    aligned table), the seams and the suspects, by block number."""
    changes = [
        _change(chunk, refine, glossary)
        for chunk, refine, glossary in zip(merged, refined, checked, strict=True)
        if not chunk.text == refine.text == glossary.text
    ]
    return "\n".join(
        [
            (
                f"{len(changes)} of {len(checked)} blocks changed after chunk "
                "translation (current text: the aligned table):"
            ),
            *changes,
            "",
            "Chunk seams (no chunk saw its neighbours): "
            + (", ".join(f"after #{seam}" for seam in seams) or "none"),
            "Blocks still carrying Latin or kana: "
            + (", ".join(f"#{index}" for index in suspects) or "none"),
            "",
        ]
    )


def _change(chunk: SrtBlock, refine: SrtBlock, glossary: SrtBlock) -> str:
    steps = [f"chunk「{flat(chunk.text)}」"]
    if refine.text != chunk.text:
        refined = "" if refine.text == glossary.text else f"「{flat(refine.text)}」"
        steps.append(f"refine{refined}")
    if glossary.text != refine.text:
        steps.append("glossary")
    return f"- #{glossary.index}: {' → '.join(steps)}"


# --- the agent task -----------------------------------------------------------


def build_review_task(
    inputs: ReviewInputs,
    checked: list[SrtBlock],
    *,
    aligned: str,
    history: str,
    session_dir: Path,
    tools: ToolSession,
) -> AgentTask[ReviewAnswer]:
    """The review call with the dossier inline; `tools` offers `get_frames`,
    its project root is readable, and the backend must search the web."""
    glossary_copy, guide_copy = inputs.glossary_copies
    instructions = join_sections(
        render_template(
            PROMPTS,
            "review.md",
            refine_report=str(inputs.refine_report),
            glossary_report=str(inputs.glossary_report),
            briefing=str(inputs.briefing),
            fixed_glossary=str(glossary_copy),
            fixed_glossary_guide=str(guide_copy),
            frames_dirs="\n".join(f"  - `{path}`" for path in inputs.frames_dirs)
            or "  - (none)",
        ),
        (
            render_template(
                PROMPTS,
                "official_subtitle_reference.md",
                official_srt=str(inputs.official_srt),
            )
            if inputs.official_srt is not None
            else None
        ),
        render_program_instruction(inputs.program_instruction),
    )
    prompt = join_sections(
        f"## Aligned table ({len(checked)} blocks)\n\n```tsv\n{aligned}```",
        f"## Edit history\n\n{history}",
        frames_guidance(load_prompt(PROMPTS, "review_frames.md")),
    )
    return AgentTask(
        name=TASK_NAME,
        role=Role.REVIEW,
        instructions=instructions,
        prompt=prompt,
        session_dir=session_dir,
        workdir=inputs.workdir,
        output=SchemaOutput(ReviewAnswer),
        tools=tools,
        add_dirs=(tools.project_root,),
        validate=make_validator(current_texts(checked)),
        requires=frozenset({Capability.WEB_SEARCH}),
    )


def _squash(text: str) -> str:
    """`text` without line breaks or whitespace, for anchoring a quote."""
    return "".join(text.replace(LINE_BREAK, "").split())


def make_validator(current: Mapping[int, str]) -> Callable[[ReviewAnswer], None]:
    """`ValidationFailure` listing every finding that names no block, quotes
    the wrong text, changes nothing, repeats a block or is an unsupported
    name/term change."""

    def validate(answer: ReviewAnswer) -> None:
        problems: list[str] = []
        seen: set[int] = set()
        for finding in answer.findings:
            where = f"#{finding.block}"
            text = current.get(finding.block)
            if text is None:
                problems.append(f"{where}：沒有這個區塊。")
                continue
            if finding.block in seen:
                problems.append(f"{where}：同一區塊出現多次，請合併成一筆。")
            seen.add(finding.block)
            if _squash(finding.current) != _squash(text):
                problems.append(
                    f"{where}：`current` 與字幕不符，目前是「{flat(text)}」。"
                )
            if not finding.revised or not all(finding.revised):
                problems.append(f"{where}：`revised` 不能有空行。")
            elif any(LINE_BREAK in line or "\n" in line for line in finding.revised):
                problems.append(
                    f"{where}：`revised` 每個元素是一行字幕，不能含 ` / ` 或換行；"
                    "換行請拆成多個元素。"
                )
            elif _squash("".join(finding.revised)) == _squash(text):
                problems.append(f"{where}：`revised` 與目前字幕相同，請刪除這筆。")
            if (
                finding.category is FindingCategory.NAME_TERM
                and not (finding.evidence or "").strip()
            ):
                problems.append(f"{where}：name_term 修改必須附上 evidence。")
        if problems:
            raise ValidationFailure.from_problems(
                "以下 findings 有問題，請修正後回傳完整結果：", problems
            )

    return validate


# --- outputs ------------------------------------------------------------------


def apply_findings(checked: list[SrtBlock], answer: ReviewAnswer) -> list[SrtBlock]:
    """`checked` with every finding's lines in place of its block's text."""
    revised = {finding.block: "\n".join(finding.revised) for finding in answer.findings}
    return [
        SrtBlock(block.index, block.timecode, revised.get(block.index, block.text))
        for block in checked
    ]


def render_report(answer: ReviewAnswer, current: Mapping[int, str]) -> str:
    """The review report: the summary, the applied findings by category, the
    briefing notes."""
    lines = ["# 最終審閱", "", answer.summary.strip(), ""]
    for category in FindingCategory:
        rows = sorted(
            (f for f in answer.findings if f.category is category),
            key=lambda finding: finding.block,
        )
        if not rows:
            continue
        lines += [
            f"## {category} ({len(rows)})",
            "",
            "| 字幕編號 | 原字幕 | 修改後 | 原因 | 證據 |",
            "| --- | --- | --- | --- | --- |",
        ]
        lines += [
            f"| {f.block} | {_cell(flat(current[f.block]))} | "
            f"{_cell(LINE_BREAK.join(f.revised))} | {_cell(f.reason)} | "
            f"{_cell(f.evidence or '')} |"
            for f in rows
        ]
        lines.append("")
    if answer.briefing_notes:
        lines += [
            "## Briefing notes（未套用）",
            "",
            "| 欄位 | 目前 | 建議 | 證據 |",
            "| --- | --- | --- | --- |",
        ]
        lines += [
            f"| {_cell(note.field)} | {_cell(note.current)} | "
            f"{_cell(note.proposed)} | {_cell(note.evidence)} |"
            for note in answer.briefing_notes
        ]
        lines.append("")
    return "\n".join(lines)


def _cell(text: str) -> str:
    """`text` safe inside one Markdown table cell."""
    return " ".join(text.split()).replace("|", "\\|")
