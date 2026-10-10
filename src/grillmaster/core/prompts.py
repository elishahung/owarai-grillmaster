"""Prompt templates live as `.md` files under each package's `prompts/` dir."""

from __future__ import annotations

from functools import cache
from importlib.resources import files

_PROGRAM_INSTRUCTION_PLACEHOLDER = "{instructions}"


@cache
def load_prompt(package: str, name: str) -> str:
    """Read `<package>/prompts/<name>` (e.g. `load_prompt(__package__, "refine.md")`)."""
    return (files(package) / "prompts" / name).read_text(encoding="utf-8").strip()


def join_sections(*sections: str | None) -> str:
    """Join non-empty prompt sections with a blank line between them."""
    return "\n\n".join(
        section.strip() for section in sections if section and section.strip()
    )


def render_program_instruction(text: str) -> str:
    """Wrap a program's configured instruction text in its prompt header.

    Empty text renders nothing, so stages can join the result unconditionally.
    """
    if not text:
        return ""
    template = load_prompt(__package__, "program_instruction.md")
    return template.replace(_PROGRAM_INSTRUCTION_PLACEHOLDER, text)
