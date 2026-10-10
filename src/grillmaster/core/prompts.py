"""Prompt templates live as `.md` files under each package's `prompts/` dir."""

from __future__ import annotations

import re
from functools import cache
from importlib.resources import files

_PACKAGE = "grillmaster.core"
# `{name}` slots; JSON examples such as `{"source": ...}` never match.
_SLOT = re.compile(r"\{([A-Za-z0-9_]+)\}")


@cache
def load_prompt(package: str, name: str) -> str:
    """Read `<package>/prompts/<name>` (e.g. `load_prompt(__package__, "refine.md")`)."""
    return (files(package) / "prompts" / name).read_text(encoding="utf-8").strip()


def render_template(package: str, name: str, **values: str) -> str:
    """`<package>/prompts/<name>` with every `{slot}` filled from `values`.

    The template's slots and `values` must match exactly (`ValueError`
    otherwise), so a renamed slot fails loudly instead of reaching the agent
    as a literal `{slot}`. Values are inserted verbatim, never re-scanned.
    """
    template = load_prompt(package, name)
    slots = set(_SLOT.findall(template))
    if slots != set(values):
        raise ValueError(
            f"{package}/prompts/{name}: template slots {sorted(slots)} do not "
            f"match values {sorted(values)}"
        )
    return _SLOT.sub(lambda match: values[match.group(1)], template)


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
    return render_template(_PACKAGE, "program_instruction.md", instructions=text)


def frames_guidance(*fragments: str | None) -> str:
    """The generic `get_frames` paragraph, then a stage's own "when to use"
    fragments.

    The window itself is not repeated here: the agents layer states it with
    the tool list, so the paragraph points there.
    """
    return join_sections(load_prompt(_PACKAGE, "frames_tool.md"), *fragments)
