"""`AppConfig`: the typed shape of `grill.toml`.

Every section forbids unknown keys so a typo fails at load time instead of
being silently ignored. Relative paths resolve against the directory holding
`grill.toml`, passed in as validation context by `validate_config`.
`grill.schema.json` is generated from these models (`config.schema`), so the
field descriptions double as the IDE's completion docs.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    model_validator,
)

from grillmaster.core.model_spec import ModelSpec, ModelSpecField, Role
from grillmaster.core.stage_key import StageKey
from grillmaster.core.tool_session import MuseEffort, MuseModel

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

_ROOT_CONTEXT_KEY = "root"


def _resolve_path(value: Path, info: ValidationInfo) -> Path:
    value = value.expanduser()
    if value.is_absolute():
        return value
    root = (info.context or {}).get(_ROOT_CONTEXT_KEY)
    if root is None:
        raise ValueError(f"relative path {value} needs the grill.toml directory")
    return Path(root) / value


# Absolute (`~` expands), or relative to the directory holding `grill.toml`.
ConfigPath = Annotated[Path, AfterValidator(_resolve_path)]

# A single Windows-safe file or directory name (pool folders, insert stems).
FileName = Annotated[str, StringConstraints(min_length=1, pattern=r'^[^\\/:*?"<>|]+$')]

_STRICT = ConfigDict(extra="forbid")


class PathsConfig(BaseModel):
    model_config = _STRICT

    archive: ConfigPath | None = Field(
        default=None,
        description="Finished projects move here once the stages finish, before packaging; unset keeps them in projects/.",
    )
    package: ConfigPath | None = Field(
        default=None,
        description="Deliverable root: burned-in video, cover and titles per project; also holds pools/.",
    )
    cookies: ConfigPath | None = Field(
        default=None,
        description="cookies.txt handed to yt-dlp for downloads.",
    )


# Where an omitted optional role goes; required roles are never `None`.
_ROLE_FALLBACKS = {
    Role.CHAT: Role.UTILITY,
    Role.PROOFREAD: Role.POSTPROCESS,
}


class AgentRoles(BaseModel):
    """Model per agent role, as backend/model[/effort][/fast]; all but chat
    and proofread are required."""

    model_config = _STRICT

    prepass: ModelSpecField = Field(description="Whole-film pre-pass analysis.")
    chunk: ModelSpecField = Field(description="Concurrent chunk translation.")
    postprocess: ModelSpecField = Field(description="Refine and glossary check.")
    proofread: ModelSpecField | None = Field(
        default=None,
        description="Final proofread after the glossary check; omitted means postprocess.",
    )
    utility: ModelSpecField = Field(
        description=(
            "Lightweight agents: titles, broadcast-date research, and the "
            "no-audio fix of a pre-pass or chunk output that failed validation."
        )
    )
    chat: ModelSpecField | None = Field(
        default=None,
        description="Live-chat replay translation; omitted means utility.",
    )
    image: ModelSpecField = Field(
        description="Cover generation; the backend must support image generation."
    )

    def spec(self, role: Role) -> ModelSpec:
        """The spec for `role`; an omitted optional role falls back
        (`chat` to `utility`, `proofread` to `postprocess`)."""
        spec: ModelSpec | None = getattr(self, role)
        return spec or self.spec(_ROLE_FALLBACKS[role])

    def specs(self) -> dict[Role, ModelSpec]:
        """Every role resolved by `spec`."""
        return {role: self.spec(role) for role in Role}


class AgentsConfig(BaseModel):
    model_config = _STRICT

    timeout_minutes: int = Field(
        default=40,
        ge=1,
        description="Wall-clock limit for one agent turn (each repair round gets its own).",
    )
    max_concurrent: int = Field(
        default=10,
        ge=1,
        description="Agent processes one grill process runs at once across fan-out stages (chunks, chat batches); several grill processes each get this many.",
    )
    roles: AgentRoles

    @property
    def timeout_s(self) -> float:
        """`timeout_minutes` in seconds."""
        return self.timeout_minutes * 60.0


class AsrConfig(BaseModel):
    model_config = _STRICT

    model: str = Field(
        default="scribe_v2",
        min_length=1,
        description="ElevenLabs speech-to-text model.",
    )
    language: str = Field(
        default="jpn", min_length=1, description="Language code hint for ElevenLabs."
    )


class WatchClipConfig(BaseModel):
    """The proofread's `watch_clip` tool: per span, an Azure MAI-Transcribe-2
    call for the speech (`AZURE_SPEECH_KEY`, `AZURE_SPEECH_ENDPOINT`) and a
    Meta Muse call for the captions and scene (`META_API_KEY`), not agent
    roles."""

    model_config = _STRICT

    model: MuseModel = Field(
        default="muse-spark-1.3-contributor",
        description="Meta Muse model that reads each span's captions and scene; the contributor tier is the cheap one (Meta may train on the clips).",
    )
    effort: MuseEffort = Field(
        default="high",
        description="Muse reasoning effort.",
    )


class TranslateConfig(BaseModel):
    model_config = _STRICT

    chunk_char_limit: int = Field(
        default=6000,
        ge=1,
        description="Target source characters per translation chunk (~5 min of a variety show).",
    )
    chunk_attempts: int = Field(
        default=3,
        ge=1,
        description="Fresh agent sessions per chunk on transient failures.",
    )
    prepass_frame_interval_s: int = Field(
        default=60, ge=1, description="Seconds of video per pre-pass frame."
    )
    chunk_frame_interval_s: int = Field(
        default=30, ge=1, description="Seconds of video per chunk-translation frame."
    )
    frame_max_side: int = Field(
        default=768,
        ge=1,
        description="Longest side in pixels of sampled frames (pre-pass, chunks, frame tool).",
    )


class FeaturesConfig(BaseModel):
    model_config = _STRICT

    official_subtitles: bool = Field(
        default=True,
        description="Fetch platform closed captions as ground truth for translation.",
    )
    cover: bool = Field(
        default=False, description="Generate a stylized cover image (side task)."
    )
    date_research: bool = Field(
        default=False,
        description="Research the broadcast date with a web-searching agent when metadata has none.",
    )
    title_suggestion: bool = Field(
        default=False,
        description="Suggest Traditional Chinese titles at package time when none exist yet.",
    )
    proofread: bool = Field(
        default=False,
        description="Run the final proofread stage after the glossary check (also --proofread).",
    )
    proofread_watch_clip: bool = Field(
        default=False,
        description="Give the proofread a watch_clip tool: Azure's MAI-Transcribe-2 hears a span of the video and Meta's Muse ([watch_clip]) reads its captions and scene.",
    )
    telop: bool = Field(
        default=False,
        description="OCR the on-screen text cards (telops) on the GPU and burn the worthwhile ones in, translated (also --telop or a program's telop = true). Needs `uv sync --extra telop`.",
    )
    infuse_metadata: bool = Field(
        default=False,
        description="Write an Infuse metadata sidecar (video.xml: title, description, broadcast date) beside each archived video.mp4.",
    )


# Stems of the deliverable's own entries (`package.assemble`): an insert
# output must not collide with them, nor with the all-digit remix parts.
RESERVED_INSERT_STEMS = frozenset(
    {"video", "cover", "info", "refine", "glossary_check", "proofread"}
)
# The deliverable's path reserve counts insert stems up to this length
# (`package.inserts.INSERT_OUTPUT_MAX_LENGTH`).
INSERT_OUTPUT_MAX_LENGTH = 24


def _check_insert_output(value: str) -> str:
    if value.casefold() in RESERVED_INSERT_STEMS:
        raise ValueError(
            f"'{value}' is a reserved deliverable name "
            f"({', '.join(sorted(RESERVED_INSERT_STEMS))}, or all digits)"
        )
    if value.isdigit():
        raise ValueError(f"'{value}' is all digits, like the remix parts")
    return value


class InsertRule(BaseModel):
    """Copy the next file of a media pool into the deliverable."""

    model_config = _STRICT

    pool: FileName = Field(description="Pool folder under <package>/pools/.")
    output: Annotated[
        FileName,
        StringConstraints(max_length=INSERT_OUTPUT_MAX_LENGTH),
        AfterValidator(_check_insert_output),
    ] = Field(
        description="Output file stem in the deliverable; programs reference inserts by it."
    )
    when: Literal["remix", "always"] = Field(
        default="remix",
        description="Copy only for remix deliverables, or for every deliverable.",
    )


class PackageConfig(BaseModel):
    model_config = _STRICT

    remix_pool: FileName = Field(
        default="default",
        description="Noise pool under <package>/pools/ used by a bare --remix or a remix program.",
    )
    inserts: list[InsertRule] = Field(
        default_factory=list,
        description="Pool files copied into deliverables. An insert some program lists applies only to the programs listing it; the rest apply to all.",
    )

    @model_validator(mode="after")
    def _unique_outputs(self) -> PackageConfig:
        outputs = [insert.output for insert in self.inserts]
        if duplicates := sorted({name for name in outputs if outputs.count(name) > 1}):
            raise ValueError(f"duplicate insert outputs: {', '.join(duplicates)}")
        return self


# Stages that accept program instructions, in prompt order. Field names of
# `ProgramInstruction` equal these stage keys.
INSTRUCTION_STAGES = (
    StageKey.PREPASS,
    StageKey.CHUNKS,
    StageKey.REFINE,
    StageKey.GLOSSARY,
    StageKey.PROOFREAD,
    StageKey.TELOP,
)


class ProgramInstruction(BaseModel):
    """Extra model instructions for one program, keyed by stage."""

    model_config = _STRICT

    common: str | None = Field(
        default=None,
        description="Added to every stage below, ahead of that stage's own text.",
    )
    prepass: str | None = Field(
        default=None,
        description="Whole-film analysis: cast, proper nouns, glossary, catchphrases, segment summaries.",
    )
    chunks: str | None = Field(
        default=None,
        description="Concurrent chunk translation into Traditional Chinese.",
    )
    refine: str | None = Field(
        default=None, description="Agent polish of the translated subtitles."
    )
    glossary: str | None = Field(
        default=None,
        description="Agent terminology/fact check against the fixed glossary.",
    )
    proofread: str | None = Field(
        default=None,
        description="Optional final proofread of the checked subtitles ([features] proofread).",
    )
    telop: str | None = Field(
        default=None,
        description="Telop selection and translation: which on-screen cards to keep and how to render them.",
    )

    def stage_text(self, stage: StageKey) -> str | None:
        """This program's own text for `stage` (without `common`)."""
        if stage not in INSTRUCTION_STAGES:
            raise ValueError(
                f"stage {stage} takes no program instructions; expected one of: "
                f"{', '.join(INSTRUCTION_STAGES)}"
            )
        text: str | None = getattr(self, stage)
        return text


class ProgramRule(BaseModel):
    """Rules for one series or channel name."""

    model_config = _STRICT

    remix: bool = Field(
        default=False,
        description="Package as remix with package.remix_pool even without --remix.",
    )
    inserts: list[str] = Field(
        default_factory=list,
        description="Outputs of [[package.inserts]] enabled for this program.",
    )
    telop: bool = Field(
        default=False,
        description="Translate this program's telops even without --telop or [features] telop.",
    )
    instruction: ProgramInstruction = Field(
        default_factory=ProgramInstruction,
        description="Extra model instructions for this program.",
    )


ProgramSection = Literal["series", "channel"]


class ProgramsConfig(BaseModel):
    model_config = _STRICT

    series: dict[str, ProgramRule] = Field(
        default_factory=dict,
        description="Rules keyed by the source program (series) name.",
    )
    channel: dict[str, ProgramRule] = Field(
        default_factory=dict,
        description="Rules keyed by the broadcast station or uploader channel.",
    )

    def rules(self) -> Iterator[tuple[ProgramSection, str, ProgramRule]]:
        """Every rule as `(section, name, rule)`, channels first."""
        for name, rule in self.channel.items():
            yield "channel", name, rule
        for name, rule in self.series.items():
            yield "series", name, rule


class AppConfig(BaseModel):
    """Owarai GrillMaster settings and per-program rules."""

    model_config = ConfigDict(extra="forbid", title="GrillMaster grill.toml")

    paths: PathsConfig = Field(default_factory=PathsConfig)
    agents: AgentsConfig
    asr: AsrConfig = Field(default_factory=AsrConfig)
    watch_clip: WatchClipConfig = Field(default_factory=WatchClipConfig)
    translate: TranslateConfig = Field(default_factory=TranslateConfig)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    package: PackageConfig = Field(default_factory=PackageConfig)
    programs: ProgramsConfig = Field(default_factory=ProgramsConfig)

    @model_validator(mode="after")
    def _program_inserts_exist(self) -> AppConfig:
        declared = {insert.output for insert in self.package.inserts}
        for section, name, rule in self.programs.rules():
            if unknown := sorted(set(rule.inserts) - declared):
                raise ValueError(
                    f"programs {section} '{name}' lists undeclared inserts: "
                    f"{', '.join(unknown)}"
                    " (declare them under [[package.inserts]])"
                )
        return self


def validate_config(data: Mapping[str, Any], *, root: Path) -> AppConfig:
    """Validate parsed `grill.toml` data; relative paths resolve under `root`."""
    return AppConfig.model_validate(data, context={_ROOT_CONTEXT_KEY: root})
