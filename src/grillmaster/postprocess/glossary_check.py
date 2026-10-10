"""Glossary check: the terminology and factual-consistency pass after refine.

The agent copies the refined SRT to `checked.srt` in its stage directory and
edits only justified spans. Python flags blocks whose text still carries a
Latin or kana run as priority hints (the agent always reviews the whole
file); a block is not flagged when its only foreign content is an exact
curated `zh` rendering. The fixed glossary is copied into the workdir for
the session (the package directory is never exposed to the agent) and the
copies are deleted afterwards.

The pre-pass briefing is never edited: a verified correction is a full copy
the agent writes as `briefing.candidate.json`; only after the session is
accepted is it promoted to the stage's own `briefing.json` (when it differs),
which then becomes the effective briefing downstream, so an unaccepted
correction is never visible. Changing the subtitles or the briefing requires
a report. All of this is the task validator, so a violation is a repair
round.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.agents.errors import ValidationFailure
from grillmaster.agents.task import AgentTask, FilesOutput
from grillmaster.core.briefing import Briefing
from grillmaster.core.json_artifact import read_model, write_model
from grillmaster.core.model_spec import Role
from grillmaster.core.prompts import (
    frames_guidance,
    join_sections,
    load_prompt,
    render_program_instruction,
    render_template,
)
from grillmaster.postprocess._shared import (
    PROMPTS,
    check_skeleton,
    read_reference,
    workdir_name,
)
from grillmaster.postprocess.errors import PostprocessError

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.agents.runner import AgentRunner
    from grillmaster.core.srt import SrtBlock
    from grillmaster.core.tool_session import ToolSession
    from grillmaster.glossary.fixed import FixedGlossary

TASK_NAME = "glossary"

# Flag a block only when its text has a RUN of >=2 consecutive Latin
# letters or >=2 consecutive kana (hiragana U+3041-3096 / katakana
# U+30A1-30FA, plus the U+30FC prolonged-sound mark so words like コーナー
# stay one run). A lone stray letter or single kana is noise, not a
# glossary miss. A block mixing Han with such a run still flags.
_SUSPECT_RE = re.compile(r"[A-Za-z]{2,}|[ぁ-ゖァ-ヺー]{2,}")
# One Latin letter or kana: tells a whole curated term from a fragment of a
# larger foreign token (which must stay flagged).
_FOREIGN_CHAR_RE = re.compile(r"[A-Za-zぁ-ゖァ-ヺー]")

NO_SUSPECTS = "No priority Latin/kana suspect blocks were detected."


@dataclass(frozen=True, slots=True)
class GlossaryInputs:
    """Everything the glossary-check agent reads and writes.

    `briefing` is the pre-pass briefing (read-only). `workdir` is the
    agent's cwd; its three outputs (`output_srt`, `report`,
    `briefing_candidate`) lie inside it. `corrected_briefing` is where an
    accepted correction is promoted to. `fixed_glossary_path` and
    `fixed_glossary_guide_path` are the sources copied into the workdir.
    """

    refined_srt: Path
    ja_srt: Path
    briefing: Path
    # Normalized platform captions; `None` when the project has none.
    official_srt: Path | None
    fixed_glossary: FixedGlossary
    fixed_glossary_path: Path
    fixed_glossary_guide_path: Path
    workdir: Path
    output_srt: Path
    report: Path
    briefing_candidate: Path
    corrected_briefing: Path
    # The program's configured glossary text; empty when none.
    program_instruction: str = ""

    @property
    def glossary_copies(self) -> tuple[Path, Path]:
        """Where the session's copies of the fixed glossary and its guide go."""
        return (
            self.workdir / self.fixed_glossary_path.name,
            self.workdir / self.fixed_glossary_guide_path.name,
        )


@dataclass(frozen=True, slots=True)
class GlossaryOutcome:
    srt_changed: bool
    briefing_corrected: bool
    report_written: bool


# --- suspect detection ------------------------------------------------------


def glossary_zh_terms(glossary: FixedGlossary) -> list[str]:
    """Curated `zh` renderings that themselves carry a Latin/kana run,
    longest first.

    Only those can cancel a flag; longest-first strips a multi-word name
    (e.g. `Long Coat Daddy`) before a shorter entry it contains.
    """
    terms = {entry.zh for entry in glossary.entries()}
    return sorted(
        (term for term in terms if _SUSPECT_RE.search(term)), key=len, reverse=True
    )


def _strip_exact_terms(text: str, terms: list[str]) -> str:
    """Drop whole-token occurrences of curated terms from `text`.

    An occurrence flanked by another Latin/kana char is a fragment of a
    larger foreign token and stays.
    """
    for term in terms:
        start = 0
        while (index := text.find(term, start)) != -1:
            end = index + len(term)
            before = text[index - 1] if index > 0 else ""
            after = text[end] if end < len(text) else ""
            if (before and _FOREIGN_CHAR_RE.match(before)) or (
                after and _FOREIGN_CHAR_RE.match(after)
            ):
                start = index + 1
                continue
            text = text[:index] + text[end:]
            start = index
    return text


def is_suspect(text: str, terms: list[str]) -> bool:
    """Whether `text` has a Latin/kana run left after removing exact curated
    terms (`terms` from `glossary_zh_terms`)."""
    if not _SUSPECT_RE.search(text):
        return False
    if not terms:
        return True
    return _SUSPECT_RE.search(_strip_exact_terms(text, terms)) is not None


def suspect_blocks(blocks: list[SrtBlock], glossary: FixedGlossary) -> list[SrtBlock]:
    terms = glossary_zh_terms(glossary)
    return [block for block in blocks if is_suspect(block.text, terms)]


def render_suspect_list(blocks: list[SrtBlock]) -> str:
    if not blocks:
        return NO_SUSPECTS
    return "\n".join(
        f"- #{block.index}: {' '.join(block.text.splitlines()).strip()}"
        for block in blocks
    )


# --- the agent task ---------------------------------------------------------


def check_glossary(
    inputs: GlossaryInputs,
    agents: AgentRunner,
    *,
    session_dir: Path,
    tools: ToolSession,
) -> GlossaryOutcome:
    """Run the glossary check and settle its accepted outputs.

    Raises `PostprocessError` when an input is missing and lets agent errors
    through; the fixed-glossary copies are removed either way.
    """
    task = build_glossary_task(inputs, session_dir=session_dir, tools=tools)
    copies = inputs.glossary_copies
    sources = (inputs.fixed_glossary_path, inputs.fixed_glossary_guide_path)
    try:
        for source, copy in zip(sources, copies, strict=True):
            shutil.copyfile(source, copy)
        agents.run(task)
    finally:
        for copy in copies:
            copy.unlink(missing_ok=True)
    return finish_glossary_check(inputs)


def build_glossary_task(
    inputs: GlossaryInputs,
    *,
    session_dir: Path,
    tools: ToolSession,
) -> AgentTask[tuple[Path, ...]]:
    """The glossary-check call; raises `PostprocessError` when the refined
    SRT or the pre-pass briefing is missing. `tools` offers `get_frames` and
    `check_srt` against the refined SRT; its project root is readable."""
    reference = read_reference(inputs.refined_srt, "refined SRT before glossary check")
    original = _read_briefing(inputs.briefing)
    suspects = suspect_blocks(reference, inputs.fixed_glossary)
    reference_name = inputs.refined_srt.name
    glossary_copy, guide_copy = inputs.glossary_copies

    def validate(_paths: tuple[Path, ...]) -> None:
        check_skeleton(reference, reference_name, inputs.output_srt)
        candidate = _candidate_briefing(inputs)
        srt_changed = _srt_changed(inputs)
        briefing_changed = candidate is not None and candidate != original
        changed = [
            name
            for name, flag in (
                (inputs.output_srt.name, srt_changed),
                (inputs.briefing_candidate.name, briefing_changed),
            )
            if flag
        ]
        if changed and not inputs.report.is_file():
            raise ValidationFailure(
                f"已修改 {'、'.join(changed)}，但沒有寫出 `{inputs.report.name}`："
                "有任何修改時報告為必要，請依原本要求的格式補寫報告。"
            )

    instructions = join_sections(
        render_template(
            PROMPTS,
            "glossary_check.md",
            refined_srt=str(inputs.refined_srt),
            ja_srt=str(inputs.ja_srt),
            briefing=str(inputs.briefing),
            fixed_glossary=str(glossary_copy),
            fixed_glossary_guide=str(guide_copy),
            output_srt=workdir_name(inputs.output_srt, inputs.workdir),
            output_srt_path=str(inputs.output_srt),
            report=workdir_name(inputs.report, inputs.workdir),
            briefing_out=workdir_name(inputs.briefing_candidate, inputs.workdir),
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
        render_template(
            PROMPTS, "glossary_suspects.md", suspects=render_suspect_list(suspects)
        ),
        frames_guidance(load_prompt(PROMPTS, "glossary_frames.md")),
    )
    return AgentTask(
        name=TASK_NAME,
        role=Role.POSTPROCESS,
        instructions=instructions,
        prompt=prompt,
        session_dir=session_dir,
        workdir=inputs.workdir,
        output=FilesOutput(
            (inputs.output_srt.relative_to(inputs.workdir),),
            optional=(
                inputs.report.relative_to(inputs.workdir),
                inputs.briefing_candidate.relative_to(inputs.workdir),
            ),
        ),
        tools=tools,
        add_dirs=(tools.project_root,),
        validate=validate,
    )


def finish_glossary_check(inputs: GlossaryInputs) -> GlossaryOutcome:
    """Settle an accepted run: promote the candidate briefing when it differs
    from the pre-pass one, else remove any `corrected_briefing`, so that file
    exists exactly when the accepted run made a real fix."""
    candidate = _candidate_briefing(inputs)
    corrected = candidate is not None and candidate != _read_briefing(inputs.briefing)
    if candidate is not None and corrected:
        write_model(inputs.corrected_briefing, candidate)
        logger.info(f"Briefing correction promoted to {inputs.corrected_briefing}")
    else:
        inputs.corrected_briefing.unlink(missing_ok=True)
    inputs.briefing_candidate.unlink(missing_ok=True)
    return GlossaryOutcome(
        srt_changed=_srt_changed(inputs),
        briefing_corrected=corrected,
        report_written=inputs.report.is_file(),
    )


def _read_briefing(path: Path) -> Briefing:
    if not path.is_file():
        raise PostprocessError(
            f"pre-pass briefing missing before glossary check: {path}"
        )
    return read_model(path, Briefing)


def _candidate_briefing(inputs: GlossaryInputs) -> Briefing | None:
    """The agent's corrected briefing, if written; `ValidationFailure` when
    it does not match the schema."""
    path = inputs.briefing_candidate
    if not path.exists():
        return None
    try:
        return read_model(path, Briefing)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValidationFailure(
            f"`{path.name}` 不符合 briefing 的 schema，請修正（欄位與結構須與 "
            f"pre-pass briefing 相同）：\n{error}"
        ) from error


def _srt_changed(inputs: GlossaryInputs) -> bool:
    # utf-8-sig: a BOM the agent wrote on one side is not a change.
    return inputs.refined_srt.read_text(encoding="utf-8-sig") != (
        inputs.output_srt.read_text(encoding="utf-8-sig")
    )
