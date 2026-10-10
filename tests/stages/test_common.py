"""The definition helpers every stage shares: `enabled` predicates and
`params` builders, checked through the definitions that use them."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from grillmaster.config.model import validate_config
from grillmaster.stages import (
    audio,
    chat_fetch,
    chat_translate,
    chunks,
    cover,
    date_research,
    download,
)
from grillmaster.stages.base import RunOptions

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.config.load import LoadedConfig
    from grillmaster.project.state import ProjectState
    from grillmaster.stages.base import SideTaskDef, StageDef


@pytest.mark.parametrize(
    "stage", [chat_fetch.STAGE, chat_translate.STAGE], ids=lambda s: s.key
)
def test_chat_stages_run_only_with_chat(stage: StageDef, state: ProjectState):
    assert stage.enabled(RunOptions(source=state.source_id, chat=True))
    assert not stage.enabled(RunOptions(source=state.source_id))


@pytest.mark.parametrize("task", [cover.TASK, date_research.TASK], ids=lambda t: t.key)
@pytest.mark.parametrize(
    ("flag", "feature", "expected"),
    [(False, False, False), (True, False, True), (False, True, True)],
)
def test_side_tasks_run_by_the_flag_or_the_feature(
    task: SideTaskDef[object],
    tmp_path: Path,
    state: ProjectState,
    roles: dict[str, str],
    *,
    flag: bool,
    feature: bool,
    expected: bool,
):
    name = str(task.key)
    config = validate_config(
        {"agents": {"roles": roles}, "features": {name: feature}}, root=tmp_path
    )
    options = RunOptions(
        source=state.source_id,
        cover=flag and name == "cover",
        date_research=flag and name == "date_research",
    )

    assert task.enabled(options, config) is expected


def test_params_name_the_role_model_tool_and_extras(loaded: LoadedConfig):
    config = loaded.config
    assert audio.STAGE.params(config) == {"tool": "ffmpeg"}
    assert download.STAGE.params(config) == {"tool": "yt-dlp"}
    assert cover.TASK.params(config) == {"model": "codex/gpt-5.5/high"}
    # No chat role configured: the utility model translates chat.
    assert chat_translate.STAGE.params(config) == {"model": "codex/gpt-5.5/medium"}
    assert date_research.TASK.params(config) == {
        "model": "codex/gpt-5.5/medium",
        "web_search": "on",
    }
    assert chunks.STAGE.params(config) == {
        "model": "agy/gemini-3.1-pro/high",
        "chunk_char_limit": str(config.translate.chunk_char_limit),
    }
