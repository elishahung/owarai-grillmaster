"""Final proofread: an optional pass over the glossary-checked SRT and briefing.

Python builds the dossier the agent must read in full and sends it inline
(a file that size costs paged reads, a turn each): `aligned.tsv`, every
block's Japanese and current Chinese side by side, `history.md`, the
earlier versions of each block a pass changed, the chunk seams and the
blocks still carrying Latin or kana, and the briefing. The tables are also
written to the stage directory for inspection. The agent answers through
the schema instead of writing files, so it pays only for what it changes
and cannot break the skeleton: fixes, each quoting the block's current text
(anchoring it to a real block), a `name_term` one citing evidence; and a
briefing patch, the replacement of each top-level field it corrects, every
changed field explained in `briefing_changes`. A violation is a repair round.
With a `watch_clip` tool in the session, the agent can also have a span of
the video heard by a speech recognizer and watched by a video model, which
report its speech, on-screen captions and scene, for lines whose ASR is
suspect.

Python then applies the accepted fixes to a copy of the checked SRT, writes
the patched briefing as this stage's own copy when it changed
(`effective_briefing` then prefers it) and renders the report from the
answer and the briefing diff, so the report is exactly what was applied.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ValidationError, field_validator

from grillmaster.agents.adapters.base import Capability
from grillmaster.agents.errors import ValidationFailure
from grillmaster.agents.task import AgentTask, SchemaOutput
from grillmaster.core.briefing import (
    Briefing,
    Catchphrase,
    Character,
    SegmentSummary,
    TermMapping,
)
from grillmaster.core.fs import atomic_write_text
from grillmaster.core.json_artifact import read_model, write_model
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
    settle_briefing,
)
from grillmaster.postprocess.errors import PostprocessError
from grillmaster.postprocess.glossary_check import suspect_blocks
from grillmaster.subtitles.structure import check_aligned

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from grillmaster.agents.runner import AgentRunner
    from grillmaster.core.tool_session import ToolSession
    from grillmaster.glossary.fixed import FixedGlossary

TASK_NAME = "proofread"

# How the dossier writes a line break inside one block.
LINE_BREAK = " / "


class FixCategory(StrEnum):
    NAME_TERM = "name_term"
    MISTRANSLATION = "mistranslation"
    OMISSION = "omission"
    REGRESSION = "regression"
    CONSISTENCY = "consistency"
    OTHER = "other"


class Fix(BaseModel):
    block: int
    # The block's text as the agent read it; anchors the fix.
    current: str
    revised: list[str]
    category: FixCategory
    reason: str
    evidence: str | None

    @field_validator("revised")
    @classmethod
    def _strip_lines(cls, lines: list[str]) -> list[str]:
        return [line.strip() for line in lines]


class BriefingField(StrEnum):
    """The `Briefing` fields (a test keeps them equal)."""

    SUMMARY = "summary"
    CHARACTERS = "characters"
    PROPER_NOUNS = "proper_nouns"
    GLOSSARY = "glossary"
    CATCHPHRASES = "catchphrases"
    TONE_NOTES = "tone_notes"
    SEGMENT_SUMMARIES = "segment_summaries"


class BriefingPatch(BaseModel):
    """A replacement value per corrected `Briefing` field; `None` keeps the
    field. The agent resends only the fields it corrects."""

    summary: str | None
    characters: list[Character] | None
    proper_nouns: list[TermMapping] | None
    glossary: list[TermMapping] | None
    catchphrases: list[Catchphrase] | None
    tone_notes: str | None
    segment_summaries: list[SegmentSummary] | None

    def apply(self, base: Briefing) -> Briefing:
        """`base` with the replaced fields; `ValidationError` when the result
        is not a valid briefing."""
        return Briefing.model_validate(
            {**base.model_dump(), **self.model_dump(exclude_none=True)}
        )


class BriefingChange(BaseModel):
    field: BriefingField
    # The entry inside the field (a character's name, a segment's range);
    # empty for a text field. For the report only.
    item: str
    reason: str
    evidence: str


class ProofreadAnswer(BaseModel):
    fixes: list[Fix]
    briefing: BriefingPatch
    briefing_changes: list[BriefingChange]
    summary: str


@dataclass(frozen=True, slots=True)
class ProofreadInputs:
    """Everything the proofread reads and writes.

    `checked_srt` is proofread; `merged_srt` and `refined_srt` are the
    earlier versions for the history; `briefing` is the briefing as the
    glossary check left it; `seams` are the last block indexes of every
    chunk but the final one; `frames_dirs` are the existing directories of
    pre-sampled frames. `workdir` is the agent's cwd; the dossier copies
    (`aligned`, `history`) and the outputs (`output_answer`, `output_srt`,
    `output_briefing`, `report`) lie inside it.
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
    output_answer: Path
    output_srt: Path
    output_briefing: Path
    report: Path
    # The program's configured proofread text; empty when none.
    program_instruction: str = ""

    @property
    def glossary_copies(self) -> tuple[Path, Path]:
        """Where the session's copies of the fixed glossary and its guide go."""
        glossary, guide = session_copy_paths(
            self.workdir, self.fixed_glossary_path, self.fixed_glossary_guide_path
        )
        return glossary, guide


def proofread_subtitles(
    inputs: ProofreadInputs,
    agents: AgentRunner,
    *,
    session_dir: Path,
    tools: ToolSession,
) -> ProofreadAnswer:
    """Run the proofread, write its outputs and return the accepted answer.

    Raises `PostprocessError` when an input SRT is missing or the versions
    do not align, and lets agent errors through; the fixed-glossary copies are
    removed either way. A previous attempt's applied SRT and briefing are
    removed first, so they exist only after an accepted session (the
    briefing only when it changed).
    """
    inputs.output_srt.unlink(missing_ok=True)
    inputs.output_briefing.unlink(missing_ok=True)
    checked = read_reference(inputs.checked_srt, "glossary-checked SRT")
    briefing = read_model(inputs.briefing, Briefing)
    aligned, history = write_dossier(inputs, checked)
    task = build_proofread_task(
        inputs,
        checked,
        briefing,
        aligned=aligned,
        history=history,
        session_dir=session_dir,
        tools=tools,
    )
    with session_copies(
        inputs.workdir, inputs.fixed_glossary_path, inputs.fixed_glossary_guide_path
    ):
        answer = agents.run(task).output
    corrected = answer.briefing.apply(briefing)
    write_model(inputs.output_answer, answer)
    atomic_write_text(
        inputs.report,
        render_report(answer, current_texts(checked), briefing, corrected),
    )
    write_srt_file(inputs.output_srt, apply_fixes(checked, answer))
    settle_briefing(corrected, briefing, inputs.output_briefing)
    return answer


# --- dossier ------------------------------------------------------------------


def flat(text: str) -> str:
    """A block's text on one line, its line breaks written `LINE_BREAK`."""
    return LINE_BREAK.join(line.strip() for line in text.splitlines())


def current_texts(checked: list[SrtBlock]) -> dict[int, str]:
    return {block.index: block.text for block in checked}


def write_dossier(inputs: ProofreadInputs, checked: list[SrtBlock]) -> tuple[str, str]:
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


def build_proofread_task(
    inputs: ProofreadInputs,
    checked: list[SrtBlock],
    briefing: Briefing,
    *,
    aligned: str,
    history: str,
    session_dir: Path,
    tools: ToolSession,
) -> AgentTask[ProofreadAnswer]:
    """The proofread call with the dossier inline; `tools` offers
    `get_frames` (and `watch_clip`, with its guidance, when configured), its
    project root is readable, and the backend must search the web."""
    glossary_copy, guide_copy = inputs.glossary_copies
    instructions = join_sections(
        render_template(
            PROMPTS,
            "proofread.md",
            refine_report=str(inputs.refine_report),
            glossary_report=str(inputs.glossary_report),
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
        f"## Briefing\n\n```json\n{briefing.model_dump_json(indent=2)}\n```",
        frames_guidance(load_prompt(PROMPTS, "proofread_frames.md")),
        (
            load_prompt(PROMPTS, "proofread_watch_clip.md")
            if tools.watch_clip is not None
            else None
        ),
    )
    return AgentTask(
        name=TASK_NAME,
        role=Role.PROOFREAD,
        instructions=instructions,
        prompt=prompt,
        session_dir=session_dir,
        workdir=inputs.workdir,
        output=SchemaOutput(ProofreadAnswer),
        tools=tools,
        add_dirs=(tools.project_root,),
        validate=make_validator(current_texts(checked), briefing),
        requires=frozenset({Capability.WEB_SEARCH}),
    )


def _squash(text: str) -> str:
    """`text` without line breaks or whitespace, for anchoring a quote."""
    return "".join(text.replace(LINE_BREAK, "").split())


def make_validator(
    current: Mapping[int, str], briefing: Briefing
) -> Callable[[ProofreadAnswer], None]:
    """`ValidationFailure` listing every fix that names no block, quotes the
    wrong text, changes nothing, repeats a block or is an unsupported
    name/term change, a briefing patch that yields no valid briefing, and
    every field the patch changes but `briefing_changes` does not explain or
    the reverse."""

    def validate(answer: ProofreadAnswer) -> None:
        problems = _fix_problems(answer.fixes, current)
        problems += _briefing_problems(answer, briefing)
        if problems:
            raise ValidationFailure.from_problems(
                "以下 fixes 或 briefing 修正有問題，請修正後回傳完整結果：", problems
            )

    return validate


def _fix_problems(fixes: list[Fix], current: Mapping[int, str]) -> list[str]:
    problems: list[str] = []
    seen: set[int] = set()
    for fix in fixes:
        where = f"#{fix.block}"
        text = current.get(fix.block)
        if text is None:
            problems.append(f"{where}：沒有這個區塊。")
            continue
        if fix.block in seen:
            problems.append(f"{where}：同一區塊出現多次，請合併成一筆。")
        seen.add(fix.block)
        if _squash(fix.current) != _squash(text):
            problems.append(f"{where}：`current` 與字幕不符，目前是「{flat(text)}」。")
        if not fix.revised or not all(fix.revised):
            problems.append(f"{where}：`revised` 不能有空行。")
        elif any(LINE_BREAK in line or "\n" in line for line in fix.revised):
            problems.append(
                f"{where}：`revised` 每個元素是一行字幕，不能含 ` / ` 或換行；"
                "換行請拆成多個元素。"
            )
        elif _squash("".join(fix.revised)) == _squash(text):
            problems.append(f"{where}：`revised` 與目前字幕相同，請刪除這筆。")
        if fix.category is FixCategory.NAME_TERM and not (fix.evidence or "").strip():
            problems.append(f"{where}：name_term 修改必須附上 evidence。")
    return problems


def _briefing_problems(answer: ProofreadAnswer, briefing: Briefing) -> list[str]:
    """The patched briefing must be valid and change exactly the fields
    `briefing_changes` explain; a field resent unchanged is no change."""
    try:
        corrected = answer.briefing.apply(briefing)
    except ValidationError as error:
        return [f"`briefing` 套用後不是有效的 briefing，請修正：\n{error}"]
    problems: list[str] = []
    # Chunks look their summary up by exact range (`segment_summary_for`).
    if _ranges(corrected) != _ranges(briefing):
        problems.append(
            "`briefing.segment_summaries` 只能修正 summary，"
            "from_index/to_index 必須與目前完全相同。"
        )
    changed = set(changed_fields(briefing, corrected))
    named = {change.field for change in answer.briefing_changes}
    return (
        problems
        + [
            f"`briefing.{field}` 有變動，但 `briefing_changes` 沒有說明；"
            "請補上說明，或把這個欄位改回 null。"
            for field in sorted(changed - named)
        ]
        + [
            f"`briefing_changes` 提到 {field}，但 `briefing.{field}` 是 null 或與目前相同。"
            for field in sorted(named - changed)
        ]
    )


def _ranges(briefing: Briefing) -> list[tuple[int, int]]:
    return [
        (segment.from_index, segment.to_index) for segment in briefing.segment_summaries
    ]


def changed_fields(base: Briefing, corrected: Briefing) -> list[BriefingField]:
    return [
        field
        for field in BriefingField
        if getattr(base, field) != getattr(corrected, field)
    ]


# --- outputs ------------------------------------------------------------------


def apply_fixes(checked: list[SrtBlock], answer: ProofreadAnswer) -> list[SrtBlock]:
    """`checked` with every fix's lines in place of its block's text."""
    revised = {fix.block: "\n".join(fix.revised) for fix in answer.fixes}
    return [
        SrtBlock(block.index, block.timecode, revised.get(block.index, block.text))
        for block in checked
    ]


def render_report(
    answer: ProofreadAnswer,
    current: Mapping[int, str],
    briefing: Briefing,
    corrected: Briefing,
) -> str:
    """The proofread report: the summary, the applied fixes by category, the
    briefing diff and the reasons for it."""
    lines = ["# 校對", "", answer.summary.strip(), ""]
    for category in FixCategory:
        rows = sorted(
            (fix for fix in answer.fixes if fix.category is category),
            key=lambda fix: fix.block,
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
            f"| {fix.block} | {_cell(flat(current[fix.block]))} | "
            f"{_cell(LINE_BREAK.join(fix.revised))} | {_cell(fix.reason)} | "
            f"{_cell(fix.evidence or '')} |"
            for fix in rows
        ]
        lines.append("")
    changed = changed_fields(briefing, corrected)
    if not changed:
        return "\n".join(lines)
    lines += [
        f"## Briefing ({len(answer.briefing_changes)})",
        "",
        "| 欄位 | 原本 | 修改後 |",
        "| --- | --- | --- |",
    ]
    for field in changed:
        old = _entries(getattr(briefing, field))
        new = _entries(getattr(corrected, field))
        removed = "；".join(entry for entry in old if entry not in new)
        added = "；".join(entry for entry in new if entry not in old)
        lines.append(f"| {field} | {_cell(removed)} | {_cell(added)} |")
    lines += [
        "",
        "| 欄位 | 項目 | 原因 | 證據 |",
        "| --- | --- | --- | --- |",
    ]
    lines += [
        f"| {change.field} | {_cell(change.item)} | {_cell(change.reason)} | "
        f"{_cell(change.evidence)} |"
        for change in answer.briefing_changes
    ]
    lines.append("")
    return "\n".join(lines)


def _entries(value: str | Sequence[BaseModel]) -> list[str]:
    """A briefing field as report entries: a text field whole, a list one
    entry per item with its values joined."""
    if isinstance(value, str):
        return [value]
    return [
        LINE_BREAK.join(str(part) for part in item.model_dump().values())
        for item in value
    ]


def _cell(text: str) -> str:
    """`text` safe inside one Markdown table cell."""
    return " ".join(text.split()).replace("|", "\\|")
