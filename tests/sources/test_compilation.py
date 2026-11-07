from __future__ import annotations

import json
from datetime import date
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError
from tests.sources.fakes import FakeYtDlp
from yt_dlp.utils import DownloadError

from grillmaster.core.json_artifact import write_model
from grillmaster.core.source_id import Platform, SourceId
from grillmaster.sources.compilation import (
    CompilationError,
    CompilationManifest,
    Playlist,
    PlaylistEntry,
    groups_for_minutes,
    is_manifest_path,
    list_playlist,
    load_manifest,
    plan_groups,
    plan_manifests,
)

if TYPE_CHECKING:
    from pathlib import Path

URL_A = "https://www.youtube.com/watch?v=aaa"
URL_B = "https://www.youtube.com/watch?v=bbb"


def manifest(**fields: Any) -> CompilationManifest:
    return CompilationManifest.model_validate(
        {"id": "m1e01", "title": "M-1 #1", "entries": [URL_A, URL_B], **fields}
    )


def test_a_manifest_parses_its_entries_on_one_platform():
    parsed = manifest(entries=[URL_A, "v=bbb"])
    assert parsed.sources == (
        SourceId(Platform.YOUTUBE, "v=aaa"),
        SourceId(Platform.YOUTUBE, "v=bbb"),
    )
    assert parsed.platform is Platform.YOUTUBE


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"id": "m1-e01"}, "pattern"),
        ({"entries": [URL_A]}, "at least 2"),
        ({"entries": [URL_A, "BV1ZArvBaEqL"]}, "mix platforms"),
        ({"entries": [URL_A, "v=aaa"]}, "repeat v=aaa"),
        ({"entries": [URL_A, "https://example.com/x"]}, "Invalid video source"),
        ({"date": "2026-11-01"}, "Extra inputs"),
    ],
)
def test_a_bad_manifest_fails_loudly(fields: dict[str, Any], message: str):
    with pytest.raises(ValidationError, match=message):
        manifest(**fields)


def test_manifests_round_trip_through_their_file(tmp_path: Path):
    path = tmp_path / "m1e01.json"
    written = manifest(channel="M-1グランプリ", broadcast_date=date(2026, 11, 1))

    write_model(path, written)

    assert "M-1グランプリ" in path.read_text(encoding="utf-8")
    assert load_manifest(path) == written


def test_an_invalid_manifest_file_names_the_file(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"id": "x"}), encoding="utf-8")
    with pytest.raises(CompilationError, match=r"Invalid manifest .*bad\.json"):
        load_manifest(path)
    with pytest.raises(CompilationError, match="Cannot read"):
        load_manifest(tmp_path / "missing.json")


def test_only_json_paths_are_manifests():
    assert is_manifest_path(r"C:\plans\M1E01.JSON")
    assert not is_manifest_path("https://www.youtube.com/watch?v=aaa")


# --- planning ---------------------------------------------------------------


def test_groups_stay_in_order_and_balance_their_lengths():
    durations = [600, 600, 300, 300, 300, 300, 600, 600]

    groups = plan_groups(durations, 2)

    assert groups == [range(4), range(4, 8)]


def test_every_group_holds_at_least_two_videos():
    groups = plan_groups([3000, 10, 10, 10], 2)
    assert groups == [range(2), range(2, 4)]
    with pytest.raises(CompilationError, match="cannot fill 3 groups"):
        plan_groups([1, 1, 1, 1, 1], 3)


def test_minutes_set_the_number_of_groups():
    assert groups_for_minutes([540] * 75, 60) == 11
    assert groups_for_minutes([3600, 3600], 60) == 2
    assert groups_for_minutes([10, 10], 60) == 1


def test_manifests_take_padded_ids_numbered_titles_and_urls():
    entries = tuple(
        PlaylistEntry(SourceId(Platform.YOUTUBE, f"v=id{n}"), f"t{n}", 60.0)
        for n in range(4)
    )
    playlist = Playlist(title="P", entries=entries)

    planned = plan_manifests(
        playlist, [range(2), range(2, 4)], id_prefix="m1r3e", title="M-1"
    )

    assert [m.id for m in planned] == ["m1r3e01", "m1r3e02"]
    assert [m.title for m in planned] == ["M-1 #1", "M-1 #2"]
    assert planned[1].entries == [
        "https://www.youtube.com/watch?v=id2",
        "https://www.youtube.com/watch?v=id3",
    ]


def test_a_bad_id_prefix_fails_the_plan():
    entries = tuple(
        PlaylistEntry(SourceId(Platform.YOUTUBE, f"v=id{n}"), "t", 60.0)
        for n in range(2)
    )
    with pytest.raises(CompilationError, match="Cannot plan"):
        plan_manifests(Playlist(None, entries), [range(2)], id_prefix="m-1", title="M")


def flat_entry(video_id: str, **fields: Any) -> dict[str, Any]:
    return {
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "title": f"title {video_id}",
        "duration": 500.0,
        **fields,
    }


def test_a_playlist_is_listed_in_one_flat_extraction():
    ytdlp = FakeYtDlp(
        info={"title": "List", "entries": [flat_entry("aaa"), flat_entry("bbb")]}
    )

    playlist = list_playlist(ytdlp, "https://p", options={"x": 1}, items="1-2")

    (call,) = ytdlp.calls
    assert call.options == {
        "x": 1,
        "extract_flat": "in_playlist",
        "playlist_items": "1-2",
    }
    assert not call.download
    assert playlist.title == "List"
    assert playlist.entries[1] == PlaylistEntry(
        SourceId(Platform.YOUTUBE, "v=bbb"), "title bbb", 500.0
    )


def test_videos_without_a_length_or_title_fail_the_listing_by_name():
    ytdlp = FakeYtDlp(
        info={
            "entries": [
                flat_entry("aaa"),
                flat_entry("bbb", duration=None),
                flat_entry("ccc", title=""),
            ]
        }
    )
    with pytest.raises(CompilationError, match=r"(?s)#2 .*v=bbb.*#3 .*v=ccc"):
        list_playlist(ytdlp, "https://p", options={})


def test_an_empty_playlist_fails():
    with pytest.raises(CompilationError, match="lists no videos"):
        list_playlist(FakeYtDlp(info={"entries": []}), "https://p", options={})


def test_a_playlist_yt_dlp_cannot_list_fails_as_a_compilation_error():
    ytdlp = FakeYtDlp(fail_with=DownloadError("This playlist does not exist"))
    with pytest.raises(CompilationError, match="Cannot list https://p"):
        list_playlist(ytdlp, "https://p", options={})
