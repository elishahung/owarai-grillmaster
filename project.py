"""Project management for video captioning workflow.

This module defines the Project model and related enums for tracking the progress
of video processing tasks through various stages including download, transcription,
and translation.
"""

from pydantic import BaseModel, Field
from pathlib import Path
from datetime import datetime
import json
import shutil
from enum import Enum
from loguru import logger
from settings import settings
import re
from urllib.parse import urlparse, parse_qs
from services.ytdlp.info import SourceTalentInfo, YtDlpVideoInfo

PROJECT_ROOT_NAME = "projects"
PROJECT_FILE_NAME = "project.json"
VIDEO_FILE_NAME = "video.mp4"
AUDIO_FILE_NAME = "audio.ogg"
ASR_FILE_NAME = "asr.json"
SRT_FILE_NAME = "video.ja.srt"
TRANSLATED_FILE_NAME = "video.cht.srt"
REFINED_SRT_FILE_NAME = "video.cht.refined.srt"
FINALIZED_SRT_FILE_NAME = "video.cht.finalized.srt"
ASS_FILE_NAME = "video.cht.ass"
POSTER_FILE_NAME = "poster.jpg"
POSTER_COVER_FILE_NAME = "poster.cover.png"
PRE_PASS_FILE_NAME = "pre_pass.json"
PRE_PASS_RAW_FILE_NAME = "pre_pass.raw.json"
REFINE_REPORT_FILE_NAME = "report.md"
GLOSSARY_CHECKED_SRT_FILE_NAME = "video.cht.glossary_checked.srt"
GLOSSARY_CHECK_REPORT_FILE_NAME = "report.md"
ASR_CACHE_DIR_NAME = ".asr"
CHUNKS_CACHE_DIR_NAME = ".chunks"
PRE_PASS_CACHE_DIR_NAME = ".pre_pass"
REFINE_CACHE_DIR_NAME = ".refine"
GLOSSARY_CHECK_CACHE_DIR_NAME = ".glossary_check"


class ProgressStage(str, Enum):
    """Enum representing different stages in the video processing workflow.

    Each value corresponds to a boolean field in the Project model that tracks
    whether that stage has been completed.
    """

    METADATA_FETCHED = "is_metadata_fetched"
    DOWNLOADED = "is_downloaded"
    VIDEO_PROCESSED = "is_video_processed"
    AUDIO_PROCESSED = "is_audio_processed"
    ASR_COMPLETED = "is_asr_completed"
    SRT_COMPLETED = "is_srt_completed"
    PREPASS_COMPLETED = "is_prepass_completed"
    CHUNK_TRANSLATED = "is_chunk_translated"
    SRT_REFINED = "is_srt_refined"
    GLOSSARY_CHECKED = "is_glossary_checked"
    FINALIZED = "is_finalized"


class VideoSource(str, Enum):
    """Enum representing supported video source platforms."""

    BILIBILI = "bilibili"
    TVER = "tver"
    ABEMA = "abema"
    YOUTUBE = "youtube"


class SourceTalent(BaseModel):
    """Person or group metadata supplied by the video source."""

    id: str
    name: str
    name_kana: str | None = None
    roles: list[str] = Field(default_factory=list)


class SourceMetadata(BaseModel):
    """Optional metadata collected from the source platform."""

    talents: list[SourceTalent] = Field(default_factory=list)


class Project(BaseModel):
    """Represents a video captioning project with progress tracking.

    This class manages project metadata, progress through various processing stages,
    and file paths for all intermediate and final outputs.

    Attributes:
        id: Unique identifier for the project (often a video source).
        name: Human-readable name for the project (defaults to "video").
        translation_hint: Optional translation hint for the project.
        is_metadata_fetched: Whether video metadata has been retrieved.
        is_downloaded: Whether video has been downloaded.
        is_video_processed: Whether video segments have been combined.
        is_audio_processed: Whether audio has been extracted.
        is_asr_completed: Whether speech recognition has been completed.
        is_srt_completed: Whether SRT subtitle file has been generated.
        is_prepass_completed: Whether the Gemini pre-pass briefing has been completed.
        is_chunk_translated: Whether concurrent chunk translation has been completed.
        is_srt_refined: Whether the optional Codex-driven SRT refinement has been completed.
        is_glossary_checked: Whether the optional Codex-driven fixed-glossary localization check has been completed.
        is_finalized: Whether the final ASS + SRT outputs have been generated.
        is_cover_generated: Whether the optional Codex-driven cover image has been generated.
    """

    id: str
    created_at: datetime = Field(default_factory=datetime.now)
    name: str = Field(default="video")
    translation_hint: str | None = None
    parent_project_path: Path | None = None
    source_metadata: SourceMetadata = Field(default_factory=SourceMetadata)
    total_cost: float = 0.0
    service_costs: dict[str, float] = Field(default_factory=dict)

    # Progress
    is_metadata_fetched: bool = False
    is_downloaded: bool = False
    is_video_processed: bool = False
    is_audio_processed: bool = False
    is_asr_completed: bool = False
    is_srt_completed: bool = False
    is_prepass_completed: bool = False
    is_chunk_translated: bool = False
    is_srt_refined: bool = False
    is_glossary_checked: bool = False
    is_finalized: bool = False
    is_cover_generated: bool = False

    @staticmethod
    def parse_source_str(source_str: str) -> str:
        """Parse a video source string to extract the video ID.

        Handles various input formats including direct IDs and full URLs.
        Supports: Bilibili (URL/BV), TVer (URL/ID), Abema (URL/ID),
        YouTube (URL or `v=<id>` prefixed form).

        Args:
            source_str: Video source as ID or URL.

        Returns:
            The extracted video ID.

        Raises:
            ValueError: If the URL format is not recognized.
        """
        # 1. Handle Bilibili (Most distinct format)
        bv_match = re.search(r"(BV[a-zA-Z0-9]+)", source_str)
        if bv_match:
            return bv_match.group(1)

        # 2. Handle YouTube: already-prefixed `v=<id>` passes through unchanged
        # so re-parsing a stored ID is idempotent.
        if source_str.startswith("v="):
            return source_str

        # 3. Handle YouTube URLs: youtube.com/watch?v=ID, youtu.be/ID,
        # youtube.com/shorts/ID, youtube.com/live/ID, m.youtube.com/...
        if "youtube.com" in source_str or "youtu.be" in source_str:
            parsed = urlparse(source_str)
            qs = parse_qs(parsed.query)
            if "v" in qs and qs["v"]:
                return f"v={qs['v'][0]}"
            parts = parsed.path.strip("/").split("/")
            if parts and parts[-1]:
                return f"v={parts[-1]}"
            raise ValueError(f"Invalid YouTube URL: {source_str}")

        # 4. Handle URLs (Bilibili & TVer & Abema)
        # Using urlparse is safer for handling query parameters
        if (
            "bilibili.com" in source_str
            or "tver.jp" in source_str
            or "abema.tv" in source_str
        ):
            try:
                path = urlparse(source_str).path
                parts = path.strip("/").split("/")
                if parts:
                    # Abema: /video/episode/90-979_s1_p123 -> 90-979_s1_p123
                    # TVer: /episodes/ep12345 -> ep12345
                    # Bilibili: /video/BV1ZArvBaEqL -> BV1ZArvBaEqL
                    return parts[-1]
            except Exception:
                pass  # Fall through to error if parsing fails

        # 5. Reject unknown URLs
        # If it looks like a URL but wasn't caught above, it's invalid/unsupported
        if source_str.startswith(("https://", "http://")):
            raise ValueError(f"Invalid video source: {source_str}")

        # 6. Return as Direct ID
        return source_str

    @classmethod
    def from_source_str(
        cls,
        source_str: str,
        translation_hint: str | None = None,
        parent_project_path: str | Path | None = None,
    ) -> "Project":
        """Load an existing project from disk or create a new one.

        Args:
            source_str: The video source, id or url (e.g., 'BV1ZArvBaEqL', 'https://www.bilibili.com/video/BV1ZArvBaEqL').
            translation_hint: Optional translation hint for new projects.
            parent_project_path: Optional filesystem path to a parent project
                directory whose pre_pass.json will seed this project's pre-pass
                for cross-episode consistency. Accepts paths under `projects/`
                or anywhere else (e.g., archived locations) since the parent
                may have been archived.

        Returns:
            A Project instance loaded from the saved JSON file, or a new
            Project if no saved file exists.

        Raises:
            ValidationError: If the saved project data is invalid.
            JSONDecodeError: If the project file is corrupted.
        """
        id = cls.parse_source_str(source_str)
        resolved_parent_path = (
            Path(parent_project_path)
            if parent_project_path is not None
            else None
        )

        logger.debug(f"Loading project: {id}")
        json_path = Path(PROJECT_ROOT_NAME) / id / PROJECT_FILE_NAME

        if not json_path.exists():
            logger.info(f"Creating new project: {id}")
            return cls(
                id=id,
                translation_hint=translation_hint,
                parent_project_path=resolved_parent_path,
            )

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                project_data = json.load(f)
            project = cls.model_validate(project_data)
            logger.info(f"Loaded existing project: {id} (name: {project.name})")

            if translation_hint is not None:
                logger.warning(
                    f"Translation hint is not supported for existing projects"
                )
            if resolved_parent_path is not None:
                logger.warning(
                    f"Parent project is not supported for existing projects"
                )

            return project
        except Exception as e:
            logger.error(f"Failed to load project {id}: {e}")
            raise

    def update_from_video_info(self, video_info: YtDlpVideoInfo) -> None:
        """Update project from video information.

        Updates the project name and translation hint from the video information.

        Args:
            video_info: The video information from yt-dlp.
        """
        self.name = video_info.filename
        # If translation hint is not set:
        # - bilibili: use the video title
        # - other sources: use the video title + description
        if self.translation_hint is None:
            self.translation_hint = video_info.title
            if (
                self.source != VideoSource.BILIBILI
                and video_info.description is not None
            ):
                self.translation_hint = (
                    f"{video_info.title} - {video_info.description}"
                )
        self.save()

    def update_from_source_talents(
        self, talents: list[SourceTalentInfo]
    ) -> None:
        """Persist source-provided talent metadata on the project."""
        self.source_metadata.talents = [
            SourceTalent(
                id=talent.id,
                name=talent.name,
                name_kana=talent.name_kana,
                roles=talent.roles,
            )
            for talent in talents
        ]
        self.save()

    def source_metadata_context(self) -> str | None:
        """Return source metadata formatted for Gemini prompt context."""
        if not self.source_metadata.talents:
            return None

        lines = ["Official source cast/talent metadata:"]
        for talent in self.source_metadata.talents:
            role_text = f" ({', '.join(talent.roles)})" if talent.roles else ""
            kana_text = f" / {talent.name_kana}" if talent.name_kana else ""
            lines.append(f"- {talent.name}{kana_text}{role_text}")
        return "\n".join(lines)

    def save(self) -> None:
        """Save the current project state to disk as JSON.

        The project is saved to project.json in the project directory.
        Creates the directory if it doesn't exist.

        Raises:
            IOError: If the file cannot be written.
        """
        logger.debug(f"Saving project: {self.id}")
        try:
            self.project_path.mkdir(parents=True, exist_ok=True)
            with open(self.json_path, "w", encoding="utf-8") as f:
                f.write(self.model_dump_json(indent=4, ensure_ascii=False))
            logger.debug(f"Project saved: {self.id}")
        except Exception as e:
            logger.error(f"Failed to save project {self.id}: {e}")
            raise

    def mark_progress(self, stage: ProgressStage) -> None:
        """Mark a processing stage as completed and save the project.

        Args:
            stage: The progress stage to mark as complete.

        Raises:
            IOError: If the project cannot be saved.
        """
        field_name = stage.value
        logger.info(f"Project {self.id}: Marking stage complete - {stage.name}")
        setattr(self, field_name, True)
        self.save()

    def add_cost(self, service: str, amount: float) -> None:
        """Accumulate non-negative API cost for a service and persist it."""
        if amount < 0:
            raise ValueError("Cost amount must be non-negative")
        if not service:
            raise ValueError("Service name must not be empty")
        if amount == 0:
            return

        self.total_cost += amount
        self.service_costs[service] = (
            self.service_costs.get(service, 0.0) + amount
        )
        logger.info(
            f"Project {self.id}: Added ${amount:.4f} to {service} "
            f"(service total ${self.service_costs[service]:.4f}, "
            f"project total ${self.total_cost:.4f})"
        )
        self.save()

    def archive(self) -> Path | None:
        """Archive the entire project by moving it to the archived directory.

        Returns the new path on success, or None if archived_path is not configured.

        Raises:
            FileNotFoundError: If the project directory doesn't exist.
            IOError: If the directory cannot be moved.
        """
        if settings.archived_path is None:
            logger.warning("Archived path is not set, skipping archiving")
            return None

        archived_root = settings.archived_path
        archived_path = archived_root / f"{self.id}_{self.name}"

        if not self.project_path.exists():
            logger.error(
                f"Project directory does not exist: {self.project_path}"
            )
            raise FileNotFoundError(
                f"Project directory not found: {self.project_path}"
            )

        # Create archived directory if it doesn't exist
        archived_root.mkdir(parents=True, exist_ok=True)

        # If archived path already exists, remove it first
        if archived_path.exists():
            logger.warning(
                f"Archived project already exists, removing: {archived_path}"
            )
            shutil.rmtree(archived_path)

        logger.info(f"Archiving project {self.id} to {archived_path}")
        shutil.move(str(self.project_path), str(archived_path))
        logger.info(f"Project {self.id} archived successfully")
        return archived_path

    # Source management
    @property
    def source(self) -> VideoSource:
        """Determine the video source platform based on the project ID.

        Returns:
            The VideoSource enum value for this project.
        """
        # Bilibili: Always starts with BV
        if self.id.startswith("BV"):
            return VideoSource.BILIBILI

        # YouTube: stored with the `v=` prefix to disambiguate from the Abema
        # fallback (their character sets overlap).
        if self.id.startswith("v="):
            return VideoSource.YOUTUBE

        # TVer: IDs typically start with 'ep' (episode) or 'sh' (series)
        # and contain ONLY alphanumeric characters (no hyphens/underscores).
        if self.id.startswith(("ep", "sh")) and self.id.isalnum():
            return VideoSource.TVER

        # Abema: IDs often contain '_', '-', or start with numbers.
        # We treat Abema as the fallback for non-TVer IDs.
        return VideoSource.ABEMA

    @property
    def source_url(self) -> str:
        """Get the full URL for the video source.

        Returns:
            The complete URL to the video on its source platform.
        """
        if self.source == VideoSource.BILIBILI:
            return f"https://www.bilibili.com/video/{self.id}"

        if self.source == VideoSource.TVER:
            return f"https://tver.jp/episodes/{self.id}"

        if self.source == VideoSource.ABEMA:
            return f"https://abema.tv/video/episode/{self.id}"

        if self.source == VideoSource.YOUTUBE:
            return f"https://www.youtube.com/watch?v={self.id[2:]}"

        raise ValueError(f"Invalid video source: {self.source}")

    # Files management
    @property
    def project_path(self) -> Path:
        """Get the project directory path.

        Returns:
            Path to the project directory.
        """
        return Path(PROJECT_ROOT_NAME) / self.id

    @property
    def json_path(self) -> Path:
        """Get the path to the project metadata JSON file.

        Returns:
            Path to project.json.
        """
        return self.project_path / PROJECT_FILE_NAME

    @property
    def downloaded_video_paths(self) -> list[Path]:
        """Get all downloaded video segment files.

        Returns:
            List of paths to downloaded MP4 files, excluding the final combined video.
        """
        return [
            video_file
            for video_file in self.project_path.glob("*.mp4")
            if video_file.is_file()
            and video_file.name != VIDEO_FILE_NAME.split(".")[0]
        ]

    @property
    def video_path(self) -> Path:
        """Get the path to the final combined video file.

        Returns:
            Path to video.mp4.
        """
        return self.project_path / VIDEO_FILE_NAME

    @property
    def audio_path(self) -> Path:
        """Get the path to the extracted audio file.

        Returns:
            Path to .asr/audio.ogg.
        """
        return self.asr_cache_dir / AUDIO_FILE_NAME

    @property
    def asr_path(self) -> Path:
        """Get the path to the ASR results JSON file.

        Returns:
            Path to .asr/asr.json.
        """
        return self.asr_cache_dir / ASR_FILE_NAME

    @property
    def srt_path(self) -> Path:
        """Get the path to the original subtitle file.

        Returns:
            Path to srt.srt.
        """
        return self.project_path / SRT_FILE_NAME

    @property
    def translated_path(self) -> Path:
        """Get the path to the translated subtitle file.

        Returns:
            Path to translated.srt.
        """
        return self.project_path / TRANSLATED_FILE_NAME

    @property
    def ass_path(self) -> Path:
        """Get the path to the styled ASS subtitle file."""
        return self.project_path / ASS_FILE_NAME

    @property
    def refined_srt_path(self) -> Path:
        """Get the path to the Codex-refined Traditional Chinese SRT file."""
        return self.project_path / REFINED_SRT_FILE_NAME

    @property
    def finalized_srt_path(self) -> Path:
        """Path to the finalized, player-friendly SRT (Netflix TC punctuation rules).

        Generated alongside the ASS during the finalize stage so devices that
        don't support ASS can still consume the same cleaned subtitles.
        """
        return self.project_path / FINALIZED_SRT_FILE_NAME

    @property
    def poster_path(self) -> Path:
        """Get the path to the source poster image downloaded by yt-dlp."""
        return self.project_path / POSTER_FILE_NAME

    @property
    def poster_cover_path(self) -> Path:
        """Get the path to the Codex-generated stylized cover image."""
        return self.project_path / POSTER_COVER_FILE_NAME

    @property
    def pre_pass_path(self) -> Path:
        """Get the path to the cached Gemini pre-pass briefing JSON.

        Returns:
            Path to .pre_pass/pre_pass.json.
        """
        return self.pre_pass_cache_dir / PRE_PASS_FILE_NAME

    @property
    def pre_pass_raw_path(self) -> Path:
        """Get the path to the original pre-pass briefing backup JSON."""
        return self.pre_pass_cache_dir / PRE_PASS_RAW_FILE_NAME

    @property
    def parent_pre_pass_path(self) -> Path | None:
        """Resolve the parent project's pre_pass.json path, if configured.

        Returns:
            Path to `<parent_project_path>/.pre_pass/pre_pass.json`, or None if
            no parent project is set.
        """
        if self.parent_project_path is None:
            return None
        return (
            self.parent_project_path
            / PRE_PASS_CACHE_DIR_NAME
            / PRE_PASS_FILE_NAME
        )

    def parent_pre_pass_context(self) -> str | None:
        """Read the parent project's pre_pass.json content for prompt injection.

        Returns:
            Raw JSON text of the parent's pre_pass.json, or None if no parent
            project is configured.

        Raises:
            FileNotFoundError: If a parent project is configured but its
                pre_pass.json does not exist on disk. Surfaced early so the
                pipeline fails before incurring any Gemini cost.
        """
        path = self.parent_pre_pass_path
        if path is None:
            return None
        if not path.exists():
            raise FileNotFoundError(
                f"Parent project pre_pass.json not found: {path}. "
                "Ensure the parent project has completed its pre-pass stage, "
                "or check the --parent-project path."
            )
        return path.read_text(encoding="utf-8")

    @property
    def asr_cache_dir(self) -> Path:
        """Get the directory for ASR audio and transcription artifacts."""
        return self.project_path / ASR_CACHE_DIR_NAME

    @property
    def pre_pass_cache_dir(self) -> Path:
        """Get the directory for persistent pre-pass multimodal cache assets."""
        return self.project_path / PRE_PASS_CACHE_DIR_NAME

    @property
    def chunks_cache_dir(self) -> Path:
        """Get the directory for persistent per-chunk translation caches."""
        return self.project_path / CHUNKS_CACHE_DIR_NAME

    @property
    def refine_cache_dir(self) -> Path:
        """Get the directory for refinement artifacts (report, etc.)."""
        return self.project_path / REFINE_CACHE_DIR_NAME

    @property
    def refine_report_path(self) -> Path:
        """Get the path to the Codex-written refinement summary report."""
        return self.refine_cache_dir / REFINE_REPORT_FILE_NAME

    @property
    def glossary_checked_srt_path(self) -> Path:
        """Path to the Codex glossary-checked Traditional Chinese SRT file.

        Produced by the optional glossary-check stage, which copies the
        refined SRT and swaps only fixed-glossary term mismatches. May be
        absent when the stage ran but found nothing to check.
        """
        return self.project_path / GLOSSARY_CHECKED_SRT_FILE_NAME

    @property
    def glossary_check_cache_dir(self) -> Path:
        """Get the directory for glossary-check artifacts (report, etc.)."""
        return self.project_path / GLOSSARY_CHECK_CACHE_DIR_NAME

    @property
    def glossary_check_report_path(self) -> Path:
        """Get the path to the Codex-written glossary-check summary report."""
        return self.glossary_check_cache_dir / GLOSSARY_CHECK_REPORT_FILE_NAME


# Runtime check enum values match field names
def check_enum_field_sync():
    """Verify that all ProgressStage enum values correspond to Project fields.

    This function is called at module import time to ensure that the enum
    values stay synchronized with the actual Project model fields.

    Raises:
        ValueError: If a ProgressStage enum value doesn't match a Project field name.
    """
    project_fields = Project.model_fields.keys()
    for stage in ProgressStage:
        if stage.value not in project_fields:
            raise ValueError(
                f"Progress stage {stage.value} does not match project field {stage.value}"
            )


check_enum_field_sync()
