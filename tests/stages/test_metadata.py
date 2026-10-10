from __future__ import annotations

import json
from datetime import date
from typing import TYPE_CHECKING

import pytest
from tests.sources.fakes import FakeJsonHttp, FakeYtDlp
from yt_dlp.utils import DownloadError

from grillmaster.core.source_id import Platform, SourceId
from grillmaster.core.stage_key import StageKey
from grillmaster.core.talent import Talent
from grillmaster.project.state import ProjectState, SourceInfo
from grillmaster.project.store import load_state
from grillmaster.sources import tver
from grillmaster.stages import metadata

if TYPE_CHECKING:
    from tests.stages.conftest import MakeContext

    from grillmaster.project.layout import ProjectLayout

TVER_INFO = {
    "id": "epabc123",
    "title": "水曜日のダウンタウン 第1回",
    "description": "説明",
    "release_timestamp": 1783515600,  # 2026-07-08 22:00 JST
    "series": "水曜日のダウンタウン",
    "channel": "TBS",
}
TVER_ROUTES: dict[str, object] = {
    tver.TALENTS_URL.format(id="epabc123"): {
        "talents": [{"id": "t1", "name": "浜田雅功", "genre1": "芸人"}]
    },
    tver.SESSION_URL: {"result": {"platform_uid": "u", "platform_token": "t"}},
    tver.EPISODE_URL.format(id="epabc123"): {
        "result": {"episode": {"content": {"broadcastDateLabel": "7月6日(月)放送分"}}}
    },
}
TALENT = Talent(id="t1", name="浜田雅功", roles=("芸人",))


@pytest.fixture
def state(request: pytest.FixtureRequest) -> ProjectState:
    """The TVer fixture project, unless parametrized with another `SourceId`."""
    source: SourceId = getattr(request, "param", SourceId(Platform.TVER, "epabc123"))
    return ProjectState.create(source)


@pytest.fixture
def ytdlp() -> FakeYtDlp:
    return FakeYtDlp(info=TVER_INFO)


@pytest.fixture
def http() -> FakeJsonHttp:
    return FakeJsonHttp(TVER_ROUTES)


def test_records_info_extras_and_the_broadcast_date(
    make_context: MakeContext, layout: ProjectLayout, ytdlp: FakeYtDlp
):
    metadata.STAGE.run(make_context(StageKey.METADATA))

    saved = load_state(layout)
    assert saved.name == "水曜日のダウンタウン_第1回"
    assert saved.broadcast_date == date(2026, 7, 6)
    assert saved.source == SourceInfo(
        title="水曜日のダウンタウン 第1回",
        description="説明",
        series="水曜日のダウンタウン",
        channel="TBS",
        broadcast_label="7月6日(月)放送分",
        talents=[TALENT],
    )
    assert json.loads(layout.metadata_info.read_text(encoding="utf-8")) == TVER_INFO
    (call,) = ytdlp.calls
    assert call.url == "https://tver.jp/episodes/epabc123"
    assert call.download is False
    assert call.options["cookiefile"] is None  # no `paths.cookies` configured


def test_failed_extras_leave_the_date_to_the_availability_start(
    make_context: MakeContext, layout: ProjectLayout
):
    metadata.STAGE.run(make_context(StageKey.METADATA, http=FakeJsonHttp()))

    saved = load_state(layout)
    assert saved.broadcast_date == date(2026, 7, 8)
    assert saved.source.talents == []
    assert saved.source.broadcast_label is None


def test_a_rerun_never_erases_what_an_earlier_run_found(
    make_context: MakeContext, layout: ProjectLayout
):
    metadata.STAGE.run(make_context(StageKey.METADATA))
    undated = {
        key: value for key, value in TVER_INFO.items() if key != "release_timestamp"
    }

    metadata.STAGE.run(
        make_context(
            StageKey.METADATA,
            ytdlp=FakeYtDlp(info={**undated, "title": "新タイトル"}),
            http=FakeJsonHttp(),
        )
    )

    saved = load_state(layout)
    assert saved.source.title == "新タイトル"
    assert saved.broadcast_date == date(2026, 7, 6)
    assert saved.source.broadcast_label == "7月6日(月)放送分"
    assert saved.source.talents == [TALENT]


def test_reset_clears_the_recorded_metadata(
    make_context: MakeContext, layout: ProjectLayout
):
    metadata.STAGE.run(make_context(StageKey.METADATA))
    saved = load_state(layout)

    metadata.STAGE.clear_state(saved)

    assert (saved.name, saved.broadcast_date, saved.source) == (
        None,
        None,
        SourceInfo(),
    )


@pytest.mark.parametrize(
    "state", [SourceId(Platform.BILIBILI, "BV1demo")], indirect=True
)
def test_bilibili_keeps_the_title_only(
    make_context: MakeContext, layout: ProjectLayout
):
    info = {
        "id": "BV1demo",
        "title": "標題",
        "description": "uploader notes",
        "timestamp": 1777822200,
    }

    metadata.STAGE.run(make_context(StageKey.METADATA, ytdlp=FakeYtDlp(info=info)))

    saved = load_state(layout)
    assert saved.source.title == "標題"
    assert saved.source.description is None
    assert saved.broadcast_date == date(2026, 5, 3)


def test_extraction_failure_fails_the_stage(make_context: MakeContext):
    ytdlp = FakeYtDlp(fail_with=DownloadError("gone"))

    with pytest.raises(DownloadError):
        metadata.STAGE.run(make_context(StageKey.METADATA, ytdlp=ytdlp))
