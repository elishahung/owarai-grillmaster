from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

from grillmaster.core.tool_session import (
    FramesTool,
    SrtCheckTool,
    ToolName,
    ToolSession,
)

if TYPE_CHECKING:
    from pathlib import Path


def _frames(root: Path, **overrides: Any) -> FramesTool:
    fields: dict[str, Any] = {
        "video": root / "video.mp4",
        "frames_dir": root / "work" / "10_refine" / "frames",
        "window": (0.0, None),
        "max_side": 768,
    }
    fields.update(overrides)
    return FramesTool(**fields)


def _session(root: Path, *, frames: bool = True, check_srt: bool = True) -> ToolSession:
    return ToolSession(
        project_root=root,
        frames=_frames(root) if frames else None,
        check_srt=(
            SrtCheckTool(reference_srt=root / "work" / "09_chunks" / "merged.srt")
            if check_srt
            else None
        ),
    )


def test_write_and_load_round_trip(tmp_path: Path):
    session = _session(tmp_path)
    path = tmp_path / "session" / "tools.json"
    session.write(path)
    assert ToolSession.load(path) == session


def test_manifest_matches_the_documented_shape(tmp_path: Path):
    path = tmp_path / "tools.json"
    ToolSession(
        project_root=tmp_path,
        frames=_frames(tmp_path, window=(12.5, 99.0)),
        check_srt=None,
    ).write(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data) == {"project_root", "frames", "check_srt"}
    assert set(data["frames"]) == {
        "video",
        "frames_dir",
        "window",
        "max_side",
        "pending_frames",
    }
    assert data["frames"]["window"] == [12.5, 99.0]
    assert data["check_srt"] is None


@pytest.mark.parametrize(
    ("frames", "check_srt", "allowed"),
    [
        (True, True, {ToolName.GET_FRAMES, ToolName.CHECK_SRT}),
        (True, False, {ToolName.GET_FRAMES}),
        (False, True, {ToolName.CHECK_SRT}),
        (False, False, set()),
    ],
)
def test_allowed_follows_the_present_sub_configs(
    tmp_path: Path, frames: bool, check_srt: bool, allowed: set[ToolName]
):
    session = _session(tmp_path, frames=frames, check_srt=check_srt)
    assert session.allowed == allowed


_NO_TOOLS: dict[str, Any] = {"project_root": ".", "frames": None, "check_srt": None}


@pytest.mark.parametrize(
    ("content", "error"),
    [
        (None, FileNotFoundError),
        ({"project_root": "."}, ValidationError),
        ({**_NO_TOOLS, "extra": 1}, ValidationError),
        ({**_NO_TOOLS, "lookup_glossary": {}}, ValidationError),
        ("not json", ValidationError),
    ],
    ids=["missing", "incomplete", "extra-key", "unknown-tool", "not-json"],
)
def test_load_rejects_unusable_manifests(
    tmp_path: Path, content: dict[str, Any] | str | None, error: type[Exception]
):
    path = tmp_path / "tools.json"
    if content is not None:
        text = content if isinstance(content, str) else json.dumps(content)
        path.write_text(text, encoding="utf-8")
    with pytest.raises(error):
        ToolSession.load(path)


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"window": (10.0, 5.0)}, "window"),
        ({"window": (-1.0, None)}, "window"),
        ({"max_side": 0}, "max_side"),
    ],
)
def test_invalid_frames_config_is_rejected(
    tmp_path: Path, overrides: dict[str, Any], match: str
):
    with pytest.raises(ValidationError, match=match):
        _frames(tmp_path, **overrides)
