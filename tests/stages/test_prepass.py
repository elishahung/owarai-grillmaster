from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.fakes import FakeAgentRunner, FakeFfmpeg, make_blocks, make_briefing

from grillmaster.agents.errors import AgentQuotaError
from grillmaster.core.briefing import Briefing
from grillmaster.core.json_artifact import read_model, write_model
from grillmaster.core.srt import SrtBlock, write_srt_file
from grillmaster.core.stage_key import StageKey
from grillmaster.core.talent import Talent
from grillmaster.project.layout import ProjectLayout
from grillmaster.stages import prepass

if TYPE_CHECKING:
    from pathlib import Path

    from tests.stages.conftest import MakeContext

    from grillmaster.project.state import ProjectState

_BLOCKS = make_blocks(6)


@pytest.fixture
def script() -> dict[str, object]:
    return {"prepass": make_briefing((1, 6))}


@pytest.fixture
def fake_ffmpeg() -> FakeFfmpeg:
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
    agents: FakeAgentRunner,
    fake_ffmpeg: FakeFfmpeg,
) -> None:
    prepass.STAGE.run(make_context(StageKey.PREPASS))

    assert read_model(layout.prepass_briefing, Briefing) == make_briefing((1, 6))
    [task] = agents.tasks
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
    agents: FakeAgentRunner,
    fake_ffmpeg: FakeFfmpeg,
) -> None:
    write_model(layout.prepass_briefing, make_briefing((1, 6), summary="cached"))

    prepass.STAGE.run(make_context(StageKey.PREPASS))

    assert agents.tasks == []
    assert fake_ffmpeg.calls == []
    assert read_model(layout.prepass_briefing, Briefing).summary == "cached"


@pytest.mark.parametrize(
    "config_data", [{"roles": {"prepass": "codex/gpt-5.5/high"}}], indirect=True
)
def test_backend_without_audio_input_gets_no_audio(
    make_context: MakeContext, agents: FakeAgentRunner, fake_ffmpeg: FakeFfmpeg
) -> None:
    prepass.STAGE.run(make_context(StageKey.PREPASS))

    [task] = agents.tasks
    assert task.audio == ()
    assert task.add_dirs == ()
    assert "Full Source Audio" not in task.instructions


@pytest.mark.parametrize(
    "config_data",
    [{"programs": {"series": {"番組": {"instruction": {"prepass": "番組ルール"}}}}}],
    indirect=True,
)
def test_source_context_reaches_the_prompt(
    tmp_path: Path,
    make_context: MakeContext,
    state: ProjectState,
    agents: FakeAgentRunner,
    fake_ffmpeg: FakeFfmpeg,
) -> None:
    ctx = make_context(StageKey.PREPASS)
    state.translation_hint = "第2回"
    state.source.title = "番組タイトル"
    state.source.series = "番組"
    state.source.talents = [Talent(id="t1", name="浜田雅功", roles=("MC",))]
    write_srt_file(
        ctx.layout.ja_official_srt,
        [SrtBlock(1, "00:00:02,000 --> 00:00:03,000", "公式")],
    )
    parent = ProjectLayout(tmp_path / "parent")
    write_model(parent.prepass_briefing, make_briefing(summary="前回"))
    state.parent = parent.root

    prepass.STAGE.run(ctx)

    task = agents.task("prepass")
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
    agents: FakeAgentRunner,
    fake_ffmpeg: FakeFfmpeg,
) -> None:
    parent = ProjectLayout(tmp_path / "parent")
    write_model(parent.prepass_briefing, make_briefing(summary="raw"))
    write_model(parent.glossary_briefing, make_briefing(summary="checked"))
    state.parent = parent.root

    prepass.STAGE.run(make_context(StageKey.PREPASS))

    assert '"summary": "checked"' in agents.task("prepass").prompt


def test_missing_parent_briefing_fails_before_any_work(
    tmp_path: Path,
    make_context: MakeContext,
    state: ProjectState,
    agents: FakeAgentRunner,
    fake_ffmpeg: FakeFfmpeg,
) -> None:
    state.parent = tmp_path / "no_such_parent"

    with pytest.raises(FileNotFoundError):
        prepass.STAGE.run(make_context(StageKey.PREPASS))

    assert fake_ffmpeg.calls == []
    assert agents.tasks == []


def test_agent_failure_writes_no_briefing(
    make_context: MakeContext,
    layout: ProjectLayout,
    agents: FakeAgentRunner,
    fake_ffmpeg: FakeFfmpeg,
) -> None:
    agents.script["prepass"] = AgentQuotaError("quota resets in 8h")

    with pytest.raises(AgentQuotaError):
        prepass.STAGE.run(make_context(StageKey.PREPASS))

    assert not layout.prepass_briefing.exists()
