from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import pytest
from tests.sources.fakes import FakeJsonHttp
from yt_dlp.extractor.abematv import AbemaTVBaseIE

from grillmaster.core.source_id import Platform
from grillmaster.sources import abema, tver
from grillmaster.sources.base import CookiePolicy, SourceExtras, SourceTalent
from grillmaster.sources.errors import SourceHttpError
from grillmaster.sources.registry import PLATFORMS, source_platform
from grillmaster.sources.ytdlp import VideoInfo

if TYPE_CHECKING:
    from collections.abc import Iterator

TVER_SESSION = {"result": {"platform_uid": "uid", "platform_token": "token"}}
ABEMA_TOKEN = {"token": "anon"}
_ABEMA_CACHE = ("_USERTOKEN", "_DEVICE_ID", "_MEDIATOKEN")
TVER_EPISODE = tver.EPISODE_URL.format(id="epdemo1")


def video_info(**fields: object) -> VideoInfo:
    return VideoInfo.model_validate({"id": "demo", "title": "demo title", **fields})


def tver_episode(label: object) -> dict[str, object]:
    return {"result": {"episode": {"content": {"broadcastDateLabel": label}}}}


def test_every_platform_is_registered():
    assert set(PLATFORMS) == set(Platform)
    for platform, implementation in PLATFORMS.items():
        assert implementation.platform is platform


def test_only_bilibili_downloads_anonymously():
    policies = {platform: impl.cookie_policy for platform, impl in PLATFORMS.items()}
    assert policies == {
        Platform.BILIBILI: CookiePolicy.ANONYMOUS,
        Platform.TVER: CookiePolicy.CONFIGURED,
        Platform.ABEMA: CookiePolicy.CONFIGURED,
        Platform.YOUTUBE: CookiePolicy.CONFIGURED,
    }


def test_bilibili_descriptions_are_not_program_information():
    assert not source_platform(Platform.BILIBILI).describes_program
    assert source_platform(Platform.TVER).describes_program


def test_url_comes_from_the_source_id_rules():
    assert (
        source_platform(Platform.YOUTUBE).url("v=abc")
        == "https://www.youtube.com/watch?v=abc"
    )
    assert (
        source_platform(Platform.ABEMA).url("DGzv6KEKhRHpe3")
        == "https://abema.tv/channels/_/slots/DGzv6KEKhRHpe3"
    )


# --- broadcast dates ---------------------------------------------------------


def test_bilibili_uses_the_pubdate_in_cst():
    # 15:30 UTC = 23:30 CST on May 3 (but 00:30 JST May 4).
    platform = source_platform(Platform.BILIBILI)
    info = video_info(timestamp=1777822200)
    assert platform.resolve_broadcast_date(info, SourceExtras()) == date(2026, 5, 3)


def test_youtube_prefers_the_release_timestamp():
    platform = source_platform(Platform.YOUTUBE)
    # A premiere a day after the upload.
    info = video_info(timestamp=1777816800, release_timestamp=1777903200)
    assert platform.resolve_broadcast_date(info, SourceExtras()) == date(2026, 5, 4)


def test_youtube_falls_back_to_the_upload_date():
    platform = source_platform(Platform.YOUTUBE)
    info = video_info(upload_date="20260503")
    assert platform.resolve_broadcast_date(info, SourceExtras()) == date(2026, 5, 3)


@pytest.mark.parametrize(
    ("label", "expected"),
    [("7月6日(月)放送分", date(2026, 7, 6)), ("2018年放送", None)],
)
def test_tver_combines_the_label_and_the_availability_start(
    label: str, expected: date | None
):
    platform = source_platform(Platform.TVER)
    info = video_info(release_timestamp=1783515600)
    extras = SourceExtras(broadcast_label=label)
    assert platform.resolve_broadcast_date(info, extras) == expected


def test_abema_uses_the_reported_on_air_time():
    platform = source_platform(Platform.ABEMA)
    extras = SourceExtras(on_air_epoch=1777816800)  # 2026-05-03 23:00 JST
    assert platform.resolve_broadcast_date(video_info(), extras) == date(2026, 5, 3)
    assert platform.resolve_broadcast_date(video_info(), SourceExtras()) is None


# --- TVer extras -------------------------------------------------------------


def test_tver_talent_roles_skip_empty_genres():
    talents = tver.parse_talents(
        {
            "talents": [
                {
                    "id": "t001",
                    "name": "小栗　有以",
                    "name_kana": "オグリ　ユイ",
                    "genre1": "アイドル",
                    "genre2": "",
                    "genre3": "俳優",
                    "thumbnail_path": "/images/t001.jpg",
                },
                {"name": "no id"},
                "junk",
            ]
        }
    )
    assert talents == (
        SourceTalent("t001", "小栗　有以", "オグリ　ユイ", ("アイドル", "俳優")),
    )


def test_tver_extras_fetch_talents_and_the_on_air_label():
    http = FakeJsonHttp(
        {
            tver.TALENTS_URL.format(id="epdemo1"): {
                "talents": [{"id": "t1", "name": "千鳥"}]
            },
            tver.SESSION_URL: TVER_SESSION,
            TVER_EPISODE: tver_episode("7月6日(月)放送分"),
        }
    )

    extras = source_platform(Platform.TVER).fetch_extras("epdemo1", http)

    assert extras == SourceExtras(
        talents=(SourceTalent("t1", "千鳥"),), broadcast_label="7月6日(月)放送分"
    )
    session, episode = http.requests[1:]
    assert session.data == b"device_type=pc"
    assert episode.url == f"{TVER_EPISODE}?platform_uid=uid&platform_token=token"


@pytest.mark.parametrize(
    "routes",
    [
        {tver.SESSION_URL: TVER_SESSION, TVER_EPISODE: tver_episode("")},
        {tver.SESSION_URL: {"result": None}},
        {tver.SESSION_URL: SourceHttpError("boom")},
    ],
    ids=["empty-label", "null-session", "network-error"],
)
def test_tver_extras_degrade_to_nothing(routes: dict[str, object]):
    extras = source_platform(Platform.TVER).fetch_extras(
        "epdemo1", FakeJsonHttp(routes)
    )

    assert extras == SourceExtras()


# --- ABEMA extras ------------------------------------------------------------


def test_abema_casts_take_the_role_of_their_heading():
    talents = abema.parse_casts(
        {
            "credit": {
                "casts": ["■MC", "千鳥", " ", "■ゲスト", "渡部健（アンジャッシュ）"]
            }
        },
        "90-979_s1_p359",
    )
    assert talents == (
        SourceTalent("abema:90-979_s1_p359:1", "千鳥", roles=("MC",)),
        SourceTalent(
            "abema:90-979_s1_p359:2", "渡部健（アンジャッシュ）", roles=("ゲスト",)
        ),
    )


def test_abema_episode_extras_read_the_program_api():
    http = FakeJsonHttp(
        {
            abema.USERS_URL: ABEMA_TOKEN,
            abema.PROGRAM_URL.format(id="90-979_s1_p360"): {
                "credit": {"casts": ["■MC", "千鳥"]},
                "broadcastAt": 1777816800,
            },
        }
    )

    extras = source_platform(Platform.ABEMA).fetch_extras("90-979_s1_p360", http)

    assert extras.on_air_epoch == 1777816800
    assert [talent.name for talent in extras.talents] == ["千鳥"]
    assert http.requests[1].headers["Authorization"] == "bearer anon"


@pytest.mark.parametrize(
    ("slot", "expected"),
    [({"startAt": 1783346400}, 1783346400), (None, None)],
)
def test_abema_slot_extras_read_the_slot_start(slot: object, expected: int | None):
    http = FakeJsonHttp(
        {
            abema.USERS_URL: ABEMA_TOKEN,
            abema.SLOT_URL.format(id="DGzv6KEKhRHpe3"): {"slot": slot},
        }
    )

    extras = source_platform(Platform.ABEMA).fetch_extras("DGzv6KEKhRHpe3", http)

    assert extras == SourceExtras(on_air_epoch=expected)


@pytest.mark.parametrize(
    "token_response",
    [SourceHttpError("boom"), {"token": ""}],
    ids=["network-error", "no-token"],
)
def test_abema_extras_degrade_to_nothing(token_response: object):
    http = FakeJsonHttp({abema.USERS_URL: token_response})

    extras = source_platform(Platform.ABEMA).fetch_extras("90-979_s1_p360", http)

    assert extras == SourceExtras()


def test_abema_program_without_a_numeric_air_time():
    http = FakeJsonHttp(
        {
            abema.USERS_URL: ABEMA_TOKEN,
            abema.PROGRAM_URL.format(id="90-979_s1_p360"): {"broadcastAt": None},
        }
    )

    extras = source_platform(Platform.ABEMA).fetch_extras("90-979_s1_p360", http)

    assert extras == SourceExtras()


@pytest.fixture
def stale_abema_tokens() -> Iterator[None]:
    """Seed yt-dlp's class-level ABEMA token cache; restore it afterwards."""
    saved = {name: getattr(AbemaTVBaseIE, name) for name in _ABEMA_CACHE}
    for name in _ABEMA_CACHE:
        setattr(AbemaTVBaseIE, name, "stale")
    yield
    for name, value in saved.items():
        setattr(AbemaTVBaseIE, name, value)


@pytest.mark.usefixtures("stale_abema_tokens")
def test_abema_download_clears_the_extractor_token_cache():
    source_platform(Platform.ABEMA).before_download()

    assert all(getattr(AbemaTVBaseIE, name) is None for name in _ABEMA_CACHE)


@pytest.mark.usefixtures("stale_abema_tokens")
@pytest.mark.parametrize(
    "platform", [Platform.TVER, Platform.YOUTUBE, Platform.BILIBILI]
)
def test_other_platforms_leave_the_token_cache_alone(platform: Platform):
    source_platform(platform).before_download()

    assert AbemaTVBaseIE._USERTOKEN == "stale"
