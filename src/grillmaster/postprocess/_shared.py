"""What refine and glossary check share: path-slotted prompt templates, the
frame-tool prompt, and the skeleton check of an agent-written SRT."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from grillmaster.agents.errors import ValidationFailure
from grillmaster.core.prompts import join_sections, load_prompt
from grillmaster.core.srt import read_srt_file
from grillmaster.postprocess.errors import PostprocessError
from grillmaster.subtitles.structure import check_aligned

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from grillmaster.core.srt import SrtBlock

_PACKAGE = "grillmaster.postprocess"
# `{name}` slots; JSON examples such as `{"source": ...}` never match.
_SLOT = re.compile(r"\{([a-z_]+)\}")


def render_template(name: str, values: Mapping[str, str]) -> str:
    """`prompts/<name>` with every `{slot}` filled.

    The template's slots and `values` must match exactly, so a renamed slot
    fails loudly instead of reaching the agent as a literal `{slot}`.
    """
    template = load_prompt(_PACKAGE, name)
    slots = set(_SLOT.findall(template))
    if slots != set(values):
        raise ValueError(
            f"{name}: template slots {sorted(slots)} do not match "
            f"values {sorted(values)}"
        )
    return _SLOT.sub(lambda match: values[match.group(1)], template)


def frames_prompt(stage_fragment: str) -> str:
    """The `get_frames` usage block followed by the stage's own guidance."""
    return join_sections(
        load_prompt(_PACKAGE, "frames_tool.md"), load_prompt(_PACKAGE, stage_fragment)
    )


def read_reference(path: Path, what: str) -> list[SrtBlock]:
    """The pass's input SRT; raises `PostprocessError` when it is missing."""
    if not path.is_file():
        raise PostprocessError(f"{what} missing: {path}")
    return read_srt_file(path)


def check_skeleton(
    reference: list[SrtBlock], reference_name: str, candidate: Path
) -> list[SrtBlock]:
    """The candidate's blocks; `ValidationFailure` when it cannot be parsed or
    diverges from the reference skeleton."""
    try:
        blocks = read_srt_file(candidate)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValidationFailure(
            f"`{candidate.name}` 不是有效的 SRT：{error}"
        ) from error
    problems = check_aligned(reference, blocks)
    if problems:
        raise ValidationFailure(
            f"`{candidate.name}` 與 `{reference_name}` 的骨架不一致"
            "（區塊數、編號、時間碼必須完全相同，且每個區塊都要有文字）：\n"
            + "\n".join(f"- {problem}" for problem in problems)
        )
    return blocks
