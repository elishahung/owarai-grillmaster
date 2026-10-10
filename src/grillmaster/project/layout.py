"""Every path inside a project directory, and nothing else.

Deliverables live at the root (`video.mp4` with its `video.cht.srt` /
`video.cht.ass` beside it, `poster.jpg`, `cover.png`, `subs/`); each stage keeps its intermediates in its own `work/NN_<stage>/`
(`NN` = `StageKey.number`); side tasks and packaging get unnumbered
`work/side/<task>/` and `work/package/`. No other module spells a project
path: stages and the pipeline ask this class and hand the paths on (to
packaging, the tool manifest and other domain code).

`ProjectLayout` is a pure path calculator. It never creates directories; the
only filesystem read is `effective_briefing`'s existence check.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from grillmaster.core.srt import chunk_range_name
from grillmaster.core.stage_key import SideTaskKey, StageKey

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

_SESSION_DIR_NAME = "session"


def session_dir(parent: Path, *, label: str = "") -> Path:
    """Session record directory under `parent` for one agent task's first
    attempt; the agent runner puts retries beside it
    (`core.paths.attempt_path`: `session.2/`, `session.3/`).

    `label` tells apart several tasks sharing one parent (chat translation
    labels batches by their file stem, `ProjectLayout.chat_batch(i).stem`,
    and the polish pass `polish`): `session_<label>/`. Names stay short for
    the MAX_PATH budget.
    """
    name = f"{_SESSION_DIR_NAME}_{label}" if label else _SESSION_DIR_NAME
    return parent / name


def _stamp(at: datetime) -> str:
    return f"{at:%Y%m%d-%H%M%S}"


@dataclass(frozen=True, slots=True)
class ProjectLayout:
    root: Path

    @classmethod
    def for_id(cls, projects_root: Path, video_id: str) -> ProjectLayout:
        """The layout of project `video_id` under `projects_root`."""
        return cls(projects_root / video_id)

    # --- root deliverables --------------------------------------------------

    @property
    def project_json(self) -> Path:
        return self.root / "project.json"

    @property
    def video(self) -> Path:
        """The video every stage processes (cut to the section, if any)."""
        return self.root / "video.mp4"

    @property
    def poster(self) -> Path:
        """The platform thumbnail yt-dlp downloads."""
        return self.root / "poster.jpg"

    @property
    def cover(self) -> Path:
        """The generated cover image (side task)."""
        return self.root / "cover.png"

    @property
    def subs_dir(self) -> Path:
        return self.root / "subs"

    @property
    def ja_srt(self) -> Path:
        """Japanese SRT built from ASR (transcript stage)."""
        return self.subs_dir / "ja.srt"

    @property
    def ja_official_srt(self) -> Path:
        """Normalized platform closed captions (combine stage; often absent)."""
        return self.subs_dir / "ja.official.srt"

    @property
    def cht_srt(self) -> Path:
        """Finalized Traditional Chinese SRT, beside `video` and sharing its
        stem, so players load it automatically."""
        return self._beside_video("cht.srt")

    @property
    def cht_ass(self) -> Path:
        """Styled Traditional Chinese ASS, beside `video` like `cht_srt`."""
        return self._beside_video("cht.ass")

    def _beside_video(self, suffix: str) -> Path:
        video = self.video
        return video.with_name(f"{video.stem}.{suffix}")

    @property
    def chat_cht_json(self) -> Path:
        """Translated live chat (`--chat` only)."""
        return self.subs_dir / "chat.cht.json"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    def run_log(self, started_at: datetime) -> Path:
        return self.logs_dir / f"run-{_stamp(started_at)}.log"

    def events_log(self, started_at: datetime) -> Path:
        return self.logs_dir / f"events-{_stamp(started_at)}.jsonl"

    # --- work directories ---------------------------------------------------

    @property
    def work_root(self) -> Path:
        return self.root / "work"

    def work_dir(self, key: StageKey) -> Path:
        """`work/NN_<stage>/`, the only directory stage `key` writes into
        besides its declared deliverables."""
        return self.work_root / f"{key.number:02d}_{key.value}"

    def side_dir(self, key: SideTaskKey) -> Path:
        return self.work_root / "side" / key.value

    @property
    def package_work_dir(self) -> Path:
        return self.work_root / "package"

    @property
    def agent_workspaces(self) -> tuple[Path, ...]:
        """Directories where agents create files of their own choosing.

        Refine and glossary agents edit SRTs and write reports in their stage
        directory; the cover agent works in its side directory. The MAX_PATH
        budget (`project.naming`) leaves extra room under these.
        """
        return (
            self.work_dir(StageKey.REFINE),
            self.work_dir(StageKey.GLOSSARY),
            self.side_dir(SideTaskKey.COVER),
        )

    def session_parents(self, chunk: tuple[int, int]) -> tuple[Path, ...]:
        """Directories that hold agent session records (`session_dir`).

        `chunk` (an inclusive SRT index range) stands for every chunk
        directory.
        """
        return (
            self.work_dir(StageKey.PREPASS),
            self.chunk_dir(*chunk),
            self.work_dir(StageKey.REFINE),
            self.work_dir(StageKey.GLOSSARY),
            self.work_dir(StageKey.CHAT_TRANSLATE),
            self.side_dir(SideTaskKey.COVER),
            self.side_dir(SideTaskKey.DATE_RESEARCH),
            self.package_work_dir,
        )

    def frames_dirs(self, chunk: tuple[int, int]) -> tuple[Path, ...]:
        """Directories the frame tools save extracted frames into.

        `chunk` stands for every chunk directory, as in `session_parents`.
        """
        return (
            self.prepass_frames_dir,
            self.chunk_frames_dir(*chunk),
            self.refine_frames_dir,
            self.glossary_frames_dir,
        )

    # --- per-stage artifacts ------------------------------------------------

    @property
    def metadata_info(self) -> Path:
        """yt-dlp's info JSON."""
        return self.work_dir(StageKey.METADATA) / "info.json"

    @property
    def download_parts_dir(self) -> Path:
        """Numbered video parts and the raw platform caption files yt-dlp
        writes next to them (`<part>.<lang>.srt`)."""
        return self.work_dir(StageKey.DOWNLOAD) / "parts"

    @property
    def full_video(self) -> Path:
        """The downloaded parts joined, uncut; combine cuts it (keeping it)
        or moves it to `video`."""
        return self.work_dir(StageKey.DOWNLOAD) / "full.mp4"

    @property
    def chat_raw(self) -> Path:
        """yt-dlp's live-chat replay (JSON lines)."""
        return self.work_dir(StageKey.CHAT_FETCH) / "live_chat.jsonl"

    @property
    def chat_messages(self) -> Path:
        """Normalized, section-rebased chat messages."""
        return self.work_dir(StageKey.CHAT_FETCH) / "messages.json"

    @property
    def audio(self) -> Path:
        return self.work_dir(StageKey.AUDIO) / "audio.ogg"

    @property
    def asr_json(self) -> Path:
        return self.work_dir(StageKey.ASR) / "asr.json"

    @property
    def prepass_briefing(self) -> Path:
        return self.work_dir(StageKey.PREPASS) / "briefing.json"

    @property
    def prepass_frames_dir(self) -> Path:
        return self.work_dir(StageKey.PREPASS) / "frames"

    def chunk_dir(self, from_index: int, to_index: int) -> Path:
        return self.work_dir(StageKey.CHUNKS) / chunk_range_name(from_index, to_index)

    def chunk_frames_dir(self, from_index: int, to_index: int) -> Path:
        return self.chunk_dir(from_index, to_index) / "frames"

    def chunk_audio(self, from_index: int, to_index: int) -> Path:
        return self.chunk_dir(from_index, to_index) / "audio.ogg"

    def chunk_translation(self, from_index: int, to_index: int) -> Path:
        """The chunk's `{blocks: [{index, text}]}` output (fixed-name cache)."""
        return self.chunk_dir(from_index, to_index) / "translation.json"

    @property
    def merged_srt(self) -> Path:
        """All chunk translations merged back onto the Japanese timecodes."""
        return self.work_dir(StageKey.CHUNKS) / "merged.srt"

    @property
    def refined_srt(self) -> Path:
        return self.work_dir(StageKey.REFINE) / "refined.srt"

    @property
    def refine_report(self) -> Path:
        return self.work_dir(StageKey.REFINE) / "report.md"

    @property
    def refine_frames_dir(self) -> Path:
        return self.work_dir(StageKey.REFINE) / "frames"

    @property
    def glossary_checked_srt(self) -> Path:
        return self.work_dir(StageKey.GLOSSARY) / "checked.srt"

    @property
    def glossary_briefing(self) -> Path:
        """The glossary check's corrected briefing; written only when an
        accepted run fixed the pre-pass one."""
        return self.work_dir(StageKey.GLOSSARY) / "briefing.json"

    @property
    def glossary_briefing_candidate(self) -> Path:
        """The agent's correction before acceptance; never read downstream."""
        return self.work_dir(StageKey.GLOSSARY) / "briefing.candidate.json"

    @property
    def glossary_report(self) -> Path:
        return self.work_dir(StageKey.GLOSSARY) / "report.md"

    @property
    def glossary_frames_dir(self) -> Path:
        return self.work_dir(StageKey.GLOSSARY) / "frames"

    @property
    def chat_batches_dir(self) -> Path:
        return self.work_dir(StageKey.CHAT_TRANSLATE) / "batches"

    def chat_batch(self, index: int) -> Path:
        """One translated chat batch (fixed-name cache)."""
        return self.chat_batches_dir / f"batch_{index:04d}.json"

    @property
    def chat_polish(self) -> Path:
        """Whole-stream polish corrections (fixed-name cache)."""
        return self.work_dir(StageKey.CHAT_TRANSLATE) / "polish.json"

    # --- side tasks and packaging -------------------------------------------

    @property
    def date_research_result(self) -> Path:
        """The research agent's full verdict and evidence."""
        return self.side_dir(SideTaskKey.DATE_RESEARCH) / "result.json"

    @property
    def titles(self) -> Path:
        """Title suggestions, produced while packaging (fixed-name cache)."""
        return self.package_work_dir / "titles.json"

    @property
    def chat_panel_ass(self) -> Path:
        """Chat-panel ASS rendered at package time for the chosen layout."""
        return self.package_work_dir / "chat.ass"

    # --- derived ------------------------------------------------------------

    def effective_briefing(self) -> Path:
        """The briefing downstream stages read.

        The glossary check never touches the pre-pass output; when it corrects
        the briefing it writes its own copy, which then wins.
        """
        if self.glossary_briefing.exists():
            return self.glossary_briefing
        return self.prepass_briefing
