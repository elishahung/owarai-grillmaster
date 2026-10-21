from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from pydantic import SecretStr, ValidationError
from tests.fakes import FakeAgentRunner, make_blocks, make_briefing

from grillmaster.agent_tools.meta import Usage
from grillmaster.agent_tools.watch_record import (
    FailedWatch,
    WatchRecord,
    failed_path,
)
from grillmaster.agents.errors import AgentOutputError
from grillmaster.config.errors import ConfigError
from grillmaster.config.model import validate_config
from grillmaster.config.secrets import Secrets
from grillmaster.core.briefing import Briefing
from grillmaster.core.json_artifact import read_model, write_model
from grillmaster.core.srt import read_srt_file, write_srt_file
from grillmaster.core.stage_key import StageKey
from grillmaster.postprocess.proofread import (
    TASK_NAME,
    BriefingChange,
    BriefingField,
    BriefingPatch,
    Fix,
    FixCategory,
    ProofreadAnswer,
)
from grillmaster.project.layout import session_dir
from grillmaster.project.store import load_state
from grillmaster.stages import finalize, proofread
from grillmaster.stages.base import MissingArtifactError, RunOptions

if TYPE_CHECKING:
    from pathlib import Path

    from tests.stages.conftest import MakeContext

    from grillmaster.config.model import AppConfig
    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState

FIX = ProofreadAnswer(
    fixes=[
        Fix(
            block=2,
            current="line 2",
            revised=["第二行"],
            category=FixCategory.MISTRANSLATION,
            reason="原因",
            evidence=None,
        )
    ],
    briefing=BriefingPatch.model_validate(dict.fromkeys(BriefingField)),
    briefing_changes=[],
    summary="已核對。",
)
CORRECTED = make_briefing(summary="corrected")
BRIEFING_FIX = FIX.model_copy(
    update={
        "briefing": FIX.briefing.model_copy(update={"summary": "corrected"}),
        "briefing_changes": [
            BriefingChange(
                field=BriefingField.SUMMARY,
                item="",
                reason="原因",
                evidence="frame_1.jpg",
            )
        ],
    }
)


@pytest.fixture
def config_sections() -> dict[str, Any]:
    # Every block its own chunk: three seams.
    return {"translate": {"chunk_char_limit": 1}}


@pytest.fixture
def project(layout: ProjectLayout) -> ProjectLayout:
    for path in (
        layout.ja_srt,
        layout.merged_srt,
        layout.refined_srt,
        layout.glossary_checked_srt,
    ):
        write_srt_file(path, make_blocks(4))
    write_model(layout.prepass_briefing, make_briefing())
    return layout


def test_proofreads_in_the_stage_directory(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
):
    agents.script[TASK_NAME] = FIX

    result = proofread.STAGE.run(make_context(StageKey.PROOFREAD))

    assert result == "1 fixes, 0 briefing changes"
    assert read_srt_file(project.proofread_srt)[1].text == "第二行"
    assert project.effective_subtitles() == project.proofread_srt
    assert project.effective_briefing() == project.prepass_briefing
    assert project.proofread_report.is_file()
    assert project.proofread_answer.is_file()
    assert "after #1, after #2, after #3" in project.proofread_history.read_text(
        "utf-8"
    )
    task = agents.task(TASK_NAME)
    workdir = project.work_dir(StageKey.PROOFREAD)
    assert task.workdir == workdir
    assert task.session_dir == session_dir(workdir)
    assert task.tools is not None
    assert task.tools.frames is not None
    assert task.tools.frames.frames_dir == project.proofread_frames_dir
    assert task.tools.check_srt is None
    assert '"summary": "summary"' in task.prompt


PROOFREAD_WATCH_CLIP = {"features": {"proofread_watch_clip": True}}
WATCH_USAGE = Usage(
    input_tokens=17_000,
    cached_input_tokens=0,
    output_tokens=5_000,
    reasoning_tokens=4_000,
)


def _write_record(clips_dir: Path, span: str, cost_usd: float) -> None:
    record = WatchRecord(
        model="muse-spark-1.3-contributor",
        effort="high",
        usage=WATCH_USAGE,
        cost_usd=cost_usd,
        text="## 発話\n[1.0] え",
    )
    write_model(clips_dir / span / "watch.json", record)


@pytest.mark.parametrize(
    "config_data",
    [{**PROOFREAD_WATCH_CLIP, "watch_clip": {"effort": "medium"}}],
    indirect=True,
)
def test_the_feature_adds_the_watch_clip_tool(
    make_context: MakeContext,
    project: ProjectLayout,
    agents: FakeAgentRunner,
    tmp_path: Path,
):
    project.video.write_bytes(b"mp4")
    (tmp_path / ".env").write_text("META_API_KEY=key\n", encoding="utf-8")
    agents.script[TASK_NAME] = FIX

    proofread.STAGE.run(make_context(StageKey.PROOFREAD))

    tools = agents.task(TASK_NAME).tools
    assert tools is not None
    assert tools.watch_clip is not None
    assert tools.watch_clip.video == project.video
    assert tools.watch_clip.clips_dir == project.proofread_watch_clip_dir
    assert tools.watch_clip.model == "muse-spark-1.3-contributor"
    assert tools.watch_clip.effort == "medium"
    assert tools.watch_clip.env_file == tmp_path / ".env"
    assert tools.watch_clip.timeout_s == proofread.WATCH_CLIP_TIMEOUT_S
    assert "`watch_clip(start, end)`" in agents.task(TASK_NAME).prompt


@pytest.mark.parametrize("config_data", [PROOFREAD_WATCH_CLIP], indirect=True)
@pytest.mark.parametrize("fails", [False, True])
def test_the_spans_this_run_requested_add_to_the_project_spend(
    make_context: MakeContext,
    project: ProjectLayout,
    agents: FakeAgentRunner,
    fails: bool,
    tmp_path: Path,
):
    project.video.write_bytes(b"mp4")
    (tmp_path / ".env").write_text("META_API_KEY=key\n", encoding="utf-8")
    clips_dir = project.proofread_watch_clip_dir
    # A span from an earlier run, already counted then.
    _write_record(clips_dir, "0001.000-0010.000", 0.5)

    def proofread_with_calls(task: object) -> ProofreadAnswer:
        _write_record(clips_dir, "0020.000-0050.000", 0.002)
        _write_record(clips_dir, "0060.000-0090.000", 0.002)
        # A request billed without an answer.
        failed = FailedWatch(
            model="muse-spark-1.3-contributor",
            effort="high",
            usage=WATCH_USAGE,
            cost_usd=0.001,
            error="ended incomplete",
        )
        write_model(failed_path(clips_dir / "0060.000-0090.000"), failed)
        if fails:
            raise AgentOutputError("no answer")
        return FIX

    agents.script[TASK_NAME] = proofread_with_calls
    ctx = make_context(StageKey.PROOFREAD)
    ctx.state.add_watch_clip_cost(1.0)

    if fails:
        with pytest.raises(AgentOutputError):
            proofread.STAGE.run(ctx)
    else:
        result = proofread.STAGE.run(ctx)
        assert result == (
            "1 fixes, 0 briefing changes · watch_clip $0.0050 · project $1.0050"
        )
    assert ctx.state.watch_clip_cost_usd == pytest.approx(1.005)
    assert load_state(project).watch_clip_cost_usd == pytest.approx(1.005)


@pytest.mark.parametrize(
    ("features", "expected"),
    [({}, "off"), ({"proofread_watch_clip": True}, "muse-spark-1.3-contributor/high")],
)
def test_the_params_record_the_watch_clip_model(
    tmp_path: Path,
    roles: dict[str, str],
    features: dict[str, bool],
    expected: str,
):
    config = validate_config(
        {"agents": {"roles": roles}, "features": features}, root=tmp_path
    )
    assert proofread.STAGE.params(config)["watch_clip"] == expected


def test_the_preflight_needs_the_meta_key_only_with_the_feature(
    tmp_path: Path, roles: dict[str, str], secrets: Secrets
):
    def config(**features: bool) -> AppConfig:
        return validate_config(
            {"agents": {"roles": roles}, "features": features}, root=tmp_path
        )

    proofread.STAGE.preflight(config(), secrets)
    proofread.STAGE.preflight(
        config(proofread_watch_clip=True), Secrets(meta_api_key=SecretStr("key"))
    )
    with pytest.raises(ConfigError, match="META_API_KEY is not set"):
        proofread.STAGE.preflight(config(proofread_watch_clip=True), secrets)


def test_an_unpriced_watch_clip_model_fails_the_config(
    tmp_path: Path, roles: dict[str, str]
):
    with pytest.raises(ValidationError, match=r"watch_clip\.model"):
        validate_config(
            {"agents": {"roles": roles}, "watch_clip": {"model": "muse-spark-9"}},
            root=tmp_path,
        )


@pytest.mark.parametrize("config_data", [PROOFREAD_WATCH_CLIP], indirect=True)
def test_a_key_only_in_the_environment_fails_before_the_proofread(
    make_context: MakeContext,
    project: ProjectLayout,
    agents: FakeAgentRunner,
    monkeypatch: pytest.MonkeyPatch,
):
    project.video.write_bytes(b"mp4")
    monkeypatch.setenv("META_API_KEY", "key")
    with pytest.raises(ConfigError, match="reads it from there alone"):
        proofread.STAGE.run(make_context(StageKey.PROOFREAD))
    assert agents.tasks == []


def test_without_the_feature_there_is_no_watch_clip_tool(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
):
    agents.script[TASK_NAME] = FIX

    proofread.STAGE.run(make_context(StageKey.PROOFREAD))

    tools = agents.task(TASK_NAME).tools
    assert tools is not None
    assert tools.watch_clip is None


def test_a_corrected_briefing_becomes_the_effective_one(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
):
    write_model(project.glossary_briefing, make_briefing(summary="checked"))
    agents.script[TASK_NAME] = BRIEFING_FIX

    result = proofread.STAGE.run(make_context(StageKey.PROOFREAD))

    assert result == "1 fixes, 1 briefing changes"
    assert project.effective_briefing() == project.proofread_briefing
    assert read_model(project.proofread_briefing, Briefing) == CORRECTED
    # It proofread the glossary check's copy.
    assert '"summary": "checked"' in agents.task(TASK_NAME).prompt


def test_a_rerun_reads_the_earlier_briefing_again(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
):
    agents.script[TASK_NAME] = BRIEFING_FIX
    proofread.STAGE.run(make_context(StageKey.PROOFREAD))
    agents.script[TASK_NAME] = FIX

    proofread.STAGE.run(make_context(StageKey.PROOFREAD))

    # Never an earlier attempt's own copy.
    assert '"summary": "summary"' in agents.tasks[-1].prompt
    assert not project.proofread_briefing.exists()
    assert project.effective_briefing() == project.prepass_briefing


def test_needs_the_glossary_check(make_context: MakeContext, project: ProjectLayout):
    project.glossary_checked_srt.unlink()

    with pytest.raises(MissingArtifactError, match="glossary"):
        proofread.STAGE.run(make_context(StageKey.PROOFREAD))


def test_skips_once_finalize_ran(
    make_context: MakeContext,
    project: ProjectLayout,
    state: ProjectState,
    agents: FakeAgentRunner,
):
    state.mark_done(StageKey.FINALIZE, elapsed_s=1.0)

    result = proofread.STAGE.run(make_context(StageKey.PROOFREAD))

    assert result == "skipped: finalize already ran"
    assert agents.tasks == []
    assert project.effective_subtitles() == project.glossary_checked_srt


def test_finalize_reads_the_proofread_subtitles(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
):
    agents.script[TASK_NAME] = FIX
    proofread.STAGE.run(make_context(StageKey.PROOFREAD))

    finalize.STAGE.run(make_context(StageKey.FINALIZE))

    assert "第二行" in project.cht_srt.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("flag", "feature", "expected"),
    [(False, False, False), (True, False, True), (False, True, True)],
)
def test_runs_by_the_flag_or_the_feature(
    tmp_path: Path,
    state: ProjectState,
    roles: dict[str, str],
    *,
    flag: bool,
    feature: bool,
    expected: bool,
):
    config = validate_config(
        {"agents": {"roles": roles}, "features": {"proofread": feature}}, root=tmp_path
    )
    options = RunOptions(source=state.source_id, proofread=flag)

    assert proofread.STAGE.enabled(options, config) is expected
