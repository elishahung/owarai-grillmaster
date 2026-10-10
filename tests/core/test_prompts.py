from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from grillmaster.core import prompts as prompts_module
from grillmaster.core.prompts import (
    frames_guidance,
    join_sections,
    load_prompt,
    render_program_instruction,
    render_template,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_join_sections_skips_empty_and_strips():
    assert (
        join_sections("  intro\n", None, "", "   \n", "\nrules  ") == "intro\n\nrules"
    )


def test_join_sections_with_nothing_is_empty():
    assert join_sections() == ""
    assert join_sections(None, " ") == ""


@pytest.fixture
def prompt_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A throwaway importable package with a `prompts/` directory."""
    name = f"grill_prompt_fixture_{tmp_path.name.replace('-', '_')}"
    prompts = tmp_path / name / "prompts"
    prompts.mkdir(parents=True)
    (tmp_path / name / "__init__.py").write_text("", encoding="utf-8")
    (prompts / "task.md").write_text("\n# 任務\n\n翻譯。\n\n", encoding="utf-8")
    (prompts / "slots.md").write_text(
        'Read {source} into {output}. Example: {"index": 1} {Not A Slot}',
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    load_prompt.cache_clear()
    return name


def test_load_prompt_reads_and_strips(prompt_package: str):
    assert load_prompt(prompt_package, "task.md") == "# 任務\n\n翻譯。"


def test_load_prompt_missing_file_raises(prompt_package: str):
    with pytest.raises(FileNotFoundError):
        load_prompt(prompt_package, "missing.md")


def test_program_instruction_wraps_text_in_the_header():
    rendered = render_program_instruction("keep 芸人 names")
    template = load_prompt("grillmaster.core", "program_instruction.md")
    assert rendered == template.replace("{instructions}", "keep 芸人 names")
    assert rendered.endswith("\n\nkeep 芸人 names")


def test_empty_program_instruction_renders_nothing():
    assert render_program_instruction("") == ""


def test_render_template_fills_every_slot_verbatim(prompt_package: str):
    rendered = render_template(
        prompt_package, "slots.md", source="a.srt", output="{output} b.srt"
    )
    assert rendered == (
        'Read a.srt into {output} b.srt. Example: {"index": 1} {Not A Slot}'
    )


@pytest.mark.parametrize(
    "values",
    [
        pytest.param({"source": "a"}, id="missing"),
        pytest.param({"source": "a", "output": "b", "extra": "c"}, id="unused"),
    ],
)
def test_render_template_slots_must_match_the_values(
    prompt_package: str, values: dict[str, str]
):
    with pytest.raises(ValueError, match="do not match"):
        render_template(prompt_package, "slots.md", **values)


def test_frames_guidance_points_at_the_stated_window():
    text = frames_guidance("STAGE FRAGMENT", None)
    assert text.startswith("## On-demand video frames")
    assert "the window stated for `get_frames` under 【可用工具】" in text
    assert text.endswith("\n\nSTAGE FRAGMENT")
    assert "{" not in text


# Every template rendered through `render_template` by the shipped code.
RENDERED_TEMPLATES = [
    ("grillmaster.core", "program_instruction.md"),
    ("grillmaster.postprocess", "refine.md"),
    ("grillmaster.postprocess", "glossary_check.md"),
    ("grillmaster.postprocess", "glossary_suspects.md"),
    ("grillmaster.postprocess", "official_subtitle_reference.md"),
]
# A brace pair around a bare word: a slot the slot pattern failed to match.
_LEFTOVER_SLOT = re.compile(r"\{[^{}\s\"':,]+\}")


@pytest.mark.parametrize(("package", "name"), RENDERED_TEMPLATES)
def test_shipped_templates_leave_no_unmatched_slot(package: str, name: str):
    slots = set(prompts_module._SLOT.findall(load_prompt(package, name)))
    assert slots
    rendered = render_template(package, name, **dict.fromkeys(slots, "VALUE"))
    assert _LEFTOVER_SLOT.findall(rendered) == []
