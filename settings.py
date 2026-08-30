from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ReasoningEffort = Literal["low", "medium", "high", "extra"]


class ModelSpec(BaseModel):
    """An inference backend, its model, and the reasoning effort.

    Written in env/config as ``"backend/model"`` or ``"backend/model/effort"``
    (effort is one of low/medium/high/extra, default high) — e.g.
    ``"gemini-api/gemini-3-flash-preview"`` or ``"codex/gpt-5.5/medium"``. The
    split happens here so call sites just read ``.backend``, ``.model``, and
    ``.reasoning_effort``.
    """

    backend: str
    model: str
    reasoning_effort: ReasoningEffort = "high"

    @field_validator("reasoning_effort", mode="before")
    @classmethod
    def _normalize_reasoning_effort(cls, value: object) -> str:
        effort = str(value).strip().lower()
        allowed = ("low", "medium", "high", "extra")
        if effort not in allowed:
            raise ValueError(
                "reasoning_effort must be one of: " + ", ".join(allowed)
            )
        return effort

    def __str__(self) -> str:
        return f"{self.backend}/{self.model}/{self.reasoning_effort}"


def _parse_model_spec(value: object) -> object:
    if isinstance(value, str):
        parts = [part.strip() for part in value.split("/")]
        if len(parts) < 2 or not parts[0] or not parts[1] or len(parts) > 3:
            raise ValueError(
                "model spec must be written as 'backend/model' or "
                f"'backend/model/effort', got: {value!r}"
            )
        parsed: dict[str, str] = {"backend": parts[0], "model": parts[1]}
        if len(parts) == 3 and parts[2]:
            parsed["reasoning_effort"] = parts[2]
        return parsed
    return value


# A ModelSpec field that accepts the "backend/model[/effort]" shorthand string.
# NoDecode stops pydantic-settings from JSON-decoding the env value (it would
# otherwise treat a BaseModel field as complex), so the raw string reaches the
# validator.
ModelSpecField = Annotated[
    ModelSpec, NoDecode, BeforeValidator(_parse_model_spec)
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Validate defaults too, so a bare-string default is parsed into a
        # ModelSpec (defaults are not validated by pydantic otherwise).
        validate_default=True,
    )

    # --- ASR: ElevenLabs Scribe ---------------------------------------------
    elevenlabs_api_key: str | None = Field(
        default=None,
        description="API key for ElevenLabs Speech to Text",
    )
    elevenlabs_stt_model: str = Field(
        default="scribe_v2",
        description="ElevenLabs Speech to Text model identifier",
    )
    elevenlabs_stt_language_code: str = Field(
        default="jpn",
        description="Language code hint for ElevenLabs Speech to Text",
    )

    # Source SRT formatting parameters live as hard-coded constants at
    # the top of services/elevenlabs/srt.py — they are fine-tuned by
    # the maintainer, not exposed as configuration.

    # --- Agent / model backends (shared) ------------------------------------
    # Every model-driven stage picks one backend: 'gemini-api', 'gemini-cli',
    # 'gemini-agy', 'claude', or 'codex'. gemini-cli / gemini-agy / claude /
    # codex use subscription/OAuth auth; gemini-api uses AGENT_GEMINI_API_KEY
    # (only then is the key required). gemini-cli can additionally receive
    # AGENT_GEMINI_GCP_PROJECT as subprocess-only GOOGLE_CLOUD_PROJECT for Code
    # Assist subscription auth. Agent backends cannot ingest audio, so those
    # stages run on frames + SRT only. Each stage sets one AGENT_*_MODEL spec
    # written as "backend/model" or "backend/model/effort" (effort
    # low/medium/high/extra, default high) and parsed into a ModelSpec; the
    # model and effort are passed to the selected backend (set them to values
    # that backend understands). Effort is mapped per client (gemini
    # thinking_level, codex model_reasoning_effort, claude effort); backends
    # without an extra-high setting clamp "extra" to their highest supported
    # value. AGENT_TIMEOUT_MINUTES bounds every single invocation. The schema
    # validate-and-repair cap is NOT configurable — it is the hardcoded
    # MAX_SCHEMA_RETRIES constant in services/inference/schema_enforce.py.
    agent_gemini_api_key: str | None = Field(
        default=None,
        description="API key for Google Gemini. Required only when a stage uses the 'gemini-api' backend.",
    )
    agent_gemini_gcp_project: str | None = Field(
        default=None,
        description="Optional Google Cloud project ID injected as GOOGLE_CLOUD_PROJECT for the gemini-cli subprocess only.",
    )

    agent_timeout_minutes: int = Field(
        default=40,
        ge=1,
        description="Per-invocation timeout in minutes, shared by every inference backend (agent subprocess/query timeout; converted to milliseconds for gemini-api). Raise it when a high-effort stage legitimately runs longer.",
    )

    agent_prepass_model: ModelSpecField = Field(
        default="gemini-cli/gemini-3.1-pro-preview",
        description="Pre-pass spec as 'backend/model[/effort]'. Backend: 'gemini-api', 'gemini-cli', 'gemini-agy', 'claude', or 'codex'.",
    )

    agent_chunk_model: ModelSpecField = Field(
        default="gemini-cli/gemini-3.1-pro-preview",
        description="Chunk translation spec as 'backend/model[/effort]'. Backend: 'gemini-api', 'gemini-cli', 'gemini-agy', 'claude', or 'codex'.",
    )

    agent_postprocess_model: ModelSpecField = Field(
        default="codex/gpt-5.6-sol/medium",
        description="Post-processing spec (subtitle refine + glossary_check) as 'backend/model[/effort]'. Backend: 'codex', 'claude', 'gemini-cli', or 'gemini-agy'.",
    )

    agent_common_model: ModelSpecField = Field(
        default="codex/gpt-5.5/medium",
        description="Spec for lightweight utility agents (chunk structural fix + broadcast-date research) as 'backend/model[/effort]'. Backend: 'codex', 'claude', 'gemini-cli', or 'gemini-agy'. Cover generation is always Codex (image generation) but reuses this effort.",
    )

    video_frame_max_side: int = Field(
        default=768,
        description="Maximum pixel length of the longest side for sampled video frames (pre-pass, chunk, and the on-demand agent frame tool).",
    )

    # --- Translation: media sampling & chunking (backend-agnostic) ----------
    prepass_frame_interval_seconds: int = Field(
        default=60,
        description="Frame budget interval in seconds for SRT-start-based pre-pass frame sampling",
    )
    enable_prepass_full_fixed_glossary: bool = Field(
        default=False,
        description="Pre-pass fixed glossary injection mode. False = normalized substring pre-filter (only matched entries injected). True = inject the entire glossary as a reference table and let the model resolve matches.",
    )
    chunk_char_limit: int = Field(
        default=6000,
        description="Target character count per chunk when splitting SRT for concurrent translation (~5 min of variety show subtitles)",
    )
    chunk_api_concurrency: int = Field(
        default=10,
        description="Maximum concurrent chunk requests for the gemini-api backend (cheap network HTTP calls, can fan out widely).",
    )
    chunk_agent_concurrency: int = Field(
        default=5,
        description="Maximum concurrent chunk processes for the agent backends (gemini-cli / codex / claude); lower than chunk_api_concurrency since each spawns a heavy local process.",
    )
    chunk_max_retries: int = Field(
        default=3,
        description="Maximum retry attempts per chunk on translation failure",
    )
    chunk_frame_interval_seconds: int = Field(
        default=30,
        description="Frame budget interval in seconds for SRT-start-based chunk translation frame sampling",
    )
    # --- Download & pipeline extras -----------------------------------------
    enable_official_subtitles: bool = Field(
        default=True,
        description="Fetch platform closed captions (TVer/Abema/...) at download time and inject them as ground-truth reference into pre-pass and chunk translation. Best-effort: absence never fails a stage.",
    )
    cookies_txt_path: Path | None = Field(
        default=None,
        description="Path to cookies.txt file used for downloading content",
    )
    archived_path: Path | None = Field(
        default=None,
        description="Path for automatic archival. If set, completed projects will be archived to this location",
    )
    package_path: Path | None = Field(
        default=None,
        description="Path for final deliverable packaging. If set, after archive, burn ASS subtitles into the video and copy the cover image to <package_path>/<id>_<name>/",
    )

    # --- Optional post-processing toggles -----------------------------------
    enable_postprocess_refine: bool = Field(
        default=False,
        description="Enable optional agent-driven Traditional Chinese subtitle refinement stage between TRANSLATED and FINALIZED",
    )
    enable_postprocess_glossary_check: bool = Field(
        default=False,
        description="Enable optional agent-driven fixed-glossary localization check stage between SRT_REFINED and FINALIZED. Independent of refine; only runs if a refined SRT exists.",
    )
    enable_cover_generation: bool = Field(
        default=False,
        description="Enable optional agent-driven cover image stylization (runs async after DOWNLOADED, joined before archive). Skipped entirely when break_after is set.",
    )
    enable_broadcast_date_agent_fallback: bool = Field(
        default=False,
        description="Research the original broadcast date with a web-searching agent (agent_common_model) when platform metadata yields none. Runs async after METADATA_FETCHED, joined before archive. Skipped entirely when break_after is set.",
    )
    enable_package_title_suggestion: bool = Field(
        default=False,
        description="At package time, derive three Traditional Chinese title candidates from pre_pass.json with agent_common_model when the source project has no .titles/titles.json yet. The file is always copied into the deliverable when it exists; this toggle only controls generating a missing one.",
    )


settings = Settings()
