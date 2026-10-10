from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from tests.fakes import FakeAgentRunner, FakeFfmpeg, make_blocks
from tests.stages.conftest import ROLES
from tests.translate.conftest import briefing

from grillmaster.agents.errors import AgentQuotaError
from grillmaster.config.load import LoadedConfig
from grillmaster.config.model import validate_config
from grillmaster.core.briefing import Briefing
from grillmaster.core.json_artifact import read_model, write_model
from grillmaster.core.srt import SrtBlock, write_srt_file
from grillmaster.core.stage_key import StageKey
from grillmaster.project.layout import ProjectLayout
from grillmaster.project.state import Talent
from grillmaster.stages import prepass

if TYPE_CHECKING:
    from pathlib import Path

    from tests.stages.conftest import MakeContext

    from grillmaster.agents.runner import AgentRunner
    from grillmaster.config.secrets import Secrets
    from grillmaster.project.state import ProjectState

_BLOCKS = make_blocks(6)


@pytest.fixture
def config_data() -> dict[str, Any]:
    return {"agents": {"roles": dict(ROLES)}}


@pytest.fixture
def loaded(
    tmp_path: Path, secrets: Secrets, config_data: dict[str, Any]
) -> LoadedConfig:
    config = validate_config(config_data, root=tmp_path)
    return LoadedConfig(root=tmp_path, config=config, secrets=secrets)


@pytest.fixture
def fake_agents(loaded: LoadedConfig) -> FakeAgentRunner:
    return FakeAgentRunner(
        roles=loaded.config.agents.roles.specs(), script={"prepass": briefing((1, 6))}
    )


@pytest.fixture
def agents(fake_agents: FakeAgentRunner) -> AgentRunner:
    return fake_agents


@pytest.fixture
def ffmpeg() -> FakeFfmpeg:
    return FakeFfmpeg(duration=60.0)


@pytest.fixture(autouse=True)
def source_srt(layout: ProjectLayout) -> None:
    """The upstream artifacts: the Japanese SRT, the video and its audio."""
    write_srt_file(layout.ja_srt, _BLOCKS)
    layout.video.write_bytes(b"video")
    layout.audio.parent.mkdir(parents=True, exist_ok=True)
    layout.audio.write_bytes(b"audio")


def test_writes_the_briefing_from_one_agent_call(
    make_context: MakeContext,
    layout: ProjectLayout,
    fake_agents: FakeAgentRunner,
    ffmpeg: FakeFfmpeg,
) -> None:
    prepass.build(ffmpeg).run(make_context(StageKey.PREPASS))

    assert read_model(layout.prepass_briefing, Briefing) == briefing((1, 6))
    [task] = fake_agents.tasks
    workdir = layout.work_dir(StageKey.PREPASS)
    assert task.workdir == workdir
    assert task.session_dir == workdir / "session"
    # agy hears: the full track goes along, readable through `add_dirs`.
    assert task.audio == (layout.audio,)
    assert task.add_dirs == (layout.audio.parent,)
    assert task.images
    assert all(path.parent == layout.prepass_frames_dir for path in task.images)
    assert task.tools is not None
    assert task.tools.frames is not None
    assert task.tools.frames.window == (0.0, _BLOCKS[-1].time_range.end)
    assert task.tools.frames.frames_dir == layout.prepass_frames_dir
    assert task.tools.check_srt is None
    assert "Full Source Audio" in task.instructions
    assert '[{"from_index": 1, "to_index": 6}]' in task.prompt


def test_existing_briefing_is_reused_before_any_work(
    make_context: MakeContext,
    layout: ProjectLayout,
    fake_agents: FakeAgentRunner,
    ffmpeg: FakeFfmpeg,
) -> None:
    write_model(layout.prepass_briefing, briefing((1, 6), summary="cached"))

    prepass.build(ffmpeg).run(make_context(StageKey.PREPASS))

    assert fake_agents.tasks == []
    assert ffmpeg.calls == []
    assert read_model(layout.prepass_briefing, Briefing).summary == "cached"


@pytest.mark.parametrize(
    "config_data",
    [{"agents": {"roles": {**ROLES, "prepass": "codex/gpt-5.5/high"}}}],
)
def test_backend_without_audio_input_gets_no_audio(
    make_context: MakeContext, fake_agents: FakeAgentRunner, ffmpeg: FakeFfmpeg
) -> None:
    prepass.build(ffmpeg).run(make_context(StageKey.PREPASS))

    [task] = fake_agents.tasks
    assert task.audio == ()
    assert task.add_dirs == ()
    assert "Full Source Audio" not in task.instructions


@pytest.mark.parametrize(
    "config_data",
    [
        {
            "agents": {"roles": dict(ROLES)},
            "programs": {
                "series": {"番組": {"instruction": {"prepass": "番組ルール"}}}
            },
        }
    ],
)
def test_source_context_reaches_the_prompt(
    tmp_path: Path,
    make_context: MakeContext,
    state: ProjectState,
    fake_agents: FakeAgentRunner,
    ffmpeg: FakeFfmpeg,
) -> None:
    ctx = make_context(StageKey.PREPASS)
    state.translation_hint = "第2回"
    state.source.title = "番組タイトル"
    state.source.series = "番組"
    state.source.talents = [Talent(id="t1", name="浜田雅功", roles=["MC"])]
    write_srt_file(
        ctx.layout.ja_official_srt,
        [SrtBlock(1, "00:00:02,000 --> 00:00:03,000", "公式")],
    )
    parent = ProjectLayout(tmp_path / "parent")
    write_model(parent.prepass_briefing, briefing(summary="前回"))
    state.parent = parent.root

    prepass.build(ffmpeg).run(ctx)

    task = fake_agents.task("prepass")
    assert "番組ルール" in task.instructions
    assert "### OFFICIAL SOURCE METADATA" in task.instructions
    assert "### OFFICIAL CLOSED CAPTIONS" in task.instructions
    assert "### PARENT-PROJECT PRE-PASS REFERENCE" in task.instructions
    assert "【節目標題】\n番組タイトル" in task.prompt
    assert "【使用者翻譯提示】\n第2回" in task.prompt
    assert "- 浜田雅功 (MC)" in task.prompt
    assert '"summary": "前回"' in task.prompt
    assert "公式" in task.prompt


def test_parent_glossary_briefing_wins(
    tmp_path: Path,
    make_context: MakeContext,
    state: ProjectState,
    fake_agents: FakeAgentRunner,
    ffmpeg: FakeFfmpeg,
) -> None:
    parent = ProjectLayout(tmp_path / "parent")
    write_model(parent.prepass_briefing, briefing(summary="raw"))
    write_model(parent.glossary_briefing, briefing(summary="checked"))
    state.parent = parent.root

    prepass.build(ffmpeg).run(make_context(StageKey.PREPASS))

    assert '"summary": "checked"' in fake_agents.task("prepass").prompt


def test_missing_parent_briefing_fails_before_any_work(
    tmp_path: Path,
    make_context: MakeContext,
    state: ProjectState,
    fake_agents: FakeAgentRunner,
    ffmpeg: FakeFfmpeg,
) -> None:
    state.parent = tmp_path / "no_such_parent"

    with pytest.raises(FileNotFoundError):
        prepass.build(ffmpeg).run(make_context(StageKey.PREPASS))

    assert ffmpeg.calls == []
    assert fake_agents.tasks == []


def test_agent_failure_writes_no_briefing(
    make_context: MakeContext,
    layout: ProjectLayout,
    fake_agents: FakeAgentRunner,
    ffmpeg: FakeFfmpeg,
) -> None:
    fake_agents.script["prepass"] = AgentQuotaError("quota resets in 8h")

    with pytest.raises(AgentQuotaError):
        prepass.build(ffmpeg).run(make_context(StageKey.PREPASS))

    assert not layout.prepass_briefing.exists()


def test_definition(layout: ProjectLayout, loaded: LoadedConfig) -> None:
    assert prepass.STAGE.key is StageKey.PREPASS
    assert prepass.STAGE.outputs(layout) == ()
    assert prepass.STAGE.params(loaded.config) == {"model": "agy/gemini-3.1-pro/high"}
