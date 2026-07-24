"""Lazy artifact previews for completed stages.

Previews read project artifacts from disk at render time; every read is
guarded so a missing or unreadable file never breaks the dashboard. Content
is cached after the first successful read (artifacts do not change once their
stage is done).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.text import Text

# Keep large artifacts (pre_pass.json) from flooding the detail panel.
_MAX_PREVIEW_CHARS = 12_000


@dataclass
class ArtifactPreview:
    """Renderable preview plus the path the `o` key should open."""

    renderables: list = field(default_factory=list)
    open_path: Path | None = None
    image_path: Path | None = None  # set for cover.png (textual-image widget)


def _read_text(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if len(text) > _MAX_PREVIEW_CHARS:
        text = text[:_MAX_PREVIEW_CHARS] + "\n… (truncated)"
    return text


def _header(name: str) -> Text:
    return Text.assemble(("artifact  ", "grey58"), (name, ""))


class ArtifactCache:
    """Builds and caches per-stage artifact previews for one project."""

    def __init__(self) -> None:
        self._cache: dict[str, ArtifactPreview] = {}

    def preview(self, project: Any, key: str) -> ArtifactPreview | None:
        if project is None:
            return None
        if key in self._cache:
            return self._cache[key]
        built = self._build(project, key)
        if built is not None:
            self._cache[key] = built
        return built

    def _build(self, project: Any, key: str) -> ArtifactPreview | None:
        try:
            if key == "prepass":
                return self._json_preview(project.pre_pass_path)
            if key == "refine":
                return self._markdown_preview(project.refine_report_path)
            if key == "glossary":
                return self._markdown_preview(
                    project.glossary_check_report_path
                )
            if key == "date":
                return self._json_preview(project.date_research_path)
            if key == "cover":
                return self._cover_preview(project.poster_cover_path)
            if key == "chunks":
                return self._path_list_preview([project.translated_path])
            if key == "finalize":
                return self._path_list_preview(
                    [project.ass_path, project.finalized_srt_path]
                )
        except Exception:
            return None
        return None

    def _json_preview(self, path: Path) -> ArtifactPreview | None:
        text = _read_text(path)
        if text is None:
            return None
        return ArtifactPreview(
            renderables=[
                _header(path.name),
                Text(),
                Syntax(
                    text,
                    "json",
                    line_numbers=True,
                    word_wrap=True,
                    background_color="default",
                ),
            ],
            open_path=path,
        )

    def _markdown_preview(self, path: Path) -> ArtifactPreview | None:
        text = _read_text(path)
        if text is None:
            return None
        return ArtifactPreview(
            renderables=[_header(path.name), Text(), Markdown(text)],
            open_path=path,
        )

    def _cover_preview(self, path: Path) -> ArtifactPreview | None:
        if not path.exists():
            return None
        return ArtifactPreview(
            renderables=[_header(path.name)],
            open_path=path,
            image_path=path,
        )

    def _path_list_preview(
        self, paths: list[Path]
    ) -> ArtifactPreview | None:
        existing = [path for path in paths if path.exists()]
        if not existing:
            return None
        lines = [Text(str(path), style="green") for path in existing]
        return ArtifactPreview(
            renderables=[_header("outputs"), Text(), *lines],
            open_path=existing[0],
        )
