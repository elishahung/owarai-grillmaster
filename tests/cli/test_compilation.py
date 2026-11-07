from __future__ import annotations

import io
import json
import sys
from typing import TYPE_CHECKING

import pytest

from grillmaster.cli import app, redirected_streams_as_utf8
from grillmaster.core.source_id import Platform
from grillmaster.project.layout import ProjectLayout
from grillmaster.project.store import load_state

if TYPE_CHECKING:
    from pathlib import Path

    from typer.testing import CliRunner

MANIFEST = {
    "id": "m1r3e01",
    "title": "M-1 #1",
    "entries": [
        "https://www.youtube.com/watch?v=aaa",
        "https://www.youtube.com/watch?v=bbb",
    ],
}


@pytest.fixture
def manifest(home: Path) -> Path:
    path = home / "plans" / "m1r3e01.json"
    path.parent.mkdir()
    path.write_text(json.dumps(MANIFEST), encoding="utf-8")
    return path


def test_a_manifest_runs_as_a_compilation_project(
    cli: CliRunner, projects_root: Path, manifest: Path
):
    result = cli.invoke(app, [str(manifest)])

    assert result.exit_code == 0, result.output
    state = load_state(ProjectLayout.for_id(projects_root, "m1r3e01"))
    assert state.platform is Platform.YOUTUBE
    assert state.compilation is not None
    assert state.compilation.entries == ["v=aaa", "v=bbb"]


@pytest.mark.parametrize(
    "flags",
    [
        pytest.param(["--parts", "2"], id="parts"),
        pytest.param(["--chat"], id="chat"),
        pytest.param(["--start", "1:00"], id="section"),
    ],
)
def test_a_manifest_refuses_what_a_compilation_cannot_do(
    cli: CliRunner, projects_root: Path, manifest: Path, flags: list[str]
):
    result = cli.invoke(app, [str(manifest), *flags])

    assert result.exit_code == 1
    assert "compilation" in result.output
    assert not ProjectLayout.for_id(projects_root, "m1r3e01").project_json.exists()


def test_an_invalid_manifest_fails_before_any_project(
    cli: CliRunner, projects_root: Path, home: Path
):
    path = home / "bad.json"
    path.write_text(json.dumps({**MANIFEST, "entries": ["v=aaa"]}), encoding="utf-8")

    result = cli.invoke(app, [str(path)])

    assert result.exit_code == 1
    assert "Invalid manifest" in result.output
    assert not projects_root.exists()


def test_redirected_output_is_written_as_utf8(monkeypatch: pytest.MonkeyPatch):
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp950")
    monkeypatch.setattr(sys, "stdout", stream)

    redirected_streams_as_utf8()
    sys.stdout.write("3回戦ネタ")
    stream.flush()

    assert raw.getvalue().decode("utf-8") == "3回戦ネタ"
