"""A manifest as the identity of a run, and `grill plan`."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest
from tests.pipeline.fakes import Journal, fake_stage
from tests.sources.fakes import FakeYtDlp

from grillmaster.core.source_id import Platform, SourceId
from grillmaster.core.stage_key import StageKey
from grillmaster.pipeline.compilation import (
    PlanRequest,
    manifest_compilation,
    manifest_source,
    plan_compilation,
    write_plan,
)
from grillmaster.pipeline.projects import find_project, open_project
from grillmaster.pipeline.registry import Pipeline
from grillmaster.project.state import Compilation, Section
from grillmaster.project.store import load_state
from grillmaster.sources.compilation import (
    CompilationError,
    CompilationManifest,
    load_manifest,
)
from grillmaster.stages.base import RunOptions

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.config.load import LoadedConfig

MANIFEST = CompilationManifest(
    id="m1r3e01",
    title="M-1 #1",
    channel="M-1グランプリ",
    entries=[
        "https://www.youtube.com/watch?v=aaa",
        "https://www.youtube.com/watch?v=bbb",
    ],
)


def manifest_options(manifest: CompilationManifest = MANIFEST) -> RunOptions:
    return RunOptions(
        source=manifest_source(manifest), compilation=manifest_compilation(manifest)
    )


def _open(loaded: LoadedConfig, options: RunOptions):
    root = loaded.projects_root
    return open_project(root, options, find_project(root, options, archive_root=None))


def test_a_manifest_becomes_the_compilation_of_a_new_project(loaded: LoadedConfig):
    layout, state = _open(loaded, manifest_options())

    assert state.source_id == SourceId(Platform.YOUTUBE, "m1r3e01")
    assert state.compilation == Compilation(
        title="M-1 #1", entries=["v=aaa", "v=bbb"], channel="M-1グランプリ"
    )
    assert state.entry_sources == (
        SourceId(Platform.YOUTUBE, "v=aaa"),
        SourceId(Platform.YOUTUBE, "v=bbb"),
    )
    assert load_state(layout) == state


def test_a_compilation_resumes_by_its_manifest_or_its_id(loaded: LoadedConfig):
    _open(loaded, manifest_options())

    _, again = _open(loaded, manifest_options())
    # A bare ID parses as some platform's video; the project keeps its own.
    _, by_id = _open(loaded, RunOptions(source=SourceId(Platform.ABEMA, "m1r3e01")))

    assert again.compilation == by_id.compilation == manifest_compilation(MANIFEST)
    assert by_id.platform is Platform.YOUTUBE


def test_a_manifest_that_changed_is_refused(loaded: LoadedConfig):
    _open(loaded, manifest_options())
    changed = MANIFEST.model_copy(
        update={"entries": [*MANIFEST.entries, "https://www.youtube.com/watch?v=ccc"]}
    )

    with pytest.raises(ValueError, match="differs from the project's compilation"):
        _open(loaded, manifest_options(changed))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"chat": True}, "--chat"),
        ({"section": Section(start=10.0)}, "--start/--to"),
    ],
)
def test_a_compilation_run_refuses_chat_and_cuts(
    loaded: LoadedConfig, change: dict[str, Any], message: str
):
    pipeline = Pipeline((fake_stage(StageKey.METADATA, Journal()),))
    options = replace(manifest_options(), **change)

    with pytest.raises(ValueError, match=message):
        pipeline.check(options, loaded.config, loaded.secrets)
    # Resumed by ID: the project's compilation decides.
    _, state = _open(loaded, manifest_options())
    by_id = replace(options, compilation=None)
    with pytest.raises(ValueError, match=message):
        pipeline.check(by_id, loaded.config, loaded.secrets, state=state)


# --- grill plan -------------------------------------------------------------

PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLx"


def playlist_info(durations: list[float]) -> dict[str, Any]:
    return {
        "title": "3回戦ネタ",
        "entries": [
            {
                "url": f"https://www.youtube.com/watch?v=id{n}",
                "title": f"video {n}",
                "duration": seconds,
            }
            for n, seconds in enumerate(durations)
        ],
    }


def plan(loaded: LoadedConfig, tmp_path: Path, durations: list[float], **fields: Any):
    request = PlanRequest(
        url=PLAYLIST_URL, id_prefix="m1r3e", out_dir=tmp_path / "plans", **fields
    )
    return plan_compilation(
        loaded, request, ytdlp=FakeYtDlp(info=playlist_info(durations))
    )


def test_a_plan_cuts_the_playlist_into_manifests(loaded: LoadedConfig, tmp_path: Path):
    planned = plan(loaded, tmp_path, [600, 600, 300, 300, 300, 300], episodes=2)

    assert [episode.manifest.id for episode in planned] == ["m1r3e01", "m1r3e02"]
    # 600+600 and 4x300 are both the mean, 1200 s.
    assert [len(episode.manifest.entries) for episode in planned] == [2, 4]
    assert planned[0].manifest.title == "3回戦ネタ #1"
    assert [entry.title for entry in planned[0].entries] == ["video 0", "video 1"]
    assert planned[0].duration == 1200
    assert not (tmp_path / "plans").exists()

    write_plan(planned)

    assert load_manifest(planned[1].path) == planned[1].manifest
    assert planned[1].path == tmp_path / "plans" / "m1r3e02.json"


def test_a_plan_never_overwrites_a_manifest(loaded: LoadedConfig, tmp_path: Path):
    planned = plan(loaded, tmp_path, [60, 60, 60, 60], episodes=2, title="T")
    planned[1].path.parent.mkdir(parents=True)
    planned[1].path.write_text("kept", encoding="utf-8")

    with pytest.raises(CompilationError, match=r"already exist.*\n  .*m1r3e02\.json"):
        write_plan(planned)
    assert not planned[0].path.exists()
    assert planned[1].path.read_text(encoding="utf-8") == "kept"


def test_minutes_choose_the_number_of_episodes(loaded: LoadedConfig, tmp_path: Path):
    planned = plan(loaded, tmp_path, [1800] * 6, minutes=60)
    assert len(planned) == 3


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({}, "exactly one of --episodes and --minutes"),
        ({"episodes": 1, "minutes": 60.0}, "exactly one"),
    ],
)
def test_a_plan_needs_one_size(
    loaded: LoadedConfig, tmp_path: Path, fields: dict[str, Any], message: str
):
    with pytest.raises(CompilationError, match=message):
        plan(loaded, tmp_path, [60, 60], **fields)


def test_a_plan_url_must_be_a_url(loaded: LoadedConfig, tmp_path: Path):
    request = PlanRequest(url="PLx", id_prefix="m", out_dir=tmp_path, episodes=1)
    with pytest.raises(CompilationError, match="Not a playlist URL"):
        plan_compilation(loaded, request, ytdlp=FakeYtDlp())


def test_a_playlist_of_any_supported_platform_is_planned(
    loaded: LoadedConfig, tmp_path: Path
):
    request = PlanRequest(
        url="https://space.bilibili.com/1/lists/2",
        id_prefix="b",
        out_dir=tmp_path,
        episodes=1,
    )
    info = {
        "title": "L",
        "entries": [
            {
                "url": f"https://www.bilibili.com/video/BV{n}x",
                "title": "t",
                "duration": 9,
            }
            for n in range(2)
        ],
    }
    (episode,) = plan_compilation(loaded, request, ytdlp=FakeYtDlp(info=info))
    assert episode.manifest.platform is Platform.BILIBILI
