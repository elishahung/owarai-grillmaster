"""One-off migration of pre-2026-10 project directories to the new layout.

    uv run python scripts/migrate_archive.py <root>            # dry run
    uv run python scripts/migrate_archive.py <root> --apply    # migrate

Finds every old-format project under `<root>` (a `project.json` carrying the
legacy `is_*` progress flags) and converts it in place, following the table in
`docs/design/refactor-2026-10.md` section 10.5: the flags become the stage
ledger and side tasks, deliverables move to `subs/` and the root, stage
intermediates move to `work/NN_<stage>/`, the pre-pass briefing is rewritten
with `TermMapping` lists, chunk responses become `translation.json`, and dead
caches are discarded.

Safety rules:
- dry run is the default and writes nothing;
- a file this script does not recognise is left in place and reported;
- a project with any problem (invalid data, destination clash, path over
  MAX_PATH) is skipped as a whole, and an unexpected error in one project is
  reported and counted without stopping the batch;
- converted files are written and read back (the NAS can truncate writes
  silently) before their sources are deleted, and `project.json` is replaced
  last (after validating as `ProjectState`; the original is kept, verified, as
  `logs/legacy-project.json`), so an interrupted run is simply re-run;
  already-migrated projects are skipped.

Only the new `grillmaster.core` / `grillmaster.project` models are used; the
legacy package is never imported. Delete this script once every archive is
migrated.
"""

from __future__ import annotations

import argparse
import io
import itertools
import json
import os
import re
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from grillmaster.core.briefing import Briefing
from grillmaster.core.fs import atomic_write_text
from grillmaster.core.paths import MAX_PATH_UNITS, measure
from grillmaster.core.source_id import Platform, platform_of
from grillmaster.core.srt import SrtBlock, parse_srt, read_srt_file
from grillmaster.core.stage_key import StageKey
from grillmaster.project.layout import ProjectLayout, chunk_range_name
from grillmaster.project.naming import PROJECT_INNER_PATH_RESERVE
from grillmaster.project.state import (
    DateResearchRecord,
    ProjectState,
    Section,
    SideTasks,
    SourceInfo,
    StageRecord,
    TaskRecord,
)
from grillmaster.project.store import save_state

PROJECT_FILE = "project.json"
# The original project.json, kept for reference once the new one is written.
LEGACY_STATE_COPY = "logs/legacy-project.json"
# `atomic_write_text` writes `.<name>.<8 random chars>.tmp` (mkstemp) next to
# the destination first; that temporary name must fit MAX_PATH too.
ATOMIC_TEMP_OVERHEAD = len("." + "." + "x" * 8 + ".tmp")

# --- legacy state ------------------------------------------------------------

FLAG_STAGES: dict[str, StageKey] = {
    "is_metadata_fetched": StageKey.METADATA,
    "is_downloaded": StageKey.DOWNLOAD,
    "is_video_processed": StageKey.COMBINE,
    "is_chat_fetched": StageKey.CHAT_FETCH,
    "is_audio_processed": StageKey.AUDIO,
    "is_asr_completed": StageKey.ASR,
    "is_srt_completed": StageKey.TRANSCRIPT,
    "is_prepass_completed": StageKey.PREPASS,
    "is_chunk_translated": StageKey.CHUNKS,
    "is_srt_refined": StageKey.REFINE,
    "is_glossary_checked": StageKey.GLOSSARY,
    "is_finalized": StageKey.FINALIZE,
    "is_chat_translated": StageKey.CHAT_TRANSLATE,
    # Pre-chunking era: one-shot translation, then a plain ASS conversion.
    "is_translated": StageKey.CHUNKS,
    "is_ass_converted": StageKey.FINALIZE,
}
SIDE_FLAGS = ("is_cover_generated", "is_broadcast_date_researched")
# Fields the new state has no place for: spend on retired services, the old
# asynchronous ASR task, and a pre-hint `description` the legacy model already
# ignored.
DROPPED_KEYS = frozenset(
    {
        "total_cost",
        "service_costs",
        "asr_task_id",
        "is_asr_task_submitted",
        "description",
    }
)
MAPPED_KEYS = frozenset(
    {
        "id",
        "created_at",
        "name",
        "translation_hint",
        "parent_project_path",
        "broadcast_date",
        "source_metadata",
        "asr_cost",
        "section_start",
        "section_end",
        *FLAG_STAGES,
        *SIDE_FLAGS,
    }
)

# --- file classification -----------------------------------------------------

KEEP = frozenset({PROJECT_FILE, "video.mp4", "poster.jpg"})
# Top-level names that only exist in the new layout (a partial earlier run).
NEW_LAYOUT_TOPS = frozenset({"work", "subs", "logs", "cover.png"})
LEGACY_DIRS = frozenset(
    {
        ".asr",
        ".pre_pass",
        "pre_pass",
        ".chunks",
        "chunks",
        ".refine",
        ".glossary_check",
        ".live_chat",
        ".artifacts",
        ".titles",
    }
)

_BATCH_RE = re.compile(r"^\.live_chat/batches/batch_(\d+)\.json$")
_CHUNK_RE = re.compile(
    r"^\.?chunks/responses/chunk_(\d+)-(\d+)(?:_[0-9a-f]+)*\.(raw|fixed)\.srt$"
)
# yt-dlp's output template was `%(playlist_index|0)s.%(ext)s` at the project
# root, its captions `<part>.<lang>.srt` with `subtitleslangs` ja / ja-*.
_DOWNLOAD_PART_RE = re.compile(r"^\d+\.(?:mp4|ja(?:-[A-Za-z0-9]+)*\.srt)$")
# The legacy frame tool's caches under `.pre_pass/media` and `.chunks/media`
# go with those directories.
_DISCARD_DIR_RES = (
    (re.compile(r"^\.?chunks/(media|manifests)$"), "chunk media/manifest cache"),
    (re.compile(r"^\.?chunks/responses/chunk_[^/]+_fix$"), "structural-fix workspace"),
    (re.compile(r"^\.?pre_pass/media$"), "pre-pass media cache"),
    (re.compile(r"^(\.refine/|\.glossary_check/)?extra_frames$"), "frame-tool cache"),
)
_DISCARD_FILE_RES = (
    (re.compile(r"^\.?pre_pass/(assets|manifest)\.json$"), "pre-pass manifest"),
)
# Explorer drops these into folders it displays (lower-cased names).
OS_JUNK = frozenset({"thumbs.db", "desktop.ini"})
_BRIEFING_RAW = (".pre_pass/pre_pass.raw.json", "pre_pass/pre_pass.raw.json")
_BRIEFING_CURRENT = (
    ".pre_pass/pre_pass.json",
    "pre_pass/pre_pass.json",
    "pre_pass.json",
)


def move_destinations(layout: ProjectLayout) -> dict[str, Path]:
    """Legacy relative path -> new location, for plain renames."""
    return {
        "video.ja.srt": layout.ja_srt,
        "video.official.ja.srt": layout.ja_official_srt,
        "video.cht.finalized.srt": layout.cht_srt,
        "video.cht.ass": layout.cht_ass,
        "chat.cht.json": layout.chat_cht_json,
        "poster.cover.png": layout.cover,
        "metadata.info.json": layout.metadata_info,
        "video.full.mp4": layout.combined_full_video,
        # `.opus` is the same Ogg Opus stream under its older extension.
        ".asr/audio.ogg": layout.audio,
        ".asr/audio.opus": layout.audio,
        "audio.opus": layout.audio,
        ".asr/asr.json": layout.asr_json,
        "asr.json": layout.asr_json,
        "video.cht.srt": layout.merged_srt,
        # The one-shot translation of the pre-chunking era.
        "video.zh-hant.srt": layout.merged_srt,
        "video.cht.refined.srt": layout.refined_srt,
        ".refine/report.md": layout.refine_report,
        "video.cht.glossary_checked.srt": layout.glossary_checked_srt,
        ".glossary_check/report.md": layout.glossary_report,
        ".live_chat/live_chat.json": layout.chat_raw,
        ".live_chat/messages.json": layout.chat_messages,
        ".live_chat/polish.json": layout.chat_polish,
        "video.chat.ass": layout.chat_panel_ass,
        ".artifacts/date_research.json": layout.date_research_result,
        ".titles/titles.json": layout.titles,
    }


def _move_destination(
    layout: ProjectLayout, renames: dict[str, Path], rel: str
) -> Path | None:
    if (dest := renames.get(rel)) is not None:
        return dest
    if match := _BATCH_RE.match(rel):
        return layout.chat_batch(int(match.group(1)))
    if _DOWNLOAD_PART_RE.match(rel):
        return layout.download_parts_dir / rel
    return None


def _discard_dir_category(rel: str) -> str | None:
    for pattern, category in _DISCARD_DIR_RES:
        if pattern.match(rel):
            return category
    return None


def _discard_file_category(rel: str) -> str | None:
    top, _, rest = rel.partition("/")
    # Only at the root and inside the legacy trees, which are removed once
    # empty; elsewhere the folder is not ours and is reported instead.
    if rel.rsplit("/", 1)[-1].lower() in OS_JUNK and (not rest or top in LEGACY_DIRS):
        return "OS junk"
    for pattern, category in _DISCARD_FILE_RES:
        if pattern.match(rel):
            return category
    return None


# --- plan --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Move:
    source: Path
    dest: Path


@dataclass(frozen=True, slots=True)
class Write:
    """A converted file; `sources` are deleted once every write reads back."""

    dest: Path
    text: str
    sources: tuple[Path, ...]
    category: str


@dataclass(frozen=True, slots=True)
class Discard:
    """A file, or a directory tree listed once at planning time.

    `files` and `dirs` (deepest first) are the tree's contents; removal works
    from that listing instead of walking the tree again.
    """

    path: Path
    category: str
    files: tuple[Path, ...] = ()
    dirs: tuple[Path, ...] = ()

    @property
    def file_count(self) -> int:
        return len(self.files) if self.dirs else 1

    @classmethod
    def tree(cls, path: Path, category: str) -> Discard:
        files: list[Path] = []
        dirs: list[Path] = []
        for dirpath, _, filenames in os.walk(path, topdown=False):
            files.extend(Path(dirpath) / name for name in filenames)
            dirs.append(Path(dirpath))
        return cls(path, category, tuple(files), tuple(dirs))

    def remove(self) -> None:
        if not self.dirs:
            self.path.unlink(missing_ok=True)
            return
        for path in self.files:
            path.unlink(missing_ok=True)
        for directory in self.dirs:
            if directory.exists():
                directory.rmdir()


@dataclass(frozen=True, slots=True)
class ChunkCandidate:
    path: Path
    from_index: int
    to_index: int
    mtime: float


Status = Literal["migrate", "migrated", "invalid"]


@dataclass
class ProjectPlan:
    root: Path
    status: Status = "migrate"
    state: ProjectState | None = None
    moves: list[Move] = field(default_factory=list)
    writes: list[Write] = field(default_factory=list)
    discards: list[Discard] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    # (kind, detail): kinds aggregate in the final tally.
    warnings: list[tuple[str, str]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    long_paths: list[tuple[Path, int]] = field(default_factory=list)
    layout: ProjectLayout = field(init=False)

    def __post_init__(self) -> None:
        self.layout = ProjectLayout(self.root)

    @property
    def blocked(self) -> bool:
        return bool(self.problems)


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _mtime(path: Path) -> float:
    return path.stat().st_mtime


def plan_project(root: Path) -> ProjectPlan:
    plan = ProjectPlan(root)
    raw_text = (root / PROJECT_FILE).read_text(encoding="utf-8-sig")
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as error:
        plan.status = "invalid"
        plan.problems.append(f"project.json is not JSON: {error}")
        return plan
    if not isinstance(data, dict) or not any(key.startswith("is_") for key in data):
        try:
            ProjectState.model_validate_json(raw_text)
        except ValidationError as error:
            plan.status = "invalid"
            plan.problems.append(
                f"project.json is neither legacy nor valid ProjectState: {error}"
            )
        else:
            plan.status = "migrated"
        return plan

    sources = _plan_files(plan)
    _plan_briefing(plan, sources)
    _plan_chunks(plan, sources)
    _check_destinations(plan)
    _build_state(plan, data, sources)
    return plan


@dataclass
class _Sources:
    """What the file walk found, for the conversions and the state."""

    # new-layout path -> file currently holding that content
    holders: dict[Path, Path] = field(default_factory=dict)
    briefing_raw: list[Path] = field(default_factory=list)
    briefing_current: list[Path] = field(default_factory=list)
    chunks: list[ChunkCandidate] = field(default_factory=list)


def _plan_files(plan: ProjectPlan) -> _Sources:
    root, layout = plan.root, plan.layout
    renames = move_destinations(layout)
    found = _Sources()
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        rel_dir = "" if current == root else _rel(root, current)
        if rel_dir.split("/")[0] in NEW_LAYOUT_TOPS:
            for name in filenames:
                found.holders[current / name] = current / name
            continue
        for name in list(dirnames):
            rel = f"{rel_dir}/{name}".lstrip("/")
            if (category := _discard_dir_category(rel)) is not None:
                dirnames.remove(name)
                plan.discards.append(Discard.tree(current / name, category))
        for name in filenames:
            path = current / name
            rel = f"{rel_dir}/{name}".lstrip("/")
            _classify_file(plan, found, renames, rel, path)
    return found


def _classify_file(
    plan: ProjectPlan, found: _Sources, renames: dict[str, Path], rel: str, path: Path
) -> None:
    if rel in KEEP or rel in NEW_LAYOUT_TOPS:
        found.holders[path] = path
    elif (dest := _move_destination(plan.layout, renames, rel)) is not None:
        plan.moves.append(Move(path, dest))
        found.holders[dest] = path
    elif rel in _BRIEFING_RAW:
        found.briefing_raw.append(path)
    elif rel in _BRIEFING_CURRENT:
        found.briefing_current.append(path)
    elif match := _CHUNK_RE.match(rel):
        found.chunks.append(
            ChunkCandidate(
                path,
                int(match.group(1)),
                int(match.group(2)),
                _mtime(path),
            )
        )
    elif (category := _discard_file_category(rel)) is not None:
        plan.discards.append(Discard(path, category))
    else:
        plan.unknown.append(rel)


def legacy_briefing(path: Path) -> Briefing:
    """Parse a legacy `pre_pass.json`, folding term objects into lists."""
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, dict):
        for key in ("proper_nouns", "glossary"):
            terms = data.get(key)
            if isinstance(terms, dict):
                data[key] = [
                    {"source": source, "target": target}
                    for source, target in terms.items()
                ]
    return Briefing.model_validate(data)


def _briefing_text(briefing: Briefing) -> str:
    return briefing.model_dump_json(indent=2) + "\n"


def _single(plan: ProjectPlan, paths: list[Path], label: str) -> Path | None:
    if len(paths) > 1:
        names = ", ".join(_rel(plan.root, path) for path in paths)
        plan.problems.append(f"several {label} briefings: {names}")
        return None
    return paths[0] if paths else None


def _plan_briefing(plan: ProjectPlan, found: _Sources) -> None:
    """raw -> pre-pass briefing, current -> glossary briefing when it differs.

    Without a raw backup the current file is the pre-pass output. A pre-pass
    briefing already written by an interrupted run stands in for a raw file
    that run deleted.
    """
    layout = plan.layout
    raw_path = _single(plan, found.briefing_raw, "raw")
    current_path = _single(plan, found.briefing_current, "current")
    if raw_path is None and current_path is None:
        return
    try:
        raw = legacy_briefing(raw_path) if raw_path else None
        current = legacy_briefing(current_path) if current_path else None
        if raw is None and layout.prepass_briefing.exists() and current is not None:
            raw = Briefing.model_validate_json(
                layout.prepass_briefing.read_text(encoding="utf-8")
            )
    except (ValueError, OSError) as error:
        plan.problems.append(f"briefing does not convert: {error}")
        return

    if raw_path is not None and raw is not None:
        extra = (current_path,) if current_path and current == raw else ()
        plan.writes.append(
            Write(
                layout.prepass_briefing,
                _briefing_text(raw),
                (raw_path, *extra),
                "briefing",
            )
        )
        found.holders[layout.prepass_briefing] = raw_path
    if current_path is None or current is None:
        return
    if raw is None:
        plan.writes.append(
            Write(
                layout.prepass_briefing,
                _briefing_text(current),
                (current_path,),
                "briefing",
            )
        )
        found.holders[layout.prepass_briefing] = current_path
    elif current != raw:
        plan.writes.append(
            Write(
                layout.glossary_briefing,
                _briefing_text(current),
                (current_path,),
                "briefing",
            )
        )
        found.holders[layout.glossary_briefing] = current_path
    elif raw_path is None:
        # Identical to the pre-pass briefing an earlier run already wrote.
        plan.discards.append(Discard(current_path, "migrated briefing"))


def _parse_response(path: Path) -> list[SrtBlock]:
    """Parse a chunk response like the legacy reader did: a stray one-line
    paragraph (agents sometimes echo a line before the first block) is
    skipped, since it cannot hold an index and a timecode."""
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    paragraphs = [
        list(group)
        for is_text, group in itertools.groupby(
            lines, key=lambda line: bool(line.strip())
        )
        if is_text
    ]
    return parse_srt("\n\n".join("\n".join(p) for p in paragraphs if len(p) > 1))


_SHOWN_ERRORS = 5


def _count_errors(label: str, values: list[object]) -> str | None:
    if not values:
        return None
    more = "..." if len(values) > _SHOWN_ERRORS else ""
    return f"{label} {len(values)}: {values[:_SHOWN_ERRORS]}{more}"


def chunk_translation_text(
    candidate: ChunkCandidate, source: list[SrtBlock] | None
) -> str:
    """The chunk as `{"blocks": [{"index", "text"}]}`.

    Like the legacy validator, output blocks are matched to the Japanese
    source blocks of the range by timecode (agents sometimes renumber), and
    every source block needs exactly one translation. Empty texts pass: older
    caches hold them, and whether the new validator rejects them is its call.
    Without a readable source SRT the response's own indexes must cover the
    range. Raises `ValueError` describing the mismatch otherwise.
    """
    blocks = _parse_response(candidate.path)
    first, last = candidate.from_index, candidate.to_index
    # (match key, source index) per expected block; the key pairs output
    # blocks with source blocks.
    expected: list[tuple[object, int]]
    if source is None:
        expected = [(index, index) for index in range(first, last + 1)]
        produced: list[object] = [block.index for block in blocks]
    else:
        expected = [(b.timecode, b.index) for b in _in_range(source, first, last)]
        produced = [block.timecode for block in blocks]
    by_key = dict(zip(produced, blocks, strict=True))
    keys = {key for key, _ in expected}
    errors = [
        _count_errors("missing", [k for k, _ in expected if k not in by_key]),
        _count_errors("unexpected", [k for k in produced if k not in keys]),
        _count_errors("duplicated", [k for k, n in Counter(produced).items() if n > 1]),
    ]
    if not expected or any(errors):
        detail = ", ".join(error for error in errors if error) or "no source blocks"
        raise ValueError(f"does not match source {first}-{last}: {detail}")
    payload = {
        "blocks": [
            {"index": index, "text": by_key[key].text} for key, index in expected
        ]
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _in_range(source: list[SrtBlock], first: int, last: int) -> list[SrtBlock]:
    return [block for block in source if first <= block.index <= last]


def _is_complete_translation(
    path: Path, first: int, last: int, source: list[SrtBlock] | None
) -> bool:
    """Whether `path` already holds the range's full translation.

    Sources are deleted only after every write was read back, so a complete
    file with sources still present was written by an interrupted run; a
    truncated write is not valid JSON and is converted again instead.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        indexes = [block["index"] for block in data["blocks"]]
    except (ValueError, OSError, KeyError, TypeError):
        return False
    if source is None:
        return indexes == list(range(first, last + 1))
    return indexes == [block.index for block in _in_range(source, first, last)]


def _source_blocks(plan: ProjectPlan, found: _Sources) -> list[SrtBlock] | None:
    holder = found.holders.get(plan.layout.ja_srt)
    if holder is None:
        return None
    try:
        return read_srt_file(holder)
    except (ValueError, OSError) as error:
        plan.warnings.append(("unreadable ja.srt", f"chunks checked by index: {error}"))
        return None


def _plan_chunks(plan: ProjectPlan, found: _Sources) -> None:
    """One `translation.json` per range, from the newest response (by mtime,
    raw or fixed alike: a newer round's raw beats an older round's fix) that
    matches the source; the range's other responses are superseded and
    discarded with it."""
    layout = plan.layout
    if not found.chunks:
        return
    source = _source_blocks(plan, found)
    ranges: dict[tuple[int, int], list[ChunkCandidate]] = {}
    for candidate in found.chunks:
        key = (candidate.from_index, candidate.to_index)
        ranges.setdefault(key, []).append(candidate)
    for (from_index, to_index), candidates in sorted(ranges.items()):
        name = f"chunk {from_index:04d}-{to_index:04d}"
        try:
            chunk_range_name(from_index, to_index)
        except ValueError:
            plan.warnings.append(("unconvertible chunk", f"{name}: invalid range"))
            plan.unknown.extend(_rel(plan.root, c.path) for c in candidates)
            continue
        dest = layout.chunk_translation(from_index, to_index)
        if _is_complete_translation(dest, from_index, to_index, source):
            plan.discards.extend(
                Discard(c.path, "migrated chunk response") for c in candidates
            )
            continue
        ordered = sorted(candidates, key=lambda c: c.mtime, reverse=True)
        errors: list[str] = []
        for candidate in ordered:
            try:
                text = chunk_translation_text(candidate, source)
            except (ValueError, OSError) as error:
                errors.append(f"{candidate.path.name}: {error}")
                continue
            # The chosen response first: its mtime is copied onto the output.
            others = tuple(c.path for c in ordered if c is not candidate)
            plan.writes.append(Write(dest, text, (candidate.path, *others), "chunk"))
            found.holders[dest] = candidate.path
            break
        else:
            plan.warnings.append(
                ("unconvertible chunk", f"{name} left in place: {'; '.join(errors)}")
            )


def _check_path_length(plan: ProjectPlan, path: Path, *, atomic: bool) -> None:
    """Block `path` when it, or the temporary file written beside it by an
    atomic write, would exceed MAX_PATH."""
    units = measure(str(path)) + (ATOMIC_TEMP_OVERHEAD if atomic else 0)
    if units > MAX_PATH_UNITS:
        plan.long_paths.append((path, units))
        kind = " (with its atomic-write temp name)" if atomic else ""
        plan.problems.append(
            f"{_rel(plan.root, path)}{kind} exceeds MAX_PATH ({units} units)"
        )


def _check_destinations(plan: ProjectPlan) -> None:
    claimed: dict[Path, Path] = {}
    for dest, source, atomic in [
        *((move.dest, move.source, False) for move in plan.moves),
        *((write.dest, write.sources[0], True) for write in plan.writes),
    ]:
        if dest in claimed:
            plan.problems.append(
                f"{_rel(plan.root, claimed[dest])} and {_rel(plan.root, source)}"
                f" both map to {_rel(plan.root, dest)}"
            )
        claimed[dest] = source
        _check_path_length(plan, dest, atomic=atomic)
    for move in plan.moves:
        if move.dest.exists():
            plan.problems.append(
                f"{_rel(plan.root, move.source)}: destination"
                f" {_rel(plan.root, move.dest)} already exists"
            )
    _check_path_length(plan, plan.root / LEGACY_STATE_COPY, atomic=False)
    _check_path_length(plan, plan.layout.project_json, atomic=True)
    root_units = measure(str(plan.root))
    if root_units + PROJECT_INNER_PATH_RESERVE > MAX_PATH_UNITS:
        detail = (
            f"{root_units} units leave less than {PROJECT_INNER_PATH_RESERVE}"
            " for the project's own paths"
        )
        plan.warnings.append(("project root too deep for future resumes", detail))


# --- state -------------------------------------------------------------------


def _stamp(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp).astimezone()


def _completed_at(
    found: _Sources, candidates: tuple[Path, ...], fallback: float
) -> datetime:
    """Best effort: the mtime of the stage's output, else of project.json."""
    for candidate in candidates:
        holder = found.holders.get(candidate)
        if holder is not None and holder.exists():
            return _stamp(_mtime(holder))
    return _stamp(fallback)


def _stage_outputs(layout: ProjectLayout) -> dict[StageKey, tuple[Path, ...]]:
    return {
        StageKey.METADATA: (layout.metadata_info,),
        StageKey.DOWNLOAD: (layout.video,),
        StageKey.COMBINE: (layout.video,),
        StageKey.CHAT_FETCH: (layout.chat_messages,),
        StageKey.AUDIO: (layout.audio,),
        StageKey.ASR: (layout.asr_json,),
        StageKey.TRANSCRIPT: (layout.ja_srt,),
        StageKey.PREPASS: (layout.prepass_briefing,),
        StageKey.CHUNKS: (layout.merged_srt,),
        StageKey.REFINE: (layout.refined_srt,),
        StageKey.GLOSSARY: (layout.glossary_checked_srt,),
        StageKey.FINALIZE: (layout.cht_srt, layout.cht_ass),
        StageKey.CHAT_TRANSLATE: (layout.chat_cht_json,),
    }


def _read_json(path: Path | None) -> Any:
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError):
        return None


def _source_info(
    plan: ProjectPlan, data: dict[str, Any], found: _Sources, platform: Platform
) -> tuple[SourceInfo, str | None]:
    """Source metadata, plus the hint with the legacy auto-filled text removed.

    Projects made before the hint/title split stored the source title (and
    description) as the hint; the legacy loader recovered both from yt-dlp's
    info JSON on every load, and so does the migration.
    """
    meta = dict(data.get("source_metadata") or {})
    hint = data.get("translation_hint")
    if meta.get("title") is None and hint is not None:
        info = _read_json(found.holders.get(plan.layout.metadata_info))
        title = info.get("title") if isinstance(info, dict) else None
        if isinstance(title, str) and title:
            description = info.get("description") if isinstance(info, dict) else None
            if not isinstance(description, str):
                description = None
            meta["title"] = title
            if platform is not Platform.BILIBILI:
                meta["description"] = description or None
            if hint in {title, f"{title} - {description}"}:
                hint = None
    meta["broadcast_label"] = meta.pop("broadcast_date_label", None)
    return SourceInfo.model_validate(meta), hint


def _asr_cost(data: dict[str, Any]) -> float:
    if (cost := data.get("asr_cost")) is not None:
        return float(cost)
    service_costs = data.get("service_costs") or {}
    return float(service_costs.get("elevenlabs") or 0.0)


def _side_tasks(
    plan: ProjectPlan, data: dict[str, Any], found: _Sources, fallback: float
) -> SideTasks:
    layout = plan.layout
    side = SideTasks()
    if data.get("is_cover_generated"):
        side.cover = TaskRecord(
            completed_at=_completed_at(found, (layout.cover,), fallback), elapsed_s=0
        )
    if data.get("is_broadcast_date_researched"):
        result = _read_json(found.holders.get(layout.date_research_result))
        if not isinstance(result, dict):
            plan.warnings.append(("date research without result", "verdict unknown"))
            result = {}
        side.date_research = DateResearchRecord(
            completed_at=_completed_at(found, (layout.date_research_result,), fallback),
            elapsed_s=0,
            verdict="found" if result.get("status") == "found" else "unknown",
            trust=result.get("trust"),
        )
    return side


def _build_state(plan: ProjectPlan, data: dict[str, Any], found: _Sources) -> None:
    layout = plan.layout
    fallback = _mtime(layout.project_json)
    if unmapped := sorted(set(data) - MAPPED_KEYS - DROPPED_KEYS):
        plan.warnings.append(("dropped project.json key", ", ".join(unmapped)))

    stages = {stage for flag, stage in FLAG_STAGES.items() if data.get(flag)}
    if data.get("is_translated") and layout.prepass_briefing in found.holders:
        stages.add(StageKey.PREPASS)
    outputs = _stage_outputs(layout)

    try:
        side = _side_tasks(plan, data, found, fallback)
        project_id = str(data["id"])
        platform = platform_of(project_id)
        source, hint = _source_info(plan, data, found, platform)
        created = data.get("created_at")
        if created is None:
            created_at = _completed_at(found, (layout.metadata_info,), fallback)
        else:
            created_at = datetime.fromisoformat(created).astimezone()
        parent = data.get("parent_project_path")
        plan.state = ProjectState(
            id=project_id,
            platform=platform,
            created_at=created_at,
            name=data.get("name"),
            translation_hint=hint,
            parent=Path(parent) if parent else None,
            broadcast_date=data.get("broadcast_date"),
            source=source,
            section=Section(
                start=data.get("section_start"), end=data.get("section_end")
            ),
            asr_cost_usd=_asr_cost(data),
            stages={
                stage: StageRecord(
                    completed_at=_completed_at(found, outputs[stage], fallback),
                    elapsed_s=0,
                )
                for stage in StageKey
                if stage in stages
            },
            side_tasks=side,
        )
    except (KeyError, ValueError, TypeError) as error:
        plan.problems.append(f"project.json does not convert: {error!r}")
        return
    if plan.state.parent is not None and not plan.state.parent.exists():
        plan.warnings.append(("parent path missing", str(plan.state.parent)))


# --- apply -------------------------------------------------------------------


class VerificationError(Exception):
    """A written file does not read back as intended (e.g. a truncated NAS
    write); the project's sources are kept."""


def _verify_written(path: Path, expected: bytes) -> None:
    actual = path.read_bytes()
    if actual != expected:
        raise VerificationError(
            f"{path} reads back {len(actual)} bytes, expected {len(expected)};"
            " sources kept, re-run to retry"
        )


def apply_plan(plan: ProjectPlan) -> None:
    """Carry out a non-blocked plan; every step is safe to repeat.

    Every converted file is read back before anything is moved or deleted; a
    mismatching one is removed (it is this run's own output) and the project
    aborted with `VerificationError`, leaving its sources in place.
    """
    if plan.state is None or plan.blocked or plan.status != "migrate":
        raise ValueError(f"Plan for {plan.root} cannot be applied")
    # Round-trip the exact JSON that will be written before touching anything.
    state = ProjectState.model_validate_json(plan.state.model_dump_json())
    for write in plan.writes:
        atomic_write_text(write.dest, write.text)
        try:
            _verify_written(write.dest, write.text.encode("utf-8"))
        except VerificationError:
            write.dest.unlink(missing_ok=True)
            raise
        source_stat = write.sources[0].stat()
        os.utime(write.dest, (source_stat.st_atime, source_stat.st_mtime))
    for move in plan.moves:
        move.dest.parent.mkdir(parents=True, exist_ok=True)
        move.source.rename(move.dest)
    legacy_copy = plan.root / LEGACY_STATE_COPY
    legacy_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(plan.layout.project_json, legacy_copy)
    _verify_written(legacy_copy, plan.layout.project_json.read_bytes())
    for write in plan.writes:
        for source in write.sources:
            source.unlink(missing_ok=True)
    for discard in plan.discards:
        discard.remove()
    _remove_empty_legacy_dirs(plan.root)
    # Last: until project.json is replaced, a re-run re-plans this project.
    save_state(plan.layout, state)
    written = plan.layout.project_json.read_text(encoding="utf-8")
    if ProjectState.model_validate_json(written) != state:
        raise VerificationError(
            f"{plan.layout.project_json} does not read back as written;"
            f" the original is {legacy_copy}"
        )


def _remove_empty_legacy_dirs(root: Path) -> None:
    for name in LEGACY_DIRS:
        for dirpath, dirnames, filenames in os.walk(root / name, topdown=False):
            if not filenames and not any(
                (Path(dirpath) / sub).exists() for sub in dirnames
            ):
                Path(dirpath).rmdir()


# --- discovery and report ----------------------------------------------------


@dataclass
class Discovery:
    projects: list[Path] = field(default_factory=list)
    # top-level directory under the root -> files outside any project
    outside: Counter[str] = field(default_factory=Counter)


def discover(root: Path) -> Discovery:
    found = Discovery()
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        if PROJECT_FILE in filenames:
            found.projects.append(current)
            dirnames.clear()
            continue
        if filenames:
            top = "." if current == root else _rel(root, current).split("/")[0]
            found.outside[top] += len(filenames)
        dirnames.sort()
    found.projects.sort()
    return found


_HASH_RE = re.compile(r"_[0-9a-f]{5,}(?:_[0-9a-f]{4,})?(?=\.(?:raw|fixed)\.srt$)")


def unknown_pattern(rel: str) -> str:
    """Collapse digits and cache hashes so unknown files group by kind."""
    return re.sub(r"\d+", "N", _HASH_RE.sub("_<hash>", rel))


@dataclass
class Tally:
    found: int = 0
    migrate: int = 0
    blocked: int = 0
    migrated: int = 0
    invalid: int = 0
    applied: int = 0
    failed: int = 0
    moves: int = 0
    briefings: int = 0
    chunks: int = 0
    chunk_sources: int = 0
    discarded_files: int = 0
    unknown: Counter[str] = field(default_factory=Counter)
    warnings: Counter[str] = field(default_factory=Counter)
    long_paths: list[tuple[Path, int]] = field(default_factory=list)

    def add(self, plan: ProjectPlan) -> None:
        if plan.status == "migrated":
            self.migrated += 1
            return
        if plan.status == "invalid":
            self.invalid += 1
            return
        self.migrate += 1
        self.blocked += plan.blocked
        self.moves += len(plan.moves)
        self.briefings += sum(w.category == "briefing" for w in plan.writes)
        chunk_writes = [w for w in plan.writes if w.category == "chunk"]
        self.chunks += len(chunk_writes)
        self.chunk_sources += sum(len(w.sources) for w in chunk_writes)
        self.discarded_files += sum(d.file_count for d in plan.discards)
        self.unknown.update(unknown_pattern(rel) for rel in plan.unknown)
        self.warnings.update(kind for kind, _ in plan.warnings)
        self.long_paths.extend(plan.long_paths)


def describe(plan: ProjectPlan, label: str) -> list[str]:
    if plan.status != "migrate":
        lines = [f"== {label}  [{plan.status}]"]
        lines.extend(f"   PROBLEM: {problem}" for problem in plan.problems)
        return lines
    header = "BLOCKED" if plan.blocked else "migrate"
    state = plan.state
    lines = [
        f"== {label}  [{header}]"
        + (f"  {state.id} ({state.platform})" if state else "")
    ]
    if state is not None:
        side = [
            key for key in ("cover", "date_research") if getattr(state.side_tasks, key)
        ]
        lines.append(
            f"   stages ({len(state.stages)}): {' '.join(state.stages)}"
            + (f" | side: {' '.join(side)}" if side else "")
        )
    briefings = [w for w in plan.writes if w.category == "briefing"]
    chunks = [w for w in plan.writes if w.category == "chunk"]
    discard_kinds = Counter[str]()
    for discard in plan.discards:
        discard_kinds[discard.category] += discard.file_count
    lines.append(
        f"   move {len(plan.moves)} | briefing {len(briefings)}"
        f" ({', '.join(_rel(plan.root, w.dest) for w in briefings) or '-'})"
        f" | chunks {len(chunks)} from {sum(len(w.sources) for w in chunks)} responses"
        f" | discard {sum(discard_kinds.values())} files"
        + (
            f" ({', '.join(f'{kind} {count}' for kind, count in sorted(discard_kinds.items()))})"
            if discard_kinds
            else ""
        )
    )
    if plan.unknown:
        lines.append(f"   unrecognised, left in place ({len(plan.unknown)}):")
        lines.extend(f"     {rel}" for rel in sorted(plan.unknown))
    lines.extend(f"   warning: {kind}: {detail}" for kind, detail in plan.warnings)
    lines.extend(f"   PROBLEM: {problem}" for problem in plan.problems)
    return lines


def summary(tally: Tally, discovery: Discovery, *, apply: bool) -> list[str]:
    lines = [
        "",
        "==== Summary",
        f"projects found: {tally.found}",
        (
            f"  legacy format: {tally.migrate}"
            f" ({tally.migrate - tally.blocked} migratable, {tally.blocked} blocked)"
        ),
        f"  already migrated: {tally.migrated}",
        f"  invalid project.json: {tally.invalid}",
    ]
    if apply:
        lines.append(f"  applied: {tally.applied}")
    lines.append(f"  failed with an error: {tally.failed}")
    lines += [
        f"moves: {tally.moves}",
        f"briefings written: {tally.briefings}",
        f"chunk translations: {tally.chunks} (from {tally.chunk_sources} response files)",
        f"discarded files: {tally.discarded_files}",
        f"MAX_PATH violations: {len(tally.long_paths)}",
    ]
    lines.extend(f"  {path} ({units} units)" for path, units in tally.long_paths)
    lines.append(f"warnings by kind: {sum(tally.warnings.values())}")
    lines.extend(
        f"  {count:6d}  {kind}" for kind, count in tally.warnings.most_common()
    )
    lines.append(f"unrecognised files left in place: {sum(tally.unknown.values())}")
    lines.extend(
        f"  {count:6d}  {pattern}" for pattern, count in tally.unknown.most_common()
    )
    lines.append(
        f"files outside any project (untouched): {sum(discovery.outside.values())}"
    )
    lines.extend(
        f"  {count:6d}  {top}/" for top, count in discovery.outside.most_common()
    )
    return lines


def run(root: Path, *, apply: bool) -> Tally:
    discovery = discover(root)
    tally = Tally(found=len(discovery.projects))
    for project_root in discovery.projects:
        print(
            "\n".join(_run_project(root, project_root, tally, apply=apply)), flush=True
        )
    print("\n".join(summary(tally, discovery, apply=apply)))
    return tally


def _run_project(
    root: Path, project_root: Path, tally: Tally, *, apply: bool
) -> list[str]:
    """Plan (and apply) one project; any error fails only this project."""
    label = _rel(root, project_root) or "."
    lines: list[str] = []
    try:
        plan = plan_project(project_root)
        tally.add(plan)
        lines = describe(plan, label)
        if apply and plan.status == "migrate" and not plan.blocked:
            apply_plan(plan)
            tally.applied += 1
            lines.append("   applied")
    except Exception as error:  # noqa: BLE001 - one bad project must not stop the batch
        tally.failed += 1
        lines = lines or [f"== {label}  [FAILED]"]
        lines.append(
            f"   FAILED: {project_root}: {type(error).__name__}: {error}"
            " (re-run to resume)"
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else None
    )
    parser.add_argument("root", type=Path, help="directory to search for projects")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="migrate (default: dry run, writes nothing)",
    )
    args = parser.parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    root: Path = args.root
    if not root.is_dir():
        parser.error(f"not a directory: {root}")
    print(f"{'APPLY' if args.apply else 'DRY RUN'}: {root.absolute()}")
    # absolute(), not resolve(): a mapped drive must stay `V:\...` rather
    # than turn into its longer UNC path, which MAX_PATH is measured on.
    tally = run(root.absolute(), apply=args.apply)
    return 1 if tally.blocked or tally.invalid or tally.failed else 0


if __name__ == "__main__":
    sys.exit(main())
