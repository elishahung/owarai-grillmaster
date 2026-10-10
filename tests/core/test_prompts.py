from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from grillmaster.core.prompts import (
    join_sections,
    load_prompt,
    render_program_instruction,
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
