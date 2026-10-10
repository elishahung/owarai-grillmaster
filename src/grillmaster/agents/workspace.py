"""Keep agent CLIs from loading the customizations of a checkout around their
workspace.

agy and codex walk up from each workspace directory to the nearest folder
holding a `.git` entry and load every `AGENTS.md`, `GEMINI.md` and
`.agents/` skill on the way. Agent workdirs under a source checkout (the
default projects root sits next to `grill.toml`) would hand that
repository's developer rules and skills to every task.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

# An empty file ends the CLIs' walk. Git rejects it as an invalid gitfile,
# which is harmless: nothing runs git inside an agent workspace root.
BOUNDARY_NAME = ".git"


def seal_workspace_root(root: Path) -> None:
    """Make `root` the repository root every agent CLI working below it sees."""
    root.mkdir(parents=True, exist_ok=True)
    (root / BOUNDARY_NAME).touch()
