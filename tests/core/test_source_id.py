from __future__ import annotations

import pytest

from grillmaster.core.source_id import (
    Platform,
    SourceId,
    parse_source,
    platform_of,
    source_url,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "v=dQw4w9WgXcQ"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s&list=ABC", "v=dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "v=dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/abc123XYZ_-", "v=abc123XYZ_-"),
        ("https://www.youtube.com/live/abc123XYZ_-", "v=abc123XYZ_-"),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "v=dQw4w9WgXcQ"),
        ("v=dQw4w9WgXcQ", "v=dQw4w9WgXcQ"),
        # A `BV` substring must not be mistaken for a Bilibili ID.
        ("https://youtu.be/aBVx9k2LmQw", "v=aBVx9k2LmQw"),
        ("v=aBVx9k2LmQw", "v=aBVx9k2LmQw"),
    ],
)
def test_parse_youtube(text: str, expected: str):
    assert parse_source(text) == SourceId(Platform.YOUTUBE, expected)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "https://www.bilibili.com/video/BV1ZArvBaEqL/?spm=x",
            SourceId(Platform.BILIBILI, "BV1ZArvBaEqL"),
        ),
        ("BV1ZArvBaEqL", SourceId(Platform.BILIBILI, "BV1ZArvBaEqL")),
        ("https://tver.jp/episodes/epknhe0jz5", SourceId(Platform.TVER, "epknhe0jz5")),
        ("epknhe0jz5", SourceId(Platform.TVER, "epknhe0jz5")),
        (
            "https://abema.tv/video/episode/90-979_s1_p360",
            SourceId(Platform.ABEMA, "90-979_s1_p360"),
        ),
        (
            "https://abema.tv/channels/special-plus/slots/DGzv6KEKhRHpe3",
            SourceId(Platform.ABEMA, "DGzv6KEKhRHpe3"),
        ),
        # The URL decides the platform even when the ID looks like TVer's.
        (
            "https://abema.tv/channels/_/slots/shXk29aPq3",
            SourceId(Platform.ABEMA, "shXk29aPq3"),
        ),
        ("90-979_s1_p360", SourceId(Platform.ABEMA, "90-979_s1_p360")),
    ],
)
def test_parse_other_platforms(text: str, expected: SourceId):
    assert parse_source(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "https://example.com/video/1",
        "https://tver.jp/",
        "https://www.youtube.com/",
        "https://www.bilibili.com/",
    ],
)
def test_parse_rejects_unsupported_urls(text: str):
    with pytest.raises(ValueError, match="Invalid"):
        parse_source(text)


@pytest.mark.parametrize(
    ("video_id", "platform"),
    [
        ("BV1ZArvBaEqL", Platform.BILIBILI),
        ("v=dQw4w9WgXcQ", Platform.YOUTUBE),
        ("epknhe0jz5", Platform.TVER),
        ("sh0123abc", Platform.TVER),
        ("ep-not-tver", Platform.ABEMA),
        ("90-979_s1_p360", Platform.ABEMA),
        ("DGzv6KEKhRHpe3", Platform.ABEMA),
    ],
)
def test_platform_of(video_id: str, platform: Platform):
    assert platform_of(video_id) is platform


@pytest.mark.parametrize(
    ("platform", "video_id", "url"),
    [
        (
            Platform.BILIBILI,
            "BV1ZArvBaEqL",
            "https://www.bilibili.com/video/BV1ZArvBaEqL",
        ),
        (Platform.TVER, "epknhe0jz5", "https://tver.jp/episodes/epknhe0jz5"),
        (
            Platform.ABEMA,
            "DGzv6KEKhRHpe3",
            "https://abema.tv/channels/_/slots/DGzv6KEKhRHpe3",
        ),
        (
            Platform.ABEMA,
            "90-979_s1_p360",
            "https://abema.tv/video/episode/90-979_s1_p360",
        ),
        (
            Platform.YOUTUBE,
            "v=dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        ),
    ],
)
def test_source_url(platform: Platform, video_id: str, url: str):
    assert source_url(platform, video_id) == url
    assert SourceId(platform, video_id).url == url


def test_source_id_str_is_the_video_id():
    assert str(SourceId(Platform.YOUTUBE, "v=abc")) == "v=abc"
